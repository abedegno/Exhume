"""Unit tests for the fast scorer, the trace reader, the two-compiler helpers and the structural
mutators (no Docker required)."""
import os
import random
import sys
import unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools')
sys.path.insert(0, TOOLS)

import godual
import gofast
import gorandperm as g
import gotrace

OBJDUMP = '''TEXT %22%22.cachedSize(SB) gofile../src/app/size.go
\tsize.go:10\t0x1a2\t64488b0c2500000000\tFS MOVQ FS:0, CX\t[5:9]R_TLS_LE
\tsize.go:11\t0x1ab\t488b4424 18\tMOVQ 0x18(SP), AX
\tsize.go:11\t0x1b0\t488b4c2420\t\tMOVQ 0x20(SP), CX
'''.replace('488b4424 18', '488b442418')


class FastScore(unittest.TestCase):
    def setUp(self):
        self.funcs = gofast.parse_objdump(OBJDUMP)
        self.insts = self.funcs['"".cachedSize']
        code = b''.join(c for _, c, _ in self.insts)
        self.ref = (code, {0: 10, 9: 11, 14: 11}, {})

    def test_parse(self):
        self.assertEqual([i[0] for i in self.insts], [10, 11, 11])
        self.assertEqual(self.insts[0][2], [(5, 9)])           # the relocated field
        self.assertEqual(len(self.insts[1][1]), 5)

    def test_equal_is_zero(self):
        self.assertEqual(gofast.score_func(self.ref, self.insts), 0)
        self.assertIsNone(gofast.first_diff(self.ref, self.insts))

    def test_relocated_bytes_are_not_compared(self):
        code = bytearray(self.ref[0]); code[6] ^= 0xFF
        self.assertEqual(gofast.score_func((bytes(code), self.ref[1], {}), self.insts), 0)

    def test_swapped_pair_and_line(self):
        code = bytearray(self.ref[0]); code[13] = 0x20; code[18] = 0x18      # the two loads swapped
        ref = (bytes(code), self.ref[1], {})
        self.assertEqual(gofast.score_func(ref, self.insts), 4)              # 2 x (1 byte + 1 instruction)
        self.assertEqual(gofast.first_diff(ref, self.insts), (9, 11))
        moved = (self.ref[0], {0: 10, 9: 12, 14: 11}, {})
        self.assertEqual(gofast.score_func(moved, self.insts), 1)            # a line counts as an instruction

    def test_size_mismatch(self):
        self.assertGreaterEqual(gofast.score_func((self.ref[0] + b'\x90\x90', self.ref[1], {}), self.insts), 1_000_000)

    def test_int3_padding_is_dropped(self):
        self.assertEqual(gofast.score_func((self.ref[0] + b'\xcc\xcc', self.ref[1], {}), self.insts), 0)


def rows(dec, stack=(), frees=()):
    out = [['free', p, v, op, b, f'x.go:{ln}'] for p, v, op, b, ln in frees]
    out += [['stack', 'before_dec', str(i), v] for i, v in enumerate(stack)]
    out += [['alloc', 'dec', v, src, op, 'b1', f'x.go:{ln}'] for v, src, op, ln in dec]
    return out


class TraceReader(unittest.TestCase):
    def test_summary_pairs_and_provenance(self):
        r = rows([('v9', 'stack', 'Load', 11), ('v4', 'stack', 'Load', 11), ('v20', 'fresh', 'OffPtr', 11),
                  ('v21', 'fresh', 'Load', 12), ('v22', 'fresh', 'Load', 12)],
                 stack=['v9', 'v4'], frees=[('early_deadcode', 'v9', 'Copy', 'b3', 40), ('opt', 'v4', 'OpInvalid', 'b1', 7)])
        n, pairs, table = gotrace.summary(r)
        self.assertEqual(n, 2)
        self.assertEqual(pairs, {11: ['>'], 12: ['<']})          # recycled descending, fresh ascending
        self.assertEqual(table[0][5:], ('early_deadcode', 'Copy', 'b3', '40'))
        self.assertEqual(table[3][5], 'fresh')

    def test_load_group_is_found_by_pattern_not_position(self):
        group = [('v30', 'stack', 'Load', 11), ('v25', 'stack', 'Load', 11), ('v24', 'stack', 'OffPtr', 11),
                 ('v12', 'stack', 'Load', 11), ('v11', 'stack', 'OffPtr', 11),
                 ('v8', 'stack', 'Load', 11), ('v7', 'stack', 'Load', 11), ('v6', 'stack', 'OffPtr', 11)]
        for lead in ([], [('v50', 'stack', 'Load', 10), ('v49', 'stack', 'Load', 10), ('v48', 'stack', 'OffPtr', 10)]):
            ids = gotrace.load_group(rows(lead + group), 11, ['slice', 'iface'])
            self.assertEqual(ids, [30, 25, 12, 8, 7])
        self.assertIsNone(gotrace.load_group(rows(group), 12, ['slice', 'iface']))

    def test_inversions(self):
        # words ptr len cap itab data; the scheduler emits ascending ID
        self.assertEqual(gotrace.inversions([50, 20, 10, 30, 40], [2, 1, 3, 4, 0]), 0)   # cap len itab data ptr
        self.assertEqual(gotrace.inversions([30, 25, 12, 8, 7], [2, 1, 3, 4, 0]), 5)     # data itab cap len ptr
        self.assertEqual(gotrace.inversions([1, 2], [0, 1]), 0)
        self.assertEqual(gotrace.inversions([2, 1], [0, 1]), 1)

    def test_deletions(self):
        dec = [('v9', 'stack', 'OffPtr', 5), ('v8', 'stack', 'Load', 5), ('v3', 'stack', 'Load', 5), ('v7', 'stack', 'OffPtr', 5)]
        _, _, table = gotrace.summary(rows(dec))
        sols = gotrace.deletions(table, {5: '<'})                 # dropping v9 or v8 leaves the pair (v3, v7)
        self.assertEqual([[t[1] for t in s] for s in sols], [['v9'], ['v8']])


    def test_patches_follow_their_anchors(self):
        func_go = ('package ssa\n\nimport (\n\t"fmt"\n\t"math"\n)\n\nfunc (f *Func) newValue(op Op) *Value {\n'
                   '\tvar v *Value\n\tif f.freeValues != nil {\n\t\tv = f.freeValues\n\t}\n'
                   '\tv.Line = line\n\tb.Values = append(b.Values, v)\n\treturn v\n}\n\n'
                   'func (f *Func) freeValue(v *Value) {\n\tif v.Uses != 0 {\n'
                   '\t\tf.Fatalf("value %s still has %d uses", v, v.Uses)\n\t}\n}\n')
        out = gotrace.patch_func_go(func_go)
        for want in ('"os"', '"strings"', 'from = "stack"', 'f.tracef("alloc', 'f.tracef("free', 'func (f *Func) traceStack'):
            self.assertIn(want, out)
        with self.assertRaises(Exception):
            gotrace.patch_func_go(func_go.replace('f.freeValues != nil', 'f.free != nil'))


class Rewrites(unittest.TestCase):
    BASE = ['package p', '', 'x := f()', 'if a && b && c {', '}']

    def test_apply(self):
        spec = {'decl': [[2, '', '\tvar x int'], [3, 'x := ', 'x = ']], 'group': [[4, 'b && c', '(b && c)']]}
        t = godual.apply_rewrites(self.BASE, spec, ['decl', 'group']).split('\n')
        self.assertEqual(t[1:4], ['\tvar x int', 'x = f()', 'if a && (b && c) {'])
        self.assertIsNone(godual.apply_rewrites(self.BASE, {'bad': [[3, 'y := ', 'y = ']]}, ['bad']))
        self.assertIsNone(godual.apply_rewrites(self.BASE, {'used': [[3, '', 'var y int']]}, ['used']))

    def test_key_orders_by_both_compilers(self):
        self.assertEqual(godual.key((0, 0, []))[0], 0)
        self.assertLess(godual.key((20008, 8, [63, 70])), godual.key((80036, 24, [64, 70, 71])))
        self.assertEqual(godual.key((None, None, []))[0], 10**6)
        self.assertEqual(godual.key((40016, -1, []))[0], 10**6)       # the second release's size differs


class StructuralMutators(unittest.TestCase):
    CHAIN = '\tok := len(d.Name) > 0 && lo > 0 && mid > 0 && hi > 0'

    def test_assoc_regroups_and_flattens(self):
        seen = {g.m_assoc(self.CHAIN, random.Random(k)) for k in range(200)}
        self.assertIn('\tok := len(d.Name) > 0 && (lo > 0 && mid > 0 && hi > 0)', seen)
        self.assertIn('\tok := (len(d.Name) > 0 && lo > 0) && mid > 0 && hi > 0', seen)
        self.assertNotIn('\tok := (len(d.Name) > 0 && lo > 0 && mid > 0 && hi > 0)', seen)
        grouped = '\tok := len(d.Name) > 0 && (lo > 0 && mid > 0) && hi > 0'
        self.assertIn(self.CHAIN, {g.m_assoc(grouped, random.Random(k)) for k in range(100)})
        self.assertIsNone(g.m_assoc('\tif a || b {', random.Random(1)))                  # two operands: nothing to regroup
        self.assertIsNone(g.m_assoc('\ts := f("a && b && c")', random.Random(1)))         # inside a literal

    def test_trueop_adds_and_removes(self):
        seen = {g.m_trueop(self.CHAIN, random.Random(k)) for k in range(300)}
        self.assertIn('\tok := len(d.Name) > 0 && true && lo > 0 && mid > 0 && hi > 0', seen)
        self.assertIn('\tok := len(d.Name) > 0 && lo > 0 && lo != 0 && mid > 0 && hi > 0', seen)
        padded = '\tok := len(d.Name) > 0 && true && lo > 0 && mid > 0 && hi > 0'
        self.assertIn(self.CHAIN, {g.m_trueop(padded, random.Random(k)) for k in range(100)})
        self.assertIsNone(g.m_trueop('\tname := r.FormValue("Name")', random.Random(1)))

    def test_else_wrap_round_trip(self):
        src = ['func f() {', '\tif err != nil {', '\t\treturn', '\t}', '', '\tg()', '}']
        ew = g.else_wrap(src, 0, len(src), random.Random(1))
        self.assertEqual(ew, [(3, '\t} else {'), (6, '}}')])
        wrapped = list(src)
        for i, new in ew:
            wrapped[i] = new
        back = g.else_wrap(wrapped, 0, len(wrapped), random.Random(1))
        self.assertEqual(back, [(3, '\t}'), (6, '}')])


if __name__ == '__main__':
    unittest.main()
