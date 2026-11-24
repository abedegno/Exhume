"""Map a Go 1.8.0 binary from .gopclntab: map/functions.tsv and map/packages.tsv.

    python3 gomap.py init [--from-tsv PATH] [--config PATH]
    python3 gomap.py packages [--first-party] [--config PATH]
    python3 gomap.py funcs [REGEX] [--config PATH]
    python3 gomap.py skeleton PKG [FILE] [--config PATH]

`init` runs `gofuncs` on `[binary] image` (or imports an existing `funcs.tsv`) and writes:
  - `<map>/functions.tsv`: entry, end, size, file, startline, endline, name
  - `<map>/packages.tsv`:  package, funcs, bytes, files

`skeleton` emits a line-numbered Go skeleton for a file in PKG using the exact function start/end
lines recorded in `.gopclntab`, ready to fill in during hand decompilation.
"""
import argparse
import collections
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gotc

FCOLS = ['entry', 'end', 'size', 'file', 'startline', 'endline', 'name']
PCOLS = ['package', 'funcs', 'bytes', 'files']

# Standard Go 1.8 top-level packages / runtime / generated prefixes
_STDLIB_PREFIXES = (
    'runtime', 'syscall', 'reflect', 'sync', 'os', 'io', 'fmt', 'net', 'crypto', 'encoding',
    'bytes', 'strings', 'strconv', 'time', 'math', 'sort', 'regexp', 'bufio', 'compress',
    'container', 'context', 'database', 'debug', 'errors', 'expvar', 'flag', 'go/', 'hash',
    'html', 'image', 'index', 'internal/', 'log', 'mime', 'path', 'plugin', 'testing', 'text/',
    'unicode', 'unsafe', 'archive', 'vendor/', 'type..', 'go.', 'cgo', '_cgo',
)


class FuncRow:
    __slots__ = ('entry', 'end', 'size', 'file', 'startline', 'endline', 'name')

    def __init__(self, entry, end, size, file, startline, endline, name):
        self.entry = entry
        self.end = end
        self.size = size
        self.file = file
        self.startline = startline
        self.endline = endline
        self.name = name

    @property
    def package(self):
        return package_of(self.name)

    @property
    def basename(self):
        return os.path.basename(self.file) if self.file else ''


def package_of(func_name):
    """Extract the Go package import path from a .gopclntab symbol name.

    Examples:
      applib/crypto.DeriveKeyA        -> applib/crypto
      store/fs.(*uncachedFile).Read   -> store/fs
      store/fs.uncachedFile.Close     -> store/fs
      main.api_AddItem.func1         -> main
      type..eq.applib.Policy        -> type..eq
    """
    if func_name.startswith(('type..hash.', 'type..eq.')):
        return '.'.join(func_name.split('.')[:3])
    # Strip pointer receiver method suffix: pkg.(*Type).Method...
    i = func_name.find('.(*')
    if i >= 0:
        return func_name[:i]
    # Find the first '.' after the last '/'
    slash = func_name.rfind('/')
    dot = func_name.find('.', slash + 1)
    if dot > 0:
        return func_name[:dot]
    return func_name


def is_first_party(pkg, first_party_prefixes=()):
    if first_party_prefixes:
        return any(pkg == p or pkg.startswith(p + '/') for p in first_party_prefixes)
    if pkg == 'main':
        return True
    for p in _STDLIB_PREFIXES:
        if p.endswith('/') or p.endswith('.'):
            if pkg.startswith(p):
                return False
        elif pkg == p or pkg.startswith(p + '/'):
            return False
    return True


def parse_funcs_tsv(text):
    rows = []
    for line in text.splitlines():
        if not line.strip() or line.startswith('#'):
            continue
        f = line.rstrip('\n').split('\t')
        if f[0] == 'entry' or len(f) < 7:
            continue
        rows.append(FuncRow(
            int(f[0], 16), int(f[1], 16), int(f[2]),
            f[3], int(f[4]), int(f[5]), f[6],
        ))
    return rows


def read_funcs(path):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return parse_funcs_tsv(f.read())


def write_funcs(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'w') as f:
        f.write('\t'.join(FCOLS) + '\n')
        for r in rows:
            f.write(f'{r.entry:#x}\t{r.end:#x}\t{r.size}\t{r.file}\t{r.startline}\t{r.endline}\t{r.name}\n')
    os.replace(path + '.tmp', path)


def summarize_packages(rows):
    by_pkg = {}
    for r in rows:
        pkg = r.package
        entry = by_pkg.setdefault(pkg, [0, 0, set()])
        entry[0] += 1
        entry[1] += r.size
        if r.basename:
            entry[2].add(r.basename)
    out = [(pkg, cnt, sz, sorted(files)) for pkg, (cnt, sz, files) in by_pkg.items()]
    out.sort(key=lambda t: (-t[2], -t[1], t[0]))
    return out


def write_packages(path, pkg_rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + '.tmp', 'w') as f:
        f.write('\t'.join(PCOLS) + '\n')
        for pkg, cnt, sz, files in pkg_rows:
            f.write(f'{pkg}\t{cnt}\t{sz}\t{",".join(files)}\n')
    os.replace(path + '.tmp', path)


def ensure_map(s, from_tsv=None, refresh=False):
    fpath = os.path.join(s.map_dir, 'functions.tsv')
    ppath = os.path.join(s.map_dir, 'packages.tsv')
    if not refresh and os.path.exists(fpath) and not from_tsv:
        return read_funcs(fpath)
    if from_tsv:
        with open(from_tsv) as f:
            rows = parse_funcs_tsv(f.read())
    else:
        if not s.image or not os.path.exists(s.image):
            raise gotc.ToolError(f'target binary not found: {s.image}')
        tc = gotc.Toolchain(s)
        rows = parse_funcs_tsv(tc.gofuncs(s.image))
    write_funcs(fpath, rows)
    write_packages(ppath, summarize_packages(rows))
    return rows


def render_skeleton(rows, pkg, filename=None):
    """Emit a line-numbered Go skeleton for functions in pkg (optionally filtered by file basename)."""
    matched = [r for r in rows if r.package == pkg and (not filename or r.basename == filename or r.file == filename)]
    if not matched:
        raise gotc.ToolError(f'no functions found for package {pkg!r}' + (f' file {filename!r}' if filename else ''))
    by_file = collections.defaultdict(list)
    for r in matched:
        by_file[r.basename or '<generated>'].append(r)
    pkg_short = pkg.rsplit('/', 1)[-1]
    out = []
    for fn_file, frows in sorted(by_file.items()):
        frows.sort(key=lambda r: (r.startline, r.entry))
        out.append(f'// === {fn_file} ({len(frows)} functions, {sum(r.size for r in frows)} bytes) ===')
        if fn_file == '<generated>' or all(r.startline == 0 for r in frows):
            for r in frows:
                out.append(f'//   {r.name}  ({r.size} bytes, {r.entry:#x}..{r.end:#x})')
            continue
        max_line = max(r.endline for r in frows)
        starts = collections.defaultdict(list)
        ends = collections.defaultdict(list)
        for r in frows:
            if r.startline > 0:
                starts[r.startline].append(r)
            if r.endline > 0:
                ends[r.endline].append(r)
        lines = {}
        lines[1] = f'package {pkg_short}'
        for ln in range(1, max_line + 1):
            if ln in starts:
                names = ', '.join(f'{r.name.split(".", 1)[-1]} ({r.size}B, L{r.startline}-L{r.endline})' for r in starts[ln])
                lines[ln] = f'// START: {names}'
            elif ln in ends:
                names = ', '.join(r.name.split('.', 1)[-1] for r in ends[ln])
                lines[ln] = f'// END: {names}'
            elif ln not in lines:
                lines[ln] = '//'
        for ln in range(1, max_line + 1):
            out.append(f'{ln:4d}: {lines[ln]}')
    return '\n'.join(out) + '\n'


def main(argv=None):
    ap = argparse.ArgumentParser(description='Map a Go 1.8.0 ELF binary via .gopclntab.')
    ap.add_argument('--config', help='path to exhume.toml')
    sub = ap.add_subparsers(dest='cmd', required=True)

    p_init = sub.add_parser('init', help='generate map/functions.tsv and map/packages.tsv')
    p_init.add_argument('--from-tsv', help='import an existing gofuncs TSV instead of running gofuncs')

    p_pkg = sub.add_parser('packages', help='list packages sorted by byte size')
    p_pkg.add_argument('--first-party', action='store_true', help='show only first-party packages')
    p_pkg.add_argument('--top', type=int, default=0, help='limit rows printed')

    p_fn = sub.add_parser('funcs', help='list functions matching a regex')
    p_fn.add_argument('regex', nargs='?', default='.*', help='function name regex')

    p_sk = sub.add_parser('skeleton', help='print line-numbered Go skeleton from .gopclntab')
    p_sk.add_argument('pkg', help='Go package import path (e.g. applib/crypto)')
    p_sk.add_argument('file', nargs='?', help='optional source file basename (e.g. derive.go)')

    args = ap.parse_args(argv)
    s = gotc.settings(args.config)
    if args.cmd == 'init':
        rows = ensure_map(s, from_tsv=args.from_tsv, refresh=True)
        pkgs = summarize_packages(rows)
        fp = [p for p in pkgs if is_first_party(p[0], s.first_party)]
        print(f'wrote {len(rows)} functions across {len(pkgs)} packages '
              f'({len(fp)} first-party, {sum(p[1] for p in fp)} funcs, {sum(p[2] for p in fp)} bytes) '
              f'to {s.map_dir}')
        return 0

    rows = ensure_map(s)
    if args.cmd == 'packages':
        pkgs = summarize_packages(rows)
        if args.first_party:
            pkgs = [p for p in pkgs if is_first_party(p[0], s.first_party)]
        if args.top > 0:
            pkgs = pkgs[:args.top]
        print(f'{"PACKAGE":40s} {"FUNCS":>6s} {"BYTES":>9s}  FILES')
        for pkg, cnt, sz, files in pkgs:
            fl = ', '.join(files[:6]) + (f', +{len(files) - 6}' if len(files) > 6 else '')
            print(f'{pkg:40s} {cnt:6d} {sz:9d}  {fl}')
        print(f'\n{len(pkgs)} packages, {sum(p[1] for p in pkgs)} functions, {sum(p[2] for p in pkgs)} bytes')
        return 0

    if args.cmd == 'funcs':
        rx = re.compile(args.regex)
        hits = [r for r in rows if rx.search(r.name)]
        print(f'{"ENTRY":10s} {"SIZE":>6s}  {"FILE:LINES":28s}  NAME')
        for r in hits:
            loc = f'{r.basename}:{r.startline}-{r.endline}' if r.basename else '-'
            print(f'{r.entry:#10x} {r.size:6d}  {loc:28s}  {r.name}')
        print(f'\n{len(hits)} functions, {sum(r.size for r in hits)} bytes')
        return 0

    if args.cmd == 'skeleton':
        sys.stdout.write(render_skeleton(rows, args.pkg, args.file))
        return 0
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except gotc.ToolError as e:
        sys.exit(f'gomap: {e}')
