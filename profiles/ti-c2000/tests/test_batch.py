"""The batch climber's text handling and scoring, and the gate's memory map, on synthetic input.
No toolchain needed."""
import unittest
from . import support  # noqa: F401
import c28batch, c28gate, c28tc

SRC = '''#include "x.h"
extern void tick(unsigned int n);
unsigned int tick_count;

void tick(unsigned int n)
{
    if (n) { tick_count += n; }
    else { tick_count = 0; }
}

static float twice(float x) { return x + x; }
'''


class TextTest(unittest.TestCase):
    def test_find_function_skips_the_declaration(self):
        i, j = c28batch.find_function(SRC, 'tick')
        self.assertTrue(SRC[i:j].startswith('void tick(unsigned int n)\n{'))
        self.assertTrue(SRC[i:j].endswith('}'))
        i, j = c28batch.find_function(SRC, 'twice')
        self.assertEqual(SRC[i:j], 'static float twice(float x) { return x + x; }')
        with self.assertRaises(c28tc.ToolError):
            c28batch.find_function(SRC, 'tock')

    def test_variants_and_copies(self):
        spec = [['tick_count += n;', ['tick_count += n;', 'tick_count = tick_count + n;']],
                ['tick_count = 0;', ['tick_count = 0;', 'tick_count -= tick_count;']]]
        i, j = c28batch.find_function(SRC, 'tick')
        body = SRC[i:j]
        v = c28batch.variant(body, spec, (1, 1))
        self.assertIn('tick_count = tick_count + n;', v)
        self.assertIn('tick_count -= tick_count;', v)
        out = c28batch.with_copies(SRC, 'tick', [c28batch.variant(body, spec, p) for p in ((0, 0), (1, 0))])
        self.assertIn('void tick_v0(unsigned int n)', out)
        self.assertIn('void tick_v1(unsigned int n)', out)
        self.assertLess(out.index('void tick(unsigned int n)'), out.index('tick_v0'))     # after the original
        self.assertLess(out.index('tick_v1'), out.index('static float twice'))            # before what follows
        self.assertEqual(out.count('extern void tick(unsigned int n);'), 1)
        with self.assertRaises(c28tc.ToolError):
            c28batch.variant(body, [['no such text', ['a']]], (0,))

    def test_normalise(self):
        texts = ['MOVW DP, #0x290', 'MOV AL, @0x1', 'MOVL XAR4, #0x00a40a', 'LCR 0x080193', 'MOVW DP, #0x291', 'MOV @0x2, AL']
        loose = c28batch.normalise(texts)
        self.assertEqual(loose, ['MOVW DP', 'MOV AL, @D', 'MOVL XAR4, #ADDR', 'LCR CALL', 'MOVW DP', 'MOV @D, AL'])
        strict = c28batch.normalise(texts, own=(0xA400, 0xA440), strict=True)
        self.assertEqual(strict, ['MOVW DP, #OWN', 'MOV AL, @OWN', 'MOVL XAR4, #0x00a40a', 'LCR CALL',
                                  'MOVW DP, #0x291', 'MOV @A442, AL'])

    def test_score(self):
        self.assertEqual(c28batch.score([1, 2], [1, 2], ['A', 'B'], ['A', 'B']), (0, 0, 0))
        self.assertEqual(c28batch.score([1, 3], [1, 2], ['A', 'B'], ['A', 'B']), (1, 0, 0))
        self.assertEqual(c28batch.score([1, 2, 3], [1, 2], ['A', 'B', 'C'], ['A', 'B']), (1, 1, 1))


class GateTest(unittest.TestCase):
    def test_memory_covers_the_space_with_parking(self):
        s = c28tc.Settings('/tmp')
        text = c28gate.memory(s)
        self.assertIn('PAGE 0: PARKP : origin = 0x200000, length = 0x80000', text)
        self.assertIn('PAGE 1: PARKD : origin = 0x300000, length = 0x80000', text)
        self.assertIn('PAGE 0: LOWP : origin = 0x0, length = 0x200000', text)
        self.assertIn('PAGE 1: HIGHD : origin = 0x380000, length = 0x80000', text)
        self.assertTrue(c28gate.parked(s, 0x200010) and c28gate.parked(s, 0x37FFFF))
        self.assertFalse(c28gate.parked(s, 0x080000) or c28gate.parked(s, None))

    def test_quote(self):
        self.assertEqual(c28gate.quote('.text'), '.text')
        self.assertEqual(c28gate.quote('.text:_f'), '".text:_f"')


if __name__ == '__main__':
    unittest.main()
