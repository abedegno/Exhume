"""Deterministic 0-token .gopclntab line-number pinner for Go 1.8.0 sources.

    python3 golinepin.py UNIT FILE.go [--no-build] [--apply] [--config PATH]

When machine code already matches (`gocmp cmp` reports `LINES`, or instructions match and only
`file:line` differs), this tool reads the `(orig_line, cand_line)` mapping from `gocmp -v cmp`
and computes exact blank-line insertions/deletions (never touching non-blank code lines) so every
statement lands on the exact line recorded in the target binary's `.gopclntab`.

Without `--apply`, prints the planned line adjustments; with `--apply`, updates FILE.go in place.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gotc


_DIFF_LINE_RE = re.compile(r'^\s+[* ]\s+([^:\s]+):(\d+)\s+.*\|\s+([^:\s]+):(\d+)\s+')


def extract_line_pairs(cmp_verbose_output, target_basename):
    """Extract sorted unique (cand_line -> orig_line) pairs for target_basename."""
    mapping = {}
    conflicts = []
    for line in cmp_verbose_output.splitlines():
        m = _DIFF_LINE_RE.match(line)
        if not m:
            continue
        of, ol, cf, cl = m.group(1), int(m.group(2)), m.group(3), int(m.group(4))
        if of != target_basename or cf != target_basename or ol == 0 or cl == 0:
            continue
        if cl in mapping and mapping[cl] != ol:
            conflicts.append((cl, mapping[cl], ol))
        else:
            mapping[cl] = ol
    return mapping, conflicts


def is_removable_padding(line):
    s = line.strip()
    return s == '' or s == '//'


def repin_lines(src_lines, mapping):
    """Adjust blank / empty `//` padding lines to move each mapped candidate line to orig_line.

    Works monotonically top-to-bottom. Returns (new_lines, applied_ops, unpinned).
    """
    lines = list(src_lines)
    shift = 0
    ops = []
    unpinned = []
    for cand_ln in sorted(mapping):
        want_ln = mapping[cand_ln]
        cur_ln = cand_ln + shift
        delta = want_ln - cur_ln
        if delta == 0:
            continue
        idx = cur_ln - 1  # 0-based index of the statement line
        if delta > 0:
            # Insert `delta` blank lines immediately before idx
            lines[idx:idx] = ['\n'] * delta
            shift += delta
            ops.append(f'L{cand_ln} -> L{want_ln}: inserted {delta} blank line(s) before line {cur_ln}')
        else:
            need = -delta
            # Remove up to `need` contiguous blank / empty-comment lines directly above idx
            removed = 0
            pos = idx - 1
            while removed < need and pos >= 0 and is_removable_padding(lines[pos]):
                del lines[pos]
                pos -= 1
                removed += 1
            shift -= removed
            if removed == need:
                ops.append(f'L{cand_ln} -> L{want_ln}: removed {removed} padding line(s) above line {cur_ln}')
            else:
                unpinned.append((cand_ln, want_ln, need - removed))
                ops.append(f'L{cand_ln} -> L{want_ln}: removed {removed}/{need} padding line(s) (blocked by code)')
    return lines, ops, unpinned


def main(argv=None):
    ap = argparse.ArgumentParser(description='Deterministically pin Go source line numbers to .gopclntab.')
    ap.add_argument('unit', help='configured unit name')
    ap.add_argument('file', help='path to .go source file')
    ap.add_argument('--no-build', action='store_true', help='use existing compiled binary')
    ap.add_argument('--apply', action='store_true', help='write repinned lines back to FILE.go')
    ap.add_argument('--func', dest='func_regex', help='optional function regex override')
    ap.add_argument('--config', help='path to exhume.toml')
    args = ap.parse_args(argv)

    s = gotc.settings(args.config)
    tc = gotc.Toolchain(s)
    unit, inferred_rx = s.resolve_unit(args.unit)
    src_path = args.file if os.path.exists(args.file) else os.path.join(unit.src_dir, args.file)
    if not os.path.exists(src_path):
        raise gotc.ToolError(f'source file not found: {args.file}')
    src_path = os.path.abspath(src_path)
    rx = args.func_regex or inferred_rx or unit.regex

    cand = unit.bin_path if args.no_build else tc.build(unit)[0]
    cmp_res = tc.cmp(s.image, cand, rx, verbose=True)
    if cmp_res.ok:
        print(f'{os.path.basename(src_path)}: already 100% MATCH (code + line table)')
        return 0
    if cmp_res.diffs or cmp_res.missing:
        print(f'note: {len(cmp_res.diffs)} DIFF and {len(cmp_res.missing)} MISSING funcs exist; '
              f'pinning line pairs from verbose output anyway')

    base = os.path.basename(src_path)
    mapping, conflicts = extract_line_pairs(cmp_res.raw_stdout, base)
    if conflicts:
        print(f'warning: {len(conflicts)} line conflict(s) (e.g. {conflicts[0]}): code structure may still differ')
    if not mapping:
        print(f'no mappable line pairs found for {base}')
        return 1

    with open(src_path) as f:
        src_lines = f.readlines()
    new_lines, ops, unpinned = repin_lines(src_lines, mapping)
    for op in ops:
        print('  ' + op)
    if not ops:
        print('no line shifts needed')
        return 0
    if args.apply:
        with open(src_path, 'w') as f:
            f.writelines(new_lines)
        print(f'wrote {src_path} ({len(new_lines)} lines)')
    else:
        print('(dry run; pass --apply to update file)')
    return 1 if unpinned else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except gotc.ToolError as e:
        sys.exit(f'golinepin: {e}')
