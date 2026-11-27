#!/usr/bin/env python3
"""gosig.py: recover the result registers of Go functions from their callers and return paths.

    gosig.py BINARY REGEX       one line per function whose name matches REGEX:
                                name<TAB>classes, - for none, ? for unknown<TAB>call sites

A plain function's signature is not in a stripped binary's type information, and its argument
layout covers only its parameters. Under the register ABI (Go 1.17 and later, amd64) results
come back in RAX, RBX, RCX, RDI, RSI, R8, R9, R10, R11 and X0 to X14, in that order. For each
function two sets of these registers are worked out over the whole binary:

  * a lower bound, registers that must hold a result: read after a direct call of the function
    before being written, or passed as arguments to the next call (every other register is
    dead after a call); written before one of the function's own ret instructions and not
    read again (the compiler writes no dead registers); or passed straight through to the ret
    of a caller whose results include them;
  * an upper bound, registers that may hold a result: at every ret, a register still holding
    what was there at entry (and not a parameter's) or what a callee left there although it is
    not one of the callee's results would be garbage, so it is not a result. A tail jump gives
    the jumped-to function's results.

Methods whose type is in the binary's method tables have exactly known results, and these seed
both bounds; the bounds of the other functions are refined until nothing changes. A function's
results are given only when its two bounds agree, it has at least one direct call site, it
returns at all, no result is on the stack, and its results are not a mix of integer and
floating-point registers (their order cannot be told apart).

The parameter registers come from the code that spills them before calling runtime.morestack,
or, in a function without it, are bounded by the number of items in the argument layout and by
the registers it reads before writing them. Stack-assigned results lie between the
stack-assigned arguments and the spill slots of the argument area, so they show as a gap in the
argument layout, or as an argument size larger than the parameters; when the layout is cut
short, as spill slots that do not start at offset 0, unless every caller stores to the word
just below them (a caller leaves results uninitialized, so that word is an argument).

Some runtime routines need care: the GC write barriers keep every register but R11, and the
panic and throw routines never return although their code may end in a ret.

The answer is a list of classes, one per register in order: 'int' for an integer register and
'float' for a floating-point one. How registers group into strings, slices, interfaces or
structs, and the Go types, stay unknown.

As a module: results(binary, name) returns the list or None; evidence(binary, name) the bounds
and call-site count behind it; abi_classes(types) the register classes of a list of
gotypes.Type results; analysis(binary, known) a solution with other known results, for testing.
The whole binary is decoded once (with objdump, as gocmpx.py does) and the solution kept on the
Binary object.
"""
import bisect
import re
import struct
import sys
import zlib

import gobin
import gocmpx

INT_REGS = ('rax', 'rbx', 'rcx', 'rdi', 'rsi', 'r8', 'r9', 'r10', 'r11')
NINT, NFLOAT = 9, 15                    # X15 is the zero register, not a result register
ALL = (1 << (NINT + NFLOAT)) - 1
INTS = (1 << NINT) - 1
FUNCDATA_ARGINFO = 5


def _reg_table():
    t = {}
    legacy = {'rax': ('eax', 'ax', 'al', 'ah'), 'rbx': ('ebx', 'bx', 'bl', 'bh'),
              'rcx': ('ecx', 'cx', 'cl', 'ch'), 'rdi': ('edi', 'di', 'dil'),
              'rsi': ('esi', 'si', 'sil')}
    for i, r in enumerate(INT_REGS):
        for n in (r,) + legacy.get(r, (r + 'd', r + 'w', r + 'b')):
            t[n] = 1 << i
    for i in range(NFLOAT):
        for p in ('xmm', 'ymm', 'zmm'):
            t['%s%d' % (p, i)] = 1 << (NINT + i)
    return t


REGS = _reg_table()
WORD = re.compile(r'[a-z][a-z0-9]*')
COMMENT = re.compile(r'\s*(#.*|<[^>]*>)$')
SPILL = re.compile(r'\[rsp \+ (0x[0-9a-f]+|\d+)\]')
STORE = re.compile(r'^[a-z0-9]+\t(byte|word|dword|qword|xmmword|ymmword) ptr '
                   r'\[rsp(?: \+ (0x[0-9a-f]+|\d+))?\], ')
WIDTH = {'byte': 1, 'word': 2, 'dword': 4, 'qword': 8, 'xmmword': 16, 'ymmword': 32}
CONDS = 'o no b nb ae nae c nc e z ne nz be na a nbe s ns p pe np po l nge ge nl le ng g nle'

# How an instruction uses its first operand when that is a register: written only, or read and
# written. Its other operands, and the address registers of a memory operand, are read.
WRITE_ONLY = set('mov movzx movsx movsxd movabs lea movq movd movups movaps movdqu movdqa movupd '
                 'movapd movss movsd cvtsi2sd cvtsi2ss cvttsd2si cvttss2si cvtsd2ss cvtss2sd '
                 'cvtsd2si pop popcnt lzcnt tzcnt bsr bsf vmovdqu vmovdqa vmovq andn rorx shlx '
                 'shrx sarx bzhi bextr pmovmskb vpmovmskb sqrtsd sqrtss roundsd roundss'.split()) | \
    {'set' + c for c in CONDS.split()}
READ_WRITE = set('add sub and or xor adc sbb inc dec neg not shl shr sar rol ror rcl rcr bswap '
                 'bts btr btc addsd subsd mulsd divsd addss subss mulss divss '
                 'andps andpd orps orpd xorps xorpd pxor por pand pandn paddq paddd psubq '
                 'psllq psrlq pslld psrld shld shrd maxsd minsd'.split()) | \
    {'cmov' + c for c in CONDS.split()}
READ_ONLY = set('cmp test bt push ucomisd ucomiss comisd comiss nop lock prefetcht0 '
                'prefetchnta'.split())
ZEROING = set('xor xorps xorpd pxor sub sbb pcmpeqd pcmpeqb'.split())  # the same register twice
NARROW = set('al ah bl bh cl ch dil sil ax bx cx di si'.split()) | \
    {'r%d%s' % (n, s) for n in range(8, 12) for s in 'bw'}
LOW_LANE = set('cvtsi2sd cvtsi2ss cvtsd2ss cvtss2sd sqrtsd sqrtss roundsd roundss'.split())
SHIFTS = set('shl shr sar rol ror rcl rcr shld shrd'.split())
TERMINATORS = set('ud2 int3 hlt'.split())

# Assembly routines that compiled code calls with their own convention: they keep every
# register except the ones given (a GC write barrier returns its buffer in R11).
PRESERVING = re.compile(r'^runtime\.gcWriteBarrier\d*$')
PRESERVING_WRITES = 1 << INT_REGS.index('r11')

# Runtime routines that never return, although their code may end in a ret.
NORETURN = re.compile(r'^runtime\.(gopanic|throw|fatal\w*|panic(?!Check)\w*|goPanic\w*|goexit\w*|'
                      r'morestack\w*|abort|badmorestack\w*|sigpanic)$')

# Kinds of instruction in the compact form
NORMAL, UNKNOWN, CALL, ICALL, RET, JMP, XJMP, IJMP, JCC, STOP, ATOMIC = range(11)
# ATOMIC is an exchange with memory: the old value it writes to a register is often unused (an
# atomic store), so that write is no evidence of a result. Otherwise it is NORMAL.


def parse(text):
    """(mnemonic, [operand text]) of an instruction's text."""
    text = COMMENT.sub('', text)
    parts = text.split('\t', 1)
    ops = [o.strip() for o in parts[1].split(',')] if len(parts) > 1 and parts[1].strip() \
        else []
    return parts[0].strip(), ops


def mask_of(op):
    m = 0
    for w in WORD.findall(op):
        m |= REGS.get(w, 0)
    return m


def uses(m, ops):
    """(reads, writes, partial writes) of result registers by a non-branch instruction, as bit
    masks, or None when this module does not know the instruction. A partial write (to an 8 or
    16-bit register, or to the low lane of an XMM register by a scalar conversion or a
    register-to-register movss or movsd) keeps the rest of the register: it is in writes and in
    partial."""
    if m in ('nop', 'lock') or m.startswith('prefetch'):
        return 0, 0, 0
    if m in READ_ONLY:
        if m == 'test' and len(ops) == 2 and '[' in ops[0] and ops[1] == 'al':
            return mask_of(ops[0]), 0, 0    # a nil check: only the address is used
        r = 0
        for o in ops:
            r |= mask_of(o)
        return r, 0, 0
    if m in ('xchg', 'xadd', 'cmpxchg'):
        r = w = 0
        for o in ops:
            r |= mask_of(o)
            if '[' not in o:
                w |= mask_of(o)
        if m == 'cmpxchg':
            r, w = r | 1, w | 1
        return r, w, 0
    if m == 'imul' and len(ops) == 3 or m in WRITE_ONLY:
        rw = False
    elif m == 'imul' and len(ops) == 2 or m in READ_WRITE:
        rw = True
    else:
        return None
    if not ops:
        return None
    reads = 0
    for o in ops[1:]:
        reads |= mask_of(o)
    dst = mask_of(ops[0])
    if '[' in ops[0]:
        return reads | dst, 0, 0
    if m in ZEROING and len(ops) == 2 and ops[0] == ops[1]:
        return 0, dst, 0
    if rw:
        reads |= dst
    if m in SHIFTS and 'cl' in ops[1:]:
        reads |= REGS['rcx']
    partial = 0
    if ops[0] in NARROW or m in LOW_LANE or m in ('movss', 'movsd') and dst and \
            len(ops) == 2 and '[' not in ops[1]:
        partial = dst
    return reads, dst, partial


def _rel(raw):
    """The displacement of a direct call, jmp or jcc, else None."""
    if len(raw) == 5 and raw[0] in (0xE8, 0xE9):
        return struct.unpack_from('<i', raw, 1)[0]
    if len(raw) == 6 and raw[0] == 0x0F and 0x80 <= raw[1] <= 0x8F:
        return struct.unpack_from('<i', raw, 2)[0]
    if len(raw) == 2 and (raw[0] == 0xEB or 0x70 <= raw[0] <= 0x7F):
        return struct.unpack_from('<b', raw, 1)[0]
    return None


# ABI

def _value_classes(t):
    """The register classes of one value of gotypes.Type t, or None when it cannot be
    register-assigned (an array of more than one element)."""
    import gotypes as g
    k = t.kind
    if t.size == 0:
        return []
    if k in (g.FLOAT32, g.FLOAT64):
        return ['float']
    if k in (g.COMPLEX64, g.COMPLEX128):
        return ['float', 'float']
    if k in (g.STRING, g.INTERFACE):
        return ['int', 'int']
    if k == g.SLICE:
        return ['int'] * 3
    if k == g.STRUCT:
        out = []
        for f in t.fields:
            c = _value_classes(f.type)
            if c is None:
                return None
            out += c
        return out
    if k == g.ARRAY:
        if t.len == 0:
            return []
        return _value_classes(t.elem) if t.len == 1 else None
    return ['int']


def abi_classes(types):
    """The register classes of a result list per the register ABI, or None when a result
    goes on the stack."""
    out, ni, nf = [], 0, 0
    for t in types:
        if t is None:
            return None
        c = _value_classes(t)
        if c is None:
            return None
        i, f = c.count('int'), c.count('float')
        if ni + i > NINT or nf + f > NFLOAT:
            return None
        ni, nf = ni + i, nf + f
        out += c
    return out


def classes_mask(classes):
    ni, nf = classes.count('int'), classes.count('float')
    return ((1 << ni) - 1) | (((1 << nf) - 1) << NINT)


def known_methods(b, types=None):
    """{function name: result register mask} of every method whose type is in a method table
    and whose results are all in registers. types is the binary's gotypes.Types, if loaded."""
    import gotypes
    try:
        types = types or gotypes.Types(b)
    except ValueError:
        return {}
    by_entry = {f.entry: f for f in b.funcs()}
    out = {}
    for t in types.all():
        for m in t.methods:
            if m.type is None or m.text is None or m.text not in by_entry:
                continue
            c = abi_classes(m.type.outs)
            if c is not None:
                out.setdefault(by_entry[m.text].name, classes_mask(c))
    return out


# Arguments

def arg_info(b, f):
    """(number of leaf items in the argument layout, end of the parameters rounded to a word,
    whether there is a gap), or (None, None, True) when the layout is cut short. The layout
    gives the offsets of stack-assigned parameters and of the spill slots of register-assigned
    ones, which come after the stack-assigned results: so a gap of a word or more between two
    items, or before the first, is stack-assigned results."""
    _, _, _, npcdata, _, _, nfuncdata = b._func_fields(f._off)
    if nfuncdata <= FUNCDATA_ARGINFO:
        return 0, 0, False
    off, = struct.unpack_from('<I', b.d, f._off + gobin.FUNC_HEADER + 4 * npcdata +
                              4 * FUNCDATA_ARGINFO)
    if off == 0xFFFFFFFF:
        return 0, 0, False
    if b._md is None:
        return None, None, True
    p = b._off(b._md_word(gobin.MD_GOFUNC) + off)
    d, items = b.d, []
    while True:
        o = d[p]
        p += 1
        if o == 0xFF:
            break
        if o in (0xFE, 0xFD):
            continue
        if o >= 0xF0:
            return None, None, True
        items.append((o, d[p]))
        p += 1
    end, gap = 0, False
    for o, size in sorted(items):
        if o > (end + 7) // 8 * 8:
            gap = True
        end = max(end, o + size)
    return len(items), (end + 7) // 8 * 8, gap


def arg_size(b, f):
    return struct.unpack_from('<i', b.d, f._off + 8)[0]


# The whole binary

class Func:
    """One function in compact form. ins is [(kind, reads, writes, target, partial writes)];
    blocks is [(first, last)] instruction ranges; succ the successor blocks of each."""
    __slots__ = ('ins', 'offs', 'blocks', 'succ', 'entry_garbage', 'stack', 'low', 'sites',
                 'passes', 'callees', 'at', 'params', 'stores', 'stack_end', 'confirmed', 'nitems')


class Analysis:
    def __init__(self, b, known=None, types=None):
        self.b = b
        self.funcs = sorted(b.funcs(), key=lambda f: f.entry)
        self.entries = [f.entry for f in self.funcs]
        self.by_name = {}
        for i, f in enumerate(self.funcs):
            self.by_name.setdefault(f.name, i)
        self.known = {}
        for name, mask in (known_methods(b, types) if known is None else known).items():
            if name in self.by_name:
                self.known[self.by_name[name]] = mask
        decoded = gocmpx.decode([b.code(f) for f in self.funcs])
        compact = [self._compact(f, ins) for f, ins in zip(self.funcs, decoded)]
        del decoded
        self.noret = self._noreturn(compact)
        self.fs = [self._func(f, ins, offs, texts, self.noret) for f, (ins, offs, texts) in
                   zip(self.funcs, compact)]
        self.fs_params = [fn.params for fn in self.fs]
        for fn in self.fs:
            if fn.params is None and fn.nitems:
                self._narrow_params(fn)
        self._static_bounds()
        for fn in self.fs:
            fn.stores = None
            if fn.stack_end is not None and fn.confirmed:
                fn.stack = False
        self._solve()

    def index(self, addr):
        i = bisect.bisect_right(self.entries, addr) - 1
        if i >= 0 and addr < self.funcs[i].end:
            return i
        return None

    def _target_func(self, addr):
        i = self.index(addr)
        return i if i is not None and self.funcs[i].entry == addr else -1

    def _compact(self, f, decoded):
        """[(kind, reads, writes, target, partial writes)], offsets and texts of a function's
        instructions."""
        size = f.end - f.entry
        out, offs = [], []
        for x in decoded:
            m, ops = parse(x.text)
            rel = _rel(x.raw)
            reads = writes = partial = 0
            target = None
            if m == 'call':
                if rel is not None:
                    kind, target = CALL, self._target_func(f.entry + x.off + len(x.raw) + rel)
                    if target >= 0 and PRESERVING.match(self.funcs[target].name):
                        kind, target, writes = NORMAL, None, PRESERVING_WRITES
                else:
                    kind = ICALL
            elif m == 'ret':
                kind = RET
            elif m == 'jmp':
                if rel is None:
                    kind = IJMP
                else:
                    t = x.off + len(x.raw) + rel
                    if 0 <= t < size:
                        kind, target = JMP, t
                    else:
                        kind, target = XJMP, self._target_func(f.entry + t)
            elif m.startswith('j'):
                t = x.off + len(x.raw) + (rel or 0)
                kind, target = (JCC, t) if rel is not None and 0 <= t < size else (UNKNOWN, None)
            elif m in TERMINATORS:
                kind = STOP
            else:
                u = uses(m, ops)
                if u is None:
                    kind = UNKNOWN
                else:
                    kind, (reads, writes, partial) = NORMAL, u
                    if m in ('xchg', 'xadd', 'cmpxchg'):
                        kind = ATOMIC
            out.append((kind, reads, writes, target, partial))
            offs.append(x.off)
        return out, offs, [x.text for x in decoded]

    def _noreturn(self, compact):
        """Whether each function never returns: it has no ret and no indirect jump, and every
        function it jumps to never returns either."""
        n = len(compact)
        noret = [True] * n
        jumps = [[] for _ in range(n)]
        for i, (ins, _, _) in enumerate(compact):
            if NORETURN.match(self.funcs[i].name):
                continue
            for kind, _, _, target, _ in ins:
                if kind in (RET, IJMP) or kind == XJMP and target < 0:
                    noret[i] = False
                elif kind == XJMP:
                    jumps[i].append(target)
        changed = True
        while changed:
            changed = False
            for i in range(n):
                if noret[i] and any(not noret[t] for t in jumps[i]):
                    noret[i] = False
                    changed = True
        return noret

    def _func(self, f, ins, offs, texts, noret):
        fn = Func()
        fn.ins, fn.offs = ins, offs
        at = {o: k for k, o in enumerate(offs)}
        ends = (JMP, JCC, RET, XJMP, IJMP, STOP)
        leaders = {0} if ins else set()
        for k, (kind, _, _, target, _) in enumerate(ins):
            if kind in (JMP, JCC) and target in at:
                leaders.add(at[target])
            if (kind in ends or kind == CALL and target >= 0 and noret[target]) and \
                    k + 1 < len(ins):
                leaders.add(k + 1)
        starts = sorted(leaders)
        fn.blocks = [(s, (starts[n + 1] if n + 1 < len(starts) else len(ins)) - 1)
                     for n, s in enumerate(starts)]
        block_at = {s: n for n, s in enumerate(starts)}
        fn.succ = []
        for s, e in fn.blocks:
            kind, _, _, target, _ = ins[e]
            succ = []
            falls = kind not in (JMP, RET, XJMP, IJMP, STOP) and \
                not (kind == CALL and target >= 0 and noret[target])
            if falls and e + 1 < len(ins):
                succ.append(block_at[e + 1])
            if kind in (JMP, JCC) and target in at:
                succ.append(block_at[at[target]])
            fn.succ.append(succ)
        fn.at = at
        fn.params, spill = self._spilled(ins, texts)
        nitems, pend, gap = arg_info(self.b, f)
        fn.stack_end, fn.confirmed = None, None
        if nitems is None:              # the layout is cut short: the spill code may tell
            params = ALL
            fn.stack = spill != 0 and not (spill is None and fn.params == 0 and
                                           arg_size(self.b, f) == 0)
            if fn.stack and spill:
                fn.stack_end = spill
            elif fn.stack and spill is None and fn.params == 0:
                fn.stack_end = arg_size(self.b, f)  # no register parameters
        else:
            params = ((1 << min(nitems, NINT)) - 1) | (((1 << min(nitems, NFLOAT)) - 1) << NINT)
            fn.stack = gap or arg_size(self.b, f) != pend
        if fn.params is not None:
            params = fn.params
        fn.nitems = nitems
        fn.entry_garbage = ALL & ~params
        fn.stores = {}
        for j, t in enumerate(texts):
            m = STORE.match(t)
            if m:
                fn.stores[j] = (int(m.group(2) or '0', 0), WIDTH[m.group(1)])
        fn.low, fn.sites, fn.passes, fn.callees = 0, 0, [], set()
        return fn

    def _spilled(self, ins, texts):
        """(parameter registers, lowest spill slot offset in the argument area or None) of a
        function, from the code that calls runtime.morestack when the stack must grow: it
        stores every register-assigned parameter to its spill slot first. The spill slots
        follow the stack-assigned arguments and results. (None, None) when the function has no
        such call."""
        for k, (kind, _, _, target, _) in enumerate(ins):
            if kind == CALL and target >= 0 and \
                    self.funcs[target].name in ('runtime.morestack_noctxt', 'runtime.morestack'):
                params, low = 0, None
                for j in range(k - 1, -1, -1):
                    kd, r, w, _, _ = ins[j]
                    if kd != NORMAL or w:
                        break
                    params |= r
                    m = SPILL.search(texts[j])
                    if m and r:
                        off = int(m.group(1), 0) - 8
                        low = off if low is None else min(low, off)
                return params, low
        return None, None

    def _narrow_params(self, fn):
        """Narrow the parameter registers of a function with no spill code, which the
        argument layout bounds only by its number of items (one register at most each, of
        either kind): the registers read before being written from the entry are parameters,
        and leave fewer items for parameters of the other kind."""
        live, _ = self._after_call(fn, -1)
        ints = (live & INTS).bit_length()
        floats = (live >> NINT).bit_length()
        n = fn.nitems
        if ints + floats > n:           # not the register convention (assembly): leave it
            return
        params = ((1 << min(n - floats, NINT)) - 1) | (((1 << min(n - ints, NFLOAT)) - 1) << NINT)
        fn.entry_garbage = ALL & ~params

    # Lower bounds that do not depend on other functions

    def _after_call(self, fn, k):
        """(registers read after instruction k, a call, before being written, registers not
        written before the code reaches a ret, or None), along the path that falls through
        conditional branches and follows unconditional ones inside the function. Crossing a
        join is sound: a register that is read there holds a value on every edge into it."""
        read = dead = 0
        j, seen = k + 1, set()
        while j < len(fn.ins) and j not in seen:
            seen.add(j)
            kind, r, w, target, _ = fn.ins[j]
            if kind == RET:
                return read, ALL & ~dead
            if kind == JMP:
                if target not in fn.at:
                    break
                j = fn.at[target]
                continue
            if kind in (CALL, XJMP) and target >= 0 and self.fs_params[target] is not None:
                read |= self.fs_params[target] & ~dead      # passed on as arguments
                break
            if kind not in (NORMAL, ATOMIC, JCC):
                break
            read |= r & ~dead
            dead |= w           # a later read of a partly written register is not counted
            j += 1
        return read, None

    def _before_ret(self, fn, k):
        """The registers written in the straight-line code before instruction k, a ret, and
        not read again before it."""
        live = out = 0
        for j in range(k - 1, -1, -1):
            kind, r, w, _, part = fn.ins[j]
            if kind == ATOMIC:
                live = (live & ~w) | r
                continue
            if kind != NORMAL:
                break
            out |= w & ~live
            live = (live & ~(w & ~part)) | r | part
        return out

    def _static_bounds(self):
        for ci, fn in enumerate(self.fs):
            for k, (kind, _, _, target, _) in enumerate(fn.ins):
                if kind == CALL and target >= 0:
                    callee = self.fs[target]
                    callee.sites += 1
                    fn.callees.add(target)
                    if callee.stack_end is not None and callee.confirmed is not False:
                        callee.confirmed = self._writes_arg(fn, k, callee.stack_end - 1)
                    if self.noret[target]:
                        continue
                    read, through = self._after_call(fn, k)
                    callee.low |= read
                    if through:
                        callee.passes.append((ci, through))
                elif kind == XJMP and target >= 0:
                    self.fs[target].sites += 1
                    self.fs[target].passes.append((ci, ALL))
                    fn.callees.add(target)
                elif kind == RET:
                    fn.low |= self._before_ret(fn, k)

    @staticmethod
    def _writes_arg(fn, k, byte):
        """Whether the straight-line code before instruction k, a call, stores to the byte of
        the outgoing argument area at offset byte. The stack-assigned results of the callee
        come just before its spill slots, and a caller leaves them uninitialized: a store to
        the last byte before the spill slots shows that byte is an argument, so there are no
        stack-assigned results."""
        for j in range(k - 1, -1, -1):
            if fn.ins[j][0] not in (NORMAL, ATOMIC, UNKNOWN):
                return False
            st = fn.stores.get(j)
            if st and st[0] <= byte < st[0] + st[1]:
                return True
        return False

    # The fixpoint

    def _upper(self, fi, high):
        """The registers that may hold a result of function fi, given the upper bounds high of
        every function: those that are not garbage at any ret. A register is garbage at entry
        unless a parameter may be in it, and after a call unless the callee may return it; a
        write makes it a value. An indirect call or an instruction this module does not know
        makes every register a possible value."""
        fn = self.fs[fi]
        if not fn.blocks:
            return 0
        ins = fn.ins
        nb = len(fn.blocks)
        state = [None] * nb             # garbage registers at the start of each block
        out = [0] * nb
        state[0] = fn.entry_garbage
        work = [0]
        while work:
            n = work.pop()
            g = state[n]
            s, e = fn.blocks[n]
            for k in range(s, e + 1):
                kind, _, w, target, _ = ins[k]
                if kind == NORMAL or kind == ATOMIC:
                    g &= ~w
                elif kind == CALL:
                    g = ALL & ~high[target] if target >= 0 else 0
                elif kind == ICALL or kind == UNKNOWN:
                    g = 0
            out[n] = g
            for m in fn.succ[n]:
                if state[m] is None or g & ~state[m]:
                    state[m] = g if state[m] is None else state[m] | g
                    work.append(m)
        allowed, exits = ALL, 0
        for n, (s, e) in enumerate(fn.blocks):
            if state[n] is None:
                continue
            kind, _, _, target, _ = ins[e]
            if kind == RET:
                allowed &= ~out[n]
                exits += 1
            elif kind == XJMP:
                allowed &= high[target] if target >= 0 else ALL
                exits += 1
            elif kind == IJMP:
                exits += 1
        return allowed if exits else 0

    def _solve(self):
        n = len(self.fs)
        high = [ALL] * n
        low = [fn.low for fn in self.fs]
        for i, mask in self.known.items():
            high[i] = low[i] = mask
        callers = [[] for _ in range(n)]
        for ci, fn in enumerate(self.fs):
            for t in fn.callees:
                callers[t].append(ci)
        work = set(i for i in range(n) if i not in self.known)
        while work:
            batch, work = sorted(work), set()
            for i in batch:
                h = self._upper(i, high)
                lo = low[i]
                for ci, through in self.fs[i].passes:
                    lo |= low[ci] & through
                if h != high[i] or lo != low[i]:
                    high[i], low[i] = h, lo
                    work.update(c for c in callers[i] if c not in self.known)
                    work.update(t for t in self.fs[i].callees if t not in self.known)
        self.high, self.low = high, low

    def evidence(self, fi):
        fn = self.fs[fi]
        return {'sites': fn.sites, 'stack': fn.stack, 'low': self.low[fi],
                'high': self.high[fi], 'known': fi in self.known}

    def results(self, fi):
        e = self.evidence(fi)
        lo, hi = e['low'], e['high']
        if not e['sites'] or e['stack'] or lo != hi or self.noret[fi]:
            return None
        ints, floats = lo & INTS, lo >> NINT
        if ints & (ints + 1) or floats & (floats + 1) or ints and floats:
            return None
        return ['int'] * bin(ints).count('1') + ['float'] * bin(floats).count('1')


def analysis(b, known=None, types=None):
    """The solved Analysis of binary b, kept on b unless known (a {name: mask} that replaces
    the methods' known results) is given. types is b's gotypes.Types, if already loaded."""
    if known is not None:
        return Analysis(b, known)
    a = getattr(b, '_gosig_analysis', None)
    if a is None:
        key = (len(b.d), zlib.crc32(b.d))      # the same file loaded again
        if _last[0] == key:
            a = _last[1]
        else:
            a = Analysis(b, types=types)
            _last[:] = [key, a]
        b._gosig_analysis = a
    return a


_last = [None, None]                # (key, Analysis) of the last binary analysed


def evidence(b, name):
    a = analysis(b)
    return a.evidence(a.by_name[name])


def results(b, name):
    """The result registers of function name as a list of 'int' and 'float' ([] for none), or
    None when the evidence is missing or inconsistent."""
    a = analysis(b)
    fi = a.by_name.get(name)
    if fi is None:
        raise KeyError(name)
    return a.results(fi)


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__.split('\n\n')[1])
    b = gobin.load(argv[1])
    a = analysis(b)
    rx = re.compile(argv[2])
    for name, fi in sorted(a.by_name.items()):
        if rx.search(name):
            r = a.results(fi)
            print('%s\t%s\t%d' % (name, '?' if r is None else ' '.join(r) or '-',
                                  a.fs[fi].sites))


if __name__ == '__main__':
    main(sys.argv)
