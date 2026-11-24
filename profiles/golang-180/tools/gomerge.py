"""Accept a matched Go 1.8.0 unit into matched.txt and <build>/go18/accepted.json.

    python3 tools/merge.py UNIT [--no-build] [--config PATH]

Dispatched from Exhume's tools/merge.py when `[toolchain] profile = "golang-180"`.
Verifies the unit passes both `gocmp cmp` (code + exact .gopclntab line table) and `gocmp raw`
(0 unmasked machine-code byte diffs), then records its hashes and coverage.
"""
import argparse
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gotc
import gomatch


def _sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _parser():
    ap = argparse.ArgumentParser(
        prog='merge.py',
        description='Accept a matched Go 1.8.0 unit after verifying cmp + raw match.',
    )
    ap.add_argument('unit', help='configured unit name or source file path')
    ap.add_argument('--no-build', action='store_true', help='verify existing binary without rebuilding')
    return ap


def main(cfg, argv):
    if not argv:
        _parser().print_help()
        return 0
    args = _parser().parse_args(argv)
    s = gotc.Settings.from_config(cfg)
    tc = gotc.Toolchain(s)
    try:
        unit, inferred_rx = s.resolve_unit(args.unit)
        rx = inferred_rx or unit.regex
        ok, cmp_res, raw_res, cand = gomatch.match_unit(tc, s, unit, regex=rx, no_build=args.no_build)
    except gotc.ToolError as e:
        print(f'gomerge: {e}', file=sys.stderr)
        return 1

    if not ok:
        sys.stdout.write(cmp_res.raw_stdout)
        print(f'gomerge: {unit.name} did NOT match ({len(cmp_res.matched)}/{cmp_res.total} funcs, '
              f'{raw_res.unmasked_diffs} unmasked raw bytes) — not accepted')
        return 1

    accepted_path = os.path.join(s.build, 'go18', 'accepted.json')
    os.makedirs(os.path.dirname(accepted_path), exist_ok=True)
    accepted = {}
    if os.path.exists(accepted_path):
        with open(accepted_path) as f:
            accepted = json.load(f)
    accepted[unit.name] = {
        'regex': rx,
        'gopath': os.path.relpath(unit.gopath, s.root),
        'pkg': unit.pkg,
        'funcs': cmp_res.total,
        'bytes': raw_res.total_bytes,
        'rel_bytes': raw_res.rel_bytes,
        'src_hash': unit.source_hash(),
        'bin_sha256': _sha256(cand),
    }
    with open(accepted_path, 'w') as f:
        json.dump(accepted, f, indent=2, sort_keys=True)

    existing = set()
    if os.path.exists(s.matched):
        with open(s.matched) as f:
            existing = {l.strip() for l in f if l.strip() and not l.startswith('#')}
    if unit.name not in existing:
        with open(s.matched, 'a') as f:
            f.write(unit.name + '\n')

    print(f'ACCEPTED {unit.name}: {cmp_res.total}/{cmp_res.total} funcs, {raw_res.total_bytes} bytes, '
          f'0 unmasked diffs (recorded in {os.path.relpath(s.matched, s.root)})')
    return 0


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(gotc.EXHUME, 'tools'))
    import config
    p, rest = config.pop_config(sys.argv[1:])
    sys.exit(main(config.load(p), rest))
