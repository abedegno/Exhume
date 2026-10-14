"""Find instruction runs the image repeats: inlined helpers, macros, copied code.

    python3 armrepeats.py [--config PATH] [--min N] [--top N] [--func NAME] [--kinds code,lib]

Every function row of the map (kinds given by --kinds, default code) is disassembled from the
image, literal-pool words skipped (armvariant.py's sweep), and each instruction reduced to its
shape: registers become R, branch targets and pc-relative offsets are dropped, immediates and
field offsets are kept. Runs of at least --min (default 8) shapes found at two or more places
are grouped, extended as far as they stay equal, and printed longest and most frequent first,
with every place (function, offset, source file). --func prints only the groups touching one
function.

A small static function or macro of the original source, inlined by armcc, shows as the same
instructions at several call sites. An unmatched function holding such a run probably called
the helper rather than spelling the code out, and writing it as a `static __inline` function
moves the registers around it (compiler.md, "Inline helpers")."""
import collections, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import armvariant as V

REG = re.compile(r'\b(r\d+|sb|sl|fp|ip|lr|sp)\b')


def shape(ins):
    """An instruction's shape: registers masked, branch targets and pc-relative offsets dropped."""
    mn, _, op = ins.partition(' ')
    if mn.startswith(('b', 'cb')) and not mn.startswith(('bic', 'bfi', 'bfc')):
        return mn
    if '[pc' in op: op = re.sub(r'#-?(0x[0-9a-f]+|\d+)', '#?', op)
    if mn.split('.')[0] in ('push', 'pop', 'stm', 'ldm', 'stmdb', 'ldmia'): op = '{..}'
    return (mn + ' ' + REG.sub('R', op)).strip()


def shapes(buf):
    """[(offset, shape)] of a function's bytes, its data words skipped."""
    pool = V.image_pool(buf)
    out, i = [], 0
    while i < len(buf):
        if i in pool: i += 4; continue
        ins = next(V.md().disasm(buf[i:i + 4], i), None)
        if ins is None: i += 2; continue
        out.append((i, shape(f'{ins.mnemonic} {ins.op_str}'.strip())))
        i += ins.size
    return out


def groups(seqs, mn):
    """{(first k-gram, length): [(name, index)]} of the maximal runs of at least mn shapes that
    occur at two or more places. seqs: {name: [(offset, shape)]}."""
    idx = collections.defaultdict(list)
    for name, s in seqs.items():
        sh = [x for _, x in s]
        for j in range(len(sh) - mn + 1): idx[tuple(sh[j:j + mn])].append((name, j))
    out = {}
    for key, places in idx.items():
        if len(places) < 2: continue
        n = mn
        while True:                       # extend while every place still agrees
            nxt = {seqs[name][j + n][1] if j + n < len(seqs[name]) else None for name, j in places}
            if len(nxt) != 1 or None in nxt: break
            n += 1
        # a tail of a longer shared run (the shapes before are equal too) is not a group
        prev = {seqs[name][j - 1][1] if j > 0 else None for name, j in places}
        if len(prev) == 1 and None not in prev: continue
        out[(key, n)] = places
    return out


def main(argv):
    sys.path.insert(0, os.path.join(V.EXHUME, 'tools'))
    import config, armmap, ehex
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    mn = int(a[a.index('--min') + 1]) if '--min' in a else 8
    top = int(a[a.index('--top') + 1]) if '--top' in a else 40
    only = a[a.index('--func') + 1] if '--func' in a else None
    kinds = set((a[a.index('--kinds') + 1] if '--kinds' in a else 'code').split(','))
    img = ehex.Image(cfg.image)
    seqs, files = {}, {}
    for r in armmap.load(os.path.join(cfg.map, 'functions.tsv')):
        if r.kind not in kinds or r.size < 8: continue
        seqs[r.name] = shapes(img.bytes(r.addr, r.size))
        files[r.name] = r.file or '(unmerged)'
    ranked = sorted(groups(seqs, mn).items(), key=lambda kv: (-kv[0][1] * len(kv[1]), -kv[0][1]))
    shown = 0
    for (key, n), places in ranked:
        if only and not any(nm == only for nm, _ in places): continue
        print(f'== {n} instructions at {len(places)} places in {len({nm for nm, _ in places})} functions')
        for name, j in places[:12]:
            print(f'   {name:28s} +0x{seqs[name][j][0]:03X}  {files[name]}')
        if len(places) > 12: print(f'   ... {len(places) - 12} more')
        s0 = seqs[places[0][0]]
        print('   ' + ' ; '.join(x for _, x in s0[places[0][1]:places[0][1] + min(n, 14)]) + (' ...' if n > 14 else ''))
        shown += 1
        if shown >= top: break
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
