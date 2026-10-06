"""Checks how a port finds MT-32 or CM-32L ROMs (runtime/port/sound/mt32roms.c): each source in
turn, their order, a file given for a folder, any file names, and the wrong cases. Each case runs
the port once, hidden, in a fresh home directory with HOME and the XDG and Windows folders pointed
into a scratch directory, with ROM copies under odd names in exactly the places the case names,
and reads the port's line `mt32: ROMs SOURCE DIR (CONTROL, PCM)` or `mt32: no ROMs`.

    python3 tools/romcheck.py --exe PORT --data GAME_DIR --roms DIR --env NAME [--sound S] [--split DIR]

PORT is the built port; GAME_DIR the game; DIR holds the ROM images to copy (a CM-32L pair and an
MT-32 pair, any names); NAME the port's ROM variable (UW2PORT_MT32_ROMS); S the --sound value
that picks the MT-32 for music (UW2: 5,1; UW1: 6,1); --split a folder of split ROM halves (Munt's
names: an MT-32 control ROM's _a and _b, the MT-32 PCM's _l and _h, the CM-32L PCM's _h), which the
port joins: matching halves make a pair, a half without its partner makes nothing, and the whole
MT-32 PCM is the CM-32L PCM's low half. Besides each source and the search's folders on this system, the cases
are a remembered file, a remembered folder that has gone (the search must find the ROMs), a
mistyped path (none, never its parent folder's) and, on Windows, %APPDATA% (never searched).
Exit status 0 when every case passes."""
import argparse, os, re, shutil, subprocess, sys, tempfile

LINE = re.compile(r'mt32: (?:ROMs (\w+) (.+?) \((\S+), (\S+)\)|no ROMs)')
WIN = sys.platform == 'win32' or os.environ.get('MSYSTEM')


def dll_path(exe, env):
    """On Windows, a development build finds its DLLs on PATH: the game folder's tools/libs/bin
    (libmt32emu, built by setup-libs.sh) above the program, as replay.py's port_env adds them."""
    if os.name != 'nt': return env
    d = os.path.dirname(os.path.abspath(exe)); extra = []
    for _ in range(4):
        for sub in (('tools', 'libs', 'bin'), ('tools', 'libs', 'lib')):
            p = os.path.join(d, *sub)
            if os.path.isdir(p): extra.append(p)
        d = os.path.dirname(d)
    if extra: env['PATH'] = os.pathsep.join(extra + [env.get('PATH', '')])
    return env


def identify(src):
    """name -> 'cm32l-ctrl' | 'cm32l-pcm' | 'mt32-ctrl' | 'mt32-pcm', by size and the copy's
    role in the case (the port identifies by content; the test only needs to place them)."""
    roles = {}
    for f in sorted(os.listdir(src)):
        n = f.lower()
        kind = ('cm32l' if 'cm32l' in n else 'mt32') + ('-pcm' if 'pcm' in n else '-ctrl')
        roles.setdefault(kind, os.path.join(src, f))
    missing = {'cm32l-ctrl', 'cm32l-pcm', 'mt32-ctrl', 'mt32-pcm'} - set(roles)
    if missing: sys.exit(f'romcheck: {src} lacks {sorted(missing)}')
    return roles


def place(roles, where, kinds=('cm32l-ctrl', 'cm32l-pcm'), names=('Control Rom.bin', 'pcm image.ROM')):
    os.makedirs(where, exist_ok=True)
    for k, n in zip(kinds, names):
        shutil.copy(roles[k], os.path.join(where, n))
    return where


def run(a, scratch, home, extra=(), env_rom=None, unset=()):
    env = dict(os.environ)
    for k in list(env):
        if k.endswith('_MT32_ROMS'): del env[k]
    fake = os.path.join(scratch, 'user')
    env.update(HOME=fake, XDG_DATA_HOME=os.path.join(fake, '.local', 'share'),
               XDG_CONFIG_HOME=os.path.join(fake, '.config'), XDG_DATA_DIRS=os.path.join(scratch, 'sysdata'),
               LOCALAPPDATA=os.path.join(fake, 'AppData', 'Local'), APPDATA=os.path.join(fake, 'AppData', 'Roaming'),
               SDL_VIDEODRIVER='offscreen', SDL_AUDIODRIVER='dummy')
    if env_rom: env[a.env] = env_rom
    for k in unset: env.pop(k, None)
    cmd = [a.exe, '--data', a.data, '--home', home, '--hidden', '--no-recording', '--sound', a.sound,
           '--exit-after', '3000'] + list(extra)
    r = subprocess.run(cmd, env=dll_path(a.exe, env), capture_output=True, text=True, errors='replace', timeout=120)
    m = None
    for line in (r.stdout + r.stderr).splitlines():
        m = LINE.search(line) or m
    if not m: return ('?', '', '', '', f'exit {r.returncode & 0xFFFFFFFF:#x}: ' + (r.stdout + r.stderr)[-600:])
    if m.group(1) is None: return ('none', '', '', '', '')
    return (m.group(1), os.path.normpath(m.group(2)), m.group(3), m.group(4), '')


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--exe', required=True); ap.add_argument('--data', required=True)
    ap.add_argument('--roms', required=True); ap.add_argument('--env', required=True)
    ap.add_argument('--sound', default='5,1')
    ap.add_argument('--split', help='a folder of split ROM halves only (_a/_b, _h/_l), which must not make a pair')
    a = ap.parse_args()
    if not os.path.exists(a.exe) and os.path.exists(a.exe + '.exe'): a.exe += '.exe'
    roles = identify(a.roms)
    results = []

    def case(name, setup, want_src, want_dir=None, want_ctrl=None, env_rom=None, extra_args=(), unset=(), want_pcm=None):
        scratch = tempfile.mkdtemp(prefix='romcheck-')
        try:
            home = os.path.join(scratch, 'home'); os.makedirs(home)
            ctx = dict(scratch=scratch, home=home, user=os.path.join(scratch, 'user'))
            given = setup(ctx) if setup else None
            extra = list(extra_args)
            if given and given.get('arg'): extra += ['--mt32-roms', given['arg']]
            src, d, ctrl, pcm, tail = run(a, scratch, home, extra, env_rom=(given or {}).get('env'), unset=unset)
            ok = src == want_src
            if ok and want_dir: ok = os.path.normpath(d).lower() == os.path.normpath(want_dir(ctx)).lower()
            if ok and want_ctrl: ok = ctrl.startswith(want_ctrl)
            if ok and want_pcm: ok = pcm == want_pcm
            results.append(ok)
            print(f"{'ok  ' if ok else 'FAIL'} {name}: {src} {d} {ctrl} {pcm}".rstrip() + (f'\n     {tail}' if tail and not ok else ''))
        finally:
            shutil.rmtree(scratch, ignore_errors=True)

    P = os.path.join
    case('given folder', lambda c: {'arg': place(roles, P(c['scratch'], 'given'))}, 'given', lambda c: P(c['scratch'], 'given'), 'ctrl_cm32l')
    case('given file (its folder)', lambda c: {'arg': P(place(roles, P(c['scratch'], 'given')), 'pcm image.ROM')}, 'given', lambda c: P(c['scratch'], 'given'), 'ctrl_cm32l')
    case('environment', lambda c: {'env': place(roles, P(c['scratch'], 'env'))}, 'environment', lambda c: P(c['scratch'], 'env'), 'ctrl_cm32l')

    def keep(c, path):
        with open(P(c['home'], a.env.split('_')[0].lower() + '.cfg'), 'w') as f: f.write(f'mt32-roms={path}\n')

    def remembered(c):
        keep(c, place(roles, P(c['scratch'], 'kept')))
        return {}
    case('remembered setting', remembered, 'remembered', lambda c: P(c['scratch'], 'kept'), 'ctrl_cm32l')

    def remembered_file(c):
        keep(c, P(place(roles, P(c['scratch'], 'kept')), 'pcm image.ROM'))
        return {}
    case('remembered setting, a file (its folder)', remembered_file, 'remembered', lambda c: P(c['scratch'], 'kept'), 'ctrl_cm32l')

    def remembered_gone(c):
        keep(c, P(c['scratch'], 'gone'))
        place(roles, P(c['home'], 'roms'))
        return {}
    case('remembered folder gone: the search finds them', remembered_gone, 'found', lambda c: P(c['home'], 'roms'), 'ctrl_cm32l')

    def mistyped(c):
        place(roles, P(c['scratch'], 'given'))
        return {'arg': P(c['scratch'], 'given', 'mt23')}
    case('a mistyped path is not its folder: none', mistyped, 'none')
    case('search: home roms/', lambda c: place(roles, P(c['home'], 'roms')) and {}, 'found', lambda c: P(c['home'], 'roms'), 'ctrl_cm32l')
    case('search: home mt32-roms/', lambda c: place(roles, P(c['home'], 'mt32-roms')) and {}, 'found', lambda c: P(c['home'], 'mt32-roms'), 'ctrl_cm32l')
    if WIN:
        case('APPDATA is not searched without LOCALAPPDATA: none', lambda c: place(roles, P(c['user'], 'AppData', 'Roaming', 'DOSBox', 'mt32-roms')) and {}, 'none', unset=('LOCALAPPDATA',))
        case('search: DOSBox Staging (Windows)', lambda c: place(roles, P(c['user'], 'AppData', 'Local', 'DOSBox', 'mt32-roms')) and {}, 'found', lambda c: P(c['user'], 'AppData', 'Local', 'DOSBox', 'mt32-roms'), 'ctrl_cm32l')
    elif sys.platform == 'darwin':
        case('search: DOSBox Staging (macOS)', lambda c: place(roles, P(c['user'], 'Library', 'Preferences', 'DOSBox', 'mt32-roms')) and {}, 'found', lambda c: P(c['user'], 'Library', 'Preferences', 'DOSBox', 'mt32-roms'), 'ctrl_cm32l')
        case('search: Audio/Sounds/MT32-Roms (macOS)', lambda c: place(roles, P(c['user'], 'Library', 'Audio', 'Sounds', 'MT32-Roms')) and {}, 'found', lambda c: P(c['user'], 'Library', 'Audio', 'Sounds', 'MT32-Roms'), 'ctrl_cm32l')
    else:
        case('search: XDG data dosbox/mt32-roms', lambda c: place(roles, P(c['user'], '.local', 'share', 'dosbox', 'mt32-roms')) and {}, 'found', lambda c: P(c['user'], '.local', 'share', 'dosbox', 'mt32-roms'), 'ctrl_cm32l')
        case('search: XDG data mt32-rom-data', lambda c: place(roles, P(c['user'], '.local', 'share', 'mt32-rom-data')) and {}, 'found', lambda c: P(c['user'], '.local', 'share', 'mt32-rom-data'), 'ctrl_cm32l')
        case('search: XDG_DATA_DIRS mt32-rom-data', lambda c: place(roles, P(c['scratch'], 'sysdata', 'mt32-rom-data')) and {}, 'found', lambda c: P(c['scratch'], 'sysdata', 'mt32-rom-data'), 'ctrl_cm32l')
        case('search: XDG config dosbox/mt32-roms', lambda c: place(roles, P(c['user'], '.config', 'dosbox', 'mt32-roms')) and {}, 'found', lambda c: P(c['user'], '.config', 'dosbox', 'mt32-roms'), 'ctrl_cm32l')

    def both(c):
        place(roles, P(c['home'], 'roms'), ('mt32-ctrl', 'mt32-pcm'))
        return {'arg': place(roles, P(c['scratch'], 'given'))}
    case('priority: given beats the search', both, 'given', lambda c: P(c['scratch'], 'given'), 'ctrl_cm32l')

    def cm_over_mt(c):
        d = P(c['home'], 'roms'); place(roles, d, ('mt32-ctrl', 'mt32-pcm'), ('a.rom', 'b.rom')); place(roles, d)
        return {}
    case('a CM-32L pair beats an MT-32 pair in one folder', cm_over_mt, 'found', lambda c: P(c['home'], 'roms'), 'ctrl_cm32l')
    case('an MT-32 pair alone', lambda c: place(roles, P(c['home'], 'roms'), ('mt32-ctrl', 'mt32-pcm')) and {}, 'found', lambda c: P(c['home'], 'roms'), 'ctrl_mt32')
    case('a lone control ROM: none', lambda c: place(roles, P(c['home'], 'roms'), ('cm32l-ctrl',), ('ctrl.rom',)) and {}, 'none')

    def wrong(c):
        d = P(c['home'], 'roms'); os.makedirs(d)
        for n in ('CM32L_CONTROL.ROM', 'CM32L_PCM.ROM'):
            with open(P(d, n), 'wb') as f: f.write(os.urandom(65536))
        return {}
    case('wrong files under the right names: none', wrong, 'none')
    case('no ROMs anywhere: none', None, 'none')
    if a.split:
        # the halves by their usual names (Munt's): mt32_ctrl_*_a/_b, mt32_pcm_l/_h, cm32l_pcm_h
        half = {}
        for f in sorted(os.listdir(a.split)):
            n = f.lower()
            for k in ('ctrl_a', 'ctrl_b', 'mt32_pcm_l', 'mt32_pcm_h', 'cm32l_pcm_h'):
                if k.startswith('ctrl') and 'ctrl' in n and n.rsplit('.', 1)[0].endswith('_' + k[-1]): half.setdefault(k, P(a.split, f))
                elif not k.startswith('ctrl') and k in n: half.setdefault(k, P(a.split, f))

        def put(c, kinds, names=None):
            d = P(c['home'], 'roms'); os.makedirs(d, exist_ok=True)
            for i, k in enumerate(kinds): shutil.copy(half[k], P(d, (names or {}).get(k, f'part {i}.bin')))
            return d
        case('split halves only: joined into an MT-32 pair', lambda c: put(c, ('ctrl_a', 'ctrl_b', 'mt32_pcm_l', 'mt32_pcm_h')) and {},
             'found', lambda c: P(c['home'], 'roms'), 'ctrl_mt32')
        case('unmatched halves: none', lambda c: put(c, ('ctrl_a', 'mt32_pcm_l', 'cm32l_pcm_h')) and {}, 'none')

        def cm_from_half(c):
            d = put(c, ('cm32l_pcm_h',))
            place(roles, d, ('cm32l-ctrl', 'mt32-pcm'), ('a.rom', 'b.rom'))
            return {}
        case('the MT-32 PCM and the CM-32L high half: a CM-32L pair', cm_from_half, 'found', lambda c: P(c['home'], 'roms'), 'ctrl_cm32l')
        case('the MT-32 PCM and the CM-32L high half: the CM-32L PCM', cm_from_half, 'found', lambda c: P(c['home'], 'roms'), None, want_pcm='pcm_cm32l')
    n = len(results); bad = results.count(False)
    print(f'romcheck: {n - bad} of {n} cases pass')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main())
