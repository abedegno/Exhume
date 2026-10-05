"""The port's configuration: exhume.toml's [port], [replay], [fuzz], [asm2c], [sound], [test] and
[package] sections with their defaults, for the tools that build, test and verify a native port of a
matched decompilation (docs/port.md). Every path is relative to [project] root unless absolute.

    import config, portcfg
    cfg = config.load(path); P = portcfg.port(cfg); R = portcfg.replay(cfg)

[port] (tools/portcheck.py, portbuild.py, portstubs.py, layoutcheck.py, intaudit.py, widths.py)
    dir          the project's own port C (default src/port): its bindings for the runtime
                 (portgame.h, asmgame.h, ailgame.h), its replacements for the game's assembly
                 modules and DOS-only C, and the link stubs; the DOS build never sees it
                 ([project] exclude). On the include path ahead of the runtime's directories.
    runtime      Exhume's runtime, compiled where it is, never copied (default this checkout's
                 runtime/; config.py): its port/ C is compiled with the project's, its
                 replay/replay.c is shared C, and its include/portable.h is staged for the DOS
                 build (docs/port.md, "The runtime")
    runtime_exclude  paths under the runtime the project does not build (["port/sound",
                 "port/sys/pit.c"] for a game without AIL 2, say); default none
    compat       force-included into every game source (default RUNTIME/port/compat.h)
    stand_ins    Borland's headers for the host (default RUNTIME/port/include), ahead of the host's
    shared       directories of the project's own C that both the port and the replay DOS
                 build compile, and the gate does not (default none); the runtime's
                 replay/replay.c is always shared C, unless excluded
    out          the build directory's name under [project] build (default "port"; the debug and
                 coverage builds go to OUT-debug and OUT-cov)
    exe          the linked program's name (default "port")
    name         the prefix of the program's messages, which tools read from its log (default exe)
    backend      the platform backend under DIR/platform/ (default "sdl3"), its flags from pkg-config
    optimise     directories under DIR, and the runtime's port/ directories of the same names,
                 compiled with -O2 in the normal build (default none)
    game_std, port_std   the C dialects (default gnu89 for the game's C, gnu11 for the port's)
    dos_only     the header comment that keeps a game source out of the port (default "port: dos-only")
    defines      extra -D flags for every host compile (default ["-D_POSIX_C_SOURCE=200809L"])
    stubs        the link stubs' directory (default DIR/stubs)
    exe_data     the label portcheck gives names that are far data taken from the EXE
    libs         where tools/setup-libs.sh builds SDL3 and libmt32emu when no package has them
                 (default tools/libs): pkg-config searches its lib/pkgconfig first, and on Linux
                 the normal build finds them there when run
    icon         the program's icon for Windows, an .ico linked in as a resource (default
                 [package] icon_dir/<icon>.ico when that exists; tools/icons.py writes it)
    [[port.vendor]]  third-party C fetched at setup and compiled in when present: name, dir,
                 sources, define, for (the port file that gets the define), hint; in the release
                 build (portbuild.py --release) each is a shared library beside the program
                 (lib<shared>.dylib, .so or <shared>.dll; shared defaults to the name's letters
                 and digits in lower case), so a user can replace it, as an LGPL library asks
    [[port.pkg]]     pkg-config packages linked when found: name, define, for, label, hint
    [port.layout]    tools/layoutcheck.py: file_records {tag = why}, probe (the DOS compile line)
    [port.audit]     tools/intaudit.py: width_types {name = [dos type, host type]}

[package] (tools/package.py, tools/icons.py; docs/port.md, "Packages for players")
    name         the packages' and the macOS app's name (default [project] name): NAME.app,
                 NAME-VERSION-macos.zip, NAME-VERSION-linux-ARCH.tar.gz, ...
    title        the game's full name, for the app's display name and the Linux menu entry
    comment      the Linux menu entry's comment
    bundle_id    the macOS app's identifier (default io.github.exhume.<exe>)
    launcher     the Linux package's start script, .desktop and .png names (default name in
                 lower case)
    readme       the players' README, its @VERSION@ and @MACOS_OPEN@ filled in (default
                 tools/dist/README-dist.txt)
    texts        project files copied in as NAME.txt (default LICENSE, NOTICE,
                 THIRD-PARTY-NOTICES)
    copyright    the app's copyright line
    category     the app's category (default public.app-category.games)
    min_macos    the oldest macOS the app runs on (default 11.0)
    icon_dir     the icon's sources (<icon>.svg, <icon>-small.svg) and the files tools/icons.py
                 makes from them (default tools/dist/icon)
    icon         their base name (default launcher)
    icon_header  the window icon tools/icons.py writes as C, for PLAT_ICON (default
                 [port] dir/platform/<backend>/icon.h)
    env          the prefix of the variables package.py reads (default name in upper case):
                 <env>_CODESIGN_IDENTITY, a Developer ID identity to sign the macOS app with
                 (ad hoc without it), and <env>_NOTARISED=1, which drops the README's advice
                 for opening an app Apple has not notarised
"""
import os, sys
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
EXHUME = os.path.dirname(here)


class _NS(dict):
    __getattr__ = dict.__getitem__


def _path(cfg, p):
    if p is None: return None
    return os.path.normpath(os.path.join(cfg.root, os.path.expanduser(p)))


def cli():
    """(cfg, rest of the command line): `--config PATH` taken from this process's arguments
    (sys.argv itself is never changed), and EXHUME_CONFIG set to it, so that every tool this one
    imports or starts reads the same exhume.toml."""
    import config
    path, rest = config.pop_config(sys.argv[1:])
    if path: os.environ['EXHUME_CONFIG'] = os.path.abspath(path)
    return config.load(path), rest


def port(cfg):
    p = cfg.raw.get('port', {})
    d = _path(cfg, p.get('dir', 'src/port'))
    rt = cfg.runtime
    rtp = os.path.join(rt, 'port')
    out = p.get('out', 'port')
    exe = p.get('exe', 'port')
    stand_ins = _path(cfg, p['stand_ins']) if p.get('stand_ins') else os.path.join(rtp, 'include')
    r = _NS(
        dir=d,
        runtime=rt,
        runtime_port=rtp,
        runtime_exclude=[os.path.normpath(os.path.join(rt, x)) for x in p.get('runtime_exclude', [])],
        compat=_path(cfg, p['compat']) if p.get('compat') else os.path.join(rtp, 'compat.h'),
        stand_ins=stand_ins,
        # the port C's include path: the project's directory first (its bindings), then the
        # runtime's, whose headers project C names as "port.h", "plat.h", "x86/asmrt.h",
        # "sound/audio.h" and the generated stubs as "stub.h"
        include_dirs=[d, rtp, os.path.join(rtp, 'platform'), stand_ins, os.path.join(rtp, 'sound'),
                      os.path.join(rtp, 'stubs')],
        # the game's headers and the runtime's portable.h, for #include "name.h"
        quote_dirs=list(cfg.includes),
        shared=[_path(cfg, x) for x in p.get('shared', [])],
        out=os.path.join(cfg.build, out),
        out_name=out,
        exe=exe,
        name=p.get('name', exe),
        backend=p.get('backend', 'sdl3'),
        optimise=[os.path.join(t, x) + os.sep for t in (d, rtp) for x in p.get('optimise', [])],
        game_std=p.get('game_std', 'gnu89'),
        port_std=p.get('port_std', 'gnu11'),
        dos_only=p.get('dos_only', 'port: dos-only'),
        defines=list(p.get('defines', ['-D_POSIX_C_SOURCE=200809L'])),
        stubs=_path(cfg, p['stubs']) if p.get('stubs') else os.path.join(d, 'stubs'),
        vendor=[dict(v, dir=_path(cfg, v['dir'])) for v in p.get('vendor', [])],
        libs=_path(cfg, p.get('libs', 'tools/libs')),
        icon=_path(cfg, p['icon']) if p.get('icon') else None,
        pkg=list(p.get('pkg', [])),
        layout=p.get('layout', {}),
        audit=p.get('audit', {}),
        # headers of the include directory the probes leave out: portable.h, and the bindings
        # for the runtime, which declare no records
        size_probe_skip=p.get('size_probe_skip', ['portable.h', 'hookgame.h', 'rpgame.h']),
    )
    return r


def variant_out(cfg, variant):
    """build/port, build/port-debug or build/port-cov: the normal, UBSan and coverage builds."""
    P = port(cfg)
    return P.out if not variant else P.out + '-' + variant


def runtime_excluded(cfg, path):
    """Whether a runtime file is one of [port] runtime_exclude, or under one."""
    path = os.path.normpath(path)
    return any(path == x or path.startswith(x + os.sep) for x in port(cfg).runtime_exclude)


def shared_sources(cfg):
    """The .C files both the port and the replay DOS build compile: [port] shared's, and the
    runtime's replay/replay.c."""
    import glob
    out = []
    for d in port(cfg).shared:
        out += glob.glob(os.path.join(d, '*.C')) + glob.glob(os.path.join(d, '*.c'))
    rp = os.path.join(port(cfg).runtime, 'replay', 'replay.c')
    out = sorted(set(os.path.normpath(p) for p in out))
    if os.path.exists(rp) and not runtime_excluded(cfg, rp): out.append(rp)
    return out


def port_sources(cfg):
    """The port's own C: every .c under [port] dir and the runtime's port/ but the stand-in
    headers, the platform backends other than [port] backend and [port] runtime_exclude,
    sorted by path, both trees together."""
    P = port(cfg)
    out = []
    for top in (P.dir, P.runtime_port):
        plat = os.path.join(top, 'platform')
        for d, _, fs in os.walk(top):
            if d == P.stand_ins or d.startswith(P.stand_ins + os.sep) or d.startswith(os.path.join(top, 'include')): continue
            if d.startswith(plat + os.sep) and os.path.relpath(d, plat).split(os.sep)[0] != P.backend: continue
            for f in sorted(fs):
                p = os.path.join(d, f)
                if f.endswith('.c') and not (top == P.runtime_port and runtime_excluded(cfg, p)): out.append(p)
    return sorted(out)


def display(cfg, path):
    """A path as the tools print it: from [project] root, or runtime/... for the runtime's."""
    path = os.path.normpath(os.path.join(cfg.root, path))
    rt = port(cfg).runtime
    if os.path.commonpath([path, rt]) == rt: return 'runtime/' + os.path.relpath(path, rt)
    return os.path.relpath(path, cfg.root)


def dos_only(cfg, path):
    """A game source whose header comment carries [port] dos_only is DOS-specific (UW2: EMS.C's
    int 67h calls); the port replaces it in its own C and never compiles it."""
    return port(cfg).dos_only in open(path, encoding='latin1').read(4000)


def game_sources(cfg):
    """The game's C the port compiles: every .C of the DOS build but the dos-only ones, and the
    shared C."""
    import sources
    srcs = [p for p in sources.all_sources(cfg) if p.upper().endswith('.C') and not dos_only(cfg, p)]
    return srcs + shared_sources(cfg)


def replay(cfg):
    """[replay]: the record and replay harness, its sessions and the golden references
    (tools/replay.py, golden.py, replaydos.mjs; docs/port.md, "Verifying a port")."""
    r = cfg.raw.get('replay', {})
    P = port(cfg)
    run = cfg.raw.get('run', {})
    out = _NS(
        sessions=_path(cfg, r.get('sessions', 'tests/replay')),
        golden=_path(cfg, r.get('golden', os.path.join(r.get('sessions', 'tests/replay'), 'golden'))),
        work=os.path.join(cfg.build, r.get('work', 'replay')),
        magic=r.get('magic', 'EXHR').encode('latin1'),
        exe_name=r.get('exe_name', run.get('exe_name', 'GAME.EXE')),
        data=os.path.expanduser(r.get('data', run.get('data', ''))),
        data_skip=list(r.get('data_skip', run.get('skip', []))),
        cfg_path=r.get('cfg_path'),                 # where a session's .cfg goes in the game's tree
        saves=list(r.get('saves', [])),
        hooks=list(r.get('hooks', ['GAME_TIME', 'KEY', 'MOUSE', 'MBUTTONS', 'JOY_READ', 'JOY_BUTTONS',
                                    'WALL_TIME', 'SRAND', 'CHECKPOINT', 'STACK_JUNK', 'STACK_JUNK_SET', 'SND_READ', 'SLAVE_TIMER'])),
        shared_opts=r.get('shared_opts', cfg.c_opts),
        env=list(r.get('env', ['UWRPCK', 'UWRPTRACE', 'UWRPFB', 'UWRPFULL', 'UWRPHOOK'])),
        out_files=list(r.get('out_files', ['RECORD.OUT', 'STATE.OUT', 'NULLTRAP.LOG', 'TRACE.OUT', 'SNDCHECK.OUT'])),
        link=r.get('link'),                         # the modding link command, with {out}, {objs}
        port_args=list(r.get('port_args', ['--hidden', '--exit-on-halt', '--exit-after', '600000'])),
        port_data_flag=r.get('port_data_flag', '--data'),
        port_home_flag=r.get('port_home_flag', '--home'),
        port_replay_flag=r.get('port_replay_flag', '--replay'),
        port_sound_logs=list(r.get('port_sound_logs', [])),   # flags for the driver logs: [ail, hw]
        order=list(r.get('order', [])),
        stage_from=dict(r.get('stage_from', {})),
        steps=dict(r.get('steps', {})),
        cfgs=dict(r.get('cfgs', {})),
        derive=dict(r.get('derive', {})),
        mask=list(r.get('mask', [])),
        string_junk=list(r.get('string_junk', [])),
        segments=dict(r.get('segments', {})),
        nulls=dict(r.get('nulls', {})),
        sound_check=r.get('sound_check', True),
        driver_check=r.get('driver_check'),          # a command run on the port's driver logs
        dos_jobs=dict(r.get('dos_jobs', {'dosbox-x': 8, 'jsdos': 4})),
        # sessions whose DOS replays need a particular DOS (UW1's sound session hangs in DOSBox-X)
        dos_backend=dict(r.get('dos_backend', {})),
        dosbox_conf=_path(cfg, r['dosbox_conf']) if r.get('dosbox_conf') else None,
        c0_signature=r.get('c0_signature', cfg.profile.get('fingerprint', {}).get('dgroup_anchor', '')),
        c0_signature_at=int(r.get('c0_signature_at', cfg.profile.get('fingerprint', {}).get('dgroup_anchor_offset', 4))),
        port_exe_name=P.exe,
        port_name=P.name,
    )
    return out


def fuzz(cfg):
    f = cfg.raw.get('fuzz', {})
    return _NS(
        targets=_path(cfg, f['targets']) if f.get('targets') else None,
        host_glue=_path(cfg, f['host_glue']) if f.get('host_glue') else None,
        main=f.get('main', 'sys/main.c'),            # the port file fuzzhost replaces
    )


def asm2c(cfg):
    a = cfg.raw.get('asm2c', {})
    return _NS(spec=_path(cfg, a['spec']) if a.get('spec') else None,
               # a project Python file whose OVERRIDES (and PATCH_OVERRIDDEN) are used: C for
               # single instructions is the game's code, so it stays in the project's repository
               overrides=_path(cfg, a['overrides']) if a.get('overrides') else None)


def sound(cfg):
    """[sound]: drivers (tools/ailcheck.py) and extensions, the project's own C for the sound
    library (UW2: src/port/sound/tvfx.c, the FM drivers' time-variant effects, which plug into
    runtime/port/sound/yamaha.c's struct AilFmExt), relative to [project] root."""
    s = cfg.raw.get('sound', {})
    return _NS(drivers=dict(s.get('drivers', {})),
               extensions=[_path(cfg, x) for x in s.get('extensions', [])])


def test(cfg):
    return cfg.raw.get('test', {})


def package(cfg):
    """[package]: the players' packages (tools/package.py) and the icon (tools/icons.py)."""
    k = cfg.raw.get('package', {})
    P = port(cfg)
    name = k.get('name', cfg.raw.get('project', {}).get('name', P.exe))
    launcher = k.get('launcher', name.lower())
    icon_dir = _path(cfg, k.get('icon_dir', 'tools/dist/icon'))
    icon = k.get('icon', launcher)
    return _NS(
        name=name,
        title=k.get('title', name),
        comment=k.get('comment', f'Native port of {k.get("title", name)} (needs your own copy of the game)'),
        bundle_id=k.get('bundle_id', f'io.github.exhume.{P.exe}'),
        launcher=launcher,
        readme=_path(cfg, k.get('readme', 'tools/dist/README-dist.txt')),
        texts=list(k.get('texts', ['LICENSE', 'NOTICE', 'THIRD-PARTY-NOTICES'])),
        copyright=k.get('copyright', ''),
        category=k.get('category', 'public.app-category.games'),
        min_macos=k.get('min_macos', '11.0'),
        icon_dir=icon_dir,
        icon=icon,
        icon_header=_path(cfg, k['icon_header']) if k.get('icon_header') else
            os.path.join(P.dir, 'platform', P.backend, 'icon.h'),
        env=k.get('env', name.upper()),
    )


def icon_ico(cfg):
    """The Windows program's .ico: [port] icon, else [package]'s when it exists, else None."""
    P = port(cfg)
    if P.icon: return P.icon
    K = package(cfg)
    p = os.path.join(K.icon_dir, K.icon + '.ico')
    return p if os.path.exists(p) else None


def load_module(path, name):
    """A Python file named by the config (fuzz targets, asm2c's spec) as a module."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m
