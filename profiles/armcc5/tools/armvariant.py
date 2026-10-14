"""Compile many variants of a source with armcc and score one function against the image.

    python3 armvariant.py [--config PATH] score SRC FUNC [--diff] [--regs]
    python3 armvariant.py [--config PATH] pool SRC FUNC

The engine under armpermute.py, armbrute.py, armoutline.py and armreduce.py. A variant is only
compiled, never linked: armcc takes many source files in one call, and Wine's start-up is most
of a call's cost, so a batch of 40 to 150 variants costs little more than one. FUNC's bytes come
from `fromelf -c` of each object (its section i.FUNC, so --split_sections is assumed).

The score is the number of instructions that differ between FUNC's listing and the image's, by
a diff of the two, plus half the size difference in bytes. Before the diff each listing is
normalised: branch targets become offsets in the function, `bl` targets and literal-pool words
are masked (an unlinked object has neither), and a switch's tbb/tbh table becomes one item. 0
means the same instructions, registers and order; it is a strong hint, not a match: run
match.py on the file before believing it. --regs also prints the structural score, with
registers, pc-relative offsets and branch targets masked as well: it tells a difference of
structure from one of register allocation, which needs other tools (compiler.md, "Searching").

  score  compile SRC and print FUNC's score (--diff: the differing instructions)
  pool   compile SRC and print FUNC's pc-relative literal loads in code order, then its literal
         pool in pool order (compiler.md: the pool shows the order the source created the
         constants in; the loads show the scheduler's order)

Environment: with EXHUME_ARMCC_RUNNER=wine, WINE names the wine command (default wine), and a
Batch given a prefix directory runs in a Wine prefix of its own there, so parallel jobs do not
share one wineserver (on many cores the shared server is the bottleneck, and now and then
hangs: every call has a timeout and is retried)."""
import difflib, os, re, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path[:0] = [HERE, os.path.join(EXHUME, 'tools')]
import armtc, armmap, ehex

WINE = os.environ.get('WINE', 'wine')
TIMEOUT = 180

# ---------------------------------------------------------------- running the tools


class Runner:
    """Keil tools for variant batches: the project's Toolchain, or host Wine with its own
    prefix and a timeout."""

    def __init__(self, cfg, prefix=None, tc=None):
        self.tc = tc or armtc.toolchain(cfg)
        self.root = os.path.realpath(cfg.root)
        self.env = None
        if self.tc.runner == 'wine':
            self.env = dict(os.environ, ARM_TOOL_VARIANT='mdk_lite', WINEDEBUG='-all',
                            ARM_PRODUCT_PATH='Z:' + self.tc.home.replace('/', '\\') + '\\ARM\\sw\\mappings')
            if prefix:
                # a new prefix without Mono or Gecko: armcc needs neither, and Wine would offer them
                os.makedirs(os.path.dirname(os.path.abspath(prefix)), exist_ok=True)
                self.env.update(WINEPREFIX=os.path.abspath(prefix), WINEDLLOVERRIDES='mscoree,mshtml=')

    def opts(self, opts):
        """Options as a list; -I/work/ (the Docker mount of the project root) becomes the root
        when the tools run under host Wine."""
        if self.tc.runner == 'wine': opts = opts.replace('-I/work/', '-I' + self.root + '/')
        return opts.split()

    def run(self, tool, args, cwd):
        """(exit status, output)."""
        if self.tc.runner != 'wine': return self.tc.run(tool, args, cwd)
        exe = os.path.join(self.tc.home, 'ARM', 'ARMCC', 'bin', tool + '.exe')
        for _ in range(3):
            try:
                r = subprocess.run([WINE, exe] + args, cwd=cwd, env=self.env, capture_output=True, text=True,
                                   timeout=TIMEOUT)
                out = armtc.clean(r.stdout + r.stderr)
                if r.returncode in (0, 1): return r.returncode, out
            except subprocess.TimeoutExpired:
                print(f'  {tool} timed out in {cwd}; retrying', flush=True)
                server = os.path.join(os.path.dirname(WINE), 'wineserver') if os.sep in WINE else 'wineserver'
                subprocess.run([server, '-k'], env=self.env, capture_output=True)
        return 1, ''


ERROR = re.compile(r'"v(\d+)\.c", line \d+: (?:Error|Fatal)')


def compile_batch(runner, texts, opts, work):
    """Compile texts (one source each) in one armcc call and list the objects with fromelf.
    Returns ([listing text or None per text], armcc's output). armcc compiles its files in order
    and stops at the first one with an error, writing nothing for the files after it, so the
    call is repeated from the file after each failure until every file has been tried."""
    os.makedirs(work, exist_ok=True)
    for f in os.listdir(work):
        if re.match(r'v\d+\.[co]$', f): os.remove(os.path.join(work, f))
    for j, t in enumerate(texts):
        with open(os.path.join(work, f'v{j}.c'), 'w', encoding='latin1') as f: f.write(t)
    todo, log = list(range(len(texts))), ''
    while todo:
        _, out = runner.run('armcc', ['-c'] + runner.opts(opts) + [f'v{j}.c' for j in todo], work)
        log += out
        bad = [int(x) for x in ERROR.findall(out)]
        bad = [j for j in bad if j in todo and not os.path.exists(os.path.join(work, f'v{j}.o'))]
        if not bad: break
        todo = [j for j in todo if j > min(bad)]
    objs = [f'v{j}.o' for j in range(len(texts)) if os.path.exists(os.path.join(work, f'v{j}.o'))]
    lst = split_listing(runner.run('fromelf', ['-c'] + objs, work)[1]) if objs else {}
    return [lst.get(f'v{j}.o') for j in range(len(texts))], log


def split_listing(text):
    """{object file name: its part of a `fromelf -c` listing of several objects}."""
    out = {}
    for chunk in re.split(r'^(?=\s*File Name: )', text, flags=re.M):
        m = re.match(r'\s*File Name: (\S+)', chunk)
        if m: out[os.path.basename(m.group(1))] = chunk
    return out

# ---------------------------------------------------------------- bytes and listings


LINE = re.compile(r'^\s+0x([0-9a-f]{8}):\s+([0-9a-f]{2,8})    (.*)$')


def func_bytes(listing, func):
    """(FUNC's bytes, {offsets of literal pool words}) from `fromelf -c` of an object; (b'', set())
    when the object has no section i.FUNC. A switch table (DCD/DCDU/DCW after tbb or tbh) is
    data but not pool."""
    out, pool, on, table = bytearray(), set(), False, False
    for l in listing.splitlines():
        if l.startswith('** Section'):
            on = f"'i.{func}'" in l; continue
        if not on: continue
        m = LINE.match(l)
        if not m: continue
        off, hx = int(m.group(1), 16), m.group(2)
        mn = (m.group(3)[len(hx) // 2:].split() or [''])[0]      # after the ASCII column, which may hold spaces
        if off != len(out): continue
        if mn.startswith('DC'):
            out += int(hx, 16).to_bytes(len(hx) // 2, 'little')
            if mn == 'DCD' and not table: pool.add(off)
            continue
        if len(hx) == 8:                                          # a 32-bit Thumb-2 instruction: two halfwords
            out += int(hx[:4], 16).to_bytes(2, 'little') + int(hx[4:], 16).to_bytes(2, 'little')
        else:
            out += int(hx, 16).to_bytes(len(hx) // 2, 'little')
        table = mn in ('TBB', 'TBH')
    return bytes(out), pool


_MD = None


def md():
    global _MD
    if _MD is None:
        import capstone
        _MD = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
    return _MD


def listing(buf, pool, collect=None):
    """Normalised instructions of a function: branch targets as offsets, bl targets and pool
    words masked, a tbb/tbh table as one item. With collect (a set), the sweep also gathers the
    targets of pc-relative loads (ints) and adr ('adr', offset): how the image's pool is found."""
    out, i, bound = [], 0, {}
    while i < len(buf):
        if i in pool or (collect is not None and i in collect):
            out.append('.word'); i += 4; continue
        ins = next(md().disasm(buf[i:i + 4], i), None)
        if ins is None:
            out.append(f'.hword {buf[i:i + 2].hex()}'); i += 2; continue
        mn, op = ins.mnemonic, ins.op_str
        if collect is not None and mn.startswith('ldr') and '[pc' in op:
            mm = re.search(r'#(-?0x[0-9a-f]+|-?\d+)\]', op)
            collect.add(((i + 4) & ~3) + (int(mm.group(1), 0) if mm else 0))
        if collect is not None and mn.startswith('adr'):         # a string or table in the section
            mm = re.search(r'#(-?0x[0-9a-f]+|-?\d+)$', op)
            if mm: collect.add(('adr', ((i + 4) & ~3) + int(mm.group(1), 0)))
        if mn in ('bl', 'blx'): op = '?'
        out.append(f'{mn} {op}'); i += ins.size
        if mn == 'cmp':
            mm = re.match(r'(\w+), #(0x[0-9a-f]+|\d+)$', op)
            if mm: bound[mm.group(1)] = int(mm.group(2), 0)
        elif mn in ('bhi', 'bls') and out[-2].startswith('cmp '):
            r0 = out[-2].split()[1].rstrip(',')
            if r0 in bound: bound[r0] += 1           # bhi to the default: one more entry
        elif mn in ('tbb', 'tbh'):
            reg = op.split(',')[-1].strip().split()[0].rstrip(']')
            n = bound.get(reg, 0) * (1 if mn == 'tbb' else 2)
            n += n & 1
            out.append(f'.table {buf[i:i + n].hex()}'); i += n
    return out


def image_pool(buf):
    """The offsets of a function's data words in the image: the words its own code loads
    pc-relative, and each adr'd block up to the next known word or the end."""
    found = set()
    listing(buf, set(), found)
    words = {x for x in found if isinstance(x, int)}
    adrs = sorted(x[1] for x in found if isinstance(x, tuple))
    pool = set(words)
    for a in adrs:
        nxt = min([w for w in words | set(adrs) if w > a] + [len(buf)])
        pool |= set(range(a & ~3, nxt, 4))
    return pool


def score(got, want):
    """Instructions that differ between two normalised listings (a diff: an insertion costs its
    length, not everything after it)."""
    sm = difflib.SequenceMatcher(None, got, want, autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != 'equal')


REG = re.compile(r'\b(r1[0-2]|r[0-9]|sb|sl|fp|ip|lr)\b')


def structural(lst):
    """A listing with registers, pc-relative offsets, branch targets and .w suffixes masked."""
    out = []
    for x in lst:
        mn, _, ops = x.partition(' ')
        if (mn.startswith('b') and not mn.startswith(('bic', 'bfi', 'bfc'))) or mn in ('cbz', 'cbnz'):
            ops = re.sub(r'#-?0x[0-9a-f]+|#-?\d+', 'L', ops)
        ops = re.sub(r'\[pc, #-?(0x[0-9a-f]+|\d+)\]', '[pc, P]', ops)
        out.append((mn.replace('.w', '') + ' ' + REG.sub('R', ops)).strip())
    return out


def diff_lines(got, want, ctx=1):
    """The differing stretches of two listings, image first, for a report."""
    out = []
    sm = difflib.SequenceMatcher(None, want, got, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal': continue
        out.append(f'--- {tag} image[{i1}:{i2}] build[{j1}:{j2}]')
        out += [f'  image {k:4d}  {want[k]}' for k in range(max(0, i1 - ctx), min(len(want), i2 + ctx))]
        out += [f'  build {k:4d}  {got[k]}' for k in range(max(0, j1 - ctx), min(len(got), j2 + ctx))]
    return out


class Target:
    """FUNC in the image: its address, size, bytes, data words, and the options SRC builds with."""

    def __init__(self, data, opts, addr=0):
        self.addr, self.bytes, self.size, self.opts = addr, bytes(data), len(data), opts
        self.pool, self.want = image_pool(self.bytes), None

    @classmethod
    def from_config(cls, cfg, src, func):
        m = armmap.Map.from_dir(cfg.map)
        with open(src, encoding='latin1') as f: extra = armmap.annotations(f.read())
        addr = m.address(func, extra)
        if addr is None: raise SystemExit(f'{func}: no address (map row, placeholder name or /* at: */ line)')
        row = m.row_at(addr)
        if row is None: raise SystemExit(f'{func}: no map row at 0x{addr:08X}')
        _, opts = cfg.directives(src)
        return cls(ehex.Image(cfg.image).bytes(addr, row.size), opts or cfg.c_opts, addr)

    def adopt(self, got, pool):
        """Take the data-word offsets from a compile of the same size (fromelf marks them exactly,
        where the image's sweep only guesses)."""
        if len(got) == self.size: self.pool, self.want = set(pool), None

    def listing(self):
        if self.want is None: self.want = listing(self.bytes, self.pool)
        return self.want

    def score(self, got, pool, regs=False):
        """FUNC's score for one compile's (bytes, pool), or None when it has no FUNC."""
        if not got: return None
        a, b = listing(got, pool), self.listing()
        if regs: a, b = structural(a), structural(b)
        return score(a, b) + abs(len(got) - self.size) // 2


class Batch:
    """Score variants of one file in batches: one armcc call per batch, in its own work directory."""

    def __init__(self, cfg, target, func, work, prefix=None, runner=None):
        self.target, self.func, self.work = target, func, work
        self.runner = runner or Runner(cfg, prefix)

    def compile(self, texts):
        """[(bytes, pool) or None per text], armcc's output."""
        lst, log = compile_batch(self.runner, texts, self.target.opts, self.work)
        return [func_bytes(x, self.func) if x else None for x in lst], log

    def scores(self, texts, regs=False):
        res, log = self.compile(texts)
        return [self.target.score(*r, regs=regs) if r and r[0] else None for r in res], log

# ---------------------------------------------------------------- pool order


_LOAD = re.compile(r'LDR\S*\s+\w+,\[pc,#-?\d+\]\s*;\s*\[(?:\S+ = )?(0x[0-9a-f]+)\]')
_DCD = re.compile(r'^\s+0x([0-9a-f]{8}):\s+[0-9a-f]{8}\s+.*?DCD\s+(-?\d+)(?:\s*;\s*(\S+))?\s*$')


def pool_order(listing_text, func):
    """(loads, pool) for FUNC in a `fromelf -c` listing: each pc-relative load in code order and
    each pool word in pool order, named by the word's symbol, or '+N' for a word with no symbol
    (an address in the file's own data section, or a constant)."""
    addrs, pool, on = [], {}, False
    for l in listing_text.splitlines():
        if l.startswith('** Section'):
            on = f"'i.{func}'" in l; continue
        if not on: continue
        m = _LOAD.search(l)
        if m: addrs.append(int(m.group(1), 16)); continue
        m = _DCD.match(l)
        if m: pool[int(m.group(1), 16)] = m.group(3) or f'+{m.group(2)}'
    return [pool.get(a, f'0x{a:x}') for a in addrs], list(pool.values())

# ---------------------------------------------------------------- command line


def main(argv):
    import config
    path, a = config.pop_config(argv)
    if len(a) < 3 or a[0] not in ('score', 'pool'): sys.exit(__doc__)
    cfg = config.load(path)
    cmd, src, func = a[0], os.path.abspath(a[1]), a[2]
    work = os.path.join(cfg.build, 'variant', func)
    runner = Runner(cfg)
    with open(src, encoding='latin1') as f: text = f.read()
    if cmd == 'pool':
        _, opts = cfg.directives(src)
        lst, log = compile_batch(runner, [text], opts or cfg.c_opts, work)
        if not lst[0]: sys.exit(log or 'compile failed')
        loads, pool = pool_order(lst[0], func)
        print('loads ' + ' '.join(loads)); print('pool  ' + ' '.join(f'[{p}]' for p in pool))
        return 0
    t = Target.from_config(cfg, src, func)
    b = Batch(cfg, t, func, work, runner=runner)
    (r,), log = b.compile([text])
    if not r or not r[0]: sys.exit(log or f'no section i.{func} in the object')
    t.adopt(*r)
    print(f'{func}: score {t.score(*r)} (0x{len(r[0]):X} bytes, image 0x{t.size:X})')
    if '--regs' in a: print(f'{func}: structural score {t.score(*r, regs=True)}')
    if '--diff' in a: print('\n'.join(diff_lines(listing(*r), t.listing())))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
