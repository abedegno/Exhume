#!/usr/bin/env python3
"""Extract original captured variable names from Go 1.8 closure type symbols.

Go 1.8 emits `type..hash.struct { F uintptr; pkg.var1 T1; ... }` symbols in `.gopclntab`
for comparable closure environments, exposing original receiver, parameter, and local names
that help resolve `cmpstackvarlt` alphabetical stack-slot ordering diffs.

Usage:
    python3 goclosures.py [--funcs-tsv map/functions.tsv] [--filter REGEX]
"""
import argparse
import csv
import re
import sys

CLOSURE_RE = re.compile(r'^type\.\.hash\.struct\s*\{\s*F uintptr;\s*(.+?)\s*\}$')


def extract_closures(rows, file_filter=None):
    """Yield (file, fields_list, full_symbol) for every closure hash struct row."""
    rx = re.compile(file_filter) if file_filter else None
    for src_file, sym in rows:
        m = CLOSURE_RE.match(sym)
        if not m:
            continue
        if rx and not (rx.search(src_file) or rx.search(sym)):
            continue
        fields = [f.strip() for f in m.group(1).split(';') if f.strip()]
        yield src_file, fields, sym


def load_tsv(path):
    rows = []
    with open(path) as f:
        for r in csv.DictReader(f, delimiter='\t'):
            rows.append((r.get('file', ''), r.get('name', '')))
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description='Extract original closure capture names from Go 1.8 .gopclntab.')
    ap.add_argument('--funcs-tsv', default='map/functions.tsv', help='path to functions.tsv (default: map/functions.tsv)')
    ap.add_argument('--filter', dest='file_filter', help='optional regex matching file path or symbol')
    args = ap.parse_args(argv)

    rows = load_tsv(args.funcs_tsv)
    count = 0
    for src_file, fields, _ in extract_closures(rows, args.file_filter):
        count += 1
        print(f'{src_file}\t{"; ".join(fields)}')
    return 0 if count >= 0 else 1


if __name__ == '__main__':
    sys.exit(main())
