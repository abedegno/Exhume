"""Map named Go 1.8.0 stack slots from a `-S` compiler listing to the target binary's [RSP+offset].

    python3 goslotmap.py UNIT FULL_FUNC_NAME [--listing FILE] [--bin PATH] [--config PATH]

Go 1.8's `gc` allocates local variables of the same pointer-ness and alignment in alphabetical
order of their identifier names (`cmpstackvarlt`). When instructions agree and only `[RSP+0x...]`
offsets differ, this tool pairs each local / `autotmp_N` slot in your build with the offset the
original binary uses at the same instruction, showing which variables moved.

If `--listing` is omitted, the unit is compiled with `-gcflags "... -S"` and the listing is saved
under `<build>/slotmap/<unit>.S`.
"""
import argparse
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gotc


def parse_listing_slots(listing_text, short_func):
    """Extract {sp_offset: local_name} for short_func from a Go 1.8 `-S` compiler listing."""
    pat = r'^""\.' + re.escape(short_func) + r' t=.*?(?=^""\.\S+ t=|\Z)'
    m = re.search(pat, listing_text, re.S | re.M)
    if not m:
        raise gotc.ToolError(f'function {short_func!r} not found in -S listing')
    names = {}
    body = m.group(0)
    for nm, off in re.findall(r'""\.([\w.]+)\+(\d+)\(SP\)', body):
        names[int(off)] = nm
    for nm, off in re.findall(r'(autotmp_\d+)\+(\d+)\(SP\)', body):
        names[int(off)] = nm
    return names


def map_slots(diff_text, names):
    """Pair candidate SP offsets with original SP offsets from `gocmp -v cmp` output."""
    pairs = collections.defaultdict(set)
    for line in diff_text.splitlines():
        if not line.startswith('   '):
            continue
        parts = line[5:].split(' | ')
        if len(parts) != 2:
            continue
        o = re.findall(r'\[RSP\+0x([0-9a-f]+)\]', parts[0])
        n = re.findall(r'\[RSP\+0x([0-9a-f]+)\]', parts[1])
        for a, b in zip(o, n):
            pairs[int(b, 16)].add(int(a, 16))
    rows = []
    for noff, ooffs in pairs.items():
        if noff in names:
            s_ooffs = sorted(ooffs)
            rows.append((s_ooffs[0], names[noff], noff, s_ooffs))
    rows.sort(reverse=True)
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description='Map Go 1.8.0 local stack slots against original binary offsets.')
    ap.add_argument('unit', help='configured unit name or source file path')
    ap.add_argument('func', help='full symbol name (e.g. applib/crypto.encryptV1)')
    ap.add_argument('--listing', help='existing -S compiler listing (otherwise builds with -S)')
    ap.add_argument('--bin', dest='bin_path', help='existing candidate binary (with --listing)')
    ap.add_argument('--config', help='path to exhume.toml')
    args = ap.parse_args(argv)

    s = gotc.settings(args.config)
    tc = gotc.Toolchain(s)
    unit, _ = s.resolve_unit(args.unit)

    # Extract short function name after package prefix: pkg.Func or pkg.(*T).Func -> Func or (*T).Func
    if '/' in args.func:
        after_slash = args.func.rsplit('/', 1)[1]
        short_func = after_slash.split('.', 1)[1] if '.' in after_slash else args.func
    elif '.' in args.func:
        short_func = args.func.split('.', 1)[1]
    else:
        short_func = args.func

    if args.listing:
        with open(args.listing) as f:
            listing_text = f.read()
        cand = os.path.abspath(args.bin_path) if args.bin_path else unit.bin_path
    else:
        cand, listing_text = tc.build(unit, capture_asm=True)
        save_path = os.path.join(s.build, 'slotmap', unit.name + '.S')
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        with open(save_path, 'w') as f:
            f.write(listing_text)

    names = parse_listing_slots(listing_text, short_func)
    cmp_res = tc.cmp(s.image, cand, '^' + re.escape(args.func) + '$', verbose=True)
    if cmp_res.ok:
        print(f'{args.func}: already an exact MATCH (no moved slots)')
        return 0
    rows = map_slots(cmp_res.raw_stdout, names)
    if not rows:
        print(f'{args.func}: no paired [RSP+0x...] slots found (instructions may differ in length)')
        return 1
    for ooff, nm, noff, all_ in rows:
        flag = '' if ooff == noff and len(all_) == 1 else f'   <-- moved (orig seen: {", ".join(hex(x) for x in all_)})'
        print(f'orig {ooff:#6x}  mine {noff:#6x}  {nm:24s}{flag}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except gotc.ToolError as e:
        sys.exit(f'goslotmap: {e}')
