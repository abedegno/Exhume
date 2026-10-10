"""Checks that the player moves the way the movement keys say, in a live run of the port
(Underworld Exhumed's issue 6): from the mouse-look session's saved game, turned to face north,
east, south, west and the saved game's own diagonal, the player slides left (Z), slides right
(C) and walks backwards (X) for half a second, and the port reports where the player ended up
(<PORT>_POS_LOG in the environment: runtime/port/sys/pace.c's games print a "motion:" line at
the end of the run).

The game writes the player's position back in whole 1/256 tiles every physics round, dropping
the fraction, so at a round a tick (a port that draws a 3D frame per tick of the 256 Hz clock)
every slow move towards +x or +y is lost: the key does nothing, or the player drifts along one
axis. So each move must go at least MIN_MOVE units, within MAX_ANGLE degrees of the way the key
points (worked out from the way the player faces at the end), and the runs' 3D frames must come
PORT_FRAME_TICKS (8) or more ticks apart: at most SHORT_FRAMES of them early, and at least 8
ticks a frame on average.

    python3 tools/movecheck.py [--config PATH]          every move (MOVECHECK_JOBS runs at a time, default 2)
    python3 tools/movecheck.py [--config PATH] --show   also print each run's readout

Exit status 0 when every check passes."""
import os, sys, re, math, shutil, tempfile
from concurrent.futures import ThreadPoolExecutor
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import enhcheck as E
from enhcheck import check
import replay as R
import setcheck as S

MIN_MOVE = 32           # 1/8 of a tile; half a second's slide goes about 120 (both games, measured)
MAX_ANGLE = 25          # a move lost on one axis is 45 degrees out at the diagonal
SHORT_FRAMES = 0.05     # the share of frames that may come early (the pace gives up after 40 ms)
MIN_TICKS = 8           # PORT_FRAME_TICKS

# The keys (the original's, both games): A and D turn, Z and C slide left and right, X walks
# backwards. Held 400 ms, A or D turns the player about 45 degrees (measured, both games), and
# the saved game faces about 45 degrees (north-east), so these face north, east, west and
# south. Each heading's moves: (key, what, the move's angle from the facing, clockwise).
SLIDE_LEFT, SLIDE_RIGHT, BACK = ('z', 'slides left', -90), ('c', 'slides right', 90), ('x', 'walks backwards', 180)
HEADINGS = (
    ('north-east (the saved game\'s)', [], 45, (SLIDE_LEFT, SLIDE_RIGHT, BACK)),
    ('north', ['6000 down a', '6400 up a'], 0, (SLIDE_LEFT, SLIDE_RIGHT, BACK)),
    ('east', ['6000 down d', '6400 up d'], 90, (SLIDE_LEFT, SLIDE_RIGHT, BACK)),
    ('west', ['6000 down a', '7200 up a'], 270, (BACK,)),
    ('south', ['6000 down d', '7200 up d'], 180, (BACK,)),
)
MOVE_AT, MOVE_FOR, EXIT_AFTER = 7600, 500, 9000

LINE = re.compile(r'motion: x (-?\d+) y (-?\d+) z (-?\d+) facing (\d+) heading (\d+) frames (\d+) ticks (\d+) short (\d+)')


def readout(out):
    m = LINE.search(out)
    if not m: return None
    v = [int(g) for g in m.groups()]
    return dict(x=v[0], y=v[1], z=v[2], facing=v[3], heading=v[4], frames=v[5], ticks=v[6], short=v[7])


def unit(angle):
    """The game's direction of a heading in degrees: 0 is +y (north), 90 is +x (east)."""
    a = math.radians(angle)
    return math.sin(a), math.cos(a)


def off(a, b):
    """How far apart two headings in degrees are."""
    return abs((a - b + 180) % 360 - 180)


def run(work, name, lines):
    """One run from the saved game, in a directory of its own (each run makes its own view of the
    game's folder there)."""
    d = os.path.join(work, 'run-' + name); os.makedirs(d)
    h, out = S.stage_run(d, name, lines, EXIT_AFTER)
    shutil.rmtree(d, ignore_errors=True)
    return name, readout(out), out


def main(argv):
    if not os.path.isdir(E.enh_dir('mouse-look')):
        print('movecheck: no mouse-look session (tests/replay/enhanced/mouse-look) to start from: nothing to check')
        return 0
    show = '--show' in argv
    os.environ[R.RC.port_name.upper() + '_POS_LOG'] = '1'
    work = tempfile.mkdtemp(prefix='movecheck-')
    try:
        S.use_stage(work)
        jobs = [('still', [])]
        for hname, turn, _, moves in HEADINGS:
            for key, _, _ in moves:
                jobs.append((f'{hname.split()[0]}-{key}', turn + [f'{MOVE_AT} down {key}', f'{MOVE_AT + MOVE_FOR} up {key}']))
        with ThreadPoolExecutor(max_workers=int(os.environ.get('MOVECHECK_JOBS', '2'))) as ex:
            res = {n: (r, out) for n, r, out in ex.map(lambda j: run(work, *j), jobs)}
        if show:
            for n, (r, _) in res.items(): print(f'  {n}: {r}')
        still, out = res['still']
        check(f'the port reports the player\'s position ({R.RC.port_name.upper()}_POS_LOG)', still, out[-400:])
        if not still: return 1
        x0, y0, f0 = still['x'], still['y'], still['facing'] * 360 / 65536
        check('standing still: the player stays put', (still['x'], still['y']) == (x0, y0))
        check('the saved game faces about north-east', off(f0, 45) <= 20, f'{f0:.0f} degrees')
        for hname, _, want, moves in HEADINGS:
            for key, what, turn in moves:
                r, out = res[f'{hname.split()[0]}-{key}']
                if r is None:
                    check(f'facing {hname}, {key.upper()} {what}: the run reports', False, out[-400:]); continue
                facing = r['facing'] * 360 / 65536
                dx, dy = r['x'] - x0, r['y'] - y0
                ux, uy = unit(facing + turn)
                dist = math.hypot(dx, dy)
                angle = math.degrees(math.acos(max(-1, min(1, (dx * ux + dy * uy) / dist)))) if dist else 180
                check(f'facing {hname} ({facing:.0f} degrees), {key.upper()} {what}: it moves {MIN_MOVE} or more '
                      f'within {MAX_ANGLE} degrees of {(facing + turn) % 360:.0f}',
                      off(facing, want) <= 20 and dist >= MIN_MOVE and angle <= MAX_ANGLE,
                      f'moved ({dx}, {dy}), {dist:.0f} at {angle:.0f} degrees off' +
                      ('' if off(facing, want) <= 20 else f'; the turn missed {want} degrees'))
        moved = [res[n][0] for n in res if n != 'still' and res[n][0]]
        frames = sum(r['frames'] for r in moved); ticks = sum(r['ticks'] for r in moved); short = sum(r['short'] for r in moved)
        check(f'the 3D frames come {MIN_TICKS} or more ticks apart (at most {SHORT_FRAMES:.0%} early, '
              f'{MIN_TICKS} ticks a frame on average)',
              frames and short <= SHORT_FRAMES * frames and ticks >= MIN_TICKS * frames,
              f'{short} of {frames} frames early, {ticks / max(frames, 1):.1f} ticks a frame')
    finally:
        shutil.rmtree(work, ignore_errors=True)
    n = len(E.results); bad = E.results.count(False)
    print(f'movecheck: {n - bad} of {n} checks pass')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(R.ARGV))
