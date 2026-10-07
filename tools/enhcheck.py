"""Checks a port's enhancements (runtime/port/sys/enhance.c; Underworld Exhumed's
docs/ENHANCEMENTS.md): each is off by default and carried by the sessions played with it.

    python3 tools/enhcheck.py [--config PATH] selftest      the tools' own handling of format 5
    python3 tools/enhcheck.py [--config PATH] registry      the options, the settings and the
                                                            recordings' enhancements
    python3 tools/enhcheck.py [--config PATH] presentation  each presentation enhancement leaves
                                                            every session's game state as DOS's
    python3 tools/enhcheck.py [--config PATH] coverage      --enhance perspective covers the
                                                            original mappers' pixels exactly
    python3 tools/enhcheck.py [--config PATH] holes         --enhance wide-pitch's back pass fills
                                                            the holes a steep look leaves
    python3 tools/enhcheck.py [--config PATH] looking       --enhance mouse-look and invert-look turn
                                                            and pitch the view by the mouse
    python3 tools/enhcheck.py [--config PATH] keys          --enhance modern-keys and rune-keys: each
                                                            key does its job
    python3 tools/enhcheck.py [--config PATH] baseline check|make|record [NAME ...]
                                                            the port-made goldens of tests/replay/
                                                            enhanced/NAME (timing and gameplay)
    python3 tools/enhcheck.py [--config PATH] all           all of these but baseline make and
                                                            record

Exit status 0 when every check passes."""
import os, re, sys, json, struct, hashlib, tempfile, shutil, subprocess
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import replay as R

results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f': {detail}' if detail != '' and not ok else ''))


def fake_recording(path, version, names=None):
    """A recording with no runs: the header, and for format 5 the enhancements' chunk."""
    with open(path, 'wb') as f:
        f.write(R.RC.magic + struct.pack('<HHI', version, 0, 0))
        if names is not None:
            b = names.encode()
            f.write(bytes([9]) + struct.pack('<H', len(b)) + b)


def selftest():
    d = tempfile.mkdtemp(prefix='enhcheck-')
    try:
        p4, p5 = os.path.join(d, 'a.rec'), os.path.join(d, 'b.rec')
        fake_recording(p4, 4); fake_recording(p5, 5, 'skip-intro')
        s4, _ = R.read_log(p4); s5, _ = R.read_log(p5)
        check('format 4: no enhancements', s4.get('ENH') is None, s4)
        check('format 5: its enhancements read', s5.get('ENH') == ['skip-intro'], s5)
        ok4, _ = R.dos_can_replay(p4); ok5, why = R.dos_can_replay(p5)
        check('DOS replays format 4', ok4)
        check('DOS refuses format 5, saying why', not ok5 and 'skip-intro' in why, why)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    selftest_presentation()


def selftest_presentation():
    g = [{'ck': [1, 1, 3, 0], 'secs': {'PLYR': 'a', 'VGA': 'x', 'PAL': 'p'}}]
    check('only the screen differs: passes',
          state_diff(g, [{'ck': [1, 1, 3, 0], 'secs': {'PLYR': 'a', 'VGA': 'y', 'PAL': 'q'}}]) == [])
    check('a state section differs: caught',
          state_diff(g, [{'ck': [1, 1, 3, 0], 'secs': {'PLYR': 'b', 'VGA': 'x', 'PAL': 'p'}}]) == [(0, ['PLYR'])])
    check('a missing checkpoint: caught', state_diff(g, []) != [])
    check('a section only one side has: caught',
          state_diff(g, [{'ck': [1, 1, 3, 0], 'secs': {'PLYR': 'a', 'RAND': 'r'}}]) == [(0, ['RAND'])])
    check('the renderer sections are ignored with the screen', SCREEN >= {'VGA', 'PAL', 'CRTC'})
    check('a checkpoint header differs: caught',
          state_diff(g, [{'ck': [1, 1, 4, 0], 'secs': {'PLYR': 'a'}}]) == [(0, ['header'])])


def port(home, *args, hidden=True, exit_after=1500):
    """Runs the port briefly in its own home (offscreen, no sound device), returning (exit
    status, output). hidden=False is a player's run: settings read and written."""
    env = R.port_env()
    env.update(SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy')   # dummy: a player's run gets a window
    cmd = [R.port_exe(), R.RC.port_data_flag, R.DATA, R.RC.port_home_flag, home, '--no-recording',
           '--exit-after', str(exit_after)] + (['--hidden'] if hidden else []) + list(args)
    r = subprocess.run(cmd, capture_output=True, text=True, errors='replace', env=env, timeout=180)
    return r.returncode, r.stdout + r.stderr


def settings(home):
    p = os.path.join(home, R.RC.port_name + '.cfg')
    return open(p).read() if os.path.exists(p) else ''


def registry():
    d = tempfile.mkdtemp(prefix='enhcheck-')
    rp = R.RC.port_replay_flag
    try:
        h = os.path.join(d, 'home'); os.makedirs(h)
        rc, out = port(h, '--enhance', 'list')
        check('--enhance list: skip-intro, timing', rc == 0 and 'skip-intro' in out and 'timing' in out, out[-400:])
        rc, out = port(h, '--enhance', 'nope')
        check('an unknown enhancement stops the run, listing the names', rc != 0 and 'skip-intro' in out, out[-400:])
        rc, out = port(h, '--enhance', 'list')
        listed = [l.split()[0] for l in out.splitlines() if l.startswith('  ')]
        # a flag another game has: listed in this game's table with "only", not in --enhance list,
        # and --enhance NAME stops with "NAME is UW2 only"
        others = []
        for name in ('subtitles', 'skill-messages'):
            if name in listed: continue
            rc, out = port(h, '--enhance', name)
            check(f"{name}: another game's, refused as such", rc != 0 and f'{name} is UW2 only' in out, out[-300:])
            others.append(name)
        if R.RC.port_name == 'uw1port':
            check('UW1 lists neither UW2-only flag', others == ['subtitles', 'skill-messages'], (listed, others))
        rc, out = port(h)
        check('none by default: no enhance line', rc == 0 and 'enhance:' not in out, out[-400:])
        rc, out = port(h, '--enhance', 'skip-intro')
        check('--enhance skip-intro: logged, not as DOS', 'enhance: skip-intro (not as DOS)' in out, out[-400:])
        rc, out = port(h, '--enhance', 'skip-intro', hidden=False)
        check("a player's run saves it", 'enhance=skip-intro' in settings(h), settings(h))
        rc, out = port(h, hidden=False)
        check("the next player's run has it", 'enhance: skip-intro' in out, out[-400:])
        rc, out = port(h)
        check('a hidden run ignores the settings file', 'enhance:' not in out, out[-400:])
        rc, out = port(h, '--no-enhance', 'skip-intro', hidden=False)
        check('--no-enhance turns it off and saves that',
              'enhance:' not in out and 'enhance=skip-intro' not in settings(h), settings(h))
        with open(os.path.join(h, R.RC.port_name + '.cfg'), 'a') as f: f.write('enhance=skip-intro,from-the-future\n')
        rc, out = port(h, hidden=False)
        check('an unknown enhancement in the settings: a warning, the game runs',
              rc == 0 and 'from-the-future' in out and 'enhance: skip-intro' in out, out[-400:])
        # a recording carries its enhancements, and a replay restores them
        r = os.path.join(d, 'rec'); os.makedirs(r)
        rec = os.path.join(r, 'RECORD.OUT')
        rc, out = port(r, '--record', '--enhance', 'skip-intro')
        data = open(rec, 'rb').read() if os.path.exists(rec) else b''
        check('a recording with an enhancement is format 5 and names it',
              data[4:5] == b'\x05' and b'skip-intro' in data[:64], (rc, data[:20], out[-300:]))
        s = R.read_log(rec)[0] if data else {}
        check('read_log reads its enhancements', s.get('ENH') == ['skip-intro'], s.get('ENH'))
        h2 = os.path.join(d, 'h2'); os.makedirs(h2)
        kept = os.path.join(d, 'with.rec'); shutil.copy(rec, kept) if data else None
        rc, out = port(h2, rp, kept)
        check('its replay restores them', 'enhance: skip-intro' in out, out[-400:])
        rc, out = port(h2, rp, kept, '--no-enhance', 'skip-intro')
        check('a replay of it refuses --enhance and --no-enhance', rc != 0 and 'recording' in out, out[-400:])
        bad = os.path.join(d, 'bad.rec'); open(bad, 'wb').write(data.replace(b'skip-intro', b'skip-intrx', 1))
        rc, out = port(h2, rp, bad)
        check('a recording naming an enhancement this build lacks: refused', rc != 0 and 'skip-intrx' in out, out[-400:])
        rc, out = port(r, '--record')
        data = open(rec, 'rb').read() if os.path.exists(rec) else b''
        check('a recording with none stays format 4', data[4:5] == b'\x04', data[:8])
        rc, out = port(h2, rp, rec, '--enhance', 'skip-intro')
        check('a replay of a format 4 recording refuses a timing enhancement', rc != 0 and 'presentation' in out, out[-400:])
    finally:
        shutil.rmtree(d, ignore_errors=True)


SCREEN = {'VGA', 'PAL', 'CRTC'}
# and the sections a project names as its renderer's working memory ([replay] renderer_sections; UW:
# GFX, the graphics engine's data segment), which a rendering enhancement changes by design; what
# it did to the game itself shows in the other sections
SCREEN |= set(R.CFG.raw.get('replay', {}).get('renderer_sections', []))


def state_diff(golden_cks, port_cks):
    """The checkpoints where a section other than the screen's differs: [(i, [sections])].
    golden_cks: golden.json's checkpoints; port_cks: [{'ck': header, 'secs': canon}]."""
    if len(golden_cks) != len(port_cks): return [(-1, [f'{len(port_cks)} checkpoints, not {len(golden_cks)}'])]
    out = []
    for i, (g, p) in enumerate(zip(golden_cks, port_cks)):
        if list(g['ck']) != list(p['ck']):
            out.append((i, ['header'])); continue
        keys = (set(g['secs']) | set(p['secs'])) - SCREEN
        bad = sorted(k for k in keys if g['secs'].get(k) != p['secs'].get(k))
        if bad: out.append((i, bad))
    return out


def run_checkpoints(rec, extra=(), stage=None):
    """The port's checkpoints replaying rec: [{'ck': header, 'secs': canon}]."""
    import golden as G
    d = tempfile.mkdtemp(prefix='enhcheck-')
    try:
        R.run_port(rec, d, list(extra), stage=stage, quiet=True)
        return [{'ck': G.header(ck), 'secs': G.canon(ck)} for ck in R.read_dump(d)]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def port_flags(kind):
    """The port's enhancements of one kind, from --enhance list."""
    d = tempfile.mkdtemp(prefix='enhcheck-')
    try:
        rc, out = port(d, '--enhance', 'list')
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return [l.split()[0] for l in out.splitlines() if l.startswith('  ') and len(l.split()) > 1 and l.split()[1] == kind]


def presentation():
    import golden as G
    flags = port_flags('presentation')
    if not flags:
        print('presentation: the port has no presentation enhancement yet; nothing to replay')
        check('presentation: the list was read', port_flags('timing') != [], 'no enhancements listed at all')
        return
    sessions = sorted(f[:-4] for f in os.listdir(R.RC.sessions) if f.endswith('.rec'))
    # a session replayed from the saved game another writes ([replay] stage_from) runs after it,
    # with that saved game staged, as verify does
    order = [n for n in sessions if n not in G.STAGE_FROM] + [n for n in sessions if n in G.STAGE_FROM]
    for flag in flags:
        work = tempfile.mkdtemp(prefix='enhcheck-')
        try:
            for name in order:
                g = G.load_golden(name)
                if not g: continue
                d = os.path.join(work, name); os.makedirs(d)
                stage = None
                if name in G.STAGE_FROM:
                    sd = os.path.join(work, G.STAGE_FROM[name])
                    stage = os.path.join(work, name + '-stage'); os.makedirs(stage)
                    for x in G.stage_dirs(sd): shutil.copytree(os.path.join(sd, x), os.path.join(stage, x))
                R.run_port(G.rec_of(name), d, ['--enhance', flag], stage=stage, quiet=True)
                got = [{'ck': G.header(ck), 'secs': G.canon(ck)} for ck in R.read_dump(d)]
                diff = state_diff(g['checkpoints'], got)
                check(f"{flag}: {name} keeps DOS's game state", not diff, diff[:3])
        finally:
            shutil.rmtree(work, ignore_errors=True)


def enh_dir(name): return os.path.join(R.RC.sessions, 'enhanced', name)


def stage_of(name, work):
    """The saved game an enhanced session starts from: its stage_from file names a session,
    which is replayed (no enhancements) and its saved game copied into work/stage. None without."""
    import golden as G
    sf = os.path.join(enh_dir(name), 'stage_from')
    if not os.path.exists(sf): return None
    src = open(sf).read().strip()
    d = os.path.join(work, 'src'); os.makedirs(d, exist_ok=True)
    R.run_port(G.rec_of(src), d, [], quiet=True)
    st = os.path.join(work, 'stage'); os.makedirs(st, exist_ok=True)
    for x in G.stage_dirs(d): shutil.copytree(os.path.join(d, x), os.path.join(st, x), dirs_exist_ok=True)
    return st


def record_session(name, flags=None):
    """Records enh_dir(name)/session.rec from its session.script with session.flags (or flags),
    hidden, starting from its stage; returns the recording's path in a scratch directory."""
    e = enh_dir(name)
    flags = flags if flags is not None else open(os.path.join(e, 'session.flags')).read().strip()
    script_path = os.path.join(e, 'session.script')
    last = max([int(l.split()[0]) for l in open(script_path) if l.split() and l.split()[0].isdigit()] or [0])
    work = tempfile.mkdtemp(prefix='enhcheck-')
    stage = stage_of(name, work)
    home = os.path.join(work, 'home'); os.makedirs(home)
    if stage: shutil.copytree(stage, home, dirs_exist_ok=True)
    data = R.port_data(work)
    env = R.port_env(); env.update(SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy')
    cmd = [R.port_exe(), R.RC.port_data_flag, data, R.RC.port_home_flag, home, '--hidden', '--no-recording',
           '--record', '--sound', '0,0', '--input-script', script_path, '--exit-after', str(last + 3000)] + \
          (['--enhance', flags] if flags else [])
    subprocess.run(cmd, capture_output=True, env=env, timeout=600)
    return os.path.join(home, 'RECORD.OUT'), os.path.join(home, *R.RC.cfg_path.replace('\\', '/').split('/')), work


def sha(path): return hashlib.sha256(open(path, 'rb').read()).hexdigest()


def baseline(args):
    mode = args[0] if args else 'check'
    root = os.path.join(R.RC.sessions, 'enhanced')
    names = args[1:] or (sorted(n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n))) if os.path.isdir(root) else [])
    for name in names:
        rec = os.path.join(enh_dir(name), 'session.rec'); gp = os.path.join(enh_dir(name), 'golden.json')
        work = tempfile.mkdtemp(prefix='enhcheck-')
        stage = stage_of(name, work)
        if mode == 'record':
            r, cfg, w = record_session(name)
            check(f'{name}: recorded from its script', os.path.exists(r), r)
            if os.path.exists(r):
                shutil.copy(r, rec)
                if os.path.exists(cfg): shutil.copy(cfg, os.path.join(enh_dir(name), 'session.cfg'))
                print(f'wrote {os.path.relpath(rec, R.root)}')
            # that the session exercises its flag is shown once by hand when it is made (screenshots
            # with and without it at the same moment; the commit records it): two recordings of
            # one script differ in timing anyway, so comparing them would prove nothing
            shutil.rmtree(w, ignore_errors=True)
        elif mode == 'make':
            a, b = run_checkpoints(rec, stage=stage), run_checkpoints(rec, stage=stage)
            check(f'{name}: the port twice identical', a == b and a, f'{len(a)} and {len(b)} checkpoints')
            if a != b or not a: continue
            meta = {'format': 1, 'session': name, 'made_by': 'port',
                    'note': 'made by the port, not DOS: a regression baseline for an enhancement DOS does not have',
                    'enhancements': R.read_log(rec)[0].get('ENH', []),
                    'recording': {'path': os.path.relpath(rec, R.root), 'sha256': sha(rec)}}
            text = json.dumps(meta, indent=1)
            lines = [json.dumps(c, separators=(',', ':')) for c in a]
            text = text[:text.rindex('}')].rstrip() + ',\n "checkpoints": [\n  ' + ',\n  '.join(lines) + '\n ]\n}\n'
            open(gp, 'w').write(text)
            print(f'wrote {os.path.relpath(gp, R.root)}: {len(a)} checkpoints')
        else:
            g = json.load(open(gp))
            check(f'{name}: made by the port, labelled so', g.get('made_by') == 'port')
            check(f'{name}: its recording unchanged', g['recording']['sha256'] == sha(rec))
            got = run_checkpoints(rec, stage=stage)
            diff = [i for i, (x, y) in enumerate(zip(g['checkpoints'], got)) if x != y]
            check(f'{name}: the port as its baseline', len(got) == len(g['checkpoints']) and not diff,
                  f'{len(got)} checkpoints, not {len(g["checkpoints"])}; differ at {diff[:5]}')
        shutil.rmtree(work, ignore_errors=True)


def png_pixels(data):
    """The decompressed image data of a PNG's bytes (the port's screenshots: one IDAT stream)."""
    import zlib
    i, idat = 8, b''
    while i + 8 <= len(data):
        n = struct.unpack('>I', data[i:i + 4])[0]
        if data[i + 4:i + 8] == b'IDAT': idat += data[i + 8:i + 8 + n]
        i += 12 + n
    return zlib.decompress(idat) if idat else b''


def differ(a, b):
    """The fraction of two screenshots' image bytes that differ (1.0 when either is missing)."""
    a, b = png_pixels(a), png_pixels(b)
    if not a or len(a) != len(b): return 1.0
    return sum(1 for x, y in zip(a, b) if x != y) / len(a)


def script():
    d = tempfile.mkdtemp(prefix='enhcheck-')
    try:
        h = os.path.join(d, 'home'); os.makedirs(h)
        bad = os.path.join(d, 'bad.script'); open(bad, 'w').write('# x\n100 key F1\n200 jump 3\n')
        rc, out = port(h, '--input-script', bad)
        check('a bad script line stops the run, naming it', rc != 0 and 'bad.script:3' in out, out[-300:])
        rc, out = port(h, '--input-script', os.path.join(d, 'missing.script'))
        check('a missing script stops the run', rc != 0 and 'missing.script' in out, out[-300:])
        p = os.path.join(d, 'player'); os.makedirs(p)
        rc, out = port(p, '--enhance', 'skip-intro', '--input-script', os.path.join(d, 'missing.script'), hidden=False)
        check('a bad script stops a player\'s run before it saves anything', rc != 0 and 'enhance=' not in settings(p), settings(p))
        good = os.path.join(d, 'k.script'); open(good, 'w').write('500 key space\n')
        r = os.path.join(d, 'rec'); os.makedirs(r)
        rc, out = port(r, '--record', '--input-script', good)
        rec = os.path.join(r, 'RECORD.OUT')
        s = R.read_log(rec)[0] if os.path.exists(rec) else {}
        keys = [v for _, v in s.get('KEY', [])]
        # wrap-menu: from the start menu's first item (home), up then enter opens the last item
        # where without it the selection stays on the first (the introduction): the screens differ
        m = os.path.join(d, 'menu.script'); open(m, 'w').write('3000 key home\n3300 key up\n3600 key enter\n')
        shots = []
        for flags in ('skip-intro,wrap-menu', 'skip-intro'):
            w = os.path.join(d, 'w' + str(len(shots))); os.makedirs(w)
            png = os.path.join(d, f'menu{len(shots)}.png')
            port(w, '--enhance', flags, '--sound', '0,0', '--input-script', m, '--screenshot-after', '7000',
                 '--screenshot', png, exit_after=7500)
            shots.append(open(png, 'rb').read() if os.path.exists(png) else b'')
        # and positively: the screen is the one end (the last item, directly) gives, taken at 7 s,
        # after the screen's fade-in (the screenshot's time is the wall clock's, the script's the PIT's);
        # nearly the same (a colour cycle may be caught at another moment), and unlike no flag's
        e = os.path.join(d, 'end.script'); open(e, 'w').write('3000 key home\n3300 key end\n3600 key enter\n')
        w = os.path.join(d, 'wend'); os.makedirs(w); png = os.path.join(d, 'menuend.png')
        port(w, '--enhance', 'skip-intro', '--sound', '0,0', '--input-script', e, '--screenshot-after', '7000',
             '--screenshot', png, exit_after=7500)
        shots.append(open(png, 'rb').read() if os.path.exists(png) else b'')
        check('wrap-menu: up from the first item opens the last, not the first',
              differ(shots[0], shots[2]) < 0.10 and differ(shots[0], shots[1]) > 0.50,
              f'differs from end by {differ(shots[0], shots[2]):.3f}, from no flag by {differ(shots[0], shots[1]):.3f}')
        check('a scripted key reaches the game (the recording holds it)',
              any(v & 0xFF == 0x20 or (v >> 8) == 0x39 for v in keys), [hex(v) for v in keys[:12]])
        # look DX DY: relative motion, as a captured mouse gives (mouse-look), not a position
        lk = os.path.join(d, 'look.script'); open(lk, 'w').write('500 look 10 0\n')
        r = os.path.join(d, 'lrec'); os.makedirs(r)
        rc, out = port(r, '--record', '--input-script', lk)
        rec = os.path.join(r, 'RECORD.OUT')
        mouse = R.read_log(rec)[0].get('MOUSE', []) if os.path.exists(rec) else []
        check('a scripted look reaches the game as mouse motion (the recording holds 10, 0)',
              rc == 0 and (10, 0) in [v for _, v in mouse], f'exit {rc}; MOUSE {mouse[:6]}; {out[-200:]}')
    finally:
        shutil.rmtree(d, ignore_errors=True)


def coverage():
    """--enhance perspective's mapper covers exactly the original's pixels: with the project's texture
    probe (<PORT>_TEXTURE_PROBE: each face drawn in a colour of its own), the screen at every checkpoint
    of [replay] coverage_session, dumped in full, is the same with the enhancement on and off."""
    name = R.CFG.raw.get('replay', {}).get('coverage_session')
    if not name: return
    import golden as G
    probe = R.RC.port_name.upper() + '_TEXTURE_PROBE'
    screens = {}
    old = {k: os.environ.get(k) for k in ('UWRPFULL', probe)}
    os.environ['UWRPFULL'] = '0'; os.environ[probe] = '1'
    try:
        for on in (True, False):
            d = tempfile.mkdtemp(prefix='enhcheck-')
            R.run_port(G.rec_of(name), d, ['--enhance', 'perspective'] if on else [], quiet=True)
            screens[on] = [ck['secs'].get('VGA ') for ck in R.read_dump(d)]
            shutil.rmtree(d, ignore_errors=True)
    finally:
        for k, v in old.items():
            if v is None: os.environ.pop(k, None)
            else: os.environ[k] = v
    a, b = screens[True], screens[False]
    bad = [i for i, (x, y) in enumerate(zip(a, b)) if x is None or x != y]
    check(f'perspective covers the original mappers\' pixels exactly ({name}, {len(b)} checkpoints)',
          len(a) == len(b) and a and not bad, f'{len(a)} and {len(b)} checkpoints; differ at {bad[:8]}')


# the colour the hole probe clears the frame buffer to, one the project's 3D view never draws
# ([replay] hole_mark; UW1 42, UW2 173, found by counting every colour in the sessions' frames);
# holes checks it stays unused
HOLE_MARK = int(R.CFG.raw.get('replay', {}).get('hole_mark', 42))


def holes():
    """--enhance wide-pitch draws what is behind the player, so a steep look fills holes the view
    ahead leaves: the wide-pitch baseline session replayed with the project's hole probe
    (<PORT>_HOLE_PROBE: the frame buffer cleared to HOLE_MARK before each frame, and after it a
    'hole-probe: pitch P holes N' line on stderr), once as it is and once with the back pass left
    out (<PORT>_NO_BACK_PASS). The original game itself leaves pixels uncovered in every frame (the
    buffer's undrawn columns at least, and in some frames gaps of its own at the original pitches),
    so the test is not zero but, over the frames beyond the original's pitch: none with more holes
    than without the back pass, and a tenth or fewer with any. With the probe off, no report, and
    the mark drawn nowhere."""
    rec = os.path.join(enh_dir('wide-pitch'), 'session.rec')
    if not os.path.exists(rec): return
    name = R.RC.port_name.upper()
    probe, no_back = name + '_HOLE_PROBE', name + '_NO_BACK_PASS'
    frames, unused = {}, []
    top = tempfile.mkdtemp(prefix='enhcheck-')
    stage = stage_of('wide-pitch', top)     # once, for all three runs
    for run, env in (('unprobed', {'UWRPFB': '1'}), ('without', {probe: str(HOLE_MARK), no_back: '1'}),
                     ('with', {probe: str(HOLE_MARK)})):
        old = {k: os.environ.get(k) for k in (probe, no_back, 'UWRPFB')}
        for k in (probe, no_back, 'UWRPFB'):
            if k in env: os.environ[k] = env[k]
            else: os.environ.pop(k, None)
        work = tempfile.mkdtemp(prefix='enhcheck-')
        try:
            d = os.path.join(work, 'run'); os.makedirs(d)
            R.run_port(rec, d, [], stage=stage, quiet=True)
            log = open(os.path.join(d, 'port.log'), errors='replace').read()
            frames[run] = [(int(p), int(n)) for p, n in re.findall(r'hole-probe: pitch (-?\d+) holes (\d+)', log)]
            if run == 'unprobed':           # the mark in the frames as drawn (UWRPFB's checkpoints)
                unused = [ck['secs']['FBUF'][2:].count(bytes([HOLE_MARK])) for ck in R.read_dump(d) if 'FBUF' in ck['secs']]
        finally:
            shutil.rmtree(work, ignore_errors=True)
            for k, v in old.items():
                if v is None: os.environ.pop(k, None)
                else: os.environ[k] = v
    shutil.rmtree(top, ignore_errors=True)
    wo, w = frames['without'], frames['with']
    check('the hole probe reports nothing when off', not frames['unprobed'], f'{len(frames["unprobed"])} reports')
    check(f'the hole mark ({HOLE_MARK}) is a colour the session never draws', unused and max(unused) == 0,
          f'{len(unused)} frames; up to {max(unused or [0])} pixels of it')
    steep = [i for i, (p, _) in enumerate(w) if abs(p) > 0x1000]
    same = len(w) == len(wo) and all(a[0] == b[0] for a, b in zip(w, wo))
    check('wide-pitch: the session looks beyond the original pitch, the same frames with and without the back pass',
          steep and same, f'{len(steep)} steep frames of {len(w)}; {len(wo)} without')
    if not (steep and same): return
    worse = [w[i] for i in steep if w[i][1] > wo[i][1]]
    check('wide-pitch: the back pass leaves no steep frame with more holes than without it', not worse,
          f'worse at {worse[:6]}')
    # a frame "has holes" when it has more than the session's usual count (the buffer's undrawn
    # columns: UW1 112, UW2 466)
    floor = max(set(n for _, n in wo), key=[n for _, n in wo].count)
    hw, hwo = sum(1 for i in steep if w[i][1] > floor), sum(1 for i in steep if wo[i][1] > floor)
    check('wide-pitch: the back pass fills the holes (steep frames with any, a tenth or fewer of those without it)',
          hwo and hw * 10 <= hwo,
          f'{hw} steep frames of {len(steep)} with holes with it, {hwo} without; most in a frame '
          f'{max(w[i][1] for i in steep) - floor} with, {max(wo[i][1] for i in steep) - floor} without')


def looking():
    """--enhance mouse-look and invert-look: from the mouse-look session's saved game, the ` key and
    then mouse motion (the input script's look) turn the view, where without the key the same
    motion only moves the pointer; invert-look pitches it the other way."""
    if not os.path.isdir(enh_dir('mouse-look')): return
    work = tempfile.mkdtemp(prefix='enhcheck-')
    try:
        stage = stage_of('mouse-look', work)
        shots = {}
        # look-speed (the settings file's) is clamped to 10..400: 0 turns as 10 does, 100000 as 400
        for name, flags, toggle, speed in (('on', 'skip-intro,mouse-look', 1, None),
                                           ('untoggled', 'skip-intro,mouse-look', 0, None),
                                           ('inverted', 'skip-intro,mouse-look,invert-look', 1, None),
                                           ('speed0', 'skip-intro,mouse-look', 1, '0'), ('speed10', 'skip-intro,mouse-look', 1, '10'),
                                           ('speedhuge', 'skip-intro,mouse-look', 1, '100000'),
                                           ('speed400', 'skip-intro,mouse-look', 1, '400')):
            h = os.path.join(work, name)
            shutil.copytree(stage, h) if stage else os.makedirs(h)
            if speed is not None:
                open(os.path.join(h, R.RC.port_name + '.cfg'), 'a').write(f'look-speed={speed}\n')
            sp = os.path.join(work, name + '.script')
            open(sp, 'w').write('3000 key enter\n4500 key enter\n' + ('9000 key grave\n' if toggle else '') +
                                '9500 look 200 0\n10000 look 0 -40\n')
            png = os.path.join(work, name + '.png')
            port(h, '--enhance', flags, '--sound', '0,0', '--input-script', sp, '--screenshot-after', '12000',
                 '--screenshot', png, exit_after=12500)
            shots[name] = open(png, 'rb').read() if os.path.exists(png) else b''
        # the 3D view is about a quarter of the screen, so a turned view differs by about that
        check('mouse-look: after the ` key the mouse turns the view (unlike the same motion without it)',
              differ(shots['on'], shots['untoggled']) > 0.15, f'differs by {differ(shots["on"], shots["untoggled"]):.3f}')
        check('invert-look: the same motion pitches the view the other way',
              differ(shots['on'], shots['inverted']) > 0.15, f'differs by {differ(shots["on"], shots["inverted"]):.3f}')
        lo, hi = differ(shots['speed0'], shots['speed10']), differ(shots['speedhuge'], shots['speed400'])
        check('mouse-look: look-speed is clamped to 10..400 (0 turns as 10, 100000 as 400, and 10 unlike 400)',
              lo < 0.05 and hi < 0.05 and differ(shots['speed10'], shots['speed400']) > 0.15,
              f'0 against 10 {lo:.3f}, 100000 against 400 {hi:.3f}, 10 against 400 {differ(shots["speed10"], shots["speed400"]):.3f}')
    finally:
        shutil.rmtree(work, ignore_errors=True)


def keys():
    """--enhance modern-keys and rune-keys: each key does its job, from the modern-keys session's
    saved game, by screenshots against the same moment without the key (or without the flag)."""
    if not os.path.isdir(enh_dir('modern-keys')): return
    work = tempfile.mkdtemp(prefix='enhcheck-')
    try:
        stage = stage_of('modern-keys', work)
        shots = {}
        def shot(name, flags, lines, after=12000):
            if name in shots: return shots[name]     # a name is one run: reused
            h = os.path.join(work, name)
            shutil.copytree(stage, h) if stage else os.makedirs(h)
            sp = os.path.join(work, name + '.script')
            open(sp, 'w').write('3000 key enter\n4500 key enter\n' + ''.join(l + '\n' for l in lines))
            png = os.path.join(work, name + '.png')
            port(h, '--enhance', flags, '--sound', '0,0', '--input-script', sp, '--screenshot-after', str(after),
                 '--screenshot', png, exit_after=after + 500)
            shots[name] = open(png, 'rb').read() if os.path.exists(png) else b''
            return shots[name]
        mk = 'skip-intro,modern-keys'
        still = shot('still', mk, [])
        # each moved key must do what the original's key for that movement does: closer to it
        # (held as long, without the flag) than to what the same key does without the flag
        hold = lambda k: [f'9000 down {k}', f'10500 up {k}']
        for key, orig, what in (('s', 'x', 'backs'), ('a', 'z', 'slides left'), ('d', 'c', 'slides right'),
                                ('left', 'a', 'turns left'), ('x', 's', 'walks')):
            got = shot('k-' + key, mk, hold(key))
            same = shot('o-' + orig, 'skip-intro', hold(orig))
            other = shot('o-' + key, 'skip-intro', hold(key))
            check(f'modern-keys: {key} {what} (as the original {orig})', differ(got, same) < differ(got, other),
                  f'from the original {orig} {differ(got, same):.3f}, from the original {key} {differ(got, other):.3f}')
        # Shift and Ctrl are movement keys (fly up and down), as in UltimaHacks, so the original's
        # Shift+letter step moves are gone (its steps are Ctrl+arrows): Shift+W is not the step
        sw = shot('k-shift-w', mk, ['9000 down shift', '9100 down w', '10500 up w', '10600 up shift'])
        osw = shot('o-shift-w', 'skip-intro', ['9000 down shift', '9100 down w', '10500 up w', '10600 up shift'])
        check("modern-keys: Shift+W is no longer the original's step move", differ(sw, osw) > 0.15,
              f'from the original Shift+W {differ(sw, osw):.3f}')
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv):
    cmd = argv[0] if argv else 'selftest'
    if cmd == 'selftest': selftest()
    elif cmd == 'registry': registry()
    elif cmd == 'script': script()
    elif cmd == 'coverage': coverage()
    elif cmd == 'holes': holes()
    elif cmd == 'looking': looking()
    elif cmd == 'keys': keys()
    elif cmd == 'presentation': presentation()
    elif cmd == 'baseline': baseline(argv[1:])
    elif cmd == 'all':
        d = tempfile.mkdtemp(prefix='enhcheck-')
        rc, out = port(d, '--enhance', 'list'); shutil.rmtree(d, ignore_errors=True)
        declared = os.path.isdir(os.path.join(R.RC.sessions, 'enhanced'))   # the project has some
        if rc != 0 or 'Enhancements for' not in out:
            if not declared:
                print('enhcheck: the port has no enhancements (no --enhance list, no tests/replay/enhanced): nothing to check')
                return 0
            check('the port lists its enhancements (--enhance list), as tests/replay/enhanced says it has some',
                  False, f'exit {rc}: {out[-300:]}')
        else:
            selftest(); registry(); script(); presentation(); coverage(); holes(); looking(); keys(); baseline(['check'])
    else: sys.exit(__doc__)
    n = len(results); bad = results.count(False)
    print(f'enhcheck: {n - bad} of {n} checks pass')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(R.ARGV))
