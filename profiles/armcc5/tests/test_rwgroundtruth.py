"""armlink's own compressed RW data, from the test firmware, equals lz77.compress of the same bytes."""
import os, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); PROFILE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROFILE, 'tools')); sys.path.insert(0, os.path.join(PROFILE, 'testfw'))
import armtc, answerkey, ehex, lz77

HOME = os.environ.get('EXHUME_ARMCC_HOME')
DOCKER = shutil.which('docker') and subprocess.run(['docker', 'info'], capture_output=True).returncode == 0


@unittest.skipUnless(HOME and DOCKER, 'needs EXHUME_ARMCC_HOME and Docker')
class GroundTruth(unittest.TestCase):
    def test_armlink_and_lz77_agree(self):
        root = tempfile.mkdtemp(dir=os.path.expanduser('~'), prefix='exhume-rw-')
        tc = armtc.Toolchain(HOME, root)
        try:
            key = answerkey.build(tc, root)
            load, execa, size = key['rw']
            img = ehex.Image(os.path.join(root, 'target', 'testfw.hex'))
            src = img.data[load - img.base:]
            raw, used = lz77.decompress(src, size)
            self.assertGreater(size, 0x100)
            self.assertEqual(lz77.compress(raw), src[:used])
        finally:
            tc.stop(); shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
