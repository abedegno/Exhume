"""Compile or assemble sources in headless DOS into <build>/<STEM>/<STEM>.OBJ.

    python3 tools/build.py SRC [SRC ...] [--jobs N] [--batch K]
    python3 tools/build.py --all [--jobs N] [--batch K]      every source with a target line
                                                              or a /* fardata */ line

Each source's switches come from its `/* opts: ... */` line (else the config's defaults);
the command lines from the toolchain profile. Several sources share one DOS session (K per
session, default 1; --all defaults to 12) and N sessions run at once (default 3: more
emulators at once slow each other and make the emulator's damaged outputs more frequent).

The old object is deleted before a build, so a failed compile never leaves a stale object
behind for match or verify to read. Each source's log is <build>/<STEM>/BUILD.LOG.
Exit status 1 if any source failed."""
import os, sys, re, subprocess, tempfile, shutil, glob
from concurrent.futures import ThreadPoolExecutor
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config


def log_problems(cfg, text):
    """(problem lines, fatal?) in a build log, by the profile's patterns."""
    lg = cfg.profile.get('log', {})
    msgs = [l for l in text.splitlines() if re.search(lg.get('problem', 'Error'), l)
            and not (lg.get('benign') and re.search(lg['benign'], l))]
    return msgs, any(re.search(lg.get('fatal', 'Error|Fatal'), l) for l in msgs)


def _session(cfg, srcs, timeout):
    """One DOS session building srcs. Returns {stem: log text}; objects land in place."""
    tmp = tempfile.mkdtemp(prefix='exhume-build-')
    cmd = ['node', os.path.join(here, 'dosrun.mjs'), tmp, '-t', str(timeout)]
    for s in cfg.stage: cmd += ['--stage', s]
    stems = []
    for src in srcs:
        stem = cfg.stem(src); ext = os.path.splitext(src)[1].upper(); stems.append(stem)
        _, opts = cfg.directives(src)
        kind = 'asm' if ext == '.ASM' else 'c'
        line = cfg.profile[kind]['command'].format(opts=opts, file=stem + ext)
        cmd += ['-f', f'{src}={stem}{ext}', '-c', f'{line} > {stem}.LOG', '-O', stem + '.OBJ', '-O', stem + '.LOG']
        obj = cfg.obj(stem)
        if os.path.exists(obj): os.remove(obj)
    r = subprocess.run(cmd, capture_output=True, text=True)
    logs = {}
    for stem in stems:
        out = os.path.join(cfg.build, stem); os.makedirs(out, exist_ok=True)
        lp = os.path.join(tmp, stem + '.LOG')
        logs[stem] = open(lp, encoding='latin1').read() if os.path.exists(lp) else ''
        if r.returncode: logs[stem] += '\nexhume: the DOS run failed: ' + r.stdout.strip().replace('\n', '; ')
        open(os.path.join(out, 'BUILD.LOG'), 'w', encoding='latin1').write(logs[stem])
        if os.path.exists(os.path.join(tmp, stem + '.OBJ')):
            shutil.copyfile(os.path.join(tmp, stem + '.OBJ'), os.path.join(out, stem + '.OBJ'))
    shutil.rmtree(tmp, ignore_errors=True)
    return logs


def build(cfg, srcs, jobs=3, batch=1, timeout=300):
    """Build srcs; returns {stem: (ok, problem lines)}."""
    groups = [srcs[i:i + batch] for i in range(0, len(srcs), batch)]
    res = {}
    with ThreadPoolExecutor(max(1, jobs)) as pool:
        for logs in pool.map(lambda g: _session(cfg, g, timeout * max(1, len(g) // 4 + 1)), groups):
            for stem, text in logs.items():
                msgs, fatal = log_problems(cfg, text)
                ok = not fatal and os.path.exists(cfg.obj(stem))
                res[stem] = (ok, msgs if msgs or ok else ['no object written'])
    return res


def all_sources(cfg):
    out = []
    for p in sorted(glob.glob(os.path.join(cfg.src, '*.C')) + glob.glob(os.path.join(cfg.src, '*.ASM'))):
        text = open(p, encoding='latin1').read(4000)
        if re.search(r'/\*\s*(target:\s*\w+|fardata)\s*\*/', text): out.append(p)
    return out


def main(argv):
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    def opt(name, default):
        return int(a[a.index(name) + 1]) if name in a else default
    jobs = opt('--jobs', 3)
    srcs = all_sources(cfg) if '--all' in a else [x for i, x in enumerate(a) if not x.startswith('--') and (i == 0 or a[i - 1] not in ('--jobs', '--batch'))]
    if not srcs: sys.exit(__doc__)
    batch = opt('--batch', 12 if '--all' in a else 1)
    res = build(cfg, [os.path.abspath(s) for s in srcs], jobs, batch)
    bad = 0
    for stem, (ok, msgs) in sorted(res.items()):
        for m in msgs: print(f'{stem}: {m}')
        if not ok: bad += 1; print(f'{stem}: FAILED')
    print(f'-- {len(res) - bad}/{len(res)} built into {cfg.build}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
