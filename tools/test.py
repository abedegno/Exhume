"""The test tiers of a port (docs/port.md, "Verifying a port"): what the pre-push hook runs, and
the long run.

    python3 tools/test.py [--config PATH] fast     the gate, the port build, the routine fuzzing's
                                                   quick run, and every session replayed in the
                                                   port against its golden: what the pre-push hook
                                                   runs (tools/install-hooks.sh PROJECT "make test")
    python3 tools/test.py [--config PATH] full     the gate, both port builds, the goldens made again
                                                   from DOS (each session twice, checked identical),
                                                   every session against them in the port and in the
                                                   UBSan build, the sound drivers against the real
                                                   ones, the fuzzing's deep run and the coverage
                                                   report

The steps are exhume.toml's [test] fast and full, each a list of [name, command, needs]: needs,
optional, names an earlier step whose failure skips this one (the replays need the port).
Commands are split like a shell line, may use {python}, {exhume}, {config}, {root} and
{build}, and run in the project root. Without a [test] section the tiers are the ones UW2
uses, below, which need [port], [replay] and [fuzz].

Each step's output goes to the terminal as it runs; at the end a summary lists every step with
its time and result, and the exit status is 1 if any step failed. After `full`, a golden that
the regenerated runs changed is reported (git status of [replay] golden): review it, and
commit it with the change that explains it.
"""
import os, sys, time, shlex, subprocess

here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
EXHUME = os.path.dirname(here)
import portcfg

T = '{python} {exhume}/tools/'
C = ' --config {config}'
DEFAULT = {
    'fast': [['the gate', T + 'gate.py' + C + ' check'],
             ['port build', T + 'portbuild.py' + C],
             ['fuzzing, quick', T + 'fuzzasm.py' + C, 'port build'],
             ['sessions against the goldens', T + 'replay.py' + C + ' verify all', 'port build']],
    'full': [['the gate', T + 'gate.py' + C + ' check'],
             ['port build', T + 'portbuild.py' + C],
             ['port-debug build', T + 'portbuild.py' + C + ' --debug'],
             ['goldens from DOS, twice each', T + 'replay.py' + C + ' golden all'],
             ['sessions against the goldens', T + 'replay.py' + C + ' verify all', 'port build'],
             ['sessions in the UBSan build', T + 'replay.py' + C + ' verify all --debug', 'port-debug build'],
             ['sound drivers', T + 'replay.py' + C + ' drivers', 'port build'],
             ['fuzzing, deep', T + 'fuzzasm.py' + C + ' --deep', 'port build'],
             ['coverage report', T + 'coverage.py' + C, 'port build']],
}


def main():
    cfg, argv = portcfg.cli()
    tier = argv[0] if argv else 'fast'
    steps = portcfg.test(cfg).get(tier) or DEFAULT.get(tier)
    if not steps: sys.exit(f'usage: tools/test.py [--config PATH] fast|full (no [test] {tier} in {cfg.file})')
    py = os.path.join(EXHUME, '.venv', 'bin', 'python')
    subst = dict(python=py if os.path.exists(py) else sys.executable, exhume=EXHUME, config=cfg.file,
                 root=cfg.root, build=cfg.build)
    results = []; failed = set(); t0 = time.time()
    for st in steps:
        name, cmd, needs = st[0], st[1], (st[2] if len(st) > 2 else None)
        if needs in failed:
            results.append((name, None, 'skipped: ' + needs + ' failed')); failed.add(name); continue
        print(f'\n==== {name}', flush=True)
        t = time.time()
        rc = subprocess.run([x.format(**subst) for x in shlex.split(cmd)], cwd=cfg.root).returncode
        results.append((name, time.time() - t, 'ok' if rc == 0 else f'FAILED (exit {rc})'))
        if rc: failed.add(name)
    if tier == 'full':
        g = portcfg.replay(cfg).golden
        r = subprocess.run(['git', 'status', '--porcelain', '--', g], cwd=cfg.root, capture_output=True, text=True)
        changed = [l for l in r.stdout.splitlines() if l.strip()]
        if changed:
            print(f'\nnote: the regenerated goldens differ from the committed ones in {len(changed)} files '
                  f'(git status {os.path.relpath(g, cfg.root)}): review them, and commit them with the change that explains them')
    print(f'\n==== test {tier}: {time.time() - t0:.0f} s')
    for name, dt, res in results:
        took = '' if dt is None else '%6.1f s' % dt
        print(f'  {name:32s} {took:8s}  {res}')
    print('PASSED' if not failed else 'FAILED: ' + ', '.join(n for n, _, r in results if r != 'ok'))
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
