#!/usr/bin/env python3
"""gobin.py: read the functions, line tables, inlining trees and build info of a Go ELF binary.

    gobin.py funcs BINARY           entry, end, name and file:line of every function
    gobin.py lines BINARY NAME      offset in function, file and line, at each change
    gobin.py inltree BINARY NAME    the calls the compiler inlined into the function
    gobin.py buildinfo BINARY       Go version, main package, build settings and modules

For binaries built by Go 1.20 and later (pclntab magic 0xfffffff1), linux/amd64 (ELF64, little
endian), internally or externally linked, stripped (-s -w) or not.

Function addresses in the pclntab are offsets from the text base, runtime.text. That is not the
start of the .text section: an externally linked binary puts the C start-up code ahead of it.
Go 1.26 no longer writes the text base into the pclntab header, so it is found, in order, from
the header (Go 1.20 to 1.25), the runtime's moduledata, the runtime.text symbol, or as the base
at which the most functions begin with a stack-check prologue.

As a module: load(path) returns a Binary; see its methods. Line tables and inlining trees are
decoded per function on demand, so funcs() stays fast on large binaries.
"""
import json
import mmap
import struct
import sys

PCLN_MAGIC_120 = 0xFFFFFFF1
PCDATA_INLTREEINDEX = 2
FUNCDATA_INLTREE = 3
FUNC_HEADER = 44                        # size of runtime._func before its pcdata array
BUILDINFO_MAGIC = b'\xff Go buildinf:'
STACK_CHECK = b'\x4d\x3b\x66\x10'       # cmp r12, [r14+0x10] (after lea r12, [rsp-n])
STACK_CHECK_RSP = b'\x49\x3b\x66\x10'   # cmp rsp, [r14+0x10]

# Word indexes of fields in runtime.moduledata (Go 1.20 and later).
MD_FUNCNAMETAB, MD_FTAB, MD_MINPC, MD_TEXT, MD_GOFUNC = 1, 16, 20, 22, 40


class Func:
    """One function: name, entry and end addresses, and the file and line of its declaration."""
    __slots__ = ('name', 'entry', 'end', 'file', 'start_line', '_off')

    def __init__(self, name, entry, end, file, start_line, off):
        self.name, self.entry, self.end = name, entry, end
        self.file, self.start_line, self._off = file, start_line, off

    def __repr__(self):
        return 'Func(%s, 0x%x-0x%x, %s:%d)' % (self.name, self.entry, self.end, self.file,
                                               self.start_line)


class Inl:
    """One inlined call. parent is the index of the enclosing inlined call in the same list, or
    -1 when the call is made by the function itself. pc_ranges are (start, end) offsets from the
    function's entry of the code that belongs to this call and not to a deeper one. start_line
    is the line on which the inlined function starts, and file its source file: the file the
    line table gives for the call's own code, or, when it has none, the file of the calls
    inlined into it ('' when neither is known)."""
    __slots__ = ('callee', 'call_file', 'call_line', 'parent', 'pc_ranges', 'start_line', 'file')

    def __init__(self, callee, call_file, call_line, parent, pc_ranges, start_line=0, file=''):
        self.callee, self.call_file, self.call_line = callee, call_file, call_line
        self.parent, self.pc_ranges = parent, pc_ranges
        self.start_line, self.file = start_line, file

    def __repr__(self):
        return 'Inl(%s at %s:%d, parent %d, %s)' % (self.callee, self.call_file, self.call_line,
                                                    self.parent, self.pc_ranges)


def _varint(d, p):
    v = shift = 0
    while True:
        b = d[p]
        p += 1
        v |= (b & 0x7F) << shift
        if b < 0x80:
            return v, p
        shift += 7


def _cstr(d, p):
    return d[p:d.find(b'\0', p)].decode('utf-8', 'replace')


class Binary:
    def __init__(self, path):
        with open(path, 'rb') as f:
            self.d = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
        d = self.d
        if d[:4] != b'\x7fELF' or d[4] != 2 or d[5] != 1:
            raise ValueError('%s: not a little-endian ELF64 file' % path)
        self._read_elf()
        self._read_pclntab()
        self._md = self._find_moduledata()
        self.text = self._text_base()
        self._funcs = None

    # ELF

    def _read_elf(self):
        d = self.d
        phoff, shoff = struct.unpack_from('<QQ', d, 0x20)
        phentsize, phnum, shentsize, shnum, shstrndx = struct.unpack_from('<HHHHH', d, 0x36)
        self.loads = []                 # (vaddr, offset, filesz, flags) of PT_LOAD segments
        for i in range(phnum):
            ptype, flags, off, vaddr, _, filesz = struct.unpack_from('<IIQQQQ', d,
                                                                     phoff + i * phentsize)
            if ptype == 1:
                self.loads.append((vaddr, off, filesz, flags))
        raw = [struct.unpack_from('<IIQQQQ', d, shoff + i * shentsize) for i in range(shnum)]
        self.sections = {}              # name -> (type, addr, offset, size)
        if raw and shstrndx < len(raw):
            stroff = raw[shstrndx][4]
            for nm, ty, _, addr, off, size in raw:
                self.sections[_cstr(d, stroff + nm)] = (ty, addr, off, size)

    def _off(self, va):
        for vaddr, off, filesz, _ in self.loads:
            if vaddr <= va < vaddr + filesz:
                return off + va - vaddr
        raise KeyError('address 0x%x is not in the file' % va)

    def _u64(self, va):
        return struct.unpack_from('<Q', self.d, self._off(va))[0]

    def _symbol(self, want):
        sec = self.sections.get('.symtab')
        if not sec:
            return None
        strtab = self.sections.get('.strtab')
        if not strtab:
            return None
        d, wantb = self.d, want.encode()
        for p in range(sec[2], sec[2] + sec[3], 24):
            nm, = struct.unpack_from('<I', d, p)
            q = strtab[2] + nm
            if d[q:q + len(wantb) + 1] == wantb + b'\0':
                return struct.unpack_from('<Q', d, p + 8)[0]
        return None

    # pclntab

    def _read_pclntab(self):
        d = self.d
        sec = self.sections.get('.gopclntab')
        if sec:
            self.pcln_va, p = sec[1], sec[2]
        else:
            p = self._search_pclntab()
            self.pcln_va = next(va + p - off for va, off, sz, _ in self.loads
                                if off <= p < off + sz)
        magic, = struct.unpack_from('<I', d, p)
        if magic != PCLN_MAGIC_120:
            raise ValueError('pclntab magic 0x%x: not a Go 1.20 or later binary' % magic)
        if d[p + 7] != 8:
            raise ValueError('pointer size %d: only 64-bit binaries are read' % d[p + 7])
        self.quantum = d[p + 6]
        (self.nfunc, _, self.header_text, funcname, cu, filetab, pctab,
         pcln) = struct.unpack_from('<QQQQQQQQ', d, p + 8)
        self.pcln = p
        self.funcnametab, self.cutab = p + funcname, p + cu
        self.filetab, self.pctab, self.ftab = p + filetab, p + pctab, p + pcln

    def _search_pclntab(self):
        d, start = self.d, 0
        pat = struct.pack('<I', PCLN_MAGIC_120) + b'\0\0'
        while True:
            p = d.find(pat, start)
            if p < 0:
                raise ValueError('no Go 1.20 or later pclntab found')
            if d[p + 6] in (1, 2, 4) and d[p + 7] == 8:
                return p
            start = p + 1

    def _entryoff(self, i):
        return struct.unpack_from('<I', self.d, self.ftab + 8 * i)[0]

    # The text base

    def _find_moduledata(self):
        """Word address of runtime.firstmoduledata, found by its pointer to the pclntab."""
        d = self.d
        want = struct.pack('<Q', self.pcln_va)
        names = struct.pack('<Q', self.pcln_va + self.funcnametab - self.pcln)
        ftab = struct.pack('<QQ', self.pcln_va + self.ftab - self.pcln, self.nfunc + 1)
        for vaddr, off, filesz, flags in self.loads:
            if flags & 1:               # executable: not data
                continue
            p = off
            while True:
                p = d.find(want, p, off + filesz)
                if p < 0:
                    break
                if (p - off) % 8 == 0 and d[p + 8 * MD_FUNCNAMETAB:p + 8 * MD_FUNCNAMETAB + 8] \
                        == names and d[p + 8 * MD_FTAB:p + 8 * MD_FTAB + 16] == ftab:
                    return vaddr + p - off
                p += 1
        return None

    def _md_word(self, i):
        return self._u64(self._md + 8 * i)

    def _text_from_moduledata(self):
        if self._md is None:
            return None
        text = self._md_word(MD_TEXT)
        if text and self._md_word(MD_MINPC) == text + self._entryoff(0):
            return text
        return None

    def _text_from_symbol(self):
        return self._symbol('runtime.text')

    def _text_from_prologues(self):
        """The base, within 4 KB of the executable segment's start, at which the most of a
        sample of functions begin with a stack check."""
        d = self.d
        exe = [s for s in self.loads if s[3] & 1]
        if not exe:
            return None
        vaddr, off, filesz, _ = exe[0]
        text = self.sections.get('.text')
        if text and vaddr <= text[1] < vaddr + filesz:
            vaddr, off, filesz = text[1], text[2], text[3]
        step = max(1, self.nfunc // 400)
        sample = [self._entryoff(i) for i in range(0, self.nfunc, step)]
        best, best_hits = None, 0
        for k in range(0, 4096):
            hits = 0
            for e in sample:
                p = off + k + e
                if d[p:p + 4] == STACK_CHECK_RSP or d[p + 5:p + 9] == STACK_CHECK or \
                        d[p + 8:p + 12] == STACK_CHECK:
                    hits += 1
            if hits > best_hits:
                best, best_hits = vaddr + k, hits
        return best

    def _text_base(self):
        for way in (lambda: self.header_text or None, self._text_from_moduledata,
                    self._text_from_symbol, self._text_from_prologues):
            text = way()
            if text:
                return text
        raise ValueError('cannot find the text base')

    # Functions

    def _func_fields(self, off):
        """(nameOff, pcfile, pcln, npcdata, cuOffset, startLine, nfuncdata) of a _func."""
        f = struct.unpack_from('<iiiIIIIIIiBBBB', self.d, off)
        return f[1], f[5], f[6], f[7], f[8], f[9], f[13]

    def _pcvalues(self, table):
        """Decode a pc-value table: [(start offset, value), ...] and the end offset."""
        d, p, q = self.d, self.pctab + table, self.quantum
        pc, val, out = 0, -1, []
        while True:
            uv, p = _varint(d, p)
            if uv == 0 and out:
                return out, pc
            val += (uv >> 1) ^ -(uv & 1)
            pcd, p = _varint(d, p)
            out.append((pc, val))
            pc += pcd * q

    def _file(self, cu, fileno):
        if fileno < 0:
            return '?'
        fo, = struct.unpack_from('<I', self.d, self.cutab + 4 * (cu + fileno))
        return '?' if fo == 0xFFFFFFFF else _cstr(self.d, self.filetab + fo)

    def funcs(self):
        """Every function, in address order."""
        if self._funcs is None:
            d, out, ftab = self.d, [], self.ftab
            entries = struct.unpack_from('<%dI' % (2 * self.nfunc + 2), d, ftab)
            for i in range(self.nfunc):
                entryoff, funcoff = entries[2 * i], entries[2 * i + 1]
                nameoff, pcfile, _, _, cu, start_line, _ = self._func_fields(ftab + funcoff)
                entry, next_entry = self.text + entryoff, self.text + entries[2 * i + 2]
                file, end = '?', next_entry
                if pcfile:
                    vals, size = self._pcvalues(pcfile)
                    file = self._file(cu, vals[0][1])
                    if 0 < size <= next_entry - entry:
                        end = entry + size
                out.append(Func(_cstr(d, self.funcnametab + nameoff), entry, end, file,
                                start_line, ftab + funcoff))
            self._funcs = out
        return self._funcs

    def func(self, name):
        return next(f for f in self.funcs() if f.name == name)

    def code(self, func):
        p = self._off(func.entry)
        return bytes(self.d[p:p + func.end - func.entry])

    @staticmethod
    def _at(vals, pc):
        """The value of a decoded pc-value table at offset pc."""
        v = -1
        for start, val in vals:
            if start > pc:
                break
            v = val
        return v

    def lines(self, func):
        """[(offset in function, file, line)] at the start and at every change of file or line."""
        _, pcfile, pcln, _, cu, _, _ = self._func_fields(func._off)
        if not pcfile or not pcln:      # a marker such as go:textfipsstart has no tables
            return []
        files, _ = self._pcvalues(pcfile)
        lines, _ = self._pcvalues(pcln)
        out = []
        for pc in sorted({s for s, _ in files} | {s for s, _ in lines}):
            row = (pc, self._file(cu, self._at(files, pc)), self._at(lines, pc))
            if not out or out[-1][1:] != row[1:]:
                out.append(row)
        return out

    def inltree(self, func):
        """The inlined calls of a function, indexed as in its inlining tree."""
        d = self.d
        _, pcfile, pcln, npcdata, cu, _, nfuncdata = self._func_fields(func._off)
        pcdata = func._off + FUNC_HEADER
        if npcdata <= PCDATA_INLTREEINDEX or nfuncdata <= FUNCDATA_INLTREE or not pcfile:
            return []
        idxtab, = struct.unpack_from('<I', d, pcdata + 4 * PCDATA_INLTREEINDEX)
        treeoff, = struct.unpack_from('<I', d, pcdata + 4 * npcdata + 4 * FUNCDATA_INLTREE)
        if idxtab == 0 or treeoff == 0xFFFFFFFF:
            return []
        if self._md is None:
            raise ValueError('no moduledata found, so no go:func.* base for the inlining tree')
        tree = self._off(self._md_word(MD_GOFUNC) + treeoff)
        idx, size = self._pcvalues(idxtab)
        files, _ = self._pcvalues(pcfile)
        lines, _ = self._pcvalues(pcln)
        ranges = {}
        for n, (start, val) in enumerate(idx):
            if val >= 0:
                stop = idx[n + 1][0] if n + 1 < len(idx) else size
                ranges.setdefault(val, []).append((start, stop))
        count = max(ranges, default=-1) + 1
        out, n = [], 0
        while n < count:
            _, nameoff, parent_pc, start_line = struct.unpack_from('<Iiii', d, tree + 16 * n)
            parent = self._at(idx, parent_pc)
            count = max(count, parent + 1)
            own = ranges.get(n, [])
            out.append(Inl(_cstr(d, self.funcnametab + nameoff),
                           self._file(cu, self._at(files, parent_pc)), self._at(lines, parent_pc),
                           parent, own, start_line,
                           self._file(cu, self._at(files, own[0][0])) if own else ''))
            n += 1
        for i in out:                   # no code of its own: its callees were called from its file
            if i.parent >= 0 and not out[i.parent].file:
                out[i.parent].file = i.call_file
        return out

    # Build info

    def buildinfo(self):
        """{'go': version, 'path': main package, 'settings': {key: value},
        'deps': [(module path, version, sum)]} from the .go.buildinfo blob."""
        d = self.d
        sec = self.sections.get('.go.buildinfo')
        p = sec[2] if sec else d.find(BUILDINFO_MAGIC)
        if p < 0 or d[p:p + 14] != BUILDINFO_MAGIC:
            raise ValueError('no Go build info')
        if not d[p + 15] & 2:
            raise ValueError('build info without inline strings: older than Go 1.18')
        p += 32
        n, p = _varint(d, p)
        version = d[p:p + n].decode()
        n, p = _varint(d, p + n)
        mod = d[p:p + n].decode('utf-8', 'replace')
        if len(mod) >= 33 and mod[-17] == '\n':
            mod = mod[16:-16]
        info = {'go': version, 'path': '', 'settings': {}, 'deps': []}
        for line in mod.split('\n'):
            f = line.split('\t')
            if f[0] == 'path' and len(f) > 1:
                info['path'] = f[1]
            elif f[0] == 'dep' and len(f) > 2:
                info['deps'].append((f[1], f[2], f[3] if len(f) > 3 else ''))
            elif f[0] == 'build' and len(f) > 1:
                key, _, value = line[len('build\t'):].partition('=')
                info['settings'][_unquote(key)] = _unquote(value)
        return info


def _unquote(s):
    if s.startswith('"'):
        try:
            return json.loads(s)
        except ValueError:
            pass
    return s


def load(path):
    return Binary(path)


def main(argv):
    if len(argv) < 3 or argv[1] not in ('funcs', 'lines', 'inltree', 'buildinfo') or \
            (argv[1] in ('lines', 'inltree') and len(argv) < 4):
        sys.exit(__doc__.split('\n\n')[1])
    b = load(argv[2])
    if argv[1] == 'funcs':
        for f in b.funcs():
            print('%x %x %s %s:%d' % (f.entry, f.end, f.name, f.file, f.start_line))
    elif argv[1] == 'lines':
        for off, file, line in b.lines(b.func(argv[3])):
            print('+%#x %s:%d' % (off, file, line))
    elif argv[1] == 'inltree':
        for n, i in enumerate(b.inltree(b.func(argv[3]))):
            print('%d %s called at %s:%d parent %d %s' % (
                n, i.callee, i.call_file, i.call_line, i.parent,
                ' '.join('%#x-%#x' % r for r in i.pc_ranges)))
    else:
        info = b.buildinfo()
        print('go', info['go'])
        print('path', info['path'])
        for k, v in info['settings'].items():
            print('build %s=%s' % (k, v))
        for dep in info['deps']:
            print('dep', *dep)


if __name__ == '__main__':
    main(sys.argv)
