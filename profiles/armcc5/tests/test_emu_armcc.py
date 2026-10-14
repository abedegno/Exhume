"""The emulator test firmware built by Arm Compiler 5 as an original would be: it boots in the
system emulator, it is the same as itself under the differential tests, and the open build of
the same C, in the original's RAM layout, behaves the same function by function. Needs
EXHUME_ARMCC_HOME and Docker (or EXHUME_ARMCC_RUNNER=wine and Wine), and for the last also
clang and arm-none-eabi binutils."""
import os, shutil, unittest
from profiles.armcc5.tests.emusupport import UNICORN, OPEN_TOOLS, ARMCC, ARMCC_HOME, make_root, open_build, \
    SystemChecks

# the functions whose behaviour does not depend on the compiler (quirks.c's rate_for and
# bcd_next are firmware bugs that may differ)
STABLE = ['crc16', 'next_frame', 'timers_tick', 'soc_percent', 'lookup', 'big_switch', 'version', 'tbl', 'cfg',
          'mixed', 'buf', 'init', 'rec_set', 'setting_set', 'count_and_next', 'i2c_write', 'i2c_read',
          'SysTick_Handler', 'TIM2_IRQHandler', 'USART1_IRQHandler']


@unittest.skipUnless(UNICORN and ARMCC, 'needs unicorn, capstone and the armcc toolchain (EXHUME_ARMCC_HOME)')
class ArmccSystem(SystemChecks, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import armtc, emufw
        cls.root, cls.cfg = make_root('exhume-emuarmcc-')
        cls.addClassCleanup(shutil.rmtree, cls.root, True)
        cls.tc = armtc.Toolchain(ARMCC_HOME, cls.root)
        cls.addClassCleanup(cls.tc.stop)
        cls.image, cls.syms = emufw.build_armcc(cls.tc, cls.root)

    def difftest(self, b, bsyms, *args):
        import difftest
        lines = []
        rc = difftest.main(['--config', self.cfg.file, '--a', self.image, '--a-syms', self.syms, '--b', b,
                            '--b-syms', bsyms, '--runs', '40', *args], out=lines.append)
        return rc, {r.name: r for r in difftest.main.results}, '\n'.join(lines)

    def test_the_exact_build_is_the_same_as_itself(self):
        rc, res, text = self.difftest(self.image, self.syms)
        self.assertEqual(rc, 0, text)
        self.assertGreater(len(res), 20)

    def test_the_default_function_list_comes_from_the_sources(self):
        import difftest
        lines = []
        difftest.main(['--config', self.cfg.file, '--a', self.image, '--a-syms', self.syms, '--b', self.image,
                       '--list'], out=lines.append)
        self.assertIn('crc16', lines); self.assertIn('rate_for', lines)
        self.assertNotIn('__scatterload', lines); self.assertNotIn('Reset_Handler', lines)

    @unittest.skipUnless(OPEN_TOOLS, 'needs clang and arm-none-eabi binutils')
    def test_the_open_build_behaves_the_same(self):
        b = open_build(self.cfg, os.path.join(self.root, 'build', 'open'), ram_syms=self.syms)
        rc, res, text = self.difftest(b['elf'], b['elf'], '--init-rw', *STABLE)
        self.assertEqual(rc, 0, text)

    @unittest.skipUnless(OPEN_TOOLS, 'needs clang and arm-none-eabi binutils')
    def test_the_open_build_needs_the_original_ram_layout(self):
        # without it another variable follows g_count: count_and_next reads a different neighbour
        b = open_build(self.cfg, os.path.join(self.root, 'build', 'nolayout'), ram_layout=False)
        rc, res, text = self.difftest(b['elf'], b['elf'], 'count_and_next')
        self.assertFalse(res['count_and_next'].same, text)

    @unittest.skipUnless(OPEN_TOOLS, 'needs clang and arm-none-eabi binutils')
    def test_the_open_build_boots_too(self):
        b = open_build(self.cfg, os.path.join(self.root, 'build', 'boot'), ram_syms=self.syms)
        saved = (self.image, self.syms)
        try:
            self.image = self.syms = b['elf']
            self.test_uart_answers_a_line()
        finally:
            self.image, self.syms = saved


if __name__ == '__main__':
    unittest.main()
