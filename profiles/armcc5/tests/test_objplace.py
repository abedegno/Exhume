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
import os, sys, tempfile, types, unittest
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
