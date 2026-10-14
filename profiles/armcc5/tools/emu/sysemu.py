"""A whole Cortex-M firmware in an emulator: from reset, through the startup code, into main and
its loop, with emulated time, interrupts and peripheral models (docs/firmware-testing.md, "The
system emulator").

    python3 profiles/armcc5/tools/emu/sysemu.py [--config PATH] [IMAGE] [--syms FILE] [--ms N]
                                                [--seed N] [--uart N:HEX] [--trace]

Library use:

    m = Machine(image, syms, flash=(0x08000000, 0x10000), ram=(0x20000000, 0x5000))
    stm32f1.add_all(m)                 # or m.add(...) model by model; plugins add their own
    m.fill_ram(seed)                   # power-on RAM: whatever it holds
    m.boot(); m.run_ms(100)
    m.periph['USART1'].rx(b'hello\\r'); m.run_ms(20); m.periph['USART1'].output()

Time: instr_per_ms instructions make one emulated millisecond. The count only sets how much
main-loop work fits in a millisecond; it is not a cycle model. Between milliseconds each
peripheral's tick() runs, then interrupts are delivered.

Interrupts: an exception is delivered by calling its handler from the vector table (VTOR) as a
function, on the interrupted stack, with LR set to a return address the emulator stops at; the
interrupted registers are saved and restored around it. A Cortex-M handler is an ordinary AAPCS
function, so this is what the hardware does, less the stacked frame and EXC_RETURN. Delivered:
SysTick, PendSV, and each IRQ that a model asserts or the NVIC's ISPR holds, enabled in the
NVIC, when PRIMASK is clear; lowest exception number first, no nesting, priorities ignored. A
level-triggered line is checked again after its handler, up to irq_repeat times a millisecond.

Peripherals: subclass Peripheral, give it a base and size, and add it. A read in its range calls
read(off, size): a value, or None for what the CPU last wrote there. A write calls
write(off, size, value) before memory is updated. tick() runs once a millisecond, irqs() returns
the IRQ numbers the model asserts. Everything not claimed by a model is plain memory. stub()
replaces a firmware function with Python, for a device the firmware drives at a level too fine to
model (a bit-banged bus, say: example_device.py).

Stops: an access to unmapped memory, an instruction Unicorn cannot run, or (with
on_reset='stop') a reset request or watchdog expiry end run_ms and are kept in m.events."""
import os, sys, struct, random, bisect, collections, importlib.util
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE]
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_MEM_READ, \
    UC_HOOK_MEM_WRITE, UC_HOOK_MEM_UNMAPPED, UC_HOOK_CODE
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, UC_ARM_REG_SP, \
    UC_ARM_REG_LR, UC_ARM_REG_PC, UC_ARM_REG_PRIMASK
import images

PERIPHS = [(0x40000000, 0x100000), (0x50000000, 0x100000)]   # APB, AHB; AHB2 (USB OTG on some parts)
SYSTEM = (0xE0000000, 0x100000)
SYSMEM = (0x1FFF0000, 0x10000)        # system memory: bootloader, device information, option bytes
SYSTICK, PENDSV = 15, 14


class Peripheral:
    """A memory-mapped device. name is how tests find it (m.periph[name])."""
    name, base, size = 'device', 0, 0x400

    def attach(self, m): self.m = m
    def reset(self): pass
    def read(self, off, size): return None
    def write(self, off, size, value): pass
    def tick(self): pass
    def irqs(self): return ()


class Scs(Peripheral):
    """The system control space: SysTick, the NVIC's enable and pending registers, and the SCB's
    ICSR (PendSV and SysTick set-pending), VTOR and AIRCR (SYSRESETREQ)."""
    name, base, size = 'SCS', 0xE000E000, 0x1000

    def reset(self):
        self.enabled = [0] * 8; self.pending = [0] * 8
        self.st_ctrl = self.st_load = 0; self.st_acc = 0.0; self.st_fired = False; self.countflag = 0
        self.vtor = None; self.pend_sv = self.pend_st = False

    def read(self, off, size):
        if off == 0x10: v = self.st_ctrl | (self.countflag << 16); self.countflag = 0; return v
        if off == 0x14: return self.st_load
        if 0x100 <= off < 0x120 or 0x180 <= off < 0x1A0: return self.enabled[(off & 0x1F) >> 2]
        if 0x200 <= off < 0x220 or 0x280 <= off < 0x2A0: return self.pending[(off & 0x1F) >> 2]
        if off == 0xD08: return self.vtor if self.vtor is not None else self.m.img.base
        return None

    def write(self, off, size, v):
        if off == 0x10: self.st_ctrl = v & 7
        elif off == 0x14: self.st_load = v & 0xFFFFFF
        elif 0x100 <= off < 0x120: self.enabled[(off - 0x100) >> 2] |= v
        elif 0x180 <= off < 0x1A0: self.enabled[(off - 0x180) >> 2] &= ~v
        elif 0x200 <= off < 0x220: self.pending[(off - 0x200) >> 2] |= v
        elif 0x280 <= off < 0x2A0: self.pending[(off - 0x280) >> 2] &= ~v
        elif off == 0xD04:
            if v & (1 << 28): self.pend_sv = True
            if v & (1 << 26): self.pend_st = True
        elif off == 0xD08: self.vtor = v & ~0x7F
        elif off == 0xD0C and v >> 16 == 0x05FA and v & 4: self.m.request_reset('SYSRESETREQ')

    def tick(self):
        """SysTick: at most one exception a millisecond, however short its period."""
        self.st_fired = False
        if not self.st_ctrl & 1: return
        hz = self.m.cpu_hz if self.st_ctrl & 4 else self.m.cpu_hz / 8
        self.st_acc += hz / 1000
        if self.st_acc >= self.st_load + 1:
            self.st_acc %= self.st_load + 1; self.countflag = 1
            self.st_fired = bool(self.st_ctrl & 2)

    def is_enabled(self, irq): return bool(self.enabled[irq >> 5] >> (irq & 31) & 1)
    def is_pending(self, irq): return bool(self.pending[irq >> 5] >> (irq & 31) & 1)
    def clear_pending(self, irq): self.pending[irq >> 5] &= ~(1 << (irq & 31))


class Machine:
    def __init__(self, image, syms=None, flash=(0x08000000, 0x80000), ram=(0x20000000, 0x10000),
                 instr_per_ms=30000, cpu_hz=72_000_000, irq_repeat=32, on_reset='reboot'):
        self.img = image if isinstance(image, images.Image) else images.load_image(image)
        self.syms = syms if isinstance(syms, images.Symbols) else (images.load_symbols(syms) if syms else images.Symbols())
        self.flash, self.ram = flash, ram
        self.instr_per_ms, self.cpu_hz, self.irq_repeat, self.on_reset = instr_per_ms, cpu_hz, irq_repeat, on_reset
        if not (flash[0] <= self.img.base and self.img.end <= flash[0] + flash[1] - 0x10):
            raise ValueError(f'the image ({self.img.base:#x}-{self.img.end:#x}) is not inside the flash')
        self.ret = flash[0] + flash[1] - 0x10           # handlers and stubs return here
        mu = self.mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        for base, size in [flash, ram, SYSTEM, SYSMEM] + PERIPHS: mu.mem_map(base, size)
        mu.mem_write(flash[0], b'\xff' * flash[1])
        mu.mem_write(self.img.base, self.img.data)
        mu.mem_write(self.ret, b'\x00\xbf\x00\xbf')
        self.devices, self._bases, self.periph = [], [], {}
        self.flash_hooks = []          # fn(addr, size, value) for CPU writes to the flash
        self.events, self.ms, self.in_irq = [], 0, None
        self.stopped = None
        for base, size in PERIPHS + [SYSTEM]:
            mu.hook_add(UC_HOOK_MEM_READ, self._on_read, begin=base, end=base + size - 1)
            mu.hook_add(UC_HOOK_MEM_WRITE, self._on_write, begin=base, end=base + size - 1)
        mu.hook_add(UC_HOOK_MEM_WRITE, self._on_flash, begin=flash[0], end=flash[0] + flash[1] - 1)
        mu.hook_add(UC_HOOK_MEM_UNMAPPED, self._on_unmapped)
        self.scs = self.add(Scs())

    # ---- models --------------------------------------------------------------------------------
    def add(self, dev):
        dev.attach(self); dev.reset()
        self.devices.append(dev); self.periph[dev.name] = dev
        if dev.size:
            i = bisect.bisect(self._bases, (dev.base,))
            self._bases.insert(i, (dev.base, dev.base + dev.size, dev))
        return dev

    def _device(self, addr):
        i = bisect.bisect(self._bases, (addr, 0xFFFFFFFF + 1)) - 1
        if i >= 0 and self._bases[i][0] <= addr < self._bases[i][1]: return self._bases[i][2]
        return None

    def _on_read(self, uc, access, addr, size, value, _):
        d = self._device(addr)
        if d is None: return
        v = d.read(addr - d.base, size)
        if v is not None: uc.mem_write(addr, (v & ((1 << 8 * size) - 1)).to_bytes(size, 'little'))

    def _on_write(self, uc, access, addr, size, value, _):
        d = self._device(addr)
        if d is not None: d.write(addr - d.base, size, value & ((1 << 8 * size) - 1))

    def _on_flash(self, uc, access, addr, size, value, _):
        for fn in self.flash_hooks: fn(addr, size, value & ((1 << 8 * size) - 1))

    def _on_unmapped(self, uc, access, addr, size, value, _):
        self.events.append(('fault', self.ms, addr, uc.reg_read(UC_ARM_REG_PC)))
        return False

    def stub(self, where, fn):
        """Replace the function at where (a name or an address) with fn(m, [r0..r3]) -> r0 or None.
        It returns to the caller as the function would."""
        a = self.syms.funcs[where][0] if isinstance(where, str) else where & ~1

        def hook(uc, addr, size, _):
            v = fn(self, [uc.reg_read(r) for r in (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)])
            if v is not None: uc.reg_write(UC_ARM_REG_R0, v & 0xFFFFFFFF)
            uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR))
        self.mu.hook_add(UC_HOOK_CODE, hook, begin=a, end=a)

    # ---- memory --------------------------------------------------------------------------------
    def peek(self, addr, fmt='<I'):
        return struct.unpack(fmt, self.mu.mem_read(addr, struct.calcsize(fmt)))[0]

    def poke(self, addr, value, fmt='<I'):
        self.mu.mem_write(addr, struct.pack(fmt, value))

    def sym(self, name):
        return (self.syms.data.get(name) or self.syms.funcs[name])[0]

    def fill_ram(self, seed):
        """RAM as at power-on: random bytes. Code that relies on RAM the startup never clears shows."""
        self.mu.mem_write(self.ram[0], random.Random(seed).randbytes(self.ram[1]))

    # ---- running -------------------------------------------------------------------------------
    def boot(self):
        """Reset: SP and PC from the vector table at the start of the image."""
        sp, pc = struct.unpack('<II', self.mu.mem_read(self.img.base, 8))
        self.mu.reg_write(UC_ARM_REG_SP, sp)
        try: self.mu.reg_write(UC_ARM_REG_PRIMASK, 0)
        except Exception: pass
        self.pc = pc
        for d in self.devices: d.reset()
        self.stopped = None

    def request_reset(self, why):
        self.events.append(('reset', self.ms, why))
        if self.on_reset == 'stop': self.stopped = why
        else: self._reboot = why

    def run_ms(self, n=1):
        """Run n milliseconds. False when the run stopped (m.stopped, m.events say why)."""
        for _ in range(n):
            if self.stopped: return False
            self._reboot = None
            try:
                self.mu.emu_start(self.pc | 1, 0xFFFFFFFF, count=self.instr_per_ms)
            except UcError as e:
                self.events.append(('error', self.ms, self.mu.reg_read(UC_ARM_REG_PC), str(e)))
                self.stopped = 'error'; return False
            self.pc = self.mu.reg_read(UC_ARM_REG_PC)
            self.ms += 1
            for d in self.devices: d.tick()
            self.interrupts()
            if self._reboot: self.boot()
            if self.stopped: return False
        return True

    def run_until(self, cond, max_ms):
        """Run a millisecond at a time until cond(m) holds; False if max_ms passed first."""
        for _ in range(max_ms):
            if cond(self): return True
            if not self.run_ms(1): return False
        return cond(self)

    def primask(self):
        try: return self.mu.reg_read(UC_ARM_REG_PRIMASK) & 1
        except Exception: return 0

    def vector(self, exc):
        return self.peek((self.scs.vtor if self.scs.vtor is not None else self.img.base) + 4 * exc)

    def call(self, addr, budget=2_000_000, args=()):
        """Run the function at addr to its return, from the current state (interrupts use this)."""
        mu = self.mu
        ctx = mu.context_save()
        sp = (mu.reg_read(UC_ARM_REG_SP) - 0x20) & ~7
        mu.reg_write(UC_ARM_REG_SP, sp)
        mu.reg_write(UC_ARM_REG_LR, self.ret | 1)
        for r, v in zip((UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3), args): mu.reg_write(r, v)
        try:
            mu.emu_start(addr | 1, self.ret, count=budget)
            r0 = mu.reg_read(UC_ARM_REG_R0)
            if mu.reg_read(UC_ARM_REG_PC) & ~1 != self.ret:
                self.events.append(('budget', self.ms, hex(addr))); r0 = None
        except UcError as e:
            self.events.append(('error', self.ms, mu.reg_read(UC_ARM_REG_PC), str(e))); r0 = None
        mu.context_restore(ctx)
        return r0

    def asserted(self):
        """Exception numbers that want delivery now."""
        out = set()
        if self.scs.st_fired or self.scs.pend_st: out.add(SYSTICK)
        if self.scs.pend_sv: out.add(PENDSV)
        for d in self.devices:
            for irq in d.irqs():
                if self.scs.is_enabled(irq): out.add(16 + irq)
        for irq in range(256):
            if self.scs.is_pending(irq) and self.scs.is_enabled(irq): out.add(16 + irq)
        return sorted(out)

    def interrupts(self):
        if self.primask(): return
        for _ in range(self.irq_repeat):
            todo = self.asserted()
            if not todo: return
            exc = todo[0]
            if exc == SYSTICK: self.scs.st_fired = self.scs.pend_st = False
            elif exc == PENDSV: self.scs.pend_sv = False
            else: self.scs.clear_pending(exc - 16)
            self.in_irq = exc
            self.call(self.vector(exc))
            self.in_irq = None
            if self.primask(): return

    def where(self, pc=None):
        pc = (self.pc if pc is None else pc) & ~1
        best = max(((a, n) for n, (a, z) in self.syms.funcs.items() if a <= pc), default=(0, '?'))
        return f'{best[1]}+{pc - best[0]:#x}'


def load_plugins(m, paths, options):
    """Each plugin is a Python file with attach(machine, options): it adds models or stubs."""
    for p in paths:
        spec = importlib.util.spec_from_file_location(os.path.splitext(os.path.basename(p))[0], p)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
        mod.attach(m, options)


def from_config(cfg, image=None, syms=None):
    """A Machine as [sysemu] describes it, with the chip's models and the plugins added."""
    s = cfg.raw.get('sysemu', {})
    rel = lambda p: p if os.path.isabs(p) else os.path.join(cfg.root, p)
    image = image or (rel(s['image']) if 'image' in s else cfg.image)
    syms = syms or (rel(s['symbols']) if 'symbols' in s else cfg.map)
    m = Machine(image, syms if syms and os.path.exists(syms) else None,
                flash=tuple(s.get('flash', (0x08000000, 0x80000))), ram=tuple(s.get('ram', (0x20000000, 0x10000))),
                instr_per_ms=s.get('instr_per_ms', 30000), cpu_hz=s.get('cpu_hz', 72_000_000),
                irq_repeat=s.get('irq_repeat', 32), on_reset=s.get('on_reset', 'reboot'))
    if s.get('chip', 'stm32f1') == 'stm32f1':
        import stm32f1
        stm32f1.add_all(m, uarts={int(k): v for k, v in s.get('uart_baud', {}).items()} or None,
                        flash_page=s.get('flash_page', 0x400), flash_kb=s.get('flash_kb'),
                        uid=bytes.fromhex(s['uid']) if 'uid' in s else None)
    load_plugins(m, [rel(p) for p in s.get('plugins', [])], s)
    if 'ram_fill' in s: m.fill_ram(s['ram_fill'])
    return m


def main(argv):
    import argparse
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE)))), 'tools'))
    import config, difftest
    cpath, argv = config.pop_config(argv)
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('image', nargs='?'); ap.add_argument('--syms')
    ap.add_argument('--ms', type=int, default=1000); ap.add_argument('--seed', type=int)
    ap.add_argument('--uart', action='append', default=[], help='N:HEX, bytes to receive on USARTn')
    ap.add_argument('--trace', action='store_true', help='print where the firmware is every 100 ms')
    o = ap.parse_args(argv)
    cfg = difftest.find_config(cpath)
    if cfg: m = from_config(cfg, o.image, o.syms)
    else:
        if not o.image: ap.error('no exhume.toml: name the image')
        import stm32f1
        m = Machine(o.image, o.syms); stm32f1.add_all(m)
    if o.seed is not None: m.fill_ram(o.seed)
    for u in o.uart:
        n, hx = u.split(':'); m.periph[f'USART{n}'].rx(bytes.fromhex(hx))
    m.boot()
    seen = collections.Counter()
    for t in range(o.ms):
        if not m.run_ms(1): break
        seen[m.where().split('+')[0]] += 1
        if o.trace and t % 100 == 0: print(f'{m.ms:6d} ms  at {m.where()}', flush=True)
    print(f'{m.ms} ms; most time in: ' + ', '.join(f'{n} {c}' for n, c in seen.most_common(6)))
    for d in m.devices:
        if hasattr(d, 'output') and d.output(): print(f'{d.name} sent: {d.output().hex(" ")}')
    for e in m.events[:20]: print('event', e)
    return 1 if m.stopped else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
