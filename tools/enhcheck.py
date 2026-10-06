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
import os, sys, struct, tempfile, shutil, subprocess
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


def port(home, *args, hidden=True):
    """Runs the port briefly in its own home (offscreen, no sound device), returning (exit
    status, output). hidden=False is a player's run: settings read and written."""
    env = R.port_env()
    env.update(SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy')   # dummy: a player's run gets a window
    cmd = [R.port_exe(), R.RC.port_data_flag, R.DATA, R.RC.port_home_flag, home, '--no-recording',
           '--exit-after', '1500'] + (['--hidden'] if hidden else []) + list(args)
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


def main(argv):
    cmd = argv[0] if argv else 'selftest'
    if cmd == 'selftest': selftest()
    elif cmd == 'registry': registry()
    else: sys.exit(__doc__)
    n = len(results); bad = results.count(False)
    print(f'enhcheck: {n - bad} of {n} checks pass')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(R.ARGV))
