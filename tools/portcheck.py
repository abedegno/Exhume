"""A port's first measurement: compile every game C source for the host, compile only
(docs/port.md, "Measure").

    python3 tools/portcheck.py [--config PATH]              summary: per category, per file, unresolved names
    python3 tools/portcheck.py [--config PATH] --diag FILE  every diagnostic for one source (path or stem)
    python3 tools/portcheck.py [--config PATH] --list CAT   every diagnostic in one category
    python3 tools/portcheck.py [--config PATH] --json       also write <build>/<port out>/summary.json
    python3 tools/portcheck.py [--config PATH] --cc CC      another compiler (default $CC, else clang, else cc)

Each .C of the DOS build (tools/sources.py: not the include directory, not [project] exclude),
and the shared C (the runtime's replay/replay.c, and [port] shared), is compiled with the host
C compiler as C89 with GNU extensions ([port] game_std), with [port] compat force-included (the
runtime's compat.h, which includes the project's portgame.h) and the runtime's stand-ins for
the compiler's own headers ([port] stand_ins) ahead of the host's; portable.h is the runtime's
too. No game source is edited.
Objects and the full log go to <build>/<[port] out>/; nothing else is written.

Diagnostics are counted once each (a header's are reported where they occur, not once per
source that includes it) and put in categories by message, warning flag and the source line.
Then the objects' undefined names, less everything the objects define and everything the host
C library resolves, are the link's unresolved names: the assembly modules' entry points and
data (to be replaced by port C), the far data taken from the EXE, Borland's library (to be
provided by the port), and the port's own platform names (port_, bc_). That list is the
platform layer's and the assembly replacement's workload; docs/PORT.md has the baseline.

The DOS build is untouched: tools/build.py stages the include directory and the runtime's
include/ only, tools/srcdeps.py hashes them only, and tools/sources.py skips [project]
exclude (the port's directory and the shared C's).
"""
import os, re, sys, json, shutil, ctypes, argparse, subprocess, collections
from concurrent.futures import ThreadPoolExecutor

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
import config, sources, portcfg

CFG, ARGV = portcfg.cli()
P = portcfg.port(CFG)
root = CFG.root
OUT = P.out
PORT = P.dir
FLAGS = ['-x', 'c', f'-std={P.game_std}', '-fsigned-char'] + P.defines + ['-ferror-limit=0', '-fno-color-diagnostics',
         '-fdiagnostics-show-option', '-fno-caret-diagnostics',
         # the runtime's compat.h, which includes the project's portgame.h (from [port] dir), the
         # stand-in headers, then the game's headers and the runtime's portable.h
         '-include', P.compat, '-I', PORT, '-I', P.stand_ins] + [f for d in P.quote_dirs for f in ('-iquote', d)] + [
         # widths: what a 16-bit int and 32-bit long become on a 64-bit host
         '-Wpointer-to-int-cast', '-Wint-to-pointer-cast', '-Wshorten-64-to-32',
         '-Wno-unused-value', '-Wno-parentheses', '-Wno-dangling-else',
         '-Wno-logical-op-parentheses', '-Wno-bitwise-op-parentheses', '-Wno-shift-op-parentheses',
         # compat.h sets #pragma pack(1) for the game's structs on purpose
         '-Wno-pragma-pack']
DIAG = re.compile(r'^(?P<file>[^:\n]+):(?P<line>\d+):(?P<col>\d+): (?P<kind>error|warning): '
                  r'(?P<msg>.*?)(?: \[(?P<flag>-W[^\]]+)\])?$')

# Categories, tried in order; the first that matches a diagnostic takes it. Each test sees the
# message, the warning flag and the text of the source line the diagnostic points at.
CATEGORIES = [
    ('inline asm', lambda m, f, t: re.search(r'^\s*asm\b', t) or "'asm'" in m),
    ('far pointers and segments', lambda m, f, t: re.search(r'\b(MK_FP|FP_SEG|FP_OFF|movedata)\b', t)),
    ('near pointer held in an int', lambda m, f, t:
        f in ('-Wint-to-pointer-cast', '-Wpointer-to-int-cast', '-Wint-conversion',
              '-Wvoid-pointer-to-int-cast')
        or re.search(r'integer to pointer|pointer to integer|pointer from integer|integer from pointer'
                     r'|to smaller integer type|from smaller integer type', m)),
    ('64-bit long and pointer differences', lambda m, f, t:
        f == '-Wshorten-64-to-32' or re.search(r"argument of type 'long'", m)),
    ('Borland library internals', lambda m, f, t:
        re.search(r"no member named 'fd'|undeclared identifier '_(ctype|IS_\w+)'", m)),
    ('missing declaration', lambda m, f, t:
        'implicit declaration of function' in m or 'call to undeclared function' in m
        or 'incomplete type' in m),
    ('char parameters against old-style declarations', lambda m, f, t:
        'conflicting types' in m or 'incompatible function pointer' in m
        or f == '-Wincompatible-function-pointer-types'),
    ('calls without prototypes', lambda m, f, t:
        f in ('-Wimplicit-int', '-Wdeprecated-non-prototype', '-Wstrict-prototypes',
              '-Wknr-promoted-parameter')
        or re.search(r'type specifier missing|without a prototype|old-style', m)),
    ('pointer signedness and types', lambda m, f, t:
        f in ('-Wpointer-sign', '-Wincompatible-pointer-types',
              '-Wincompatible-pointer-types-discards-qualifiers')
        or 'incompatible pointer' in m),
    ('bitfields', lambda m, f, t: 'bit-field' in m or 'bitfield' in (f or '')),
    ('constants, shifts and overflow', lambda m, f, t:
        f in ('-Wconstant-conversion', '-Wshift-count-overflow', '-Wshift-count-negative',
              '-Winteger-overflow', '-Wshift-overflow', '-Wimplicit-int-conversion',
              '-Wtautological-constant-out-of-range-compare', '-Wconstant-logical-operand',
              '-Wshift-negative-value')
        or re.search(r'overflow|changes value|out of range', m)),
    ('unsigned abs and always-true tests', lambda m, f, t:
        f in ('-Wabsolute-value', '-Wtautological-pointer-compare',
              '-Wtautological-unsigned-zero-compare', '-Wtautological-compare')),
    ('empty if bodies (kept for matching)', lambda m, f, t: f == '-Wempty-body'),
    ('return and control flow', lambda m, f, t:
        f in ('-Wreturn-type', '-Wreturn-mismatch', '-Wsometimes-uninitialized', '-Wuninitialized')
        or re.search(r'should (not )?return|does not return', m)),
    ('comments', lambda m, f, t: f == '-Wcomment'),
]
OTHER = 'other'


def category(msg, flag, text):
    for name, test in CATEGORIES:
        if test(msg, flag, text): return name
    return OTHER


def host_cc():
    """The host C compiler: $CC, else clang (the flags here are clang's: Apple's on macOS, the
    distribution's on Linux, MSYS2's on Windows), else cc."""
    return os.environ.get('CC') or ('clang' if shutil.which('clang') else 'cc')


def dos_only(path):
    """A source whose header comment says [port] dos_only (default `port: dos-only`) is
    DOS-specific (UW2: EMS.C's int 67h calls); the port replaces it in its own C and never
    compiles it."""
    return portcfg.dos_only(CFG, path)


_layout = {}
def layout_flags(cc):
    """The flags that give the game's structs Turbo C's layout with this compiler, beyond the
    #pragma pack(1) in compat.h. A compiler for Windows (MinGW, MSYS2's CLANG64) lays bitfields
    out as Microsoft's compiler does, by default: a bitfield of a different type from the one
    before it starts a new unit of its own type (UW2's PLAYER.DAT record came out 0x381 bytes
    instead of 0x37E). -mno-ms-bitfields gives the layout clang and GCC use everywhere else,
    which tools/layoutcheck.py shows is Turbo C's. Decided by the compiler's target, not by
    the Python running this."""
    if cc not in _layout:
        try: m = subprocess.run([cc, '-dumpmachine'], capture_output=True, text=True).stdout
        except OSError: m = ''
        _layout[cc] = ['-mno-ms-bitfields'] if re.search(r'mingw|windows|cygwin|msys', m) else []
    return _layout[cc]


def compile_one(cc, path, extra=()):
    """Compile game source PATH with FLAGS and EXTRA (portbuild.py --web: [port.web] renames)."""
    stem = sources.stem(path)
    obj = os.path.join(OUT, stem + '.o')
    os.makedirs(OUT, exist_ok=True)
    if os.path.exists(obj): os.remove(obj)
    r = subprocess.run([cc] + FLAGS + layout_flags(cc) + list(extra) + ['-c', '-o', obj, path], capture_output=True, text=True,
                       cwd=root)
    return path, r.returncode, r.stderr, obj if r.returncode == 0 and os.path.exists(obj) else None


_lines = {}
def source_line(path, n):
    if path not in _lines:
        try: _lines[path] = open(path, encoding='latin1').read().split('\n')
        except OSError: _lines[path] = []
    ls = _lines[path]
    return ls[n - 1] if 0 < n <= len(ls) else ''


def rel(p):
    """A path from [project] root; runtime/... for the runtime's."""
    return portcfg.display(CFG, p)


NM = shutil.which('nm') or shutil.which('llvm-nm')


def nm(obj, flag):
    """The names an object defines (-gU) or needs (-u). Mach-O puts an underscore before every C
    name; ELF and 64-bit COFF do not."""
    out = set()
    if not NM: return out
    r = subprocess.run([NM, flag, obj], capture_output=True, text=True)
    for l in r.stdout.split('\n'):
        l = l.strip()
        if not l: continue
        name = l.split()[-1]
        out.add(name[1:] if sys.platform == 'darwin' and name.startswith('_') else name)
    return out


def asm_publics():
    """{C name: .ASM path} for every public of an assembly module (the C name drops TASM's _)."""
    out = {}
    for p in sources.all_sources(CFG):
        if not p.upper().endswith('.ASM'): continue
        for m in re.finditer(r'(?im)^\s*public\s+(\S+)', open(p, encoding='latin1').read()):
            for n in m.group(1).split(','):
                n = n.strip()
                if n: out[(n[1:] if n.startswith('_') else n)[:TC_NAME]] = p
    return out


def symbol_table():
    out = {}
    for l in open(CFG.symbols, encoding='latin1'):
        if l.startswith('#'): continue
        f = l.rstrip('\n').split('\t')
        if len(f) >= 2: out[(f[0][1:] if f[0].startswith('_') else f[0])[:TC_NAME]] = f[1]
    return out


# the characters of a C name the compiler and assembler keep, without the leading underscore
TC_NAME = int(CFG.raw.get('port', {}).get('name_limit', CFG.profile.get('c', {}).get('name_limit', 32) - 1))

BORLAND_HEADERS = tuple(CFG.raw.get('port', {}).get('stand_in_headers',
                        ('dos.h', 'alloc.h', 'mem.h', 'io.h', 'stat.h', 'dir.h', 'conio.h', 'bios.h')))
# how the link places data no source defines yet, as the summary names it
EXTRACTED_BY = CFG.raw.get('port', {}).get('extracted_by', 'the link')


def borland_names():
    """Names declared by the port's stand-in headers and compat.h's Borland extensions."""
    names = {}
    for p in [os.path.join(P.stand_ins, h) for h in BORLAND_HEADERS] + [P.compat]:
        h = os.path.basename(p)
        for m in re.finditer(r'\b(\w+)\s*\(|\bextern\b[^;(]*\b(\w+)\s*\[', open(p).read()):
            n = m.group(1) or m.group(2)
            if n in ('if', 'while', 'sizeof', 'return', 'defined'): continue
            names.setdefault(n, h)
    return names


_libc = []
def libc_has(name):
    """Whether the host C library has the name: the process's own symbols on macOS and Linux,
    the Universal C Runtime (or msvcrt) on Windows."""
    if not _libc:
        libs = [None] if os.name != 'nt' else ['ucrtbase', 'msvcrt']
        for n in libs:
            try: _libc.append(ctypes.CDLL(n)); break
            except OSError: pass
        else: _libc.append(None)
    try:
        _libc[0][name]; return True
    except (AttributeError, OSError, TypeError):
        return False


def main(argv):
    ap = argparse.ArgumentParser(description='Compile the game sources for the host (Milestone 1).')
    ap.add_argument('--cc', default=host_cc())
    ap.add_argument('--diag', metavar='FILE')
    ap.add_argument('--list', metavar='CATEGORY')
    ap.add_argument('--json', action='store_true', help='also write <build>/<port out>/summary.json')
    a = ap.parse_args(argv)
    os.makedirs(OUT, exist_ok=True)
    srcs = portcfg.game_sources(CFG)    # with the shared C: the record and replay hooks' code, which the port links too
    if a.diag:
        want = a.diag.upper()
        srcs = [p for p in srcs if sources.stem(p) == os.path.splitext(os.path.basename(want))[0]]
        if not srcs: raise SystemExit(f'portcheck.py: no source {a.diag}')
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        results = list(ex.map(lambda p: compile_one(a.cc, p), srcs))
    if not a.diag:                     # the full run's log stays the full run's
      with open(os.path.join(OUT, 'portcheck.log'), 'w') as log:
        for path, rc, err, obj in results:
            log.write(f'=== {rel(path)} (exit {rc})\n{err}\n')

    seen = set(); diags = []
    for path, rc, err, obj in results:
        for l in err.split('\n'):
            m = DIAG.match(l)
            if not m: continue
            f = rel(m['file']); line = int(m['line'])
            key = (f, line, int(m['col']), m['kind'], m['msg'])
            if key in seen: continue
            seen.add(key)
            text = source_line(os.path.join(root, f), line)
            diags.append(dict(file=f, line=line, kind=m['kind'], msg=m['msg'], flag=m['flag'],
                              cat=category(m['msg'], m['flag'], text), text=text.strip()))

    if a.diag:
        for d in diags:
            print(f"{d['file']}:{d['line']}: {d['kind']}: [{d['cat']}] {d['msg']}"
                  + (f" [{d['flag']}]" if d['flag'] else ''))
        return 0
    if a.list:
        for d in diags:
            if d['cat'].startswith(a.list):
                print(f"{d['file']}:{d['line']}: {d['kind']}: {d['msg']}\n    {d['text']}")
        return 0

    # the summary
    ok = [p for p, rc, e, o in results if o]
    clean = [p for p, rc, e, o in results if o and not any(d['file'] == rel(p) for d in diags)]
    print(f'{len(srcs)} C sources: {len(ok)} compile, {len(srcs) - len(ok)} fail; '
          f'{len(clean)} with no diagnostic in the file itself')
    errs = [d for d in diags if d['kind'] == 'error']; warns = [d for d in diags if d['kind'] == 'warning']
    print(f'{len(errs)} errors and {len(warns)} warnings (each counted once)\n')
    bycat = collections.OrderedDict((c, [0, 0]) for c, _ in CATEGORIES)
    bycat[OTHER] = [0, 0]
    for d in diags: bycat[d['cat']][0 if d['kind'] == 'error' else 1] += 1
    print(f"{'category':44} {'errors':>7} {'warnings':>9}")
    for c, (e, w) in bycat.items():
        if e or w: print(f'{c:44} {e:7} {w:9}')
    print()
    byfile = collections.defaultdict(lambda: collections.Counter())
    for d in diags: byfile[d['file']][d['kind']] += 1
    failed = {rel(p) for p, rc, e, o in results if not o}
    print(f"{'file':32} {'errors':>7} {'warnings':>9}  top categories")
    for f in sorted(byfile, key=lambda f: (-byfile[f]['error'], -byfile[f]['warning'], f)):
        cats = collections.Counter(d['cat'] for d in diags if d['file'] == f)
        top = ', '.join(f'{c} {n}' for c, n in cats.most_common(3))
        print(f"{f:32} {byfile[f]['error']:7} {byfile[f]['warning']:9}  {top}"
              + ('  (no object)' if f in failed else ''))
    nodiag_fail = sorted(failed - set(byfile))
    for f in nodiag_fail: print(f'{f:32}  failed with no parsed diagnostic; see {os.path.relpath(os.path.join(OUT, "portcheck.log"), root)}')
    print()

    # what a link would still need
    defined, undefined = set(), collections.defaultdict(set)
    for p, rc, e, o in results:
        if not o: continue
        defined |= nm(o, '-gU')
        for n in nm(o, '-u'): undefined[n].add(sources.stem(p))
    unresolved = {n: s for n, s in undefined.items() if n not in defined}
    pubs, syms, bor = asm_publics(), symbol_table(), borland_names()
    failed_text = {rel(p): open(p, encoding='latin1').read() for p, rc, e, o in results if not o}
    dosonly_text = {rel(p): open(p, encoding='latin1').read() for p in sources.all_sources(CFG)
                    if p.upper().endswith('.C') and dos_only(p)}
    def defined_in(n, texts):
        for f, text in texts.items():
            if re.search(r'(?m)^(?!\s|#|/\*|extern\b|static\b)[^;=(]*\b' + re.escape(n) + r'\s*(\(|\[|=|;|,)', text):
                return f
    def defined_in_failed(n):
        for f, text in failed_text.items():
            if re.search(r'(?m)^(?!\s|#|/\*|extern\b|static\b)[^;=(]*\b' + re.escape(n) + r'\s*(\(|\[|=|;|,)', text):
                return f
    groups = collections.defaultdict(list)
    for n in sorted(unresolved):
        t = n[:TC_NAME]                    # Turbo C and TASM keep 32 characters with the _
        if n.startswith('port_') or n.startswith('bc_'): g = 'port layer (compat.h: port_, bc_)'
        elif t in pubs: g = 'asm: ' + rel(pubs[t])
        elif n in bor: g = f'Borland library ({bor[n]})'
        elif libc_has(n): continue
        elif defined_in_failed(n): g = 'C that does not compile yet: ' + defined_in_failed(n)
        elif defined_in(n, dosonly_text): g = 'C replaced by the port (dos-only): ' + defined_in(n, dosonly_text)
        elif t in syms and syms[t].startswith('DS:'): g = f'no source: DGROUP gaps placed by {EXTRACTED_BY}'
        elif t in syms: g = 'no source: far data taken from the EXE, or a name inside another array (' + syms[t].split(':')[0] + ')'
        else: g = 'unknown (not in symbols.tsv)'
        groups[g].append(n + ('' if t == n else f' (DOS: {t})'))
    total = sum(len(v) for v in groups.values())
    print(f'{total} unresolved names for a link, by where they come from:')
    order = sorted(groups, key=lambda g: (not g.startswith('asm'), g))
    for g in order:
        print(f'  {g}: {len(groups[g])}')
        print('    ' + ' '.join(groups[g]))
    clash = sorted(n for n in defined if libc_has(n))
    print(f'\n{len(clash)} names the game defines that the host C library also has '
          '(the game\'s definition wins in the link, but the port should rename them):')
    print('    ' + ' '.join(clash))
    if a.json:
        with open(os.path.join(OUT, 'summary.json'), 'w') as f:
            json.dump(dict(sources=len(srcs), compiled=len(ok), clean=len(clean),
                           categories=bycat, files={k: dict(v) for k, v in byfile.items()},
                           unresolved={g: v for g, v in groups.items()}, clashes=clash,
                           callers={n: sorted(s) for n, s in unresolved.items()}), f, indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main(ARGV))
