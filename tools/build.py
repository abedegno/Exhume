"""Compile or assemble sources in headless DOS into <build>/<STEM>/<STEM>.OBJ.

    python3 tools/build.py SRC [SRC ...] [--jobs N] [--batch K]
    python3 tools/build.py --all [--jobs N] [--batch K]      every source with a target line
                                                              or a /* fardata */ line

Each source's switches come from its `/* opts: ... */` line (else the config's defaults);
the command lines from the toolchain profile. Several sources share one DOS session (K per
session, default 1; --all defaults to 12; a source that fails in a batch is built once more
in a session of its own) and N sessions run at once. N defaults to what
tools/dosbatch.py's sessions() says for the DOS in use (tools/dosbackend.mjs): one per core up
to 12 in emu2 or DOSBox, 3 in js-dos, where more emulators at once slow each other and make
the emulator's damaged outputs more frequent; EXHUME_DOS_SESSIONS or [toolchain] sessions
override it.

The project's shared headers ([project] include, default src/include), and the runtime's
(runtime/include: portable.h, unless the project has a header of that name), are copied into
C:\\ beside the sources for every session, where `#include "name.h"` finds them; a header with
the name of a file the toolchain stages stops the build, since it would replace that file.
Sources are found in every directory under src (tools/sources.py).

The old object is deleted before a build, so a failed compile never leaves a stale object
behind for match or verify to read. Each source's log is <build>/<STEM>/BUILD.LOG.
Exit status 1 if any source failed."""
import os, sys, re, subprocess, tempfile, shutil, glob
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config, sources, compile_context


def log_problems(cfg, text):
    """(problem lines, fatal?) in a build log, by the profile's patterns."""
    lg = cfg.profile.get('log', {})
    msgs = [l for l in text.splitlines() if re.search(lg.get('problem', 'Error'), l)
            and not (lg.get('benign') and re.search(lg['benign'], l))]
    return msgs, any(re.search(lg.get('fatal', 'Error|Fatal'), l) for l in msgs)


def shared_headers(cfg, stage=None):
    """[(path, DOS name)] of the include directories' headers (the project's, then the
    runtime's, a name staged once), checked against the names the toolchain stages."""
    staged = set()
    for s in (cfg.stage if stage is None else stage):
        if os.path.isdir(s): staged.update(n.upper() for n in os.listdir(s))
        else: staged.add(os.path.basename(s).upper())
    out, have = [], set()
    for d in cfg.includes:
        if not os.path.isdir(d): continue
        for n in sorted(os.listdir(d)):
            if not n.lower().endswith(('.h', '.inc', '.ash')) or n.upper() in have: continue
            if n.upper() in staged:
                sys.exit(f'{os.path.join(d, n)} has the name of a file the toolchain stages; rename it')
            out.append((os.path.join(d, n), n.upper())); have.add(n.upper())
    return out



def publish_object(path, raw):
    """Expose only a completely written object; clean up an interrupted write."""
    descriptor, temporary = tempfile.mkstemp(prefix='.object-', suffix='.tmp',
                                             dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _session(cfg, srcs, timeout):
    """One DOS session building srcs. Returns {stem: log text}; objects land in place."""
    tmp = tempfile.mkdtemp(prefix='exhume-build-')
    cmd = ['node', os.path.join(here, 'dosrun.mjs'), tmp, '-t', str(timeout)]
    which = getattr(cfg, 'compiler', lambda src: None)
    compiler = which(srcs[0]) if srcs else None
    if any(which(src) != compiler for src in srcs):
        raise ValueError('one DOS session builds sources of one compiler only')
    stage = cfg.compilers[compiler]['stage'] if compiler else cfg.stage
    for s in stage: cmd += ['--stage', s]
    for h, name in shared_headers(cfg, stage): cmd += ['-f', f'{h}={name}']
    stems = []
    source_paths = {}
    for src in srcs:
        stem = cfg.stem(src); ext = os.path.splitext(src)[1].upper(); stems.append(stem)
        source_paths[stem] = src
        obj = cfg.obj(stem)
        if os.path.exists(obj): os.remove(obj)
        composed = compile_context.compose_source(src, cfg) if ext == '.C' else None
        staged_source = src
        if composed is not None:
            staged_source = os.path.join(tmp, 'context-' + stem + '.C')
            with open(staged_source, 'w', encoding='latin1') as stream:
                stream.write(composed)
        _, opts = cfg.directives(src)
        kind = 'asm' if ext == '.ASM' else 'c'
        command = cfg.compilers[compiler]['c_command'] if compiler and kind == 'c' else cfg.profile[kind]['command']
        line = command.format(opts=opts, file=stem + ext)
        cmd += ['-f', f'{staged_source}={stem}{ext}', '-c', f'{line} > {stem}.LOG', '-O', stem + '.OBJ', '-O', stem + '.LOG']
    r = subprocess.run(cmd, capture_output=True, text=True)
    logs = {}
    for stem in stems:
        out = os.path.join(cfg.build, stem); os.makedirs(out, exist_ok=True)
        lp = os.path.join(tmp, stem + '.LOG')
        logs[stem] = Path(lp).read_text(encoding='latin1') if os.path.exists(lp) else ''
        if r.returncode: logs[stem] += '\nexhume: the DOS run failed: ' + r.stdout.strip().replace('\n', '; ')
        compiled = os.path.join(tmp, stem + '.OBJ')
        if os.path.exists(compiled):
            try:
                with open(compiled, 'rb') as stream:
                    raw = stream.read()
                raw = compile_context.finish_object(source_paths[stem], cfg, raw,
                                                    os.path.join(out, 'CONTEXT'))
                publish_object(os.path.join(out, stem + '.OBJ'), raw)
            except (ValueError, OSError, RuntimeError) as error:
                logs[stem] += '\nFatal: compilation context: ' + str(error) + '\n'
        with open(os.path.join(out, 'BUILD.LOG'), 'w', encoding='latin1') as stream:
            stream.write(logs[stem])
    shutil.rmtree(tmp, ignore_errors=True)
    return logs


def build(cfg, srcs, jobs=None, batch=1, timeout=300):
    """Build srcs, jobs sessions at once (default: dosbatch.sessions()); returns {stem: (ok,
    problem lines)}."""
    if jobs is None:
        import dosbatch
        jobs = dosbatch.sessions() if len(srcs) > batch else 1
    by_compiler = {}
    which = getattr(cfg, 'compiler', lambda src: None)
    for src in srcs: by_compiler.setdefault(which(src), []).append(src)
    groups = [g[i:i + batch] for g in by_compiler.values() for i in range(0, len(g), batch)]
    res = {}
    with ThreadPoolExecutor(max(1, jobs)) as pool:
        for logs in pool.map(lambda g: _session(cfg, g, timeout * max(1, len(g) // 4 + 1)), groups):
            for stem, text in logs.items():
                msgs, fatal = log_problems(cfg, text)
                # an object with no log of its own is not trusted: the session died around it
                ok = not fatal and os.path.exists(cfg.obj(stem)) and os.path.getsize(cfg.obj(stem)) > 0 \
                    and bool(text.split('\nexhume: the DOS run failed')[0].strip())
                res[stem] = (ok, msgs if msgs or ok else ['no object written'])
    return res


def all_sources(cfg):
    """Every source with a target line or a /* fardata */ line, in every directory under src."""
    out = []
    for p in sources.all_sources(cfg):
        text = open(p, encoding='latin1').read(4000)
        if re.search(r'/\*\s*(target:\s*\w+|fardata)\s*\*/', text): out.append(p)
    return out


def main(argv):
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    def opt(name, default):
        return int(a[a.index(name) + 1]) if name in a else default
    jobs = opt('--jobs', None)
    srcs = all_sources(cfg) if '--all' in a else [x for i, x in enumerate(a) if not x.startswith('--') and (i == 0 or a[i - 1] not in ('--jobs', '--batch'))]
    if not srcs: sys.exit(__doc__)
    batch = opt('--batch', 12 if '--all' in a else 1)
    import dosbatch
    if len(srcs) > 1:
        print(f'{len(srcs)} sources in {dosbatch.backend()}, {jobs or dosbatch.sessions()} sessions at once, {batch} to a session')
    # a source that fails in a batch is built once more alone (a session can die part way)
    res = dosbatch.compile_many(cfg, [os.path.abspath(s) for s in srcs], batch, jobs)
    bad = 0
    for stem, (ok, msgs) in sorted(res.items()):
        for m in msgs: print(f'{stem}: {m}')
        if not ok: bad += 1; print(f'{stem}: FAILED')
    print(f'-- {len(res) - bad}/{len(res)} built into {cfg.build}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
