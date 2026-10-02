"""Count the places a source reads a struct through a cast pointer and a number.

    python3 tools/rawoffsets.py [--config PATH] [--ref GITREF] [-v] [SRC ...]

Matching often leaves code like `*(unsigned far *)((char far *)obj + 0x16)` or
`((unsigned char *)&rec)[0x17]`: the bytes were right before anyone knew the field. Step 4 of
skills/readability-pass replaces them with fields (`obj->home`), and this is its measure.
Lines that are wholly comments are skipped. The patterns:

    *(T *)(expr)                  a scalar read through a cast of an address expression
    ((T *)p)[N]                   indexing a cast pointer
    (char *)p + N                 byte arithmetic on a cast pointer
    ((struct X *)((char *)p ...   a struct view at a byte offset

T is any scalar type, with far, near or huge. Not every hit is a missing field: some code
must keep its cast because the original computed the address that way (a different scaling,
a different base pointer; skills/readability-pass lists the cases), so the count falls to a
floor, not to zero. --ref compares with the same files at a git revision (by path relative to
the repository), so a step can report "96 -> 12". -v lists every site.
"""
import os, re, sys, subprocess
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config, sources

T = r'(?:unsigned |signed )?(?:char|int|long|short|unsigned)'
PATS = [
    re.compile(r'\*\s*\(\s*' + T + r'\s*(?:far|near|huge)?\s*\*\s*\)\s*\('),
    re.compile(r'\(\s*\(\s*' + T + r'\s*(?:far|near|huge)?\s*\*\s*\)\s*&?[\w.>\-\[\]]+\s*\)\s*\['),
    re.compile(r'\(\s*(?:unsigned |signed )?char\s*(?:far|near|huge)?\s*\*\s*\)\s*&?[\w.>\-\[\]]+\s*[+-]\s*(?:0x[0-9A-Fa-f]+|\d+)\b'),
    re.compile(r'\(\s*\(\s*struct \w+\s*(?:far|near)?\s*\*\s*\)\s*\(\s*\(\s*(?:unsigned )?char'),
]


def scan_text(text, name):
    out = []
    for i, l in enumerate(text.split('\n'), 1):
        s = l.split('//')[0]
        if s.strip().startswith(('/*', '*')): continue
        if any(p.search(s) for p in PATS): out.append(f'{name}:{i}: {l.strip()[:150]}')
    return out


def main(argv):
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    ref = None
    if '--ref' in a: i = a.index('--ref'); ref = a[i + 1]; del a[i:i + 2]
    verbose = '-v' in a
    files = [x for x in a if not x.startswith('-')] or [p for p in sources.all_sources(cfg) if p.upper().endswith('.C')]
    now = []
    for f in files: now += scan_text(open(f, encoding='latin1').read(), os.path.relpath(f, cfg.root))
    line = f'{len(now)} cast-pointer offset sites in {len(files)} files'
    if ref:
        top = subprocess.run(['git', '-C', cfg.root, 'rev-parse', '--show-toplevel'], capture_output=True, text=True).stdout.strip()
        old = []
        for f in files:
            r = subprocess.run(['git', '-C', top, 'show', f'{ref}:{os.path.relpath(os.path.abspath(f), top)}'],
                               capture_output=True, text=True, encoding='latin-1')
            if r.returncode == 0: old += scan_text(r.stdout, f)
        line += f'; {len(old)} at {ref} (files that did not exist there are not counted)'
    print(line)
    if verbose: print('\n'.join(now))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
