import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import ehex

HEX = (':020000040800F2\r\n'
       ':048000000102030472\r\n'
       ':02800600AABB13\r\n'
       ':040000050800816509\r\n'
       ':00000001FF\r\n')


class EhexTest(unittest.TestCase):
    def path(self, text, name='a [1].ehex'):
        d = tempfile.mkdtemp(); p = os.path.join(d, name)
        with open(p, 'w', newline='') as f: f.write(text)
        return p

    def test_read_fills_gaps(self):
        base, data = ehex.read(self.path(HEX))
        self.assertEqual(base, 0x08008000)
        self.assertEqual(data, bytes([1, 2, 3, 4, 0xFF, 0xFF, 0xAA, 0xBB]))

    def test_bad_checksum_is_refused(self):
        with self.assertRaises(ValueError):
            ehex.read(self.path(HEX.replace('0472', '0473')))

    def test_image_bounds(self):
        img = ehex.Image(self.path(HEX))
        self.assertEqual(img.bytes(0x08008002, 2), bytes([3, 4]))
        self.assertEqual(img.end, 0x08008008)
        with self.assertRaises(ValueError):
            img.bytes(0x08008006, 4)


if __name__ == '__main__':
    unittest.main()
