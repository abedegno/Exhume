"""The open-toolchain build (clang, GNU ld) of the emulator test firmware: it boots in the system
emulator, two builds of it compare the same, and the differential tests catch each build bug
the open build exists to avoid. Needs clang, arm-none-eabi binutils, unicorn and capstone."""
import os, shutil, subprocess, unittest
from profiles.armcc5.tests.emusupport import UNICORN, OPEN_TOOLS, make_root, open_build, SystemChecks

READY = UNICORN and OPEN_TOOLS
STABLE = ['crc16', 'next_frame', 'timers_tick', 'soc_percent', 'lookup', 'big_switch', 'version', 'tbl', 'buf',
          'rec_set', 'setting_set', 'count_and_next', 'i2c_write', 'USART1_IRQHandler', 'TIM2_IRQHandler']


def difftest(a, b, *funcs, extra=()):
    import difftest as dt
    lines = []
    rc = dt.main(['--config', a['cfg'].file, '--a', a['elf'], '--a-syms', a['elf'], '--b', b['elf'], '--b-syms', b['elf'],
                  '--runs', '40', '--init-rw', *extra, *funcs], out=lines.append)
    return rc, {r.name: r for r in dt.main.results}, '\n'.join(lines)


@unittest.skipUnless(READY, 'needs clang, arm-none-eabi binutils, unicorn and capstone')
class OpenBuild(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cls.cfg = make_root('exhume-openbuild-')
        cls.addClassCleanup(shutil.rmtree, cls.root, True)
        cls.ref = open_build(cls.cfg, os.path.join(cls.root, 'build', 'ref'), opt='-Os')
        cls.ref['cfg'] = cls.cfg

    def candidate(self, name, **over):
        b = open_build(self.cfg, os.path.join(self.root, 'build', name), **over)
        b['cfg'] = self.cfg
        return b

    def test_another_optimisation_behaves_the_same(self):
        b = self.candidate('o1', opt='-O1', ram_syms=self.ref['elf'])
        rc, res, text = difftest(self.ref, b, *STABLE)
        self.assertEqual(rc, 0, text)

    def test_a_patched_byte_is_caught(self):
        rc, res, text = difftest(self.ref, self.ref, 'crc16', extra=('--mutate', 'crc16=2:0x00'))
        self.assertFalse(res['crc16'].same, text)

    def test_packed_before_struct_needs_the_rewrite(self):
        # without it clang unpacks the struct: rec_set writes a and b at other offsets
        b = self.candidate('nopack', default_rewrites=False)
        rc, res, text = difftest(self.ref, b, 'rec_set', 'setting_set')
        self.assertFalse(res['rec_set'].same, text)
        self.assertTrue(res['setting_set'].same, text)

    def test_enums_need_short_enums(self):
        b = self.candidate('intenum', short_enums=False)
        rc, res, text = difftest(self.ref, b, 'setting_set', 'rec_set')
        self.assertFalse(res['setting_set'].same, text)
        self.assertTrue(res['rec_set'].same, text)

    def test_ram_layout_follows_the_reference(self):
        import images
        b = self.candidate('layout', opt='-O2', ram_syms=self.ref['elf'])
        ra, rb = images.load_symbols(self.ref['elf']).data, images.load_symbols(b['elf']).data
        ram = [n for n, (a, z) in ra.items() if 0x20000000 <= a < 0x20002000]
        self.assertGreater(len(ram), 20)
        for n in ram: self.assertEqual(rb[n][0], ra[n][0], n)

    def test_over_alignment_is_lowered(self):
        import ramlayout
        b = self.candidate('align')
        obj = next(o for o in b['objects'] if o.endswith('quirks.o'))
        text, notes = ramlayout.place([obj], {'g_count': 0x20000101}, (0x20000000, 0x2000))
        self.assertIn('. = 0x101; KEEP(*(.data.g_count))', text)
        self.assertTrue(any(n.startswith('g_count: aligned to') and n.endswith('lowered to 1 for 0x20000101')
                            for n in notes), notes)
        out = subprocess.run(['arm-none-eabi-objdump', '-h', obj], capture_output=True, text=True).stdout
        self.assertRegex(out, r'\.data\.g_count\s+\S+\s+\S+\s+\S+\s+\S+\s+2\*\*0')

    def test_a_name_in_two_objects_is_not_placed(self):
        import ramlayout
        b = self.candidate('dup')
        objs = [o for o in b['objects'] if o.endswith(('quirks.o', 'system.o'))]
        text, notes = ramlayout.place(objs + objs[:1], {'g_count': 0x20000100}, (0x20000000, 0x2000))
        self.assertNotIn('g_count', text)
        self.assertTrue(any('defined in 2 objects' in n for n in notes), notes)


@unittest.skipUnless(READY, 'needs clang, arm-none-eabi binutils, unicorn and capstone')
class OpenBuildSystem(SystemChecks, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root, cfg = make_root('exhume-opensys-')
        cls.addClassCleanup(shutil.rmtree, cls.root, True)
        b = open_build(cfg, os.path.join(cls.root, 'build', 'sys'), opt='-Os')
        cls.image = cls.syms = b['elf']


if __name__ == '__main__':
    unittest.main()
