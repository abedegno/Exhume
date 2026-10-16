"""The tools against TI's own compiler, on the synthetic firmware in tests/fw: build an "original"
image from it (synth.py), then match each source, solve a shuffled .ebss, fingerprint the
library, disassemble, search a function's variants, and link the whole image through the gate.

They skip unless a compiler is named in the environment (synth.py says how). Everything they
build goes to a temporary directory; the Wine servers they start are stopped at the end."""
import json, os, re, shutil, tempfile, unittest
from . import support  # noqa: F401
from . import synth
import config, c28tc, c28map, c28match, c28gate, c28ebss, c28libfp, c28disasm, c28batch

ENV, WHY = synth.environment()


@unittest.skipIf(ENV is None, 'no C2000 compiler: %s' % WHY)
class Synthetic(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix='c28-')
        cls.cfg = config.Config(synth.build(cls.dir, ENV))
        cls.s = c28tc.Settings.from_config(cls.cfg)
        cls.tc = c28tc.Toolchain(cls.s)
        cls.img = c28tc.Image.of(cls.s)
        cls.m = c28map.Map.from_dir(cls.s.map_dir)

    @classmethod
    def tearDownClass(cls):
        c28tc.stop_wine(cls.s)
        shutil.rmtree(cls.dir, ignore_errors=True)

    def run_tool(self, fn, *args):
        lines = []
        rc = fn(self.s, list(args), out=lines.append)
        return rc, '\n'.join(lines)

    def copy_of(self, name, edit):
        os.makedirs(os.path.join(self.dir, 'try'), exist_ok=True)
        with open(os.path.join(self.dir, 'src', name)) as f: text = f.read()
        p = os.path.join(self.dir, 'try', name)
        with open(p, 'w') as f: f.write(edit(text))
        return p

    def test_every_source_matches(self):
        texts = {}
        for name in ('loopctl', 'helpers', 'appmain'):
            rc, texts[name] = self.run_tool(c28match.run, name)
            self.assertEqual(rc, 0, texts[name])
            self.assertNotIn('DIFF', texts[name])
        f = self.m.frows['fast_sum']                                            # a ramfunc, compared at its load address
        self.assertNotEqual(c28map.run_addr(f), f.addr)
        self.assertRegex(texts['helpers'], r'fast_sum +%06X +\d+  EXACT' % f.addr)
        self.assertRegex(texts['loopctl'], r'sw_ctl_mode .* EXACT')                  # a switch table
        self.assertRegex(texts['helpers'], r'table .* EXACT')                       # a const object

    def test_a_difference_is_shown_side_by_side(self):
        p = self.copy_of('loopctl.c', lambda t: t.replace('hist[ticks & 7] = mode;', 'hist[ticks & 7] = flags;'))
        rc, text = self.run_tool(c28match.run, p)
        self.assertEqual(rc, 1)
        self.assertRegex(text, r'ctl_tick +[0-9A-F]+ +\d+  DIFF')
        self.assertIn('| image', text)
        self.assertIn('loop_step', text)

    def test_unresolved_names_are_reported(self):
        p = self.copy_of('appmain.c', lambda t: t.replace('ctl_mode(2);', 'ctl_mode(2); helper_nobody_has();')
                         .replace('extern unsigned int ctl_mode', 'extern void helper_nobody_has(void);\nextern unsigned int ctl_mode'))
        rc, text = self.run_tool(c28match.run, p)
        self.assertEqual(rc, 1)
        self.assertIn('UNRESOLVED _helper_nobody_has', text)

    def test_ebss_solver(self):
        def shuffle(t):
            old = t[t.index('unsigned int mode;'):t.index('float target;') + len('float target;')]
            lines = [l for l in old.split('\n') if l.strip()]
            return t.replace(old, '/* ebss-begin */\n' + '\n'.join(reversed(lines)) + '\n/* ebss-end */')
        p = self.copy_of('loopctl.c', shuffle)
        layout = c28ebss.compiler_layout(self.tc, p, os.path.join(self.s.build, 'ebss', 'test'))
        addr_of = lambda n: self.m.drows[n].addr if n in self.m.drows else None
        with open(p) as f: text = f.read()
        bad, _ = c28ebss.misplaced(layout(text), addr_of)
        self.assertTrue(bad)
        rc, out = self.run_tool(c28ebss.run, p)
        self.assertEqual(rc, 0, out)
        self.assertIn('solved', out)
        rc, out = self.run_tool(c28match.run, p)
        self.assertEqual(rc, 0, out)

    def test_library_fingerprint(self):
        lib, d, members, found = c28libfp.find(self.tc, self.img)
        rts = {r.addr for r in self.m.functions if r.kind == 'rts'}
        placed = set()
        for f in found:
            for v, n in f['syms']: placed.add(f['addr'] + v)
        self.assertTrue(rts <= placed, sorted(rts - placed))
        self.assertTrue(any(f['how'].startswith('called from') for f in found))

    def test_disassembly(self):
        r = self.m.frows['loop_step']
        ins = c28disasm.disasm(self.tc, self.img.words(r.addr, r.size), r.addr)
        self.assertEqual(ins[0][0], r.addr)
        self.assertEqual([w for _, ws, _ in ins for w in ws], self.img.words(r.addr, r.size))
        self.assertEqual(ins[-1][2], 'LRETR')
        f = self.m.frows['fast_sum']                       # a ramfunc, at its run address
        ins = c28disasm.disasm(self.tc, self.img.words(f.addr, f.size), c28map.run_addr(f))
        self.assertEqual(ins[0][0], c28map.run_addr(f))

    def test_batch_climb(self):
        spec = [['ticks = 0;', ['ticks = 1;', 'ticks = 0;']],
                ['total += ticks;', ['total = total + ticks;', 'total += ticks;']]]
        p = os.path.join(self.dir, 'spec.json')
        with open(p, 'w') as f: json.dump(spec, f)
        rc, out = self.run_tool(c28batch.run, 'loopctl', 'ctl_tick', p)
        first = out.splitlines()[0]
        self.assertTrue(first.startswith('(1, 0, 0) [1,'), out)    # only data addresses differ: the copies move .ebss
        rc, out = self.run_tool(c28batch.run, 'loopctl', 'ctl_tick', p, '--climb')
        self.assertIn('better (1, 0, 0) [1,', out)

    def test_gate(self):
        rc, out = self.run_tool(c28gate.run, '--fail-test')
        self.assertEqual(rc, 0, out)
        self.assertIn('IDENTICAL', out)
        self.assertIn("the image's records in the image's order", out)
        self.assertRegex(out, r'fail test: .* PASS')
        self.assertRegex(out, r'\| Original words \(blobs\) \| 0 \|')
        # a source changed: that file fails, and the image differs where it does
        p = self.copy_of('loopctl.c', lambda t: t.replace('hist[ticks & 7] = mode;', 'hist[ticks & 7] = flags;'))
        rc, out = self.run_tool(c28gate.run, '--with', p)
        self.assertEqual(rc, 1)
        self.assertIn('DIFFERENT: 1 words differ', out)
        # a source missing: its words come from blobs, the image is still identical, --strict fails
        os.rename(os.path.join(self.dir, 'src', 'helpers.c'), os.path.join(self.dir, 'helpers.c'))
        try:
            rc, out = self.run_tool(c28gate.run)
            self.assertEqual(rc, 0, out)
            self.assertIn('IDENTICAL', out)
            self.assertRegex(out, r'\| Original words \(blobs\) \| [1-9]\d* \|')
            rc, out = self.run_tool(c28gate.run, '--strict')
            self.assertEqual(rc, 1)
        finally:
            os.rename(os.path.join(self.dir, 'helpers.c'), os.path.join(self.dir, 'src', 'helpers.c'))

    def test_gate_with_the_compilers_own_linker(self):
        if not os.path.exists(os.path.join(self.s.cgt, 'bin', 'lnk2000.exe')) and \
                not os.path.exists(os.path.join(self.s.cgt, 'bin', 'lnk2000')):
            self.skipTest('no lnk2000 in the compiler home')
        s = c28tc.Settings.from_config(self.cfg)
        s.gate = dict(s.gate, linker='cgt')
        lines = []
        rc = c28gate.run(s, [], out=lines.append)
        self.assertEqual(rc, 0, '\n'.join(lines))
        self.assertTrue(any(re.match(r'link: lnk2000 \d', l) for l in lines), lines)


if __name__ == '__main__':
    unittest.main()
