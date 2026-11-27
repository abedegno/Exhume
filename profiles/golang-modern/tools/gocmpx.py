#!/usr/bin/env python3
"""gocmpx.py: compare one function, or every function matching a regex, of two Go ELF binaries.

    gocmpx.py REF CAND REGEX       one line per function of REF whose name matches REGEX:
                                   STATUS<TAB>name<TAB>detail
    gocmpx.py REF CAND --diff NAME  the instructions of one function in both, side by side

Both binaries are read with gobin.py (Go 1.20 and later, linux/amd64). The status of a function is
the first of these that applies:

    MISSING   the candidate has no function of that name
    SIZE      the code length differs, once trailing int3 and nop padding is removed
    DIFF      an instruction differs, or a byte differs outside a relocated field
    LINES     the code is equal, a file or line of the line table differs
    INLINE    code and lines are equal, an inlined call's callee or call-site line differs
    MATCH     none of the above

A relocated field is the 4-byte displacement of a call, jmp or conditional jump that leaves the
function, or of a RIP-relative operand: it holds a distance to something else in the binary, and
that moves whenever anything before it is added or removed. So the field is not compared, but the
target of a branch that leaves the function is: it must land at the same offset of a function of
the same name in both binaries, or outside every Go function in both. A RIP-relative operand (a
data operand) is compared in one of four ways, counted per function:

    function  it points into a Go function, or at data whose first word does (a function value):
              it must point at the same offset of a function of the same name
    type      it points at a runtime type descriptor: the types must have the same name (a named
              type with its import path, as example.com/p.T); or at an itab: the interface and
              the concrete type must have the same names
    content   it is a lea of the address of string bytes whose length is an immediate paired
              with it in the same basic block (see string_lengths): the bytes over that length
              must be equal
    masked    anything else (globals, jump tables, other static data, a string whose length is
              not paired): not compared

The pairing of a length with an address is found in the reference and applied at the same
offset of the candidate (the instructions are equal by then), and only the two ways Go passes a
string are accepted, so that equal sources never give a DIFF. A string compared inline against
immediates is code and compared as such. A data difference is a DIFF whose detail holds both
strings or names.

The command prints one line to stderr after the rows: `data operands of N MATCH functions: C by
content, T by type, F by function, M masked`, the totals over the functions that are MATCH, so
that it is known what the MATCH count rests on.

--diff prints a summary line (status, sizes, number of differing rows, the data operand counts
when the code was compared, detail), then one row per
instruction after padding is trimmed: offset, instruction text, source file base name and line,
and in brackets the innermost inlined callee, for REF on the left and CAND on the right. A branch
that leaves the function and a RIP-relative operand that points at a Go function or a function
value show the name of their target; other RIP-relative operands show as [rip + data], since data
addresses are not compared. The two sides are aligned by a sequence diff of the instruction text,
so they line up even when the sizes differ. A row is marked `*` when the instructions differ or
one side has none, `~` when they are equal but the source line or inlined callee differs.

As a module: compare(ref, cand, name) returns a Result (status, name, detail, and data, the
Operands counts or None when the code was not compared); compare_many(ref, cand, names) returns the
results of many functions and decodes them in as few objdump runs as possible. Every function
is decoded, since equal bytes can still call different functions. The code is written
as the sections of one relocatable ELF file, which objdump (LLVM or GNU) disassembles in a single process, so there is no per-function
process cost.
"""
import bisect
import collections
import difflib
import os
import re
import struct
import subprocess
import sys
import tempfile

import gobin
import gotypes

STATUSES = ('MATCH', 'LINES', 'INLINE', 'DIFF', 'SIZE', 'MISSING')
PADDING = (0xCC, 0x90)                  # int3 and nop, which the linker puts between functions
CHUNK = 1500                            # functions per objdump process
OUTSIDE = 'outside Go code'
# data is the Operands of a function whose code was compared (DIFF from data, LINES, INLINE,
# MATCH), else None
Result = collections.namedtuple('Result', 'status name detail data', defaults=(None,))
Insn = collections.namedtuple('Insn', 'off raw text')
# how the RIP-relative operands of a function were compared
Operands = collections.namedtuple('Operands', 'content type function masked')
OPERAND_KINDS = ('content', 'type', 'function', 'masked')
SHOWN = 60                              # bytes of a string shown in a detail

RIP_OPERAND = re.compile(r'\[rip(?:\s*([+-])\s*(0x[0-9a-fA-F]+|\d+))?\]')
SECTION = re.compile(r'^Disassembly of section (\S+):')
INSN_LINE = re.compile(r'^\s*([0-9a-f]+):\s+((?:[0-9a-f]{2} )*[0-9a-f]{2})(?:\s*\t\s*(.*))?$')


def is_padding(ins):
    """True for an int3 or a nop, the instructions the linker puts between functions."""
    return ins.raw == b'\xcc' or ins.text.split('\t')[0] in ('int3', 'nop') or ins.raw == b'\x66\x90'


def _padding_count(insns):
    n = 0
    while n < len(insns) and is_padding(insns[-1 - n]):
        n += 1
    return n


def trim_padding(code, insns):
    """The code without the whole int3 and nop instructions that pad it to the next function,
    given its decoded instructions. The cut is always between instructions, so two different
    instructions that happen to end in 0x90 or 0xcc bytes are never shortened alike."""
    keep = len(insns) - _padding_count(insns)
    return code[:insns[keep].off] if keep < len(insns) else code


# Decoding

def _elf_of_sections(codes):
    """A relocatable x86-64 ELF file with one executable section, .t0, .t1 ..., per code."""
    names, offs = b'\0.shstrtab\0', {}
    for i in range(len(codes)):
        offs[i] = len(names)
        names += b'.t%d\0' % i
    body, pos, where = b'', 64, []
    for c in codes:
        where.append(pos + len(body))
        body += c
    names_at = 64 + len(body)
    shoff = (names_at + len(names) + 7) & ~7
    shdrs = [struct.pack('<IIQQQQIIQQ', *([0] * 10))]
    for i, c in enumerate(codes):
        shdrs.append(struct.pack('<IIQQQQIIQQ', offs[i], 1, 6, 0, where[i], len(c), 0, 0, 1, 0))
    shdrs.append(struct.pack('<IIQQQQIIQQ', 1, 3, 0, 0, names_at, len(names), 0, 0, 1, 0))
    ehdr = b'\x7fELF\2\1\1' + b'\0' * 9 + struct.pack('<HHIQQQIHHHHHH', 1, 62, 1, 0, 0, shoff, 0,
                                                       64, 0, 0, 64, len(shdrs), len(shdrs) - 1)
    return ehdr + body + names + b'\0' * (shoff - names_at - len(names)) + b''.join(shdrs)


def _parse_listing(text, n):
    """[[Insn, ...] for each of n sections] from objdump's disassembly of _elf_of_sections."""
    out, cur = [[] for _ in range(n)], None
    for line in text.splitlines():
        m = SECTION.match(line)
        if m:
            cur = out[int(m.group(1)[2:])] if re.fullmatch(r'\.t\d+', m.group(1)) else None
            continue
        m = INSN_LINE.match(line)
        if m is None or cur is None:
            continue
        raw = bytes.fromhex(m.group(2).replace(' ', ''))
        if m.group(3) is None and cur:          # a long instruction continued on the next line
            last = cur[-1]
            cur[-1] = Insn(last.off, last.raw + raw, last.text)
        else:
            cur.append(Insn(int(m.group(1), 16), raw, (m.group(3) or '').strip()))
    return out


def decode(codes):
    """The instructions of each code, as [Insn(offset, bytes, text), ...] per code."""
    out = []
    for start in range(0, len(codes), CHUNK):
        part = codes[start:start + CHUNK]
        fd, path = tempfile.mkstemp(suffix='.o', prefix='gocmpx-')
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(_elf_of_sections(part))
            r = subprocess.run(['objdump', '-d', '-z', '--x86-asm-syntax=intel', path],
                               capture_output=True, text=True)
        finally:
            os.unlink(path)
        if r.returncode != 0:
            raise RuntimeError('objdump failed: ' + r.stderr.strip())
        out.extend(_parse_listing(r.stdout, len(part)))
    return out


def _branch(ins):
    """(position of the rel32 field, displacement) of a call, jmp or jcc rel32, else None."""
    raw = ins.raw
    if len(raw) == 5 and raw[0] in (0xE8, 0xE9):
        return 1, struct.unpack_from('<i', raw, 1)[0]
    if len(raw) == 6 and raw[0] == 0x0F and 0x80 <= raw[1] <= 0x8F:
        return 2, struct.unpack_from('<i', raw, 2)[0]
    return None


def leaving_branches(code, insns):
    """{offset of the instruction: target offset from the function's entry} of each rel32 branch
    that leaves the function."""
    out = {}
    for ins in insns:
        br = _branch(ins)
        if br:
            target = ins.off + len(ins.raw) + br[1]
            if not 0 <= target < len(code):
                out[ins.off] = target
    return out


def _rip_displacement(ins):
    """The displacement of the RIP-relative operand of an instruction that is not a branch
    rel32, else None."""
    m = RIP_OPERAND.search(ins.text)
    if not m:
        return None
    disp = int(m.group(2), 0) if m.group(2) else 0
    return -disp if m.group(1) == '-' else disp


def references(code, insns):
    """{(kind, offset of the instruction): target offset from the function's entry} of each rel32
    branch that leaves the function ('branch') and each RIP-relative operand ('rip')."""
    out = {}
    for ins in insns:
        end = ins.off + len(ins.raw)
        br = _branch(ins)
        if br:
            if not 0 <= end + br[1] < len(code):
                out['branch', ins.off] = end + br[1]
            continue
        disp = _rip_displacement(ins)
        if disp is not None:
            out['rip', ins.off] = end + disp
    return out


def masked(code, insns):
    """The code with the 4-byte relocated field of each instruction set to zero."""
    out = bytearray(code)
    leaving = references(code, insns)
    for ins in insns:
        br = _branch(ins)
        if br:
            if ('branch', ins.off) in leaving:
                out[ins.off + br[0]:ins.off + br[0] + 4] = bytes(4)
            continue
        disp = _rip_displacement(ins)
        if disp is not None:
            at = ins.raw.find(struct.pack('<i', disp), 1)
            if at >= 0:
                out[ins.off + at:ins.off + at + 4] = bytes(4)
    return bytes(out)


class Resolver:
    """Finds the Go function that contains an address of a binary."""

    def __init__(self, binary):
        self.binary = binary
        funcs = sorted(binary.funcs(), key=lambda f: f.entry)
        self.funcs, self.entries = funcs, [f.entry for f in funcs]

    def where(self, addr):
        """'name+0xoff', or 'outside Go code' when no function contains addr."""
        i = bisect.bisect_right(self.entries, addr) - 1
        if i >= 0 and addr < self.funcs[i].end:
            return '%s+%#x' % (self.funcs[i].name, addr - self.funcs[i].entry)
        return OUTSIDE


    def value_where(self, addr):
        """Like where, but an address in data that holds the address of a Go function (a function
        value, the closure object a func-typed operand points at) counts as that function."""
        here = self.where(addr)
        if here != OUTSIDE:
            return here
        try:
            held = self.where(self.binary._u64(addr))
        except (KeyError, AttributeError, struct.error):
            return OUTSIDE
        return OUTSIDE if held == OUTSIDE else 'value of ' + held


def _resolver(binary):
    r = getattr(binary, '_gocmpx_resolver', None)
    if r is None:
        r = binary._gocmpx_resolver = Resolver(binary)
    return r


# Data operands: string literals, type descriptors and itabs

class Data:
    """Reads the read-only data of a binary that the comparator checks: string bytes, and the
    names of type descriptors and itabs. Go puts all of them between runtime.types and
    runtime.etypes (the module's read-only data), whose bounds the moduledata holds. Names are
    read on demand and cached."""

    def __init__(self, binary):
        self.b = binary
        self.lo = self.hi = 0
        if getattr(binary, '_md', None) is not None:
            try:
                self.lo = binary._md_word(gotypes.MD_TYPES)
                self.hi = binary._md_word(gotypes.MD_ETYPES)
            except (KeyError, struct.error):
                self.lo = self.hi = 0
        # a gotypes reader that parses single descriptors without loading them all
        self.types = gotypes.Types.__new__(gotypes.Types)
        self.types.b, self.types.base, self.types.end = binary, self.lo, self.hi
        self.types.text, self.types._raw, self.types._types = 0, {}, {}
        self._types = {}

    def readonly(self, va, n=1):
        return self.lo <= va and va + n <= self.hi

    def bytes(self, va, n):
        p = self.b._off(va)
        return bytes(self.b.d[p:p + n])

    def _type(self, va, depth=0):
        """(name, kind) of the type descriptor at va, or None. A named type of a package is
        written with its import path, a pointer to a type as * and the name of that type, any
        other type as the runtime's string for it."""
        if va in self._types:
            return self._types[va]
        got = None
        if self.readonly(va) and self.types._plausible(va):
            try:
                t, _ = self.types._parse(va)
                name = t.str
                if t.named and t.pkgpath:
                    name = '%s.%s' % (t.pkgpath, t.name)
                elif t.kind == gotypes.POINTER and t.elem and depth < 8:
                    inner = self._type(t.elem, depth + 1)
                    if inner:
                        name = '*' + inner[0]
                got = (name, t.kind)
            except (KeyError, struct.error, IndexError):
                got = None
        self._types[va] = got
        return got

    def type_name(self, va):
        got = self._type(va)
        return got and got[0]

    def itab(self, va):
        """(interface type name, concrete type name) of the itab at va, or None."""
        if not self.readonly(va, 16):
            return None
        inter, typ = struct.unpack('<QQ', self.bytes(va, 16))
        i, t = self._type(inter), self._type(typ)
        if i is None or t is None or i[1] != gotypes.INTERFACE:
            return None
        return i[0], t[0]


def _data(binary):
    d = getattr(binary, '_gocmpx_data', None)
    if d is None:
        d = binary._gocmpx_data = Data(binary)
    return d


def _show(s):
    """A string's bytes, quoted, escaped and cut to SHOWN bytes."""
    text = s[:SHOWN].decode('utf-8', 'replace').encode('unicode_escape').decode('ascii')
    more = '... (%d bytes)' % len(s) if len(s) > SHOWN else ''
    return '"%s"%s' % (text.replace('"', '\\"'), more)


# Pairing a string's address with its length. The pairing is found in the reference only: the
# function's instructions are equal in both by the time data is compared, so the same length
# belongs to the operand at the same offset of the candidate.

REG_NAMES = [('rax', 'eax', 'ax', 'al', 'ah'), ('rbx', 'ebx', 'bx', 'bl', 'bh'),
             ('rcx', 'ecx', 'cx', 'cl', 'ch'), ('rdx', 'edx', 'dx', 'dl', 'dh'),
             ('rsi', 'esi', 'si', 'sil'), ('rdi', 'edi', 'di', 'dil'),
             ('rbp', 'ebp', 'bp', 'bpl'), ('rsp', 'esp', 'sp', 'spl')] + \
            [('r%d' % n, 'r%dd' % n, 'r%dw' % n, 'r%db' % n) for n in range(8, 16)]
REG = {alias: names[0] for names in REG_NAMES for alias in names}
ABI_REGS = ('rax', 'rbx', 'rcx', 'rdi', 'rsi', 'r8', 'r9', 'r10', 'r11')   # Go's integer arguments
ALL = frozenset(REG.values())
IMMEDIATE = re.compile(r'^(0x[0-9a-f]+|\d+)$')
QWORD_AT = re.compile(r'^qword ptr \[(\w+)(?: ([+-]) (0x[0-9a-f]+|\d+))?\]$')
READS_ONLY = {'cmp', 'test', 'push', 'bt', 'ucomisd', 'ucomiss', 'comisd', 'comiss', 'nop'}
WRITES_MANY = {'mul', 'div', 'idiv', 'cqo', 'cdq', 'cwd', 'cdqe', 'cwde', 'xchg', 'xadd',
               'cmpxchg', 'syscall', 'rdtsc', 'cpuid', 'call', 'lock', 'movsb', 'movsw', 'movsq',
               'stosb', 'stosw', 'stosd', 'stosq', 'lodsb', 'lodsq', 'scasb'}
ENDS_BLOCK = {'call', 'ret', 'ud2', 'int3', 'hlt'}
MAX_STRING = 1 << 24


def _parsed(ins):
    """(mnemonic, [operand, ...]) of an instruction."""
    text = TARGET_NOTE.sub('', ins.text)
    op, _, rest = text.partition('\t')
    rest = re.sub(r'\s+', ' ', rest).strip()
    return op.strip(), [o.strip() for o in rest.split(',')] if rest else []


def _written(op, ops):
    """The general registers (by their 64-bit names) an instruction may write."""
    if op in READS_ONLY:
        return frozenset()
    if op in WRITES_MANY or op.startswith('rep') or (op == 'imul' and len(ops) == 1):
        return ALL
    if ops and ops[0] in REG:
        return frozenset((REG[ops[0]],))
    return frozenset()


def _imm(s):
    return int(s, 0) if IMMEDIATE.match(s) else None


def _qword_at(s):
    """(base register, offset) of a qword memory operand [reg +- off], else None."""
    m = QWORD_AT.match(s)
    if not m or m.group(1) not in REG:
        return None
    off = int(m.group(3), 0) if m.group(3) else 0
    return REG[m.group(1)], -off if m.group(2) == '-' else off


def string_lengths(insns, leas):
    """{offset: length} for those of the lea instructions at the offsets leas (each loading an
    address with a RIP-relative operand) whose address is paired with a length given as an
    immediate in the same basic block, in one of two ways:

    - in registers, as Go passes and returns a string: the address in one argument register
      and a `mov` of the immediate into the next one (rax and rbx, rbx and rcx, ...; also the
      operands of runtime.memequal), before or after the lea, with no other write to either
      register from the first of the two to the end of the block;
    - in memory, as Go stores a string header: the address stored to [base+off] (the first
      store of the register after the lea, which is not written in between) and the only store
      of an immediate to [base+off+8] in the block, with no write of base between the stores.

    A pairing found this way that is wrong could pass a length longer than the data at the
    address, so lengths are not guessed in any other case."""
    parsed = [_parsed(i) for i in insns]
    targets = set()
    for op, ops in parsed:
        if op.startswith('j') and ops and IMMEDIATE.match(ops[0]):
            targets.add(int(ops[0], 0))
    block, blocks = 0, []
    for k, ins in enumerate(insns):
        if k and (ins.off in targets or parsed[k - 1][0] in ENDS_BLOCK or
                  parsed[k - 1][0].startswith('j')):
            block += 1
        blocks.append(block)
    ends = {}
    for k, b in enumerate(blocks):
        ends[b] = k
    starts = {}
    for k, b in reversed(list(enumerate(blocks))):
        starts[b] = k
    writes = [_written(op, ops) for op, ops in parsed]
    out = {}
    for i, ins in enumerate(insns):
        if ins.off not in leas:
            continue
        op, ops = parsed[i]
        if op != 'lea' or len(ops) != 2 or ops[0] not in REG or REG[ops[0]] != ops[0]:
            continue
        reg, b = ops[0], blocks[i]
        lo, hi = starts[b], ends[b]
        last = hi if parsed[hi][0] in ENDS_BLOCK or parsed[hi][0].startswith('j') else hi + 1
        n = None
        if reg in ABI_REGS[:-1]:
            partner = ABI_REGS[ABI_REGS.index(reg) + 1]
            for j in range(lo, last):
                jop, jops = parsed[j]
                if j == i or jop != 'mov' or len(jops) != 2 or REG.get(jops[0]) != partner or \
                        _imm(jops[1]) is None:
                    continue
                if not any(writes[k] & {reg, partner} for k in range(min(i, j) + 1, last)
                           if k not in (i, j)):
                    n = _imm(jops[1])
                    break
        if n is None:
            for j in range(i + 1, last):
                jop, jops = parsed[j]
                if jop == 'mov' and len(jops) == 2 and jops[1] == reg and _qword_at(jops[0]):
                    base, off = _qword_at(jops[0])
                    found = [k for k in range(lo, last) if k not in (i, j) and
                             parsed[k][0] == 'mov' and len(parsed[k][1]) == 2 and
                             _qword_at(parsed[k][1][0]) == (base, off + 8) and
                             _imm(parsed[k][1][1]) is not None]
                    if len(found) == 1 and not any(
                            base in writes[k] for k in range(min(j, found[0]) + 1,
                                                             max(j, found[0]))):
                        n = _imm(parsed[found[0]][1][1])
                    break
                if reg in writes[j]:
                    break
        if n:
            out[ins.off] = n
    return out


def _target_difference(ref, cand, rf, cf, rc, cc, rins, cins):
    """(None or a description of the first difference, Operands). The branches that leave the
    function must go to the same place in both binaries. Of the RIP-relative operands, one that
    points into Go code or at a function value must point at the same function; one that points
    at a type descriptor or an itab at a type or itab of the same names; one that is the address
    of string bytes whose length is known (see string_lengths) at equal bytes. The others are
    masked."""
    rb, cb = references(rc, rins), references(cc, cins)
    rr, cr = _resolver(ref), _resolver(cand)
    rd, cd = _data(ref), _data(cand)
    counts = dict.fromkeys(OPERAND_KINDS, 0)
    found = []
    lengths = None
    for key in sorted(set(rb) | set(cb), key=lambda k: (k[1], k[0])):
        kind, off = key
        if key not in rb or key not in cb:
            if kind == 'branch':
                found.append('+%#x: branch leaves the function in only one of them' % off)
            continue
        if kind == 'branch':
            a, b = rr.where(rf.entry + rb[key]), cr.where(cf.entry + cb[key])
            if a != b:
                found.append('+%#x: branch to %s / %s' % (off, a, b))
            continue
        ra, ca = rf.entry + rb[key], cf.entry + cb[key]
        a, b = rr.value_where(ra), cr.value_where(ca)
        if a != OUTSIDE or b != OUTSIDE:
            counts['function'] += 1
            if a != b:
                found.append('+%#x: address to %s / %s' % (off, a, b))
            continue
        name = rd.type_name(ra)
        if name is not None:
            counts['type'] += 1
            other = cd.type_name(ca)
            if other != name:
                found.append('+%#x: type %s / %s' % (off, name, other or 'not a type'))
            continue
        itab = rd.itab(ra)
        if itab is not None:
            counts['type'] += 1
            other = cd.itab(ca)
            if other != itab:
                found.append('+%#x: itab %s / %s' % (off, '(%s, %s)' % itab,
                                                     '(%s, %s)' % other if other else 'not an itab'))
            continue
        if lengths is None:
            leas = {o for k, o in rb if k == 'rip'}
            lengths = string_lengths(rins, leas) if rd.hi else {}
        n = lengths.get(off)
        if n and n <= MAX_STRING and rd.readonly(ra, n):
            counts['content'] += 1
            sa = rd.bytes(ra, n)
            sb = cd.bytes(ca, n) if cd.readonly(ca, n) else None
            if sa != sb:
                found.append('+%#x: string %s / %s' % (
                    off, _show(sa), _show(sb) if sb is not None else 'not read-only data'))
            continue
        counts['masked'] += 1
    return (found[0] if found else None), Operands(**counts)


def _code_difference(rc, cc, rins, cins):
    """None if the two decoded codes are equal apart from relocated fields, else a description."""
    rm, cm = masked(rc, rins), masked(cc, cins)
    if [(i.off, len(i.raw)) for i in rins] != [(i.off, len(i.raw)) for i in cins]:
        for a, b in zip(rins, cins):
            if (a.off, len(a.raw)) != (b.off, len(b.raw)):
                return '+%#x: %s / %s' % (a.off, a.text, b.text)
        return 'instruction count %d / %d' % (len(rins), len(cins))
    for a, b in zip(rins, cins):
        lo, hi = a.off, a.off + len(a.raw)
        if rm[lo:hi] != cm[lo:hi]:
            return '+%#x: %s / %s' % (a.off, a.text, b.text)
    if rm != cm:
        return 'bytes differ'
    return None


# Comparison

def _first_difference(a, b, show):
    for x, y in zip(a, b):
        if x != y:
            return '%s / %s' % (show(x), show(y))
    return 'count %d / %d' % (len(a), len(b))


def _line_detail(a, b):
    return _first_difference(a, b, lambda r: '+%#x %s:%d' % r)


def _inline_key(i):
    return (i.callee, i.call_file, i.call_line, i.parent, tuple(i.pc_ranges))


def _inline_detail(a, b):
    return _first_difference([_inline_key(i) for i in a], [_inline_key(i) for i in b],
                             lambda k: '%s called at %s:%d, ranges %s' % (k[0], k[1], k[2], list(k[4])))


def _by_name(binary):
    index = getattr(binary, '_gocmpx_names', None)
    if index is None:
        index = {}
        for f in binary.funcs():
            index.setdefault(f.name, f)
        binary._gocmpx_names = index
    return index


def compare_many(ref, cand, names):
    """[Result, ...] for the functions called names. Every function whose size agrees is decoded,
    even if its bytes are equal: equal bytes, even at the same address, can call different
    functions when the two binaries are laid out differently."""
    rnames, cnames = _by_name(ref), _by_name(cand)
    results, found, data = [None] * len(names), [], [None] * len(names)
    for n, name in enumerate(names):
        rf, cf = rnames.get(name), cnames.get(name)
        if rf is None or cf is None:
            results[n] = Result('MISSING', name, 'not in the %s' % (
                'reference' if rf is None else 'candidate'))
        else:
            found.append((n, rf, cf, ref.code(rf), cand.code(cf)))
    decoded = decode([c for _, _, _, rc, cc in found for c in (rc, cc)]) if found else []
    for k, (n, rf, cf, rc, cc) in enumerate(found):
        rins, cins = decoded[2 * k], decoded[2 * k + 1]
        rc, cc = trim_padding(rc, rins), trim_padding(cc, cins)
        rins, cins = rins[:len(rins) - _padding_count(rins)], cins[:len(cins) - _padding_count(cins)]
        if len(rc) != len(cc):
            results[n] = Result('SIZE', names[n], '%d / %d bytes' % (len(rc), len(cc)))
            continue
        why = _code_difference(rc, cc, rins, cins)
        if why:
            results[n] = Result('DIFF', names[n], why)
            continue
        why, data[n] = _target_difference(ref, cand, rf, cf, rc, cc, rins, cins)
        if why:
            results[n] = Result('DIFF', names[n], why, data[n])
    for n, name in enumerate(names):
        if results[n] is not None:
            continue
        rf, cf = rnames[name], cnames[name]
        rl, cl = ref.lines(rf), cand.lines(cf)
        if rl != cl:
            results[n] = Result('LINES', name, _line_detail(rl, cl), data[n])
            continue
        ri, ci = ref.inltree(rf), cand.inltree(cf)
        if [_inline_key(i) for i in ri] != [_inline_key(i) for i in ci]:
            results[n] = Result('INLINE', name, _inline_detail(ri, ci), data[n])
        else:
            results[n] = Result('MATCH', name, '', data[n])
    return results


def operand_totals(results):
    """(number of MATCH results, Operands summed over them)."""
    match = [r for r in results if r.status == 'MATCH' and r.data is not None]
    return len(match), Operands(*(sum(r.data[k] for r in match) for k in range(4)))


def operand_text(ops):
    return '%d by content, %d by type, %d by function, %d masked' % ops


def compare(ref, cand, name):
    return compare_many(ref, cand, [name])[0]


# Side-by-side listing

TARGET_NOTE = re.compile(r'\s*(?:#.*|<\.t\d+[^>]*>)$')
ROW = collections.namedtuple('Row', 'off text src inl key')


def _name_of(where):
    return where[:-len('+0x0')] if where.endswith('+0x0') else where


def listing(binary, func, code, insns):
    """[Row, ...] of a function's decoded, padding-trimmed instructions: offset, instruction text
    with targets outside the function named, 'file.go:line', innermost inlined callee or '', and a
    key (text and masked bytes) that is equal for instructions the comparator treats as equal."""
    res = _resolver(binary)
    refs = references(code, insns)
    mask = masked(code, insns)
    try:
        lines = binary.lines(func)
    except Exception:
        lines = []
    try:
        tree = binary.inltree(func)
    except Exception:
        tree = []
    starts = [l[0] for l in lines]
    rows = []
    for ins in insns:
        text = TARGET_NOTE.sub('', ins.text)
        if ('branch', ins.off) in refs:
            op = text.split('\t')[0]
            text = '%s\t%s' % (op, _name_of(res.where(func.entry + refs['branch', ins.off])))
        elif ('rip', ins.off) in refs:
            where = res.value_where(func.entry + refs['rip', ins.off])
            text = RIP_OPERAND.sub('[rip + data]' if where == OUTSIDE else
                                   '[rip -> %s]' % _name_of(where), text)
        text = re.sub(r'\s+', ' ', text).strip()
        k = bisect.bisect_right(starts, ins.off) - 1
        src = '%s:%d' % (os.path.basename(lines[k][1]), lines[k][2]) if k >= 0 else ''
        inl = next((i.callee for i in tree if any(a <= ins.off < b for a, b in i.pc_ranges)), '')
        rows.append(ROW(ins.off, text, src, inl, (text, mask[ins.off:ins.off + len(ins.raw)])))
    return rows


def diff(ref, cand, name):
    """(summary line, [(mark, ref Row or None, cand Row or None)]) for one function."""
    r = compare(ref, cand, name)
    rf, cf = _by_name(ref).get(name), _by_name(cand).get(name)
    sides = []
    for b, f in ((ref, rf), (cand, cf)):
        if f is None:
            sides.append(([], 0))
            continue
        code = b.code(f)
        insns = decode([code])[0]
        code = trim_padding(code, insns)
        insns = insns[:len(insns) - _padding_count(insns)]
        sides.append((listing(b, f, code, insns), len(code)))
    (left, lsize), (right, rsize) = sides
    pairs = []
    sm = difflib.SequenceMatcher(None, [x.text for x in left], [x.text for x in right],
                                 autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        a, b = left[i1:i2], right[j1:j2]
        for k in range(max(len(a), len(b))):
            pairs.append((a[k] if k < len(a) else None, b[k] if k < len(b) else None))
    rows = []
    for a, b in pairs:
        # the bytes are compared only at the same offset: a branch inside the function that
        # has moved has other bytes for the same target
        if a is None or b is None or a.text != b.text or (a.off == b.off and a.key != b.key):
            mark = '*'
        elif (a.src, a.inl) != (b.src, b.inl):
            mark = '~'
        else:
            mark = ' '
        rows.append((mark, a, b))
    ndiff = sum(m != ' ' for m, _, _ in rows)
    size = lambda f, n: 'missing' if f is None else '%d bytes' % n
    summary = '%s\t%s\t%s / %s; %d differing rows%s%s' % (
        r.status, name, size(rf, lsize), size(cf, rsize), ndiff,
        '; data: ' + operand_text(r.data) if r.data is not None else '',
        '; ' + re.sub(r'\s+', ' ', r.detail) if r.detail and r.status != 'SIZE' else '')
    return summary, rows


def format_diff(summary, rows):
    def cell(x):
        if x is None:
            return ''
        return '%#06x  %s  %s%s' % (x.off, x.text, x.src, ' [%s]' % x.inl if x.inl else '')
    width = max([len(cell(a)) for _, a, _ in rows] + [len('reference')])
    out = [summary, '  %s | %s' % ('reference'.ljust(width), 'candidate')]
    for mark, a, b in rows:
        out.append(('%s %s | %s' % (mark, cell(a).ljust(width), cell(b))).rstrip())
    return '\n'.join(out)


def main(argv):
    if len(argv) == 5 and argv[3] == '--diff':
        ref, cand = gobin.load(argv[1]), gobin.load(argv[2])
        print(format_diff(*diff(ref, cand, argv[4])))
        return
    if len(argv) != 4:
        sys.exit(__doc__.split('\n\n')[1])
    ref, cand = gobin.load(argv[1]), gobin.load(argv[2])
    pattern = re.compile(argv[3])
    names = list(dict.fromkeys(f.name for f in ref.funcs() if pattern.search(f.name)))
    results = compare_many(ref, cand, names)
    for r in results:
        print('%s\t%s\t%s' % (r.status, r.name, r.detail))
    n, ops = operand_totals(results)
    print('data operands of %d MATCH functions: %s' % (n, operand_text(ops)), file=sys.stderr)


if __name__ == '__main__':
    main(sys.argv)
