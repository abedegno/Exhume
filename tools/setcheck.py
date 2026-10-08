"""Checks the port's settings screen at runtime (runtime/port/ui/settings.c, shown by the SDL
backend's layer): F11 opens it over the game and pauses the game's clock, the keys held when it
opened do not stick, the volume and the window's scale come from the settings file.

    python3 tools/setcheck.py [--config PATH] f11      F11 shows the layer (its title's gold)
    python3 tools/setcheck.py [--config PATH] pause    the game's clock stops while it is open
    python3 tools/setcheck.py [--config PATH] held     a key held when it opens is let go when it closes
    python3 tools/setcheck.py [--config PATH] heldbutton  a mouse button held when it opens is let go when it closes
    python3 tools/setcheck.py [--config PATH] volume   volume=50 halves what the device plays
    python3 tools/setcheck.py [--config PATH] scale    scale, aspect in the file; --scale overrides
    python3 tools/setcheck.py [--config PATH] roundtrip   every row of the game's table: changed on the screen, kept in the file, shown again
    python3 tools/setcheck.py [--config PATH] firstrun    a fresh home shows the screen at start; settings-at-start=0 does not
    python3 tools/setcheck.py [--config PATH] cmdline     --scale 4 beats scale=2 in the file, also after the screen is used
    python3 tools/setcheck.py [--config PATH] restart     a restart row (skip-intro) is saved, not applied, until the next start
    python3 tools/setcheck.py [--config PATH] folder      a folder that is not the game is refused, the file's data= unchanged
    python3 tools/setcheck.py [--config PATH] textentry   keys typed after the screen closes reach a text entry (UW1's save description)
    python3 tools/setcheck.py [--config PATH] badvalues   volume=abc and scale=99 in the file start the run as the screen reads them
    python3 tools/setcheck.py [--config PATH] wclick      a click in window coordinates hits the row drawn there after a scale change
    python3 tools/setcheck.py [--config PATH] lostup      a click after a press whose release was lost still opens a folder row's picker
    python3 tools/setcheck.py [--config PATH] roms        the MT-32 ROMs row says "not found" or "found" (SETCHECK_ROMS=DIR for found)
    python3 tools/setcheck.py [--config PATH] all      all of these: the independent ones side by side (SETCHECK_JOBS, default 4),
                                                       then the timing-sensitive ones two at a time (SETCHECK_ALONE_JOBS); `all --serial` runs them one by one

Exit status 0 when every check passes."""
import os, sys, struct, hashlib, tempfile, shutil, subprocess, zlib, math, time
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import enhcheck as E
from enhcheck import check, port, stage_of, differ, settings
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


def fresh(work, name, text='', start=False):
    """A new home. A player's run shows the screen at start unless the file says settings-at-start=0,
    so it does, as the other checks open it with F11 themselves; START=True leaves that to the game."""
    h = os.path.join(work, name)
    shutil.rmtree(h, ignore_errors=True); os.makedirs(h)
    if not start: write_cfg(h, 'settings-at-start=0\n')
    if text: write_cfg(h, text)
    return h


def f11():
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        sp = script_file(work, 'f11', ['1500 key f11'])
        sp0 = script_file(work, 'none', ['# no events'])
        res = {}
        for name, s in (('open', sp), ('closed', sp0)):
            h = fresh(work, name)
            w = os.path.join(work, name + '.png')
            rc, out = port(h, '--input-script', s, '--screenshot-after', '3000', '--screenshot', os.path.join(work, name + '-s.png'),
                           '--window-shot', w, hidden=False, exit_after=3700)
            res[name] = (gold_pixels(w) if os.path.exists(w) else -1, rc, out[-300:])
        check('F11: the layer shows (its title\'s gold pixels are in the window)', res['open'][0] >= 20, res['open'])
        check('no F11: no gold pixels in the window', res['closed'][0] == 0, res['closed'])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def use_stage(work):
    """The mouse-look session's saved game for stage_run. `all` makes it once, before the timing-sensitive
    checks, and names it in SETCHECK_STAGE (read only: each run copies it into its own home)."""
    shared = os.environ.get('SETCHECK_STAGE')
    if shared and os.path.isdir(shared): stage_run.stage = shared
    else:
        stage_of('mouse-look', work)
        stage_run.stage = os.path.join(work, 'stage')


# When a stage run's game is loaded and ready for the checks' keys: the menu's two Enters at 1 s and
# 2.5 s load the saved game, which takes about 2 s (measured in both games, 2026-10-08: the menu takes
# keys from 0.5 s, the view is still from 2 s after the second Enter), so 6 s leaves it half again.
READY = 6000


def stage_run(work, name, lines, after, extra=(), text=''):
    """A hidden run from the mouse-look session's saved game (the menu's two Enters load it; the
    checks' own keys start at READY)."""
    stage = stage_of('mouse-look', work) if not hasattr(stage_run, 'stage') or not os.path.isdir(stage_run.stage) else stage_run.stage
    stage_run.stage = stage
    h = os.path.join(work, 'home-' + name)
    shutil.rmtree(h, ignore_errors=True); shutil.copytree(stage, h)
    if text: write_cfg(h, text)
    sp = script_file(work, name, ['1000 key enter', '2500 key enter'] + lines)
    env = R.port_env(); env.update(SDL_VIDEODRIVER='dummy', SDL_AUDIODRIVER='dummy')
    cmd = [R.port_exe(), R.RC.port_data_flag, R.port_data(work), R.RC.port_home_flag, h, '--hidden', '--no-recording',
           '--sound', '0,0', '--enhance', 'skip-intro', '--input-script', sp, '--exit-after', str(after)] + list(extra)
    r = subprocess.run(cmd, capture_output=True, text=True, errors='replace', env=env, timeout=300)
    return h, r.stdout + r.stderr


def shot_of(work, name, lines, after):
    png = os.path.join(work, name + '.png')
    if os.path.exists(png): os.remove(png)
    stage_run(work, name, lines, after + 1000, ['--screenshot-after', str(after), '--screenshot', png])
    return open(png, 'rb').read() if os.path.exists(png) else b''


def pause():
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        use_stage(work)
        h, out = stage_run(work, 'pause', [f'{READY} key f11', f'{READY + 4000} key f11'], READY + 8000, ['--record'])
        rec = os.path.join(h, 'RECORD.OUT')
        if not os.path.exists(rec):
            check('pause: a recording was made', False, out[-300:]); return
        runs = R.read_log(rec)[0].get('TIME', [])
        t = [x[1] for x in runs]
        steps = [b - a for a, b in zip(t, t[1:])]
        h0, _ = stage_run(work, 'nopause', [], READY + 8000, ['--record'])
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
        use_stage(work)
        T, r = READY + 4000, READY
        # S walks forward in the original's keys (W is not bound)
        walked = shot_of(work, 'walk', [f'{r} down s', f'{r + 500} up s'], T)
        through = shot_of(work, 'through', [f'{r} down s', f'{r + 500} key f11', f'{r + 1500} key f11', f'{r + 2000} up s'], T)
        stuck = shot_of(work, 'stuck', [f'{r} down s'], T)
        d, s = differ(through, walked), differ(stuck, walked)
        check('held: S held across the screen stops (closer to the view let go after half a second than to the held-on view)',
              d < s, f'vs let go {d:.3f}, vs held on {s:.3f}')
        check('held: the check can tell (S held on to the end differs)', s > 0.15, f'{s:.3f}')
    finally:
        shutil.rmtree(work, ignore_errors=True)


def heldbutton():
    """A mouse button held when the screen opens and let go inside it: the game must get the release
    when it closes. Observed in the recording's BUTTONS stream (what the game read of the buttons):
    the last value it read is 0, and the hold did reach it (a read of 2, the right button)."""
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        use_stage(work)
        at = '160 60 right'
        res = {}
        r = READY
        for name, lines in (('through', [f'{r} mdown {at}', f'{r + 500} key f11', f'{r + 1000} mup {at}', f'{r + 1500} key f11']),
                            ('plain', [f'{r} mdown {at}', f'{r + 500} mup {at}'])):
            h, out = stage_run(work, 'b-' + name, lines, r + 3500, ['--record'])
            rec = os.path.join(h, 'RECORD.OUT')
            res[name] = [v for _, v in R.read_log(rec)[0].get('BUTTONS', [])] if os.path.exists(rec) else []
        for name, vals in res.items():
            check(f'held button ({name}): the hold reached the game, and the last read is 0 (let go)',
                  2 in vals and vals[-1] == 0, f'last {vals[-3:]}')
    finally:
        shutil.rmtree(work, ignore_errors=True)


def rms(path, limit=50000):
    """The RMS of the first LIMIT samples of a raw 16-bit file. Only the start is the same in two
    runs: the replay runs faster than the device plays, so once the ring is full what is dropped
    depends on timing. Measured over 20 pairs of runs per game, the first 50000 samples give the same
    ratio every time (0.5 for volume=50); by 100000 it already wanders from 0.38 to 0.65 (UW2)."""
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
                rc, o = port(h, R.RC.port_replay_flag, os.path.abspath(rec), hidden=False, exit_after=5000)
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
         hidden=False, exit_after=2200)
    return (png_rgb(w)[:2] if os.path.exists(w) else None), open(cfg_path(h)).read() if os.path.exists(cfg_path(h)) else ''


def scale():
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        size, _ = window_size(work, 'a-off', 'scale=2\naspect=0\n')
        check('scale: scale=2 with aspect off makes the window 640x400', size == (640, 400), size)
        size, _ = window_size(work, 'a-on', 'scale=2\n')
        check('scale: scale=2 with aspect on makes the window 640x480', size == (640, 480), size)
        # at scale 1 the layer's text is too small: the window doubles while the screen is open
        # (the window's size is read in the shot, taken 1100 ms after the screen closes)
        h = fresh(work, 'a-one', 'scale=1\n')
        for name, lines in (('open', ['500 key f11']), ('closed', ['500 key f11', '900 key f11'])):
            sp = script_file(work, 'one-' + name, lines)
            w = os.path.join(work, 'one-' + name + '.png')
            port(h, '--input-script', sp, '--screenshot-after', '2000', '--screenshot', os.path.join(work, 'one-s.png'),
                 '--window-shot', w, hidden=False, exit_after=2700)
            size = png_rgb(w)[:2] if os.path.exists(w) else None
            want = (640, 480) if name == 'open' else (320, 240)
            check(f'scale: at scale 1 the window is {want[0]} wide with the screen {name}', size == want, size)
        size, text = window_size(work, 'a-cmd', 'scale=2\n', '--scale', '3')
        check('scale: --scale 3 beats the file\'s 2 (960 wide)', size and size[0] == 960, size)
        check('scale: and the file still says scale=2', 'scale=2' in text, text)
        # the test display (SDL's dummy driver) is 1024x768: 3x is the largest window that fits it, and
        # a larger scale opens at that rather than past the screen (macOS clamps such a window, and the
        # settings screen's steps above it looked alike); the file keeps the larger one for a larger display
        size, text = window_size(work, 'a-big', 'scale=8\n')
        check('scale: scale=8 on a 1024x768 display opens the largest that fits, 960x720', size == (960, 720), size)
        check('scale: and the file still says scale=8', 'scale=8' in text, text)
        size, text = window_size(work, 'a-bigcmd', '', '--scale', '6')
        check('scale: so does --scale 6', size == (960, 720), size)
    finally:
        shutil.rmtree(work, ignore_errors=True)


TABS = 5    # settings.h's SET_TABS; the rows' tab numbers are its enum (sound, controls, display, enhancements, game)
CYCLE, BOOL, SLIDER, FOLDER = range(4)


def table_rows():
    """The game's table as `--settings-list` prints it: one row a line, tab-separated: tab, kind, key
    (- for none: the sound cards, kept in DATA\\UW.CFG), label, def, lo, hi, step, names, stored (| between)."""
    d = tempfile.mkdtemp(prefix='setcheck-')
    try:
        rc, out = port(os.path.join(d, 'h'), '--settings-list')
    finally:
        shutil.rmtree(d, ignore_errors=True)
    rows = []
    for l in out.splitlines():
        f = l.split('\t')
        if len(f) == 10:
            rows.append(dict(tab=int(f[0]), kind=int(f[1]), key=f[2], label=f[3], d=int(f[4]), lo=int(f[5]), hi=int(f[6]),
                             step=int(f[7]), names=f[8].split('|') if f[8] else [], stored=f[9].split('|') if f[9] else []))
    return rows


def nav(tab, k, t0=500, dt=100):
    """Script lines: F11, Tab to the tab, Down to its K-th row; returns the lines and the next time."""
    lines = [f'{t0} key f11']; t = t0
    for _ in range(tab): t += dt; lines.append(f'{t} key tab')
    for _ in range(k): t += dt; lines.append(f'{t} key down')
    return lines, t + dt


def cfg_file(home): return os.path.join(home, *R.RC.cfg_path.replace('\\', '/').split('/'))


def card_cfg(home, music, speech):
    """DATA\\UW.CFG as the game's installer writes it (the first two lines are the cards)."""
    p = cfg_file(home); os.makedirs(os.path.dirname(p), exist_ok=True)
    open(p, 'w', newline='').write(f'{music} -1 -1 -1 sound\r\n{speech} -1 -1 -1 speech\r\n0 cuts\r\n')


def roundtrip(part=0, parts=1):
    """PART of PARTS: the rows are dealt out in turn, so that `all` can run the slices side by side."""
    rows = table_rows()
    if part == 0:       # once, not in every slice
        check('the table lists rows in all of the tabs but the generated one', {r['tab'] for r in rows} >= {0, 1, 2, 4}, [r['label'] for r in rows])
        # a row that resizes the window must not be a slider: dragging it would move the row under the pointer
        sc = [r for r in rows if r['key'] == 'scale']
        check('the Window scale row is a cycle, 1x to 8x (no drag resizes the window)',
              sc and sc[0]['kind'] == CYCLE and sc[0]['names'] == [f'{i}x' for i in range(1, 9)], sc)
    cards = [r for r in rows if r['key'] == '-']
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        def start_home(name):
            h = fresh(work, name, 'scale=2\n')
            if cards: card_cfg(h, *[c['stored'][c['d']] for c in cards[:2]])
            return h
        seen = {}
        seen_noise = part != 0      # the control for the check below is made once, by the first slice
        done = -1
        for r in rows:
            k = seen.get(r['tab'], 0); seen[r['tab']] = k + 1      # its place in its tab, folder rows counted
            if r['kind'] == FOLDER: continue
            done += 1
            if done % parts != part: continue
            label = r['label']
            if r['key'] == 'settings-at-start': r['d'] = 0      # start_home's file turns it off, so that is where it starts
            if r['key'] == 'scale': r['d'] = 1                  # start_home's file says 2x: 3x is the largest the test display offers
            lines, t = nav(r['tab'], k)
            if r['kind'] == SLIDER:
                up = r['d'] + r['step'] <= r['hi']
                want = r['d'] + r['step'] if up else r['d'] - r['step']
                lines.append(f'{t} key {"right" if up else "left"}')
            else:
                n = len(r['names']) or 2
                want = (r['d'] + 1) % n if r['kind'] == CYCLE else 1 - r['d']
                lines.append(f'{t} key enter')
            lines.append(f'{t + 200} key f11')
            h = start_home('rt-' + label.replace(' ', '_'))
            rc, out = port(h, '--input-script', script_file(work, 'rt', lines), hidden=False, exit_after=t + 900)
            if r['key'] == '-':
                which = cards.index(r)
                text = open(cfg_file(h)).read().splitlines() if os.path.exists(cfg_file(h)) else []
                got = text[which].split()[0] if len(text) > which else None
                ok, expect = got == r['stored'][want], r['stored'][want]
            else:
                if r['kind'] == CYCLE: expect = r['stored'][want] if r['stored'] else str(want)
                else: expect = str(want)
                ok, got = f"{r['key']}={expect}" in settings(h).splitlines(), None
            check(f'roundtrip {label}: changed on the screen, the file holds {expect}', ok, (got, settings(h), out[-200:]))
            # a second run with that file: the row's value text is drawn, and differs from the default's
            lines2, t2 = nav(r['tab'], k)
            shots = {}
            same = [('again', start_home('rt1-' + label.replace(' ', '_')))] if not seen_noise else []
            seen_noise = True
            for name, home in (('changed', h), ('default', start_home('rt0-' + label.replace(' ', '_')))) + tuple(same):
                if name == 'changed':
                    h2 = os.path.join(work, 'rt2'); shutil.rmtree(h2, ignore_errors=True); shutil.copytree(h, h2); home = h2
                w = os.path.join(work, f'rt-{name}.png')
                if os.path.exists(w): os.remove(w)
                port(home, '--input-script', script_file(work, 'rt2', lines2), '--screenshot-after', str(t2 + 800),
                     '--screenshot', os.path.join(work, 'rt-s.png'), '--window-shot', w, hidden=False, exit_after=t2 + 1400)
                shots[name] = open(w, 'rb').read() if os.path.exists(w) else b''
            if 'again' in shots:
                check('roundtrip: the check can tell (two shots of the same state are identical)', shots['again'] and differ(shots['again'], shots['default']) == 0,
                      f"{differ(shots['again'], shots['default']):.5f}")
            d = differ(shots['changed'], shots['default'])
            check(f'roundtrip {label}: the saved value shows on the screen (differs from the default\'s)', shots['changed'] and d > 0, f'{d:.5f}')
    finally:
        shutil.rmtree(work, ignore_errors=True)


def firstrun(part=None):
    """PART 'gold': the screen at start (counts of its gold pixels, which a busy machine does not move);
    'resume': the game running on after Esc (a picture 5 s into the run); None: both."""
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        if part != 'resume': firstrun_gold(work)
        if part != 'gold': firstrun_resume(work)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def firstrun_gold(work):
    for name, start, want in (('first', True, True), ('off', False, False)):
        h = fresh(work, name, start=start)
        w = os.path.join(work, name + '.png')
        port(h, '--screenshot-after', '1500', '--screenshot', os.path.join(work, name + '-s.png'), '--window-shot', w,
             hidden=False, exit_after=2200)
        n = gold_pixels(w) if os.path.exists(w) else -1
        check(f'first run: {"a fresh home shows the screen with no key pressed" if want else "settings-at-start=0 does not"}',
              n >= 20 if want else n == 0, n)
    # a player turns it off in the screen, and the next start does not show it
    h = fresh(work, 'toggle', start=True)
    lines, t = nav(4, 2, t0=1000)       # Game tab, "Show this at start": the screen is open already, so F11 would close it
    lines = lines[1:]                    # no F11 first
    lines.append(f'{t} key enter'); lines.append(f'{t + 200} key f11')
    port(h, '--input-script', script_file(work, 'toggle', lines), hidden=False, exit_after=t + 900)
    check('first run: turning "Show this at start" off in the screen is kept', 'settings-at-start=0' in settings(h), settings(h))


def firstrun_resume(work):
    # Esc closes the first-run screen and the game starts: its own picture moves on from the
    # paused first frame (the run kept open shows that frame throughout)
    res = {}
    for name, lines in (('kept', ['# no events']), ('closed', ['1500 key esc'])):
        h = fresh(work, 'fr-' + name, start=True)
        s, w = os.path.join(work, f'fr-{name}-s.png'), os.path.join(work, f'fr-{name}.png')
        port(h, '--input-script', script_file(work, 'fr-' + name, lines), '--screenshot-after', '5000', '--screenshot', s,
             '--window-shot', w, hidden=False, exit_after=5700)
        res[name] = (open(s, 'rb').read() if os.path.exists(s) else b'', gold_pixels(w) if os.path.exists(w) else -1)
    d = differ(res['closed'][0], res['kept'][0]) if res['closed'][0] and res['kept'][0] else 0
    check('first run: Esc closes the screen (no gold in the window afterwards)', res['closed'][1] == 0 and res['kept'][1] >= 20,
          (res['closed'][1], res['kept'][1]))
    check('first run: and the game resumes (its picture differs from the paused first frame)', d > 0.01, f'{d:.4f}')


def cmdline():
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        h = fresh(work, 'cl', 'mouse=follow\n')
        rc, out = port(h, '--mouse', 'lock', hidden=False)
        check('cmdline: --mouse lock locks the run', 'mouse: lock' in out, out[-300:])
        rc, out = port(h, hidden=False)
        check('cmdline: the file\'s mouse setting stands with no option', 'mouse: lock' in out or 'mouse: follow' in out, out[-300:])
        h = fresh(work, 'cl2', 'mouse=lock\n')
        rc, out = port(h, hidden=False)
        check('cmdline: mouse=lock in the file locks a run', 'mouse: lock' in out, out[-300:])
        # --scale 3 beats scale=2 in the file for the run, also once the screen is used: toggling the
        # 4:3 row (the Display tab's third) applies the effective values, not the file's
        rows = table_rows()
        k = [r['key'] for r in rows if r['tab'] == 2].index('aspect')
        lines, t = nav(2, k)
        lines += [f'{t} key enter', f'{t + 200} key f11']
        h = fresh(work, 'cl3', 'scale=2\n')
        w = os.path.join(work, 'cl3.png')
        port(h, '--scale', '3', '--input-script', script_file(work, 'cl3', lines), '--screenshot-after', str(t + 1200),
             '--screenshot', os.path.join(work, 'cl3-s.png'), '--window-shot', w, hidden=False, exit_after=t + 1700)
        size = png_rgb(w)[:2] if os.path.exists(w) else None
        check('cmdline: --scale 3 over scale=2 stays at scale 3 after the screen toggles aspect (960x600)', size == (960, 600), size)
        text = settings(h).splitlines()
        check('cmdline: and the file still says scale=2 (aspect=0 written)', 'scale=2' in text and 'aspect=0' in text, text)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def enh_names():
    d = tempfile.mkdtemp(prefix='setcheck-')
    try:
        rc, out = port(os.path.join(d, 'h'), '--enhance', 'list')
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return [l.split()[0] for l in out.splitlines() if l.startswith('  ')]


def restart():
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        names = enh_names()
        k = names.index('skip-intro')
        lines, t = nav(3, k)
        lines += [f'{t} key enter', f'{t + 200} key f11']
        h = fresh(work, 'rs')
        rc, out = port(h, '--input-script', script_file(work, 'rs', lines), hidden=False, exit_after=t + 900)
        check('restart: skip-intro turned on in the screen does not apply in that run (no enhance: line)', 'enhance:' not in out, out[-300:])
        check('restart: and the file says enhance=skip-intro', 'enhance=skip-intro' in settings(h), settings(h))
        rc, out = port(h, hidden=False)
        check('restart: the next start logs enhance: skip-intro', 'enhance: skip-intro' in out, out[-300:])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def folder():
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        empty = os.path.join(work, 'empty'); os.makedirs(empty)
        want = 'data=' + os.path.abspath(R.DATA)
        lines, t = nav(4, 0)
        lines.append(f'{t} key enter')
        res = {}
        for name, ans in (('refused', empty), ('control', ''), ('accepted', os.path.abspath(R.DATA))):
            h = fresh(work, 'f-' + name, want + '\n')
            w = os.path.join(work, name + '.png')
            if ans: os.environ['PORT_FOLDER_ANSWER'] = ans
            try:
                # the control never presses Enter on the row (without an answer set it would open the real dialog)
                rc, out = port(h, '--input-script', script_file(work, 'folder', lines if ans else lines[:-1]), '--screenshot-after', str(t + 800),
                               '--screenshot', os.path.join(work, 'f-s.png'), '--window-shot', w, hidden=False, exit_after=t + 1300)
            finally:
                os.environ.pop('PORT_FOLDER_ANSWER', None)
            res[name] = (settings(h), out, open(w, 'rb').read() if os.path.exists(w) else b'')
        check('folder: an empty folder is refused and the file\'s data= is unchanged',
              want in res['refused'][0].splitlines() and 'refused' in res['refused'][1], (res['refused'][0], res['refused'][1][-300:]))
        check('folder: the screen shows "That folder does not hold the game"',
              'That folder does not hold the game' in res['refused'][1] and differ(res['refused'][2], res['control'][2]) > 0.0002,
              f"{differ(res['refused'][2], res['control'][2]):.5f}")
        check('folder: the game\'s own folder is accepted', 'accepted' in res['accepted'][1] and want in res['accepted'][0].splitlines(), res['accepted'][1][-300:])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def lostup():
    """A click after a press whose release never came (the host lost it: a macOS fullscreen switch
    can) must still be a click: a Mac player had to click a folder row twice in fullscreen. Scale 2
    with aspect off is the layer one to one: the Game tab's first row, Game folder, is at y 72..91;
    the lost press is on the empty area below the rows."""
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        lines, t = nav(4, 0)
        res = {}
        for name, lost in (('lost', True), ('control', False)):
            ls = lines + ([f'{t} wdown 320 300'] if lost else []) + [f'{t + 300} wclick 100 82', f'{t + 800} key f11']
            h = fresh(work, 'lu-' + name, 'scale=2\naspect=0\n')
            os.environ['PORT_FOLDER_ANSWER'] = os.path.abspath(R.DATA)
            try:
                rc, out = port(h, '--input-script', script_file(work, 'lu-' + name, ls), hidden=False, exit_after=t + 1400)
            finally:
                os.environ.pop('PORT_FOLDER_ANSWER', None)
            res[name] = out
        check('lostup: a click on a folder row after a lost release opens its picker at once', 'Game folder accepted' in res['lost'], res['lost'][-300:])
        check('lostup: and with no lost release, as before', 'Game folder accepted' in res['control'], res['control'][-300:])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def textentry():
    """UW1 only (UW2's save dialog is not reached by these keys): the screen opened and closed again in
    the middle of a text entry leaves the typing where it was: keys typed while it is open do not reach
    the entry, and the ones typed after it closes do."""
    if R.RC.port_name != 'uw1port': return
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        use_stage(work)
        # Ctrl+S opens the save dialog, Enter takes the first slot and asks for the description
        r = READY
        head = [f'{r} down ctrl', f'{r + 100} key s', f'{r + 200} up ctrl', f'{r + 1000} key enter']
        for name, mid in (('through', [f'{r + 2000} key f11', f'{r + 2200} key z', f'{r + 2300} key z', f'{r + 2500} key f11']), ('plain', [])):
            h, out = stage_run(work, 'te-' + name, head + mid + [f'{r + 3000} key a', f'{r + 3200} key b', f'{r + 3500} key enter'], r + 5000)
            p = os.path.join(h, 'SAVE1', 'DESC')
            d = open(p, 'rb').read()[:40] if os.path.exists(p) else None
            check(f'textentry ({name}): the save\'s description is ab', d is not None and d.startswith(b'ab') and not d.startswith(b'abz') and b'z' not in d, (d, out[-200:]))
    finally:
        shutil.rmtree(work, ignore_errors=True)


def badvalues():
    """Bad values in the file start the run with what the screen reads for them (its defaults): a
    window of the default scale 3 (960x720), and sound (volume=abc is 100, not 0)."""
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        rec = os.path.join(R.RC.sessions, 'sound.rec')
        if not os.path.exists(rec):
            check('badvalues: the sound session is there', False, R.RC.sessions); return
        h = fresh(work, 'bad', 'volume=abc\nscale=99\nlook-speed=abc\n')
        cfg = R.cfg_of(rec)
        if cfg:
            dst = os.path.join(h, *R.RC.cfg_path.replace('\\', '/').split('/'))
            os.makedirs(os.path.dirname(dst), exist_ok=True); shutil.copy(cfg, dst)
        tap, w = os.path.join(work, 'bad.raw'), os.path.join(work, 'bad.png')
        os.environ['PORT_AUDIO_TAP'] = tap
        try:
            rc, out = port(h, R.RC.port_replay_flag, os.path.abspath(rec), '--screenshot-after', '3000',
                           '--screenshot', os.path.join(work, 'bad-s.png'), '--window-shot', w, hidden=False, exit_after=5000)
        finally:
            os.environ.pop('PORT_AUDIO_TAP', None)
        size = png_rgb(w)[:2] if os.path.exists(w) else None
        check('badvalues: scale=99 starts at the screen\'s reading, the default 3 (960x720)', size == (960, 720), (size, out[-300:]))
        r = rms(tap)
        check('badvalues: volume=abc plays (the screen reads it as 100, not 0)', r > 0, f'RMS {r:.1f}')
        size, _ = window_size(work, 'zero', '', '--scale', '0')
        check('badvalues: --scale 0 is taken as 1 (320x240)', size == (320, 240), size)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def wclick():
    """A click given in window coordinates (the input script's wclick, an SDL event like a player's)
    after the window's scale changed on the screen: it must hit the row drawn there now. Scale 2
    with aspect off is 640x400, the layer one to one; Right on Window scale makes it 3x (960x600),
    where the Whole-number scaling row (the Display tab's fourth, layer y 132..151) is at window
    y 198..227. At its middle, y 213, the old mapping holds no row, so a stale mapping changes nothing."""
    work = tempfile.mkdtemp(prefix='setcheck-')
    try:
        rows = [r['key'] for r in table_rows() if r['tab'] == 2]
        k_scale, k_int = rows.index('scale'), rows.index('integer')
        res = {}
        for name, change in (('scaled', True), ('control', False)):
            lines, t = nav(2, k_scale)
            if change: lines.append(f'{t} key right')
            y = (72 + 20 * k_int + 10) * 3 // 2       # the row's middle in the layer, at 1.5 window pixels a layer pixel
            lines += [f'{t + 600} wclick 450 {y}', f'{t + 900} key f11']
            h = fresh(work, 'wc-' + name, 'scale=2\naspect=0\n')
            rc, out = port(h, '--input-script', script_file(work, 'wc-' + name, lines), hidden=False, exit_after=t + 1500)
            res[name] = (settings(h).splitlines(), out[-300:])
        check('wclick: after Right on Window scale the file says scale=3', 'scale=3' in res['scaled'][0], res['scaled'])
        check('wclick: a click at the row\'s new place in the window turns Whole-number scaling off', 'integer=0' in res['scaled'][0], res['scaled'])
        check('wclick: the check can tell (the same click at scale 2 changes nothing)', 'integer=0' not in res['control'][0], res['control'])
    finally:
        shutil.rmtree(work, ignore_errors=True)


def roms():
    """The MT-32 ROMs row says whether the ROMs the port uses are there. A home and HOME of their own
    (the search looks in other emulators' folders under HOME) and no ROM variable: "not found".
    With SETCHECK_ROMS=DIR (a folder holding a ROM pair) given as the folder picker's answer: "found"."""
    work = tempfile.mkdtemp(prefix='setcheck-')
    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.endswith('_MT32_ROMS')}
    old_home = os.environ.get('HOME')
    try:
        os.environ['HOME'] = os.path.join(work, 'userhome'); os.makedirs(os.environ['HOME'])
        lines, t = nav(0, 0)
        h = fresh(work, 'none')
        rc, out = port(h, '--input-script', script_file(work, 'none', lines + [f'{t + 300} key f11']), hidden=False, exit_after=t + 1500)
        check('roms: with no ROMs anywhere the row says "not found"', 'MT-32 ROMs: not found' in out, out[-400:])
        src = os.environ.get('SETCHECK_ROMS')
        if not src:
            print('roms: SETCHECK_ROMS is not set, so "found" is not checked')
            return
        k = [r['label'] for r in table_rows() if r['tab'] == 0].index('MT-32 ROMs')
        lines, t = nav(0, k)
        lines += [f'{t} key enter', f'{t + 300} key f11']
        h = fresh(work, 'found')
        os.environ['PORT_FOLDER_ANSWER'] = os.path.abspath(src)
        try:
            rc, out = port(h, '--input-script', script_file(work, 'found', lines), hidden=False, exit_after=t + 1500)
        finally:
            os.environ.pop('PORT_FOLDER_ANSWER', None)
        check('roms: a ROM folder chosen on the screen makes it say "found"', 'MT-32 ROMs: found' in out, out[-400:])
    finally:
        if old_home is None: os.environ.pop('HOME', None)
        else: os.environ['HOME'] = old_home
        os.environ.update(saved)
        shutil.rmtree(work, ignore_errors=True)


CHECKS = ('f11', 'pause', 'held', 'heldbutton', 'volume', 'scale', 'roundtrip', 'firstrun', 'cmdline',
          'restart', 'folder', 'textentry', 'badvalues', 'wclick', 'lostup', 'roms')
# What `all` runs side by side (each in its own process and temp home): checks whose answer does not
# depend on when the game gets its events or frames. ROUNDTRIP is dealt out in slices, longest jobs first.
POOL = ('roundtrip:0/3', 'roundtrip:1/3', 'roundtrip:2/3', 'scale', 'stage', 'f11', 'firstrun:gold', 'folder', 'cmdline', 'wclick', 'lostup', 'restart', 'roms')
# What `all` runs after the pool, two at a time (SETCHECK_ALONE_JOBS): these read the game clock, the
# recorded input, the audio or a picture at a wall-clock moment, or press keys at fixed times in a loaded
# game, so a busy machine would move what they measure. A live run paced in real time uses little of a
# core (the port rests while the game waits on its clock), so two side by side leave each its timing;
# the pool's four would not. (firstrun's second half compares a picture 5 s into the run; badvalues the
# audio and a picture; textentry types at fixed times; the others as the names say.)
ALONE = ('held', 'pause', 'textentry', 'heldbutton', 'firstrun:resume', 'volume', 'badvalues')   # longest first


def timed(name):
    t = time.time()
    if name == 'stage':                # `all`'s one copy of the saved game the stage runs start from
        d = os.environ['SETCHECK_STAGE_DIR']; stage_of('mouse-look', d)
    elif ':' in name:
        base, part = name.split(':')
        globals()[base](*(int(x) for x in part.split('/'))) if '/' in part else globals()[base](part)
    else: globals()[name]()
    print(f'setcheck: {name} took {time.time() - t:.1f} s', flush=True)


def job(name, env):
    """One check as a process of its own: (name, seconds, return code, output)."""
    cfg = ['--config', os.environ['EXHUME_CONFIG']] if os.environ.get('EXHUME_CONFIG') else []
    t = time.time()
    try:
        r = subprocess.run([sys.executable, os.path.abspath(__file__)] + cfg + [name], capture_output=True, text=True,
                           errors='replace', env=env, timeout=1200)
        return name, time.time() - t, r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired as e:
        return name, time.time() - t, 99, f'FAIL {name}: timed out\n{e.stdout or ""}'


def run_all(workers, alone_workers=2):
    """The pool, then the timed checks, fewer at a time. The totals are the sum of the processes'."""
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.time()
    stage_dir = tempfile.mkdtemp(prefix='setcheck-stage-')
    env = dict(os.environ, SETCHECK_STAGE_DIR=stage_dir)
    tally = [0, 0]      # checks run, checks failed
    bad_jobs = []
    times = {}

    def report(r, label):
        name, secs, rc, out = r
        times[name] = secs
        print(f'--- {name} ({label}, {secs:.1f} s)')
        print(out.rstrip(), flush=True)
        for l in out.splitlines():
            if l.startswith('ok  '): tally[0] += 1
            elif l.startswith('FAIL'): tally[0] += 1; tally[1] += 1
        if rc != 0 and 'FAIL' not in out: tally[0] += 1; tally[1] += 1
        if rc != 0: bad_jobs.append(name)

    try:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(lambda n: job(n, env), POOL): report(r, 'pool')
        t1 = time.time()
        env['SETCHECK_STAGE'] = os.path.join(stage_dir, 'stage')
        with ThreadPoolExecutor(max_workers=alone_workers) as ex:
            for r in ex.map(lambda n: job(n, env), ALONE): report(r, 'timed')
        print(f'setcheck: the pool took {t1 - t0:.1f} s, the timed checks {time.time() - t1:.1f} s')
    finally:
        shutil.rmtree(stage_dir, ignore_errors=True)
    print(f'setcheck: {tally[0] - tally[1]} of {tally[0]} checks pass in {time.time() - t0:.1f} s')
    return 1 if tally[1] or bad_jobs else 0


def main(argv):
    cmd = argv[0] if argv else 'all'
    if cmd == 'all' and '--serial' not in argv:
        return run_all(int(os.environ.get('SETCHECK_JOBS', '4')), int(os.environ.get('SETCHECK_ALONE_JOBS', '2')))
    if cmd == 'all':
        for name in CHECKS: timed(name)
    elif cmd in CHECKS or cmd == 'stage' or cmd.split(':')[0] in ('roundtrip', 'firstrun') and ':' in cmd:
        timed(cmd)
    else: sys.exit(__doc__)
    n = len(E.results); bad = E.results.count(False)
    print(f'setcheck: {n - bad} of {n} checks pass')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(R.ARGV))
