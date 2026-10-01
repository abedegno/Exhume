"""Accept a source file: the gates every file passes before it counts as matched.

    python3 tools/merge.py SRC [--config PATH]

1. match.py builds it and must report WHOLE SEGMENT MATCHES (a fresh build, never a stale
   object);
2. verify.py must report no problems (fixups, data, BSS, far data, names);
3. verify.py --update merges its names into symbols.tsv, and must again report none;
4. its target goes into matched.txt, and <map>/files.tsv is refreshed when the map exists.

Exits non-zero, changing nothing, at the first failure, and prints why. Run merges one at a
time: symbols.tsv and matched.txt are shared. After renaming anything, rebuild symbols.tsv
from every matched file (rebuild-symbols.py) rather than patching it."""
import sys, os, subprocess
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config

def run(tool, args):
    r = subprocess.run([sys.executable, os.path.join(here, tool)] + args, capture_output=True, text=True)
    return r.returncode, r.stdout + r.stderr

def main(argv):
    path, a = config.pop_config(argv); cfg = config.load(path)
    if not a: sys.exit(__doc__)
    src = a[0]; seg = cfg.directives(src)[0]
    extra = ['--config', cfg.file]
    rc, out = run('match.py', [src] + extra)
    last = out.strip().splitlines()[-1] if out.strip() else ''
    print(last)
    if rc or 'WHOLE SEGMENT MATCHES' not in last:
        print(out if rc else 'not a whole-segment match'); return 1
    rc, out = run('verify.py', [src] + extra)
    if rc:
        print('\n'.join(l for l in out.splitlines() if l.startswith('PROBLEM') or l.startswith('--'))); return 1
    rc, out = run('verify.py', [src, '--update'] + extra)
    if rc:
        print('\n'.join(l for l in out.splitlines() if l.startswith('PROBLEM') or l.startswith('--') or 'not updated' in l)); return 1
    print(out.strip().splitlines()[-1])
    if seg not in cfg.matched_set():
        with open(cfg.matched, 'a') as f: f.write(seg + '\n')
    if os.path.exists(os.path.join(cfg.map, 'functions.tsv')):
        rc, out = run('files.py', extra)
        print('\n'.join(l for l in out.splitlines() if 'matched bytes' in l))
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
