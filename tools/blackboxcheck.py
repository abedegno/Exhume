"""Checks the port's black box (runtime/port/sys/blackbox.c) as a player's run uses it: a run with
a window, recording on (no --no-recording), in a fresh home directory. Each session must get a
folder of its own, recordings/YYYYMMDD-HHMMSS, holding its RECORD.OUT and stage/ (the home
directory's files as the session began); stage/ must leave out recordings/ and the RECORDIN/
that the ports' 1.0.0 to 1.2.1 wrote (their recordings went to RECORDIN/YYYYMMDD/RECORD.OUT, one
per day, through the game's 8.3 file names), and an old RECORDIN/ is left as it was. After
seven sessions the newest five are kept.

    python3 tools/blackboxcheck.py [--config PATH]

Each run lasts BLACKBOX_RUN_MS (1500) and starts in a new second, since a session's folder is
named to the second. Exit status 0 when every check passes."""
import os, re, sys, time, shutil, tempfile, subprocess
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import enhcheck as E
from enhcheck import check
import replay as R

RUN_MS = int(os.environ.get('BLACKBOX_RUN_MS', '1500'))
STAMP = re.compile(r'^\d{8}-\d{6}$')
KEEP = 5


def session(home):
    """A player's run (a window, on SDL's dummy drivers; recording as the settings say), started in
    a second of its own."""
    t = time.time()
    time.sleep(1.05 - (t - int(t)))
    env = R.port_env()
    env.update(SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy')
    cmd = [R.port_exe(), R.RC.port_data_flag, R.DATA, R.RC.port_home_flag, home, '--sound', '0,0',
           '--exit-after', str(RUN_MS)]
    r = subprocess.run(cmd, capture_output=True, text=True, errors='replace', env=env, timeout=180)
    return r.returncode, r.stdout + r.stderr


def sessions(home):
    d = os.path.join(home, 'recordings')
    return sorted(n for n in os.listdir(d) if os.path.isdir(os.path.join(d, n))) if os.path.isdir(d) else []


def top(path):
    return sorted(os.listdir(path)) if os.path.isdir(path) else None


def tree(path):
    """Every file under path, relative, with its contents."""
    out = {}
    for dp, _, files in os.walk(path):
        for f in files:
            p = os.path.join(dp, f)
            out[os.path.relpath(p, path)] = open(p, 'rb').read()
    return out


def recording_ok(rec):
    """A recording replay.py reads, with the game's clock in it."""
    try:
        return bool(R.read_log(rec)[0].get('TIME'))
    except Exception:
        return False


def main(argv):
    work = tempfile.mkdtemp(prefix='blackboxcheck-')
    try:
        home = os.path.join(work, 'home'); os.makedirs(home)
        # settings-at-start=0: the settings screen, which a fresh home shows at start, is not this check's
        open(os.path.join(home, R.RC.port_name + '.cfg'), 'w').write('settings-at-start=0\n')
        outs = []
        for _ in range(2): outs.append(session(home))
        names = sessions(home)
        check('two sessions: two folders under recordings/, named YYYYMMDD-HHMMSS',
              len(names) == 2 and all(STAMP.match(n) for n in names), (names, top(home), outs[-1][1][-400:]))
        for n in names:
            s = os.path.join(home, 'recordings', n)
            rec = os.path.join(s, 'RECORD.OUT')
            check(f'{n}: RECORD.OUT, not empty', os.path.isfile(rec) and os.path.getsize(rec) > 0, top(s))
            check(f'{n}: its RECORD.OUT is a recording with the game\'s clock in it', recording_ok(rec))
            check(f'{n}: stage/', os.path.isdir(os.path.join(s, 'stage')), top(s))
        check('no RECORDIN/ in the home directory', not any(x.upper() == 'RECORDIN' for x in os.listdir(home)), top(home))
        if len(names) == 2:
            st = top(os.path.join(home, 'recordings', names[1], 'stage')) or []
            check('the second session\'s stage/ has neither recordings/ nor RECORDIN/',
                  not any(x.lower() in ('recordings', 'recordin') for x in st), st)
        # an old install's RECORDIN/ (1.0.0 to 1.2.1): left as it is, and kept out of stage/
        old = os.path.join(home, 'RECORDIN', '20261009')
        os.makedirs(old)
        open(os.path.join(old, 'RECORD.OUT'), 'wb').write(b'an old recording')
        before = tree(os.path.join(home, 'RECORDIN'))
        for _ in range(5): outs.append(session(home))
        names = sessions(home)
        check(f'seven sessions: the newest {KEEP} are kept', len(names) == KEEP and all(STAMP.match(n) for n in names),
              (names, outs[-1][1][-400:]))
        check('an old RECORDIN/ is left as it was', tree(os.path.join(home, 'RECORDIN')) == before,
              sorted(tree(os.path.join(home, 'RECORDIN'))))
        if names:
            st = top(os.path.join(home, 'recordings', names[-1], 'stage')) or []
            check('with an old RECORDIN/ there, the newest stage/ has neither recordings/ nor RECORDIN/',
                  not any(x.lower() in ('recordings', 'recordin') for x in st), st)
            rec = os.path.join(home, 'recordings', names[-1], 'RECORD.OUT')
            check('the newest session\'s RECORD.OUT is a recording', recording_ok(rec))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    n = len(E.results); bad = E.results.count(False)
    print(f'blackboxcheck: {n - bad} of {n} checks pass')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(R.ARGV))
