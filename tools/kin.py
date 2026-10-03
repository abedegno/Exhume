"""Find the functions two builds of one family share: a new target and a kin whose sources are
already matched (for UW1, UW2: the same compiler, libraries and engine, a year apart).

    python3 tools/kin.py --kin KIN_CONFIG [--out FILE] [--min 0.6] [--functions]

Both projects need their map (doslist.py and locate.py: map/dos_procs.tsv, map/segments.tsv).
Each procedure of each build is decoded (16-bit) and normalised: far pointers and segment
values (relocated, so different in every build), near branch and call targets, and direct
global addresses ([disp], or [reg+disp] with disp >= 200h) are masked; registers, stack
offsets ([bp+n]), structure offsets and constants are kept. A target procedure whose
normalised instructions equal a kin procedure's is `same` (the kin's C is the target's,
names and data addresses aside); otherwise the best kin procedure by shared instruction
triples, scored by difflib's ratio over the two sequences, is `near` (>= 0.9) or `like`.
The kin's source file for each kin procedure comes from its sources' `/* target: SEG */`
lines (that file implements that segment).

Writes FILE (default <map>/kin.tsv): target segment, proc, offset, size, kind, score, kin
segment, kin proc, kin source; and prints a summary per target segment: how many of its
bytes are `same` or `near`, and the kin file that supplies most of them, which is the
file to seed the target segment's source from (skills/map-binary, "Seeding from kin").

--functions also writes <map>/functions.tsv, which targets.py reads, for a target with no
symbol-bearing sibling: each `same` or `near` procedure of six or more instructions, whose
`same` kin is unique, gets its kin's name (its target tables' cname, else its functions.tsv),
with how `kin-same` or `kin-near`. (On UW1 a 7-byte `return 0` was `same` as a COMBINE.C
function and was named after it.) A procedure much larger than any
kin's (an IDA proc running on over data) is reported, not paired."""
import sys, os, re, difflib, collections
from iced_x86 import Decoder, Formatter, FormatterSyntax, OpKind, Register
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config


def args():
    a = sys.argv[1:]
    path, a = config.pop_config(a)
    kin = out = None; mn = 0.6; fn = False
    while a:
        k = a.pop(0)
        if k == '--kin': kin = a.pop(0)
        elif k == '--functions': fn = True
        elif k == '--out': out = a.pop(0)
        elif k == '--min': mn = float(a.pop(0))
        else: sys.exit(__doc__)
    if not kin: sys.exit(__doc__)
    return config.load(path), config.load(kin), out, mn, fn


def procs(cfg):
    """[(seg, name, file offset, size)] for the located segments."""
    segs = {}
    for l in open(os.path.join(cfg.map, 'segments.tsv')):
        if l.startswith('#'): continue
        f = l.rstrip('\n').split('\t')
        if len(f) > 1 and f[1] not in ('None', ''): segs[f[0]] = int(f[1], 16)
    out = []
    for l in open(os.path.join(cfg.map, 'dos_procs.tsv')):
        s, n, o, z = l.rstrip('\n').split('\t')[:4]
        if s in segs and int(z, 16) > 0: out.append((s, n, segs[s] + int(o, 16), int(z, 16)))
    return out


FMT = Formatter(FormatterSyntax.MASM)


def norm(exe, off, size):
    toks = []
    for ins in Decoder(16, exe[off:off + size], ip=0):
        if ins.is_invalid: toks.append('?'); continue
        m = ins.mnemonic
        ops = []
        for i in range(ins.op_count):
            k = ins.op_kind(i)
            if k == OpKind.REGISTER: ops.append(Register(ins.op_register(i)).name if False else str(ins.op_register(i)))
            elif k in (OpKind.NEAR_BRANCH16, OpKind.NEAR_BRANCH32, OpKind.NEAR_BRANCH64): ops.append('NEAR')
            elif k == OpKind.FAR_BRANCH16: ops.append('FAR')
            elif k == OpKind.MEMORY:
                b, x, d = ins.memory_base, ins.memory_index, ins.memory_displacement & 0xFFFF
                seg = ins.segment_prefix
                if b == Register.NONE and x == Register.NONE: ops.append(f'[{seg}:G]')
                elif b == Register.BP: ops.append(f'[{seg}:bp+{x}+{d:x}]')
                else: ops.append(f'[{seg}:{b}+{x}+' + ('G' if d >= 0x200 else f'{d:x}') + ']')
            elif k in (OpKind.IMMEDIATE8, OpKind.IMMEDIATE8TO16, OpKind.IMMEDIATE16, OpKind.IMMEDIATE8_2ND):
                v = ins.immediate(i) & 0xFFFF
                ops.append(f'{v:x}')
            else: ops.append(str(k))
        toks.append(f'{m}:' + ','.join(ops))
    return toks


def kin_files(cfg):
    """kin segment -> its source file (from the /* target: */ lines)."""
    m = {}
    for d, _, fs in os.walk(cfg.src):
        for f in fs:
            if not f.upper().endswith(('.C', '.ASM')): continue
            p = os.path.join(d, f)
            t = re.search(r'/\*\s*target:\s*(\S+)\s*\*/', open(p, encoding='latin1').read(4000))
            if t: m[t.group(1)] = os.path.relpath(p, cfg.root)
            t = re.search(r';\s*target:\s*(\S+)', open(p, encoding='latin1').read(4000))
            if t and t.group(1) not in m: m[t.group(1)] = os.path.relpath(p, cfg.root)
    return m


def main():
    cfg, kcfg, out, mn, fn = args()
    exe, kexe = cfg.exe, kcfg.exe
    tp, kp = procs(cfg), procs(kcfg)
    tn = [norm(exe, o, z) for _, _, o, z in tp]
    kn = [norm(kexe, o, z) for _, _, o, z in kp]
    files = kin_files(kcfg)
    exact = collections.defaultdict(list)
    for i, t in enumerate(kn):
        if len(t) >= 3: exact[tuple(t)].append(i)
    tri = collections.defaultdict(set)
    for i, t in enumerate(kn):
        for j in range(len(t) - 2): tri[(t[j], t[j + 1], t[j + 2])].add(i)
    rows = []
    for (s, n, o, z), t in zip(tp, tn):
        best = None
        if tuple(t) in exact:
            best = ('same', 1.0, exact[tuple(t)][0])
        elif len(t) >= 3:
            c = collections.Counter()
            for j in range(len(t) - 2):
                for i in tri.get((t[j], t[j + 1], t[j + 2]), ()): c[i] += 1
            for i, _ in c.most_common(5):
                r = difflib.SequenceMatcher(None, t, kn[i], autojunk=False).ratio()
                if best is None or r > best[1]: best = ('near' if r >= 0.9 else 'like', r, i)
        if best and best[1] >= mn:
            ks, kname = kp[best[2]][0], kp[best[2]][1]
            rows.append((s, n, o, z, best[0], best[1], ks, kname, files.get(ks, '')))
        else:
            rows.append((s, n, o, z, 'none', 0.0, '', '', ''))
    out = out or os.path.join(cfg.map, 'kin.tsv')
    with open(out, 'w') as f:
        f.write('# segment\tproc\tfile offset\tsize\tkind\tscore\tkin segment\tkin proc\tkin source\n')
        for r in rows:
            f.write(f'{r[0]}\t{r[1]}\t{r[2]:X}\t{r[3]:X}\t{r[4]}\t{r[5]:.3f}\t{r[6]}\t{r[7]}\t{r[8]}\n')
    by = collections.OrderedDict()
    for r in rows: by.setdefault(r[0], []).append(r)
    tot = collections.Counter()
    print(f'{"segment":14} {"bytes":>7} {"same":>6} {"near":>6} {"like":>6}  kin file (share of same+near bytes)')
    for s, rs in by.items():
        b = sum(r[3] for r in rs)
        k = collections.Counter()
        for r in rs: tot[r[4]] += r[3]; k[r[4]] += r[3]
        src = collections.Counter()
        for r in rs:
            if r[4] in ('same', 'near'): src[r[8] or r[6]] += r[3]
        top = src.most_common(1)
        tops = f'{top[0][0]} ({top[0][1] * 100 // max(1, k["same"] + k["near"])}%)' if top else ''
        print(f'{s:14} {b:7} {k["same"] * 100 // max(1, b):5}% {k["near"] * 100 // max(1, b):5}% {k["like"] * 100 // max(1, b):5}%  {tops}')
    big = [r for r in rows if r[3] > 0x8000]
    for r in big: print(f'  {r[0]} {r[1]}: {r[3]:#x} bytes, an IDA proc that runs on over data or other code; check its extent')
    if fn:
        # the kin's names: its target tables' cname column (what its matched C calls each
        # function), then its functions.tsv's original names
        kf = {}
        for l in open(os.path.join(kcfg.map, 'functions.tsv')):
            if l.startswith('#'): continue
            f = l.rstrip('\n').split('\t')
            # only names the kin's map anchored or confirmed: its candidates ("size only",
            # "calls disagree") were names it rejected (on UW1 they named a console printer
            # free_speech_stuff and _ld_close free_timers)
            if len(f) > 5 and f[4] and f[5] in ('anchor', 'confirmed'): kf[(f[0], f[1])] = f[4].rstrip('_')
        for t in os.listdir(kcfg.targets):
            if not t.endswith('.tsv'): continue
            seg = t[:-4]
            for l in open(os.path.join(kcfg.targets, t)):
                if l.startswith('#'): continue
                f = l.rstrip('\n').split('\t')
                if len(f) > 1 and f[0] and f[0] != f[1]: kf[(seg, f[1])] = f[0]
        with open(os.path.join(cfg.map, 'functions.tsv'), 'w') as f:
            f.write('# segment\tida_name\toffset\tsize\toriginal_name\thow\tcalls_agree\tcalls_checkable\n')
            segbase = {}
            for l in open(os.path.join(cfg.map, 'segments.tsv')):
                if not l.startswith('#'):
                    x = l.rstrip('\n').split('\t')
                    if len(x) > 1 and x[1] not in ('None', ''): segbase[x[0]] = int(x[1], 16)
            # a short body (a `return 0`, an empty function) is the same as many kin functions,
            # so its pairing names nothing: only a unique `same` or a `near` of at least six
            # instructions passes a name on
            for r, t in zip(rows, tn):
                ok = r[4] == 'near' or (r[4] == 'same' and len(exact.get(tuple(t), ())) == 1)
                name = kf.get((r[6], r[7]), '') if ok and len(t) >= 6 else ''
                f.write(f'{r[0]}\t{r[1]}\t{r[2] - segbase[r[0]]:X}\t{r[3]:X}\t{name}\t{"kin-" + r[4] if name else ""}\t0\t0\n')
    T = sum(tot.values())
    print(f'all: {T} bytes in {len(rows)} procs: same {tot["same"] * 100 // T}%, near {tot["near"] * 100 // T}%, '
          f'like {tot["like"] * 100 // T}%, none {tot["none"] * 100 // T}%; written to {out}')


if __name__ == '__main__':
    main()
