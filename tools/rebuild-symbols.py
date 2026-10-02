"""Rebuild symbols.tsv from scratch from every source with a target line.

    python3 tools/rebuild-symbols.py [--config PATH]      (build them all first: build.py --all)

Use it after any rename: a name changed in one file and left in symbols.tsv makes the next
merge refuse a conflict, or worse, keeps two names for one address. Names marked 'library'
by hand keep that mark. The old file is kept as symbols.tsv.bak.

Sources are merged overlays first and then by target, and the ones that fail are tried again
after the rest until a round adds nothing: a file can need a name another file merges first
(a far address it refers to by its offset alone, data placed only by its publics), so a
fixed order in which every file passes may not exist once files move between directories.
The gate (tools/gate.py check) does the same into a scratch file and compares. If any
source still fails, symbols.tsv is left as it was."""
import sys, os, shutil
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config
from gate import Gate


def main(argv):
    path, a = config.pop_config(argv); cfg = config.load(path)
    g = Gate(cfg)
    srcs = [s for _, s, _, _ in g.sources() if os.path.exists(cfg.obj(s))]
    keep = {n: (v, l) for n, (v, l) in cfg.load_symbols().items() if l == 'library'}
    if os.path.exists(cfg.symbols): shutil.copyfile(cfg.symbols, cfg.symbols + '.bak')
    tmp = cfg.symbols + '.new'
    failed = g.rebuild_symbols(tmp, srcs, keep)
    if failed:
        os.remove(tmp)
        print('verify --update fails for: ' + ' '.join(os.path.relpath(s, cfg.root) for s in failed))
        print('run verify.py on each to see why; symbols.tsv is unchanged')
        return 1
    os.replace(tmp, cfg.symbols)
    print(f'symbols.tsv rebuilt from {len(srcs)} sources: {len(cfg.load_symbols())} names')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
