"""COFF objects as ofd2000 shows them, relocation fields, .cinit records, the image and dis2000's
listing, on text fixtures made from synthetic C (fixtures/small.c, fixtures/mix.c). No toolchain
needed."""
import os, struct, tempfile, unittest
from . import support
import c28tc, c28disasm


def fixture_obj():
    with open(os.path.join(support.FIXTURES, 'ofd_small.xml'), encoding='latin1') as f: xml = f.read()
    raw = bytearray(4096)
    with open(os.path.join(support.FIXTURES, 'ofd_small.words')) as f:
        for line in f:
            if line.startswith('#') or not line.strip(): continue
            name, ptr, ws = line.rstrip('\n').split('\t')
            ws = [int(w, 16) for w in ws.split()]
            raw[int(ptr):int(ptr) + 2 * len(ws)] = struct.pack('<%dH' % len(ws), *ws)
    return c28tc.parse_ofd(xml, bytes(raw), 'small.obj')


class ObjTest(unittest.TestCase):
    def setUp(self):
        self.o = fixture_obj()

    def test_sections(self):
        s = self.o.sections
        self.assertEqual(s['.text:_bump'].kind, 'text')
        self.assertEqual(s['.text:_bump'].size, 16)
        self.assertEqual(s['.econst:_steps'].data, [1, 2, 4])
        self.assertEqual(s['.ebss'].kind, 'bss')
        self.assertEqual(s['.ebss'].data, [])
        self.assertEqual(s['.cinit'].data, [0xFFFF, 0, 0, 5])

    def test_symbols(self):
        d = self.o.defined()
        self.assertEqual(d['_bump'], ('.text:_bump', 0))
        self.assertEqual(d['_steps'], ('.econst:_steps', 0))
        self.assertEqual(d['_count'][0], '.ebss')
        self.assertIn('_last', self.o.statics())
        self.assertEqual(self.o.undefined(), ['_notify'])

    def test_relocations(self):
        rel = self.o.sections['.text:_bump'].relocs
        types = {t for _, t, _ in rel}
        self.assertIn('R_PARTMS16', types)
        self.assertTrue(any(sym == '_notify' and t == 'R_REL22' for _, t, sym in rel))
        masks = c28tc.reloc_masks(self.o.sections['.text:_bump'])
        lcr = next(off for off, t, sym in rel if sym == '_notify')
        self.assertEqual((masks[lcr], masks[lcr + 1]), (0x003F, 0xFFFF))
        self.assertEqual(masks[0], 0)

    def test_cinit_record(self):
        recs = c28tc.cinit_records(self.o.sections['.cinit'].data)
        self.assertEqual(recs, [(0, 1, 3, (0xFFFF, 0, 0, 5))])


class FieldTest(unittest.TestCase):
    def test_decode(self):
        self.assertEqual(c28tc.decode('R_REL22', [0x7648, 0x0193], 0), 0x080193)
        self.assertEqual(c28tc.decode('R_ANKPTR', [0xA400, 0x0000], 0), 0xA400)
        self.assertEqual(c28tc.decode('R_PARTMS16', [0x0290], 0), 0xA400)
        self.assertEqual(c28tc.decode('R_PARTLS6', [0x9201], 0), 1)

    def test_cinit_forms_and_end(self):
        ws = [0xFFFE, 0xA400, 0x0000, 7, 8,     # two words at a 32-bit address
              0x0001, 0xA410, 9,                # one word at a 16-bit address
              0x0000, 0x0000]                   # the end
        recs = c28tc.cinit_records(ws)
        self.assertEqual([(p, n, h) for p, n, h, _ in recs], [(0, 2, 3), (5, 1, 2)])

    def test_derive_bases(self):
        # a section that loads a data page and reads @3 of `_x`, and calls `_f`
        sec = c28tc.Section('.text:_g', 5, [0x761F, 0x0000, 0x9200, 0x7640, 0x0000], [
            (1, 'R_PARTMS16', '_x'), (2, 'R_PARTLS6', '_x'), (3, 'R_REL22', '_f')], 'text')
        img = c28tc.Image([0xFFFF, 0x761F, 0x0291, 0x9205, 0x7648, 0x0120], 0x080000)
        got = c28tc.derive_bases(sec, 0x080001, img)
        self.assertEqual(got['_x'], [0x0291 * 64 + 5])
        self.assertEqual(got['_f'], [0x080120])


class ImageTest(unittest.TestCase):
    def test_load_big_and_little(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'a.bin')
        with open(p, 'wb') as f: f.write(bytes([0x12, 0x34, 0xAB, 0xCD]))
        img = c28tc.Image.load(p, 0x080000)
        self.assertEqual(img.words(0x080000, 2), [0x1234, 0xABCD])
        self.assertEqual(c28tc.Image.load(p, 0x080000, byte_order='little').W(0x080001), 0xCDAB)
        self.assertTrue(img.contains(0x080001) and not img.contains(0x080001, 2))
        with self.assertRaises(c28tc.ToolError): c28tc.Image.load(p, 0, words=4)
        with self.assertRaises(c28tc.ToolError): img.words(0x080001, 2)


class DisTest(unittest.TestCase):
    def test_parse(self):
        with open(os.path.join(support.FIXTURES, 'dis_mix.txt')) as f:
            ins = c28disasm.parse(f.read())
        self.assertEqual(ins[0], (0, [0xE2AF, 0x01C4], 'MOV32 R1H, *+XAR4[0], UNCF'))
        par = [x for x in ins if '||' in x[2]]
        self.assertEqual(len(par), 2)
        self.assertEqual(par[0][2], 'MPYF32 R0H, R0H, R2H ||MOV32 *+XAR4[2], R1H')
        self.assertEqual(par[0][1], [0xE004, 0x01D4])
        self.assertEqual(ins[-1], (0x13, [0x0006], 'LRETR'))
        self.assertEqual(sum(len(w) for _, w, _ in ins), 0x14)

    def test_annotate(self):
        names = {0x080193: 'loop_step'}
        self.assertEqual(c28disasm.annotate('LCR 0x080193', names), 'LCR 0x080193 <loop_step>')
        self.assertEqual(c28disasm.annotate('MOV AL, @0x1', names), 'MOV AL, @0x1')


if __name__ == '__main__':
    unittest.main()
