r"""Differential fuzzing of single routines: the original's bytes against the port's C
(docs/port.md, "Fuzzing single routines").

    python3 tools/fuzzasm.py [--config PATH] [--quick | --deep] [--seed N] [--only NAME[,NAME...]] [--list]
                             [--coverage] [-v]

The replay sessions test the routines the program reaches in them. This tests routines one at a
time, on inputs no session gives them: for each target ([fuzz] targets, a Python file of T()
definitions; UW2's is examples/uw2/port/fuzz_targets.py) it runs the routine's own bytes (from
the user's EXE, which the gate proves the matched sources build byte for byte) in an x86
emulator, Unicorn, and the port's code for the same routine (its translation by
tools/asm2c.py, or its hand-written C through the glue, or a C entry) in tools/fuzzhost.c, on
the same registers and memory, and compares what each leaves: the registers, the flags the
routine's callers read, and every byte of memory it changed. The inputs are random (seeded,
so a run is reproducible: the seed is printed, and --seed repeats it) and edge cases (0, 1,
-1, 7FFFh, 8000h, FFFFh, the boundaries each routine has). --quick (the default, a few
seconds) runs a few hundred cases of each target, --deep tens of thousands with a new seed.
A difference is reported with its inputs, and the run exits 1.

Both sides start each case from the same memory: the EXE's image loaded at the port's load
segment (LOAD, the targets file's, default 800h, the port's PORT_LOAD_SEG), so segment values
are the same on both sides, with two scratch segments, E000 for data and F000 for the stack.
A divide that faults stops both (Unicorn takes the interrupt; the port stops where DOS would
have run the handler), and that counts as agreement. Memory below the stack pointer at the
end is not compared: it is what the routine pushed and popped, which hand-written C does not
reproduce. Memory the original changes outside the targets file's REGIONS (the memory the
port has) is reported.

Unicorn 2.1.4's emu_start, in 16-bit mode, takes a linear address and sets IP to it minus
CS's base, modulo 64 KB: pass the linear address (CS * 16 + IP), as this does. A caller that
passes the offset alone works only while CS's base is 0 modulo 64 KB (tools/ailcheck.py's
driver segment, 1000h).

The targets file imports this module (import fuzzasm) for T, the input helpers (base_regs,
setw, word, EDGE16) and the constants, and defines TARGETS (a list of T), REGIONS (the
[lo, hi) linear ranges the port has) and any constants it changes (LOAD, SCRATCH, STACK, MEMTOP).

Needs Unicorn (pip install unicorn==2.1.4; tools/requirements.txt) and the port's objects
(tools/portbuild.py; --coverage uses the coverage build and writes a .profraw for
tools/coverage.py).
"""
import os, sys, re, time, zlib, random, struct, argparse, subprocess

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
EXHUME = os.path.dirname(here)
# the targets file imports this module by name, also when it runs as a script
sys.modules.setdefault('fuzzasm', sys.modules[__name__])
try:
    from unicorn import Uc, UcError, UC_ARCH_X86, UC_MODE_16, UC_HOOK_INTR, UC_HOOK_INSN
    from unicorn import x86_const as X
except ImportError:
    sys.exit('fuzzasm.py: needs Unicorn (.venv/bin/pip install unicorn==2.1.4)')
import portcfg

CFG, ARGV = portcfg.cli()
root = CFG.root
EXE = CFG.exe_path
LOAD = 0x800                           # the port's PORT_LOAD_SEG
SCRATCH, STACK = 0xE000, 0xF000        # fuzzhost's scratch segments
MEMTOP = 0x110000
RET_NEAR = 0xFFF0                      # a near call's return address (in the routine's CS)
RET_CS, RET_IP = 0x0010, 0x0000        # a far call's (linear 100h, never code)
# the regions the port has (the host glue's fuzz_regions): memory outside them that the original
# changes is memory the port cannot model, and is reported; the targets file sets them
REGIONS = []
TARGETS = []
REGS = ['eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp', 'esp', 'ds', 'es', 'ss', 'fs', 'gs', 'flags']
UREG = [X.UC_X86_REG_EAX, X.UC_X86_REG_EBX, X.UC_X86_REG_ECX, X.UC_X86_REG_EDX, X.UC_X86_REG_ESI,
        X.UC_X86_REG_EDI, X.UC_X86_REG_EBP, X.UC_X86_REG_ESP, X.UC_X86_REG_DS, X.UC_X86_REG_ES,
        X.UC_X86_REG_SS, X.UC_X86_REG_FS, X.UC_X86_REG_GS, X.UC_X86_REG_EFLAGS]
FLAGBITS = {'cf': 0, 'zf': 6, 'sf': 7, 'df': 10, 'of': 11}
ALLFLAGS = ('cf', 'zf', 'sf', 'of', 'df')
GPR = ('eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp', 'esp', 'ds', 'es', 'ss', 'fs', 'gs')


# ---- the two machines ------------------------------------------------------------------------

def load_image():
    """The EXE's load module at LOAD:0 with its relocations applied, as DOS loads it, in a 1 MB
    (and 64 KB) memory. The linear address is what Unicorn's 16-bit emu_start takes."""
    exe = open(EXE, 'rb').read()
    hdr = struct.unpack_from('<H', exe, 8)[0] * 16
    nrel, relat = struct.unpack_from('<H', exe, 6)[0], struct.unpack_from('<H', exe, 0x18)[0]
    img = bytearray(exe[hdr:])
    for i in range(nrel):
        off, seg = struct.unpack_from('<HH', exe, relat + 4 * i)
        lin = seg * 16 + off
        if lin + 2 <= len(img):
            struct.pack_into('<H', img, lin, (struct.unpack_from('<H', img, lin)[0] + LOAD) & 0xFFFF)
    mem = bytearray(MEMTOP)
    mem[LOAD * 16:LOAD * 16 + len(img)] = img
    return bytes(mem)


class Dos:
    """The original: Unicorn on the EXE's image."""

    def __init__(self, image=None):
        self.pristine = image or load_image()
        self.mu = Uc(UC_ARCH_X86, UC_MODE_16)
        self.mu.mem_map(0, MEMTOP)
        self.mu.mem_write(0, self.pristine)
        self.mu.hook_add(UC_HOOK_INTR, self.on_int)
        self.mu.hook_add(UC_HOOK_INSN, lambda uc, port, size, v, ud: None, None, 1, 0, X.UC_X86_INS_OUT)
        self.mu.hook_add(UC_HOOK_INSN, lambda uc, port, size, ud: 0, None, 1, 0, X.UC_X86_INS_IN)
        self.dirty = False

    def on_int(self, uc, n, ud):
        self.intr = n
        uc.emu_stop()

    def run(self, regs, mem, kind, seg, off):
        if getattr(self, 'fresh', False):
            # a machine stopped inside an interrupt does not always start cleanly again
            self.__init__(self.pristine)
        mu = self.mu
        if self.dirty: mu.mem_write(0, self.pristine)
        self.dirty = True
        for lin, b in mem: mu.mem_write(lin, bytes(b))
        r = dict(regs)
        ss, sp = r['ss'], r['esp'] & 0xFFFF
        if kind == 'far':
            sp = (sp - 4) & 0xFFFF
            mu.mem_write(ss * 16 + sp, struct.pack('<HH', RET_IP, RET_CS))
            stop = RET_CS * 16 + RET_IP
        else:
            sp = (sp - 2) & 0xFFFF
            mu.mem_write(ss * 16 + sp, struct.pack('<H', RET_NEAR))
            stop = (seg + LOAD) * 16 + RET_NEAR
        r['esp'] = (r['esp'] & 0xFFFF0000) | sp
        for k, u in zip(REGS, UREG):
            mu.reg_write(u, r[k] | (0x2 if k == 'flags' else 0))
        mu.reg_write(X.UC_X86_REG_CS, seg + LOAD)
        self.intr = None
        status = 'ret'
        try:
            mu.emu_start((seg + LOAD) * 16 + off, stop, count=50_000_000)
        except UcError as e:
            status = f'error {e}'
        if self.intr is not None:
            status = f'int {self.intr:X}'
            self.fresh = True
        elif status == 'ret' and (mu.reg_read(X.UC_X86_REG_CS) * 16 + mu.reg_read(X.UC_X86_REG_IP)) != stop:
            status = 'runaway'
        out = {k: mu.reg_read(u) for k, u in zip(REGS, UREG)}
        after = bytes(mu.mem_read(0, MEMTOP))
        return status, out, changed(self.pristine, after, mem)


def changed(before, after, preset):
    """{linear: byte} of the bytes that differ, before being the image with the case's own
    memory laid over it."""
    base = bytearray(before)
    for lin, b in preset: base[lin:lin + len(b)] = b
    out = {}
    if bytes(base) == after: return out
    C = 4096
    for c in range(0, MEMTOP, C):
        x, y = base[c:c + C], after[c:c + C]
        if x != y:
            for i in range(len(x)):
                if x[i] != y[i]: out[c + i] = y[i]
    return out


class Port:
    """The port: tools/fuzzhost.c, linked with the port's objects."""

    def __init__(self, exe):
        self.p = subprocess.Popen([exe, EXE], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
        if self.p.stdout.readline().strip() != 'READY': sys.exit('fuzzasm.py: fuzzhost did not start')

    def run(self, regs, mem, kind, seg, off):
        w = self.p.stdin
        w.write('R ' + ' '.join(f'{regs[k]:x}' for k in REGS) + '\n')
        for lin, b in mem:
            w.write(f'M {lin:x} {bytes(b).hex()}\n')
        rcs, rip = (RET_CS, RET_IP) if kind == 'far' else (0, RET_NEAR)
        w.write(f'C {kind} {seg:x} {off:x} {rcs:x} {rip:x}\n')
        w.flush()
        rl = self.p.stdout.readline().split()
        if not rl: sys.exit('fuzzasm.py: fuzzhost stopped (it crashed; run the case in a debugger)')
        out = {k: int(v, 16) for k, v in zip(REGS, rl[1:])}
        st = self.p.stdout.readline().rstrip('\n').split(' ', 2)
        status = 'ret' if st[1] == '0' else 'halt ' + (st[2] if len(st) > 2 else '')
        mem_out = {}
        base = {}
        for lin, b in mem:
            for i, v in enumerate(b): base[lin + i] = v
        while True:
            l = self.p.stdout.readline()
            if l.startswith('E') or not l: break
            _, a, h = l.split()
            a = int(a, 16); b = bytes.fromhex(h)
            for i, v in enumerate(b): mem_out[a + i] = v
        # fuzzhost reports changes against the image; the case's own memory is the baseline here
        for a, v in base.items():
            if a in mem_out and mem_out[a] == v: del mem_out[a]
            elif a not in mem_out and v != self.image_byte(a): mem_out[a] = self.image_byte(a)
        return status, out, mem_out

    image = None

    def image_byte(self, a): return Port.image[a]

    def close(self):
        self.p.stdin.close(); self.p.wait()


# ---- inputs ------------------------------------------------------------------------------------

EDGE16 = [0, 1, 2, 0x7F, 0x80, 0xFF, 0x100, 0x3FFF, 0x4000, 0x5A82, 0x7FFE, 0x7FFF, 0x8000, 0x8001,
          0xC000, 0xFF00, 0xFFFE, 0xFFFF]


def word(rng, k):
    return EDGE16[k] if k < len(EDGE16) else rng.getrandbits(16)


def base_regs(rng):
    """Random general registers (the routine must not depend on what it does not take), the
    scratch segment in DS and ES, the stack segment with SP near its top."""
    r = {k: rng.getrandbits(32) for k in ('eax', 'ebx', 'ecx', 'edx', 'esi', 'edi', 'ebp')}
    r.update(esp=0xFF00, ds=SCRATCH, es=SCRATCH, ss=STACK, fs=SCRATCH, gs=SCRATCH,
             flags=rng.choice([0, 1, 0x40, 0x80, 0x800, 0x8C1]))
    return r


def setw(r, name, v):
    big = {'ax': 'eax', 'bx': 'ebx', 'cx': 'ecx', 'dx': 'edx', 'si': 'esi', 'di': 'edi', 'bp': 'ebp', 'sp': 'esp'}
    if name in big: r[big[name]] = (r[big[name]] & 0xFFFF0000) | (v & 0xFFFF)
    elif name in ('dh', 'bh', 'ch', 'ah'):
        e = 'e' + name[0] + 'x'; r[e] = (r[e] & ~0xFF00) | (v & 0xFF) << 8
    elif name in ('dl', 'bl', 'cl', 'al'):
        e = 'e' + name[0] + 'x'; r[e] = (r[e] & ~0xFF) | (v & 0xFF)
    else: r[name] = v


# ---- the targets ------------------------------------------------------------------------------

class T:
    """A routine: name, its code segment (EXE paragraph) and offset, how it is called in DOS
    (near, far) and in the port (the same, or a C entry's kind), its inputs (a function of the
    random generator and the case number giving registers and memory), and what to compare."""

    def __init__(self, name, seg, off, call, gen, port=None, regs=GPR, flags=ALLFLAGS, what='',
                 mem=True, ignore=(), note='', heavy=False):
        self.name, self.seg, self.off, self.call, self.gen = name, seg, off, call, gen
        self.port = port or call
        self.regs, self.flags, self.what, self.mem = regs, flags, what, mem
        self.ignore, self.note, self.heavy = ignore, note, heavy


# ---- running ---------------------------------------------------------------------------------

def build_host(coverage=False):
    """fuzzhost.c compiled with the project's glue ([fuzz] host_glue) and linked with the port's
    objects, in place of the port's main ([fuzz] main)."""
    import portbuild, portcheck, sources
    P = portcfg.port(CFG); F = portcfg.fuzz(CFG)
    if not F.host_glue: sys.exit('fuzzasm.py: [fuzz] host_glue is not set')
    out = portcfg.variant_out(CFG, 'cov' if coverage else '')
    if not os.path.exists(os.path.join(out, P.exe)):
        sys.exit(f'fuzzasm.py: build the port first (tools/portbuild.py{" --coverage" if coverage else ""})')
    game = portcfg.game_sources(CFG)
    objs = [os.path.join(out, sources.stem(p) + '.o') for p in game]
    for p in portbuild.port_sources():
        if p.endswith(os.sep + F.main.replace('/', os.sep)): continue
        objs.append(portbuild.port_object(out, p))
    _, libs, extra = portbuild.sound_deps()
    objs += [os.path.join(out, 'deps', os.path.basename(p)[:-2] + '.o') for p in extra]
    host = os.path.join(CFG.build, 'fuzz' + ('-cov' if coverage else ''), 'fuzzhost')
    os.makedirs(os.path.dirname(host), exist_ok=True)
    src = os.path.join(here, 'fuzzhost.c')
    newest = max(os.path.getmtime(o) for o in objs + [src, F.host_glue])
    if os.path.exists(host) and os.path.getmtime(host) >= newest: return host
    cov = ['-fprofile-instr-generate', '-fcoverage-mapping'] if coverage else []
    r = subprocess.run([portcheck.host_cc()] + portbuild.PORT_FLAGS + cov + [f'-DFUZZ_GLUE="{F.host_glue}"', '-c', '-o', host + '.o', src],
                       capture_output=True, text=True)
    if r.returncode: sys.exit('fuzzasm.py: fuzzhost.c does not compile:\n' + r.stderr[-3000:])
    r = subprocess.run([portcheck.host_cc(), '-o', host] + (['-fprofile-instr-generate'] if coverage else []) + [host + '.o'] + objs +
                       portbuild.pkg_config('--libs') + libs, capture_output=True, text=True)
    if r.returncode: sys.exit('fuzzasm.py: fuzzhost does not link:\n' + r.stderr[-3000:])
    return host


def fmt_regs(r, keys):
    return ' '.join(f'{k}={r[k]:x}' for k in keys)


def compare(t, regs_in, mem_in, dos, port):
    """'' when they agree, else what differs."""
    ds, dr, dm = dos
    ps, pr, pm = port
    if ds.startswith('int ') and ps.startswith('halt'): return ''    # a fault in both
    if ds != 'ret' or ps != 'ret': return f'DOS {ds}, port {ps}'
    bad = []
    for k in t.regs:
        a, b = dr[k], pr[k]
        if k in ('ds', 'es', 'ss', 'fs', 'gs'): a &= 0xFFFF; b &= 0xFFFF
        if a != b: bad.append(f'{k} {a:x} against {b:x}')
    for f in t.flags:
        a, b = dr['flags'] >> FLAGBITS[f] & 1, pr['flags'] >> FLAGBITS[f] & 1
        if a != b: bad.append(f'{f.upper()} {a} against {b}')
    if t.mem:
        sp = dr['esp'] & 0xFFFF
        ss = dr['ss'] & 0xFFFF
        dead = (ss * 16, ss * 16 + sp)     # below the final SP: what was pushed and popped
        def keep(a): return not (dead[0] <= a < dead[1]) and not any(lo <= a < hi for lo, hi in t.ignore)
        dk = {a: v for a, v in dm.items() if keep(a)}
        pk = {a: v for a, v in pm.items() if keep(a)}
        outside = [a for a in dk if not any(lo <= a < hi for lo, hi in REGIONS)]
        if outside: bad.append(f'DOS wrote outside the regions the port has: {min(outside):X}..{max(outside):X}')
        if dk != pk:
            diff = sorted(a for a in set(dk) | set(pk) if dk.get(a) != pk.get(a))
            bad.append(f'memory: {len(diff)} bytes differ, from {diff[0]:X} (DOS ' +
                       ' '.join(f'{dk.get(a, -1) & 0xFF:02x}' if a in dk else '--' for a in diff[:8]) + '; port ' +
                       ' '.join(f'{pk[a]:02x}' if a in pk else '--' for a in diff[:8]) + ')')
    return '; '.join(bad)


def load_targets():
    """[fuzz] targets: the project's file of T() definitions, which sets TARGETS and REGIONS and
    may change LOAD, SCRATCH, STACK and MEMTOP."""
    F = portcfg.fuzz(CFG)
    if not F.targets: sys.exit('fuzzasm.py: [fuzz] targets is not set')
    m = portcfg.load_module(F.targets, 'fuzz_targets')
    for k in ('REGIONS', 'LOAD', 'SCRATCH', 'STACK', 'MEMTOP', 'RET_NEAR', 'RET_CS', 'RET_IP'):
        if hasattr(m, k): globals()[k] = getattr(m, k)
    return m.TARGETS


def main(argv):
    targets_all = load_targets()
    ap = argparse.ArgumentParser(description='Differential fuzzing of single routines, DOS bytes against the port.')
    ap.add_argument('--quick', action='store_true')
    ap.add_argument('--deep', action='store_true')
    ap.add_argument('--cases', type=int)
    ap.add_argument('--seed', type=int)
    ap.add_argument('--only')
    ap.add_argument('--list', action='store_true')
    ap.add_argument('--coverage', action='store_true')
    ap.add_argument('-v', action='store_true')
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)
    if a.list:
        for t in targets_all: print(f'{t.name:22s} {t.seg:04X}:{t.off:04X} {t.call:4s}  {t.what}')
        return 0
    n = a.cases or (10000 if a.deep else 200)
    seed = a.seed if a.seed is not None else (random.SystemRandom().getrandbits(31) if a.deep else 1)
    targets = [t for t in targets_all if not a.only or any(t.name.startswith(o) for o in a.only.split(','))]
    host = build_host(a.coverage)
    env_cov = os.path.join(CFG.build, 'coverage', 'fuzz-%p.profraw')
    if a.coverage:
        os.makedirs(os.path.dirname(env_cov), exist_ok=True)
        os.environ['LLVM_PROFILE_FILE'] = env_cov
    t0 = time.time()
    dos = Dos(); port = Port(host); Port.image = dos.pristine
    print(f'fuzzasm: {len(targets)} routines, {n} cases each ({max(1, n // 5)} for the decoders), seed {seed}' +
          (' (--coverage)' if a.coverage else ''))
    total_bad = 0; faults = 0; cases_run = 0
    for t in targets:
        rng = random.Random(seed ^ zlib.crc32(t.name.encode()))
        bad = 0; tf = 0; wrote = 0; first = None; t1 = time.time()
        nt = max(1, n // 5) if t.heavy and not a.cases else n     # the decoders write up to 64 KB a case
        cases_run += nt
        for k in range(nt):
            regs, mem = t.gen(rng, k)
            d = dos.run(regs, mem, t.call, t.seg, t.off)
            p = port.run(regs, mem, t.port if t.port in ('near', 'far') else t.port, t.seg, t.off)
            if d[0].startswith('int'): tf += 1
            sp_end = d[1]['ss'] * 16 + (d[1]['esp'] & 0xFFFF)
            if any(not (d[1]['ss'] * 16 <= x < sp_end) for x in d[2]): wrote += 1
            why = compare(t, regs, mem, d, p)
            if why:
                bad += 1
                if first is None:
                    first = f'case {k}: in {fmt_regs(regs, ("eax", "ebx", "ecx", "edx", "esi", "edi", "ebp"))}' + \
                            (f', {sum(len(b) for _, b in mem)} bytes of memory' if mem else '') + f': {why}'
                if a.v: print(f'  {t.name} case {k}: {why}')
        faults += tf
        total_bad += bad
        print(f'{t.name:22s} {"ok  " if not bad else "DIFF"} {nt} cases{f", {tf} faulted in both" if tf else ""}'
              f'{f", {wrote} wrote memory" if wrote else ""}'
              f'{f", {bad} differ; first {first}" if bad else ""}  ({time.time() - t1:.1f} s)')
    port.close()
    print(f'fuzzasm: {len(targets)} routines, {cases_run} cases, {total_bad} differ, seed {seed}, '
          f'{time.time() - t0:.1f} s')
    return 1 if total_bad else 0


if __name__ == '__main__':
    sys.exit(main(ARGV))
