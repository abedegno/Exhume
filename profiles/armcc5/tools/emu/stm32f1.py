"""Peripheral models of an STM32F1-class microcontroller for sysemu.py, each as far as a firmware
needs it to start and run, not a datasheet's worth: what start-up code waits on, what a main
loop polls, and the data paths a test drives (UART bytes, GPIO inputs, ADC samples).

    add_all(m)    RCC, FLASH, GPIOA-G, USART1-3, UART4-5, TIM2-5, ADC1, DMA1, IWDG, device info

Register offsets and bits are the STM32F1 reference manual's (RM0008); a register-compatible
part runs against the same models."""
import collections, struct
from sysemu import Peripheral

IRQ = {'DMA1_Channel1': 11, 'ADC1_2': 18, 'TIM2': 28, 'TIM3': 29, 'TIM4': 30, 'USART1': 37, 'USART2': 38,
       'USART3': 39, 'TIM5': 50, 'UART4': 52, 'UART5': 53}
UARTS = {1: 0x40013800, 2: 0x40004400, 3: 0x40004800, 4: 0x40004C00, 5: 0x40005000}
TIMERS = {2: 0x40000000, 3: 0x40000400, 4: 0x40000800, 5: 0x40000C00}


class Rcc(Peripheral):
    """Clocks: each oscillator and the PLL is ready as soon as it is switched on, and the clock
    switch status follows the switch, so start-up code's wait loops end at once."""
    name, base, size = 'RCC', 0x40021000, 0x400
    READY = {0x00: ((1 << 0, 1 << 1), (1 << 16, 1 << 17), (1 << 24, 1 << 25), (1 << 26, 1 << 27), (1 << 28, 1 << 29)),
             0x20: ((1 << 0, 1 << 1),), 0x24: ((1 << 0, 1 << 1),)}       # CR, BDCR (LSE), CSR (LSI)

    def reset(self):
        self.m.poke(self.base, 0x83)                      # HSI on and ready

    def read(self, off, size):
        if off in self.READY:
            v = self.m.peek(self.base + off)
            for on, rdy in self.READY[off]: v = v | rdy if v & on else v & ~rdy
            return v
        if off == 0x04:                                   # CFGR: SWS = SW
            c = self.m.peek(self.base + 4); return (c & ~0xC) | ((c & 3) << 2)
        return None


class Usart(Peripheral):
    """A USART or UART. rx(bytes) queues bytes to receive: they arrive at the baud rate, one in
    DR at a time (RXNE). What the firmware writes to DR is kept with the time it would leave the
    line: output() the bytes, tx the (ms, byte) pairs. SR always reads TXE and TC, so a polling
    sender never waits; the TXE interrupt is paced at the baud rate. A byte arriving while DR is
    full is lost and counted (overruns)."""
    def __init__(self, n, base=None, irq=None, baud=115200):
        self.n, self.name = n, f'USART{n}' if n <= 3 else f'UART{n}'
        self.base, self.size = base or UARTS[n], 0x400
        self.irq = irq if irq is not None else IRQ[self.name]
        self.baud = baud
        self.rxq, self.tx = collections.deque(), []

    def reset(self):
        self.dr_full = False; self.dr = 0; self.cr1 = 0
        self.rx_credit = 0.0; self.free_at = 0.0; self.overruns = 0; self.full_since = 0

    @property
    def byte_ms(self): return 10_000 / self.baud          # 8N1: ten bits a byte

    def rx(self, data): self.rxq.extend(data)
    def output(self): return bytes(b for _, b in self.tx)

    def _feed(self):
        """The next byte into an empty DR, when a byte time has passed since the last."""
        if self.rxq and not self.dr_full and self.rx_credit >= 1:
            self.rx_credit -= 1
            self.dr, self.dr_full, self.full_since = self.rxq.popleft(), True, self.m.ms

    def read(self, off, size):
        if off == 0x00:
            self._feed()
            return 0xC0 | (0x20 if self.dr_full else 0)
        if off == 0x04:
            v = self.dr; self.dr_full = False; return v
        return None

    def write(self, off, size, v):
        if off == 0x04:
            t = max(self.free_at, float(self.m.ms))
            self.tx.append((t, v & 0xFF)); self.free_at = t + self.byte_ms
        elif off == 0x0C: self.cr1 = v

    def tick(self):
        self.rx_credit = min(self.rx_credit + 1 / self.byte_ms, 1 / self.byte_ms + 1)
        # DR unread for longer than a byte time while bytes keep arriving: the next one is lost
        if self.dr_full and self.rxq and (self.m.ms - self.full_since) >= max(1.0, self.byte_ms) + 1:
            self.rxq.popleft(); self.overruns += 1; self.full_since = self.m.ms
        self._feed()

    def irqs(self):
        self._feed()
        cr1 = self.m.peek(self.base + 0x0C)
        rxne = self.dr_full and cr1 & 0x20
        txe = cr1 & 0x80 and self.free_at < self.m.ms + 1
        tc = cr1 & 0x40 and self.free_at <= self.m.ms + 1
        return (self.irq,) if rxne or txe or tc else ()


class Gpio(Peripheral):
    """A GPIO port. set_input(pin, level) drives a pin from outside; IDR reads the driven pins'
    levels and the output latch elsewhere. ODR, BSRR and BRR change the latch, and each change is
    kept in history as (ms, odr)."""
    def __init__(self, port):
        self.port = port.upper(); self.name = 'GPIO' + self.port
        self.base, self.size = 0x40010800 + 0x400 * (ord(self.port) - ord('A')), 0x400
        self.driven = self.inputs = 0

    def reset(self):
        self.odr = 0; self.history = []

    def set_input(self, pin, level):
        self.driven |= 1 << pin
        self.inputs = self.inputs | (1 << pin) if level else self.inputs & ~(1 << pin)

    def _set(self, odr):
        if odr != self.odr: self.odr = odr; self.history.append((self.m.ms, odr))

    def read(self, off, size):
        if off == 0x08: return (self.odr & ~self.driven) | (self.inputs & self.driven)
        if off == 0x0C: return self.odr
        return None

    def write(self, off, size, v):
        if off == 0x0C: self._set(v & 0xFFFF)
        elif off == 0x10: self._set((self.odr | (v & 0xFFFF)) & ~(v >> 16))
        elif off == 0x14: self._set(self.odr & ~(v & 0xFFFF))


class Flash(Peripheral):
    """The flash interface: locked at reset, unlocked by the key sequence; never busy. A page
    erase (PER, AR, STRT) or a mass erase (MER, STRT) erases the emulated flash. A CPU write to
    the flash while PG is set programs it; programming a halfword that is not erased sets PGERR,
    a write without PG is an event. The firmware's flash operations are kept in ops."""
    name, base, size = 'FLASH', 0x40022000, 0x400
    KEYS = (0x45670123, 0xCDEF89AB)

    def __init__(self, page=0x400):
        self.page = page

    def attach(self, m):
        super().attach(m); m.flash_hooks.append(self.on_flash_write)

    def reset(self):
        self.cr, self.sr, self.ar, self.keys, self.ops = 0x80, 0, 0, [], []

    def read(self, off, size):
        return {0x0C: self.sr, 0x10: self.cr, 0x14: self.ar}.get(off)

    def write(self, off, size, v):
        if off == 0x04:
            self.keys = (self.keys + [v])[-2:]
            if tuple(self.keys) == self.KEYS: self.cr &= ~0x80
        elif off == 0x0C: self.sr &= ~(v & 0x34)            # PGERR, WRPRTERR, EOP: write 1 to clear
        elif off == 0x14: self.ar = v
        elif off == 0x10:
            if self.cr & 0x80: self.cr |= v & 0x80; return   # locked: only LOCK can be written
            self.cr = v & 0x2F7
            if v & 0x40 and v & 2:                           # STRT with PER
                page = self.ar - self.ar % self.page
                if self.m.flash[0] <= page < self.m.flash[0] + self.m.flash[1]:
                    self.m.mu.mem_write(page, b'\xff' * self.page)
                self.ops.append(('erase', self.m.ms, page)); self.sr |= 0x20
            elif v & 0x40 and v & 4:                         # STRT with MER
                self.m.mu.mem_write(self.m.flash[0], b'\xff' * (self.m.flash[1] - 0x10))
                self.ops.append(('mass-erase', self.m.ms)); self.sr |= 0x20

    def on_flash_write(self, addr, size, value):
        if not self.cr & 1:
            self.m.events.append(('flash-write-without-PG', self.m.ms, addr)); return
        old = self.m.peek(addr & ~1, '<H')
        if old != 0xFFFF and value != 0:
            self.sr |= 4; self.ops.append(('pgerr', self.m.ms, addr))
        else:
            self.sr |= 0x20; self.ops.append(('program', self.m.ms, addr, value))


class Timer(Peripheral):
    """A general-purpose timer's update event: with CEN and the period from PSC and ARR, UIF is
    set and, with UIE, the IRQ asserted until the firmware clears UIF. At most one update a
    millisecond however short the period."""
    def __init__(self, n, clock_hz=None):
        self.n, self.name = n, f'TIM{n}'; self.base, self.size = TIMERS[n], 0x400
        self.irq, self.clock_hz = IRQ[self.name], clock_hz

    def reset(self):
        self.sr, self.acc, self.updates = 0, 0.0, 0

    def read(self, off, size): return self.sr if off == 0x10 else None

    def write(self, off, size, v):
        if off == 0x10: self.sr &= v                          # rc_w0

    def tick(self):
        cr1, psc, arr = (self.m.peek(self.base + o) & 0xFFFF for o in (0x00, 0x28, 0x2C))
        if not cr1 & 1: return
        period_ms = (psc + 1) * (arr + 1) * 1000 / (self.clock_hz or self.m.cpu_hz)
        self.acc += 1
        if self.acc >= period_ms:
            self.acc = self.acc - period_ms if period_ms >= 1 else 0
            self.sr |= 1; self.updates += 1

    def irqs(self):
        return (self.irq,) if self.sr & 1 and self.m.peek(self.base + 0x0C) & 1 else ()


class Adc(Peripheral):
    """ADC1: calibration and its reset finish at once, a conversion is always done (EOC), and DR
    reads the value set for the first channel of the regular sequence. set(channel, value);
    sequence() is the regular sequence's channels, which Dma uses for scan mode."""
    name, base, size = 'ADC1', 0x40012400, 0x400

    def __init__(self):
        self.values = collections.defaultdict(lambda: 2048)

    def set(self, channel, value): self.values[channel] = value & 0xFFF

    def sequence(self):
        sqr1, sqr2, sqr3 = (self.m.peek(self.base + o) for o in (0x2C, 0x30, 0x34))
        n = ((sqr1 >> 20) & 0xF) + 1
        chans = [(sqr3 >> 5 * i) & 0x1F for i in range(6)] + [(sqr2 >> 5 * i) & 0x1F for i in range(6)] + \
                [(sqr1 >> 5 * i) & 0x1F for i in range(4)]
        return chans[:n]

    def read(self, off, size):
        if off == 0x00: return self.m.peek(self.base) | 0x02
        if off == 0x08: return self.m.peek(self.base + 8) & ~0xC
        if off == 0x4C: return self.values[self.sequence()[0]]
        return None


class Dma(Peripheral):
    """DMA1, for peripheral-to-memory streams from ADC1: each millisecond an enabled channel whose
    CPAR is ADC1's DR gets CNDTR samples written at CMAR, in the regular sequence's order (scan
    mode), with TCIF set and the channel's IRQ asserted when TCIE is on."""
    name, base, size = 'DMA1', 0x40020000, 0x400

    def reset(self): self.isr = 0

    def read(self, off, size): return self.isr if off == 0 else None

    def write(self, off, size, v):
        if off == 4: self.isr &= ~v

    def tick(self):
        adc = self.m.periph.get('ADC1')
        for ch in range(1, 8):
            o = 0x08 + 0x14 * (ch - 1)
            ccr, cndtr, cpar, cmar = (self.m.peek(self.base + o + k) for k in (0, 4, 8, 12))
            if not ccr & 1 or not adc or cpar != adc.base + 0x4C: continue
            width = {0: 1, 1: 2, 2: 4}.get((ccr >> 10) & 3, 2)
            seq = adc.sequence() if self.m.peek(adc.base + 4) & 0x100 else adc.sequence()[:1]
            buf = b''.join(adc.values[seq[i % len(seq)]].to_bytes(width, 'little') for i in range(cndtr & 0xFFFF))
            self.m.mu.mem_write(cmar, buf)
            self.isr |= 0x3 << 4 * (ch - 1)

    def irqs(self):
        out = []
        for ch in range(1, 8):
            ccr = self.m.peek(self.base + 0x08 + 0x14 * (ch - 1))
            if ccr & 2 and self.isr >> (4 * (ch - 1) + 1) & 1: out.append(IRQ['DMA1_Channel1'] + ch - 1)
        return out


class Iwdg(Peripheral):
    """The independent watchdog: started by 0xCCCC, reloaded by 0xAAAA, timing out after
    (RLR + 1) * 4 << PR cycles of a 40 kHz LSI. Expiry is a reset (Machine.on_reset)."""
    name, base, size = 'IWDG', 0x40003000, 0x400

    def reset(self): self.running, self.left, self.pr, self.rlr, self.expired = False, 0.0, 0, 0xFFF, 0

    def timeout_ms(self): return (self.rlr + 1) * (4 << self.pr) / 40.0

    def write(self, off, size, v):
        if off == 0:
            if v & 0xFFFF == 0xCCCC: self.running = True; self.left = self.timeout_ms()
            elif v & 0xFFFF == 0xAAAA: self.left = self.timeout_ms()
        elif off == 4: self.pr = v & 7
        elif off == 8: self.rlr = v & 0xFFF

    def read(self, off, size): return 0 if off == 0x0C else None      # SR: never updating

    def tick(self):
        if not self.running: return
        self.left -= 1
        if self.left <= 0:
            self.expired += 1; self.running = False; self.m.request_reset('watchdog')


def device_info(m, flash_kb=None, uid=None, idcode=0x20036410):
    """The device information block: flash size in KB at 0x1FFFF7E0, the 96-bit unique ID at
    0x1FFFF7E8 (an arbitrary one unless given), DBGMCU_IDCODE at 0xE0042000."""
    m.poke(0x1FFFF7E0, flash_kb or m.flash[1] // 1024, '<H')
    m.mu.mem_write(0x1FFFF7E8, uid or bytes.fromhex('0123456789abcdef00112233'))
    m.poke(0xE0042000, idcode)


def add_all(m, uarts=None, flash_page=0x400, flash_kb=None, uid=None):
    m.add(Rcc()); m.add(Flash(flash_page)); m.add(Adc()); m.add(Dma()); m.add(Iwdg())
    for p in 'ABCDEFG': m.add(Gpio(p))
    for n in UARTS: m.add(Usart(n, baud=(uarts or {}).get(n, 115200)))
    for n in TIMERS: m.add(Timer(n))
    device_info(m, flash_kb, uid)
    return m
