r"""Test vectors: tables of a routine's inputs and outputs computed by running the original's
own bytes, for a reimplementation to be checked against (docs/port.md, "Test vectors").

    python3 tools/vectors.py [--config PATH] [--targets FILE] [--glue FILE] [--out DIR]
                             [--only NAME[,NAME...]] [--check] [--no-port] [--list]

For each target ([vectors] targets, a Python file of V() definitions) it makes a few hundred
cases from a seeded generator (edge values first, then random ones; the same seed always gives
the same cases), runs the routine's bytes from the user's EXE in Unicorn on each (as
tools/fuzzasm.py does: the load module at the port's load segment, relocations applied), and
writes one CSV per target to the output directory ([vectors] out, default vectors/): a header
row of the input and output column names, then one row per case, decimal integers. The EXE is
the authority. The port's C for the same routine is run on the same inputs too, through the
project's glue ([vectors] host_glue, linked into tools/fuzzhost.c's host; fuzzhost.c names what
it defines), and every case where the two differ (as 32-bit words: the glue's outputs are C
ints, the EXE's as the target reads them) is reported and makes the run exit 1, so the
vectors are known to be what the matched sources compute as well. --no-port skips that (no port
build needed). --check writes nothing and exits 1 if a file would change: regeneration is
byte-identical or it is a failure.

The routines are called the way the program's C calls a far function: DS and SS the data group
([binary] dgroup_para), SP at STACK_TOP in it, the arguments on the stack in C order, and the
return address far (RET_CS:RET_IP, linear 100h, never code). Memory a case needs (a global, an
object a pointer argument points at) is laid over the image first, so a case's output depends
only on its own inputs and on the EXE. Only routines that are resident (not in an overlay) can
run this way: an overlay's code is not where its segment's stub says, and a call into one stops
at INT 3Fh, which counts as an error here.

A target file imports this module (import vectors) for V, Call, sym, image_word and the
constants, and defines VECTORS, a list of V. Each V names the routine (a symbols.tsv name or a
(segment, offset) pair, an EXE paragraph), its input and output columns, a generator of input
rows (rng, case number -> dict), how a row becomes a call (-> Call) and how the call's result
becomes the output row (Res, inputs -> dict). Its port kind is the call kind the glue's
fuzz_vector() knows; the inputs go to it as 32-bit words in column order, and it writes the
outputs as 32-bit words in column order at the block's offset 100h.

Needs Unicorn (tools/requirements.txt) and, unless --no-port, the port's objects
(tools/portbuild.py).
"""
import os, sys, csv, io, zlib, random, struct, hashlib, argparse

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
sys.modules.setdefault('vectors', sys.modules[__name__])
import fuzzasm, portcfg
from fuzzasm import CFG, ARGV

STACK_TOP = 0xF000                     # SP in the data group when a routine is called
LOCALS = 0xF100                        # the data group's offsets Call.local hands out from
LOCALS_END = 0xF800
BLOCK = fuzzasm.SCRATCH                # the port side's block: inputs at 0, outputs at 100h
OUT_OFF = 0x100
DEFAULT_CASES = 300


def dgroup():
    """The data group's segment as loaded (EXE paragraph + the load segment)."""
    if CFG.dgroup_para is None: sys.exit('vectors.py: [binary] dgroup_para is not set')
    return CFG.dgroup_para + fuzzasm.LOAD


_syms = None


def sym(name):
    """A symbols.tsv address: ('DS', offset) for data, (segment, offset) for code, the segment
    an EXE paragraph."""
    global _syms
    if _syms is None: _syms = CFG.load_symbols()
    if name not in _syms: sys.exit(f'vectors.py: {name} is not in {CFG.symbols}')
    a = _syms[name][0]
    s, o = a.split(':')
    return ('DS' if s == 'DS' else int(s, 16)), int(o, 16)


_image = None


def image():
    global _image
    if _image is None: _image = fuzzasm.load_image()
    return _image


def image_word(seg, off):
    """The word at an EXE paragraph and offset in the loaded image (relocations applied)."""
    lin = (seg + fuzzasm.LOAD) * 16 + off
    return struct.unpack_from('<H', image(), lin)[0]


def s16(v): v &= 0xFFFF; return v - 0x10000 if v & 0x8000 else v
def s8(v): v &= 0xFF; return v - 0x100 if v & 0x80 else v
def s32(v): v &= 0xFFFFFFFF; return v - 0x100000000 if v & 0x80000000 else v


class Call:
    """One call: the words pushed (in C order, the first argument lowest), memory laid over the
    image (linear address, bytes), and the data group's locals handed out by local()."""

    def __init__(self):
        self.args = []
        self.mem = []
        self._next = LOCALS

    def word(self, v): self.args.append(v & 0xFFFF); return self

    def dword(self, v): self.word(v); self.word((v & 0xFFFFFFFF) >> 16); return self

    def ds(self, off, data):
        """Bytes at a data group offset (a global)."""
        self.mem.append((dgroup() * 16 + off, bytes(data))); return self

    def local(self, data, align=2):
        """Bytes in the data group's locals area; returns their offset."""
        off = (self._next + align - 1) & -align
        if off + len(data) > LOCALS_END: sys.exit('vectors.py: Call.local: out of room')
        self._next = off + len(data)
        self.ds(off, data)
        return off

    def near_ptr(self, data):
        """A near pointer argument to bytes in the data group; returns their offset."""
        off = self.local(data); self.word(off); return off

    def far_ptr(self, data):
        """A far pointer argument (offset, then segment) to bytes in the data group; returns
        their offset."""
        off = self.local(data); self.word(off); self.word(dgroup()); return off


class Res:
    """What a call left: status ('ret', or what stopped it), the registers, and the memory."""

    def __init__(self, status, regs, mu):
        self.status, self.regs, self._mu = status, regs, mu

    @property
    def ax(self): return self.regs['eax'] & 0xFFFF

    @property
    def dx(self): return self.regs['edx'] & 0xFFFF

    def ds_bytes(self, off, n): return bytes(self._mu.mem_read(dgroup() * 16 + off, n))

    def ds_byte(self, off): return self.ds_bytes(off, 1)[0]

    def ds_word(self, off): return struct.unpack('<H', self.ds_bytes(off, 2))[0]

    def ds_dword(self, off): return struct.unpack('<I', self.ds_bytes(off, 4))[0]


class V:
    """A vector target: name, the routine (symbol name or (segment, offset)), the input and
    output column names, gen(rng, k) -> inputs, call(inputs) -> Call, result(Res, inputs) ->
    outputs, the port glue's kind (None: no port side), the number of cases, and a sentence of
    what it computes (for --list)."""

    def __init__(self, name, fn, inputs, outputs, gen, call, result, port=None, cases=DEFAULT_CASES, what=''):
        self.name, self.fn, self.inputs, self.outputs = name, fn, list(inputs), list(outputs)
        self.gen, self.call, self.result, self.port = gen, call, result, port
        self.cases, self.what = cases, what

    def address(self):
        if isinstance(self.fn, tuple): return self.fn
        s, o = sym(self.fn)
        if s == 'DS': sys.exit(f'vectors.py: {self.fn} is data, not a routine')
        return s, o


def run_dos(dos, t, inp):
    """Runs one case in Unicorn: (outputs dict, status)."""
    seg, off = t.address()
    c = t.call(inp)
    dg = dgroup()
    regs = {k: 0 for k in fuzzasm.REGS}
    regs.update(ds=dg, es=dg, ss=dg, fs=dg, gs=dg, esp=STACK_TOP, flags=0)
    mem = list(c.mem)
    if c.args: mem.append((dg * 16 + STACK_TOP, struct.pack(f'<{len(c.args)}H', *c.args)))
    status, out, _ = dos.run(regs, mem, 'far', seg, off)
    if status != 'ret': return None, status
    return t.result(Res(status, out, dos.mu), inp), status


def run_port(port, t, inp):
    """Runs one case in the port through the glue: (outputs dict, status)."""
    blk = struct.pack(f'<{len(t.inputs)}i', *[s32(inp[k]) for k in t.inputs])
    blk = blk.ljust(OUT_OFF, b'\0') + bytes(4 * len(t.outputs))
    regs = {k: 0 for k in fuzzasm.REGS}
    regs.update(ds=BLOCK, es=BLOCK, ss=fuzzasm.STACK, fs=BLOCK, gs=BLOCK, esp=0xFF00, flags=0)
    status, _, mem = port.run(regs, [(BLOCK * 16, blk)], t.port, BLOCK, 0)
    if status != 'ret': return None, status
    raw = bytes(mem.get(BLOCK * 16 + OUT_OFF + i, 0) for i in range(4 * len(t.outputs)))
    vals = struct.unpack(f'<{len(t.outputs)}i', raw)
    return dict(zip(t.outputs, vals)), status


def table(t, rows):
    f = io.StringIO()
    w = csv.writer(f, lineterminator='\n')
    w.writerow(t.inputs + t.outputs)
    for inp, out in rows: w.writerow([inp[k] for k in t.inputs] + [out[k] for k in t.outputs])
    return f.getvalue()


def main(argv):
    ap = argparse.ArgumentParser(description='Test vectors from the original routines.')
    ap.add_argument('--targets'); ap.add_argument('--glue'); ap.add_argument('--out')
    ap.add_argument('--only'); ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--check', action='store_true'); ap.add_argument('--no-port', action='store_true')
    ap.add_argument('--list', action='store_true'); ap.add_argument('-v', action='store_true')
    a = ap.parse_args(argv)
    sys.stdout.reconfigure(line_buffering=True)
    VC = portcfg.vectors(CFG)
    targets_file = os.path.abspath(a.targets) if a.targets else VC.targets
    glue = os.path.abspath(a.glue) if a.glue else VC.host_glue
    outdir = os.path.abspath(a.out) if a.out else VC.out
    if not targets_file: sys.exit('vectors.py: no targets ([vectors] targets or --targets)')
    m = portcfg.load_module(targets_file, 'vector_targets')
    targets = [t for t in m.VECTORS if not a.only or t.name in a.only.split(',')]
    if a.list:
        for t in targets:
            s, o = t.address()
            print(f'{t.name:24s} {s:04X}:{o:04X}  {t.port or "-":20s} {t.what}')
        return 0
    port = None
    if not a.no_port and any(t.port for t in targets):
        if not glue: sys.exit('vectors.py: no port glue ([vectors] host_glue or --glue); --no-port to skip the port')
        port = fuzzasm.Port(fuzzasm.build_host(vectors=glue))
        fuzzasm.Port.image = image()
    dos = fuzzasm.Dos(image())
    os.makedirs(outdir, exist_ok=True) if not a.check else None
    bad = 0; stale = []
    exe_sha = hashlib.sha256(open(CFG.exe_path, 'rb').read()).hexdigest()
    print(f'vectors: {len(targets)} routines, seed {a.seed}, {os.path.basename(CFG.exe_path)} sha256 {exe_sha[:16]}'
          + ('' if port else ', no port check'))
    for t in targets:
        rng = random.Random(a.seed ^ zlib.crc32(t.name.encode()))
        rows = []; diffs = 0; first = None; ok_port = 0
        for k in range(t.cases):
            inp = t.gen(rng, k)
            out, st = run_dos(dos, t, inp)
            if out is None: sys.exit(f'vectors.py: {t.name} case {k} {inp}: the EXE did not return ({st})')
            rows.append((inp, out))
            if port and t.port:
                pout, pst = run_port(port, t, inp)
                if pout is None or any((pout[k] - out[k]) & 0xFFFFFFFF for k in t.outputs):
                    diffs += 1
                    if first is None: first = f'case {k} {inp}: EXE {out}, port {pout if pout else pst}'
                    if a.v: print(f'  {t.name} case {k} {inp}: EXE {out}, port {pout if pout else pst}')
                else: ok_port += 1
        text = table(t, rows)
        path = os.path.join(outdir, t.name + '.csv')
        old = open(path).read() if os.path.exists(path) else None
        if a.check:
            if old != text: stale.append(path)
        elif old != text:
            with open(path, 'w', newline='') as f: f.write(text)
        bad += diffs
        pc = '' if not (port and t.port) else (f', port agrees on all' if not diffs else f', port DIFFERS in {diffs}; first {first}')
        print(f'{t.name:24s} {len(rows)} cases{pc}{"" if a.check else ("  (written)" if old != text else "  (unchanged)")}')
    if port: port.close()
    if stale:
        print('vectors: out of date: ' + ', '.join(stale)); return 1
    print(f'vectors: {"the port differs in " + str(bad) + " cases" if bad else "done"}')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(ARGV))
