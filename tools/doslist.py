"""List every code segment and its procedures from the IDA listing, in address order.

    python3 tools/doslist.py       writes <map>/dos_procs.tsv (everything) and
                                   <map>/dos_code.tsv (without the overlay stub segments)

TSV: segment  proc  offset  size (to the next proc; the last one to the segment's last label)"""
import re, os, sys
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config
cfg = config.load(config.pop_config(sys.argv[1:])[0])
asm = cfg.listing
seg = None; procs = []; last = 0; out = []
def flush():
    if seg and procs:
        for i, (n, o) in enumerate(procs):
            nxt = procs[i + 1][1] if i + 1 < len(procs) else max(last + 1, o + 1)
            out.append((seg, n, o, nxt - o))
for line in open(asm, encoding='latin1'):
    m = re.match(r'^(\w+)\s+segment\b', line)
    if m:
        flush(); seg = m.group(1); procs = []; last = 0; continue
    if seg is None: continue
    m = re.match(r'^(\w+)\s+proc\b', line)
    if m:
        n = m.group(1); mo = re.search(r'_([0-9A-F]{1,5})$', n)
        # names carry their offset as a hex suffix; a few don't, so fall back to the last label
        procs.append((n, int(mo.group(1), 16) if mo else last)); continue
    m = re.match(r'^\w*?_?([0-9A-F]{1,5}):', line)
    if m: last = int(m.group(1), 16)
flush()
with open(os.path.join(cfg.map, 'dos_procs.tsv'), 'w') as f, open(os.path.join(cfg.map, 'dos_code.tsv'), 'w') as g:
    for s, n, o, z in out:
        f.write(f'{s}\t{n}\t{o:X}\t{z:X}\n')
        if not s.startswith(cfg.listing_stub_prefix): g.write(f'{s}\t{n}\t{o:X}\t{z:X}\n')
print(len(out), 'procs')
