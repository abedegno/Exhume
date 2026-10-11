"""The test firmware, stripped of its names, matched again from its own sources."""
import os, shutil, subprocess, sys, tempfile, textwrap, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.dirname(HERE)
EXHUME = os.path.dirname(os.path.dirname(PROFILE))
sys.path.insert(0, os.path.join(PROFILE, 'tools')); sys.path.insert(0, os.path.join(PROFILE, 'testfw'))
sys.path.insert(0, os.path.join(EXHUME, 'tools'))
import armtc, armmatch, answerkey, config

HOME = os.environ.get('EXHUME_ARMCC_HOME')
DOCKER = shutil.which('docker') and subprocess.run(['docker', 'info'], capture_output=True).returncode == 0


@unittest.skipUnless(HOME and DOCKER, 'needs EXHUME_ARMCC_HOME and Docker')
class TestFirmware(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(dir=os.path.expanduser('~'), prefix='exhume-testfw-')
        cls.tc = armtc.Toolchain(HOME, cls.root)
        cls.key = answerkey.build(cls.tc, cls.root)
        with open(os.path.join(cls.root, 'exhume.toml'), 'w') as f:
            f.write(textwrap.dedent(f'''
                [project]
                name = "testfw"
                [toolchain]
                profile = "armcc5"
                home = "{HOME}"
                [binary]
                image = "target/testfw.hex"
            '''))
        cls.cfg = config.Config(os.path.join(cls.root, 'exhume.toml'))
        cls.AT = '/* at: ' + ', '.join(f'{nm} 0x{a:08X}' for nm, (a, s) in sorted(cls.key['answers'].items())) + ' */\n'
        # each source gains an at: line for the names it defines and uses, as an agent would write
        at = '/* at: ' + ', '.join(f'{nm} 0x{a:08X}' for nm, (a, s) in sorted(cls.key['answers'].items())) + ' */\n'
        for n in os.listdir(os.path.join(cls.root, 'src')):
            p = os.path.join(cls.root, 'src', n)
            with open(p) as f: body = f.read()
            # an assembly source takes its annotations in a ; comment
            with open(p, 'w') as f: f.write(('; ' + at.replace('/*', '').replace('*/', '') if n.endswith('.s') else at) + body)

    @classmethod
    def tearDownClass(cls):
        cls.tc.stop()
        shutil.rmtree(cls.root, ignore_errors=True)

    def run_src(self, stem, text=None):
        p = os.path.join(self.root, 'src', stem + '.c')
        if text is not None:
            with open(p, 'w') as f: f.write(text)
        return armmatch.match(self.cfg, p)

    def source(self, stem):
        with open(os.path.join(self.root, 'src', stem + '.c')) as f: return f.read()

    def check(self, stem):
        r = self.run_src(stem)
        self.assertTrue(r.whole, '\n'.join(r.lines))
        return r

    def test_crc(self): self.check('crc')
    def test_proto(self): self.check('proto')
    def test_main(self): self.check('main')
    def test_tables(self): self.check('tables')

    def test_init(self): self.check('init')

    def test_vectors_assembly(self):
        p = os.path.join(self.root, 'src', 'vectors.s')
        with open(p) as f: body = f.read()
        lib = ', '.join(f'{n} 0x{a:08X}' for n, a in self.key['lib'].items() if n == '__main')
        with open(p, 'w') as f: f.write(f'; at: {lib}\n' + body)
        r = armmatch.match(self.cfg, p)
        self.assertTrue(r.whole, '\n'.join(r.lines))

    def test_uninitialised_candidate_for_initialised_data_is_not_a_match(self):
        # declared without an initialiser, g_init goes to .bss: its zeros must be compared against
        # the original's initial values, not skipped
        r = self.run_src('initzero', self.AT + 'unsigned char g_init[16];\nunsigned char *init(void) { return g_init; }\n')
        self.assertFalse(r.whole, '\n'.join(r.lines))

    EXTRA = '\nint extra_unused(int x) { return x * 3 + 1; }\n'

    def test_a_function_the_image_lacks_is_an_error(self):
        r = self.run_src('crcextrafn', self.source('crc') + self.EXTRA)
        self.assertFalse(r.whole)
        self.assertTrue(any(l.startswith('ERROR no address for section i.extra_unused') for l in r.lines), '\n'.join(r.lines))

    def test_unused_dropped_lets_a_library_file_carry_functions_the_image_lacks(self):
        r = self.run_src('crcunused', '/* unused: dropped */\n' + self.source('crc') + self.EXTRA)
        self.assertTrue(r.whole, '\n'.join(r.lines))
        self.assertTrue(any(l.startswith('NOTE') and 'extra_unused' in l for l in r.lines), '\n'.join(r.lines))

    def test_unused_dropped_alone_is_never_whole(self):
        r = self.run_src('onlyunused', '/* unused: dropped */\n' + self.EXTRA)
        self.assertFalse(r.whole, '\n'.join(r.lines))

    def test_wrong_initialiser_is_reported(self):
        r = self.run_src('protoinit', self.source('proto').replace('= 955', '= 956'))
        self.assertFalse(r.whole)
        self.assertTrue(any('bytes differ' in l for l in r.lines), '\n'.join(r.lines))

    # soc_percent alone (it only reads RAM it doesn't define) plus a table nothing references: armcc
    # gives the table its own .constdata, which the original's armlink would have dropped
    SOC = ('extern unsigned short g_soc_tenths;\n'
           'unsigned char soc_percent(void) { return (unsigned char)(g_soc_tenths / 10); }\n'
           'const unsigned char g_extra_tbl[4] = { 1, 2, 3, 4 };\n')

    def test_data_the_map_lacks_blocks_a_whole_match(self):
        r = self.run_src('socextra', self.AT + self.SOC)
        self.assertFalse(r.whole, '\n'.join(r.lines))
        self.assertTrue(any(l.startswith('UNPLACED') and 'g_extra_tbl' in l for l in r.lines), '\n'.join(r.lines))

    def test_dropped_data_is_allowed(self):
        r = self.run_src('socdropped', self.AT + '/* dropped: g_extra_tbl */\n' + self.SOC)
        self.assertTrue(r.whole, '\n'.join(r.lines))

    def test_a_shorter_table_is_not_a_match(self):
        text = self.source('crc').replace(', 0x4400 }', ' }').replace('crc_hi[16]', 'crc_hi[15]')
        r = self.run_src('crcshort', text)
        self.assertFalse(r.whole, '\n'.join(r.lines))

    def test_a_function_with_no_map_row_has_no_known_size(self):
        fpath = os.path.join(self.root, 'map', 'functions.tsv')
        with open(fpath) as f: saved = f.read()
        crc_addr = self.key['answers']['crc16'][0]
        try:
            with open(fpath, 'w') as f:
                f.write(''.join(l for l in saved.splitlines(True) if not l.startswith(f'{crc_addr:08X}')))
            r = self.run_src('crc')
            self.assertFalse(r.whole, '\n'.join(r.lines))
            self.assertTrue(any('no map row' in l for l in r.lines), '\n'.join(r.lines))
        finally:
            with open(fpath, 'w') as f: f.write(saved)

    def test_wrong_constant_is_reported(self):
        src = os.path.join(self.root, 'src', 'proto.c')
        with open(src) as f: text = f.read()
        bad = os.path.join(self.root, 'src', 'protobad.c')
        with open(bad, 'w') as f: f.write(text.replace('g_soc_tenths / 10', 'g_soc_tenths / 9'))
        r = armmatch.match(self.cfg, bad)
        self.assertFalse(r.whole)
        self.assertTrue(any('bytes differ' in l for l in r.lines), '\n'.join(r.lines))

    def test_unknown_global_is_an_error(self):
        bad = os.path.join(self.root, 'src', 'extra.c')
        with open(bad, 'w') as f: f.write('int g_nowhere = 5;\nint get(void){ return g_nowhere; }\n')
        r = armmatch.match(self.cfg, bad)
        self.assertFalse(r.whole)
        self.assertTrue(any(l.startswith('ERROR no address for section i.get') for l in r.lines), '\n'.join(r.lines))

    def test_unreferenced_data_marked_dropped_is_noted(self):
        # proto.c marks g_proto_name /* dropped: */: armlink left it out of the image
        r = self.check('proto')
        self.assertTrue(any(l.startswith('NOTE') and 'g_proto_name' in l for l in r.lines), '\n'.join(r.lines))


if __name__ == '__main__':
    unittest.main()
