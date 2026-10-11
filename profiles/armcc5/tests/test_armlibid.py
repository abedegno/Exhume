"""armlibid finds a library source's functions in an image, by masked compare and name order."""
import os, shutil, subprocess, sys, tempfile, textwrap, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); PROFILE = os.path.dirname(HERE)
EXHUME = os.path.dirname(os.path.dirname(PROFILE))
sys.path.insert(0, os.path.join(PROFILE, 'tools')); sys.path.insert(0, os.path.join(PROFILE, 'testfw'))
sys.path.insert(0, os.path.join(EXHUME, 'tools'))
import armtc, answerkey, armlibid, config

HOME = os.environ.get('EXHUME_ARMCC_HOME')
DOCKER = shutil.which('docker') and subprocess.run(['docker', 'info'], capture_output=True).returncode == 0


class Order(unittest.TestCase):
    def test_ambiguity_settled_by_name_order(self):
        # b_fn could be at 0x10 or 0x40; a_fn (0x08) and c_fn (0x20) are known, so only 0x10 fits
        at, amb = armlibid.settle({'a_fn': [0x08], 'b_fn': [0x10, 0x40], 'c_fn': [0x20]})
        self.assertEqual(at, {'a_fn': 0x08, 'b_fn': 0x10, 'c_fn': 0x20})
        self.assertEqual(amb, {})

    def test_unsettled_stays_ambiguous(self):
        at, amb = armlibid.settle({'b_fn': [0x10, 0x40]})
        self.assertEqual(at, {})
        self.assertEqual(amb, {'b_fn': [0x10, 0x40]})


@unittest.skipUnless(HOME and DOCKER, 'needs EXHUME_ARMCC_HOME and Docker')
class OnTestFirmware(unittest.TestCase):
    def test_crc_as_a_library(self):
        root = tempfile.mkdtemp(dir=os.path.expanduser('~'), prefix='exhume-libid-')
        tc = armtc.Toolchain(HOME, root)
        try:
            key = answerkey.build(tc, root)
            with open(os.path.join(root, 'exhume.toml'), 'w') as f:
                f.write(textwrap.dedent(f'''
                    [project]
                    name = "t"
                    [toolchain]
                    profile = "armcc5"
                    home = "{HOME}"
                    [binary]
                    image = "target/testfw.hex"
                '''))
            cfg = config.Config(os.path.join(root, 'exhume.toml'))
            at, amb, missing = armlibid.identify(cfg, os.path.join(root, 'src', 'crc.c'), answerkey.OPTS)
            self.assertEqual(at['crc16'], key['answers']['crc16'][0])
            self.assertEqual(at['step'], key['answers']['step'][0])
            self.assertEqual((amb, missing), ({}, []))
        finally:
            tc.stop(); shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
