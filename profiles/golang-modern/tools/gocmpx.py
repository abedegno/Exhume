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
the same name in both binaries, or outside every Go function in both. A RIP-relative operand is
treated alike when it points into a Go function, or at data whose first word does (a function
value); an operand that points at other data is only masked.

--diff prints a summary line (status, sizes, number of differing rows, detail), then one row per
instruction after padding is trimmed: offset, instruction text, source file base name and line,
and in brackets the innermost inlined callee, for REF on the left and CAND on the right. A branch
that leaves the function and a RIP-relative operand that points at a Go function or a function
value show the name of their target; other RIP-relative operands show as [rip + data], since data
addresses are not compared. The two sides are aligned by a sequence diff of the instruction text,
so they line up even when the sizes differ. A row is marked `*` when the instructions differ or
one side has none, `~` when they are equal but the source line or inlined callee differs.

As a module: compare(ref, cand, name) returns a Result; compare_many(ref, cand, names) returns the
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

STATUSES = ('MATCH', 'LINES', 'INLINE', 'DIFF', 'SIZE', 'MISSING')
PADDING = (0xCC, 0x90)                  # int3 and nop, which the linker puts between functions
CHUNK = 1500                            # functions per objdump process
OUTSIDE = 'outside Go code'
Result = collections.namedtuple('Result', 'status name detail')
Insn = collections.namedtuple('Insn', 'off raw text')

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


def _target_difference(ref, cand, rf, cf, rc, cc, rins, cins):
    """None if the branches that leave the function and the RIP-relative operands that point into
    Go code go to the same place in both binaries; the other RIP-relative operands (data) are not
    compared."""
    rb, cb = references(rc, rins), references(cc, cins)
    rr, cr = _resolver(ref), _resolver(cand)
    for key in sorted(set(rb) | set(cb), key=lambda k: (k[1], k[0])):
        kind, off = key
        if key not in rb or key not in cb:
            if kind == 'branch':
                return '+%#x: branch leaves the function in only one of them' % off
            continue
        if kind == 'branch':
            a, b = rr.where(rf.entry + rb[key]), cr.where(cf.entry + cb[key])
        else:
            a, b = rr.value_where(rf.entry + rb[key]), cr.value_where(cf.entry + cb[key])
        if a != b and (kind == 'branch' or a != OUTSIDE or b != OUTSIDE):
            return '+%#x: %s to %s / %s' % (off, 'branch' if kind == 'branch' else 'address', a, b)
    return None


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
    results, found = [None] * len(names), []
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
        why = _code_difference(rc, cc, rins, cins) or \
            _target_difference(ref, cand, rf, cf, rc, cc, rins, cins)
        if why:
            results[n] = Result('DIFF', names[n], why)
    for n, name in enumerate(names):
        if results[n] is not None:
            continue
        rf, cf = rnames[name], cnames[name]
        rl, cl = ref.lines(rf), cand.lines(cf)
        if rl != cl:
            results[n] = Result('LINES', name, _line_detail(rl, cl))
            continue
        ri, ci = ref.inltree(rf), cand.inltree(cf)
        if [_inline_key(i) for i in ri] != [_inline_key(i) for i in ci]:
            results[n] = Result('INLINE', name, _inline_detail(ri, ci))
        else:
            results[n] = Result('MATCH', name, '')
    return results


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
    summary = '%s\t%s\t%s / %s; %d differing rows%s' % (
        r.status, name, size(rf, lsize), size(cf, rsize), ndiff,
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
    for r in compare_many(ref, cand, names):
        print('%s\t%s\t%s' % (r.status, r.name, r.detail))


if __name__ == '__main__':
    main(sys.argv)
