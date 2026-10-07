"""Checks the port's settings screen at runtime (runtime/port/ui/settings.c, shown by the SDL
backend's layer): F11 opens it over the game and pauses the game's clock, the keys held when it
opened do not stick, the volume and the window's scale come from the settings file.

    python3 tools/setcheck.py [--config PATH] f11      F11 shows the layer (its title's gold)
    python3 tools/setcheck.py [--config PATH] pause    the game's clock stops while it is open
    python3 tools/setcheck.py [--config PATH] held     a key held when it opens is let go when it closes
    python3 tools/setcheck.py [--config PATH] volume   volume=50 halves what the device plays
    python3 tools/setcheck.py [--config PATH] scale    scale, aspect in the file; --scale overrides
    python3 tools/setcheck.py [--config PATH] all      all of these

Exit status 0 when every check passes."""
import os, sys, struct, hashlib, tempfile, shutil, subprocess, zlib, math
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import enhcheck as E
from enhcheck import check, port, stage_of, differ
import replay as R

GOLD = (0xF2, 0xC6, 0x6D)       # settings.c's C_SELECT, the title's colour


def png_rgb(path):
    """(width, height, rows of RGB bytes) of one of the port's PNGs (24-bit, filter 0)."""
    d = open(path, 'rb').read()
    w, h = struct.unpack('>II', d[16:24])
    raw = E.png_pixels(d)
    rows = []
    for y in range(h):
        row = raw[y * (1 + 3 * w):(y + 1) * (1 + 3 * w)]
        if not row or row[0] != 0: return w, h, None
        rows.append(row[1:])
    return w, h, rows


def gold_pixels(path, tol=24):
    """How many pixels of a PNG are within tol of the title's gold."""
    w, h, rows = png_rgb(path)
    if rows is None: return 0
    n = 0
    for row in rows:
        for x in range(0, w * 3, 3):
            if all(abs(row[x + i] - GOLD[i]) <= tol for i in range(3)): n += 1
    return n


def cfg_path(home): return os.path.join(home, R.RC.port_name + '.cfg')


def write_cfg(home, text):
    os.makedirs(home, exist_ok=True)
    open(cfg_path(home), 'a').write(text)


def script_file(work, name, lines):
    p = os.path.join(work, name + '.script')
    open(p, 'w').write(''.join(l + '\n' for l in lines))
    return p


def fresh(work, name, text=''):
    h = os.path.join(work, name)
    shutil.rmtree(h, ignore_errors=True); os.makedirs(h)
    if text: write_cfg(h, text)
    return h


def f11():
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        sp = script_file(work, 'f11', ['3000 key f11'])
        sp0 = script_file(work, 'none', ['# no events'])
        res = {}
        for name, s in (('open', sp), ('closed', sp0)):
            h = fresh(work, name)
            w = os.path.join(work, name + '.png')
            rc, out = port(h, '--input-script', s, '--screenshot-after', '5000', '--screenshot', os.path.join(work, name + '-s.png'),
                           '--window-shot', w, hidden=False, exit_after=7000)
            res[name] = (gold_pixels(w) if os.path.exists(w) else -1, rc, out[-300:])
        check('F11: the layer shows (its title\'s gold pixels are in the window)', res['open'][0] >= 20, res['open'])
        check('no F11: no gold pixels in the window', res['closed'][0] == 0, res['closed'])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def stage_run(work, name, lines, after, extra=(), text=''):
    """A hidden run from the mouse-look session's saved game (the menu's two Enters load it)."""
    stage = stage_of('mouse-look', work) if not hasattr(stage_run, 'stage') or not os.path.isdir(stage_run.stage) else stage_run.stage
    stage_run.stage = stage
    h = os.path.join(work, 'home-' + name)
    shutil.rmtree(h, ignore_errors=True); shutil.copytree(stage, h)
    if text: write_cfg(h, text)
    sp = script_file(work, name, ['3000 key enter', '4500 key enter'] + lines)
    env = R.port_env(); env.update(SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy')
    cmd = [R.port_exe(), R.RC.port_data_flag, R.port_data(work), R.RC.port_home_flag, h, '--hidden', '--no-recording',
           '--sound', '0,0', '--enhance', 'skip-intro', '--input-script', sp, '--exit-after', str(after)] + list(extra)
    r = subprocess.run(cmd, capture_output=True, text=True, errors='replace', env=env, timeout=300)
    return h, r.stdout + r.stderr


def shot_of(work, name, lines, after):
    png = os.path.join(work, name + '.png')
    if os.path.exists(png): os.remove(png)
    stage_run(work, name, lines, after + 2500, ['--screenshot-after', str(after), '--screenshot', png])
    return open(png, 'rb').read() if os.path.exists(png) else b''


def pause():
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        stage_of('mouse-look', work)
        stage_run.stage = os.path.join(work, 'stage')
        h, out = stage_run(work, 'pause', ['9000 key f11', '13000 key f11'], 17000, ['--record'])
        rec = os.path.join(h, 'RECORD.OUT')
        if not os.path.exists(rec):
            check('pause: a recording was made', False, out[-300:]); return
        runs = R.read_log(rec)[0].get('TIME', [])
        t = [x[1] for x in runs]
        steps = [b - a for a, b in zip(t, t[1:])]
        h0, _ = stage_run(work, 'nopause', [], 17000, ['--record'])
        r0 = R.read_log(os.path.join(h0, 'RECORD.OUT'))[0].get('TIME', []) if os.path.exists(os.path.join(h0, 'RECORD.OUT')) else []
        t0 = [x[1] for x in r0]
        # the clock is 1/256 s: 100 ms is 25 ticks. A level loading is a big step too (UW2's first
        # takes 2.4 s, and its length varies by a few percent), so the limit is 100 ms or the
        # control's largest step with a quarter over; the pause itself is 4 s, 1024 ticks
        top = max([26] + [(b - a) * 5 // 4 for a, b in zip(t0, t0[1:])])
        big = [(i, s) for i, s in enumerate(steps) if s > top]
        check('pause: no step of the game clock over 100 ms (no jump across the pause)', t and not big,
              f'{len(t)} reads, steps over {top}: {big[:6]}')
        check('pause: the run reached past the pause (clock ran on after it)', t and t[-1] - t[0] > 256 * 6,
              f'{(t[-1] - t[0]) / 256:.1f} s of game clock' if t else 'no TIME reads')
        shas = []
        for k in (1, 2):
            d = os.path.join(work, f'replay{k}'); os.makedirs(d)
            R.run_port(rec, d, [], stage=stage_run.stage, quiet=True)
            sp = os.path.join(d, 'STATE.OUT')
            shas.append(hashlib.sha256(open(sp, 'rb').read()).hexdigest() if os.path.exists(sp) else None)
        lost = (r0[-1][1] - r0[0][1] - (t[-1] - t[0])) / 256 if r0 and t else 0
        check('pause: the clock stood still while it was open (about 4 s less than without F11)', 3.0 <= lost <= 5.0,
              f'{lost:.2f} s less')
        check('pause: its replay twice is identical', shas[0] and shas[0] == shas[1], shas)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def held():
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        stage_of('mouse-look', work)
        stage_run.stage = os.path.join(work, 'stage')
        T = 13000
        # S walks forward in the original's keys (W is not bound)
        walked = shot_of(work, 'walk', ['9000 down s', '9500 up s'], T)
        through = shot_of(work, 'through', ['9000 down s', '9500 key f11', '10500 key f11', '11000 up s'], T)
        stuck = shot_of(work, 'stuck', ['9000 down s'], T)
        d, s = differ(through, walked), differ(stuck, walked)
        check('held: S held across the screen stops (the view as if S were let go at 9500)', d <= 0.15, f'{d:.3f}')
        check('held: the check can tell (S held on to the end differs)', s > 0.15, f'{s:.3f}')
    finally:
        shutil.rmtree(work, ignore_errors=True)


def rms(path, limit=100000):
    """The RMS of the first LIMIT samples of a raw 16-bit file. Only the start is the same in two
    runs: the replay runs faster than the device plays, so once the ring is full what is dropped
    depends on timing."""
    d = open(path, 'rb').read() if os.path.exists(path) else b''
    n = min(len(d) // 2, limit)
    if not n: return 0.0
    s = struct.unpack(f'<{n}h', d[:n * 2])
    return math.sqrt(sum(x * x for x in s) / n)


def volume():
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        rec = os.path.join(R.RC.sessions, 'sound.rec') if os.path.exists(os.path.join(R.RC.sessions, 'sound.rec')) else None
        if not rec:
            check('volume: the sound session is there', False, R.RC.sessions); return
        out = {}
        for name, text in (('full', ''), ('half', 'volume=50\n')):
            h = fresh(work, name, text)
            cfg = R.cfg_of(rec)
            if cfg:
                dst = os.path.join(h, *R.RC.cfg_path.replace('\\', '/').split('/'))
                os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy(cfg, dst)
            tap = os.path.join(work, name + '.raw')
            os.environ['PORT_AUDIO_TAP'] = tap
            try:
                rc, o = port(h, R.RC.port_replay_flag, os.path.abspath(rec), hidden=False, exit_after=9000)
            finally:
                os.environ.pop('PORT_AUDIO_TAP', None)
            out[name] = rms(tap)
        ratio = out['half'] / out['full'] if out['full'] else 0
        check('volume: volume=50 plays at 0.4 to 0.6 of the full RMS', out['full'] > 0 and 0.4 <= ratio <= 0.6,
              f'full {out["full"]:.1f}, half {out["half"]:.1f}, ratio {ratio:.3f}')
    finally:
        shutil.rmtree(work, ignore_errors=True)


def window_size(work, name, text, *args):
    h = fresh(work, name, text)
    w = os.path.join(work, name + '.png')
    port(h, '--screenshot-after', '1500', '--screenshot', os.path.join(work, name + '-s.png'), '--window-shot', w, *args,
         hidden=False, exit_after=3000)
    return (png_rgb(w)[:2] if os.path.exists(w) else None), open(cfg_path(h)).read() if os.path.exists(cfg_path(h)) else ''


def scale():
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        size, _ = window_size(work, 'a-off', 'scale=2\naspect=0\n')
        check('scale: scale=2 with aspect off makes the window 640x400', size == (640, 400), size)
        size, _ = window_size(work, 'a-on', 'scale=2\n')
        check('scale: scale=2 with aspect on makes the window 640x480', size == (640, 480), size)
        # at scale 1 the layer's text is too small: the window doubles while the screen is open
        # (the window's size is read in the shot, so the screen is open at 1500 and the shot at 1500)
        h = fresh(work, 'a-one', 'scale=1\n')
        for name, lines in (('open', ['500 key f11']), ('closed', ['500 key f11', '900 key f11'])):
            sp = script_file(work, 'one-' + name, lines)
            w = os.path.join(work, 'one-' + name + '.png')
            port(h, '--input-script', sp, '--screenshot-after', '2500', '--screenshot', os.path.join(work, 'one-s.png'),
                 '--window-shot', w, hidden=False, exit_after=4000)
            size = png_rgb(w)[:2] if os.path.exists(w) else None
            want = (640, 480) if name == 'open' else (320, 240)
            check(f'scale: at scale 1 the window is {want[0]} wide with the screen {name}', size == want, size)
        size, text = window_size(work, 'a-cmd', 'scale=2\n', '--scale', '4')
        check('scale: --scale 4 beats the file\'s 2 (1280 wide)', size and size[0] == 1280, size)
        check('scale: and the file still says scale=2', 'scale=2' in text, text)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv):
    cmd = argv[0] if argv else 'all'
    if cmd == 'all':
        f11(); pause(); held(); volume(); scale()
    elif cmd in ('f11', 'pause', 'held', 'volume', 'scale'):
        globals()[cmd]()
    else: sys.exit(__doc__)
    n = len(E.results); bad = E.results.count(False)
    print(f'setcheck: {n - bad} of {n} checks pass')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(R.ARGV))
