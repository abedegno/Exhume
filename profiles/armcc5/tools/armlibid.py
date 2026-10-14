"""Find a library source's functions in a linked image, and write the /* at: */ line for them.

    python3 armlibid.py --config exhume.toml SRC [--opts "..."]

The source is compiled with the pinned flags (or --opts), linked alone with every undefined
symbol given a dummy address, and each function compared with every map row it could fill (same
size, or up to 3 bytes of padding more), with call targets and literal-pool words masked. A
function with one candidate is settled; one with several is settled by name order, since armlink
lays code out sorted by section name: its address must lie between those of the settled names
just below and above it. Functions with no candidate are missing (armlink removed them as
unused, or the library differs)."""
import os, re, sys
import capstone
import armtc, armmap, ehex

_MAPLINE = re.compile(r'^\s*0x([0-9a-f]{8})\s+0x([0-9a-f]{8})\s+Code\s+RO\s+\d+\s+(?:\*\s+)?i\.(\w+)\s+\S+\s*$', re.M)
_PCREL = re.compile(r'\[pc, #(-?0x[0-9a-f]+|-?\d+)\]')
BASE = 0x08000000


def _mask(code, base):
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    m = bytearray(len(code))
    for ins in md.disasm(code, base):
        o = ins.address - base
        if ins.mnemonic in ('bl', 'b.w', 'blx'): m[o:o + ins.size] = b'\1' * ins.size
        mm = _PCREL.search(ins.op_str)
        if mm and ins.mnemonic.startswith('ldr'):
            t = ((ins.address + 4) & ~3) + int(mm.group(1), 0) - base
            if 0 <= t < len(code): m[t:t + 4] = b'\1\1\1\1'
    return m


def settle(candidates):
    """(settled {name: addr}, still ambiguous {name: [addrs]}). An address holds one function, and
    code is laid out sorted by name: a name is settled when exactly one of its candidate addresses
    lies between the settled names just below and above it and no other name also fits there.
    Names that fit nowhere drop out (the image lacks them)."""
    pending = {n: list(c) for n, c in candidates.items() if c}
    at = {}
    changed = True
    while changed and pending:
        changed = False
        def window(n):
            lower = [at[k] for k in at if k < n]; upper = [at[k] for k in at if k > n]
            return (max(lower) if lower else -1), (min(upper) if upper else 1 << 40)
        fits = {}
        for n, c in pending.items():
            lo, hi = window(n)
            fits[n] = [x for x in c if lo < x < hi and x not in at.values()]
        claim = {}
        for n, f in fits.items():
            for x in f: claim.setdefault(x, []).append(n)
        for n in sorted(pending):
            f = fits[n]
            if not f:
                del pending[n]; changed = True          # fits nowhere
            elif len(f) == 1 and claim[f[0]] == [n]:
                at[n] = f[0]; del pending[n]; changed = True
            elif len(f) < len(pending[n]):
                pending[n] = f; changed = True
    return at, pending


def candidates_for(tc, cfg, src, opts):
    """{function name: [map row addresses its masked bytes fit]}."""
    stem = os.path.splitext(os.path.basename(src))[0]
    work = os.path.join(cfg.build, 'libid', stem); os.makedirs(work, exist_ok=True)
    obj = os.path.join(work, 'm.o')
    ok, log = tc.compile(src, obj, opts)
    if not ok: raise RuntimeError(log)
    with open(os.path.join(work, 'm.sct'), 'w') as f:
        f.write(f'LR 0x{BASE:08X} {{ ER 0x{BASE:08X} {{ m.o(+RO) }} RW 0x20000000 {{ m.o(+RW +ZI) }} }}\n')
    sym = '#<SYMDEFS># ARM Linker, 5060960: Last Updated: Thu Jan  1 00:00:00 1970\n'
    for _ in range(3):
        with open(os.path.join(work, 'x.sym'), 'w') as f: f.write(sym)
        rc, out = tc.run('armlink', ['--cpu', 'Cortex-M3', '--scatter', 'm.sct', '--no_startup', '--no_scanlib', '--keep', '*',
                                     '--diag_suppress', '6305,6314', 'm.o', 'x.sym', '-o', 'm.axf', '--map', '--list', 'm.lst'], work)
        if not rc: break
        for k, name in enumerate(re.findall(r'Undefined symbol (\w+)', out)):
            sym += f'0x{0x08F00001 + 4 * (len(sym.splitlines()) + k):08x} T {name}\n'
    if rc: raise RuntimeError(out)
    code = tc.tobin(os.path.join(work, 'm.axf'), os.path.join(work, 'bin')).get('ER', b'')
    with open(os.path.join(work, 'm.lst'), encoding='latin1') as f: lst = f.read()
    img = ehex.Image(cfg.image)
    rows = armmap.load(os.path.join(cfg.map, 'functions.tsv'))
    out = {}
    for a, n, name in _MAPLINE.findall(lst):
        a = int(a, 16) - BASE; n = int(n, 16)
        f = code[a:a + n]; m = _mask(f, BASE + a)
        out[name] = [r.addr for r in rows if n <= r.size <= n + 3 and r.addr + n <= img.end
                     and all(m[i] or f[i] == g for i, g in enumerate(img.bytes(r.addr, n)))]
    return out


def identify(cfg, src, opts=None):
    """(at {name: addr}, ambiguous {name: [addrs]}, missing [names])."""
    tc = armtc.toolchain(cfg)
    cand = candidates_for(tc, cfg, os.path.abspath(src), opts or cfg.c_opts)
    missing = sorted(n for n, c in cand.items() if not c)
    at, amb = settle({n: c for n, c in cand.items() if c})
    return at, amb, missing


def main(argv):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'tools'))
    import config
    path, a = config.pop_config(argv); cfg = config.load(path)
    if not a: sys.exit(__doc__)
    opts = a[a.index('--opts') + 1] if '--opts' in a else None
    at, amb, missing = identify(cfg, a[0], opts)
    print('/* at: ' + ', '.join(f'{n} 0x{v:08X}' for n, v in sorted(at.items(), key=lambda x: x[1])) + ' */')
    for n, c in sorted(amb.items()): print(f'AMBIGUOUS {n}: ' + ', '.join(f'0x{x:08X}' for x in c))
    if missing: print('MISSING (unused or different): ' + ', '.join(missing))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
