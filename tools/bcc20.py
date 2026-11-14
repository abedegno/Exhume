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
