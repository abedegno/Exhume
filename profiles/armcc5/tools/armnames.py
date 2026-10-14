"""Rename functions and globals across an armcc tree without changing the image, and check the
name order armlink's layout demands.

    python3 armnames.py [--config PATH] check [--from ADDR] [--to ADDR]
    python3 armnames.py [--config PATH] gaps [--from ADDR] [--to ADDR]
    python3 armnames.py [--config PATH] rename OLD NEW BASIS [NOTE] [--from ADDR] [--to ADDR] [-n]
    python3 armnames.py [--config PATH] rename --file RENAMES.tsv [--from ADDR] [--to ADDR] [-n]

armlink lays out the sections of an execution region in ASCII order of section name, and with
--split_sections a function's section is i.<name>: so where the original was laid out that way,
the function names sort in address order, and a new name must sort between its neighbours'
(compiler.md, "Names decide the layout"). Placeholders (sub_ADDR) do not sort like the names
they stand for; each counts as the last real name before it plus `_ADDR`, which sorts between
its neighbours whatever the real name was. --from and --to limit the check to the address
range laid out by name (a startup file's code and assembler areas usually are not); the default
is every function row of the map.

  check   list every pair of neighbours out of order
  gaps    each placeholder function with the real names that bound its gap (`after`, `before`),
          as a tab-separated table: the input for naming a range (skills/readability-pass)
  rename  rename whole words in every source and header under [project] src and include and in
          map/functions.tsv and map/data.tsv, refusing a function name that breaks the order, a
          name already in the map and anything that is not an identifier. Appends one row per
          name to map/names.tsv: address, kind, old, new, basis, note. BASIS says how much is
          known: `original` (the name the original had, shown by evidence: a library's own
          source, a string, a sibling build), `likely` (the job and the place in the order make
          a conventional name all but certain), `descriptive` (a name for the job, chosen to fit
          the order). -n reports without writing.

Globals have no order rule (data is laid out per file, by size or definition order). Run the
whole-image gate after every rename: a name is also a symbol other files link against."""
import dataclasses, glob, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import armmap

BASES = ('original', 'likely', 'descriptive')
IDENT = re.compile(r'^[A-Za-z_][A-Za-z0-9_]*$')


def in_range(r, lo, hi):
    return (lo is None or r.addr >= lo) and (hi is None or r.addr < hi) and r.kind != 'asm'


def link_names(functions, lo=None, hi=None):
    """[(row, the name its section sorts by)] in address order: a placeholder sorts as the last
    real name before it, '_' and its address."""
    out, prev = [], ''
    for r in sorted(functions, key=lambda r: r.addr):
        if not in_range(r, lo, hi): continue
        if armmap.placeholder(r.name) is not None:
            out.append((r, f'{prev}_{r.addr:08X}' if prev else f'{r.addr:08X}'))
        else:
            prev = r.name; out.append((r, r.name))
    return out


def order_problems(functions, lo=None, hi=None):
    """[(row a, row b)] for neighbours whose names do not sort in address order."""
    ln = link_names(functions, lo, hi)
    return [(a, b) for (a, x), (b, y) in zip(ln, ln[1:]) if x >= y]


def gaps(functions, lo=None, hi=None):
    """[(row, after, before)] for each placeholder: the real names bounding its gap."""
    rows = [r for r in sorted(functions, key=lambda r: r.addr) if in_range(r, lo, hi)]
    out = []
    for i, r in enumerate(rows):
        if armmap.placeholder(r.name) is None: continue
        after = next((x.name for x in reversed(rows[:i]) if armmap.placeholder(x.name) is None), '')
        before = next((x.name for x in rows[i + 1:] if armmap.placeholder(x.name) is None), '')
        out.append((r, after, before))
    return out


def tree_files(cfg):
    """Sources and headers under [project] src and include, and the map's two tables."""
    out = set()
    for d in {cfg.src, getattr(cfg, 'include', None) or cfg.src}:
        for ext in ('c', 'h', 's'):
            out |= set(glob.glob(os.path.join(d, '**', f'*.{ext}'), recursive=True))
    return sorted(out) + [os.path.join(cfg.map, 'functions.tsv'), os.path.join(cfg.map, 'data.tsv')]


def plan(m, pairs, lo=None, hi=None):
    """An error message, or '' when every (old, new, basis, note) may go ahead."""
    fnames = {r.name for r in m.functions}
    taken = fnames | {r.name for r in m.data}
    news = [n for _, n, _, _ in pairs]
    for old, new, basis, _ in pairs:
        if basis not in BASES: return f'{old}: basis must be one of {", ".join(BASES)}'
        if not IDENT.match(new): return f'{new}: not an identifier'
        if old not in taken: return f'{old}: not in the map'
        if new in taken and new != old: return f'{new}: already a name in the map'
        if news.count(new) > 1: return f'{new}: given twice'
    newname = {o: n for o, n, _, _ in pairs}
    after = [dataclasses.replace(r, name=newname.get(r.name, r.name)) for r in m.functions]
    bad = order_problems(after, lo, hi)
    before = {(a.addr, b.addr) for a, b in order_problems(m.functions, lo, hi)}
    bad = [(a, b) for a, b in bad if (a.addr, b.addr) not in before]      # only problems this makes
    if bad:
        return 'order: ' + '; '.join(f'{a.name} (0x{a.addr:08X}) does not sort before {b.name} (0x{b.addr:08X})'
                                     for a, b in bad[:5])
    return ''


def rename(cfg, pairs, lo=None, hi=None, write=True):
    """Rename and log; returns an error message or ''."""
    m = armmap.Map.from_dir(cfg.map)
    err = plan(m, pairs, lo, hi)
    if err: return err
    pats = [(re.compile(r'\b' + re.escape(o) + r'\b'), n) for o, n, _, _ in pairs]
    changed = []
    for f in tree_files(cfg):
        if not os.path.exists(f): continue
        with open(f, encoding='latin1') as fh: text = fh.read()
        new = text
        for p, n in pats: new = p.sub(n, new)
        if new != text:
            changed.append(f)
            if write:
                with open(f, 'w', encoding='latin1') as fh: fh.write(new)
    rows = {r.name: ('data', r) for r in m.data}
    rows.update({r.name: ('function', r) for r in m.functions})
    if write:
        log = os.path.join(cfg.map, 'names.tsv')
        fresh = not os.path.exists(log)
        with open(log, 'a') as fh:
            if fresh: fh.write('addr\tkind\told\tnew\tbasis\tnote\n')
            for o, n, b, note in pairs:
                kind, r = rows[o]
                fh.write(f'{r.addr:08X}\t{kind}\t{o}\t{n}\t{b}\t{note}\n')
    print(f'{"renamed" if write else "would rename"} {len(pairs)} names in {len(changed)} files')
    return ''


def read_pairs(path):
    pairs = []
    with open(path) as f:
        for line in f:
            if not line.strip() or line.startswith('#'): continue
            x = line.rstrip('\n').split('\t') + ['', '']
            pairs.append((x[0], x[1], x[2], x[3]))
    return pairs


def main(argv):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), 'tools'))
    import config
    path, a = config.pop_config(argv)
    if not a: sys.exit(__doc__)
    cfg = config.load(path)
    lo = int(a[a.index('--from') + 1], 16) if '--from' in a else None
    hi = int(a[a.index('--to') + 1], 16) if '--to' in a else None
    for opt in ('--from', '--to'):
        if opt in a: del a[a.index(opt):a.index(opt) + 2]
    m = armmap.Map.from_dir(cfg.map)
    if a[0] == 'check':
        bad = order_problems(m.functions, lo, hi)
        for x, y in bad: print(f'0x{x.addr:08X} {x.name}  >=  0x{y.addr:08X} {y.name}')
        print(f'{len(bad)} pairs out of order')
        return 1 if bad else 0
    if a[0] == 'gaps':
        print('addr\tsize\tplaceholder\tfile\tafter\tbefore')
        for r, after, before in gaps(m.functions, lo, hi):
            print(f'{r.addr:08X}\t{r.size:X}\t{r.name}\t{r.file}\tafter={after}\tbefore={before}')
        return 0
    if a[0] == 'rename':
        write = '-n' not in a
        a = [x for x in a[1:] if x != '-n']
        if a and a[0] == '--file': pairs = read_pairs(a[1])
        elif len(a) >= 3: pairs = [(a[0], a[1], a[2], a[3] if len(a) > 3 else '')]
        else: sys.exit(__doc__)
        err = rename(cfg, pairs, lo, hi, write)
        if err: sys.exit('rename refused: ' + err)
        return 0
    sys.exit(__doc__)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
