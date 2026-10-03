"""Build the native port (docs/port.md): compile every game C source for the host as
tools/portcheck.py does, compile the port's own C ([port] dir), and link them into
<build>/<[port] out>/<[port] exe>.

    python3 tools/portbuild.py [--config PATH]           build and link; exit 1 if any step fails
    python3 tools/portbuild.py [--config PATH] --run     then run it, passing PORT_ARGS from the environment
    python3 tools/portbuild.py [--config PATH] --debug   the debug build: -g and UBSan's -fsanitize=null,
                                                         in <out>-debug; a null dereference is reported
                                                         with its file and line and the program goes on,
                                                         so a replay lists every one it meets
    python3 tools/portbuild.py [--config PATH] --coverage  clang's source-based coverage, in <out>-cov:
                                                         each run writes a .profraw (LLVM_PROFILE_FILE),
                                                         for tools/coverage.py

The port's own C is compiled with its headers and the game's; the platform backend
([port] backend, default sdl3: the files under <dir>/platform/<backend>/) with the backend's
flags from pkg-config, and the link takes its libraries. Platform backends other than the one
chosen are not compiled. What the port does not replace yet is still the generated stubs
([port] stubs, written by tools/portstubs.py), and the port stops at the first one it reaches.
The DOS build is untouched.

The port's own C in the [port] optimise directories is compiled with -O2 (UW2: the translated
assembly modules, the machine they run on and the graphics C, which made the replays two to
four times faster); the rest of the port's C and all of the game's C stay unoptimised, and the
debug and coverage builds compile everything without optimisation.

Third-party code is never committed (docs/third-party.md): [[port.vendor]] entries are C a
setup script fetched (UW2: Nuked OPL3, tools/setup-sound.sh), compiled with their own flags
when present, each defining its macro for the one port file that uses it; [[port.pkg]]
entries are libraries linked when pkg-config finds them (UW2: libmt32emu). Without either the
port builds and that part is silent.
"""
import os, re, sys, argparse, subprocess
from concurrent.futures import ThreadPoolExecutor

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
import sources, portcfg, portcheck

CFG, ARGV = portcfg.cli()
P = portcfg.port(CFG)
root = CFG.root
OUT = P.out
PORT = P.dir
# [sound] extensions: the project's own C for the sound library (runtime/port/sound/yamaha.h's
# struct AilFmExt), compiled with the port's; one inside the port's directory is already
SOUND_EXT = portcfg.sound(CFG).extensions
EXE = os.path.join(OUT, P.exe)
# The port's own C: C11, with the port's headers and the game's (port C that includes a game
# header includes compat.h first, and gets the stand-in headers through [port] stand_ins).
PORT_FLAGS = ['-x', 'c', f'-std={P.port_std}', '-fsigned-char'] + P.defines + ['-Wall', '-Wno-comment', '-Wno-unused-function',
              '-Wno-pragma-pack', '-I', PORT, '-I', os.path.join(PORT, 'platform'),
              '-I', P.stand_ins, '-iquote', CFG.include]
# The port's own C that only computes ([port] optimise): compiled with -O2 in the normal build.
OPTIMISED = P.optimise
OPT = ['-O2']
# The one backend the port builds with: only files under <dir>/platform/<backend>/ see its
# headers, and the link takes its libraries.
BACKEND = P.backend


def pkg_config(*args):
    r = subprocess.run(['pkg-config'] + list(args) + [BACKEND], capture_output=True, text=True)
    if r.returncode:
        raise SystemExit(f'portbuild.py: pkg-config cannot find {BACKEND} (for SDL3: brew install sdl3, or tools/setup-libs.sh on Linux)')
    return r.stdout.split()


def port_sources():
    """Every .c under the port's directory but the stand-in headers, and the platform backends
    other than BACKEND."""
    out = []
    plat = os.path.join(PORT, 'platform')
    for d, _, fs in os.walk(PORT):
        if P.stand_ins in d: continue
        if d.startswith(plat + os.sep) and os.path.relpath(d, plat).split(os.sep)[0] != BACKEND: continue
        out += [os.path.join(d, f) for f in sorted(fs) if f.endswith('.c')]
    return sorted(out) + [x for x in SOUND_EXT if not x.startswith(PORT + os.sep) and os.path.exists(x)]


def port_object(out, path):
    """The object of port source PATH in build directory OUT (a [sound] extension outside the
    port's directory is named by its path from [project] root)."""
    if path.startswith(PORT + os.sep):
        return os.path.join(out, 'port', os.path.relpath(path, PORT).replace(os.sep, '_')[:-2] + '.o')
    return os.path.join(out, 'port', 'ext_' + os.path.relpath(path, root).replace(os.sep, '_')[:-2] + '.o')


def is_backend(path):
    return os.path.join(PORT, 'platform', BACKEND) + os.sep in path


VENDOR_DIRS = [v['dir'] for v in P.vendor]


def deps():
    """The third-party parts found ([[port.vendor]] fetched sources, [[port.pkg]] libraries):
    ({port file: compile flags}, link flags, extra sources, [what was found or is missing])."""
    cflags, libs, extra, said = {}, [], [], []
    for v in P.vendor:
        srcs = [os.path.join(v['dir'], f) for f in v.get('sources', [])]
        if srcs and all(os.path.exists(f) for f in srcs):
            cflags.setdefault(v.get('for', ''), []).extend([f"-D{v['define']}", '-I', v['dir']])
            extra += srcs; said.append(v['name'])
        else: said.append(v.get('missing', f"no {v['name']}"))
    for k in P.pkg:
        r = subprocess.run(['pkg-config', '--cflags', '--libs', k['name']], capture_output=True, text=True)
        if r.returncode == 0:
            flags = r.stdout.split()
            cflags.setdefault(k.get('for', ''), []).extend([f"-D{k['define']}"] + [f for f in flags if f.startswith('-I')])
            libs += [f for f in flags if not f.startswith('-I')]
            said.append(k.get('label', k['name']))
        else: said.append(k.get('missing', f"no {k.get('label', k['name'])}"))
    return cflags, libs, extra, said


def sound_deps():
    """(the flags of every port file that takes some, link flags, extra sources): deps() as
    tools/fuzzasm.py reads it."""
    c, l, e, _ = deps()
    return c, l, e


def compile_port(cc, path, dep_cflags=None):
    if any(path.startswith(d + os.sep) for d in VENDOR_DIRS):     # third-party: its own flags, optimised
        obj = os.path.join(OUT, 'deps', os.path.basename(path)[:-2] + '.o')
        os.makedirs(os.path.dirname(obj), exist_ok=True)
        r = subprocess.run([cc, '-x', 'c', '-std=c99', '-O2', '-w', '-c', '-o', obj, path],
                           capture_output=True, text=True, cwd=root)
        return path, r.returncode, r.stderr if r.returncode else '', obj if r.returncode == 0 else None
    obj = port_object(OUT, path)
    os.makedirs(os.path.dirname(obj), exist_ok=True)
    extra = pkg_config('--cflags') if is_backend(path) else []
    for f, fl in (dep_cflags or {}).items():
        if f and path.endswith(os.sep + f.replace('/', os.sep)): extra = extra + list(fl)
    opt = OPT if any(path.startswith(d) for d in OPTIMISED) else []
    r = subprocess.run([cc] + opt + PORT_FLAGS + extra + ['-c', '-o', obj, path], capture_output=True, text=True, cwd=root)
    return path, r.returncode, r.stderr, obj if r.returncode == 0 else None


def main(argv):
    ap = argparse.ArgumentParser(description='Build and link the native port.')
    ap.add_argument('--cc', default=portcheck.host_cc())
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--debug', action='store_true')
    ap.add_argument('--coverage', action='store_true')
    a = ap.parse_args(argv)
    global OUT, EXE
    link_extra = []
    global OPT
    if a.debug or a.coverage: OPT = []
    if a.debug:
        OUT = portcfg.variant_out(CFG, 'debug')
        EXE = os.path.join(OUT, P.exe)
        portcheck.OUT = OUT
        san = ['-g', '-fsanitize=null']
        portcheck.FLAGS = portcheck.FLAGS + san
        PORT_FLAGS.extend(san)
        link_extra = ['-fsanitize=null']
    if a.coverage:
        OUT = portcfg.variant_out(CFG, 'cov')
        EXE = os.path.join(OUT, P.exe)
        portcheck.OUT = OUT
        cov = ['-fprofile-instr-generate', '-fcoverage-mapping']
        portcheck.FLAGS = portcheck.FLAGS + cov
        PORT_FLAGS.extend(cov)
        link_extra = ['-fprofile-instr-generate']
    os.makedirs(OUT, exist_ok=True)
    game = portcfg.game_sources(CFG)     # with the shared C (the record and replay hooks' code)
    snd_cflags, snd_libs, snd_extra, said = deps()
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        gres = list(ex.map(lambda p: portcheck.compile_one(a.cc, p), game))
        pres = list(ex.map(lambda p: compile_port(a.cc, p, snd_cflags), port_sources() + snd_extra))
    bad = [(p, e) for p, rc, e, o in gres + pres if not o]
    for p, e in bad:
        print(f'{os.path.relpath(p, root)}: does not compile\n' + '\n'.join(l for l in e.split('\n') if 'error' in l)[:2000])
    if bad: return 1
    objs = [o for p, rc, e, o in gres + pres]
    print(f'compiled {len(gres)} game sources and {len(pres)} port sources')
    warn = [(p, e) for p, rc, e, o in pres if o and 'warning' in e]
    for p, e in warn:
        print(f'{os.path.relpath(p, root)}: warnings\n' + '\n'.join(l for l in e.split('\n') if 'warning' in l)[:2000])
    if said: print('third-party: ' + ', '.join(said))
    for x in SOUND_EXT:
        if not os.path.exists(x): print(f'[sound] extensions: no {os.path.relpath(x, root)}')
    # the sound drivers' threads: in the C library on macOS and on Linux's glibc 2.34 and later,
    # in winpthreads on Windows (MinGW), which -pthread links
    threads = [] if sys.platform == 'darwin' else ['-pthread']
    r = subprocess.run([a.cc, '-o', EXE] + link_extra + objs + pkg_config('--libs') + snd_libs + threads,
                       capture_output=True, text=True, cwd=root)
    if not os.path.exists(EXE) and os.path.exists(EXE + '.exe'): EXE += '.exe'     # Windows
    if r.returncode:
        und = sorted(set(re.findall(r'"_?([A-Za-z_]\w*)", referenced from', r.stderr)))
        print(f'link failed: {len(und)} undefined names' + (': ' + ' '.join(und) if und else ''))
        print(r.stderr[-3000:])
        return 1
    print(f'linked {os.path.relpath(EXE, root)} ({os.path.getsize(EXE)} bytes)')
    if a.run:
        r = subprocess.run([EXE] + os.environ.get('PORT_ARGS', '').split(), cwd=root)
        print(f'exit status {r.returncode}')
    return 0


if __name__ == '__main__':
    sys.exit(main(ARGV))
