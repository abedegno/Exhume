"""Reduce a search result to the edits that matter.

    python3 armreduce.py [--config PATH] ORIG.c BEST.c OUT.c FUNC [--match]

Splits the line diff ORIG -> BEST into hunks and tries dropping each in turn, keeping a drop
when FUNC still scores no worse than BEST does (armvariant.py: compile only), or, with --match,
when match.py still reports FUNC as MATCH with OUT.c (a link per try: slower, and the proof).
OUT.c ends holding the reduced source, and the hunks that stayed are printed. A permuter or
armbrute.py hit usually carries edits that do nothing; what is left is what to understand
(compiler.md: two changes far apart, each useless alone, are common)."""
import difflib, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)


def hunks(a, b):
    """The non-equal opcodes of a line diff of a and b (lists of lines)."""
    return [o for o in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if o[0] != 'equal']


def apply(a, b, ops, keep):
    """a with the hunks in keep taken from b."""
    out, i = [], 0
    for k, (_, i1, i2, j1, j2) in enumerate(ops):
        out += a[i:i1]
        out += b[j1:j2] if k in keep else a[i1:i2]
        i = i2
    return out + a[i:]


def reduce(a, b, ok):
    """The smallest set of hunk indices (one pass, each hunk dropped in turn) that ok() accepts.
    ok takes the lines of a candidate. Returns (ops, keep), or (ops, None) if b itself fails."""
    ops = hunks(a, b)
    keep = set(range(len(ops)))
    if not ok(apply(a, b, ops, keep)): return ops, None
    for k in range(len(ops)):
        if ok(apply(a, b, ops, keep - {k})): keep -= {k}
    return ops, keep


def main(argv):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), 'tools'))
    import config
    path, args = config.pop_config(argv)
    if len(args) < 4: sys.exit(__doc__)
    cfg = config.load(path)
    orig, best, dest, func = args[:4]
    with open(orig, encoding='latin1') as f: a = f.read().split('\n')
    with open(best, encoding='latin1') as f: b = f.read().split('\n')

    def write(lines):
        with open(dest, 'w', encoding='latin1') as f: f.write('\n'.join(lines))

    if '--match' in args:
        import armmatch

        def ok(lines):
            write(lines)
            r = armmatch.match(cfg, dest)
            return any(l.split()[:1] == [func] and l.rstrip().endswith('MATCH') or
                       (l.split()[:1] == [func] and 'MATCH (+' in l) for l in r.lines)
    else:
        import armvariant as V
        t = V.Target.from_config(cfg, best, func)
        batch = V.Batch(cfg, t, func, os.path.join(cfg.build, 'reduce', func))
        (r,), log = batch.compile(['\n'.join(b)])
        if not r or not r[0]: sys.exit('BEST does not compile:\n' + log[-2000:])
        t.adopt(*r)
        limit = t.score(*r)
        print(f'{func}: BEST scores {limit}')

        def ok(lines):
            s = batch.scores(['\n'.join(lines)])[0][0]
            return s is not None and s <= limit
    ops, keep = reduce(a, b, ok)
    if keep is None: sys.exit('BEST does not pass')
    write(apply(a, b, ops, keep))
    print(f'{len(keep)} of {len(ops)} hunks needed:')
    for k in sorted(keep):
        _, i1, i2, j1, j2 = ops[k]
        print('  -', '\n    '.join(a[i1:i2]) or '(nothing)'); print('  +', '\n    '.join(b[j1:j2]) or '(nothing)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
