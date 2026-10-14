"""The search tools against the real compiler: batches, pool order, a table. Skipped without
EXHUME_ARMCC_HOME and a way to run it (Docker, or EXHUME_ARMCC_RUNNER=wine with wine on PATH)."""
import contextlib, io, os, shutil, subprocess, sys, tempfile, textwrap, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import armtc, armmap, armvariant as V, armbrute as B, armreduce as R, armpermute as P

HOME = os.environ.get('EXHUME_ARMCC_HOME')
RUNNER = os.environ.get('EXHUME_ARMCC_RUNNER', 'docker')
if RUNNER == 'wine':
    CAN_RUN = bool(shutil.which(os.environ.get('WINE', 'wine')))
else:
    CAN_RUN = bool(shutil.which('docker')) and subprocess.run(['docker', 'info'], capture_output=True).returncode == 0
OPTS = '--cpu Cortex-M3 -O1 -Otime --split_sections'

STORE = '''typedef unsigned char u8; typedef unsigned short u16;
extern u16 g_buf[50];
extern struct { u8 n; u8 idx; } g_pos;
extern struct { u16 a; u16 val; } g_src;
void store(void)
{
    {{@g_buf[g_pos.idx] = g_src.val;@|@{ u16 *p; u8 k; (p = g_buf)[k = g_pos.idx] = g_src.val; }@}}
    if (g_pos.idx < 49) g_pos.idx++; else g_pos.idx = 0;
}
'''


@unittest.skipUnless(HOME and CAN_RUN, 'needs EXHUME_ARMCC_HOME and Docker or Wine')
class ToolchainSearchTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(dir=os.path.expanduser('~'))
        cls.tc = armtc.Toolchain(HOME, cls.root)
        cls.runner = V.Runner(types.SimpleNamespace(root=cls.root), tc=cls.tc)

    @classmethod
    def tearDownClass(cls):
        cls.tc.stop()
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_a_batch_survives_a_failing_file(self):
        srcs = ['unsigned char tenth(unsigned short x) { return x / 10; }\n',
                'int broken(void) { return undefined_name; }\n',
                'int triple(int a) { return a * 3; }\n']
        lst, log = V.compile_batch(self.runner, srcs, OPTS, os.path.join(self.root, 'b1'))
        self.assertEqual([x is not None for x in lst], [True, False, True], log)
        got, pool = V.func_bytes(lst[0], 'tenth')
        self.assertIn('movw r1, #0xcccd', V.listing(got, pool))          # -Otime: divide by multiplying
        self.assertTrue(V.func_bytes(lst[2], 'triple')[0])

    def test_a_side_effect_in_the_array_operand_reorders_the_pool(self):
        parts, opts = B.parse(STORE)
        texts = [B.render(parts, opts, [i]) for i in range(2)]
        lst, log = V.compile_batch(self.runner, texts, OPTS, os.path.join(self.root, 'b2'))
        plain, side = V.pool_order(lst[0], 'store'), V.pool_order(lst[1], 'store')
        self.assertEqual(plain[1], ['g_src', 'g_pos', 'g_buf'], log)      # value, index, array
        self.assertEqual(side[1], ['g_src', 'g_buf', 'g_pos'])             # value, array, index
        self.assertEqual(side[0][0], 'g_pos')                              # the index still loaded first

    def test_table(self):
        with open(os.path.join(self.root, 'exhume.toml'), 'w') as f:
            f.write(textwrap.dedent(f'''
                [project]
                name = "t"
                [toolchain]
                profile = "armcc5"
                home = "{HOME}"
            '''))
        tpl = os.path.join(self.root, 'store.c')
        with open(tpl, 'w') as f: f.write(STORE)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            B.main(['--config', os.path.join(self.root, 'exhume.toml'), tpl, 'store', '--table', '--pool', '--jobs', '1'])
        self.assertIn('loads g_src g_pos g_buf  pool [g_src] [g_pos] [g_buf]', out.getvalue())
        self.assertIn('loads g_pos g_src g_buf  pool [g_src] [g_buf] [g_pos]', out.getvalue())

    def project(self, name, original, func):
        """A project whose 'image' is ORIGINAL's FUNC compiled, at address 0 (a raw image)."""
        root = os.path.join(self.root, name); os.makedirs(os.path.join(root, 'map'))
        lst, log = V.compile_batch(self.runner, [original], OPTS, os.path.join(root, 'orig'))
        got, _ = V.func_bytes(lst[0], func)
        self.assertTrue(got, log)
        with open(os.path.join(root, 'fw.bin'), 'wb') as f: f.write(got)
        armmap.save(os.path.join(root, 'map', 'functions.tsv'), [armmap.Row(0, len(got), func, 'code', '')])
        armmap.save(os.path.join(root, 'map', 'data.tsv'), [])
        with open(os.path.join(root, 'exhume.toml'), 'w') as f:
            f.write(f'[project]\nname = "t"\n[toolchain]\nprofile = "armcc5"\nhome = "{HOME}"\n'
                    f'[binary]\nimage = "fw.bin"\n')
        return root

    def test_brute_finds_the_spelling_and_reduce_keeps_only_it(self):
        head = 'typedef unsigned char u8; typedef unsigned short u16;\n'
        root = self.project('p1', head + 'u8 f(u16 x) { return (x + 1) / 250; }\n', 'f')
        tpl = os.path.join(root, 't.c')
        with open(tpl, 'w') as f: f.write(head + 'u8 f(u16 x) { return {{x||(x + 1)||(x + 2)}} / 250; }\n')
        cfgp = os.path.join(root, 'exhume.toml')
        with contextlib.redirect_stdout(io.StringIO()):
            B.main(['--config', cfgp, tpl, 'f', '--jobs', '1'])
        with open(os.path.join(root, 'build', 'brute', 'f', 'hits.c')) as f: hits = f.read()
        self.assertIn('combination 1 [1]', hits)
        self.assertNotIn('combination 0 ', hits)
        orig, best, dest = (os.path.join(root, n) for n in ('o.c', 'b.c', 'd.c'))
        with open(orig, 'w') as f: f.write(head + '/* one */\nint n;\nu8 f(u16 x) { return x / 250; }\n')
        with open(best, 'w') as f: f.write(head + '/* two */\nint n;\nu8 f(u16 x) { return (x + 1) / 250; }\n')
        with contextlib.redirect_stdout(io.StringIO()):
            R.main(['--config', cfgp, orig, best, dest, 'f'])
        with open(dest) as f: self.assertEqual(f.read(), head + '/* one */\nint n;\nu8 f(u16 x) { return (x + 1) / 250; }\n')

    def test_permuter_runs(self):
        head = 'typedef unsigned char u8; typedef unsigned short u16;\n'
        root = self.project('p2', head + 'u8 g(u16 x, u16 y) { u16 t; t = x + y; return t / 250; }\n', 'g')
        src = os.path.join(root, 'g.c')
        with open(src, 'w') as f: f.write(head + 'u8 g(u16 x, u16 y) { u16 t; t = y + x; return (t + 1) / 250; }\n')
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            P.main(['--config', os.path.join(root, 'exhume.toml'), src, 'g', '--iters', '3', '--jobs', '2', '--seed', '1'])
        self.assertRegex(out.getvalue(), r'g: start score [1-9]')
        self.assertIn('g: best score', out.getvalue())


if __name__ == '__main__':
    unittest.main()
