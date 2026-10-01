"""Rebuild symbols.tsv from scratch from every source with a target line.

    python3 tools/rebuild-symbols.py [--config PATH]      (build them all first: build.py --all)

Use it after any rename: a name changed in one file and left in symbols.tsv makes the next
merge refuse a conflict, or worse, keeps two names for one address. Names marked 'library'
by hand keep that mark. The old file is kept as symbols.tsv.bak. Stops at the first file
whose verify reports a problem, leaving the old file in place."""
import sys, os, glob, shutil, subprocess, re
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config

def main(argv):
    path, a = config.pop_config(argv); cfg = config.load(path)
    srcs = [p for p in sorted(glob.glob(os.path.join(cfg.src, '*.C')) + glob.glob(os.path.join(cfg.src, '*.ASM')))
            if cfg.directives(p)[0] and os.path.exists(cfg.obj(p))]
    keep = {n: v for n, (v, l) in cfg.load_symbols().items() if l == 'library'}
    if os.path.exists(cfg.symbols): shutil.copyfile(cfg.symbols, cfg.symbols + '.bak')
    with open(cfg.symbols, 'w') as f:
        f.write('# name\taddress\tname source\n')
        for n, v in sorted(keep.items()): f.write(f'{n}\t{v}\tlibrary\n')
    for src in srcs:
        r = subprocess.run([sys.executable, os.path.join(here, 'verify.py'), src, '--update', '--config', cfg.file],
                           capture_output=True, text=True)
        if r.returncode:
            shutil.copyfile(cfg.symbols + '.bak', cfg.symbols)
            print(f'{src}:\n' + '\n'.join(l for l in r.stdout.splitlines() if l.startswith('PROBLEM')))
            return 1
    print(f'symbols.tsv rebuilt from {len(srcs)} sources: {len(cfg.load_symbols())} names')
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
