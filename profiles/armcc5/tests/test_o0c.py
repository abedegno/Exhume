"""o0c.py: the armcc -O0 to C translator, on a synthetic image.

fixtures/o0c/corpus.c was compiled with armcc 5.06u7 (`armcc -c --cpu Cortex-M3 -O0 corpus.c`,
no --split_sections) and linked by armlink with its code at 0x08000000 and its data at
0x20000000; corpus.hex is the code region (its .text, the one literal pool at the end) and
functions.tsv and data.tsv the map, from armlink's symbol listing. expected_file.c is what
`o0c.py file put` writes for it: the drafts of the whole file as one source, which match.py
proves byte for byte (ToolchainTest). The translation tests need no toolchain."""
import contextlib, io, os, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import armmap, ehex, o0c

FX = os.path.join(HERE, 'fixtures', 'o0c')


def context():
    im = ehex.Image(os.path.join(FX, 'corpus.hex'))
    rows = armmap.load(os.path.join(FX, 'functions.tsv'))
    ctx = o0c.Ctx(o0c.Mem(im.base, im.data), [(r.addr, r.size, r.name) for r in rows])
    o0c.global_facts(ctx)
    return ctx


def data_names():
    return {r.addr: r.name for r in armmap.load(os.path.join(FX, 'data.tsv'))}


class TranslateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ctx = context()

    def draft(self, name):
        return o0c.translate(self.ctx, self.ctx.by_name[name])

    def test_every_function_translates(self):
        for a, n, name in self.ctx.rows:
            text, meta = o0c.translate(self.ctx, a)
            self.assertIn(f'{name}(', text)
            self.assertEqual((meta['addr'], meta['size']), (a, n))

    def test_parameter_counts_and_returns(self):
        np = {self.ctx.name[a]: n for a, n in self.ctx.nparams.items()}
        self.assertEqual((np['put'], np['add3'], np['clear_table'], np['clamp']), (1, 3, 0, 3))
        self.assertTrue(self.ctx.retval.get(self.ctx.by_name['pick']))          # a caller reads r0
        self.assertTrue(self.ctx.retval.get(self.ctx.by_name['sum_to']))        # a leaf moves its parameter out of r0
        self.assertFalse(self.ctx.retval.get(self.ctx.by_name['put'], False))

    def test_switch_from_a_tbb_table(self):
        text, _ = self.draft('pick')
        self.assertIn('switch (param_1) {', text)
        for v, r in ((0, '0xa'), (4, '0x33')): self.assertIn(f'case {v}:\n    return {r};', text)
        self.assertIn('default:\n    return -1;', text)

    def test_loops_and_globals(self):
        text, meta = self.draft('clear_table')
        self.assertIn('while (v0 < 8) {\n    (&DAT_20000008)[v0] = 0;\n    v0++;', text)
        self.assertEqual(meta['globals'], {'DAT_20000008': [0x20000008, 'int', 4]})
        text, _ = self.draft('sum_to')
        self.assertTrue(text.split('\n')[1].startswith('int sum_to(int param_1)'))
        self.assertIn('return v0;', text)

    def test_if_else_on_narrow_globals(self):
        text, meta = self.draft('tick')
        self.assertIn('if (DAT_20000000 == 2) {\n    DAT_20000002 = DAT_20000002 + 1;\n  }\n  else {', text)
        self.assertEqual(meta['globals']['DAT_20000000'][1], 'uchar')
        self.assertEqual(meta['globals']['DAT_20000002'][1], 'ushort')

    def test_a_local_kept_in_r2_across_a_call(self):
        # armcc keeps i in r2 across calls to callees it knows leave r2 alone (both are in the
        # file), and uses r4 and r5 as scratch: the draft still reads as the source did
        self.assertEqual(self.ctx.clobbers[self.ctx.by_name['pick']], frozenset({1}))
        text, meta = self.draft('report')
        self.assertIn('put(pick(v2) + (&DAT_20000008)[v2 & 7]);', text)
        self.assertEqual(sorted(meta['calls']), ['pick', 'put'])

    def test_unit_declares_named_variables_through_their_pool_words(self):
        text, meta = self.draft('tick')
        u = o0c.unit(self.ctx, text, meta, data_names())
        self.assertIn('extern uchar g_mode;\n#define DAT_20000000 g_mode', u)
        self.assertIn('extern ushort g_count;\n#define DAT_20000002 g_count', u)

    def test_unit_without_a_data_row_uses_the_placeholder(self):
        text, meta = self.draft('tick')
        self.assertIn('extern uchar g_20000000;', o0c.unit(self.ctx, text, meta))

    def test_the_whole_file(self):
        self.assertEqual([[n for _, _, n in g] for g in o0c.files(self.ctx)],
                         [['put', 'add3', 'clear_table', 'pick', 'tick', 'sum_to', 'clamp', 'report']])
        drafts = [o0c.translate(self.ctx, a) for a, _, _ in self.ctx.rows]
        with open(os.path.join(FX, 'expected_file.c')) as f: want = f.read()
        self.assertEqual('/* opts: --cpu Cortex-M3 -O0 */\n' + o0c.file_unit(self.ctx, drafts, data_names()), want)

    def test_the_shared_pool_is_not_code(self):
        a = self.ctx.by_name['report']
        ins = o0c.decode(self.ctx.img, a, self.ctx.size[a], self.ctx.pw)
        self.assertEqual(ins[-1].raw, 'pop {r4, r5, pc}')
        self.assertEqual({w for w in self.ctx.pw}, {0x080000C8, 0x080000CC, 0x080000D0, 0x080000D4})

    def test_norm_masks_pools_calls_and_targets(self):
        lst = o0c.image_norm(self.ctx, self.ctx.by_name['report'])
        self.assertIn('ldr r5, [pc]', lst)
        self.assertIn('bl ?', lst)
        self.assertTrue(any(x.startswith('blt L') for x in lst))
        self.assertEqual(o0c.score_lists(lst, lst), 0)
        self.assertEqual(o0c.score_lists(lst, lst[:-1] + ['bx lr']), 1)


HOME = os.environ.get('EXHUME_ARMCC_HOME')


def can_run():
    if os.environ.get('EXHUME_ARMCC_RUNNER', 'docker') == 'wine': return bool(shutil.which(os.environ.get('WINE', 'wine')))
    return bool(shutil.which('docker')) and subprocess.run(['docker', 'info'], capture_output=True).returncode == 0


@unittest.skipUnless(HOME and can_run(), 'needs EXHUME_ARMCC_HOME and Docker or Wine')
class ToolchainTest(unittest.TestCase):
    """The drafts compiled: each alone scored, and the whole file proved by match.py."""

    @classmethod
    def setUpClass(cls):
        cls.root = root = tempfile.mkdtemp(dir=os.path.expanduser('~'))
        os.makedirs(os.path.join(root, 'map'))
        shutil.copy(os.path.join(FX, 'corpus.hex'), root)
        for n in ('functions.tsv', 'data.tsv'): shutil.copy(os.path.join(FX, n), os.path.join(root, 'map', n))
        with open(os.path.join(root, 'exhume.toml'), 'w') as f:
            f.write(f'[project]\nname = "t"\n[toolchain]\nprofile = "armcc5"\nhome = "{HOME}"\n[binary]\nimage = "corpus.hex"\n')
        cls.cfgp = os.path.join(root, 'exhume.toml')

    @classmethod
    def tearDownClass(cls):
        import armtc
        armtc.Toolchain(HOME, cls.root).stop()
        shutil.rmtree(cls.root, ignore_errors=True)

    def run_main(self, args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out): rc = o0c.main(['--config', self.cfgp] + args)
        return rc, out.getvalue()

    def test_each_draft_alone(self):
        self.run_main(['gen'])
        rc, out = self.run_main(['score'])
        self.assertEqual(rc, 0, out)
        with open(os.path.join(self.root, 'build', 'o0c', 'score.tsv')) as f:
            got = {l.split('\t')[1]: l.split('\t')[3].strip() for l in f.read().splitlines()[1:]}
        # report keeps i in r2 across its calls only when pick and put are in the same file
        self.assertEqual({n for n, s in got.items() if s == '0'},
                         {'put', 'add3', 'clear_table', 'pick', 'tick', 'sum_to', 'clamp'}, got)
        self.assertNotEqual(got['report'], '0')

    def test_the_whole_file_matches(self):
        import config, armmatch
        src = os.path.join(self.root, 'corpus_o0c.c')
        rc, out = self.run_main(['file', 'put', '--write', src])
        self.assertEqual(rc, 0, out)
        r = armmatch.match(config.Config(self.cfgp), src)
        self.assertTrue(r.whole, '\n'.join(r.lines))


if __name__ == '__main__':
    unittest.main()
