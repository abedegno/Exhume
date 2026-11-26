#!/usr/bin/env python3
"""gomoved.py - which source files changed between two releases of one program.

A stripped Go binary still records, for every function, its source file and the lines its
instructions come from. Two releases built from the same file give a function the same lines,
whichever compiler built them. A function that starts lower in the later release, or covers
more lines, means the file was edited above it or inside it.

A package's init function is given the line after the last line of the package's last file. So
a file added to the package, or lines appended to that file, show there even when no other
function moved.

Run this before trying to make one source reproduce both releases (godual.py). A function
whose own lines are unchanged can still differ because it inlines a helper from a file that
changed: the helper leaves no function of its own, only the moved lines of its neighbours.

usage: gomoved.py RELEASE_A RELEASE_B [REGEX] [--config FILE]

Output: functions present in only one release; then one row per changed file with the number
of functions that moved and, for each function whose length changed, by how many lines.
A function is placed by its first instruction's line and its last line, because the lowest
line of a function can belong to code inlined from another file.
"""
import argparse
import collections
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gotc

HEADER = re.compile(r'^== (.+?)  \((\S+):(\d+)-(\d+), (\d+) bytes\) ==$')
LINE = re.compile(r'^\s+L(\d+):')


def parse_lift(text):
    """{function: (file, first instruction's line, last line, bytes)} from `golift lift` output."""
    out, cur = {}, None
    for ln in text.split('\n'):
        m = HEADER.match(ln)
        if m:
            cur = m.group(1)
            out[cur] = [m.group(2), None, int(m.group(4)), int(m.group(5))]
            continue
        m = LINE.match(ln)
        if m and cur and out[cur][1] is None:
            out[cur][1] = int(m.group(1))
    return {f: (v[0], v[1] if v[1] is not None else v[2], v[2], v[3]) for f, v in out.items()}


def package_of(func):
    """Import path of a function name such as a/b.(*T).M or a/b.F.func1."""
    slash = func.rfind('/')
    dot = func.find('.', slash + 1)
    return func[:dot] if dot >= 0 else func


def compare(a, b):
    """(only in a, only in b, {(package, file a, file b): [(function, shift, growth)]}) for the
    functions whose file, first line or last line differ. SHIFT is how far the first line moved,
    GROWTH how many lines longer the function is in b."""
    moved = collections.defaultdict(list)
    for f in sorted(set(a) & set(b)):
        (fa, la, ha, _), (fb, lb, hb, _) = a[f], b[f]
        if fa.startswith('<'):          # compiler-generated wrappers carry no source line
            continue
        if (fa, la, ha) != (fb, lb, hb):
            moved[(package_of(f), fa, fb)].append((f, lb - la, (hb - lb) - (ha - la)))
    return sorted(set(a) - set(b)), sorted(set(b) - set(a)), dict(moved)


def report(a, b, say=print):
    only_a, only_b, moved = compare(a, b)
    for name, only in (('first', only_a), ('second', only_b)):
        if only:
            say(f'only in the {name} release: {", ".join(only)}')
    for (pkg, fa, fb), rows in sorted(moved.items()):
        grew = [r for r in rows if r[2]]
        shifts = sorted({r[1] for r in rows})
        where = fa if fa == fb else f'{fa} -> {fb}'
        say(f'{pkg}  {where}: {len(rows)} moved (by {", ".join("%+d" % s for s in shifts)})'
            + (f'; changed length: {", ".join("%s %+d" % (r[0][len(pkg) + 1:], r[2]) for r in grew)}' if grew else ''))
    if not (only_a or only_b or moved):
        say('no function moved')
    return len(moved)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Source files that changed between two releases')
    ap.add_argument('release_a')
    ap.add_argument('release_b')
    ap.add_argument('regex', nargs='?', default='.', help='functions to compare (default: all)')
    ap.add_argument('--config')
    a = ap.parse_args(argv)
    tc = gotc.Toolchain(gotc.settings(a.config))
    report(parse_lift(tc.lift(a.release_a, a.regex)), parse_lift(tc.lift(a.release_b, a.regex)))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except gotc.ToolError as e:
        sys.exit(f'gomoved: {e}')
