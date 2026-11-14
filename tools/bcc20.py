"""Borland C++ 2.0's compiler in-process: a ctypes binding of the bcc20 port's library
(borland-reborn's bcc20-port, `make shlib`; its include/bcc20.h is the C interface).

    import bcc20
    bcc = bcc20.Compiler()                      # BCC20_LIB: the shared library's path
    result = bcc.compile(["-c", "-ml", "-O", r"-IC:\\INCLUDE", "T.C"],
                         files={"T.C": source_bytes}, root="/path/to/BC20")
    result.status, result.console, result.outputs["T.OBJ"]

Each call is one BCC command line run from memory to memory: the files given come from
memory, other files from root (standing for C:\\), and every file BCC writes comes back in
result.outputs.  Compiles never share state.  explain=True also returns the compiler's
explanation of its choices (result.explain, one line per event).

Pool runs compiles on several bcc20run --serve processes at once, capped (workers, default
one fewer than the cores) and restarted when one crashes or hangs, and on POSIX hosts can
pause every server in one compile and finish variants of the rest on copies of it
(snapshot, resume_map, release); see its docstring.
"""
import ctypes
import os
from pathlib import Path


class _File(ctypes.Structure):
    _fields_ = [("name", ctypes.c_char_p), ("data", ctypes.c_void_p), ("size", ctypes.c_size_t)]


_EXPLAIN = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_char_p)


class _Job(ctypes.Structure):
    _fields_ = [("argc", ctypes.c_int), ("argv", ctypes.POINTER(ctypes.c_char_p)),
                ("files", ctypes.POINTER(_File)), ("n_files", ctypes.c_int),
                ("root", ctypes.c_char_p), ("time", ctypes.c_longlong),
                ("stack_limit", ctypes.c_int),
                ("explain", _EXPLAIN), ("explain_context", ctypes.c_void_p)]


class _Result(ctypes.Structure):
    _fields_ = [("status", ctypes.c_int), ("console", ctypes.c_void_p),
                ("console_size", ctypes.c_size_t), ("outputs", ctypes.POINTER(_File)),
                ("n_outputs", ctypes.c_int)]


class Result:
    def __init__(self, status, console, outputs, explain):
        self.status, self.console, self.outputs, self.explain = status, console, outputs, explain


def default_library():
    if os.environ.get("BCC20_LIB"):
        return Path(os.environ["BCC20_LIB"])
    port = Path(__file__).resolve().parents[2] / "bcc20-port" / "build"
    for name in ("libbcc20.dylib", "libbcc20.so"):
        if (port / name).exists():
            return port / name
    raise SystemExit("bcc20: set BCC20_LIB to the bcc20 port's shared library (make shlib)")


class Compiler:
    def __init__(self, library=None):
        self.lib = ctypes.CDLL(str(library or default_library()))
        self.lib.bcc20_compile.argtypes = [ctypes.POINTER(_Job), ctypes.POINTER(_Result)]
        self.lib.bcc20_compile.restype = ctypes.c_int
        self.lib.bcc20_free.argtypes = [ctypes.POINTER(_Result)]

    def compile(self, args, files=None, root=None, time=0, stack_limit=True, explain=False):
        files = files or {}
        keep = []                                   # buffers alive for the call
        argv = (ctypes.c_char_p * len(args))(*[a.encode("latin1") for a in args])
        table = (_File * max(1, len(files)))()
        for i, (name, data) in enumerate(files.items()):
            buf = ctypes.create_string_buffer(bytes(data), len(data))
            keep.append(buf)
            table[i] = _File(name.encode("latin1"), ctypes.cast(buf, ctypes.c_void_p), len(data))
        lines = []
        callback = _EXPLAIN(lambda context, line: lines.append(line.decode("latin1")))
        job = _Job(len(args), argv, table, len(files), root.encode() if root else None,
                   int(time), 1 if stack_limit else 0,
                   callback if explain else _EXPLAIN(), None)
        result = _Result()
        self.lib.bcc20_compile(ctypes.byref(job), ctypes.byref(result))
        console = ctypes.string_at(result.console, result.console_size).decode("latin1") if result.console else ""
        outputs = {result.outputs[i].name.decode("latin1"):
                   ctypes.string_at(result.outputs[i].data, result.outputs[i].size)
                   for i in range(result.n_outputs)}
        status = result.status
        self.lib.bcc20_free(ctypes.byref(result))
        return Result(status, console, outputs, lines)


# ---- a pool of compile servers (bcc20run --serve) ----

def default_program():
    """the bcc20 port's bcc20run: BCC20_RUN, else beside the shared library"""
    if os.environ.get("BCC20_RUN"):
        return Path(os.environ["BCC20_RUN"])
    for name in ("bcc20run", "bcc20run.exe"):
        p = default_library().parent / name
        if p.exists():
            return p
    raise SystemExit("bcc20: set BCC20_RUN to the bcc20 port's bcc20run (make lib)")


def default_workers():
    """how many compiles at once: BCC20_WORKERS, else one fewer than the cores (at least 1)"""
    if os.environ.get("BCC20_WORKERS"):
        return max(1, int(os.environ["BCC20_WORKERS"]))
    return max(1, (os.cpu_count() or 2) - 1)


class _Server:
    """one bcc20run --serve process"""

    def __init__(self, program, nice, setup):
        import subprocess
        kwargs = {}
        if nice and hasattr(os, "nice"):
            kwargs["preexec_fn"] = lambda: os.nice(nice)
        if os.name == "posix":                      # its snapshot copies die with it (kill)
            kwargs["start_new_session"] = True
        self.proc = subprocess.Popen([str(program), "--serve"], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, **kwargs)
        self.send(setup)

    def send(self, data):
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def answer(self):
        """the answer to RUN, as a Result; None if the server died"""
        out = self.proc.stdout
        status, console, outputs, explain = 0, "", {}, []
        while True:
            line = out.readline()
            if not line:
                return None
            if line.strip() == b"PAUSED":
                return "PAUSED"
            words = line.split()
            tag, nums = words[0], [int(w) for w in words[1:]]
            if tag == b"STATUS":
                status = nums[0]
            elif tag == b"CONSOLE":
                console = out.read(nums[0]).decode("latin1")
            elif tag == b"OUTPUT":
                name = out.read(nums[0]).decode("latin1")
                outputs[name] = out.read(nums[1])
            elif tag == b"EXPLAIN":
                explain = out.read(nums[0]).decode("latin1").splitlines()
            elif tag == b"DONE":
                return Result(status, console, outputs, explain)
            elif tag == b"ERROR":
                raise RuntimeError("bcc20run: " + out.read(nums[0]).decode("latin1"))

    def kill(self):
        """the server and any snapshot copy it is running"""
        try:
            if os.name == "posix":
                import signal
                os.killpg(self.proc.pid, signal.SIGKILL)
            else:
                self.proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass

    def close(self):
        try:
            self.send(b"QUIT\n")
            self.proc.stdin.close()
            self.proc.wait(timeout=5)
        except Exception:
            self.proc.kill()


def _record(tag, *parts):
    """one message: TAG with the parts' lengths, then the parts"""
    parts = [p.encode("latin1") if isinstance(p, str) else bytes(p) for p in parts]
    return (tag + "".join(f" {len(p)}" for p in parts) + "\n").encode() + b"".join(parts)


class Pool:
    """Compiles on `workers` bcc20run --serve processes at once (default_workers(): no more
    than that many cores are ever busy), each kept running between jobs.

        with bcc20.Pool(workers=4, root="/path/to/BC20") as pool:
            pool.keep({"PROJ.H": header_bytes})            # sent once, held by every server
            results = pool.map([(["-c", "-ml", "A.C"], {"A.C": a}), ...])
            r = pool.compile(["-c", "-ml", "B.C"], {"B.C": b})

    A compile that takes longer than `timeout` seconds, or crashes its server, comes back
    with status -2 (and says so in its console text); the server is started again.  `nice`
    lowers the servers' priority (POSIX), so the machine stays responsive.  Thread-safe:
    map() and compile() may be called from several threads.
    """

    def __init__(self, workers=None, program=None, root=None, time=0, stack_limit=True,
                 nice=0, timeout=60):
        import queue
        import threading
        self.program = program or default_program()
        self.workers = max(1, workers or default_workers())
        self.nice, self.timeout = nice, timeout
        self.setup = _record("ROOT", root or "") + f"TIME {int(time)}\nSTACK {1 if stack_limit else 0}\n".encode()
        self.kept = b""
        self.snap = None                            # the job every server is paused in
        self.lock = threading.Lock()
        self.free = queue.Queue()
        self.servers = [_Server(self.program, nice, self.setup) for _ in range(self.workers)]
        for s in self.servers:
            self.free.put(s)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def keep(self, files):
        """files every later job holds, sent once to each server"""
        data = b"".join(_record("KEEP", name, content) for name, content in files.items())
        with self.lock:
            self.kept += data
        taken = [self.free.get() for _ in range(self.workers)]
        for s in taken:
            s.send(data)
            self.free.put(s)

    def _exchange(self, server, data):
        """sends data to server and reads its answer within the timeout; (answer, None), or
        (None, why) when it timed out or died, the server then killed"""
        import threading
        killed = threading.Event()
        timer = threading.Timer(self.timeout, lambda: (killed.set(), server.kill()))
        timer.start()
        try:
            server.send(data)
            answer = server.answer()
        except (BrokenPipeError, OSError, ValueError, RuntimeError):
            answer = None
        finally:
            timer.cancel()
        if answer is None:
            server.kill()
            return None, "timed out" if killed.is_set() else "crashed"
        return answer, None

    def _replace(self, server):
        """a fresh server in server's place, with the settings, kept files and snapshot"""
        server.kill()
        fresh = _Server(self.program, self.nice, self.setup + self.kept)
        with self.lock:
            self.servers[self.servers.index(server)] = fresh
        if self.snap is not None:
            answer, _ = self._exchange(fresh, self.snap)
            if answer != "PAUSED":
                raise RuntimeError("bcc20: a restarted server could not take the snapshot again")
        return fresh

    def compile(self, args, files=None, explain=False):
        if self.snap is not None:
            raise RuntimeError("bcc20: release() the snapshot before compiling other jobs")
        server = self.free.get()
        job = (f"EXPLAIN {1 if explain else 0}\n".encode()
               + b"".join(_record("ARG", a) for a in args)
               + b"".join(_record("FILE", n, d) for n, d in (files or {}).items()) + b"RUN\n")
        result, why = self._exchange(server, job)
        if result is None:
            server = self._replace(server)
            result = Result(-2, f"bcc20: the compile {why}\n", {}, [])
        self.free.put(server)
        return result

    # ---- snapshots (POSIX hosts): one paused compile per server, a copy per variant ----

    def snapshot(self, args, files, pause="VARIANT.C"):
        """Starts args with files on every server and stops each where BCC first opens the
        file named pause; returns False (and nothing is paused) if the host cannot, or if
        the compile never opened that file.  resume() then finishes a copy of the paused
        compile for each variant: the paused file's text, and any other files."""
        job = (_record("PAUSE", pause) + b"EXPLAIN 0\n" + b"".join(_record("ARG", a) for a in args)
               + b"".join(_record("FILE", n, d) for n, d in files.items()) + b"RUN\n")
        taken = [self.free.get() for _ in range(self.workers)]
        answers = [self._exchange(s, job)[0] for s in taken]
        ok = all(a == "PAUSED" for a in answers)
        if not ok:                                  # put every server back as it was
            for i, (server, answer) in enumerate(zip(taken, answers)):
                if answer == "PAUSED" and self._exchange(server, b"RELEASE\n")[0] is None:
                    answer = None
                if answer is None:
                    taken[i] = self._replace(server)
                else:
                    server.send(b"PAUSE 0\n")
        else:
            self.snap = job
        for s in taken:
            self.free.put(s)
        return ok

    def resume(self, files):
        """one variant on a copy of the paused compile (files: the paused file and others)"""
        server = self.free.get()
        data = b"".join(_record("FILE", n, d) for n, d in files.items()) + b"RESUME\n"
        result, why = self._exchange(server, data)
        if result is None or result == "PAUSED":
            server = self._replace(server)
            result = Result(-2, f"bcc20: the compile {why or 'failed'}\n", {}, [])
        self.free.put(server)
        return result

    def resume_map(self, variants):
        """variants (dicts of files) at once on the paused copies; results in order"""
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(self.workers) as ex:
            return list(ex.map(self.resume, variants))

    def release(self):
        """ends the paused compiles; the servers take ordinary jobs again"""
        if self.snap is None:
            return
        self.snap = None
        taken = [self.free.get() for _ in range(self.workers)]
        for i, s in enumerate(taken):
            answer, _ = self._exchange(s, b"RELEASE\n")
            if answer is None:
                taken[i] = self._replace(s)
            else:
                s.send(b"PAUSE 0\n")
        for s in taken:
            self.free.put(s)

    def map(self, jobs, explain=False):
        """compiles (args, files) jobs at once; results in the jobs' order"""
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(self.workers) as ex:
            return list(ex.map(lambda j: self.compile(j[0], j[1] if len(j) > 1 else None, explain), jobs))

    def close(self):
        try:
            self.release()
        except Exception:
            pass
        for s in self.servers:
            s.close()
        self.servers = []
