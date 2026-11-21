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

Pool: one worker process per job (fork), each holding its own runner and the scorer (Target),
so compile and score both run in the workers; the parent only mutates and decides."""
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

    def compile(self, src, obj, opts):
        cmd = self.wrap + [os.path.join(self.home, 'bin', 'armcc'), '-c'] + opts + [src, '-o', obj]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, env=self.env, timeout=TIMEOUT)
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
        self.prefix = os.path.join(work, 'wineprefix')
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
        for attempt in range(3):
            try:
                r = subprocess.run(cmd, capture_output=True, text=True, env=self.env, timeout=TIMEOUT)
            except subprocess.TimeoutExpired:
                continue
            if r.returncode in (0, 1): break
        else:
            return None, 'timeout or crash'
        if r.returncode != 0 or not os.path.exists(obj): return None, r.stdout + r.stderr
        with open(obj, 'rb') as f: data = f.read()
        os.remove(obj)
        return data, r.stdout + r.stderr


def make_runner(kind, cfg, args):
    if kind == 'native':
        return Native(args.get('armcc_home'), args.get('mappings'), args.get('exec'))
    if kind == 'wine':
        home = cfg.home if os.path.isabs(cfg.home) else os.path.join(cfg.root, cfg.home)
        return Wine(home)
    raise SystemExit(f'unknown runner {kind}')


# ---------------------------------------------------------------- the pool

_W = {}


def _worker_init(runner, target, opts, stem, work):
    os.makedirs(work, exist_ok=True)
    wd = os.path.join(work, f'w{os.getpid()}')
    os.makedirs(wd, exist_ok=True)
    runner.start(wd)
    _W.update(runner=runner, target=target, opts=opts, src=os.path.join(wd, stem + '.c'),
              obj=os.path.join(wd, stem + '.o'), wd=wd)
    import atexit
    atexit.register(_worker_exit)


def _worker_exit():
    try:
        _W['runner'].stop()
        shutil.rmtree(_W['wd'], ignore_errors=True)
    except Exception:
        pass


def _worker_job(job):
    """job (key, text) -> (key, Result or None, object hash, seconds compile, seconds score, log)."""
    key, text = job
    with open(_W['src'], 'w', encoding='latin1') as f: f.write(text)
    t0 = time.perf_counter()
    raw, log = _W['runner'].compile(_W['src'], _W['obj'], _W['opts'])
    t1 = time.perf_counter()
    if raw is None:
        return key, None, None, t1 - t0, 0.0, log[-1500:]
    r = _W['target'].score(raw)
    r.code = b''
    t2 = time.perf_counter()
    return key, r, hashlib.blake2b(raw[0x34:], digest_size=12).hexdigest(), t1 - t0, t2 - t1, ''


class Pool:
    def __init__(self, runner, target, opts, stem, jobs, work):
        ctx = mp.get_context('fork')
        self.work = work
        self.pool = ctx.Pool(jobs, _worker_init, (runner, target, opts, stem, work))

    def imap(self, jobs):
        return self.pool.imap_unordered(_worker_job, jobs)

    def map(self, jobs):
        return self.pool.map(_worker_job, jobs)

    def close(self):
        self.pool.close(); self.pool.join()
        shutil.rmtree(self.work, ignore_errors=True)


def compile_once(runner, cfg, src_path, text, work=None):
    """(object bytes or None, log) of one compile in this process."""
    work = work or tempfile.mkdtemp(prefix='pmt-', dir=ram_dir())
    os.makedirs(work, exist_ok=True)
    stem = os.path.splitext(os.path.basename(src_path))[0]
    p = os.path.join(work, stem + '.c')
    with open(p, 'w', encoding='latin1') as f: f.write(text)
    runner.start(work)
    try:
        return runner.compile(p, os.path.join(work, stem + '.o'), source_opts(cfg, src_path, text))
    finally:
        os.remove(p)
