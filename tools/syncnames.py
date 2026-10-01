"""Rename target-table rows to the names a matched file actually defines.

    python3 tools/syncnames.py SRC      (after match.py has built it)

For each public function in the object, the row at the same offset takes its name (minus
the underscore). Prints each change; rows with no public at their offset (statics) are left."""
import sys, os, re
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config
from omf import publics, module_masked
path, a = config.pop_config(sys.argv[1:]); cfg = config.load(path)
src = a[0]; stem = cfg.stem(src)
seg = cfg.directives(src)[0]
d = open(cfg.obj(stem), 'rb').read()
pubs = publics(d)
pre = cfg.profile.get('c', {}).get('public_prefix', '_'); lim = cfg.profile.get('c', {}).get('name_limit', 32)
# only publics in the code segment name functions; a data public can share an offset
_, segs, _, _, _ = module_masked(d)
code = next(i for i, x in enumerate(segs, 1) if x[1] == cfg.profile.get('c', {}).get('code_class', 'CODE'))
at = {o: n[len(pre):] if n.startswith(pre) else n for n, (s, o) in pubs.items() if s == code}
t = os.path.join(cfg.targets, seg + '.tsv'); lines = open(t).read().split('\n')
for j, l in enumerate(lines):
    if not l or l.startswith('#'): continue
    f = l.split('\t'); off = int(f[2], 16)
    # a 32-character name is only Turbo C truncating the table's longer one
    if off in at and at[off] != f[0] and not (len(at[off]) == lim and f[0].startswith(at[off])):
        print(f'{f[0]} -> {at[off]}'); f[0] = at[off]; lines[j] = '\t'.join(f)
open(t, 'w').write('\n'.join(lines))
