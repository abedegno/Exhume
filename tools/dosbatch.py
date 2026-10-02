"""Many sources in headless DOS at once: which DOS, how many sessions, and compiling a list of
sources several to a session, several sessions at a time, each failure tried once more alone.

    from dosbatch import backend, sessions, compile_many
    backend()                          the DOS in use: emu2, dosbox-x, staging or jsdos
    sessions(configured=None)          how many DOS sessions to run at once
    compile_many(cfg, srcs, batch=8)   {stem: (ok, problem lines)}, as tools/build.py's build()

backend() asks tools/dosbackend.mjs once and puts the answer in EXHUME_DOS, so every
dosrun.mjs this process starts uses the same DOS ([toolchain] dos in exhume.toml, or
EXHUME_DOS, chooses it; auto is the first installed of emu2, DOSBox-X, js-dos).

sessions() is EXHUME_DOS_SESSIONS (or [toolchain] sessions, which tools/config.py puts there)
when set, else `configured` (a tool's own setting, such as [gate] sessions) when given, else
the backend's default: one per core up to 12 for a native DOS (more gained nothing measurable
on 14 cores, docs/case-study-uw2.md) and 3 for js-dos, where each session is a headless
Chrome and more of them slow each other and damage more outputs.

compile_many() is what the gate and the modding build use: tools/build.py's batches in
sessions() sessions, then every source that failed in a batch built again in a session of its
own before it counts as failed (a session that dies part of the way through a batch, which
js-dos does, fails every source after the one it died on).
"""
import os, sys, subprocess
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)

BATCH = 8           # sources per DOS session
NATIVE_MAX = 12     # sessions at once in a native DOS, at most
JSDOS = 3           # sessions at once in js-dos
_backend = None


def backend():
    global _backend
    if _backend is None:
        r = subprocess.run(['node', os.path.join(here, 'dosbackend.mjs')], capture_output=True, text=True)
        if r.returncode: sys.exit('tools/dosbackend.mjs: ' + (r.stderr.strip() or f'exit {r.returncode}'))
        _backend = r.stdout.strip()
        os.environ['EXHUME_DOS'] = _backend
    return _backend


def sessions(configured=None):
    if os.environ.get('EXHUME_DOS_SESSIONS'): return max(1, int(os.environ['EXHUME_DOS_SESSIONS']))
    if configured: return max(1, int(configured))
    return JSDOS if backend() == 'jsdos' else max(1, min(os.cpu_count() or 4, NATIVE_MAX))


def compile_many(cfg, srcs, batch=BATCH, jobs=None):
    """Build srcs (paths) into cfg.build, batch to a session and jobs (default sessions()) at
    once; a source that fails in a batch is built once more alone. Returns {stem: (ok, msgs)}."""
    import build
    jobs = jobs or sessions()
    res = build.build(cfg, srcs, jobs=jobs, batch=batch)
    if batch > 1:
        again = [s for s in srcs if not res[cfg.stem(s)][0]]
        if again: res.update(build.build(cfg, again, jobs=jobs, batch=1))
    return res
