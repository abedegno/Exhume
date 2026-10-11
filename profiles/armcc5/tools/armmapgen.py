"""Generate the map of a linked image: map/functions.tsv, map/data.tsv and map/coverage.txt.

    python3 armmapgen.py --config exhume.toml [--seed 0xADDR ...] [--force]

Ghidra (headless) finds the functions, seeded with the vector handlers (or the image base when
there is no vector table); gap starts that look like functions (a push of lr, or the target of
a pointer in a literal pool) are seeded and Ghidra runs again, a few rounds at most. A function's
row runs to the next function (its literal pool and alignment padding belong to it); the last
runs to the first read-only data a literal pool points at. RAM rows are the RAM addresses literal
pools name, sized to the next one, rw or zi from the region table.

Coverage: every byte of a function row must be in Ghidra's body for it, a literal word it loads,
a switch table after a tbb/tbh, or zero padding to a 4-byte boundary at its end; the read-only
data, the region table and the compressed RW data must follow one another to the image's end.
Anything else is a line in coverage.txt (address, length, first instruction), and the exit status
is 1. Existing rows keep their names and files when their address and size are unchanged."""
import os, re, shutil, struct, subprocess, sys
import capstone
import ehex, rwdata, armmap
from armmap import Row

PROFILE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAM = 0x20000000
_PCREL = re.compile(r'\[pc, #(-?0x[0-9a-f]+|-?\d+)\]')


def ghidra_home():
    return os.environ.get('GHIDRA_HOME', '/opt/homebrew/Cellar/ghidra/12.1.4/libexec')


def ghidra_headless():
    return os.path.join(ghidra_home(), 'support', 'analyzeHeadless')


def java_home():
    return os.environ.get('JAVA_HOME') or '/opt/homebrew/opt/openjdk@21/libexec/openjdk.jdk/Contents/Home'


def vector_table(img):
    """(length in bytes, handler addresses). A vector table starts with a RAM stack pointer."""
    sp = struct.unpack_from('<I', img.data, 0)[0]
    if not RAM <= sp < RAM + 0x100000: return 0, []
    n, handlers = 4, []
    while n + 4 <= len(img.data):
        w = struct.unpack_from('<I', img.data, n)[0]
        if w == 0: n += 4; continue
        if not (w & 1 and img.base <= w < img.end): break
        handlers.append(w & ~1); n += 4
    return n, handlers


def seeds(img):
    """Vector handlers (or the image base without a vector table), and the region handlers
    __scatterload calls through the region table."""
    n, handlers = vector_table(img)
    out = set(handlers) if n else {img.base}
    span = table_span(img)
    end = span[0] if span else img.end
    out |= bl_targets(img, img.base + n, end)
    if span:
        for a in range(span[0], span[1], 16):
            fn = struct.unpack_from('<I', img.data, a + 12 - img.base)[0]
            if img.base <= fn < img.end: out.add(fn & ~1)
    return out


def ends_in_return(img, a):
    """True when the instruction ending at a returns or branches away (bx lr, pop {..pc}, b)."""
    for back in (2, 4):
        try:
            ins = next(_md().disasm(img.bytes(a - back, back), a - back, count=1), None)
        except ValueError:
            continue
        if ins and ins.size == back and (ins.mnemonic in ('bx', 'b', 'b.w') or ins.mnemonic.startswith('pop') and 'pc' in ins.op_str):
            return True
    return False


def bl_targets(img, lo, hi):
    """Targets of bl instructions found by a linear sweep (a data word that decodes as bl must
    still land in the code area, and Ghidra must agree it is a function)."""
    md = _md(); out = set(); a = lo
    while a < hi:
        ins = next(md.disasm(img.bytes(a, min(4, hi - a)), a, count=1), None)
        if ins is None: a += 2; continue
        if ins.mnemonic == 'bl':
            t = int(ins.op_str.lstrip('#'), 0)
            if lo <= t < hi: out.add(t)
        a += ins.size
    return out


def pointer_targets(img, lo, hi):
    """Thumb code addresses held in aligned words anywhere in the image or its initial RW data:
    function-pointer tables. Ghidra must still agree each is a function."""
    words = [struct.unpack_from('<I', img.data, k)[0] for k in range(0, len(img.data) - 3, 4)]
    try:
        regions = rwdata.find_table(img)
        ram = rwdata.RamImage(img, regions)
        for s0, d in ram.parts:
            words += [struct.unpack_from('<I', d, k)[0] for k in range(0, len(d) - 3, 4)]
    except ValueError:
        pass
    md = _md(); out = set()
    for w in set(words):
        if w & 1 and lo <= w - 1 < hi and next(md.disasm(img.bytes(w - 1, 4), w - 1, count=1), None):
            out.add(w - 1)
    return out


def inside_code(img, t, funcs):
    """True when t is in the middle of a function body, reached by falling through an ordinary
    instruction: a data word that only looks like a pointer to it."""
    for e, ranges in funcs.items():
        for lo, hi in ranges:
            if lo <= t < hi and t != e or hi == t:      # inside, or where a range of it was cut at t
                prev = before(img, t)
                if prev and not (prev[-1].mnemonic in ('bx', 'b', 'b.w') or prev[-1].mnemonic.startswith('pop') and 'pc' in prev[-1].op_str):
                    return True
    return False


def follow(img, start, lo, hi, explained):
    """Mark the code reachable from start inside [lo, hi) (switch cases Ghidra did not follow, or
    where its body stops short), and the literal words it loads."""
    md = _md(); todo = [start]; seen = set()
    while todo:
        a = todo.pop()
        while lo <= a < hi and a not in seen:
            seen.add(a)
            ins = next(md.disasm(img.bytes(a, min(4, hi - a)), a, count=1), None)
            if ins is None: break
            for x in range(a, min(a + ins.size, hi)): explained[x - lo] = 1
            m = _PCREL.search(ins.op_str)
            if m and ins.mnemonic.startswith('ldr'):
                t = ((ins.address + 4) & ~3) + int(m.group(1), 0)
                for x in range(t, t + 4):
                    if lo <= x < hi: explained[x - lo] = 1
            mn = ins.mnemonic
            if mn.startswith(('b', 'cb')) and '#' in ins.op_str and mn not in ('bl', 'blx', 'bic', 'bics', 'bfi', 'bfc'):
                t = int(ins.op_str.split('#')[-1], 0)
                if lo <= t < hi: todo.append(t)
                if mn in ('b', 'b.w'): break
            if mn in ('bx', 'tbb', 'tbh') or mn.startswith('pop') and 'pc' in ins.op_str: break
            a += ins.size


def data_starts(lits, adrs, lo, hi, code_starts):
    """Starts of data inside a function row: adr targets and even literal values pointing into
    it (an odd value is a Thumb code pointer, not data)."""
    return {a for a in adrs if lo <= a < hi} | {v for v in lits.values() if lo <= v < hi and v not in code_starts and not v & 1}


def filler(img, a, n):
    """True when [a, a+n) is only nop, nop.w, 0000 halfwords or b-to-self loops (a startup file's
    unused default handlers): padding or dead code armlink or armcc left."""
    x = a
    for ins in _md().disasm(img.bytes(a, n), a):
        selfloop = ins.mnemonic == 'b' and '#' in ins.op_str and int(ins.op_str.split('#')[-1], 0) == ins.address
        if ins.mnemonic not in ('nop', 'nop.w') and not selfloop and not (ins.size == 2 and ins.bytes == b'\0\0'): return False
        x = ins.address + ins.size
    return x == a + n


def run_ghidra(img, seed_set, workdir):
    """{entry: [(lo, hi exclusive), ...]} from Ghidra's analysis."""
    os.makedirs(workdir, exist_ok=True)
    binp = os.path.join(workdir, 'image.bin'); seedp = os.path.join(workdir, 'seeds.txt'); out = os.path.join(workdir, 'functions.tsv')
    with open(binp, 'wb') as f: f.write(img.data)
    with open(seedp, 'w') as f: f.write(''.join(f'{a:08X}\n' for a in sorted(seed_set)))
    if os.path.exists(out): os.remove(out)
    env = dict(os.environ, JAVA_HOME=java_home())
    r = subprocess.run([ghidra_headless(), workdir, 'exhume', '-import', binp, '-overwrite', '-deleteProject',
                        '-loader', 'BinaryLoader', '-loader-baseAddr', f'0x{img.base:08X}', '-processor', 'ARM:LE:32:Cortex',
                        '-scriptPath', os.path.join(PROFILE, 'ghidra'), '-postScript', 'SeedAndExport.java', seedp, out],
                       capture_output=True, text=True, env=env)
    if not os.path.exists(out):
        raise RuntimeError('Ghidra wrote no functions:\n' + (r.stdout + r.stderr)[-2000:])
    funcs = {}
    with open(out) as f:
        for line in f:
            e, lo, hi = (int(x, 16) for x in line.split())
            funcs.setdefault(e, []).append((lo, hi + 1))
    return funcs


def _md():
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    return md


def first_insn(img, addr):
    try:
        ins = next(_md().disasm(img.bytes(addr, 4), addr, count=1), None)
    except ValueError:
        return '?'
    return f'{ins.mnemonic} {ins.op_str}' if ins else '.short'


def scan(img, ranges):
    """From the instructions in ranges: literal words (addr -> value) loaded pc-relative, data
    addresses taken pc-relative (adr, add rX, pc), and switch tables (start, tbb/tbh, cases)."""
    md = _md(); lits, adrs, switches = {}, set(), []
    for lo, hi in ranges:
        recent = []
        for ins in md.disasm(img.bytes(lo, hi - lo), lo):
            m = _PCREL.search(ins.op_str)
            if m and ins.mnemonic.startswith(('ldr', 'vldr')):
                a = ((ins.address + 4) & ~3) + int(m.group(1), 0)
                n = 8 if ins.mnemonic.startswith('ldrd') else 4
                if img.base <= a and a + n <= img.end:
                    for k in range(0, n, 4): lits[a + k] = struct.unpack_from('<I', img.data, a + k - img.base)[0]
            elif ins.mnemonic in ('adr', 'adr.w'):
                adrs.add(((ins.address + 4) & ~3) + int(ins.op_str.split('#')[-1], 0))
            elif ins.mnemonic in ('add', 'addw', 'add.w') and ', pc, #' in ins.op_str:
                adrs.add(((ins.address + 4) & ~3) + int(ins.op_str.split('#')[-1], 0))
            if ins.mnemonic in ('tbb', 'tbh'):
                cases = None          # the bound: a cmp a few instructions back, before the default branch
                if not any(p.mnemonic in ('cmp', 'cmp.w') for p in recent):
                    recent = before(img, ins.address)        # Ghidra's body range may start after the cmp
                for k, p in enumerate(reversed(recent)):
                    if p.mnemonic in ('cmp', 'cmp.w') and '#' in p.op_str:
                        try: imm = int(p.op_str.split('#')[-1], 0)
                        except ValueError: break
                        between = [q.mnemonic for q in recent[len(recent) - k:]]
                        # bhs/bcs default: cases 0..imm-1; bhi default: 0..imm
                        cases = imm if any(b.startswith(('bhs', 'bcs')) for b in between) else imm + 1
                        break
                switches.append((ins.address + 4, ins.mnemonic, cases))
            recent = (recent + [ins])[-8:]
    return lits, adrs, switches


def case_targets(img, switches):
    """Every switch's case code. armcc sends cases beyond a tbb's 510-byte reach through b
    trampolines placed after the function's return, so a case that is an unconditional b adds
    the b's target too."""
    md = _md(); out = set()
    for s0, mn, cases in switches:
        if not cases: continue
        w = 1 if mn == 'tbb' else 2
        for k in range(cases):
            try: t = s0 + 2 * int.from_bytes(img.bytes(s0 + k * w, w), 'little')
            except ValueError: break
            out.add(t)
            ins = next(md.disasm(img.bytes(t, 4), t, count=1), None)
            if ins and ins.mnemonic in ('b', 'b.w') and '#' in ins.op_str:
                out.add(int(ins.op_str.split('#')[-1], 0))
    return out


def trampolines(img, lo, hi, skip=frozenset()):
    """Runs of two or more 16-bit unconditional b straight after a return or branch: the b
    trampolines armcc places for a switch's distant cases. Returns the stubs and their targets,
    all code of the function before the run."""
    md = _md(); out = set(); a = lo; prev = None; run = []
    def flush():
        if len(run) >= 2:
            for stub in run:
                out.add(stub.address); out.add(int(stub.op_str.split('#')[-1], 0))
    while a < hi:
        if a in skip:                               # a literal word, not code
            flush(); run = []; prev = None; a += 2; continue
        ins = next(md.disasm(img.bytes(a, min(4, hi - a)), a, count=1), None)
        if ins is None: flush(); run = []; prev = None; a += 2; continue
        if ins.mnemonic == 'b' and ins.size == 2 and '#' in ins.op_str and int(ins.op_str.split('#')[-1], 0) != a and \
                (run or prev is not None and (prev.mnemonic in ('bx', 'b', 'b.w') or prev.mnemonic.startswith('pop') and 'pc' in prev.op_str)):
            run.append(ins)
        else:
            flush(); run = []
        prev = ins; a += ins.size
    flush()
    return out


def before(img, addr, span=32):
    """The instructions ending exactly at addr, decoded from a little before it."""
    md = _md()
    for back in range(span, 0, -2):
        try: code = img.bytes(addr - back, back)
        except ValueError: continue
        out = list(md.disasm(code, addr - back))
        if out and out[-1].address + out[-1].size == addr and sum(i.size for i in out) == back:
            return out[-8:]
    return []


def table_span(img):
    at = img.data.find(rwdata.SCATTERLOAD)
    if at < 0: return None
    pc = img.base + at
    return struct.unpack_from('<II', img.data, ((pc + 4) & ~3) + 24 - img.base)


def build_map(img, funcs, regions):
    """(function rows, data rows, coverage lines, plausible new seeds)."""
    cov, new_seeds = [], set()
    vec_len, _ = vector_table(img)
    span = table_span(img)
    t0, t1 = span if span else (img.end, img.end)
    starts = sorted(e for e in funcs if img.base + vec_len <= e < t0)
    # a start in the middle of another function's body, after an ordinary instruction, is not a
    # function (Ghidra makes one from any data word that looks like a pointer) unless called
    called = set(vector_table(img)[1]) | bl_targets(img, img.base + vec_len, t0)
    others = lambda e: {k: v for k, v in funcs.items() if k != e}
    mid = {e for e in starts if e not in called and inside_code(img, e, others(e))}
    if mid:
        for e in mid:
            owner = max((x for x in starts if x < e and x not in mid), default=None)
            if owner is not None: funcs[owner] = funcs[owner] + funcs[e]
        starts = [e for e in starts if e not in mid]
    all_ranges = [r for e in starts for r in funcs[e]]
    lits, adrs, switches = scan(img, all_ranges)
    # a switch's case targets are code inside the function that holds the switch, never function
    # starts of their own (Ghidra makes functions of some when it can't follow the table)
    cases_code = case_targets(img, switches) | trampolines(img, img.base + vec_len, t0, skip=set(lits) | {a + 2 for a in lits})
    # a start something calls or points at is a function, whatever a switch rule says
    referenced = set(vector_table(img)[1]) | bl_targets(img, img.base + vec_len, t0) | pointer_targets(img, img.base + vec_len, t0)
    for e in sorted(cases_code & set(starts) & referenced):
        cov.append(f'0x{e:08X} is both switch case code and referenced as a function: check by hand')
    cases_code -= referenced
    if cases_code & set(starts):
        starts = [e for e in starts if e not in cases_code]
        merged = {}
        for e in sorted(funcs):
            if e in cases_code:
                owner = max((x for x in starts if x < e), default=None)
                if owner is not None: funcs.setdefault(owner, []).extend(funcs[e])
        all_ranges = [r for e in starts for r in funcs[e]]
        lits, adrs, switches = scan(img, all_ranges)
    body_end = max((hi for e in starts for _, hi in funcs[e]), default=img.base + vec_len)
    code_starts = set(starts)
    flash_targets = sorted({v for v in lits.values() if img.base <= v < t0 and (v & ~1) not in code_starts and not v & 1})
    pool_end = max([a + 4 for a in lits if a < t0] + [body_end])
    ro = [v for v in flash_targets if v >= pool_end]
    code_end = ro[0] if ro else t0
    # function rows
    frows = []
    for i, e in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else code_end
        frows.append(Row(e, nxt - e, f'sub_{e:08X}', 'code', ''))
    # coverage inside function rows
    if starts and starts[0] != img.base + vec_len:
        cov.append(f'0x{img.base + vec_len:08X} +0x{starts[0] - img.base - vec_len:X} before the first function: {first_insn(img, img.base + vec_len)}')
    sw_by_start = {s: (m, c) for s, m, c in switches}
    for r in frows:
        lo, hi = r.addr, r.addr + r.size
        explained = bytearray(r.size)
        for blo, bhi in funcs[r.addr]:
            if bhi > hi: cov.append(f'0x{r.addr:08X} body runs past the next function at 0x{hi:08X}')
            for x in range(max(blo, lo), min(bhi, hi)): explained[x - lo] = 1
        for a in lits:
            if lo <= a < hi:
                for x in range(a, min(a + 4, hi)): explained[x - lo] = 1
        follow(img, lo, lo, hi, explained)     # our own decode from the entry, where Ghidra's body stops short
        for s, (mn, cases) in sw_by_start.items():
            if lo <= s < hi:
                n = cases * (1 if mn == 'tbb' else 2) if cases else 0
                if not n:
                    cov.append(f'0x{s - 4:08X} {mn} with no bound found: its table and cases are unchecked')
                x = s                    # the table is data: mark it before following any case
                while x < hi and (n and x < s + n or not n and not explained[x - lo]):
                    explained[x - lo] = 1; x += 1
                if n and mn == 'tbb' and n % 2 and x < hi: explained[x - lo] = 1      # tbb table padded to a halfword
                if n and s + n <= hi:
                    for t in case_targets(img, [(s, mn, cases)]):
                        follow(img, t, lo, hi, explained)
        for t in cases_code:
            if lo <= t < hi: follow(img, t, lo, hi, explained)
        # data reached by adr, or by a pointer in a literal pool, runs to the next explained byte
        for d in sorted(data_starts(lits, adrs, lo, hi, code_starts)):
            x = d
            while x < hi and not explained[x - lo]:
                explained[x - lo] = 1; x += 1
        tail = hi
        while tail > lo and not explained[tail - lo - 1] and img.bytes(tail - 1, 1) == b'\0' and hi - tail < 3: tail -= 1
        if (hi - (tail if tail < hi else hi)) and hi % 4 == 0:
            for x in range(tail, hi): explained[x - lo] = 1
        x = 0
        while x < r.size:
            if explained[x]: x += 1; continue
            y = x
            while y < r.size and not explained[y]: y += 1
            a = lo + x
            if filler(img, a, y - x): x = y; continue
            ins = first_insn(img, a)
            cov.append(f'0x{a:08X} +0x{y - x:X} unexplained in sub_{r.addr:08X}: {ins}')
            c = a
            while c < lo + y and img.bytes(c, 2) == b'\0\0': c += 2          # alignment before the code
            if c < lo + y and first_insn(img, c) not in ('?', '.short') and (c % 4 == 0 or ends_in_return(img, c)):
                new_seeds.add(c)          # one per gap per round: armcc's sections are 4-aligned
            x = y
    # read-only data rows
    drows = []
    for i, v in enumerate(ro):
        nxt = ro[i + 1] if i + 1 < len(ro) else t0
        drows.append(Row(v, nxt - v, f'c_{v:08X}', 'ro', ''))
    if not ro and code_end != t0:
        cov.append(f'0x{code_end:08X} read-only data with no reference')
    # RAM rows
    ram_regions = [(r.exec, r.exec + r.size, 'zi' if r.kind == 'zero' else 'rw') for r in regions]
    ram_targets = sorted({v for v in lits.values() if any(s <= v < e for s, e, _ in ram_regions)})
    for i, v in enumerate(ram_targets):
        s, e, kind = next(t for t in ram_regions if t[0] <= v < t[1])
        nxt = min([w for w in ram_targets[i + 1:i + 2] if w < e] + [e])
        drows.append(Row(v, nxt - v, f'g_{v:08X}', kind, ''))
    # the end of the image: region table, compressed RW, padding
    if span:
        comp = [r for r in regions if r.kind == 'compressed']
        if comp:
            ram = rwdata.RamImage(img, regions)
            cs, ce = ram.used_compressed
            if cs != t1: cov.append(f'0x{t1:08X} region table end, but compressed RW data starts at 0x{cs:08X}')
            if img.end - ce >= 4 or any(img.bytes(ce, img.end - ce)):
                cov.append(f'0x{ce:08X} +0x{img.end - ce:X} after the compressed RW data is not padding')
    return frows, drows, cov, new_seeds


def generate(img, workdir, extra_seeds=(), rounds=6):
    """(function rows, data rows, coverage lines), seeding Ghidra again with gap starts that look
    like functions until no new ones appear."""
    s = seeds(img) | set(extra_seeds)
    regions = rwdata.find_table(img)
    # pointers seed only after a first pass: a word that equals an address inside a function body
    # (after an ordinary instruction) is data, not a function pointer
    funcs = run_ghidra(img, s, workdir)
    n = vector_table(img)[0]; span = table_span(img)
    s |= {t for t in pointer_targets(img, img.base + n, span[0] if span else img.end) if not inside_code(img, t, funcs)}
    for _ in range(rounds):
        funcs = run_ghidra(img, s, workdir)
        frows, drows, cov, new = build_map(img, funcs, regions)
        lt, _, sw = scan(img, [r for e in funcs for r in funcs[e]])
        new -= case_targets(img, sw) | trampolines(img, img.base + vector_table(img)[0], (table_span(img) or (img.end,))[0],
                                                   skip=set(lt) | {a + 2 for a in lt})
        if not new - s: break
        s |= new
    return frows, drows, cov


def merge_names(new_rows, old_rows, report):
    old = {(r.addr, r.size): r for r in old_rows}
    by_addr = {r.addr: r for r in old_rows}
    for r in new_rows:
        o = old.get((r.addr, r.size))
        if o:
            r.name, r.file = o.name, o.file
            if o.kind != r.kind and armmap.placeholder(o.name) is None: r.kind = o.kind
        elif r.addr in by_addr and armmap.placeholder(by_addr[r.addr].name) is None:
            o = by_addr[r.addr]
            report.append(f'0x{r.addr:08X} {o.name}: size 0x{o.size:X} now 0x{r.size:X}; name kept')
            r.name, r.file = o.name, o.file
    return new_rows


def main(argv):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(PROFILE)), 'tools'))
    import config
    path, a = config.pop_config(argv); cfg = config.load(path)
    extra = [int(a[i + 1], 0) for i, x in enumerate(a) if x == '--seed']
    img = ehex.Image(cfg.image)
    frows, drows, cov = generate(img, os.path.join(cfg.build, 'mapgen'), extra)
    os.makedirs(cfg.map, exist_ok=True)
    fpath, dpath = os.path.join(cfg.map, 'functions.tsv'), os.path.join(cfg.map, 'data.tsv')
    report = []
    frows = merge_names(frows, armmap.load(fpath), report)
    drows = merge_names(drows, armmap.load(dpath), report)
    armmap.save(fpath, frows); armmap.save(dpath, drows)
    with open(os.path.join(cfg.map, 'coverage.txt'), 'w') as f: f.write(''.join(l + '\n' for l in cov))
    for l in report: print(l)
    print(f'-- {len(frows)} functions, {len(drows)} data rows, {len(cov)} coverage problems')
    return 1 if cov else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
