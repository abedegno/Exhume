"""The whole-image gate: its parts on synthetic input, and the test firmware rebuilt in pieces."""
import os, shutil, subprocess, sys, tempfile, textwrap, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.dirname(HERE)
EXHUME = os.path.dirname(os.path.dirname(PROFILE))
sys.path.insert(0, os.path.join(PROFILE, 'tools')); sys.path.insert(0, os.path.join(PROFILE, 'testfw'))
sys.path.insert(0, os.path.join(EXHUME, 'tools'))
import armgate, armdata, armmap, ehex
from armmap import Row

HOME = os.environ.get('EXHUME_ARMCC_HOME')
DOCKER = shutil.which('docker') and subprocess.run(['docker', 'info'], capture_output=True).returncode == 0
WINE = os.environ.get('EXHUME_ARMCC_RUNNER') == 'wine' and shutil.which('wine')
TOOLCHAIN = HOME and (DOCKER or WINE)


def write(text, name):
    p = os.path.join(tempfile.mkdtemp(), name)
    with open(p, 'w', newline='') as f: f.write(text)
    return p


def read(p):
    with open(p) as f: return f.read()


def put(p, text):
    with open(p, 'w') as f: f.write(text)


class LinkNamesTest(unittest.TestCase):
    def test_placeholders_sort_between_their_neighbours(self):
        m = armmap.Map([Row(0x08000100, 8, 'alpha', 'code', ''), Row(0x08000108, 8, 'sub_08000108', 'code', ''),
                        Row(0x08000110, 8, 'sub_08000110', 'code', ''), Row(0x08000118, 8, 'beta', 'code', '')], [])
        ln, notes = armgate.link_names(m, [armgate.Piece(0x08000100, 0x08000120)])
        self.assertEqual(ln['sub_08000108'], 'alpha_08000108')
        self.assertEqual(ln['sub_08000110'], 'alpha_08000110')
        self.assertEqual(ln['sub_08000118'], 'beta')          # a source still spelling a named row
        self.assertEqual(notes, [])
        names = ['alpha', ln['sub_08000108'], ln['sub_08000110'], 'beta']
        self.assertEqual(names, sorted(names))

    def test_a_leading_placeholder_sorts_first(self):
        m = armmap.Map([Row(0x08000100, 8, 'sub_08000100', 'code', ''), Row(0x08000108, 8, 'Alpha', 'code', '')], [])
        ln, notes = armgate.link_names(m, [armgate.Piece(0x08000100, 0x08000110)])
        self.assertLess(ln['sub_08000100'], 'Alpha')
        self.assertEqual(notes, [])

    def test_names_out_of_order_are_noted(self):
        m = armmap.Map([Row(0x08000100, 8, 'zeta', 'code', ''), Row(0x08000108, 8, 'alpha', 'code', '')], [])
        ln, notes = armgate.link_names(m, [armgate.Piece(0x08000100, 0x08000110)])
        self.assertEqual(len(notes), 1)
        self.assertIn('zeta', notes[0])

    def test_the_startup_piece_is_not_name_ordered(self):
        m = armmap.Map([Row(0x08000000, 8, 'zeta', 'code', ''), Row(0x08000008, 8, 'alpha', 'code', '')], [])
        ln, notes = armgate.link_names(m, [armgate.Piece(0x08000000, 0x08000010, True)])
        self.assertEqual(notes, [])


class ScatterTest(unittest.TestCase):
    def test_fixed_sections_meeting_off_a_boundary_share_a_region(self):
        lines, pads = armgate.fixed_scatter([('a.o', '.data', 0x20000010, 0x0A), ('b.o', '.data', 0x2000001A, 4)], 'F', 0x08100000)
        text = '\n'.join(lines)
        self.assertEqual(text.count('ER_F'), 1)
        self.assertIn('a.o(.data, +FIRST) b.o(.data)', text)
        self.assertEqual(pads, [])

    def test_an_unaligned_group_gets_a_pad_of_its_kind(self):
        lines, pads = armgate.fixed_scatter([('a.o', '.bss', 0x20000102, 6)], 'F', 0x08100000)
        self.assertEqual(pads, [(1, 2, 'zi')])
        self.assertIn('ER_F1 0x20000100 { gate_pad.o(.pad1, +FIRST) a.o(.bss) }', '\n'.join(lines))
        self.assertIn('zero_init', armgate.pad_source(pads))

    def test_listing_parses_symbols_sections_and_load_regions(self):
        text = ('    __scatterload                            0x08000035   Thumb Code    28  init.o(.text)\n'
                '    i.big_switch                             0x080000cc   Section        0  tables.o(i.big_switch)\n'
                '  Load Region LR_IROM (Base: 0x08000000, Size: 0x00000954, Max: 0x00008000, ABSOLUTE, '
                'COMPRESSED[0x00000818])\n'
                '    0x08000034   0x00000024   Code   RO           59    .text               mc_w.l(init.o)\n'
                '    0x0800063e   0x00000002   PAD\n')
        p = write(text, 'x.lst')
        syms, secs, loads = armgate.listing(p)
        self.assertEqual(syms['__scatterload'], (0x08000035, 'Thumb Code', 'init.o', '.text'))
        self.assertNotIn('i.big_switch', syms)
        self.assertEqual(secs, [(0x08000034, 0x24, '.text', 'init.o')])
        self.assertEqual(loads['LR_IROM'], (0x08000000, 0x818))


class DataStageTest(unittest.TestCase):
    SRC = textwrap.dedent('''\
        /* opts: -O1 */
        #include "x.h"
        #define TWICE(x) ((x) * 2)
        struct S { int a; };
        static const unsigned char tbl[3] = { 1, 2, 3 };
        unsigned int count = 5;
        int get(int i) { return tbl[i] + count; }
        unsigned short lookup(unsigned int i)
        {
            static const unsigned short steps[2] = { 3, 5 };
            return steps[i & 1];
        }
        const char *name(void) { return "a { b"; }
        ''')

    def test_function_bodies_go_but_statics_stay(self):
        body, kept, statics = armdata.strip_functions(self.SRC, 't')
        self.assertIn('int get(int i) ;', body)
        self.assertNotIn('return tbl', body)
        self.assertIn('static const unsigned short steps[2]', body)
        self.assertEqual(kept, ['lookup'])
        self.assertEqual(statics, {'steps'})
        self.assertIn('const char *name(void) ;', body)
        self.assertIn('struct S { int a; };', body)
        self.assertIn('static const unsigned char tbl[3] = { 1, 2, 3 };', body)

    def test_copies_owned_elsewhere_become_declarations(self):
        text = 'unsigned char g_a[4];\nunsigned char g_b[4];\n'
        self.assertEqual(armdata.extern_copies(text, {'g_b'}), 'unsigned char g_a[4];\nextern unsigned char g_b[4];\n')

    def test_one_object_order_from_every_kind(self):
        first = {'a.c': [0x08001000, 0x20000000, None], 'b.c': [None, 0x20000010, 0x20000100],
                 'c.c': [0x08001010, None, 0x20000080]}
        order, conflict = armdata.link_order(first)
        self.assertEqual(conflict, [])
        self.assertEqual(order, ['a.c', 'c.c', 'b.c'])

    def test_a_conflict_means_an_owner_is_wrong(self):
        first = {'a.c': [0x08001000, 0x20000010, None], 'b.c': [0x08001010, 0x20000000, None]}
        order, conflict = armdata.link_order(first)
        self.assertEqual(sorted(conflict), ['a.c', 'b.c'])


class EhexWriteTest(unittest.TestCase):
    def test_round_trip_keeps_the_layout(self):
        data = bytes(range(256)) * 300                 # crosses a 64 KB boundary
        text = ehex.write(0x0800FF00, data, entry=0x08000101)
        p = write(text, 'a.hex')
        base, back = ehex.read(p)
        self.assertEqual((base, back), (0x0800FF00, data))
        self.assertEqual(ehex.layout(p), (16, '\r\n', 0x08000101))
        self.assertEqual(ehex.write(base, back, like=p), text)

    def test_record_width_and_line_ends_are_copied(self):
        p = write(ehex.write(0x08000000, bytes(100), width=32, end='\n'), 'a.hex')
        self.assertEqual(ehex.layout(p), (32, '\n', None))


def make_project(root, key, pieces, extra=''):
    """The test firmware as a finished project: every row named and given its file (as merging
    leaves it), and a pieces file."""
    fpath, dpath = os.path.join(root, 'map', 'functions.tsv'), os.path.join(root, 'map', 'data.tsv')
    functions, data = armmap.load(fpath), armmap.load(dpath)
    src = {os.path.splitext(n)[0]: 'src/' + n for n in os.listdir(os.path.join(root, 'src'))}
    by_addr = {}
    for name, (a, s) in key['answers'].items(): by_addr.setdefault(a, []).append((name, s))
    for r in functions + data:
        hits = sorted(by_addr.get(r.addr, []))
        if hits: r.name, r.file = hits[0][0], src[hits[0][1]]
    lib = {}
    for name, a in key['lib'].items(): lib.setdefault(a, []).append(name)
    for r in functions:
        if armmap.placeholder(r.name) is not None and r.addr in lib: r.name = sorted(lib[r.addr])[0]
    armmap.save(fpath, functions); armmap.save(dpath, data)
    with open(os.path.join(root, 'pieces.toml'), 'w') as f:
        f.write('known_diffs = []\n' + ''.join(f'[[piece]]\nstart = "{s:08X}"\nend = "{e:08X}"\n' +
                                               ('startup = true\n' if st else '') for s, e, st in pieces))
    # the test firmware's startup file has no stack area: its link took the stack top from outside
    with open(os.path.join(root, 'outside.sym'), 'w') as f: f.write(answerkey_sp())
    with open(os.path.join(root, 'exhume.toml'), 'w') as f:
        f.write(textwrap.dedent(f'''
            [project]
            name = "testfw"
            [toolchain]
            profile = "armcc5"
            home = "{HOME}"
            c_opts = "--cpu Cortex-M3 -O1 -Otime --split_sections --library_type=microlib"
            [binary]
            image = "target/testfw.hex"
            [gate]
            symdefs = "outside.sym"
        ''') + extra)


def answerkey_sp():
    import answerkey
    return answerkey.SP_SYMDEFS


def pieces_of(key):
    """Three pieces: the startup and library code, then the C functions in two runs."""
    a = key['answers']
    return [(0x08000000, a['big_switch'][0], True), (a['big_switch'][0], a['crc16'][0], False),
            (a['crc16'][0], a['crc_hi'][0], False)]


@unittest.skipUnless(TOOLCHAIN, 'needs EXHUME_ARMCC_HOME and Docker (or EXHUME_ARMCC_RUNNER=wine)')
class GateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import armtc, answerkey, config
        cls.root = tempfile.mkdtemp(dir=os.path.expanduser('~'), prefix='exhume-gate-')
        cls.tc = armtc.Toolchain(HOME, cls.root)
        cls.key = answerkey.build(cls.tc, cls.root)
        make_project(cls.root, cls.key, pieces_of(cls.key))
        cls.cfg = config.Config(os.path.join(cls.root, 'exhume.toml'))

    @classmethod
    def tearDownClass(cls):
        cls.tc.stop()
        shutil.rmtree(cls.root, ignore_errors=True)

    def gate(self, **kw):
        lines = []
        r = armgate.run(self.cfg, self.cfg.file, log=lines.append, jobs=kw.pop('jobs', 2), **kw)
        return r, '\n'.join(lines)

    def test_the_image_is_rebuilt_identical(self):
        r, out = self.gate()
        self.assertTrue(r.ok, out)
        self.assertIn('IDENTICAL to the original file', out)
        self.assertEqual(out.count('MATCHES'), 4, out)          # three pieces and the data stage

    def test_a_changed_function_fails_its_piece(self):
        p = os.path.join(self.root, 'src', 'proto.c')
        saved = read(p)
        try:
            put(p, saved.replace('g_soc_tenths / 10', 'g_soc_tenths / 9'))
            r, out = self.gate(only=2)
            self.assertFalse(r.ok, out)
            self.assertIn('not known: soc_percent', out)
        finally:
            put(p, saved)

    def test_known_diffs_pass(self):
        p = os.path.join(self.root, 'src', 'proto.c'); pf = os.path.join(self.root, 'pieces.toml')
        saved, spec = read(p), read(pf)
        try:
            put(p, saved.replace('g_soc_tenths / 10', 'g_soc_tenths / 9'))
            put(pf, spec.replace('known_diffs = []', 'known_diffs = ["soc_percent"]'))
            r, out = self.gate(only=2)
            self.assertTrue(r.ok, out)
            self.assertIn('(all known)', out)
        finally:
            put(p, saved); put(pf, spec)

    def test_a_wrong_initialiser_fails_the_data_stage(self):
        p = os.path.join(self.root, 'src', 'tables.c')
        saved = read(p)
        try:
            put(p, saved.replace('1000, 1001', '1000, 1009'))
            r, out = self.gate()
            self.assertFalse(r.ok, out)
            self.assertIn('bytes differ', out.split('data stage:')[1])
        finally:
            put(p, saved)


if __name__ == '__main__':
    unittest.main()
