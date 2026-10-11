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
