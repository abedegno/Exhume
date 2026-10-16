"""The .ebss solver on synthetic data: the block and its items, and the permutation search, with a
stand-in for cl2000 that lays variables out the way the compiler does (by class: 16-bit scalars,
32-bit scalars, aggregates; inside a class a fixed shuffle of the definition order). No toolchain
needed."""
import random, re, unittest
from . import support  # noqa: F401
import c28ebss

SIZES = {'unsigned int': 1, 'int': 1, 'long': 2, 'unsigned long': 2, 'float': 2}


def fake_cl2000(shuffle_seed=0, spelling_matters=False):
    """layout(text) -> {name: offset}, like cl2000: class order, then a permutation of the slots
    that depends only on the sequence of classes (and, if asked, on the identifiers too). Only the
    first name of a declaration with several is laid out."""
    def layout(text):
        blk = c28ebss.block(text)
        defs = []
        for lines, names in blk[3]:
            code = c28ebss.strip_comments('\n'.join(lines))
            m = re.match(r'\s*(unsigned int|unsigned long|int|long|float|struct \w+)\s+(\w+)\s*(\[(\d+)\])?', code)
            t, n, arr = m.group(1), m.group(2), m.group(4)
            size = (int(arr) if arr else 1) * SIZES.get(t, 6) if not t.startswith('struct') else 6
            cls = 2 if arr or t.startswith('struct') else (0 if size == 1 else 1)
            defs.append((cls, size, n, t))
        out, off = {}, 0
        for cls in (0, 1, 2):
            group = [d for d in defs if d[0] == cls]
            key = ''.join(str(d[0]) for d in defs) + (''.join(d[2] for d in defs) if spelling_matters else '')
            rng = random.Random(key + str(shuffle_seed) + str(cls))
            perm = list(range(len(group))); rng.shuffle(perm)
            for i in perm:
                c, size, n, t = group[i]
                if size == 2 and off % 2: off += 1
                out[n] = off; off += size
        return out
    return layout


TEXT = '''#include "x.h"
/* ebss-begin */
unsigned int a0;
float f0 = 1.0f;     /* a trailing comment */
/* the counters */
unsigned int a1 = 3;
unsigned int a2, a3;
long l0;
struct blk b0;
unsigned int arr0[4] = { 1, 2, 3, 4 };
float f1;
unsigned int a4;
/* ebss-end */
void f(void) { a0++; }
'''


class BlockTest(unittest.TestCase):
    def test_items(self):
        lines, b, e, items, tail = c28ebss.block(TEXT)
        self.assertEqual([ns for _, ns in items], [['a0'], ['f0'], ['a1'], ['a2', 'a3'], ['l0'], ['b0'], ['arr0'], ['f1'], ['a4']])
        self.assertEqual(items[2][0], ['/* the counters */', 'unsigned int a1 = 3;'])    # a comment goes with what follows
        self.assertEqual(tail, [])
        self.assertEqual(c28ebss.compose(lines, b, e, items, tail), TEXT)

    def test_declared_names(self):
        self.assertEqual(c28ebss.declared_names('struct s { int x, y; } v = { 1, 2 };'), ['v'])
        self.assertEqual(c28ebss.declared_names('unsigned int a = 1, b[3] = { 1, 2, 3 }, c;'), ['a', 'b', 'c'])
        self.assertEqual(c28ebss.declared_names('volatile float *p;'), ['p'])

    def test_no_markers(self):
        self.assertIsNone(c28ebss.block('int a;\n'))


class SolveTest(unittest.TestCase):
    def addresses(self, names, layout_text, base=0x00C040):
        """The 'image': where a reference order of the definitions put each variable."""
        lay = fake_cl2000()(layout_text)
        return {n: base + lay[n] for n in lay}

    def reference(self):
        """The original file: the same definitions in another order."""
        lines, b, e, items, tail = c28ebss.block(TEXT)
        rng = random.Random(7)
        movable = [k for k, (it, ns) in enumerate(items) if len(ns) == 1]    # the solver never moves the others
        order = list(items); picked = [items[k] for k in movable]; rng.shuffle(picked)
        for k, it in zip(movable, picked): order[k] = it
        return c28ebss.compose(lines, b, e, order, tail)

    def test_misplaced(self):
        addr = {'a': 0x100, 'b': 0x101, 'c': 0x105}
        bad, base = c28ebss.misplaced({'a': 0, 'b': 1, 'c': 2}, addr.get)
        self.assertEqual((bad, base), (['c'], 0x100))

    def test_one_round_when_the_shuffle_follows_the_types(self):
        ref = self.reference()
        addr = self.addresses(None, ref)
        said = []
        ok, text = c28ebss.solve(TEXT, fake_cl2000(), addr.get, say=said.append)
        self.assertTrue(ok, said)
        lay = fake_cl2000()(text)
        self.assertEqual({n: 0x00C040 + o for n, o in lay.items()}, addr)
        self.assertTrue(text.endswith('void f(void) { a0++; }\n'))

    def test_already_right(self):
        addr = self.addresses(None, TEXT)
        ok, text = c28ebss.solve(TEXT, fake_cl2000(), addr.get, say=lambda s: None)
        self.assertTrue(ok)
        self.assertEqual(text, TEXT)

    def test_unknown_address_is_never_solved(self):
        addr = self.addresses(None, TEXT)
        del addr['a4']
        said = []
        ok, text = c28ebss.solve(TEXT, fake_cl2000(), addr.get, swaps=20, say=said.append)
        self.assertFalse(ok)
        self.assertEqual(text, TEXT)                 # left as it was
        self.assertTrue(any('NOT SOLVED' in s for s in said))

    def test_spellings_that_move_under_the_rounds(self):
        # when the identifiers take part, each round's renaming moves the shuffle; the swaps
        # still have to end at a layout that fits, or say so
        ref = self.reference()
        lay = fake_cl2000(spelling_matters=True)(ref)
        addr = {n: 0x00C040 + o for n, o in lay.items()}
        ok, text = c28ebss.solve(TEXT, fake_cl2000(spelling_matters=True), addr.get, swaps=400, say=lambda s: None)
        if ok:
            got = fake_cl2000(spelling_matters=True)(text)
            self.assertEqual({n: 0x00C040 + o for n, o in got.items()}, addr)
        else:
            self.assertEqual(text, TEXT)


if __name__ == '__main__':
    unittest.main()
