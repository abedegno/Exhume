"""Build both call graphs: <map>/dos_calls.tsv (from the IDA listing) and <map>/fm_calls.tsv
(from the sibling build's image; the file name is historical: it is the sibling's graph).
Each line: caller <TAB> callee. DOS callees are IDA proc names (j_ thunks resolved to the
overlay function); sibling callees are symbol names from direct near calls.

    python3 tools/callgraphs.py"""
import os, re, sys
from iced_x86 import Decoder, Mnemonic, OpKind
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config
cfg = config.load(config.pop_config(sys.argv[1:])[0])
asm = cfg.listing
sib = cfg.sibling
procs = {l.split('\t')[1] for l in open(os.path.join(cfg.map, 'dos_code.tsv'))}
cur = None; out = set()
for l in open(asm, encoding='latin1'):
    m = re.match(r'^(\w+)\s+proc\b', l)
    if m: cur = m.group(1); continue
    if re.match(r'^(\w+)\s+endp\b', l): cur = None; continue
    m = cur and re.match(r'^\s*call\s+(?:far ptr\s+|near ptr\s+)?(\w+)', l)
    if m:
        t = re.sub(r'^j_', '', m.group(1))
        if t in procs and t != cur: out.add((cur, t))
with open(os.path.join(cfg.map, 'dos_calls.tsv'), 'w') as f:
    for a, b in sorted(out): f.write(f'{a}\t{b}\n')
print(len(out), 'DOS call edges')

img = open(sib['image'], 'rb').read()
syms = sorted((int(a, 16), n) for n, a, s in (l.rstrip('\n').split('\t') for l in open(sib['symbols'])))
code = [(a, n) for a, n in syms if a <= sib['code_end']]
bits = sib.get('bits', 32)
NEAR = OpKind.NEAR_BRANCH32 if bits == 32 else OpKind.NEAR_BRANCH16
byaddr = {a: n for a, n in code}
out = set()
for i, (a, n) in enumerate(code):
    end = code[i + 1][0] if i + 1 < len(code) else a + 16
    for ins in Decoder(bits, img[a:end], ip=a):
        if ins.mnemonic == Mnemonic.CALL and ins.op0_kind == NEAR:
            t = byaddr.get(ins.near_branch_target)
            if t and t != n: out.add((n, t))
with open(os.path.join(cfg.map, 'fm_calls.tsv'), 'w') as f:
    for a, b in sorted(out): f.write(f'{a}\t{b}\n')
print(len(out), 'sibling call edges')
