"""Compile runners and the worker pool under armpermute2.py.

A runner compiles one C text with armcc and returns the object's bytes. Runners:

  native  a Linux armcc (ARM Compiler 5 for Linux), run directly: the home (holding bin/armcc)
          from --armcc-home or EXHUME_ARMCC_LINUX, with ARM_TOOL_VARIANT=mdk_lite and
          ARM_PRODUCT_PATH set to an MDK mappings folder (--mappings or EXHUME_ARMCC_MAPPINGS:
          <MDK>/ARM/sw/mappings). EXHUME_ARMCC_EXEC may name a command to run it through
          (for example `docker exec NAME`, for an x86 container on another host); paths are
          passed unchanged, so the container must mount them at the same place.
  wine    the project's MDK armcc.exe under host Wine, one Wine prefix per worker with a
          persistent wineserver (as armvariant.py does).

Work files go in a RAM-backed directory when there is one (/dev/shm), one folder per worker.
The source's own directory is added to the include path, so the variant, written elsewhere,
still finds its quoted includes. One compiler process per variant: measured natively, process
start is 7 ms against 140-250 ms of code generation, and batching many variants into one call
gains nothing.

Pool: one worker process per job (fork), each holding its own runner, the scorer (Target) and
armpermute2's generator (its Gen: the rules, the parsed bases, the shared sets), so a variant
is made, compiled and scored in a worker (_worker_gen); the parent only hands out jobs and
decides. _worker_job compiles and scores a text the caller made.

The compiler's messages go to a file, not a pipe: under Wine a background process of the
prefix (services.exe) can inherit a pipe and hold it open after armcc has ended, and the
caller then waits for the time limit."""
import hashlib, multiprocessing as mp, os, re, shlex, shutil, subprocess, sys, tempfile, time
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path: sys.path.insert(0, HERE)

TIMEOUT = 120


def ram_dir():
    for d in ('/dev/shm',):
        if os.path.isdir(d) and os.access(d, os.W_OK): return d
    return tempfile.gettempdir()


def source_opts(cfg, src_path, text):
    """The source's compile options (its /* opts: */ line, else the project's), as a list, with
    {root} filled and the source's directory added to the include path."""
    m = re.search(r'/\*\s*opts:\s*([^*]+?)\s*\*/', text[:4000])
    opts = m.group(1) if m else cfg.c_opts
    opts = opts.replace('{root}', os.path.realpath(cfg.root))
    return shlex.split(opts) + ['-I' + os.path.dirname(os.path.abspath(src_path))]


class Native:
    name = 'native'

    def __init__(self, home=None, mappings=None, wrap=None):
        self.home = home or os.environ.get('EXHUME_ARMCC_LINUX')
        self.mappings = mappings or os.environ.get('EXHUME_ARMCC_MAPPINGS')
        if not self.home or not os.path.isfile(os.path.join(self.home, 'bin', 'armcc')):
            raise SystemExit('native runner: set --armcc-home or EXHUME_ARMCC_LINUX to a Linux armcc home (bin/armcc)')
        if not self.mappings:
            raise SystemExit('native runner: set --mappings or EXHUME_ARMCC_MAPPINGS to <MDK>/ARM/sw/mappings')
        self.wrap = shlex.split(wrap if wrap is not None else os.environ.get('EXHUME_ARMCC_EXEC', ''))
        self.env = dict(os.environ, ARM_TOOL_VARIANT='mdk_lite', ARM_PRODUCT_PATH=self.mappings)
        if self.wrap and self.wrap[:2] == ['docker', 'exec']:
            self.wrap = self.wrap[:2] + ['-e', 'ARM_TOOL_VARIANT=mdk_lite', '-e', 'ARM_PRODUCT_PATH=' + self.mappings] + self.wrap[2:]

    def start(self, work):
        pass

    def stop(self):
        pass

    def compile(self, src, obj, opts, env=None):
        """env: more environment variables for this compile (the allocator trace's, permtrace.py)."""
        cmd = self.wrap + [os.path.join(self.home, 'bin', 'armcc'), '-c'] + opts + [src, '-o', obj]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, env=dict(self.env, **env) if env else self.env, timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            return None, 'timeout'
        if r.returncode != 0 or not os.path.exists(obj): return None, r.stdout + r.stderr
        with open(obj, 'rb') as f: data = f.read()
        os.remove(obj)
        return data, r.stdout + r.stderr


class Wine:
    name = 'wine'

    def __init__(self, home, wine=None):
        self.home = os.path.realpath(home)
        self.exe = os.path.join(self.home, 'ARM', 'ARMCC', 'bin', 'armcc.exe')
        if not os.path.isfile(self.exe): raise SystemExit(f'wine runner: no {self.exe}')
        self.wine = wine or os.environ.get('WINE', 'wine')
        self.prefix = None

    def start(self, work):
        prefix = os.path.join(work, 'wineprefix')
        if prefix == self.prefix and os.path.isdir(prefix): return        # already running there
        self.prefix = prefix
        os.makedirs(self.prefix, exist_ok=True)
        self.env = dict(os.environ, ARM_TOOL_VARIANT='mdk_lite', WINEDEBUG='-all', WINEPREFIX=self.prefix,
                        WINEDLLOVERRIDES='mscoree,mshtml=',
                        ARM_PRODUCT_PATH='Z:' + self.home.replace('/', '\\') + '\\ARM\\sw\\mappings')
        q = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, env=self.env)
        server = os.path.join(os.path.dirname(shutil.which(self.wine) or self.wine), 'wineserver')
        subprocess.run([server if os.path.exists(server) else 'wineserver', '-p'], start_new_session=True, **q)
        subprocess.run([self.wine, 'cmd', '/c', 'exit'], timeout=300, **q)

    def stop(self):
        """Stop this worker's own wineserver (never anyone else's) and delete its prefix."""
        if not self.prefix: return
        server = os.path.join(os.path.dirname(shutil.which(self.wine) or self.wine), 'wineserver')
        subprocess.run([server if os.path.exists(server) else 'wineserver', '-k'], env=self.env,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        shutil.rmtree(self.prefix, ignore_errors=True)

    def compile(self, src, obj, opts):
        cmd = [self.wine, self.exe, '-c'] + opts + [src, '-o', obj]
        logp = obj + '.log'
        for attempt in range(3):
            try:
                with open(logp, 'w') as lf:
                    r = subprocess.run(cmd, stdout=lf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                       env=self.env, timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                continue
            if r.returncode in (0, 1): break
        else:
            return None, 'timeout or crash'
        with open(logp, errors='replace') as lf: log = lf.read()
        os.remove(logp)
        if r.returncode != 0 or not os.path.exists(obj): return None, log
        with open(obj, 'rb') as f: data = f.read()
        os.remove(obj)
        return data, log


def tree_env(root):
    """{KEY: VALUE} of the optional permuter.env in a tree's root (beside its exhume.toml): lines KEY=VALUE (# for
    comments, `export ` allowed). It names the compiler the tree is built with
    (EXHUME_ARMCC_LINUX, EXHUME_ARMCC_MAPPINGS, EXHUME_ARMCC_EXEC), so that one run can score
    against trees built with different compilers."""
    out = {}
    p = os.path.join(root, 'permuter.env')
    if os.path.isfile(p):
        with open(p) as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith('export '): ln = ln[7:].strip()
                if ln and not ln.startswith('#') and '=' in ln:
                    k, v = ln.split('=', 1)
                    out[k.strip()] = v.strip().strip('"\'')
    return out


def make_runner(kind, cfg, args):
    if kind == 'native':
        return Native(args.get('armcc_home'), args.get('mappings'), args.get('exec'))
    if kind == 'wine':
        home = cfg.home if os.path.isabs(cfg.home) else os.path.join(cfg.root, cfg.home)
        return Wine(home)
    raise SystemExit(f'unknown runner {kind}')


# ---------------------------------------------------------------- the pool

_W = {}


def _worker_init(runner, target, opts, stem, work, gen=None, also=()):
    os.makedirs(work, exist_ok=True)
    wd = os.path.join(work, f'w{os.getpid()}')
    os.makedirs(wd, exist_ok=True)
    runner.start(wd)
    _W.update(runner=runner, target=target, opts=opts, src=os.path.join(wd, stem + '.c'),
              obj=os.path.join(wd, stem + '.o'), wd=wd, gen=gen, also=[])
    for k, a in enumerate(also):                # the other targets of a run (--also): each its own compiler and scorer
        d = os.path.join(wd, f'also{k + 1}')
        os.makedirs(d, exist_ok=True)
        a['runner'].start(d)
        _W['also'].append(dict(runner=a['runner'], target=a['target'], opts=a['opts'], src=os.path.join(d, a['stem'] + '.c'),
                               obj=os.path.join(d, a['stem'] + '.o')))
    import atexit
    atexit.register(_worker_exit)


def _worker_exit():
    try:
        for a in _W.get('also', ()): a['runner'].stop()
        _W['runner'].stop()
        shutil.rmtree(_W['wd'], ignore_errors=True)
    except Exception:
        pass


def worker_compile(text, extra=(), env=None):
    """(object bytes or None, log, seconds) of one compile of text in this worker (env: more
    environment variables, native runner only)."""
    with open(_W['src'], 'w', encoding='latin1') as f: f.write(text)
    opts = _W['opts']
    if 'PMT_FILE_OPTS' in text:                 # a variant with its own /* opts: */ line (permrules.r_file_opts)
        m = re.search(r'/\*\s*opts:\s*([^*]+?)\s*\*/', text[:4000])
        if m and '{root}' not in m.group(1): opts = shlex.split(m.group(1)) + opts[-1:]
    t0 = time.perf_counter()
    if env: raw, log = _W['runner'].compile(_W['src'], _W['obj'], opts + list(extra), env=env)
    else: raw, log = _W['runner'].compile(_W['src'], _W['obj'], opts + list(extra))
    return raw, log, time.perf_counter() - t0


def worker_score(text):
    """(object bytes, Result or None, code hash, seconds compile, seconds score, log) of text
    compiled and scored in this worker. The hash is of the function's placed bytes and the
    changed neighbours (Result.chash), the same in every worker for the same code."""
    raw, log, tc = worker_compile(text)
    if raw is None:
        return None, None, None, tc, 0.0, log[-1500:]
    t1 = time.perf_counter()
    r = _W['target'].score(raw)
    r.code = b''
    r.chash        # (set before the code is dropped)
    return raw, r, r.chash or hashlib.blake2b(raw[0x34:], digest_size=12).hexdigest(), tc, time.perf_counter() - t1, ''


def worker_score_also(k, text):
    """(Result or None, object bytes, seconds compile, seconds score, log) of text compiled with the k-th other
    target's compiler and scored against that target's image."""
    a = _W['also'][k]
    with open(a['src'], 'w', encoding='latin1') as f: f.write(text)
    t0 = time.perf_counter()
    raw, log = a['runner'].compile(a['src'], a['obj'], a['opts'])
    t1 = time.perf_counter()
    if raw is None: return None, None, t1 - t0, 0.0, log[-1500:]
    r = a['target'].score(raw)
    r.code = b''
    return r, raw, t1 - t0, time.perf_counter() - t1, ''


def _worker_job(job):
    """job (key, text) -> (key, Result or None, object hash, seconds compile, seconds score, log)."""
    key, text = job
    return (key,) + worker_score(text)[1:]


def _worker_gen(job):
    """A job for the worker's generator (armpermute2.Gen.run): a dict with the job's 'id'."""
    try:
        return _W['gen'].run(job)
    except Exception as e:
        import traceback
        return {'id': job[1], 'err': f'{type(e).__name__}: {e}', 'trace': traceback.format_exc()[-1500:]}


class Pool:
    def __init__(self, runner, target, opts, stem, jobs, work, gen=None, also=()):
        ctx = mp.get_context('fork')
        self.work = work
        self.pool = ctx.Pool(jobs, _worker_init, (runner, target, opts, stem, work, gen, also))

    def imap(self, jobs):
        return self.pool.imap_unordered(_worker_job, jobs)

    def map(self, jobs):
        return self.pool.map(_worker_job, jobs)

    def close(self):
        self.pool.close(); self.pool.join()
        shutil.rmtree(self.work, ignore_errors=True)


def compile_once(runner, cfg, src_path, text, work=None, extra=(), env=None):
    """(object bytes or None, log) of one compile in this process (extra: more options; env:
    more environment variables, native runner only)."""
    work = work or tempfile.mkdtemp(prefix='pmt-', dir=ram_dir())
    os.makedirs(work, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src_path))[0]
    p = os.path.join(work, stem + '.c')
    with open(p, 'w', encoding='latin1') as f: f.write(text)
    runner.start(work)
    try:
        if env: return runner.compile(p, os.path.join(work, stem + '.o'), source_opts(cfg, src_path, text) + list(extra), env=env)
        return runner.compile(p, os.path.join(work, stem + '.o'), source_opts(cfg, src_path, text) + list(extra))
    finally:
        os.remove(p)
