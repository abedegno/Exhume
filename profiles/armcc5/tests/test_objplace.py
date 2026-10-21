"""objplace.py: object placement, the proof for a source too large for MDK-Lite's armlink.

The object is fixtures/objplace/unit.c compiled with armcc 5.06u7
(`armcc -c --cpu Cortex-M3 -O0 unit.c -o unit.o`, no --split_sections): one `.text` holding two
functions, with an R_ARM_THM_CALL from caller to helper and two R_ARM_ABS32 literal words (the
file's own table and an extern). unit_fromelf.txt is `fromelf --text -v -r -s unit.o`,
unit_syms.txt `fromelf --text -s unit.o`. The object file itself is not kept (no binaries in the
repository): raw() rebuilds the bytes objplace reads from it, the allocatable sections' contents
(RAW_TEXT, RAW_CONST, before relocation) at the file offsets the listing gives. ARMLINK_TEXT and ARMLINK_CONST are what armlink made
of the object with .text FIXED at 0x08000100, .constdata at 0x08000200 and g_total defined at
0x20000010 by a symdefs file. No toolchain is needed: these tests check the placement against
armlink's own bytes."""
import os, struct, sys, tempfile, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import armtc, armmap, ehex, objplace
from armmap import Row

FX = os.path.join(HERE, 'fixtures', 'objplace')
TEXT, CONST, G_TOTAL = 0x08000100, 0x08000200, 0x20000010
ARMLINK_TEXT = bytes.fromhex('014601eb410001f00302074b9a5c1044704710b504462046fff7f2ff401c034908600846006810bd0002000810000020')
ARMLINK_CONST = bytes.fromhex('01020304')
RAW_TEXT = bytes.fromhex('014601eb410001f00302074b9a5c1044704710b504462046fff7feff401c034908600846006810bd0000000000000000')
RAW_CONST = ARMLINK_CONST


def read(name, mode='r'):
    with open(os.path.join(FX, name), mode) as f: return f.read()


def raw():
    """The object file's bytes as far as objplace reads them: an MDK-Lite header, and each
    allocatable section's contents at its file offset."""
    b = bytearray(1492)
    b[:4] = b'\xf7ORC'
    b[60:60 + len(RAW_TEXT)] = RAW_TEXT
    b[228:228 + len(RAW_CONST)] = RAW_CONST
    return bytes(b)


def obj():
    return objplace.fill(objplace.parse(read('unit_fromelf.txt'), 'unit.o'), raw())


def index(o, name):
    return next(i for i, s in o.sections.items() if s.name == name)


class ReadTest(unittest.TestCase):
    def test_sections_symbols_and_relocations(self):
        o = obj()
        t = o.sections[index(o, '.text')]
        self.assertEqual((t.type, t.size, t.align), ('SHT_PROGBITS', 0x30, 4))
        self.assertIn('SHF_EXECINSTR', t.flags)
        self.assertEqual(len(t.data), 0x30)
        self.assertEqual(o.sections[index(o, '.constdata')].data, ARMLINK_CONST)
        rels = [(r.offset, r.tname, r.sname) for r in o.relocs[index(o, '.text')]]
        self.assertEqual(rels, [(0x18, 'R_ARM_THM_CALL', 'helper'), (0x28, 'R_ARM_ABS32', 'k_table'),
                                (0x2C, 'R_ARM_ABS32', 'g_total')])
        names = {s.name: s for s in o.symbols.values()}
        self.assertEqual((names['caller'].value, names['caller'].type, names['g_total'].sec), (0x13, 'Code', 'Ref'))
        self.assertEqual(objplace.mapping_states(o, index(o, '.text')), [(0, '$t'), (0x28, '$d')])

    def test_a_section_past_the_end_of_the_file_is_an_error(self):
        with self.assertRaisesRegex(RuntimeError, 'runs past the end'):
            objplace.fill(objplace.parse(read('unit_fromelf.txt'), 'unit.o'), raw()[:100])

    def test_a_relocation_section_read_short_is_an_error(self):
        o = objplace.parse(read('unit_fromelf.txt'), 'unit.o')
        o.relocs[index(o, '.text')].pop()
        with self.assertRaisesRegex(RuntimeError, '2 relocations read, 3 in the section'):
            objplace.fill(o, raw())


class LinkTest(unittest.TestCase):
    def setUp(self):
        self.o = obj()
        self.bases = {index(self.o, '.text'): TEXT, index(self.o, '.constdata'): CONST}

    def test_same_bytes_as_armlink(self):
        out, errors, count = objplace.link(self.o, self.bases, {'g_total': (G_TOTAL, 'D')})
        self.assertEqual(errors, [])
        self.assertEqual(count, 3)
        self.assertEqual(out[index(self.o, '.text')], ARMLINK_TEXT)
        self.assertEqual(out[index(self.o, '.constdata')], ARMLINK_CONST)

    def test_moved_section_moves_the_call_and_the_word(self):
        bases = dict(self.bases); bases[index(self.o, '.constdata')] = CONST + 0x40
        out, errors, _ = objplace.link(self.o, bases, {'g_total': (G_TOTAL, 'D')})
        self.assertEqual(errors, [])
        got = out[index(self.o, '.text')]
        self.assertEqual(got[0x28:0x2C], (CONST + 0x40).to_bytes(4, 'little'))
        self.assertEqual(got[0x18:0x1C], ARMLINK_TEXT[0x18:0x1C])        # helper is in the same section

    def test_an_unresolved_symbol_fails(self):
        _, errors, _ = objplace.link(self.o, self.bases, {})
        self.assertEqual(errors, ['.text+0x2C R_ARM_ABS32 g_total: unresolved symbol g_total'])

    def test_a_call_to_data_fails(self):
        t = index(self.o, '.text')
        for r in self.o.relocs[t]:
            if r.tname == 'R_ARM_THM_CALL': r.sym = next(i for i, s in self.o.symbols.items() if s.name == 'g_total')
        _, errors, _ = objplace.link(self.o, self.bases, {'g_total': (G_TOTAL, 'D')})
        self.assertTrue(any('not Thumb code' in e for e in errors), errors)

    def test_a_relocation_in_the_wrong_state_fails(self):
        t = index(self.o, '.text')
        self.o.relocs[t][1].offset = 0x10                 # a literal word inside $t code
        _, errors, _ = objplace.link(self.o, self.bases, {'g_total': (G_TOTAL, 'D')})
        self.assertTrue(any('in a $t part of the code, not $d' in e for e in errors), errors)

    def test_an_unplaced_section_a_relocation_needs_fails(self):
        _, errors, _ = objplace.link(self.o, {index(self.o, '.text'): TEXT}, {'g_total': (G_TOTAL, 'D')})
        self.assertTrue(any('k_table is in section' in e and 'not placed' in e for e in errors), errors)

    def test_a_weak_definition_gives_way_to_symdefs(self):
        h = next(s for s in self.o.symbols.values() if s.name == 'helper')
        h.bind = 'Wk'
        out, errors, _ = objplace.link(self.o, self.bases, {'g_total': (G_TOTAL, 'D'), 'helper': (0x08000301, 'T')})
        self.assertEqual(errors, [])
        self.assertNotEqual(out[index(self.o, '.text')][0x18:0x1C], ARMLINK_TEXT[0x18:0x1C])

    def test_branch_encodings_round_trip(self):
        for off in (-(1 << 24), -0x1000, -4, 0, 2, 0x7FFFFE, (1 << 24) - 2):
            self.assertEqual(objplace._bl_get(*objplace._bl_put(0xF000, 0xF800, off)), off)
        for off in (-(1 << 20), -2, 0, 0x1000, (1 << 20) - 2):
            self.assertEqual(objplace._b19_get(*objplace._b19_put(0xF000, 0x8000, off)), off)
        for v in (0, 1, 0x1234, 0xFFFF):
            self.assertEqual(objplace._mov_get(*objplace._mov_put(0xF240, 0x0000, v)), v)


class PlaceObjectTest(unittest.TestCase):
    """place_object end to end on the fixture: map, image and symdefs as a project has them."""

    def project(self, text=ARMLINK_TEXT, data_rows=True):
        root = tempfile.mkdtemp(); self.addCleanup(lambda: __import__('shutil').rmtree(root, ignore_errors=True))
        img = bytearray(b'\xff' * 0x200) + bytearray(ARMLINK_CONST)
        img[0x100:0x100 + len(text)] = text
        with open(os.path.join(root, 'fw.hex'), 'w') as f: f.write(ehex.write(0x08000000, bytes(img)))
        m = armmap.Map([Row(TEXT, 0x12, 'helper', 'code', ''), Row(TEXT + 0x12, 0x1E, 'caller', 'code', '')],
                       [Row(CONST, 4, 'k_table', 'ro', '')] + ([Row(G_TOTAL, 4, 'g_total', 'rw', '')] if data_rows else []))
        src = os.path.join(root, 'unit.c')
        with open(src, 'w') as f: f.write(read('unit.c'))
        cfg = types.SimpleNamespace(image=os.path.join(root, 'fw.hex'), root=root)
        info = armtc.parse_symbols(read('unit_syms.txt'))
        return objplace.place_object(cfg, None, src, 'unit.o', info, m, o=obj())

    def test_whole_match(self):
        r = self.project()
        self.assertTrue(r.whole, r.lines)
        self.assertEqual(r.lines[-1], '-- 52/52 bytes matched, 3 relocations resolved (object placement); WHOLE FILE MATCHES')
        self.assertEqual(r.placed['.text'], (TEXT, ARMLINK_TEXT))

    def test_a_changed_byte_fails(self):
        changed = bytearray(ARMLINK_TEXT); changed[0x1C] ^= 1           # adds r0,r0,#1 -> #0 in the image
        r = self.project(bytes(changed))
        self.assertFalse(r.whole)
        self.assertTrue(any('1 bytes differ, first at +0x1C' in l for l in r.lines), r.lines)

    def test_a_changed_literal_word_fails(self):
        changed = bytearray(ARMLINK_TEXT); changed[0x2C] ^= 4             # g_total + 4 in the image
        r = self.project(bytes(changed))
        self.assertFalse(r.whole)
        self.assertTrue(any('first at +0x2C' in l for l in r.lines), r.lines)

    def test_an_unresolved_symbol_fails(self):
        r = self.project(data_rows=False)
        self.assertFalse(r.whole)
        self.assertIn('ERROR .text+0x2C R_ARM_ABS32 g_total: unresolved symbol g_total', r.lines)
        self.assertEqual(r.lines[-1], '-- not placed')


class RepeatedSectionTest(unittest.TestCase):
    """One object can hold several sections of one name; placements are kept by section index."""

    def setUp(self):
        # armcc's two .data sections of one file: scalars, then a table of pointers
        self.info = armtc.ObjectInfo(
            {1: ('.text', 8), 5: ('.data', 48), 6: ('.data', 72), 3: ('.bss', 16)},
            [armtc.Sym('f', 1, 'Gb', 1, 'Code', 8), armtc.Sym('state', 0, 'Gb', 5, 'Data', 1),
             armtc.Sym('table', 0, 'Gb', 6, 'Data', 72), armtc.Sym('buf', 0, 'Gb', 3, 'Data', 16)],
            {1: 4, 5: 4, 6: 4, 3: 4},
            {1: 'SHF_ALLOC + SHF_EXECINSTR', 5: 'SHF_ALLOC + SHF_WRITE', 6: 'SHF_ALLOC + SHF_WRITE',
             3: 'SHF_ALLOC + SHF_WRITE'},
            {1: 'SHT_PROGBITS', 5: 'SHT_PROGBITS', 6: 'SHT_PROGBITS', 3: 'SHT_NOBITS'})
        self.placements = [('.text', 0x08001000), ('.data', 0x20000100), ('.data', 0x20000130), ('.bss', 0x20000400)]

    def test_each_placement_takes_its_own_section(self):
        self.assertEqual(objplace.section_indexes(self.info, self.placements),
                         ([(1, 0x08001000), (5, 0x20000100), (6, 0x20000130), (3, 0x20000400)], []))

    def test_sizes_follow_the_section_not_the_name(self):
        self.assertEqual(objplace.sized_placements(self.info, self.placements),
                         [('.text', 0x08001000, 8), ('.data', 0x20000100, 48), ('.data', 0x20000130, 72),
                          ('.bss', 0x20000400, 16)])

    def test_labels_name_repeated_sections_by_index(self):
        self.assertEqual([objplace.section_label(self.info, i) for i in (1, 5, 6)], ['.text', '.data#5', '.data#6'])
        o = objplace.Obj('x.o', {i: objplace.Sec(n, 'SHT_PROGBITS', '', 0, z, 4) for i, (n, z) in self.info.sections.items()}, {})
        self.assertEqual([objplace.label_index(o, l) for l in ('.text', '.data#5', '.data#6')], [1, 5, 6])

    def test_a_repeated_name_left_partly_unplaced_is_an_error(self):
        _, errors = objplace.section_indexes(self.info, self.placements[:2] + self.placements[3:])
        self.assertEqual(errors, ['section .data: 2 sections of that name, 1 placed'])

    def test_a_single_section_placed_twice_is_an_error(self):
        _, errors = objplace.section_indexes(self.info, self.placements + [('.text', 0x08002000)])
        self.assertEqual(errors, ['section .text: placed 2 times', 'section .text: placed 2 times'])

    def test_unique_names_are_unchanged(self):
        info = armtc.ObjectInfo({1: ('.text', 8), 2: ('.data', 4)}, [], {}, {}, {})
        self.assertEqual(objplace.section_indexes(info, [('.data', 0x20000000), ('.text', 0x08000000)]),
                         ([(2, 0x20000000), (1, 0x08000000)], []))


# fixtures/objplace/repeat.c, compiled as unit.c is but at -O1 (`armcc -c --cpu Cortex-M3 -O1
# repeat.c -o repeat.o`): .text (#1) with two R_ARM_THM_JUMP11 tail calls to bump and an
# R_ARM_ABS32 to the first .data; two .data sections, #4 (state, count) and #5 (ops, a table of
# pointers to bump and step). R_TEXT, R_DATA4, R_DATA5 are their bytes before relocation (file
# offsets 60, 224, 232). R_ARMLINK_TEXT and R_ARMLINK_DATA are what armlink made of the object with
# .text FIXED at 0x08000100 and both .data (one scatter selector) from 0x20000000.
R_TEXT = bytes.fromhex('08480168491c01607047002800d0fee704484168491c41607047002800d0fee77047000000000000')
R_DATA4 = bytes.fromhex('0100000002000000')
R_DATA5 = bytes.fromhex('000000000000000003000000')
R_ARMLINK_TEXT = bytes.fromhex('08480168491c01607047002800d0f7e704484168491c41607047002800d0efe77047000000000020')
R_ARMLINK_DATA = bytes.fromhex('0100000002000000010100080b01000803000000')
R_PLACEMENTS = [('.text', TEXT), ('.data', 0x20000000), ('.data', 0x20000008)]


def repeat_obj():
    b = bytearray(1672)
    b[:4] = b'\xf7ORC'
    for off, data in ((60, R_TEXT), (224, R_DATA4), (232, R_DATA5)): b[off:off + len(data)] = data
    return objplace.fill(objplace.parse(read('repeat_fromelf.txt'), 'repeat.o'), bytes(b))


class RepeatedSectionLinkTest(unittest.TestCase):
    """The repeat fixture: two .data sections and narrow branches, against armlink's bytes."""

    def setUp(self):
        self.o = repeat_obj()
        self.info = armtc.parse_symbols(read('repeat_syms.txt'))

    def test_the_fixture_reads(self):
        self.assertEqual([(i, s.name, s.size) for i, s in sorted(self.o.sections.items()) if 'SHF_ALLOC' in s.flags],
                         [(1, '.text', 40), (4, '.data', 8), (5, '.data', 12)])
        self.assertEqual([(r.offset, r.tname, r.sname) for r in self.o.relocs[1]],
                         [(0xE, 'R_ARM_THM_JUMP11', 'bump'), (0x1E, 'R_ARM_THM_JUMP11', 'bump'), (0x24, 'R_ARM_ABS32', '.data')])

    def test_same_bytes_as_armlink(self):
        bases, errors = objplace.bases_for(self.o, self.info, R_PLACEMENTS, [])
        self.assertEqual(errors, [])
        self.assertEqual(bases, {1: TEXT, 4: 0x20000000, 5: 0x20000008})
        out, errors, count = objplace.link(self.o, bases, {})
        self.assertEqual(errors, [])
        self.assertEqual(count, 5)
        self.assertEqual(out[1], R_ARMLINK_TEXT)
        self.assertEqual(out[4] + out[5], R_ARMLINK_DATA)

    def test_place_object_labels_each_section(self):
        root = tempfile.mkdtemp(); self.addCleanup(lambda: __import__('shutil').rmtree(root, ignore_errors=True))
        img = bytearray(b'\xff' * 0x100) + bytearray(R_ARMLINK_TEXT)
        with open(os.path.join(root, 'fw.hex'), 'w') as f: f.write(ehex.write(0x08000000, bytes(img)))
        m = armmap.Map([Row(TEXT, 0xA, 'bump', 'code', ''), Row(TEXT + 0xA, 0x10, 'step', 'code', ''),
                        Row(TEXT + 0x1A, 0xE, 'maybe', 'code', '')],
                       [Row(0x20000000, 4, 'state', 'rw', ''), Row(0x20000004, 4, 'count', 'rw', ''),
                        Row(0x20000008, 12, 'ops', 'rw', '')])
        src = os.path.join(root, 'repeat.c')
        with open(src, 'w') as f: f.write(read('repeat.c'))
        cfg = types.SimpleNamespace(image=os.path.join(root, 'fw.hex'), root=root)
        r = objplace.place_object(cfg, None, src, 'repeat.o', self.info, m, o=self.o)
        self.assertNotIn('-- not placed', r.lines)
        self.assertEqual(sorted(r.placed), ['.data#4', '.data#5', '.text'])
        self.assertEqual(r.placed['.text'], (TEXT, R_ARMLINK_TEXT))
        self.assertEqual(r.placed['.data#4'], (0x20000000, R_ARMLINK_DATA[:8]))
        self.assertEqual(r.placed['.data#5'], (0x20000008, R_ARMLINK_DATA[8:]))
        self.assertIn(f'{"bump":28} 0x{TEXT:08X}  MATCH', r.lines)
        self.assertIn('.data#5                      0x20000008  placed (RAM, not compared: the image has no region table)', r.lines)


class NarrowBranchTest(unittest.TestCase):
    """R_ARM_THM_JUMP11 (B) and R_ARM_THM_JUMP8 (B<cond>): a 2-byte field, the addend in halfwords."""

    def put(self, tname, h, S, P, T=1):
        b = bytearray(struct.pack('<H', h))
        e = objplace.apply(objplace.Rel(0, 0, tname, 0, 's'), b, S, T, P)
        return e or struct.unpack('<H', b)[0]

    def test_jump11(self):
        self.assertEqual(self.put('R_ARM_THM_JUMP11', 0xE7FE, 0x08000100, 0x0800010E), 0xE7F7)   # the fixture's first
        self.assertEqual(self.put('R_ARM_THM_JUMP11', 0xE7FE, 0x08000200, 0x08000100), 0xE07E)
        self.assertRegex(self.put('R_ARM_THM_JUMP11', 0xE7FE, 0x08000A00, 0x08000100), 'out of range')
        self.assertRegex(self.put('R_ARM_THM_JUMP11', 0xD0FE, 0x08000100, 0x08000100), 'not a narrow unconditional branch')
        self.assertRegex(self.put('R_ARM_THM_JUMP11', 0xE7FE, 0x20000000, 0x08000100, T=0), 'not Thumb code')

    def test_jump8(self):
        self.assertEqual(self.put('R_ARM_THM_JUMP8', 0xD1FE, 0x08000120, 0x08000100), 0xD10E)     # bne +0x20
        self.assertEqual(self.put('R_ARM_THM_JUMP8', 0xD0FE, 0x08000080, 0x08000100), 0xD0BE)     # beq -0x80
        self.assertRegex(self.put('R_ARM_THM_JUMP8', 0xD0FE, 0x08000300, 0x08000100), 'out of range')
        self.assertRegex(self.put('R_ARM_THM_JUMP8', 0xE7FE, 0x08000120, 0x08000100), 'not a narrow conditional branch')
        self.assertRegex(self.put('R_ARM_THM_JUMP8', 0xDEFE, 0x08000120, 0x08000100), 'not a narrow conditional branch')

    def test_width(self):
        self.assertEqual([objplace.relocation_width(objplace.Rel(0, 0, t, 0, '')) for t in
                          ('R_ARM_THM_JUMP11', 'R_ARM_THM_JUMP8', 'R_ARM_THM_CALL', 'R_ARM_ABS32')], [2, 2, 4, 4])


HOME = os.environ.get('EXHUME_ARMCC_HOME')


def can_run():
    import shutil, subprocess
    if os.environ.get('EXHUME_ARMCC_RUNNER', 'docker') == 'wine': return bool(shutil.which(os.environ.get('WINE', 'wine')))
    return bool(shutil.which('docker')) and subprocess.run(['docker', 'info'], capture_output=True).returncode == 0


@unittest.skipUnless(HOME and can_run(), 'needs EXHUME_ARMCC_HOME and Docker or Wine')
class ToolchainTest(unittest.TestCase):
    """`objplace.py check` and `match` on a project whose image armlink made from unit.c."""

    @classmethod
    def setUpClass(cls):
        import shutil
        cls.root = root = tempfile.mkdtemp(dir=os.path.expanduser('~'))
        os.makedirs(os.path.join(root, 'map'))
        shutil.copy(os.path.join(FX, 'unit.c'), os.path.join(root, 'unit.c'))
        tc = cls.tc = armtc.Toolchain(HOME, root)
        work = os.path.join(root, 'orig'); os.makedirs(work)
        ok, log = tc.compile(os.path.join(root, 'unit.c'), os.path.join(work, 'unit.o'), '--cpu Cortex-M3 -O0')
        assert ok, log
        with open(os.path.join(work, 'u.sct'), 'w') as f:
            f.write(f'LR_1 0x{TEXT:08X} {{\n  ER_1 0x{TEXT:08X} FIXED {{ unit.o(.text) }}\n}}\n'
                    f'LR_2 0x{CONST:08X} {{\n  ER_2 0x{CONST:08X} FIXED {{ unit.o(.constdata) }}\n}}\n')
        with open(os.path.join(work, 'u.sym'), 'w') as f:
            f.write(armmap.symdefs_text([(G_TOTAL, 'D', 'g_total')]))
        ok, log = tc.link([os.path.join(work, 'unit.o'), os.path.join(work, 'u.sym')], os.path.join(work, 'u.sct'),
                          os.path.join(work, 'u.axf'))
        assert ok, log
        b = tc.tobin(os.path.join(work, 'u.axf'), os.path.join(work, 'bin'))
        img = bytearray(b'\xff' * 0x200) + bytearray(b['ER_2'])
        img[0x100:0x100 + len(b['ER_1'])] = b['ER_1']
        with open(os.path.join(root, 'fw.hex'), 'w') as f: f.write(ehex.write(0x08000000, bytes(img)))
        armmap.save(os.path.join(root, 'map', 'functions.tsv'),
                    [Row(TEXT, 0x12, 'helper', 'code', ''), Row(TEXT + 0x12, 0x1E, 'caller', 'code', '')])
        armmap.save(os.path.join(root, 'map', 'data.tsv'), [Row(CONST, 4, 'k_table', 'ro', ''), Row(G_TOTAL, 4, 'g_total', 'rw', '')])
        with open(os.path.join(root, 'exhume.toml'), 'w') as f:
            f.write(f'[project]\nname = "t"\n[toolchain]\nprofile = "armcc5"\nhome = "{HOME}"\nc_opts = "--cpu Cortex-M3 -O0"\n'
                    f'[binary]\nimage = "fw.hex"\n')
        cls.cfg = os.path.join(root, 'exhume.toml')

    @classmethod
    def tearDownClass(cls):
        import shutil
        cls.tc.stop()
        shutil.rmtree(cls.root, ignore_errors=True)

    def run_main(self, args):
        import contextlib, io
        out = io.StringIO()
        with contextlib.redirect_stdout(out): rc = objplace.main(['--config', self.cfg] + args)
        return rc, out.getvalue()

    def test_armlink_and_placement_agree(self):
        rc, out = self.run_main(['check', os.path.join(self.root, 'unit.c')])
        self.assertEqual(rc, 0, out)
        self.assertIn('3 relocations: armlink and placement SAME; armmatch whole, placement whole', out)

    def test_match(self):
        rc, out = self.run_main(['match', os.path.join(self.root, 'unit.c')])
        self.assertEqual(rc, 0, out)
        self.assertIn('WHOLE FILE MATCHES', out)


if __name__ == '__main__':
    unittest.main()
