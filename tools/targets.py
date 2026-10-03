"""Build a target table for one segment from the map.

    python3 tools/targets.py SEGNAME > <targets>/SEGNAME.tsv

Base from map/segments.tsv, verified proc offsets from map/procs.tsv, and each function's
original name from map/functions.tsv where it is anchored or confirmed (otherwise the
listing's name, to be replaced once known). Sizes run to the next proc; the last runs to
the segment's first far return after it (the profile's far_return byte).

Segments are byte-aligned, so a file starts at its first function, possibly a few bytes
past the paragraph: offsets are from that start, and `org` is the difference.

Check every table before handing it to an agent. The last function's size can be short (a
`retf` or a CB byte inside its code) or, for assembly, run past the module into data or
padding: assembly tables should run from the module's first byte to the padding before
the next module, which the listing's procs do not show."""
import sys, os
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config

def main(argv):
    path, a = config.pop_config(argv)
    cfg = config.load(path); seg = a[0]
    M = lambda p: [l.rstrip('\n').split('\t') for l in open(os.path.join(cfg.map, p)) if not l.startswith('#')]
    base = next(int(r[1], 16) for r in M('segments.tsv') if r[0] == seg and r[1])
    # a listed proc with no offset (a label IDA could not place) or one past any 16-bit
    # segment (a stray address IDA named) is not a function of this segment
    bad = [r[1] for r in M('procs.tsv') if r[0] == seg and (r[2] in ('None', '') or int(r[2], 16) > 0xFFFF)]
    if bad: print(f'# skipped listing entries: {", ".join(bad)}', file=sys.stderr)
    procs = [(r[1], int(r[2], 16), r[3]) for r in M('procs.tsv') if r[0] == seg and r[1] not in bad]
    names = {r[1]: (r[4].rstrip('_'), r[5]) for r in M('functions.tsv') if r[0] == seg}
    procs.sort(key=lambda p: p[1])
    exe = cfg.exe
    if any(st == 'unverified' for _, _, st in procs):
        print(f'# warning: unverified offsets in {seg}', file=sys.stderr)
    org = procs[0][1]
    last = procs[-1][1]
    ret = bytes([cfg.profile.get('link', {}).get('far_return', 0xCB)])
    end = exe.index(ret, base + last) - base + 1
    print(f'# segment {seg} base 0x{base + org:X} size 0x{end - org:X} org 0x{org:X}')
    print(f'# cname: original ({cfg.original_label}) name where the map anchors or confirms it, else the listing name')
    for i, (ida, o, st) in enumerate(procs):
        nxt = procs[i + 1][1] if i + 1 < len(procs) else end
        orig, how = names.get(ida, ('', ''))
        c = orig if orig and how in ('anchor', 'confirmed', 'kin-same', 'kin-near') else ida
        print(f'{c}\t{ida}\t0x{o - org:X}\t0x{nxt - o:X}')

if __name__ == '__main__':
    main(sys.argv[1:])
