"""Build the native port (docs/port.md): compile every game C source for the host as
tools/portcheck.py does, compile the port's own C (the project's, [port] dir, and Exhume's
runtime/port, where it is: nothing is copied), and link them into
<build>/<[port] out>/<[port] exe>.

    python3 tools/portbuild.py [--config PATH]           build and link; exit 1 if any step fails
    python3 tools/portbuild.py [--config PATH] --run     then run it, passing PORT_ARGS from the environment
    python3 tools/portbuild.py [--config PATH] --debug   the debug build: -g and UBSan's -fsanitize=null,
                                                         in <out>-debug; a null dereference is reported
                                                         with its file and line and the program goes on,
                                                         so a replay lists every one it meets
    python3 tools/portbuild.py [--config PATH] --debug --sanitize address   the debug build with other
                                                         sanitizers in place of null (a comma-separated
                                                         list for -fsanitize=: address, array-bounds),
                                                         for the host hazard audits
                                                         (skills/port-and-verify); replay.py verify
                                                         --debug replays every session in it
    python3 tools/portbuild.py [--config PATH] --coverage  clang's source-based coverage, in <out>-cov:
                                                         each run writes a .profraw (LLVM_PROFILE_FILE),
                                                         for tools/coverage.py
    python3 tools/portbuild.py [--config PATH] --release  the build players' packages are made from
                                                         (tools/package.py), in <out>-release: as the
                                                         normal build, but each [[port.vendor]] library
                                                         is a shared library beside the program (so a
                                                         user can replace it, as its LGPL asks), and
                                                         the program looks for its libraries beside
                                                         itself and in ../lib (Linux) and
                                                         ../Frameworks (macOS) only; --arch A
                                                         (repeatable, macOS) builds for each
                                                         architecture A, a universal binary
    python3 tools/portbuild.py [--config PATH] --web     the WebAssembly build, with Emscripten's emcc
                                                         (tools/setup-emsdk.sh) and the libraries
                                                         tools/setup-libs.sh built with SETUP_WEB=1
                                                         ([port] libs beside it, libs-web): the page's
                                                         program in <build>/web (<exe>.js and .wasm,
                                                         for the browser), and the same objects linked
                                                         for Node.js in <build>/web-node, with <exe> a
                                                         script that runs it, so tools/replay.py replays
                                                         the sessions in it (EXHUME_PORT)

The port's own C is compiled with its headers and the game's: the project's directory first,
whose bindings (portgame.h, asmgame.h, ailgame.h) the runtime's headers include, then the
runtime's (docs/port.md, "The runtime"); the platform backend ([port] backend, default sdl3:
the files under the runtime's platform/<backend>/) with the backend's flags from pkg-config,
and the link takes its libraries. Platform backends other than the one
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
port builds and that part is silent. Libraries tools/setup-libs.sh built into [port] libs
(default tools/libs) are found without setting PKG_CONFIG_PATH, and on Linux the normal build
is linked to find them there when run.

For a Windows target (MinGW, MSYS2's CLANG64) the game's C and the port's are compiled with
portcheck.layout_flags (-mno-ms-bitfields, Turbo C's bitfield layout), and the program gets
its icon ([port] icon, else [package]'s .ico; tools/icons.py) as a resource, through
llvm-windres or windres; a release build without it fails.
"""
import os, re, sys, shutil, argparse, subprocess
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
# header includes compat.h first, and gets the stand-in headers through [port] stand_ins):
# portcfg's include_dirs, the project's first, then the game's headers and portable.h.
PORT_FLAGS = ['-x', 'c', f'-std={P.port_std}', '-fsigned-char'] + P.defines + ['-Wall', '-Wno-comment', '-Wno-unused-function',
              '-Wno-pragma-pack'] + [f for d in P.include_dirs for f in ('-I', d)] + \
             [f for d in P.quote_dirs for f in ('-iquote', d)]
# The port's own C that only computes ([port] optimise): compiled with -O2 in the normal build.
OPTIMISED = P.optimise
OPT = ['-O2']
# The one backend the port builds with: only files under <dir>/platform/<backend>/ see its
# headers, and the link takes its libraries.
BACKEND = P.backend
LIBS = P.libs
RELEASE = False
ARCHS = []      # -arch flags for a universal macOS build
WEB = []        # --web: the flags every object is compiled with (threads: shared memory)


def pkg_env():
    """The environment for pkg-config: [port] libs (tools/setup-libs.sh) searched first."""
    env = dict(os.environ)
    pc = os.path.join(LIBS, 'lib', 'pkgconfig')
    if os.path.isdir(pc):
        env['PKG_CONFIG_PATH'] = pc + (os.pathsep + env['PKG_CONFIG_PATH'] if env.get('PKG_CONFIG_PATH') else '')
    return env


def strip_rpaths(flags):
    """Link flags without any run path a .pc file adds (SDL3's sdl3.pc has
    -Wl,-rpath,${libdir}): the release program must look only beside itself, so a run path
    into the build tree cannot come before $ORIGIN/../lib."""
    out, skip = [], False
    for f in flags:
        if skip: skip = False; continue
        if f == '-Wl,-rpath' or f == '-rpath': skip = True; continue     # the path follows
        if f.startswith('-Wl,'):
            parts, keep, i = f[4:].split(','), [], 0
            while i < len(parts):
                if parts[i] in ('-rpath', '--rpath', '-R'): i += 2; continue
                if parts[i].startswith(('-rpath=', '--rpath=')) or parts[i] == '--enable-new-dtags': i += 1; continue
                keep.append(parts[i]); i += 1
            if keep: out.append('-Wl,' + ','.join(keep))
            continue
        out.append(f)
    return out


def pkg_config(*args):
    r = subprocess.run(['pkg-config'] + list(args) + [BACKEND], capture_output=True, text=True, env=pkg_env())
    if r.returncode:
        raise SystemExit(f'portbuild.py: pkg-config cannot find {BACKEND} (for SDL3: brew install sdl3, or tools/setup-libs.sh on Linux)')
    return r.stdout.split()


def windows_target(cc):
    return bool(portcheck.layout_flags(cc))


def shared_name(v):
    """A [[port.vendor]] library's file in the release build, by the target's convention."""
    base = v.get('shared') or re.sub(r'[^a-z0-9]', '', v['name'].lower())
    if sys.platform == 'darwin': return f'lib{base}.dylib'
    if os.name == 'nt' or sys.platform in ('msys', 'cygwin') or os.environ.get('MSYSTEM'): return f'{base}.dll'
    return f'lib{base}.so'


def windows_resource(cc):
    """On a Windows target, the program's icon (portcfg.icon_ico) as a resource object for the
    link, compiled by llvm-windres (MSYS2 CLANG64's llvm package) or windres; [] elsewhere, or
    when there is no icon or no resource compiler (the program then has Windows's default
    icon; a release build stops)."""
    if not windows_target(cc): return []
    ico = portcfg.icon_ico(CFG)
    tool = shutil.which('llvm-windres') or shutil.which('windres')
    print(f'portbuild.py: Windows target; icon {ico and os.path.relpath(ico, root)}, resource compiler {tool or "none"}')
    if not ico or not tool:
        if RELEASE: sys.exit('portbuild.py: a release build for Windows needs its icon (tools/icons.py) and llvm-windres or windres')
        return []
    rc, obj = os.path.join(OUT, P.exe + '.rc'), os.path.join(OUT, P.exe + '-res.o')
    with open(rc, 'w') as f: f.write('1 ICON "%s"\n' % ico.replace(os.sep, '/').replace('\\', '/'))
    cmd = [tool, '-O', 'coff', '-i', rc, '-o', obj]
    if 'llvm' in os.path.basename(tool):        # for the compiler's target, not the tool's host
        m = subprocess.run([cc, '-dumpmachine'], capture_output=True, text=True).stdout.strip()
        if m: cmd.append('--target=' + m)
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=root)
    if r.returncode:
        print(f'portbuild.py: {os.path.basename(tool)} failed; the program gets no icon\n{r.stderr[-1000:]}')
        if RELEASE: sys.exit('portbuild.py: a release build for Windows needs its icon')
        return []
    return [obj]


def port_sources():
    """The port's own C (portcfg.port_sources: the project's and the runtime's), and the
    [sound] extensions outside the project's port directory."""
    return portcfg.port_sources(CFG) + [x for x in SOUND_EXT if not x.startswith(PORT + os.sep) and os.path.exists(x)]


def port_object(out, path):
    """The object of port source PATH in build directory OUT: the project's under port/, the
    runtime's under port/rt_ (a project file may share a runtime file's name), a [sound]
    extension outside the port's directory by its path from [project] root."""
    if path.startswith(P.runtime_port + os.sep):
        return os.path.join(out, 'port', 'rt_' + os.path.relpath(path, P.runtime_port).replace(os.sep, '_')[:-2] + '.o')
    if path.startswith(PORT + os.sep):
        return os.path.join(out, 'port', os.path.relpath(path, PORT).replace(os.sep, '_')[:-2] + '.o')
    return os.path.join(out, 'port', 'ext_' + os.path.relpath(path, root).replace(os.sep, '_')[:-2] + '.o')


def is_backend(path):
    return any(os.path.join(t, 'platform', BACKEND) + os.sep in path for t in (PORT, P.runtime_port))


VENDOR_DIRS = [v['dir'] for v in P.vendor]


def _for(entry):
    """the port files an entry's flags go to: 'for' as one path or a list of them"""
    f = entry.get('for', '')
    return [f] if isinstance(f, str) else list(f)


def deps():
    """The third-party parts found ([[port.vendor]] fetched sources, [[port.pkg]] libraries):
    ({port file: compile flags}, link flags, extra sources, [what was found or is missing])."""
    cflags, libs, extra, said = {}, [], [], []
    for v in P.vendor:
        srcs = [os.path.join(v['dir'], f) for f in v.get('sources', [])]
        if srcs and all(os.path.exists(f) for f in srcs):
            for f in _for(v): cflags.setdefault(f, []).extend([f"-D{v['define']}", '-I', v['dir']])
            extra += srcs; said.append(v['name'])
        else: said.append(v.get('missing', f"no {v['name']}"))
    for k in P.pkg:
        r = subprocess.run(['pkg-config', '--cflags', '--libs', k['name']], capture_output=True, text=True, env=pkg_env())
        if r.returncode == 0:
            flags = r.stdout.split()
            for f in _for(k): cflags.setdefault(f, []).extend([f"-D{k['define']}"] + [x for x in flags if x.startswith('-I')])
            libs += [f for f in flags if not f.startswith('-I')]
            said.append(k.get('label', k['name']))
        else: said.append(k.get('missing', f"no {k.get('label', k['name'])}"))
    return cflags, libs, extra, said


def sound_deps():
    """(the flags of every port file that takes some, link flags, extra sources): deps() as
    tools/fuzzasm.py reads it."""
    c, l, e, _ = deps()
    return c, l, e


def vendor_of(path):
    return next((v for v in P.vendor if path.startswith(v['dir'] + os.sep)), None)


def compile_port(cc, path, dep_cflags=None):
    v = vendor_of(path)
    if v:                                   # third-party: its own flags, optimised
        obj = os.path.join(OUT, 'deps', os.path.basename(path)[:-2] + '.o')
        os.makedirs(os.path.dirname(obj), exist_ok=True)
        pic = ['-fPIC'] if RELEASE and not windows_target(cc) else []
        r = subprocess.run([cc] + ARCHS + WEB + pic + ['-x', 'c', '-std=c99', '-O2', '-w', '-c', '-o', obj, path],
                           capture_output=True, text=True, cwd=root)
        return path, r.returncode, r.stderr if r.returncode else '', obj if r.returncode == 0 else None
    obj = port_object(OUT, path)
    os.makedirs(os.path.dirname(obj), exist_ok=True)
    extra = pkg_config('--cflags') if is_backend(path) else []
    for f, fl in (dep_cflags or {}).items():
        if f and path.endswith(os.sep + f.replace('/', os.sep)): extra = extra + list(fl)
    opt = OPT if any(path.startswith(d) for d in OPTIMISED) else []
    # the game's struct layout (portcheck.layout_flags): port C that includes a game header
    # must see the same layout as the game's C
    r = subprocess.run([cc] + ARCHS + opt + PORT_FLAGS + portcheck.layout_flags(cc) + extra + ['-c', '-o', obj, path],
                       capture_output=True, text=True, cwd=root)
    return path, r.returncode, r.stderr, obj if r.returncode == 0 else None


def link_shared(cc, v, objs):
    """A [[port.vendor]] library as a shared library of its own in OUT (the release build)."""
    lib = os.path.join(OUT, shared_name(v))
    cmd = [cc] + ARCHS + ['-shared', '-o', lib] + objs
    if lib.endswith('.dylib'): cmd += ['-dynamiclib', '-install_name', '@rpath/' + os.path.basename(lib)]
    elif lib.endswith('.so'): cmd += ['-Wl,-soname,' + os.path.basename(lib)]
    else: cmd += ['-Wl,--out-implib,' + os.path.join(OUT, 'lib' + os.path.basename(lib) + '.a')]     # libX.dll.a
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=root)
    if r.returncode: sys.exit(f'portbuild.py: {v["name"]} as a shared library does not link\n{r.stderr[-2000:]}')
    print(f'shared library {os.path.relpath(lib, root)}')
    return lib


def web_out(name):
    """The web build's directories, beside [port] out's: <build>/web (the page's program) and
    <build>/web-node (the Node.js one)."""
    return os.path.join(os.path.dirname(P.out), name)


def web_mismatches(stderr, allow):
    """wasm-ld's signature mismatches in a link's messages: a direct call whose declaration
    differs from the definition is linked as a trap, with only a warning, so each is a failure
    unless ALLOW ({name: why}; [port.web.allow_mismatch]) names it. True when one is not."""
    blocks = re.split(r'\n(?=wasm-ld: )', stderr)
    bad = []
    for b in blocks:
        m = re.match(r'wasm-ld: warning: function signature mismatch: (\S+)', b)
        if not m: continue
        if m.group(1) in allow: print(f'portbuild.py: signature mismatch {m.group(1)} allowed: {allow[m.group(1)]}')
        else: bad.append(b.strip())
    if bad:
        print('portbuild.py: the link makes these calls traps (a declaration differs from the definition; '
              '[port.web.renames] and an adapter, or [port.web.allow_mismatch] with the reason):\n' + '\n'.join(bad))
    return bool(bad)


def link_web(cc, objs, libs):
    """--web's link, twice from the same objects: the page's program (EXE, <exe>.js and .wasm
    in <build>/web: a module the page starts itself, with the file system's IDBFS for the saved
    games) and a Node.js one in <build>/web-node, which reads and writes the host's files
    (NODERAWFS), with the script <exe> beside it that runs it under node."""
    # EMULATE_FUNCTION_POINTER_CASTS: the game's C calls functions through pointers of other
    # types (UW1's LOADGR.C passes adrnew_vram(void) to gronk_gr as a function of an int), which
    # Turbo C's and the desktop's calling conventions let through and WebAssembly's call_indirect
    # traps on ("function signature mismatch")
    common = ['-pthread', '-O2', '-sPTHREAD_POOL_SIZE=4', '-sALLOW_MEMORY_GROWTH=1', '-sINITIAL_MEMORY=128MB',
              '-sSTACK_SIZE=1MB', '-sDEFAULT_PTHREAD_STACK_SIZE=1MB', '-sEXIT_RUNTIME=1',
              '-sEMULATE_FUNCTION_POINTER_CASTS=1']
    name = os.path.basename(EXE)
    stem = re.sub(r'\.exe$', '', P.exe)
    page = [cc, '-o', EXE] + objs + libs + common + ['-lidbfs.js', '-sENVIRONMENT=web,worker', '-sMODULARIZE=1',
            f'-sEXPORT_NAME={stem}', '-sEXPORTED_RUNTIME_METHODS=FS,IDBFS,callMain,UTF8ToString',
            '-sINVOKE_RUN=0', '-sEXPORTED_FUNCTIONS=_main,_web_open_settings,_exhume_quit,_audio_underruns,_web_test_stop']
    nodeout = web_out('web-node'); os.makedirs(nodeout, exist_ok=True)
    node = [cc, '-o', os.path.join(nodeout, name)] + objs + libs + common + ['-sENVIRONMENT=node', '-sNODERAWFS=1']
    for cmd in (page, node):
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=root)
        if r.returncode:
            print(f'link failed ({os.path.relpath(cmd[2], root)})\n' + r.stderr[-3000:])
            return 1
        if web_mismatches(r.stderr, P.web_allow): return 1
    # Emscripten's output is CommonJS, which node reads as an ES module under a package.json
    # that says "type": "module" (UW2's, for its tools' .mjs): this directory says otherwise
    with open(os.path.join(nodeout, 'package.json'), 'w') as f: f.write('{"type": "commonjs"}\n')
    wrap = os.path.join(nodeout, stem)
    with open(wrap, 'w') as f: f.write(f'#!/bin/sh\nexec node "{os.path.join(nodeout, name)}" "$@"\n')
    os.chmod(wrap, 0o755)
    print(f'linked {os.path.relpath(EXE, root)} and {os.path.relpath(wrap, root)}')
    return 0


def main(argv):
    ap = argparse.ArgumentParser(description='Build and link the native port.')
    ap.add_argument('--cc', default=portcheck.host_cc())
    ap.add_argument('--run', action='store_true')
    ap.add_argument('--debug', action='store_true')
    ap.add_argument('--sanitize', default='null')
    ap.add_argument('--coverage', action='store_true')
    ap.add_argument('--frame-single', action='store_true', help='the EMS frame mapped once, as WebAssembly has it (a desktop check)')
    ap.add_argument('--release', action='store_true')
    ap.add_argument('--arch', action='append', default=[])
    ap.add_argument('--web', action='store_true', help='the WebAssembly build (emcc): the page\'s and a Node.js one')
    a = ap.parse_args(argv)
    global OUT, EXE, RELEASE, ARCHS, LIBS, WEB
    link_extra = []
    global OPT
    if a.debug or a.coverage: OPT = []
    if a.debug:
        OUT = portcfg.variant_out(CFG, 'debug')
        EXE = os.path.join(OUT, P.exe)
        portcheck.OUT = OUT
        san = ['-g', f'-fsanitize={a.sanitize}']
        portcheck.FLAGS = portcheck.FLAGS + san
        PORT_FLAGS.extend(san)
        link_extra = [f'-fsanitize={a.sanitize}']
    if a.coverage:
        OUT = portcfg.variant_out(CFG, 'cov')
        EXE = os.path.join(OUT, P.exe)
        portcheck.OUT = OUT
        cov = ['-fprofile-instr-generate', '-fcoverage-mapping']
        portcheck.FLAGS = portcheck.FLAGS + cov
        PORT_FLAGS.extend(cov)
        link_extra = ['-fprofile-instr-generate']
    if a.frame_single:
        OUT = portcfg.variant_out(CFG, 'single')
        EXE = os.path.join(OUT, P.exe)
        portcheck.OUT = OUT
        portcheck.FLAGS = portcheck.FLAGS + ['-DPORT_FRAME_SINGLE']
        PORT_FLAGS.append('-DPORT_FRAME_SINGLE')
    if a.release:
        RELEASE = True
        OUT = portcfg.variant_out(CFG, 'release')
        EXE = os.path.join(OUT, P.exe)
        portcheck.OUT = OUT
    if a.arch:
        ARCHS = [f for x in a.arch for f in ('-arch', x)]
        portcheck.FLAGS = portcheck.FLAGS + ARCHS
    if a.web and (a.debug or a.coverage or a.release or a.arch or a.frame_single or a.run):
        sys.exit('portbuild.py: --web is a build of its own (no --debug, --coverage, --release, --arch, --frame-single or --run)')
    if a.web:
        # the EMS frame mapped once (port.h turns PORT_FRAME_SINGLE on under __EMSCRIPTEN__);
        # threads (the PIT, sound) are Web Workers on shared memory, which every object must
        # be compiled for; everything -O2
        a.cc = shutil.which('emcc') or sys.exit('portbuild.py: --web needs emcc (. ~/emsdk/emsdk_env.sh; tools/setup-emsdk.sh)')
        OUT = web_out('web')
        portcheck.OUT = OUT
        EXE = os.path.join(OUT, re.sub(r'\.exe$', '', P.exe) + '.js')
        # the build's own paths kept out of the program (__FILE__ in the null traps): the page is
        # published (web/deploy.sh refuses a site naming a local path)
        WEB = ['-pthread', f'-ffile-prefix-map={root}/=', f'-ffile-prefix-map={os.path.dirname(here)}/=exhume/']
        portcheck.FLAGS = portcheck.FLAGS + WEB + ['-O2']
        PORT_FLAGS.extend(WEB)
        LIBS = os.path.join(os.path.dirname(LIBS), 'libs-web')
        if not os.path.isdir(os.path.join(LIBS, 'lib', 'pkgconfig')):
            sys.exit(f'portbuild.py: --web needs SDL3 and libmt32emu built for the web in {LIBS} (SETUP_WEB=1 tools/setup-libs.sh)')
    if not os.path.isfile(P.compat): sys.exit(f'portbuild.py: no runtime at {P.runtime} ([port] runtime)')
    os.makedirs(OUT, exist_ok=True)
    game = portcfg.game_sources(CFG)     # with the shared C (the record and replay hooks' code)
    snd_cflags, snd_libs, snd_extra, said = deps()
    renames = P.web_renames if a.web else {}
    unknown = set(renames) - {os.path.normpath(p) for p in game}
    if unknown: sys.exit('portbuild.py: [port.web] renames names no game source: ' + ', '.join(sorted(unknown)))
    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as ex:
        gres = list(ex.map(lambda p: portcheck.compile_one(a.cc, p, renames.get(os.path.normpath(p), [])), game))
        pres = list(ex.map(lambda p: compile_port(a.cc, p, snd_cflags), port_sources() + snd_extra))
    bad = [(p, e) for p, rc, e, o in gres + pres if not o]
    for p, e in bad:
        print(f'{os.path.relpath(p, root)}: does not compile\n' + '\n'.join(l for l in e.split('\n') if 'error' in l)[:2000])
    if bad: return 1
    objs = [o for p, rc, e, o in gres + pres]
    shared = []
    if RELEASE:                 # each [[port.vendor]] library a shared library of its own
        for v in P.vendor:
            vo = [o for p, rc, e, o in pres if vendor_of(p) is v]
            if not vo: continue
            objs = [o for o in objs if o not in vo]
            link_shared(a.cc, v, vo)
            base = shared_name(v)
            shared += ['-L', OUT, '-l' + re.sub(r'^lib|\.(dylib|so|dll)$', '', base)]
    print(f'runtime: {os.path.dirname(P.runtime)}')
    print(f'compiled {len(gres)} game sources and {len(pres)} port sources'
          f' (layout flags: {" ".join(portcheck.layout_flags(a.cc)) or "none"})')
    warn = [(p, e) for p, rc, e, o in pres if o and 'warning' in e]
    for p, e in warn:
        print(f'{os.path.relpath(p, root)}: warnings\n' + '\n'.join(l for l in e.split('\n') if 'warning' in l)[:2000])
    if said: print('third-party: ' + ', '.join(said))
    for x in SOUND_EXT:
        if not os.path.exists(x): print(f'[sound] extensions: no {os.path.relpath(x, root)}')
    # the sound drivers' threads: in the C library on macOS and on Linux's glibc 2.34 and later,
    # in winpthreads on Windows (MinGW), which -pthread links
    threads = [] if sys.platform == 'darwin' else ['-pthread']
    libs = shared + pkg_config('--libs') + snd_libs
    if a.web: return link_web(a.cc, objs, libs)
    rpath = []
    if RELEASE:
        # the libraries beside the program, or in the package's lib (Linux) or Frameworks (macOS)
        if sys.platform == 'darwin':
            rpath = ['-Wl,-rpath,@executable_path', '-Wl,-rpath,@executable_path/../Frameworks', '-Wl,-headerpad_max_install_names']
        elif sys.platform.startswith('linux'):
            # only these: RUNPATH (new dtags), so LD_LIBRARY_PATH can still override it
            rpath = ['-Wl,--enable-new-dtags', '-Wl,-rpath,$ORIGIN', '-Wl,-rpath,$ORIGIN/../lib']
            libs = strip_rpaths(libs)
    elif sys.platform.startswith('linux') and any(f.startswith('-L' + LIBS) for f in libs):
        rpath = ['-Wl,-rpath,' + os.path.join(LIBS, 'lib')]     # runs without LD_LIBRARY_PATH
    objs += windows_resource(a.cc)
    r = subprocess.run([a.cc, '-o', EXE] + ARCHS + link_extra + objs + libs + threads + rpath,
                       capture_output=True, text=True, cwd=root)
    if not os.path.exists(EXE) and os.path.exists(EXE + '.exe'): EXE += '.exe'     # Windows
    if r.returncode:
        und = sorted(set(re.findall(r'"_?([A-Za-z_]\w*)", referenced from', r.stderr)))
        print(f'link failed: {len(und)} undefined names' + (': ' + ' '.join(und) if und else ''))
        print(r.stderr[-3000:])
        return 1
    print(f'linked {os.path.relpath(EXE, root)} ({os.path.getsize(EXE)} bytes)')
    if windows_target(a.cc) and shutil.which('llvm-readobj'):      # is the icon in?
        res = subprocess.run(['llvm-readobj', '--coff-resources', EXE], capture_output=True, text=True).stdout
        print(f'portbuild.py: resources in the program: {res.count("Type: ICON")} icon')
        if RELEASE and 'Type: ICON' not in res: sys.exit('portbuild.py: the Windows release program has no icon')
    if a.run:
        r = subprocess.run([EXE] + os.environ.get('PORT_ARGS', '').split(), cwd=root)
        print(f'exit status {r.returncode}')
    return 0


if __name__ == '__main__':
    sys.exit(main(ARGV))
