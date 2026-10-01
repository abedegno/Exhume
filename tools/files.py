"""Summarise <map>/functions.tsv per code segment (one segment = one source file).

    python3 tools/files.py        writes <map>/files.tsv, prints totals

Classifies each segment as C or assembly by how many functions open with the compiler's
standard frame (`push bp; mov bp,sp` for Turbo C), and counts progress against matched.txt.
Sizes come from the listing's procs and overstate assembly modules; the target tables have
the true extents."""
import os, re, glob, sys
from collections import defaultdict
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config
cfg = config.load(config.pop_config(sys.argv[1:])[0])
asm = cfg.listing

# C-prologue share per segment, from the first two instructions of each proc
first = {}; seg = cur = None
insn = re.compile(r'^\s*([a-z]{2,6})\b\s*(.*)$')
for l in open(asm, encoding='latin1'):
    m = re.match(r'^(\w+)\s+segment\b', l)
    if m: seg = m.group(1); continue
    m = re.match(r'^(\w+)\s+proc\b', l)
    if m: cur = (seg, m.group(1)); first[cur] = []; continue
    if cur and len(first[cur]) < 2:
        m = insn.match(l)
        if m and m.group(1) not in ('assume', 'align', 'db', 'dw', 'dd', 'public', 'extrn'):
            first[cur].append(m.group(1) + ' ' + m.group(2).split(';')[0])
cpro = defaultdict(int)
for (s, n), ins in first.items():
    if len(ins) == 2 and ins[0].startswith('push') and 'bp' in ins[0] and re.search(r'bp,\s*sp', ins[1]):
        cpro[s] += 1

rows = [l.rstrip('\n').split('\t') for l in open(os.path.join(cfg.map, 'functions.tsv')) if not l.startswith('#')]
segs = defaultdict(list)
for r in rows: segs[r[0]].append(r)
# segments whose source matches whole and verifies, listed by hand in matched.txt
done = cfg.matched_set()

out = open(os.path.join(cfg.map, 'files.tsv'), 'w')
out.write('# segment\tkind\tfunctions\tbytes\tnamed\tsize only\tcalls disagree\tunpaired\tfirst named\tlast named\tmatched\n')
tot = defaultdict(int)
for s, rs in segs.items():
    k = defaultdict(int); b = 0
    for r in rs: k[r[5] or 'unpaired'] += 1; b += int(r[3], 16)
    named = [r[4] for r in rs if r[5] in ('anchor', 'confirmed')]
    good = k['anchor'] + k['confirmed']
    kind = 'library' if 'library' in k else ('C' if cpro[s] * 4 >= len(rs) * 3 else 'assembly' if cpro[s] * 2 < len(rs) else 'mixed')
    out.write(f"{s}\t{kind}\t{len(rs)}\t{b}\t{good}\t{k['size only']}\t{k['size, calls disagree']}\t{k['unpaired']}"
              f"\t{named[0] if named else ''}\t{named[-1] if named else ''}\t{'yes' if s in done else ''}\n")
    tot[kind + ' segments'] += 1; tot[kind + ' bytes'] += b; tot[kind + ' functions'] += len(rs)
    if kind != 'library': tot['named functions'] += good
    if s in done: tot['matched bytes'] += b
for k in sorted(tot): print(f'{k}: {tot[k]}')
