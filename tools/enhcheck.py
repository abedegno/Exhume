"""Checks a port's enhancements (runtime/port/sys/enhance.c; Underworld Exhumed's
docs/ENHANCEMENTS.md): each is off by default and carried by the sessions played with it.

    python3 tools/enhcheck.py [--config PATH] selftest      the tools' own handling of format 5
    python3 tools/enhcheck.py [--config PATH] registry      the options, the settings and the
                                                            recordings' enhancements
    python3 tools/enhcheck.py [--config PATH] presentation  each presentation enhancement leaves
                                                            every session's game state as DOS's
    python3 tools/enhcheck.py [--config PATH] baseline check|make [NAME ...]
                                                            the port-made goldens of tests/replay/
                                                            enhanced/NAME (timing and gameplay)
    python3 tools/enhcheck.py [--config PATH] all           selftest, registry, presentation and
                                                            baseline check

Exit status 0 when every check passes."""
import os, sys, json, struct, hashlib, tempfile, shutil, subprocess
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


def run_checkpoints(rec, extra=()):
    """The port's checkpoints replaying rec: [{'ck': header, 'secs': canon}]."""
    import golden as G
    d = tempfile.mkdtemp(prefix='enhcheck-')
    try:
        R.run_port(rec, d, list(extra), quiet=True)
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


def sha(path): return hashlib.sha256(open(path, 'rb').read()).hexdigest()


def baseline(args):
    mode = args[0] if args else 'check'
    root = os.path.join(R.RC.sessions, 'enhanced')
    names = args[1:] or (sorted(n for n in os.listdir(root) if os.path.isdir(os.path.join(root, n))) if os.path.isdir(root) else [])
    for name in names:
        rec = os.path.join(enh_dir(name), 'session.rec'); gp = os.path.join(enh_dir(name), 'golden.json')
        if mode == 'make':
            a, b = run_checkpoints(rec), run_checkpoints(rec)
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
            got = run_checkpoints(rec)
            diff = [i for i, (x, y) in enumerate(zip(g['checkpoints'], got)) if x != y]
            check(f'{name}: the port as its baseline', len(got) == len(g['checkpoints']) and not diff,
                  f'{len(got)} checkpoints, not {len(g["checkpoints"])}; differ at {diff[:5]}')


def script():
    d = tempfile.mkdtemp(prefix='enhcheck-')
    try:
        h = os.path.join(d, 'home'); os.makedirs(h)
        bad = os.path.join(d, 'bad.script'); open(bad, 'w').write('# x\n100 key F1\n200 jump 3\n')
        rc, out = port(h, '--input-script', bad)
        check('a bad script line stops the run, naming it', rc != 0 and 'bad.script:3' in out, out[-300:])
        rc, out = port(h, '--input-script', os.path.join(d, 'missing.script'))
        check('a missing script stops the run', rc != 0 and 'missing.script' in out, out[-300:])
        good = os.path.join(d, 'k.script'); open(good, 'w').write('500 key space\n')
        r = os.path.join(d, 'rec'); os.makedirs(r)
        rc, out = port(r, '--record', '--input-script', good)
        rec = os.path.join(r, 'RECORD.OUT')
        s = R.read_log(rec)[0] if os.path.exists(rec) else {}
        keys = [v for _, v in s.get('KEY', [])]
        check('a scripted key reaches the game (the recording holds it)',
              any(v & 0xFF == 0x20 or (v >> 8) == 0x39 for v in keys), [hex(v) for v in keys[:12]])
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main(argv):
    cmd = argv[0] if argv else 'selftest'
    if cmd == 'selftest': selftest()
    elif cmd == 'registry': registry()
    elif cmd == 'script': script()
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
            selftest(); registry(); script(); presentation(); baseline(['check'])
    else: sys.exit(__doc__)
    n = len(results); bad = results.count(False)
    print(f'enhcheck: {n - bad} of {n} checks pass')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(R.ARGV))
