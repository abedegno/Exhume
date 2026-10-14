"""The search tools without a toolchain: templates, mutations, outlining, reduction, repeats."""
import contextlib, io, os, random, re, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import armbrute as B, armpermute as P, armoutline as O, armreduce as R, armrepeats as RP, armvariant as V
import armbuilds as BU, types


def rd(p):
    with open(p) as f: return f.read()


def wr(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f: f.write(s)


SRC = '''typedef unsigned char u8;
typedef unsigned short u16;
typedef struct { u8 n; u16 v[4]; } Ring;
extern Ring g_ring;
extern u16 g_out;

/* at: step 0x08001000 */
void step(u8 k)
{
    u8 i, n;
    u16 sum;
    sum = 0;
    n = g_ring.n;
    for (i = 0; i < n; i++) {
        sum = sum + g_ring.v[i];
    }
    if (sum > 100) g_out = 100; else g_out = sum;
    g_ring.n = k;
}
'''


class TemplateTest(unittest.TestCase):
    def test_both_choice_syntaxes(self):
        parts, opts = B.parse('a {{x||y||}} b {{@p || q@|@r@}} c')
        self.assertEqual(opts, [['x', 'y', ''], ['p || q', 'r']])
        self.assertEqual(B.render(parts, opts, [1, 0]), 'a y b p || q c')
        self.assertEqual(B.render(parts, opts, [2, 1]), 'a  b r c')

    def test_rank_round_trip(self):
        radix = [3, 1, 4, 2]
        for k in range(3 * 4 * 2):
            self.assertEqual(B.rank(B.unrank(k, radix), radix), k)
        self.assertEqual(B.unrank(0, radix), [0, 0, 0, 0])

    def test_group_largest_first(self):
        g = B.group([('a', 'x'), ('b', 'y'), ('c', 'x'), ('d', None)])
        self.assertEqual(g[0], ('x', ['a', 'c']))
        self.assertIn(('(does not compile)', ['d']), g)

    def test_table_signatures(self):
        lst = rd(os.path.join(HERE, 'fixtures', 'fromelf_c.txt'))
        self.assertTrue(B.signature(lst, 'put', 'pool').startswith('loads g_val g_ring g_buf'))
        self.assertIn('ldrh R, [R]', B.signature(lst, 'put', 'regs'))
        self.assertIsNone(B.signature(lst, 'nosuch', 'listing'))


class PermuteTest(unittest.TestCase):
    def setUp(self):
        P.set_types(sorted(P.scalar_typedefs(SRC)))

    def test_typedefs_through_includes(self):
        d = tempfile.mkdtemp()
        wr(os.path.join(d, 'inc', 'types.h'), 'typedef unsigned int u32;\ntypedef signed short s16;\n')
        wr(os.path.join(d, 'src', 'a.c'), '#include "types.h"\ntypedef unsigned char u8;\n')
        known = P.known_types(os.path.join(d, 'src', 'a.c'), [os.path.join(d, 'inc')])
        self.assertEqual(known, {'u32': 'unsigned int', 's16': 'signed short', 'u8': 'unsigned char'})

    def test_find_func(self):
        start, lo, hi = P.find_func(SRC, 'step')
        self.assertTrue(SRC[start:].startswith('void step(u8 k)'))
        self.assertTrue(SRC[lo:hi].strip().startswith('u8 i, n;'))
        self.assertEqual(SRC[hi], '}')
        self.assertEqual(P.signature(SRC, 'step'), 'u8 k')

    def test_source_round_trip(self):
        s = P.Source(SRC, 'step')
        self.assertEqual(s.whole(s.body), SRC)

    def test_locals(self):
        _, lo, hi = P.find_func(SRC, 'step')
        d = P._decls(SRC[lo:hi])
        self.assertEqual(d['i'][0], 'u8'); self.assertEqual(d['sum'][0], 'u16')

    def test_every_mutation_keeps_braces_balanced(self):
        s = P.Source(SRC, 'step')
        for seed in range(200):
            r = random.Random(seed)
            b, how = P.mutate(s.body, r, s.sig)
            if b is None: continue
            text = s.whole(b)
            self.assertEqual(text.count('{'), text.count('}'), how)
            self.assertEqual(text.count('('), text.count(')'), how)

    def test_outline_makes_an_inline_helper(self):
        s = P.Source(SRC, 'step')
        for seed in range(100):
            b = P.m_outline(s.body, random.Random(seed), s.sig)
            if b: break
        self.assertIsNotNone(b)
        text = s.whole(b)
        self.assertIn('static __inline void inl', text)
        self.assertLess(text.index('static __inline'), text.index('void step('))

    def test_a_cast_never_lands_on_a_bare_global(self):
        body = '\n    u16 x;\n    x = g_out;\n    x = x + 1;\n'
        for seed in range(100):
            b = P.m_cast(body, random.Random(seed))
            if b: self.assertNotRegex(b, r'= \(\w+\)g_out;')

    def test_retype_offers_project_types(self):
        self.assertIn('u16', P.INT_TYPES)
        b = P.m_retype('\n    u8 i;\n', random.Random(1))
        self.assertRegex(b, r'^\n    [\w ]+ i;\n$')


class OutlineTest(unittest.TestCase):
    def setUp(self):
        P.set_types(sorted(P.scalar_typedefs(SRC)))
        _, lo, hi = P.find_func(SRC, 'step')
        self.lines = SRC[lo:hi].split('\n')

    def test_runs_are_whole_statements(self):
        rs = list(O.runs(self.lines, 10))
        self.assertTrue(rs)
        for i, k in rs:
            t = '\n'.join(self.lines[i:i + k])
            self.assertEqual(t.count('{'), t.count('}'))

    def test_a_written_local_is_returned_or_passed_by_address(self):
        i = next(j for j, l in enumerate(self.lines) if 'sum = 0;' in l)
        self.assertIsNone(O.outline(self.lines, i, 1, 'u8 k', 'h', 'plain'))   # sum is read later
        fn, new = O.outline(self.lines, i, 1, 'u8 k', 'h', 'ret')
        self.assertIn('static __inline u16 h(u16 sum)', fn)
        self.assertIn('sum = h(sum);', '\n'.join(new))
        fn, new = O.outline(self.lines, i, 1, 'u8 k', 'h', 'ptr')
        self.assertIn('(*p_sum) = 0;', fn)

    def test_sweep_puts_the_statement_at_each_boundary(self):
        bodies = O.sweep(self.lines, 'MARK;')
        self.assertTrue(bodies)
        for b in bodies:
            self.assertEqual(sum(l.strip() == 'MARK;' for l in b), 1)
            k = next(j for j, l in enumerate(b) if l.strip() == 'MARK;')
            self.assertFalse(any(P.DECL.match(l) for l in b[k + 1:]))      # never among the declarations

    def test_template_variant_zero_is_the_source(self):
        d = tempfile.mkdtemp()
        src, out = os.path.join(d, 'a.c'), os.path.join(d, 'brute', 't.c')
        wr(src, SRC)
        with contextlib.redirect_stdout(io.StringIO()): O.main([src, 'step', out, '--decls'])
        parts, opts = B.parse(rd(out))
        self.assertEqual(B.render(parts, opts, [0] * len(opts)), SRC)
        self.assertGreater(len(opts[0]), 2)

    def test_types_template(self):
        d = tempfile.mkdtemp()
        src, out = os.path.join(d, 'a.c'), os.path.join(d, 't.c')
        wr(src, SRC)
        with contextlib.redirect_stdout(io.StringIO()): O.main([src, 'step', out, '--types', '--max-run', '2'])
        text = rd(out)
        self.assertIn('#define T_sum {{u16||', text)
        parts, opts = B.parse(text)
        self.assertIn('T_sum sum;', B.render(parts, opts, [0] * len(opts)))


class ReduceTest(unittest.TestCase):
    def test_only_the_needed_hunks_stay(self):
        a = ['x', 'a1', 'y', 'b1', 'z', 'c1']
        b = ['x', 'a2', 'y', 'b2', 'z', 'c2']
        ok = lambda lines: 'a2' in lines and 'c2' in lines        # b2 does nothing
        ops, keep = R.reduce(a, b, ok)
        self.assertEqual(R.apply(a, b, ops, keep), ['x', 'a2', 'y', 'b1', 'z', 'c2'])

    def test_best_must_pass(self):
        _, keep = R.reduce(['a'], ['b'], lambda lines: False)
        self.assertIsNone(keep)


class RepeatsTest(unittest.TestCase):
    def test_shape(self):
        self.assertEqual(RP.shape('ldrb.w r7, [r5, #0x61]'), 'ldrb.w R, [R, #0x61]')
        self.assertEqual(RP.shape('bne #0x20'), 'bne')
        self.assertEqual(RP.shape('ldr r0, [pc, #0x1c]'), 'ldr R, [pc, #?]')
        self.assertEqual(RP.shape('push {r4, r5, lr}'), 'push {..}')
        self.assertEqual(RP.shape('bic r0, r0, #3'), 'bic R, R, #3')

    def test_groups_are_maximal(self):
        run = [(0, s) for s in 'abcdefgh']
        seqs = {'f': [(0, 'x')] + run + [(0, 'y')], 'g': [(0, 'z')] + run + [(0, 'w')], 'h': [(0, 'q')]}
        g = RP.groups(seqs, 4)
        self.assertEqual(len(g), 1)
        (key, n), places = next(iter(g.items()))
        self.assertEqual(n, 8)
        self.assertEqual(sorted(places), [('f', 1), ('g', 1)])

    def test_shapes_skip_the_pool(self):
        b, _ = V.func_bytes(rd(os.path.join(HERE, 'fixtures', 'fromelf_c.txt')), 'put')
        sh = RP.shapes(b)
        self.assertEqual(sh[0], (0, 'ldr R, [pc, #?]'))
        self.assertTrue(all(off < 0x20 for off, _ in sh))


class BuildsTest(unittest.TestCase):
    def test_cells_and_table(self):
        whole = types.SimpleNamespace(whole=True, lines=['-- 10/10 bytes matched; WHOLE FILE MATCHES'])
        part = types.SimpleNamespace(whole=False, lines=['-- 8/10 bytes matched'])
        failed = types.SimpleNamespace(whole=False, lines=['-- compile failed'])
        self.assertEqual([BU.cell(r) for r in (whole, part, failed)], ['whole', '8/10', 'fails'])
        homes, srcs = ['/tc/MDK520', '/tc/MDK536'], ['src/a.c']
        out = BU.table(homes, srcs, {('/tc/MDK520', 'src/a.c'): 'whole', ('/tc/MDK536', 'src/a.c'): '8/10'},
                       {'/tc/MDK520': 'Compiler 5.06 update 2', '/tc/MDK536': 'Compiler 5.06 update 7'})
        self.assertEqual(out[0], 'MDK520: Compiler 5.06 update 2')
        self.assertRegex(out[3], r'^src/a.c\s+whole\s+8/10$')
        self.assertRegex(out[4], r'^whole\s+1\s+0$')

    def test_each_home_builds_apart(self):
        cfg = types.SimpleNamespace(home='/a', build=tempfile.mkdtemp())
        c = BU.for_home(cfg, '/tc/MDK520/')
        self.assertEqual((c.home, cfg.home), ('/tc/MDK520', '/a'))
        self.assertTrue(c.build.endswith(os.path.join('builds', 'MDK520')))


if __name__ == '__main__':
    unittest.main()
