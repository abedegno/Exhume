"""armtrace.py's parser and armreplay.py's allocator replay, on synthetic graphs and a synthetic
trace (fixtures/armtrace/synthetic.trace: written from the replay model by a script, in the
trace's format; no compiler output)."""
import collections, json, os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import armtrace
import armreplay as R

FIXTURE = os.path.join(HERE, 'fixtures', 'armtrace', 'synthetic.trace')


def graph(vnodes, clashes, moves=(), refs=None):
    """An allocator input: the real registers precoloured (13, sp, uninitialised), vnodes extra low."""
    C, M = collections.defaultdict(set), collections.defaultdict(set)
    for a, b in clashes: C[a].add(b); C[b].add(a)
    for a, b, w in moves: M[a].add(b); M[b].add(a)
    init = {r: R.PRE for r in range(15)}
    init[13] = R.UNINIT
    init.update({v: R.XL for v in vnodes})
    nodes = [n for n, s in init.items() if s != R.UNINIT]
    rf = {n: 0 for n in nodes}
    rf.update(refs or {})
    return dict(nodes=nodes, C=C, M=M, direct={frozenset((a, b)) for a, b, w in moves if w == 5}, refs=rf,
                nonspill=set(), uncoal=set(), init=init)


LOW = (0, 1, 2, 3)


class TestHeap(unittest.TestCase):
    def test_a_key_lowered_in_place_is_not_resorted(self):
        key = {1: 10, 2: 20, 3: 30, 4: 40, 5: 50}
        pos = {}
        h = R.Heap(lambda a, b: key[a] - key[b], pos)
        for n in (1, 2, 3, 4, 5): h.push(n)
        key[4] = 15                       # 4 sits under 2; nothing sifts it up
        self.assertEqual(h.marks(), [(1, False), (2, True), (3, False), (4, False), (5, False)])
        self.assertEqual([h.pop(), h.pop(), h.pop()], [1, 2, 4])   # sorted keys would give 1, 4, 2

    def test_remove_keeps_positions(self):
        key = {n: n for n in range(1, 9)}
        pos = {}
        h = R.Heap(lambda a, b: key[a] - key[b], pos)
        for n in range(8, 0, -1): h.push(n)
        h.remove(3)
        self.assertEqual({v: i for i, v in enumerate(h.a)}, pos)
        self.assertEqual([h.pop() for _ in range(7)], [1, 2, 4, 5, 6, 7, 8])


class TestColour(unittest.TestCase):
    # Coloured in the order 32, 30, 31. 32 clashes with r0-r3 and ip, so it takes r4 by either
    # rule; 30 can then have ip or r4; 31 clashes with 30.
    G = graph([30, 31, 32], [(r, v) for v in (30, 31, 32) for r in LOW] + [(12, 32), (30, 31)],
              refs={31: 1, 30: 2, 32: 3})

    def colours(self, otime):
        st = R.State(self.G).run()
        self.assertEqual(st.stack, [31, 30, 32])
        col, _ = R.colour(st, otime)
        return {v: R.rname(col[v]) for v in (30, 31, 32)}

    def test_without_otime_callee_saved_in_use_before_ip(self):
        # r4 is already handed out, so it joins the preferred set and comes before ip.
        self.assertEqual(self.colours(False), {32: 'r4', 30: 'r4', 31: 'ip'})

    def test_with_otime_ip_before_every_callee_saved(self):
        self.assertEqual(self.colours(True), {32: 'r4', 30: 'ip', 31: 'r4'})

    def test_vote_follows_a_coloured_copy_partner(self):
        G = graph([50, 51], [(r, 50) for r in LOW], moves=[(50, 51, 3)], refs={50: 2, 51: 1})
        G['C'][51] = {0, 50}; G['C'][0].add(51); G['C'][50].add(51)
        st = R.State(G).run()
        col, rec = R.colour(st, True)
        self.assertEqual((col[50], col[51]), (12, 1))   # 51 clashes with 50, so its vote is void
        G = graph([50, 51], [(r, 50) for r in LOW] + [(0, 51)], moves=[(50, 51, 3)], refs={50: 2, 51: 1})
        st = R.State(G).run()
        col, rec = R.colour(st, True)
        self.assertEqual((col[51], rec[51][2]), (12, 'vote'))

    def test_coalescing_merges_into_the_partner(self):
        G = graph([60, 61], [(r, 60) for r in LOW], moves=[(60, 61, 5)], refs={60: 3, 61: 0})
        st = R.State(G).run()
        self.assertEqual(st.alias, {61: 60})
        col, _ = R.colour(st, True)
        self.assertEqual(col[61], col[60])

    def test_no_merge_when_clashes_are_not_a_subset(self):
        G = graph([60, 61], [(r, 60) for r in LOW] + [(4, 61)], moves=[(60, 61, 5)], refs={60: 3, 61: 0})
        st = R.State(G).run()
        self.assertEqual(st.alias, {})
        self.assertIn(('coal', "can't coalesce with 60 - 61's clashes aren't a subset of 60's"), st.log)

    def test_a_tie_on_every_key_goes_by_node_number(self):
        G = graph([40, 41], [(r, v) for v in (40, 41) for r in LOW] + [(40, 41)], refs={40: 5, 41: 5})
        st = R.State(G).run()
        self.assertEqual(st.stack, [41, 40])        # the higher number leaves first, is coloured last
        col, _ = R.colour(st, True)
        self.assertEqual((col[40], col[41]), (12, 4))


class TestTrace(unittest.TestCase):
    T = armtrace.pick(armtrace.parse(FIXTURE))

    def test_parse(self):
        T = self.T
        self.assertEqual(T['header']['second_clash_set'], 1)
        self.assertEqual(len(T['g0']['clash']), 22)
        self.assertEqual(T['g0']['move'], {(32, 33): 5})
        P = T['passes'][0]
        self.assertEqual([s['n'] for s in P['steps']], [33, 31, 30, 32, 41, 40])
        self.assertEqual(P['steps'][1]['refs'], 1)
        self.assertEqual(P['steps'][0]['coal'], ['coalescing with 32'])
        self.assertEqual(P['steps'][0]['lists']['extralownodes'], [(31, False), (30, False), (32, False), (41, False), (40, False)])
        self.assertEqual({c['n']: c['colour'] for c in P['col']}, {40: 12, 41: 4, 32: 12, 30: 12, 31: 4})
        self.assertEqual(P['init'][13][2], 'NS_Uninitialised')
        self.assertEqual(T['final']['nodes'][33], (12, 'INT32'))
        self.assertIn('6 removals', armtrace.summary([T])[0])

    def test_two_colourings_and_pick(self):
        text = open(FIXTURE).read()
        cs = armtrace.parse_text(text + '\n' + text)
        self.assertEqual(len(cs), 2)
        self.assertIs(armtrace.pick(cs, 1), cs[1])
        with self.assertRaises(SystemExit): armtrace.pick([])

    def test_check_agrees_with_the_rule_it_was_made_with(self):
        lines, ok, hidden = R.check(self.T, True)
        self.assertTrue(ok, lines)
        self.assertEqual(hidden, set())
        lines, ok, _ = R.check(self.T, False)
        self.assertFalse(ok)
        self.assertTrue(R.infer_otime(self.T))

    def test_check_reports_a_wrong_pick(self):
        T = armtrace.parse_text(open(FIXTURE).read().replace('refs = 1\n', 'refs = 9\n'))[0]
        lines, ok, _ = R.check(T, True)
        self.assertFalse(ok)
        self.assertTrue(any(l.startswith('  pick:') for l in lines), lines)

    def test_tie(self):
        out = R.tie(self.T, 40, 41, True)
        self.assertIn('removal 4: 41 leaves first; every key ties, and the higher node number leaves first', out)
        self.assertEqual(out[-1], 'colours: 40 ip, 41 r4 (the one removed first is coloured last)')


class TestOrders(unittest.TestCase):
    T = TestTrace.T
    webs = [('x', [40]), ('y', [41])]

    def test_relabel(self):
        self.assertEqual(R.order_relabel([('a', [10, 11]), ('b', [12])], ('b', 'a')), {12: 10, 10: 11, 11: 12})
        self.assertEqual(len(list(R.orders([('a', [1]), ('b', [2]), ('c', [3])]))), 6)
        self.assertEqual(list(R.orders([('a', [1]), ('b', [2]), ('c', [3])], ['a', 'c'])),
                         [('a', 'b', 'c'), ('c', 'b', 'a')])

    def test_the_traced_order_predicts_the_trace(self):
        G = R.graph(self.T)
        col, rel = R.predict(self.T, G, self.webs, ('x', 'y'), True)
        self.assertEqual((col[40], col[41]), (12, 4))
        col, rel = R.predict(self.T, G, self.webs, ('y', 'x'), True)
        self.assertEqual((col[40], col[41]), (4, 12))

    def test_sweep_finds_the_order_for_a_swap(self):
        base = R.final_colours(R.replay(self.T, True))
        want = {n: c for n, c in base.items() if n > 14}
        want[40], want[41] = base[41], base[40]
        res = R.sweep(self.T, self.webs, want, True)
        self.assertEqual(res, [(0, ('y', 'x')), (2, ('x', 'y'))])

    def test_webs_file(self):
        with tempfile.NamedTemporaryFile('w', suffix='.json', delete=False) as f:
            json.dump({'vars': [['x', [40]], ['y', ['41']]]}, f)
        try:
            self.assertEqual(R.load_webs(f.name), [('x', [40]), ('y', [41])])
        finally:
            os.remove(f.name)


class TestLocal(unittest.TestCase):
    def test_debugger_script_fills_in_the_level(self):
        s = armtrace.debugger_script(['set *(int*)0xADDRESS = 0x{level:02x}', 'echo {level}'], 5)
        self.assertEqual(s, 'set *(int*)0xADDRESS = 0x05\necho 5\ncont\nquit\n')

    def test_local_file(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, 'armtrace.local.toml')
            with open(p, 'w') as f: f.write('[trace]\ncommands = ["echo {level}"]\n')
            self.assertEqual(armtrace.find_local(None, d), p)
            self.assertEqual(armtrace.load_local(p)['commands'], ['echo {level}'])
            with open(p, 'w') as f: f.write('[trace]\ncommands = []\n')
            with self.assertRaises(SystemExit): armtrace.load_local(p)

    def test_example_file_holds_no_address(self):
        ex = os.path.join(os.path.dirname(HERE), 'armtrace.local.example.toml')
        text = open(ex).read()
        import re
        self.assertIsNone(re.search(r'0x[0-9A-Fa-f]{4,}', text))


if __name__ == '__main__':
    unittest.main()
