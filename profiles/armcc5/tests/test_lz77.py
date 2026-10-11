import os, random, sys, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import lz77


class Lz77Test(unittest.TestCase):
    def test_tokens(self):
        # 0x12: one literal (0xAB), then a zero run of 1; 0x09: n=0 so the next byte (0x17) is n,
        # then offset 1: copy n + 2 = 25 bytes
        out, used = lz77.decompress(bytes([0x12, 0xAB, 0x09, 0x17, 0x01]), 27)
        self.assertEqual(out, bytes([0xAB, 0]) + bytes(25))
        self.assertEqual(used, 5)

    def test_round_trip(self):
        rnd = random.Random(7)
        for case in range(60):
            raw = bytearray()
            target = rnd.randrange(1, 700)
            while len(raw) < target:
                k = rnd.random()
                if k < 0.3: raw += bytes(rnd.randrange(1, 300))
                elif k < 0.6 and raw: start = rnd.randrange(len(raw)); raw += raw[start:start + rnd.randrange(1, 40)]
                else: raw += bytes(rnd.randrange(256) for _ in range(rnd.randrange(1, 300)))
            comp = lz77.compress(bytes(raw))
            out, used = lz77.decompress(comp, len(raw))
            self.assertEqual(out, bytes(raw), f'case {case}')
            self.assertEqual(used, len(comp), f'case {case}')


if __name__ == '__main__':
    unittest.main()
