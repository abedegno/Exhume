"""exhume.toml for a C2000 project: the profile's defaults, [c2000] and [c2000.gate], the per-file
lines, and finding installed compilers. No toolchain needed."""
import os, tempfile, textwrap, unittest
from . import support  # noqa: F401
import config, c28tc


class ConfigTest(unittest.TestCase):
    def write(self, text):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'exhume.toml')
        with open(p, 'w') as f: f.write(textwrap.dedent(text))
        return d, p

    def test_profile_defaults(self):
        d, p = self.write('''
            [project]
            name = "t"
            [toolchain]
            profile = "ti-c2000"
            home = "~/c2000/cgt/16.9.6.LTS"
            [binary]
            image = "fw/app.bin"
            [c2000]
            base = 0x080000
            native = "tools/native/bin"
            wine = "/opt/wine/bin"
            prefix = "~/c2000/wine"
            others = "~/c2000/cgt/*/bin/cl2000.exe"
            [c2000.gate]
            cinit = 0x080400
            cinit_order = ["a", "rts:_lock.obj", "b"]
        ''')
        cfg = config.Config(p)
        self.assertEqual(cfg.object_model, 'linked-image')
        self.assertEqual(cfg.profile['link']['match_tool'], 'c28match')
        self.assertEqual(cfg.c_opts, '-v28 -ml -mt --float_support=fpu32 -O1')
        s = c28tc.Settings.from_config(cfg)
        self.assertEqual(s.base, 0x080000)
        self.assertIsNone(s.words)
        self.assertEqual(s.byte_order, 'big')
        self.assertEqual(s.fill, 0xFFFF)
        self.assertEqual(s.cflags, ['-v28', '-ml', '-mt', '--float_support=fpu32', '-O1'])
        self.assertEqual(s.tool_cflags, ['--gen_func_subsections=on'])
        self.assertEqual(s.image, os.path.join(d, 'fw/app.bin'))
        self.assertEqual(s.cgt, os.path.expanduser('~/c2000/cgt/16.9.6.LTS'))
        self.assertEqual(s.rtslib, os.path.join(s.cgt, 'lib/rts2800_fpu32.lib'))
        self.assertEqual(s.native, os.path.join(d, 'tools/native/bin'))
        self.assertEqual(s.prefix, os.path.expanduser('~/c2000/wine'))
        self.assertEqual(s.gate['cinit'], 0x080400)
        self.assertEqual(s.gate['cinit_order'][1], 'rts:_lock.obj')
        self.assertEqual((s.park, s.park_data), (0x200000, 0x300000))
        self.assertEqual(s.map_dir, os.path.join(d, 'map'))

    def test_project_overrides(self):
        d, p = self.write('''
            [project]
            name = "t"
            [toolchain]
            profile = "ti-c2000"
            home = "/cgt"
            c_opts = "-v28 -ml --float_support=fpu32 -O2"
            [binary]
            image = "app.bin"
            [c2000]
            base = "0x0A0000"
            words = 0x8000
            byte_order = "little"
            fill = 0
            rtslib = "/elsewhere/rts2800_fpu32.lib"
            park = 0x100000
        ''')
        s = c28tc.Settings.from_config(config.Config(p))
        self.assertEqual((s.base, s.words, s.byte_order, s.fill), (0x0A0000, 0x8000, 'little', 0))
        self.assertEqual(s.cflags[-1], '-O2')
        self.assertEqual(s.rtslib, '/elsewhere/rts2800_fpu32.lib')
        self.assertEqual(s.park, 0x100000)

    def test_base_is_required(self):
        d, p = self.write('''
            [project]
            name = "t"
            [toolchain]
            profile = "ti-c2000"
        ''')
        with self.assertRaises(c28tc.ToolError):
            c28tc.Settings.from_config(config.Config(p))


class DirectiveTest(unittest.TestCase):
    def test_lines(self):
        d = c28tc.directives('/* cgt: 16.9.6.LTS */\n// cflags: -mf --opt_for_speed=2\n#include "x.h"\n')
        self.assertEqual(d['cgt'], '16.9.6.LTS')
        self.assertIsNone(d['opts'])
        self.assertEqual(d['cflags'], ['-mf', '--opt_for_speed=2'])

    def test_opts_replace_and_assembly(self):
        d = c28tc.directives('; cgt: 15.12.3\n; opts: -v28 -ml -Ooff\n\t.sect "x"\n')
        self.assertEqual(d['cgt'], '15.12.3')
        self.assertEqual(d['opts'], ['-v28', '-ml', '-Ooff'])

    def test_only_the_head_counts(self):
        d = c28tc.directives('int x;\n' * 1000 + '/* cgt: 1.0 */\n')
        self.assertIsNone(d['cgt'])

    def test_flags_for(self):
        s = c28tc.Settings(tempfile.mkdtemp(), cflags='-v28 -O1', tool_cflags='--gen_func_subsections=on')
        tc = c28tc.Toolchain(s)
        src = os.path.join(s.root, 'a.c')
        with open(src, 'w') as f: f.write('/* opts: -v28 -Ooff */\n/* cflags: -g */\nint a;\n')
        self.assertEqual(tc.flags_for(src), ['-v28', '-Ooff', '-g', '--gen_func_subsections=on'])
        with open(src, 'w') as f: f.write('int a;\n')
        self.assertEqual(tc.flags_for(src), ['-v28', '-O1', '--gen_func_subsections=on'])


class InstalledTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        for v, inner in (('15.12.3.LTS', 'c2000_15.12.3.LTS'), ('16.9.6.LTS', 'ti-cgt-c2000_16.9.6.LTS'),
                         ('15.12.7', 'ti-cgt-c2000_15.12.7.LTS')):
            os.makedirs(os.path.join(self.d, v, inner, 'bin'))
            open(os.path.join(self.d, v, inner, 'bin', 'cl2000.exe'), 'w').close()
        self.others = os.path.join(self.d, '*', '*', 'bin', 'cl2000.exe')

    def test_homes(self):
        self.assertEqual(len(c28tc.cgt_homes(self.others)), 3)

    def test_resolve_by_version(self):
        self.assertTrue(c28tc.resolve_cgt('16.9.6.LTS', self.others).endswith('ti-cgt-c2000_16.9.6.LTS'))
        self.assertTrue(c28tc.resolve_cgt('15.12.3.LTS', self.others).endswith('c2000_15.12.3.LTS'))
        self.assertTrue(c28tc.resolve_cgt('15.12.7', self.others).endswith('ti-cgt-c2000_15.12.7.LTS'))
        with self.assertRaises(c28tc.ToolError):
            c28tc.resolve_cgt('16.9.9.LTS', self.others)

    def test_version_of_a_home(self):
        self.assertEqual(c28tc.cgt_version('/x/ti-cgt-c2000_16.9.6.LTS'), '16.9.6.LTS')


class SourcesTest(unittest.TestCase):
    def test_stems_must_be_unique(self):
        d = tempfile.mkdtemp()
        for p in ('src/a/x.c', 'src/b/y.c', 'src/include/z.c'):
            os.makedirs(os.path.dirname(os.path.join(d, p)), exist_ok=True)
            open(os.path.join(d, p), 'w').close()
        s = c28tc.Settings(d)
        self.assertEqual([os.path.relpath(p, d) for p in s.sources()], ['src/a/x.c', 'src/b/y.c'])
        self.assertEqual(os.path.relpath(s.source('y'), d), 'src/b/y.c')
        open(os.path.join(d, 'src/b/x.asm'), 'w').close()
        with self.assertRaises(c28tc.ToolError):
            s.sources()


if __name__ == '__main__':
    unittest.main()
