"""Differential tests: the same functions of two builds of a firmware, run side by side in an
emulator (Unicorn, Cortex-M) on the same random inputs, and what they do compared
(docs/firmware-testing.md, "Differential tests").

    python3 profiles/armcc5/tools/emu/difftest.py [--config PATH] [options] [FUNC ...]

    --a IMAGE          the reference (default [difftest] reference, else [binary] image)
    --a-syms FILE      its symbols: an ELF, a map directory or a TSV (default [difftest]
                       reference_syms, else [project] map)
    --b IMAGE          the candidate: any build of the source (default [difftest] candidate)
    --b-syms FILE      its symbols (default [difftest] candidate_syms, else the reference's,
                       right only for a build at the same addresses)
    --src DIR          where the C definitions are read (default [project] src); repeatable
    --runs N           inputs per function (default [difftest] runs, else 200)
    --seed S           first seed (default 1)
    --mode deep|unit   deep (default): callees run too, so inlining does not matter. unit: calls
                       to other functions are stubbed and the calls (callee, r0-r3) compared
    --budget N         instructions per run (default [difftest] budget, else 300000)
    --init-rw          start initialised data whose initial bytes differ between the builds
                       (pointers, function tables) from each build's own values
    --mutate FUNC=OFF:BYTE   self-test: patch one byte of B at FUNC+OFF, to see it caught
    --list             print the functions that would be tested and stop

An image is a HEX, raw or ELF file (images.load_image). Symbols are matched by name, so the
candidate may be laid out anywhere. Each run: RAM gets the same random bytes in both builds, each
data symbol's bytes at that symbol's address in each build (half the time small values, which
reach thresholds and states more often); r0-r3 get the arguments, from the C definition (a
pointer points into a scratch region at the same address in both); every peripheral read returns
a value drawn from the seed, the register's address and how often it has been read. The function
runs to its return or the budget. Compared: r0 (as wide as the C return type), every RAM symbol's
bytes, the scratch region, the sequence of peripheral writes, and in unit mode the calls. A value
that points into code or data counts as the same when it points to the same symbol and offset in
each build, or into read-only data holding the same bytes. A run cut short counts only when both
builds fault at the same place. Exit status 1 when any function differs."""
import os, re, sys, hashlib, argparse, random, collections, bisect
HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE_TOOLS = os.path.dirname(HERE)
EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(PROFILE_TOOLS)))
sys.path[:0] = [HERE, PROFILE_TOOLS, os.path.join(EXHUME, 'tools')]
from unicorn import Uc, UcError, UC_ARCH_ARM, UC_MODE_THUMB, UC_MODE_MCLASS, UC_HOOK_MEM_READ, \
    UC_HOOK_MEM_WRITE, UC_HOOK_MEM_UNMAPPED, UC_HOOK_BLOCK
from unicorn.arm_const import UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3, \
    UC_ARM_REG_SP, UC_ARM_REG_LR, UC_ARM_REG_PC
import images

ARGREGS = (UC_ARM_REG_R0, UC_ARM_REG_R1, UC_ARM_REG_R2, UC_ARM_REG_R3)


class Layout:
    """The address space both builds run in. Every region but the flash is filled per run."""
    def __init__(self, flash=(0x08000000, 0x80000), ram=(0x20000000, 0x10000), scratch=(0x30000000, 0x10000),
                 periph=((0x40000000, 0x100000), (0x50000000, 0x100000)), system=(0xE0000000, 0x100000),
                 sysmem=(0x1FFF0000, 0x10000)):
        self.flash, self.ram, self.scratch, self.system, self.sysmem = flash, ram, scratch, system, sysmem
        self.periph = [tuple(p) for p in periph]          # every peripheral read is random, every write kept
        self.ret = flash[0] + flash[1] - 0x10          # LR at entry: the run ends when PC gets here

    @classmethod
    def from_config(cls, c):
        kw = {k: tuple(c[k]) for k in ('flash', 'ram', 'scratch') if k in c}
        if 'periph' in c: kw['periph'] = [tuple(p) for p in c['periph']]
        return cls(**kw)

    def in_flash(self, v): return self.flash[0] <= v < self.flash[0] + self.flash[1]
    def in_ram(self, v): return self.ram[0] <= v < self.ram[0] + self.ram[1]


def hval(*parts):
    return int.from_bytes(hashlib.blake2b(repr(parts).encode(), digest_size=8).digest(), 'little')


class Build:
    """One build: its image, symbols and initial RAM, and how to run one function on one input."""
    def __init__(self, img, syms, layout, mode='deep', budget=300000, rwinit=None, mutate=None):
        self.img, self.syms, self.L, self.mode, self.budget = img, syms, layout, mode, budget
        self.rwinit, self.mutate = rwinit or {}, mutate
        spans = sorted([(a, a + max(z, 1), n) for n, (a, z) in syms.data.items()] +
                       [(a, a + max(z, 2), n) for n, (a, z) in syms.funcs.items()])
        self._starts, self._spans = [x[0] for x in spans], spans
        self.byaddr = {a: n for n, (a, s) in syms.funcs.items()}

    def symbolize(self, v):
        """(symbol, offset) when v points into a known symbol in flash or RAM, else None."""
        if not (self.L.in_flash(v) or self.L.in_ram(v)): return None
        i = bisect.bisect_right(self._starts, v & ~1) - 1
        if i >= 0 and self._spans[i][0] <= (v & ~1) < self._spans[i][1]:
            return (self._spans[i][2], v - self._spans[i][0])
        return None

    def rodata(self, v, n=64):
        """The bytes at v in this build's flash, up to and including a 0 (a string), at most n."""
        if not self.img.has(v): return None
        b = self.img.bytes(v, min(n, self.img.end - v))
        z = b.find(b'\0')
        return b[:z + 1] if z >= 0 else b[:16]

    def _machine(self):
        """One emulator per build, made once: mapping 512 KB regions and adding hooks for every run
        costs more than the runs. Each run rewrites everything a run can change."""
        L = self.L
        mu = self.mu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
        for base, size in [L.flash, L.ram, L.scratch, L.system, L.sysmem] + L.periph:
            mu.mem_map(base, size)
        flash = bytearray(b'\xff' * L.flash[1])
        flash[self.img.base - L.flash[0]:self.img.end - L.flash[0]] = self.img.data
        if self.mutate: flash[self.mutate[0] - L.flash[0]] = self.mutate[1]
        flash[L.ret - L.flash[0]:L.ret - L.flash[0] + 4] = b'\x00\xbf\x00\xbf'      # nops: somewhere to stop
        self.flash_bytes = bytes(flash)
        self.ctx0 = mu.context_save()

        def on_block(uc, addr, size, _):
            c = self.cur
            if c['start'] <= addr < c['end']: self.blocks.append((addr, size))
            if self.mode == 'unit' and addr != c['start'] and addr in self.byaddr and not c['start'] <= addr < c['end']:
                callee = self.byaddr[addr]
                if not images.RUNTIME.match(callee):
                    self.calls.append((callee, tuple(uc.reg_read(r) for r in ARGREGS)))
                    uc.reg_write(UC_ARM_REG_R0, hval(c['seed'], callee, len(self.calls)) & 0xFFFF)
                    uc.reg_write(UC_ARM_REG_PC, uc.reg_read(UC_ARM_REG_LR) | 1)

        def on_read(uc, access, addr, size, value, _):
            n = self.reads[addr]; self.reads[addr] += 1
            v = hval(self.cur['seed'], 'mmio', addr, n) & ((1 << 8 * size) - 1)
            uc.mem_write(addr, v.to_bytes(size, 'little'))

        def on_write(uc, access, addr, size, value, _):
            self.events.append(('mmio', addr, size, value & ((1 << 8 * size) - 1)))

        def on_unmapped(uc, access, addr, size, value, _):
            self.events.append(('fault', access, addr)); return False

        mu.hook_add(UC_HOOK_BLOCK, on_block)
        for lo, n in L.periph + [L.system]:
            mu.hook_add(UC_HOOK_MEM_READ, on_read, begin=lo, end=lo + n - 1)
            mu.hook_add(UC_HOOK_MEM_WRITE, on_write, begin=lo, end=lo + n - 1)
        mu.hook_add(UC_HOOK_MEM_UNMAPPED, on_unmapped)
        return mu

    def go(self, fname, seed, args, fill):
        L = self.L
        mu = self.mu if hasattr(self, 'mu') else self._machine()
        mu.context_restore(self.ctx0)
        mu.mem_write(L.flash[0], self.flash_bytes)
        mu.mem_write(L.ram[0], fill['bulk'])
        for name, data in fill['syms'].items():
            if name in self.syms.data:
                a, s = self.syms.data[name]
                if L.in_ram(a): mu.mem_write(a, data[:s])
        for name, data in self.rwinit.items():
            mu.mem_write(self.syms.data[name][0], data)
        mu.mem_write(L.scratch[0], fill['scratch'])
        mu.mem_write(L.sysmem[0], fill['sysmem'])
        self.events, self.calls, self.reads, self.blocks = [], [], collections.Counter(), []
        entry = self.syms.funcs[fname][0]
        self.cur = {'seed': seed, 'start': entry, 'end': entry + self.syms.funcs[fname][1]}
        mu.reg_write(UC_ARM_REG_SP, L.ram[0] + L.ram[1] - 0x100)
        mu.reg_write(UC_ARM_REG_LR, L.ret | 1)
        for reg, v in zip(ARGREGS, args): mu.reg_write(reg, v)
        try:
            mu.emu_start(entry | 1, L.ret, count=self.budget)
        except UcError as e:
            self.events.append(('error', str(e)))
        if mu.reg_read(UC_ARM_REG_PC) & ~1 != L.ret and not any(e[0] in ('error', 'fault') for e in self.events):
            self.events.append(('budget',))
        mem = {name: bytes(mu.mem_read(a, s)) for name, (a, s) in self.syms.data.items() if L.in_ram(a) and s}
        return {'r0': mu.reg_read(UC_ARM_REG_R0), 'mem': mem, 'events': self.events, 'calls': self.calls,
                'scratch': bytes(mu.mem_read(L.scratch[0], L.scratch[1]))}


# ---- inputs ----------------------------------------------------------------------------------
_TYPES8 = r'\b(u8|s8|char|uint8_t|int8_t|bool|_Bool|BOOL|FunctionalState|FlagStatus|ITStatus)\b'
_TYPES16 = r'\b(u16|s16|short|uint16_t|int16_t)\b'


def return_of(defn):
    """'void', 8, 16 or 32: the bits of r0 that matter, from the C definition (32 if unknown)."""
    if not defn: return 32
    head = re.sub(r'\b(static|extern|inline|__inline|__forceinline|__irq|__weak|const|volatile)\b', ' ', defn[0]).strip()
    if '*' in head: return 32
    if re.fullmatch(r'void', head): return 'void'
    if re.search(_TYPES8, head): return 8
    if re.search(_TYPES16, head): return 16
    return 32


def params_of(defn):
    """[(kind, bits)]: 'p' pointer, 'i' unsigned, 's' signed (sign-extended, as AAPCS callers do).
    None when the definition is unknown: then four random words."""
    if not defn: return None
    ps = defn[1]
    if ps in ('', 'void'): return []
    out = []
    for p in ps.split(','):
        p = p.strip()
        if '*' in p or '[' in p: out.append(('p', 32))
        elif re.search(r'\b(s8|int8_t|signed\s+char)\b', p): out.append(('s', 8))
        elif re.search(_TYPES8, p): out.append(('i', 8))
        elif re.search(r'\b(s16|int16_t)\b', p) or (re.search(r'\bshort\b', p) and 'unsigned' not in p): out.append(('s', 16))
        elif re.search(_TYPES16, p): out.append(('i', 16))
        else: out.append(('i', 32))
    return out[:4]


INTERESTING = [0, 1, 2, 3, 10, 100, 1000, 0x7F, 0x80, 0xFF, 0x7FFF, 0x8000, 0xFFFF, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF]


def make_input(seed, params, ramsyms, layout):
    rnd = random.Random(seed)
    args = []
    for kind, bits in (params if params is not None else [('i', 32)] * 4):
        if kind == 'p':
            args.append(layout.scratch[0] + 0x100 * rnd.randrange(layout.scratch[1] // 0x200))
            continue
        v = (rnd.choice(INTERESTING) if rnd.random() < 0.3 else rnd.getrandbits(bits)) & ((1 << bits) - 1)
        if kind == 's' and v >> (bits - 1): v |= (0xFFFFFFFF << bits) & 0xFFFFFFFF
        args.append(v)
    args += [0] * (4 - len(args))
    syms = {}
    for name, (a, s) in sorted(ramsyms.items()):
        b = bytearray(rnd.randbytes(s))
        if rnd.random() < 0.5:
            for i in range(s):
                if rnd.random() < 0.7: b[i] = rnd.choice((0, 0, 1, 2, 3, 0xFF)) if rnd.random() < 0.5 else b[i] & 0x0F
        syms[name] = bytes(b)
    return args, {'bulk': rnd.randbytes(layout.ram[1]), 'syms': syms,
                  'scratch': rnd.randbytes(layout.scratch[1]), 'sysmem': rnd.randbytes(layout.sysmem[1])}


# ---- comparing -------------------------------------------------------------------------------
def stopped(f):
    """How a run ended early: ('fault', address), ('error',), ('budget',), or None."""
    for e in f['events']:
        if e[0] == 'fault': return ('fault', e[2])
        if e[0] in ('error', 'budget'): return (e[0],)
    return None


class Comparer:
    def __init__(self, a, b, mode='deep'):
        self.a, self.b, self.mode = a, b, mode
        self.relocated = a.syms is not b.syms

    def same_value(self, x, y):
        """Two 32-bit values: equal, or (builds laid out differently) pointers to the same symbol
        and offset, or to read-only data holding the same bytes."""
        if x == y: return True
        if not self.relocated: return False
        sa, sb = self.a.symbolize(x), self.b.symbolize(y)
        if sa is not None and sa == sb: return True
        if self.a.L.in_flash(x) and self.b.L.in_flash(y):
            da, db = self.a.rodata(x), self.b.rodata(y)
            return da is not None and len(da) > 1 and da == db
        return False

    def same_bytes(self, x, y):
        if x == y: return True
        n = min(len(x), len(y)); i = 0
        while i < n:
            if x[i] != y[i]:
                k = i & ~3
                if k + 4 <= n and self.same_value(int.from_bytes(x[k:k + 4], 'little'), int.from_bytes(y[k:k + 4], 'little')):
                    i = k + 4; continue
                return False
            i += 1
        return True

    def compare(self, fa, fb, ret=32, ia=None, ib=None):
        """None (the same), 'inconclusive', or the first difference as text."""
        sa, sb = stopped(fa), stopped(fb)
        if sa or sb:
            # the builds take different numbers of instructions, so a run cut short stops at a
            # different point in each: only a fault at the same address in both is the same
            if sa and sb and sa[0] == 'fault' and sa == sb: return None
            if sa == ('budget',) or sb == ('budget',) or (sa and sb): return 'inconclusive'
            return f'one build stopped early: {sa or sb}'
        if ret != 'void':
            mask = {8: 0xFF, 16: 0xFFFF}.get(ret, 0xFFFFFFFF)
            x, y = fa['r0'] & mask, fb['r0'] & mask
            if x != y and not (mask == 0xFFFFFFFF and self.same_value(x, y)):
                return f'r0 {x:#x} != {y:#x}'
        ia, ib = ia or {}, ib or {}
        for name, x in fa['mem'].items():
            if name not in fb['mem']: continue
            if name in ia and x == ia[name] and fb['mem'][name] == ib.get(name):
                continue            # initialised, untouched by both: its pointers may differ by design
            y = fb['mem'][name]; n = min(len(x), len(y))     # the builds may size a symbol differently
            if not self.same_bytes(x[:n], y[:n]):
                i = next(k for k in range(n) if x[k] != y[k])
                return f'{name}+{i:#x}: {x[i]:#04x} != {y[i]:#04x}'
        if not self.same_bytes(fa['scratch'], fb['scratch']):
            i = next(k for k in range(len(fa['scratch'])) if fa['scratch'][k] != fb['scratch'][k])
            return f'scratch+{i:#x} (written through a pointer argument) differs'
        ea = [e for e in fa['events'] if e[0] == 'mmio']; eb = [e for e in fb['events'] if e[0] == 'mmio']
        for k, (x, y) in enumerate(zip(ea, eb)):
            if x[:3] != y[:3] or not self.same_value(x[3], y[3]): return f'peripheral write {k}: {_ev(x)} != {_ev(y)}'
        if len(ea) != len(eb): return f'{len(ea)} peripheral writes != {len(eb)}'
        if self.mode == 'unit':
            for k, (x, y) in enumerate(zip(fa['calls'], fb['calls'])):
                if x[0] != y[0] or not all(self.same_value(p, q) for p, q in zip(x[1], y[1])):
                    return f'call {k}: {x} != {y}'
            if len(fa['calls']) != len(fb['calls']): return f'{len(fa["calls"])} calls != {len(fb["calls"])}'
        return None


def _ev(e):
    return f'[{e[1]:#010x}]{e[2] * 8}={e[3]:#x}'


# ---- running ---------------------------------------------------------------------------------
class Result:
    def __init__(self, name, first, coverage, cut):
        self.name, self.first, self.coverage, self.cut = name, first, coverage, cut

    @property
    def same(self): return self.first is None

    def line(self, runs):
        extra = f'  [{self.cut} of {runs} runs cut short]' if self.cut else ''
        diff = f'  seed {self.first[0]}: {self.first[1]}' if self.first else ''
        return f'{"same" if self.same else "DIFF"} {self.name:32} coverage {self.coverage:5.1%}{extra}{diff}'


def test_function(fn, A, B, cmp, defs, ramsyms, runs, seed, ia=None, ib=None):
    defn = defs.get(fn); params, ret = params_of(defn), return_of(defn)
    covered, first, cut = set(), None, 0
    for k in range(runs):
        s = seed + k
        args, fill = make_input(hval(fn, s), params, ramsyms, A.L)
        fa = A.go(fn, s, args, fill)
        covered |= A.img.block_instructions(A.blocks)
        fb = B.go(fn, s, args, fill)
        d = cmp.compare(fa, fb, ret, ia, ib)
        if d == 'inconclusive': cut += 1
        elif d and not first: first = (s, d)
    a, n = A.syms.funcs[fn]
    return Result(fn, first, A.img.instructions_covered(a, n, covered), cut)


def default_functions(sa, sb, defs, kinds, exclude):
    """The functions in both builds with a C definition in the sources (all common functions when
    no sources were read), leaving out the runtime, library and assembly rows of the map."""
    out = []
    for n in sorted(sa.funcs, key=lambda n: sa.funcs[n][0]):
        if n not in sb.funcs or images.RUNTIME.match(n): continue
        if kinds.get(n) in ('lib', 'asm'): continue
        if defs and n not in defs: continue
        if any(re.search(x, n) for x in exclude): continue
        out.append(n)
    return out


def initial_differences(pa, sa, pb, sb, layout):
    """{name: bytes} for each build: the initialised RAM symbols whose initial bytes differ between
    the builds (pointers and function tables, which a relocated build changes by design)."""
    ra, rb = images.ram_init(pa, layout.ram), images.ram_init(pb, layout.ram)
    ia, ib = {}, {}
    for n, (a, z) in sa.data.items():
        if n not in sb.data or not layout.in_ram(a) or not z: continue
        x = images.init_bytes(ra, a, z); y = images.init_bytes(rb, sb.data[n][0], sb.data[n][1])
        if x is not None and y is not None and x != y[:len(x)]: ia[n], ib[n] = x, y
    return ia, ib


def find_config(path):
    import config
    if path or os.environ.get('EXHUME_CONFIG'): return config.load(path)
    d = os.getcwd()
    while True:
        if os.path.exists(os.path.join(d, 'exhume.toml')): return config.load(os.path.join(d, 'exhume.toml'))
        if os.path.dirname(d) == d: return None
        d = os.path.dirname(d)


def main(argv, out=print):
    import config
    cpath, argv = config.pop_config(argv)
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('funcs', nargs='*')
    for k in ('--a', '--a-syms', '--b', '--b-syms', '--mutate'): ap.add_argument(k)
    ap.add_argument('--src', action='append')
    ap.add_argument('--runs', type=int); ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--mode', choices=('deep', 'unit'), default='deep')
    ap.add_argument('--budget', type=int)
    ap.add_argument('--init-rw', action='store_true'); ap.add_argument('--list', action='store_true')
    o = ap.parse_args(argv)
    cfg = find_config(cpath)
    dt = cfg.raw.get('difftest', {}) if cfg else {}
    rel = lambda p: os.path.join(cfg.root, p) if cfg and p and not os.path.isabs(p) else p
    pa = o.a or rel(dt.get('reference')) or (cfg.image if cfg else None)
    pb = o.b or rel(dt.get('candidate'))
    if not pa or not pb: ap.error('need --a and --b, or [difftest] reference and candidate in exhume.toml')
    sya = o.a_syms or rel(dt.get('reference_syms')) or (cfg.map if cfg else None)
    syb = o.b_syms or rel(dt.get('candidate_syms')) or sya
    srcs = o.src or ([rel(s) for s in dt['src']] if 'src' in dt else ([cfg.src] if cfg else []))
    layout = Layout.from_config(dt)
    runs, budget = o.runs or dt.get('runs', 200), o.budget or dt.get('budget', 300000)
    sa = images.load_symbols(sya); sb = sa if syb == sya else images.load_symbols(syb)
    kinds = images.map_kinds(sya) if os.path.isdir(sya) else {}
    defs = images.c_definitions(srcs)
    funcs = o.funcs or default_functions(sa, sb, defs, kinds, dt.get('exclude', []))
    if o.list:
        for f in funcs: out(f)
        return 0
    missing = [f for f in funcs if f not in sa.funcs or f not in sb.funcs]
    if missing: ap.error('not in both builds: ' + ', '.join(missing))
    mutate = None
    if o.mutate:
        fn, rest = o.mutate.split('='); off, byte = rest.split(':')
        mutate = (sb.funcs[fn][0] + int(off, 0), int(byte, 0))
    ia = ib = {}
    if o.init_rw or dt.get('init_rw'):
        ia, ib = initial_differences(pa, sa, pb, sb, layout)
    A = Build(images.load_image(pa), sa, layout, o.mode, budget, ia)
    B = Build(images.load_image(pb), sb, layout, o.mode, budget, ib, mutate)
    cmp = Comparer(A, B, o.mode)
    ramsyms = {n: v for n, v in sa.data.items() if layout.in_ram(v[0]) and v[1]}
    results = []
    for fn in funcs:
        r = test_function(fn, A, B, cmp, defs, ramsyms, runs, o.seed, ia, ib)
        results.append(r); out(r.line(runs))
    bad = sum(not r.same for r in results)
    cov = sum(r.coverage for r in results) / max(1, len(results))
    out(f'{len(results) - bad} of {len(results)} functions behave the same; mean coverage {cov:.1%}')
    main.results = results
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
