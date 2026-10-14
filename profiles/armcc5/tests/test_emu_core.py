"""The emulator tools without any compiler: hand-encoded Thumb code, the peripheral models and
the differential tester's comparisons. Needs unicorn and capstone (Exhume's .venv)."""
import os, struct, tempfile, unittest
from profiles.armcc5.tests.emusupport import UNICORN, EMU

if UNICORN:
    import images, difftest, sysemu, stm32f1, example_device

FLASH = 0x08000000


def thumb(*halfwords):
    return b''.join(struct.pack('<H', h) for h in halfwords)


def systick_image():
    """Reset: SysTick LOAD = 71999, CTRL = 7, then b . ; the handler counts at 0x20000000."""
    vec = [0x20001000, FLASH + 0x41] + [FLASH + 0x4B] * 13 + [FLASH + 0x4D]
    code = thumb(0x4805,             # 40 ldr r0, =0xE000E010
                 0x4906,             # 42 ldr r1, =71999
                 0x6041,             # 44 str r1, [r0, #4]
                 0x2107,             # 46 movs r1, #7
                 0x6001,             # 48 str r1, [r0]
                 0xE7FE,             # 4A b .
                 0x4804,             # 4C ldr r0, =0x20000000
                 0x6801,             # 4E ldr r1, [r0]
                 0x3101,             # 50 adds r1, #1
                 0x6001,             # 52 str r1, [r0]
                 0x4770,             # 54 bx lr
                 0xBF00)             # 56 nop
    lits = struct.pack('<III', 0xE000E010, 71999, 0x20000000)
    return images.Image(FLASH, struct.pack('<16I', *vec) + code + lits)


@unittest.skipUnless(UNICORN, 'needs unicorn and capstone')
class MachineCore(unittest.TestCase):
    def machine(self, **kw):
        m = sysemu.Machine(systick_image(), None, flash=(FLASH, 0x1000), ram=(0x20000000, 0x1000), **kw)
        stm32f1.add_all(m)
        return m

    def test_systick_from_reset(self):
        m = self.machine(); m.boot()
        self.assertTrue(m.run_ms(10))
        self.assertEqual(m.peek(0x20000000), 10)

    def test_systick_period_follows_load_and_clock(self):
        m = self.machine(cpu_hz=36_000_000); m.boot()         # LOAD 71999 at 36 MHz: 2 ms
        m.run_ms(10)
        self.assertEqual(m.peek(0x20000000), 5)

    def test_primask_holds_interrupts(self):
        m = self.machine(); m.boot(); m.run_ms(1)
        from unicorn.arm_const import UC_ARM_REG_PRIMASK
        m.mu.reg_write(UC_ARM_REG_PRIMASK, 1)
        m.run_ms(5)
        self.assertEqual(m.peek(0x20000000), 1)

    def test_power_on_ram_is_random_and_seeded(self):
        a = self.machine(); a.fill_ram(7)
        b = self.machine(); b.fill_ram(7)
        self.assertEqual(bytes(a.mu.mem_read(0x20000100, 64)), bytes(b.mu.mem_read(0x20000100, 64)))
        self.assertNotEqual(bytes(a.mu.mem_read(0x20000100, 64)), bytes(64))

    def test_unmapped_access_stops(self):
        m = self.machine()
        m.poke(FLASH + 0x40, 0x6800 | 0, '<H')       # ldr r0, [r0]
        m.poke(FLASH + 0x42, 0xE7FE, '<H')
        from unicorn.arm_const import UC_ARM_REG_R0
        m.boot(); m.mu.reg_write(UC_ARM_REG_R0, 0x90000000)
        self.assertFalse(m.run_ms(1))
        self.assertEqual(m.events[0][0], 'fault')

    def test_stub_replaces_a_function(self):
        m = self.machine(); m.boot()
        calls = []
        m.stub(FLASH + 0x4C, lambda m, r: calls.append(r[0]))   # the SysTick handler
        m.run_ms(3)
        self.assertEqual((len(calls), m.peek(0x20000000)), (3, 0))


@unittest.skipUnless(UNICORN, 'needs unicorn and capstone')
class Models(unittest.TestCase):
    def setUp(self):
        self.m = sysemu.Machine(systick_image(), None, flash=(FLASH, 0x1000), ram=(0x20000000, 0x1000))
        stm32f1.add_all(self.m, uarts={2: 9600})

    def test_rcc_ready_follows_on(self):
        rcc = self.m.periph['RCC']
        self.m.poke(rcc.base, 0x83 | 1 << 16 | 1 << 24)
        v = rcc.read(0, 4)
        self.assertTrue(v & 1 << 17 and v & 1 << 25)
        self.m.poke(rcc.base + 4, 2)
        self.assertEqual(rcc.read(4, 4) & 0xC, 8)

    def test_uart_receives_at_the_baud_rate(self):
        u = self.m.periph['USART2']                  # 9600 baud: about a byte a millisecond
        u.rx(b'abc')
        got = []
        for ms in range(5):
            self.m.ms = ms; u.tick()
            if u.read(0, 4) & 0x20: got.append(u.read(4, 4))
        self.assertEqual(bytes(got), b'abc')

    def test_uart_overrun_when_dr_is_not_read(self):
        u = self.m.periph['USART2']; u.rx(b'abcd')
        for ms in range(8): self.m.ms = ms; u.tick()
        self.assertGreater(u.overruns, 0)
        self.assertEqual(u.read(4, 4), ord('a'))

    def test_flash_lock_erase_program(self):
        f = self.m.periph['FLASH']
        f.write(0x10, 4, 2)
        self.assertEqual(f.cr & 2, 0)                # locked: ignored
        f.write(0x04, 4, 0x45670123); f.write(0x04, 4, 0xCDEF89AB)
        self.assertEqual(f.cr & 0x80, 0)
        self.m.poke(FLASH + 0x800, 0x12345678)
        f.write(0x14, 4, FLASH + 0x810); f.write(0x10, 4, 0x42)
        self.assertEqual(self.m.peek(FLASH + 0x800), 0xFFFFFFFF)
        f.write(0x10, 4, 1)
        f.on_flash_write(FLASH + 0x800, 2, 0x55AA)
        self.m.poke(FLASH + 0x800, 0x55AA, '<H')
        f.on_flash_write(FLASH + 0x800, 2, 0x1111)   # not erased
        self.assertTrue(f.sr & 4)
        self.assertEqual([o[0] for o in f.ops], ['erase', 'program', 'pgerr'])

    def test_gpio_bsrr_and_inputs(self):
        g = self.m.periph['GPIOC']
        g.write(0x10, 4, (1 << 3) | (1 << (16 + 4)))
        self.assertEqual(g.odr, 1 << 3)
        g.set_input(4, 1)
        self.assertEqual(g.read(0x08, 4), (1 << 3) | (1 << 4))

    def test_timer_update(self):
        t = self.m.periph['TIM3']
        for off, v in ((0x28, 71), (0x2C, 1999), (0x0C, 1), (0x00, 1)): self.m.poke(t.base + off, v)
        for _ in range(4): t.tick()                   # 72 MHz / 72 / 2000: every 2 ms
        self.assertEqual(t.updates, 2)
        self.assertEqual(t.irqs(), (stm32f1.IRQ['TIM3'],))
        t.write(0x10, 4, ~1 & 0xFFFF)
        self.assertEqual(t.irqs(), ())

    def test_adc_dma_scan(self):
        adc, dma = self.m.periph['ADC1'], self.m.periph['DMA1']
        adc.set(3, 1000); adc.set(5, 2000)
        self.m.poke(adc.base + 0x2C, 1 << 20)                      # two conversions
        self.m.poke(adc.base + 0x34, 3 | 5 << 5)
        self.m.poke(adc.base + 0x04, 0x100)                        # scan
        ch1 = dma.base + 0x08
        for off, v in ((4, 4), (8, adc.base + 0x4C), (12, 0x20000200), (0, 1 | 1 << 10)): self.m.poke(ch1 + off, v)
        dma.tick()
        self.assertEqual(struct.unpack('<4H', self.m.mu.mem_read(0x20000200, 8)), (1000, 2000, 1000, 2000))

    def test_watchdog_resets(self):
        w = self.m.periph['IWDG']
        w.write(4, 4, 0); w.write(8, 4, 99); w.write(0, 4, 0xCCCC)  # 100 * 4 / 40 kHz: 10 ms
        for ms in range(12): self.m.ms = ms; w.tick()
        self.assertEqual(w.expired, 1)
        self.assertEqual(self.m.events[-1][0], 'reset')

    def test_example_device(self):
        d = example_device.RegisterDevice(0x2A, pec=2); d.attach(self.m); d.reset()
        d.regs[5:7] = b'\x10\x20'
        d.start(); d.write(0x54); d.write(5); d.start(); d.write(0x55)
        got = [d.read(1), d.read(1), d.read(0)]
        self.assertEqual(got[:2], [0x10, 0x20])
        self.assertEqual(got[2], example_device.crc8([0x54, 5, 0x55, 0x10, 0x20]))
        self.assertEqual(d.write(0x40), 0)            # another address: no ACK


def add_sub_image():
    """f at +0: adds r0, r0, r1; bx lr.  g at +4: stores r0 at 0x20000010 through a literal."""
    code = thumb(0x1840, 0x4770,                      # f
                 0x4901, 0x6008, 0x4770, 0xBF00)      # g: ldr r1, =0x20000010; str r0, [r1]; bx lr
    return code + struct.pack('<I', 0x20000010)


@unittest.skipUnless(UNICORN, 'needs unicorn and capstone')
class DiffCore(unittest.TestCase):
    def build(self, code, extra_sym=None):
        s = images.Symbols()
        s.funcs = {'f': (FLASH, 4), 'g': (FLASH + 4, 12)}
        s.data = {'g_word': (0x20000010, 4)}
        if extra_sym: s.data.update(extra_sym)
        L = difftest.Layout(flash=(FLASH, 0x10000), ram=(0x20000000, 0x1000))
        return difftest.Build(images.Image(FLASH, code), s, L)

    def run_pair(self, a, b, fn, runs=20, defs=None):
        cmp = difftest.Comparer(a, b)
        ram = {n: v for n, v in a.syms.data.items()}
        return difftest.test_function(fn, a, b, cmp, defs or {}, ram, runs, 1)

    def test_same_code_is_the_same(self):
        a, b = self.build(add_sub_image()), self.build(add_sub_image())
        for fn in ('f', 'g'): self.assertTrue(self.run_pair(a, b, fn).same)

    def test_a_changed_instruction_is_caught(self):
        code = bytearray(add_sub_image()); code[0:2] = thumb(0x1A40)       # subs
        r = self.run_pair(self.build(add_sub_image()), self.build(bytes(code)), 'f')
        self.assertFalse(r.same); self.assertIn('r0', r.first[1])

    def test_a_ram_write_is_compared(self):
        code = bytearray(add_sub_image()); code[6:8] = thumb(0x7008)       # strb: one byte only
        r = self.run_pair(self.build(add_sub_image()), self.build(bytes(code)), 'g')
        self.assertFalse(r.same); self.assertIn('g_word', r.first[1])

    def test_return_width_from_the_c_definition(self):
        defs = {'f': ('unsigned char', 'unsigned char a, unsigned char b', 'x.c')}
        self.assertEqual(difftest.return_of(defs['f']), 8)
        self.assertEqual(difftest.params_of(defs['f']), [('i', 8), ('i', 8)])
        self.assertEqual(difftest.params_of(('void', 'const u8 *p, s16 n', 'x.c')), [('p', 32), ('s', 16)])

    def test_pointers_to_the_same_symbol_are_the_same(self):
        a, b = self.build(add_sub_image()), self.build(add_sub_image(), {'g_other': (0x20000100, 4)})
        b.syms = images.Symbols(); b.syms.funcs = dict(a.syms.funcs); b.syms.data = {'g_word': (0x20000020, 4)}
        b.__init__(b.img, b.syms, b.L)
        cmp = difftest.Comparer(a, b)
        self.assertTrue(cmp.same_value(0x20000012, 0x20000022))
        self.assertFalse(cmp.same_value(0x20000012, 0x20000023))

    def test_a_fault_in_one_build_only_is_a_difference(self):
        fa = {'events': [], 'r0': 0, 'mem': {}, 'scratch': b'', 'calls': []}
        fb = dict(fa, events=[('fault', 0, 0x90000000)])
        cmp = difftest.Comparer(self.build(add_sub_image()), self.build(add_sub_image()))
        self.assertIn('stopped early', cmp.compare(fa, fb))
        self.assertIsNone(cmp.compare(fb, fb))
        self.assertEqual(cmp.compare(dict(fa, events=[('budget',)]), fa), 'inconclusive')

    def test_symbols_from_map_and_tsv(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, 'functions.tsv'), 'w') as f: f.write('addr\tsize\tname\tkind\tfile\n08000100\t20\tfoo\tcode\t\n')
        with open(os.path.join(d, 'data.tsv'), 'w') as f: f.write('addr\tsize\tname\tkind\tfile\n20000004\t8\tg_x\tram\t\n')
        s = images.load_symbols(d)
        self.assertEqual((s.funcs['foo'], s.data['g_x']), ((0x08000100, 0x20), (0x20000004, 8)))
        p = os.path.join(d, 'b.tsv')
        with open(p, 'w') as f: f.write('name\taddr\tsize\nfoo\t08000201\t10\ng_x\t20000008\t4\n')
        s = images.load_symbols(p)
        self.assertEqual((s.funcs['foo'], s.data['g_x']), ((0x08000200, 0x10), (0x20000008, 4)))

    def test_c_definitions(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, 'a.c'), 'w') as f:
            f.write('/* x(int) { */\nstatic unsigned short calc(const unsigned char *p,\n    int n)\n{\n'
                    '    if (n) { return 1; }\n    return 0;\n}\nvoid z(void) { }\n')
        defs = images.c_definitions([d])
        self.assertEqual(sorted(defs), ['calc', 'z'])
        self.assertEqual(difftest.return_of(defs['calc']), 16)
        self.assertEqual(difftest.params_of(defs['calc']), [('p', 32), ('i', 32)])


if __name__ == '__main__':
    unittest.main()
