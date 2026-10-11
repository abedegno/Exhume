"""Accept a source for a linked image (the armcc5 profile's merge.py).

The file must match whole (armmatch). Then the names it defines replace placeholders in the
map's TSVs at their addresses, with the file recorded, and the file goes into matched.txt.
Run merges one at a time: the TSVs and matched.txt are shared."""
import os, sys
import armmap, armmatch


def merge(cfg, src, matcher=None):
    r = (matcher or armmatch.match)(cfg, src)
    if not r.whole:
        return False, r.lines + ['-- not merged: not a whole-file match']
    rel = os.path.relpath(os.path.abspath(src), cfg.root)
    fpath, dpath = os.path.join(cfg.map, 'functions.tsv'), os.path.join(cfg.map, 'data.tsv')
    functions, data = armmap.load(fpath), armmap.load(dpath)
    renamed = 0
    for name, addr in r.defined.items():
        row = next((x for x in functions + data if x.addr == addr), None)
        if row is None: continue
        if armmap.placeholder(row.name) is not None and row.name != name:
            row.name = name; renamed += 1
        row.file = rel
    armmap.save(fpath, functions); armmap.save(dpath, data)
    done = open(cfg.matched).read().split() if os.path.exists(cfg.matched) else []
    if rel not in done:
        with open(cfg.matched, 'a') as f: f.write(rel + '\n')
    return True, [r.lines[-1], f'-- merged {rel}: {renamed} names into the map']


def main(cfg, argv):
    if not argv: sys.exit(__doc__)
    ok, lines = merge(cfg, argv[0])
    print('\n'.join(lines))
    return 0 if ok else 1
