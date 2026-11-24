"""Match one Go 1.8.0 unit/package against the target ELF binary (profile: golang-180).

    python3 tools/match.py UNIT_OR_SRC [--func REGEX] [--no-build] [--bin PATH] [-v] [--asm OUT]
    python3 tools/match.py --dis REGEX [--addr]
    python3 tools/match.py --str 0xADDR LEN

Dispatched from Exhume's tools/match.py when `[toolchain] profile = "golang-180"`.
Compiles the unit with golang:1.8.0 (unless `--no-build` or `--bin` is given), then verifies:
  1. `gocmp cmp`: normalized disassembly (calls, funcvals, intra-function offsets) AND exact
     `.gopclntab` `file:line` per instruction.
  2. `gocmp raw`: raw machine-code byte equality across every byte outside x86-64 PC-relative
     displacement fields (0 unmasked byte diffs).

Exit status 0 only when at least one function is compared, every function matches code + line
table, and raw comparison reports 0 unmasked byte differences.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gotc


def match_unit(tc, s, unit, regex=None, no_build=False, bin_path=None,
               verbose=False, first_window=0, skip_raw=False, asm_out=None):
    """Build (optional) and compare unit against s.image. Returns (ok, cmp_res, raw_res, cand_bin)."""
    if not s.image or not os.path.exists(s.image):
        raise gotc.ToolError(f'target binary not found: {s.image}')
    rx = regex or unit.regex
    if bin_path:
        cand = os.path.abspath(bin_path)
    elif no_build:
        cand = unit.bin_path
    else:
        cand, asm_text = tc.build(unit, capture_asm=bool(asm_out))
        if asm_out:
            os.makedirs(os.path.dirname(os.path.abspath(asm_out)), exist_ok=True)
            with open(asm_out, 'w') as f:
                f.write(asm_text)
    if not os.path.exists(cand):
        raise gotc.ToolError(f'candidate binary not found at {cand} (run without --no-build first)')
    cmp_res = tc.cmp(s.image, cand, rx, verbose=verbose, first_window=first_window)
    raw_res = None if skip_raw else tc.raw(s.image, cand, rx)
    ok = cmp_res.ok and (skip_raw or (raw_res is not None and raw_res.ok))
    return ok, cmp_res, raw_res, cand


def _parser():
    ap = argparse.ArgumentParser(
        prog='match.py',
        description='Match one Go 1.8.0 unit/package against the target ELF binary.',
    )
    ap.add_argument('target', nargs='?', help='unit name, .go file path, or package name')
    ap.add_argument('--func', dest='func_regex', help='override/narrow function regex')
    ap.add_argument('--no-build', action='store_true', help='compare existing binary without rebuilding')
    ap.add_argument('--bin', dest='bin_path', help='compare a specific compiled binary (implies --no-build)')
    ap.add_argument('-v', '--verbose', action='store_true', help='show side-by-side diffs for mismatches')
    ap.add_argument('--first', type=int, default=25, help='with -v, lines around first diff (0 = all; default 25)')
    ap.add_argument('--skip-raw', action='store_true', help='skip raw byte comparison')
    ap.add_argument('--asm', dest='asm_out', help='build with -S and write compiler listing to PATH')
    ap.add_argument('--dis', dest='dis_regex', help='disassemble functions matching REGEX from target binary')
    ap.add_argument('--lift', dest='lift_regex', help='compact line-grouped lift for functions matching REGEX')
    ap.add_argument('--types', dest='types_regex', help='extract Go types/structs/tags from .typelink matching REGEX')
    ap.add_argument('--addr', action='store_true', help='with --dis, show .rodata addresses and string previews')
    ap.add_argument('--str', nargs=2, metavar=('ADDR', 'LEN'), help='print LEN bytes at hex ADDR from target binary')
    return ap


def main(cfg, argv):
    if not argv:
        _parser().print_help()
        return 0
    args = _parser().parse_args(argv)
    s = gotc.Settings.from_config(cfg)
    tc = gotc.Toolchain(s)

    if args.types_regex:
        if not s.image or not os.path.exists(s.image):
            sys.exit(f'gomatch: target binary not found: {s.image}')
        sys.stdout.write(tc.types(s.image, args.types_regex))
        return 0

    if args.lift_regex:
        if not s.image or not os.path.exists(s.image):
            sys.exit(f'gomatch: target binary not found: {s.image}')
        sys.stdout.write(tc.lift(s.image, args.lift_regex))
        return 0

    if args.dis_regex:
        if not s.image or not os.path.exists(s.image):
            sys.exit(f'gomatch: target binary not found: {s.image}')
        sys.stdout.write(tc.dis(s.image, args.dis_regex, show_addr=args.addr))
        return 0

    if args.str:
        if not s.image or not os.path.exists(s.image):
            sys.exit(f'gomatch: target binary not found: {s.image}')
        sys.stdout.write(tc.str_at(s.image, args.str[0], args.str[1]))
        return 0

    try:
        unit, inferred_rx = s.resolve_unit(args.target)
        rx = args.func_regex or inferred_rx or unit.regex
        ok, cmp_res, raw_res, cand = match_unit(
            tc, s, unit,
            regex=rx,
            no_build=args.no_build,
            bin_path=args.bin_path,
            verbose=args.verbose,
            first_window=args.first if args.verbose else 0,
            skip_raw=args.skip_raw,
            asm_out=args.asm_out,
        )
    except gotc.ToolError as e:
        print(f'gomatch: {e}', file=sys.stderr)
        return 1

    sys.stdout.write(cmp_res.raw_stdout)
    if raw_res is not None:
        print(f'raw: {raw_res.total_bytes} bytes; {raw_res.rel_bytes} PC-relative displacement bytes; '
              f'{raw_res.unmasked_diffs} unmasked diffs')
        if args.verbose and raw_res.details:
            for d in raw_res.details[:40]:
                print('  ' + d)
    if ok:
        print(f'WHOLE UNIT MATCHES ({unit.name}: {cmp_res.total}/{cmp_res.total} funcs, {cmp_res.total_bytes} bytes)')
        return 0
    if cmp_res.total == 0:
        print(f'FAIL: regex {rx!r} matched 0 functions in {s.image}')
    else:
        print(f'FAIL ({unit.name}: {len(cmp_res.matched)}/{cmp_res.total} matched, '
              f'{len(cmp_res.lines_only)} line-only, {len(cmp_res.diffs)} diff, '
              f'{len(cmp_res.missing)} missing'
              + (f', {raw_res.unmasked_diffs} unmasked raw bytes' if raw_res else '') + ')')
    return 1


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(gotc.EXHUME, 'tools'))
    import config
    p, rest = config.pop_config(sys.argv[1:])
    sys.exit(main(config.load(p), rest))
