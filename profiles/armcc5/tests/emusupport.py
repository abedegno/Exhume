"""Shared by the emulator and open-build tests: paths, what is installed, and the emulator test
firmware's builds (testfw/emufw.py) in a throwaway root."""
import importlib.util, os, shutil, subprocess, sys, tempfile, textwrap
HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.dirname(HERE)
EXHUME = os.path.dirname(os.path.dirname(PROFILE))
EMU = os.path.join(PROFILE, 'tools', 'emu')
OPENBUILD = os.path.join(PROFILE, 'tools', 'openbuild')
sys.path[:0] = [EMU, OPENBUILD, os.path.join(PROFILE, 'tools'), os.path.join(PROFILE, 'testfw'), os.path.join(EXHUME, 'tools')]

UNICORN = all(importlib.util.find_spec(m) for m in ('unicorn', 'capstone'))
OPEN_TOOLS = all(shutil.which(t) for t in ('clang', 'arm-none-eabi-ld', 'arm-none-eabi-objcopy', 'arm-none-eabi-objdump'))
ARMCC_HOME = os.environ.get('EXHUME_ARMCC_HOME')


def armcc_available():
    if not ARMCC_HOME: return False
    if os.environ.get('EXHUME_ARMCC_RUNNER') == 'wine': return bool(shutil.which('wine'))
    return bool(shutil.which('docker')) and subprocess.run(['docker', 'info'], capture_output=True).returncode == 0


ARMCC = armcc_available()

CONFIG = '''
[project]
name = "emufw"
[toolchain]
profile = "armcc5"
[binary]
image = "target/emufw.hex"
[openbuild]
extra = ["boot_gnu.S"]
flash = [0x08000000, 0x8000]
ram = [0x20000000, 0x2000]
entry = "Reset_Handler"
'''


def make_root(prefix):
    """A throwaway project root with the emulator firmware's sources and an exhume.toml. Under the
    home directory, which the armcc container mounts."""
    import config, emufw
    root = tempfile.mkdtemp(dir=os.path.expanduser('~'), prefix=prefix)
    emufw.stage(root)
    with open(os.path.join(root, 'exhume.toml'), 'w') as f: f.write(textwrap.dedent(CONFIG))
    return root, config.Config(os.path.join(root, 'exhume.toml'))


def open_build(cfg, out, **over):
    import openbuild
    return openbuild.build(cfg, dict(over, out=out), log=lambda s: None)


def ram_word(m, name, fmt='<I'):
    return m.peek(m.sym(name), fmt)


def crc16_reference(data):
    """testfw/src/crc.c's CRC-16 (reflected 0xA001, from 0xFFFF), written out bit by bit."""
    c = 0xFFFF
    for b in data:
        c ^= b
        for _ in range(8): c = (c >> 1) ^ 0xA001 if c & 1 else c >> 1
    return c


def boot_system(image, syms, seed=None, flash=(0x08000000, 0x10000), ram=(0x20000000, 0x5000)):
    """The system firmware booted with the STM32F1 models and the example I2C device, the device
    holding 0x41, 0x42 in registers 2 and 3. Returns (machine, device)."""
    import sysemu, stm32f1, example_device
    m = sysemu.Machine(image, syms, flash=flash, ram=ram)
    stm32f1.add_all(m, flash_kb=32)
    dev = example_device.attach(m, {'example_device': {'address': 0x2A}})
    dev.regs[2], dev.regs[3] = 0x41, 0x42
    if seed is not None: m.fill_ram(seed)
    m.boot()
    return m, dev


class SystemChecks:
    """What the system firmware must do in the emulator, whichever compiler built it. A test class
    mixes this in and sets self.image and self.syms."""
    def boot(self, seed=None):
        return boot_system(self.image, self.syms, seed)

    def test_boots_into_the_main_loop_with_interrupts(self):
        m, dev = self.boot(seed=1)
        self.assertTrue(m.run_ms(50), m.events)
        self.assertTrue(m.where().startswith('main'), m.where())
        self.assertEqual(ram_word(m, 'g_ticks'), 50)             # SysTick each millisecond
        self.assertEqual(ram_word(m, 'g_tim_updates'), 50)       # TIM2 at 10 kHz / 10

    def test_device_information_and_flash(self):
        m, dev = self.boot()
        m.run_ms(5)
        self.assertEqual(ram_word(m, 'g_flash_kb', '<H'), 32)
        self.assertEqual(ram_word(m, 'g_uid0'), 0x67452301)
        self.assertEqual(ram_word(m, 'g_saved', '<H'), 0x1234)
        ops = [o[0] for o in m.periph['FLASH'].ops]
        self.assertEqual(ops, ['erase', 'program'])

    def test_i2c_device_through_stubs(self):
        m, dev = self.boot()
        m.run_ms(5)
        # the firmware writes register 3, then reads registers 2 and 3
        self.assertEqual(dev.log, [('w', 3, b'\x77'), ('r', 2, b'\x41\x77')])
        self.assertEqual(bytes(m.mu.mem_read(m.sym('g_sensor'), 2)), b'\x41\x77')

    def test_uart_answers_a_line(self):
        for seed in (1, 2, 3):                                     # random power-on RAM each time
            m, dev = self.boot(seed)
            m.run_ms(10)
            u = m.periph['USART1']; u.rx(b'hello\r')
            m.run_ms(10)
            want = b'HELLO' + crc16_reference(b'HELLO').to_bytes(2, 'little')
            self.assertEqual(u.output(), want)
            times = [t for t, b in u.tx]
            self.assertAlmostEqual(times[-1] - times[0], 6 * u.byte_ms, places=6)   # paced at the baud rate

    def test_gpio(self):
        m, dev = self.boot()
        m.periph['GPIOA'].set_input(0, 1)
        m.run_ms(250)
        self.assertEqual(ram_word(m, 'g_button', '<B'), 1)
        pa5 = [(t, (odr >> 5) & 1) for t, odr in m.periph['GPIOA'].history]
        self.assertEqual(pa5[:2], [(100, 1), (200, 0)])
