"""Disassemble a function from the sibling build by its original name, naming calls and globals.

    python3 tools/sibling.py NAME        (for UW2: tools/sibling.py dream_)

The sibling is a second build of the same program that kept its symbols (for UW2 the FM
Towns release: 32-bit Watcom register-call code, arguments in EAX, EDX, EBX, ECX). Its
[sibling] image and symbols (TSV: name, hex address, size) come from exhume.toml. It is a
second witness for what the code means and what things were called; the target binary is
the authority on bytes."""
import sys, os, re
from iced_x86 import Decoder, Formatter, FormatterSyntax
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config
path, a = config.pop_config(sys.argv[1:]); cfg = config.load(path); sib = cfg.sibling
img = open(sib['image'], 'rb').read()
syms = {}
for l in open(sib['symbols']):
    n, ad, s = l.rstrip('\n').split('\t')[:3]; syms[int(ad, 16)] = n
addrs = sorted(syms); byname = {v: k for k, v in syms.items()}
if not a or a[0] not in byname: sys.exit(f'usage: sibling.py NAME (a name in {sib["symbols"]})')
start = byname[a[0]]; end = next(x for x in addrs if x > start)
f = Formatter(FormatterSyntax.NASM)
def name(v):
    if v in syms: return syms[v]
    lo = max([x for x in addrs if x <= v], default=None)
    return f'{syms[lo]}+{v - lo:#x}' if lo is not None and v - lo < 0x400 else None
for i in Decoder(sib.get('bits', 32), img[start:end], ip=start):
    s = f.format(i)
    for h in re.findall(r'\b([0-9A-F]{5,8})h\b', s):
        n = name(int(h, 16))
        if n: s = s.replace(h + 'h', n)
    print(f'{i.ip:08x}  {s}')
