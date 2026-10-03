"""Place the segments locate.py could not, from the VROOMM segment table: every overlay from
its stub, the functions of overlays the listing left empty, and resident segments the
listing does not name with a paragraph.

    python3 tools/segmap.py [--write]

A resident segment IDA left unnumbered whose functions did not disassemble as listed
(UW1's seg004, the 3D renderer, starts with a data table IDA decoded as code, as UW2's did)
takes the one segment table entry between its located neighbours' paragraphs that no
segment holds yet; --write gives it that offset and its listed functions (status `placed`).

Each overlay's stub (a segment table entry with flags 3) starts with a header after its
`int 3Fh`: the overlay's offset (dword at +4, from the FBOV block's end of header, that is
the load module's end + 10h), its code size (+8), its relocation data's size (+0Ah) and its
entry count (+0Ch). That places every overlay without the listing. An IDA listing can leave
overlays empty (UW1's has 20: `ovr128 segment ... ovr128_0 label byte ends`); for those the
code is decoded linearly from offset 0 and split after each far return (CB, CA nn nn), and a
function is named ovrNNN_OFF as IDA would.

Prints each overlay: the stub's offset and size, map/segments.tsv's offset where locate.py
placed it, and whether they agree. --write adds the overlays locate.py could not place to
map/segments.tsv, and their functions to map/dos_procs.tsv, map/dos_code.tsv and
map/procs.tsv (status `decoded`), and cuts any listed function that runs past its overlay's
code (an IDA proc that ran on: UW1's ovr143 had one of 582 KB) to the code's end.""" 
import os, struct, sys
from iced_x86 import Decoder, Mnemonic
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config

path, a = config.pop_config(sys.argv[1:])
cfg = config.load(path); write = '--write' in a
exe = cfg.exe
mz = cfg.mz(); hdr, mzend = mz['hdr'], mz['end']
tab, ents = cfg.segtable()


def funcs(off, size):
    """[(offset, size)] of the far functions in size bytes at off, split after far returns."""
    out = []; start = 0
    for ins in Decoder(16, exe[off:off + size], ip=0):
        if ins.mnemonic == Mnemonic.RETF:
            end = ins.next_ip
            out.append((start, end - start)); start = end
            # alignment padding (a zero byte before a word-aligned function) belongs to nothing
            while start < size and exe[off + start] == 0 and start + 1 < size and exe[off + start + 1] == 0x55:
                start += 1
    if start < size and any(exe[off + start:off + size]): out.append((start, size - start))
    return out


M = cfg.map
segs = {}
for l in open(os.path.join(M, 'segments.tsv')):
    if l.startswith('#'): continue
    f = l.rstrip('\n').split('\t')
    segs[f[0]] = f
names = lambda i: next((s for s in segs if cfg.overlay_index(s) == i), f'ovr{i:03d}')
add_segs, add_procs = [], []
codesize = {}
# resident segments: segment table entries with flags other than 3, by load paragraph
order = [l.rstrip('\n').split('\t') for l in open(os.path.join(M, 'segments.tsv')) if not l.startswith('#')]
paras = sorted({e[0] for e in ents if e[2] != 3})
held = {(int(r[1], 16) - hdr) // 16 for r in order if r[1] and cfg.overlay_index(r[0]) is None}
listed = {}
for l in open(os.path.join(M, 'dos_procs.tsv')):
    f = l.rstrip('\n').split('\t'); listed.setdefault(f[0], []).append(f)
for k, r in enumerate(order):
    if r[1] or cfg.overlay_index(r[0]) is not None or r[0].startswith(cfg.listing_stub_prefix): continue
    lo = next(((int(x[1], 16) - hdr) // 16 for x in reversed(order[:k]) if x[1] and cfg.overlay_index(x[0]) is None), -1)
    hi = next(((int(x[1], 16) - hdr) // 16 for x in order[k + 1:] if x[1] and cfg.overlay_index(x[0]) is None), 1 << 20)
    cand = [p for p in paras if lo < p < hi and p not in held]
    if len(cand) != 1:
        print(f'{r[0]}  not placed: {len(cand)} segment table entries between its neighbours'); continue
    off = hdr + cand[0] * 16
    print(f'{r[0]}  resident, segment table paragraph {cand[0]:#x}  file {off:#x}  placed by the table')
    add_segs.append((r[0], off))
    for f in listed.get(r[0], []): add_procs.append((r[0], f[1], int(f[2], 16), int(f[3], 16), 'placed'))
for i, (para, end, flags, start) in enumerate(ents):
    if flags != 3: continue
    so = hdr + para * 16
    if exe[so:so + 2] != b'\xcd\x3f': continue
    fo, = struct.unpack_from('<I', exe, so + 4)
    cs, rs, ne = struct.unpack_from('<HHH', exe, so + 8)
    s = names(i); off = mzend + 0x10 + fo
    codesize[s] = cs
    have = segs.get(s, [s, ''])[1]
    state = 'empty' if cs == 0 else 'agrees' if have and int(have, 16) == off else 'placed by stub' if not have else f'listing at {have}'
    print(f'{s}  stub {fo:#x} code {cs:#x} relocs {rs:#x} entries {ne}  file {off:#x}  {state}')
    if cs and not have:
        add_segs.append((s, off))
        for o, z in funcs(off, cs): add_procs.append((s, f'{s}_{o:X}', o, z, 'decoded'))
if write and add_segs:
    lines = [l for l in open(os.path.join(M, 'segments.tsv')) if not any(l.startswith(s + '\t') for s, _ in add_segs)]
    with open(os.path.join(M, 'segments.tsv'), 'w') as f:
        f.writelines(lines)
        for s, off in add_segs: f.write(f'{s}\t{off:#x}\t0\t0\n')
    for fn in ('dos_procs.tsv', 'dos_code.tsv'):
        have = {tuple(l.split('\t')[:2]) for l in open(os.path.join(M, fn))}
        with open(os.path.join(M, fn), 'a') as f:
            for s, n, o, z, st in add_procs:
                if (s, n) not in have: f.write(f'{s}\t{n}\t{o:X}\t{z:X}\n')
    with open(os.path.join(M, 'procs.tsv'), 'a') as f:
        for s, n, o, z, st in add_procs: f.write(f'{s}\t{n}\t{o:X}\t{st}\n')
    print(f'wrote {len(add_segs)} segments and {len(add_procs)} functions to {M}')

if write:
    for fn in ('dos_procs.tsv', 'dos_code.tsv'):
        p = os.path.join(M, fn); out = []; cut = 0
        for l in open(p):
            f = l.rstrip('\n').split('\t')
            if len(f) >= 4 and f[0] in codesize and codesize[f[0]] and int(f[2], 16) + int(f[3], 16) > codesize[f[0]]:
                f[3] = f'{max(0, codesize[f[0]] - int(f[2], 16)):X}'; cut += 1
                l = '\t'.join(f) + '\n'
            out.append(l)
        open(p, 'w').writelines(out)
        if cut: print(f'{fn}: {cut} functions cut to the end of their overlay code')
