import os, sys, tempfile, textwrap, unittest
EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
sys.path.insert(0, os.path.join(EXHUME, 'tools'))
import config


class ConfigTest(unittest.TestCase):
    def write(self, text):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'exhume.toml')
        open(p, 'w').write(textwrap.dedent(text)); return d, p

    def test_armcc5_profile_is_linked_image(self):
        d, p = self.write('''
            [project]
            name = "t"
            [toolchain]
            profile = "armcc5"
            home = "tc"
            [binary]
            image = "fw/app.ehex"
        ''')
        cfg = config.Config(p)
        self.assertEqual(cfg.object_model, 'linked-image')
        self.assertEqual(cfg.image, os.path.join(d, 'fw/app.ehex'))
        self.assertEqual(cfg.home, os.path.join(d, 'tc'))
        self.assertEqual(cfg.c_opts, '--cpu Cortex-M3 -O1 -Otime --split_sections')

    def test_borland_default_is_omf(self):
        d, p = self.write('''
            [project]
            name = "t"
        ''')
        cfg = config.Config(p)
        self.assertEqual(cfg.object_model, 'omf-masked')
        self.assertIsNone(cfg.image)


if __name__ == '__main__':
    unittest.main()
