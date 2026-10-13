"""Checks the port's black box (runtime/port/sys/blackbox.c) as a player's run uses it: a run with
a window, recording on (no --no-recording), in a fresh home directory. Each session must get a
folder of its own, recordings/YYYYMMDD-HHMMSS, holding its RECORD.OUT and stage/ (the game's
files in the home directory as the session began); stage/ must leave out recordings/ and the RECORDIN/
that the ports' 1.0.0 to 1.2.1 wrote (their recordings went to RECORDIN/YYYYMMDD/RECORD.OUT, one
per day, through the game's 8.3 file names), and an old RECORDIN/ is left as it was. With seven
session folders the newest five are kept: four older ones (stamped 2000-01-01, each with a file and
a stage/ in it) are planted after two real sessions, and a third session must leave the newest
five, two of the planted ones among them as they were.

stage/ is an allow-list (blackbox.c's BLACKBOX_STAGE): before the third session the home
directory gets what a player's may hold that is not the player's to send or the game's own
state: a game copied out of GOG's CD image (gog-cd/), MT-32 ROMs (roms/, mt32-roms/), game
folders (UW1/, UW2/) and a stray file at the top, all with made-up contents; a saved game,
SAVE1/; and an override file in DATA/ beside UW.CFG (STRINGS.PAK: the home directory's DATA/ is
read ahead of the game's own, so a replay needs it). The third session's stage/ must hold the
saved game and the whole of DATA/, as they are in the home directory, and none of the rest.
Symbolic links are never followed, where the host lets this make them (not every Windows
account can): a saved game's folder that is a link to a folder outside the home directory
(SAVE2), a link in a saved game's folder back up to the home directory (SAVE3/LOOP, which
followed would copy the home directory into itself until the paths ran out) and a link in DATA/
to a file outside are all left out of stage/, and a planted session folder that is a link is
pruned as a link, the folder it leads to left as it was.

A crash on the game's thread must write out the recording (crash.c): a session in a home
directory of its own, with an input script whose "fault" event makes the game's thread write
through a null pointer (sys/pit.c), must end by the fault, say so, and leave a whole recording.
The event is a test's alone (sys/inscript.c): the script is refused, and the run ends before the
game starts with no fault, unless the environment has EXHUME_TEST_FAULT=1, which this check sets
for the run that faults; a player's build given such a script cannot be made to crash. The run
that faults must leave a whole recording:
the black box's call count FFFFFFFFh in the header, the MISC stream (written only when the
recording is closed) and the clock. On macOS and Linux the signal handlers are the process's, so
this passed before the game's thread installed them too; on Windows, whose C runtime keeps them
per thread, it is the check of issue 8's cut-short recordings.

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


def session(home, script=None, env_add=None):
    """A player's run (a window, on SDL's dummy drivers; recording as the settings say), started in
    a second of its own; with an input script's text, that script; env_add, more environment."""
    t = time.time()
    time.sleep(1.05 - (t - int(t)))
    env = R.port_env()
    env.pop('EXHUME_TEST_FAULT', None)
    env.update(SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy', **(env_add or {}))
    cmd = [R.port_exe(), R.RC.port_data_flag, R.DATA, R.RC.port_home_flag, home, '--sound', '0,0',
           '--exit-after', str(RUN_MS)]
    if script:
        path = os.path.join(home, '..', 'blackboxcheck.script')
        open(path, 'w').write(script)
        cmd += ['--input-script', path]
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


def game_file(rel):
    """The game's own file rel ('/' between names), its names matched without case."""
    p = R.DATA
    for name in rel.split('/'):
        p = os.path.join(p, next((n for n in os.listdir(p) if n.upper() == name.upper()), name))
    return p


def link(target, at):
    """A symbolic link at `at` to target; False where the host will not make one (Windows without
    the right to)."""
    try:
        os.symlink(target, at, target_is_directory=os.path.isdir(target))
        return True
    except (OSError, NotImplementedError):
        return False


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
        # four older sessions' folders, planted rather than recorded (a session takes seconds): with
        # the two real ones and the third session's, seven, of which the two oldest planted must go
        real = sessions(home)
        planted = [f'20000101-00000{i}' for i in range(1, 5)]
        outside = os.path.join(work, 'outside'); os.makedirs(outside)
        for n in planted:
            os.makedirs(os.path.join(home, 'recordings', n, 'stage'))
            open(os.path.join(home, 'recordings', n, 'MARKER'), 'w').write(n)
            open(os.path.join(home, 'recordings', n, 'stage', 'MARKER'), 'w').write(n)
        # the oldest planted session a link to a folder outside: pruned as a link, the folder kept
        linked_session = os.path.join(outside, 'session')
        shutil.move(os.path.join(home, 'recordings', planted[0]), linked_session)
        links = link(linked_session, os.path.join(home, 'recordings', planted[0]))
        if not links: shutil.move(linked_session, os.path.join(home, 'recordings', planted[0]))
        outside_before = tree(outside)
        kept_before = {n: tree(os.path.join(home, 'recordings', n)) for n in planted[2:]}
        # what stage/ must leave out (fake contents, never game files), and a saved game it keeps
        out_of_stage = ['gog-cd/' + R.RC.port_name.upper().replace('PORT', '') + '/DATA/STRINGS.PAK', 'gog-cd/.complete',
                        'roms/MT32_CONTROL.ROM', 'mt32-roms/CM32L_PCM.ROM', 'UW1/UW.EXE', 'UW2/UW2.EXE',
                        'notes.txt']
        for f in out_of_stage + ['SAVE1/DESC', 'SAVE3/DESC']:
            os.makedirs(os.path.dirname(os.path.join(home, f)) or home, exist_ok=True)
            open(os.path.join(home, f), 'wb').write(b'planted by blackboxcheck: ' + f.encode())
        # the override: the game reads it ahead of its own, so it is a copy of the game's own (a
        # made-up one would stop the game); a translation's would differ only in its strings
        os.makedirs(os.path.join(home, 'DATA'), exist_ok=True)
        shutil.copyfile(game_file('DATA/STRINGS.PAK'), os.path.join(home, 'DATA', 'STRINGS.PAK'))
        # links stage/ must not follow: a saved game's folder outside, a loop, a file outside
        if links:
            os.makedirs(os.path.join(outside, 'save'))
            open(os.path.join(outside, 'save', 'DESC'), 'wb').write(b'planted by blackboxcheck: outside')
            open(os.path.join(outside, 'FILE.PAK'), 'wb').write(b'planted by blackboxcheck: outside')
            link(os.path.join(outside, 'save'), os.path.join(home, 'SAVE2'))
            link(home, os.path.join(home, 'SAVE3', 'LOOP'))
            link(os.path.join(outside, 'FILE.PAK'), os.path.join(home, 'DATA', 'LINK.PAK'))
            outside_before = tree(outside)
        outs.append(session(home))
        names = sessions(home)
        check(f'seven sessions: the newest {KEEP} are kept (the two oldest planted ones gone)',
              len(names) == KEEP and all(STAMP.match(n) for n in names) and names[:4] == planted[2:] + real
              and names[4] not in planted + real, (names, outs[-1][1][-400:]))
        check('the planted sessions kept are as they were',
              all(tree(os.path.join(home, 'recordings', n)) == t for n, t in kept_before.items()),
              {n: sorted(tree(os.path.join(home, 'recordings', n))) for n in planted[2:]})
        check('an old RECORDIN/ is left as it was', tree(os.path.join(home, 'RECORDIN')) == before,
              sorted(tree(os.path.join(home, 'RECORDIN'))))
        if names:
            st = top(os.path.join(home, 'recordings', names[-1], 'stage')) or []
            check('with an old RECORDIN/ there, the newest stage/ has neither recordings/ nor RECORDIN/',
                  not any(x.lower() in ('recordings', 'recordin') for x in st), st)
            rec = os.path.join(home, 'recordings', names[-1], 'RECORD.OUT')
            check('the newest session\'s RECORD.OUT is a recording', recording_ok(rec))
            staged = {k.replace(os.sep, '/').upper(): v for k, v in tree(os.path.join(home, 'recordings', names[-1], 'stage')).items()}
            home_files = {k.replace(os.sep, '/').upper(): v for k, v in tree(home).items()}
            check('the newest stage/ has the saved game (SAVE1/) as it is in the home directory',
                  'SAVE1/DESC' in staged and staged['SAVE1/DESC'] == home_files.get('SAVE1/DESC'), sorted(staged))
            check('the newest stage/ has DATA/UW.CFG as it is in the home directory',
                  'DATA/UW.CFG' in staged and staged['DATA/UW.CFG'] == home_files.get('DATA/UW.CFG'), sorted(staged))
            check('the newest stage/ has the override file DATA/STRINGS.PAK as it is in the home directory',
                  'DATA/STRINGS.PAK' in staged and staged['DATA/STRINGS.PAK'] == home_files.get('DATA/STRINGS.PAK'), sorted(staged))
            left_in = [f for f in out_of_stage if f.upper() in staged]
            check('the newest stage/ leaves out gog-cd/, roms/, mt32-roms/, UW1/, UW2/ and a stray file',
                  not left_in, left_in)
            others = [f for f in staged if not re.match(r'(SAVE[0-4]|DATA)/', f)]
            check('the newest stage/ holds nothing but SAVE0 to SAVE4 and DATA/', not others, others)
            if links:
                followed = [f for f in staged if f.startswith(('SAVE2/', 'SAVE3/LOOP', 'DATA/LINK.PAK'))]
                check('the newest stage/ follows no link: not SAVE2 (a link outside), SAVE3/LOOP (a loop) or DATA/LINK.PAK',
                      not followed and 'SAVE3/DESC' in staged, (followed, sorted(staged)[:20]))
                check('a planted session folder that is a link is pruned as a link, the folder it led to left as it was',
                      not os.path.lexists(os.path.join(home, 'recordings', planted[0])) and tree(outside) == outside_before,
                      sorted(tree(outside)))
            else:
                print('blackboxcheck: this host makes no symbolic links here; the link checks are skipped')
        # a fault on the game's thread: the recording written out whole
        home = os.path.join(work, 'fault'); os.makedirs(home)
        open(os.path.join(home, R.RC.port_name + '.cfg'), 'w').write('settings-at-start=0\n')
        rc, out = session(home, '800 fault\n')
        check('a script with the fault event is refused without EXHUME_TEST_FAULT=1, and nothing faults',
              rc != 0 and 'EXHUME_TEST_FAULT' in out and 'host fault' not in out, (rc, out[-400:]))
        shutil.rmtree(os.path.join(home, 'recordings'), ignore_errors=True)
        rc, out = session(home, '800 fault\n', {'EXHUME_TEST_FAULT': '1'})
        names = sessions(home)
        check('a fault on the game\'s thread ends the run, reported as a host fault',
              rc != 0 and 'host fault' in out, (rc, out[-400:]))
        log = None
        if len(names) == 1:
            try: log = R.read_log(os.path.join(home, 'recordings', names[0], 'RECORD.OUT'))
            except Exception as e: log = None
        check('after the fault the recording is whole: call count FFFFFFFFh, the MISC stream and the clock',
              log is not None and log[1] == 0xFFFFFFFF and 'MISC' in log[0] and bool(log[0].get('TIME')),
              (names, log and (hex(log[1]), sorted(log[0]))))
    finally:
        shutil.rmtree(work, ignore_errors=True)
    n = len(E.results); bad = E.results.count(False)
    print(f'blackboxcheck: {n - bad} of {n} checks pass')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(R.ARGV))
