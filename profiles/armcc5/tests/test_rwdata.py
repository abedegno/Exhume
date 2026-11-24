import os, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); PROFILE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROFILE, 'tools')); sys.path.insert(0, os.path.join(PROFILE, 'testfw'))
import armtc, answerkey, ehex, rwdata

HOME = os.environ.get('EXHUME_ARMCC_HOME')
DOCKER = shutil.which('docker') and subprocess.run(['docker', 'info'], capture_output=True).returncode == 0


class NoTable(unittest.TestCase):
    def test_raw_image_without_scatterload(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'x.bin')
        with open(p, 'wb') as f: f.write(bytes(64))
        with self.assertRaises(ValueError):
            rwdata.find_table(ehex.Image(p))


class StandardLibraryTable(unittest.TestCase):
    """The standard C library's __scatterload_rt2: an adr to two offsets, and handlers of its own.
    The image is made by hand (the code patterns and a table), so no toolchain is needed."""

    def image(self):
        import struct
        b = bytearray(0x100)
        struct.pack_into('<H', b, 0, 0xA004)                      # adr r0, T  (T = 4 + 4*4 = 0x14)
        b[2:2 + len(rwdata.SCATTERLOAD_RT2)] = rwdata.SCATTERLOAD_RT2
        struct.pack_into('<II', b, 0x14, 0x40 - 0x14, 0x60 - 0x14)  # table base and limit, from T
        struct.pack_into('<IIII', b, 0x40, 0, 0x20000000, 32, 0x80 | 1)        # zeroed by the library's zeroinit
        struct.pack_into('<IIII', b, 0x50, 0xC0, 0x20000020, 8, 0xA0 | 1)      # copied
        b[0x80:0x80 + len(rwdata.HANDLERS['zero '])] = rwdata.HANDLERS['zero ']
        b[0xA0:0xA0 + len(rwdata.HANDLERS['copy'])] = rwdata.HANDLERS['copy']
        b[0xC0:0xC8] = bytes(range(1, 9))
        d = tempfile.mkdtemp(); p = os.path.join(d, 'x.bin')
        with open(p, 'wb') as f: f.write(bytes(b))
        return ehex.Image(p)

    def test_table_and_regions(self):
        img = self.image()
        self.assertEqual(rwdata._table_rt2(img), (0x40, 0x60))
        regs = rwdata.find_table(img)
        self.assertEqual([(r.load, r.exec, r.size, r.kind) for r in regs],
                         [(0, 0x20000000, 32, 'zero'), (0xC0, 0x20000020, 8, 'copy')])
        ram = rwdata.RamImage(img, regs)
        self.assertEqual(ram.bytes(0x20000000, 32), bytes(32))
        self.assertEqual(ram.bytes(0x20000020, 8), bytes(range(1, 9)))
        self.assertEqual(ram.kind_at(0x20000004), 'zero')

    def test_its_decompress_handler_is_known(self):
        import struct
        b = bytearray(0x40); code = rwdata.HANDLERS['compressed ']
        b[0x10:0x10 + len(code)] = code
        d = tempfile.mkdtemp(); p = os.path.join(d, 'x.bin')
        with open(p, 'wb') as f: f.write(bytes(b))
        self.assertEqual(rwdata._kind(ehex.Image(p), 0x10 | 1), 'compressed')

    def test_another_instruction_before_the_pattern_is_not_a_table(self):
        b = bytearray(0x40); b[2:2 + len(rwdata.SCATTERLOAD_RT2)] = rwdata.SCATTERLOAD_RT2   # no adr before it
        d = tempfile.mkdtemp(); p = os.path.join(d, 'x.bin')
        with open(p, 'wb') as f: f.write(bytes(b))
        with self.assertRaises(ValueError):
            rwdata.find_table(ehex.Image(p))


@unittest.skipUnless(HOME and DOCKER, 'needs EXHUME_ARMCC_HOME and Docker')
class TestFirmwareRam(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(dir=os.path.expanduser('~'), prefix='exhume-rwd-')
        cls.tc = armtc.Toolchain(HOME, cls.root)
        cls.key = answerkey.build(cls.tc, cls.root)
        cls.img = ehex.Image(os.path.join(cls.root, 'target', 'testfw.hex'))

    @classmethod
    def tearDownClass(cls):
        cls.tc.stop(); shutil.rmtree(cls.root, ignore_errors=True)

    def test_table(self):
        regs = rwdata.find_table(self.img)
        self.assertEqual(regs[0].kind, 'compressed')
        self.assertEqual(regs[0].exec, 0x20000000)

    def test_ram_image(self):
        ram = rwdata.ram_image(self.img)
        g_soc = self.key['answers']['g_soc_tenths'][0]
        self.assertEqual(ram.bytes(g_soc, 2), (955).to_bytes(2, 'little'))
        g_tbl = self.key['answers']['g_tbl'][0]
        self.assertEqual(ram.bytes(g_tbl, 4), bytes([0, 1, 2, 3]))

    def test_straddling_rw_into_zero(self):
        regs = rwdata.find_table(self.img); ram = rwdata.ram_image(self.img)
        z = next(r for r in regs if r.kind == 'zero')
        self.assertEqual(ram.bytes(z.exec - 2, 4)[2:], bytes(2))
        self.assertFalse(ram.covers(z.exec + z.size - 1, 2))


if __name__ == '__main__':
    unittest.main()
