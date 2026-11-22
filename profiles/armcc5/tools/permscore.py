"""The fast scorer under armpermute2.py: one function of a compiled object against the image,
read in-process (orcobj.py, no fromelf), placed at the function's map address.

    python3 permscore.py [--config PATH] SRC FUNC OBJ [--fnscore] [--diff]

Target (built once): the image's bytes at FUNC's map row, the byte addresses its code reads as
literals and tables (fnscore.image_data), its instruction tokens, and from a base object of the
same unit the placement of every section other than code (armmatch.place, as match.py and
objplace place them) and the symbol table the link would see.

score(object bytes): FUNC's bytes are cut from its section at its symbol (to the next code
symbol), every relocation inside them is applied with FUNC at its map address, a code symbol
the map knows at its map address (so a call or literal naming a neighbour is right even if the
neighbour moved), any other symbol as objplace links it. Then:

- exact: the placed bytes equal the image's bytes (same size). This is the comparison
  match.py and objplace make for those bytes, nothing masked;
- otherwise diff: the number of differing instructions after an instruction-aligned diff, as
  fnscore counts them (branch targets as instruction indexes, then each aligned branch must
  reach the instruction the alignment pairs with the target), but with bl targets and literal
  VALUES compared, not masked (fnscore masks both, which hid a wrong helper call), and
  pc-relative displacements dropped (so a cut file whose pool sits elsewhere still scores);
  size difference reported apart. --fnscore masks bl targets and literal values as fnscore
  does, to check the two counts agree.

The hot path costs a byte compare when the bytes are equal; instructions are split with the
Thumb-2 length rule and only branches, literal loads and adr are decoded (no capstone); results
are cached by object hash. Neighbours: a function of the unit that the base build placed exactly
and whose code (raw bytes and relocations) differs in the variant is placed at its own map
address and compared with the image; one that no longer matches is reported, so a declaration
edit that breaks a neighbour is seen.

Two counts (the trust key): the plain count above can fall while the code gets further from the
image, because one wrong instruction (an `sxtb` a retyped temporary adds) can shift a dozen
register ties elsewhere into place. So score also counts the structural difference: the same
aligned diff over instructions with their registers masked (mnemonic without its width and
without the flag suffix a narrow encoding forces, operand kinds, immediates, memory offsets,
shifts, literal values, call targets, branch structure; b and b.w, and a conditional branch of
either width, are one kind; sp and pc stay themselves; a plain register-to-register mov is left
out, being a tie the allocator did not make). Result.key orders by the structural count first,
then by the rest of the plain count (the register level): an instruction the image does not
have, lacks or has in another place cannot be paid for with register ties. Result.flat (the
permuter's --flat-score) gives the old single count. Literal pools and pc-relative displacements
are in neither count (data is skipped, branch targets are instruction indexes), so a pool or a
branch that moved only because the size changed upstream does not count as structure.

Source lines of the differing instructions (hot_lines, for the permuter's focused site choice):
MDK-Lite's armcc refuses -S, --asm and --interleave ("not available with your license"), so the
lines come from a second compile of the same text with -g: the object's .debug_line programs are
read in-process (line_rows) and each differing instruction of the object's FUNC takes the line
of the last row at or before it. The mapping is only used when FUNC places to the same bytes
from the -g object as from the plain one."""
import difflib, hashlib, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
for p in (HERE, os.path.join(EXHUME, 'tools')):
    if p not in sys.path: sys.path.insert(0, p)
import orcobj, objplace          # noqa: E402


def thumb_len(hw):
    return 4 if (hw >> 11) in (0x1D, 0x1E, 0x1F) else 2


def _sext(v, bits):
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def _bl_target(h1, h2, addr):
    return addr + 4 + objplace._bl_get(h1, h2)


def rows_of(buf, at, data, word, fn_mask=False):
    """[(address, masked token, branch target address or None)] of the Thumb code in buf at address at.
    data: set of byte addresses that are literals or tables (skipped; a table becomes one
    token). word(addr): the 32-bit literal at addr, or None."""
    out = []
    off, n = 0, len(buf)
    while off + 1 < n:
        a = at + off
        if a in data:
            # a run of data: tables after tbb/tbh are kept as one token of their bytes
            e = off
            while e < n and at + e in data: e += 1
            if out and out[-1][1][0] in ('tbb', 'tbh'):
                out.append((a, ('table', bytes(buf[off:e])), None))
            off = e; continue
        hw = buf[off] | buf[off + 1] << 8
        if hw == 0 and a % 4 == 2 and a + 2 in data:                       # padding before a literal
            off += 2; continue
        if thumb_len(hw) == 2 or off + 3 >= n:
            if hw & 0xF000 == 0xD000 and (hw >> 8) & 0xF < 0xE:            # b<cond>
                out.append((a, ('bc', (hw >> 8) & 0xF), a + 4 + _sext(hw & 0xFF, 8) * 2))
            elif hw & 0xF800 == 0xE000:                                    # b
                out.append((a, ('b',), a + 4 + _sext(hw & 0x7FF, 11) * 2))
            elif hw & 0xF500 == 0xB100:                                    # cbz / cbnz
                out.append((a, ('cb', hw & 0xFD07), a + 4 + ((((hw >> 9) & 1) << 6) | (((hw >> 3) & 31) << 1))))
            elif hw & 0xF800 == 0x4800:                                    # ldr rt, [pc, #]
                lit = ((a + 4) & ~3) + (hw & 0xFF) * 4
                out.append((a, ('ldr', (hw >> 8) & 7, None if fn_mask else word(lit)), None))
            elif hw & 0xF800 == 0xA000:                                    # adr
                out.append((a, ('adr', (hw >> 8) & 7), None))
            else:
                out.append((a, (hw,), None))
            off += 2; continue
        h2 = buf[off + 2] | buf[off + 3] << 8
        if hw & 0xF800 == 0xF000 and h2 & 0x8000:
            k = h2 & 0xD000
            if k == 0xD000:                                                # bl
                out.append((a, ('bl', None if fn_mask else _bl_target(hw, h2, a)), None))
            elif k == 0x9000:                                              # b.w
                out.append((a, ('bw',), _bl_target(hw, h2, a)))
            elif k == 0x8000 and (hw >> 6) & 0xF < 0xE:                    # b<cond>.w
                out.append((a, ('bcw', (hw >> 6) & 0xF), a + 4 + objplace._b19_get(hw, h2)))
            else:
                out.append((a, (hw, h2), None))
        elif hw & 0xFF7F == 0xF85F:                                        # ldr.w rt, [pc, #]
            k = h2 & 0xFFF
            lit = ((a + 4) & ~3) + (k if hw & 0x80 else -k)
            out.append((a, ('ldrw', h2 >> 12, None if fn_mask else word(lit)), None))
        elif hw & 0xFBFF in (0xF20F, 0xF2AF) and not h2 & 0x8000:          # adr.w (addw/subw pc)
            out.append((a, ('adrw', (h2 >> 8) & 0xF), None))
        elif hw == 0xE8DF and h2 & 0xFFE0 in (0xF000, 0xF010):            # tbb / tbh
            out.append((a, ('tbh' if h2 & 0x10 else 'tbb', h2 & 0xF), None))
        else:
            out.append((a, (hw, h2), None))
        off += 4
    return out


_CS = None
_STOK = {}
_ALU = ('add', 'sub', 'and', 'orr', 'eor', 'bic', 'lsl', 'lsr', 'asr', 'ror', 'adc', 'sbc', 'mul', 'rsb', 'orn')
_FLAG = {m + 's': m for m in _ALU + ('mov', 'mvn', 'neg')}


def _stok(tok):
    """The structural token of a plain instruction (its halfwords): mnemonic and operand
    shape with the registers masked; None for a register-to-register mov. Without capstone,
    or for halfwords it does not decode, the halfwords themselves."""
    global _CS
    s = _STOK.get(tok, 0)
    if s != 0: return s
    if _CS is None:
        try:
            import capstone
            _CS = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
            _CS.detail = True
        except Exception:
            _CS = False
    s = tok
    if _CS:
        from capstone import arm
        insn = next(_CS.disasm(b''.join(struct.pack('<H', h) for h in tok), 0), None)
        if insn is not None:
            mn = insn.mnemonic
            if mn.endswith('.w'): mn = mn[:-2]
            mn = _FLAG.get(mn, mn)
            if mn.startswith('it') and not insn.operands:
                s = tok                            # an IT instruction has no register: itself
            elif mn in ('push', 'pop', 'ldm', 'stm', 'ldmdb', 'stmdb'):
                s = (mn,)                          # which registers are saved is the allocator's doing
            else:
                ops = []
                for op in insn.operands:
                    if op.type == arm.ARM_OP_REG:
                        k = 'S' if op.reg == arm.ARM_REG_SP else 'P' if op.reg == arm.ARM_REG_PC else 'r'
                        ops.append((k, op.shift.type, op.shift.value) if op.shift.type else k)
                    elif op.type == arm.ARM_OP_IMM:
                        ops.append(('i', op.imm))
                    elif op.type == arm.ARM_OP_MEM:
                        b = 'S' if op.mem.base == arm.ARM_REG_SP else 'P' if op.mem.base == arm.ARM_REG_PC else 'r'
                        ops.append(('m', b, bool(op.mem.index), op.mem.disp, op.shift.type, op.shift.value))
                    else:
                        ops.append(('?', op.type))
                if mn == 'mov' and ops == ['r', 'r']: s = None
                else:
                    if mn in _ALU and len(ops) == 2: ops.insert(0, ops[0] if not isinstance(ops[0], tuple) else 'r')   # `adds r0, #1` is `add r0, r0, #1`
                    s = (mn,) + tuple(ops)
    _STOK[tok] = s
    return s


def struct_rows(rows):
    """rows_of's rows as structural rows [(address, structural token, branch target)]: registers
    masked, branch widths made one kind, register-to-register movs left out."""
    out = []
    for a, t, tgt in rows:
        h = t[0]
        if isinstance(h, int):
            s = _stok(t)
            if s is None: continue
        elif h in ('bc', 'bcw'): s = ('bc', t[1])
        elif h in ('b', 'bw'): s = ('b',)
        elif h == 'cb': s = ('cb', (t[1] >> 11) & 1)
        elif h in ('ldr', 'ldrw'): s = ('ldr=', t[2])
        elif h in ('adr', 'adrw'): s = ('adr',)
        elif h in ('tbb', 'tbh'): s = ('tb',)
        elif h == 'table': s = ('table',)
        else: s = t
        out.append((a, s, tgt))
    return out


def normalise_struct(rows):
    """As normalise, for structural rows: a branch target becomes the index of the first row at
    or after it (the row it names may be a mov that was left out)."""
    import bisect
    addrs = [r[0] for r in rows]
    masked, labels = [], []
    for a, t, tgt in rows:
        if tgt is None or not addrs or not addrs[0] <= tgt <= addrs[-1]:
            masked.append(t if tgt is None else t + (tgt,)); labels.append(None)
        else:
            masked.append(t); labels.append(bisect.bisect_left(addrs, tgt))
    return masked, labels


def normalise(rows, mask_ext=False):
    """rows [(addr, masked token, target)] -> (masked tokens, labels): branch targets that are
    the address of a row become that row's index (label); others stay in the token."""
    index = {a: i for i, (a, _, _) in enumerate(rows)}
    masked, labels = [], []
    for a, t, tgt in rows:
        if tgt is None:
            masked.append(t); labels.append(None)
        elif tgt in index:
            masked.append(t); labels.append(index[tgt])
        else:
            masked.append(t if mask_ext else t + (tgt,)); labels.append(None)
    return masked, labels


def diff_count(exp, got):
    """(count, opcodes) between two normalised listings ((masked, labels) pairs), as
    fnscore.compare counts: aligned on masked tokens, then aligned branches must agree."""
    em, el = exp
    gm, gl = got
    # trim the common prefix and suffix first: difflib is quadratic on the middle only
    lo = 0
    lim = min(len(em), len(gm))
    while lo < lim and em[lo] == gm[lo]: lo += 1
    hi = 0
    while hi < lim - lo and em[len(em) - 1 - hi] == gm[len(gm) - 1 - hi]: hi += 1
    blocks = [('equal', 0, lo, 0, lo)]
    mid = difflib.SequenceMatcher(None, em[lo:len(em) - hi], gm[lo:len(gm) - hi], autojunk=False).get_opcodes()
    blocks += [(t, i1 + lo, i2 + lo, j1 + lo, j2 + lo) for t, i1, i2, j1, j2 in mid]
    if hi: blocks.append(('equal', len(em) - hi, len(em), len(gm) - hi, len(gm)))
    pair = {}
    for tag, i1, i2, j1, j2 in blocks:
        if tag == 'equal':
            for k in range(i2 - i1): pair[i1 + k] = j1 + k
    ops = []
    for tag, i1, i2, j1, j2 in blocks:
        if tag != 'equal':
            ops.append((tag, i1, i2, j1, j2)); continue
        for k in range(i2 - i1):
            a, b = el[i1 + k], gl[j1 + k]
            if (a is None) != (b is None) or (a is not None and pair.get(a) != b):
                ops.append(('replace', i1 + k, i1 + k + 1, j1 + k, j1 + k + 1))
    return sum(max(i2 - i1, j2 - j1) for _, i1, i2, j1, j2 in ops), ops


def _uleb(d, p):
    v = sh = 0
    while True:
        c = d[p]; p += 1
        v |= (c & 0x7F) << sh; sh += 7
        if not c & 0x80: return v, p


def _sleb(d, p):
    v = sh = 0
    while True:
        c = d[p]; p += 1
        v |= (c & 0x7F) << sh; sh += 7
        if not c & 0x80:
            return (v - (1 << sh) if c & 0x40 else v), p


def _cstr(d, p):
    e = d.index(b'\0', p)
    return d[p:e].decode('latin1'), e + 1


def line_rows(o, raw):
    """{code section index: sorted [(offset, line, file name)]} from every .debug_line section
    of an object compiled with -g (DWARF 2 and 3 line programs; a DW_LNE_set_address takes its
    section and base from the relocation on its operand)."""
    out = {}
    for idx, sec in o.sections.items():
        if sec.name != '.debug_line' or sec.type != 'SHT_PROGBITS': continue
        d = raw[sec.offset:sec.offset + sec.size]
        rel = {r.offset: r for r in o.relocs.get(idx, [])}
        p = 0
        while p + 10 <= len(d):
            length, ver, hlen = struct.unpack_from('<IHI', d, p)
            end, prog = p + 4 + length, p + 10 + hlen
            if ver not in (2, 3) or end > len(d): break
            min_inst, is_stmt, line_base, line_range, op_base = struct.unpack_from('<BBbBB', d, p + 10)
            oplen = d[p + 15:p + 15 + op_base - 1]
            q = p + 15 + op_base - 1
            while d[q]: _, q = _cstr(d, q)                 # include directories
            q += 1
            files = ['']
            while d[q]:
                name, q = _cstr(d, q)
                for _ in range(3): _, q = _uleb(d, q)
                files.append(name)
            q = prog
            target, addr, line, f = None, 0, 1, 1

            def row():
                if target is not None:
                    out.setdefault(target, []).append((addr, line, files[f] if f < len(files) else ''))
            while q < end:
                op = d[q]; q += 1
                if op >= op_base:
                    adj = op - op_base
                    addr += (adj // line_range) * min_inst; line += line_base + adj % line_range
                    row()
                elif op == 0:
                    n, q = _uleb(d, q)
                    sub = d[q]
                    if sub == 1:                             # end of sequence
                        target, addr, line, f = None, 0, 1, 1
                    elif sub == 2:                           # set address: the operand is relocated
                        r = rel.get(q + 1)
                        sym = o.symbols.get(r.sym) if r is not None else None
                        add = struct.unpack_from('<I', d, q + 1)[0]
                        if sym is not None and isinstance(sym.sec, int):
                            target, addr = sym.sec, (sym.value & ~1) + add
                        else:
                            target, addr = None, add
                    elif sub == 3:                           # define file
                        name, _ = _cstr(d, q + 1); files.append(name)
                    q += n
                elif op == 1: row()
                elif op == 2:
                    v, q = _uleb(d, q); addr += v * min_inst
                elif op == 3:
                    v, q = _sleb(d, q); line += v
                elif op == 4:
                    f, q = _uleb(d, q)
                elif op == 8: addr += ((255 - op_base) // line_range) * min_inst
                elif op == 9:
                    addr += struct.unpack_from('<H', d, q)[0]; q += 2
                else:
                    for _ in range(oplen[op - 1] if op - 1 < len(oplen) else 0): _, q = _uleb(d, q)
            p = end
    for rows in out.values(): rows.sort(key=lambda r: r[0])
    return out


class Result:
    __slots__ = ('ok', 'exact', 'diff', 'size', 'want_size', 'neighbours', 'error', 'code', 'chash', 'struct')
    flat = False                 # True: the old single count (no structural level in the key)

    def __init__(self, ok=False, exact=False, diff=None, size=None, want_size=None, neighbours=(), error='', code=b'', struct=None):
        self.ok, self.exact, self.diff, self.size, self.want_size = ok, exact, diff, size, want_size
        self.neighbours, self.error, self.code = neighbours, error, code
        self.struct = diff if struct is None else min(struct, diff)      # the structural part of diff
        # what the result hangs on: FUNC's placed bytes and the neighbours that changed (the object's
        # own bytes hold the path it was compiled at, so they differ from one work folder to the next)
        self.chash = hashlib.blake2b(code + repr(neighbours).encode(), digest_size=12).hexdigest() if ok else ''

    @property
    def size_ok(self):
        return self.size == self.want_size

    def key(self):
        """Sort key: exact first, then right size, then the structural difference, then the
        rest of the differing instructions (the register level). With flat the count is one."""
        if not self.ok: return (3, 0, 0, 10 ** 9)
        if self.exact and not self.neighbours: return (0, 0, 0, 0)
        st = 0 if Result.flat else self.struct
        return (1 if self.size_ok and not self.neighbours else 2, abs(self.size - self.want_size), st, self.diff - st)

    def text(self):
        if not self.ok: return 'FAILED ' + self.error
        s = 'EXACT' if self.exact else f'{self.diff}' if Result.flat else f'{self.diff} (structure {self.struct}, registers {self.diff - self.struct})'
        if not self.size_ok: s += f' size {self.size:#x}/{self.want_size:#x}'
        if self.neighbours: s += ' neighbours changed: ' + ','.join(self.neighbours)
        return s


class Target:
    """What one function is scored against (see the module docstring)."""

    def __init__(self, cfg, src_text, func, base_raw, fn_mask=False):
        import armmap, armmatch, ehex, fnscore
        self.func, self.fn_mask = func, fn_mask
        self.m = m = armmap.Map.from_dir(cfg.map)
        self.img = img = ehex.Image(cfg.image)
        row = m.by_name.get(func)
        if row is None: raise SystemExit(f'{func}: no map row')
        self.addr, self.want_size = row.addr, row.size
        self.want = img.bytes(row.addr, row.size)
        # literals and tables the image's code reads, decoded over the whole unit as fnscore
        # does (over the function alone a pool reached only from later code is missed)
        bo, _ = orcobj.read(raw=base_raw)
        unit = [m.by_name[s.name] for s in bo.symbols.values() if s.type == 'Code' and s.name in m.code_names]
        lo = min([r.addr for r in unit] + [row.addr]); hi = max([r.addr + r.size for r in unit] + [row.addr + row.size])
        self.idata = {a for a in fnscore.image_data(img, lo, hi) if row.addr <= a < row.addr + row.size}
        word = lambda a: struct.unpack('<I', img.bytes(a, 4))[0] if row.addr <= a < row.addr + row.size else None
        rows = rows_of(self.want, row.addr, self.idata, word, fn_mask)
        self.exp = normalise(rows, fn_mask)
        self.exp_srows = struct_rows(rows)
        self.exp_s = normalise_struct(self.exp_srows)
        # placement of the other sections, from the base object
        o, info = orcobj.read(raw=base_raw)
        extra = armmap.annotations(src_text)
        placements, defined, errors, unplaced = armmatch.place(info, m, extra, armmap.unused_dropped(src_text))
        # a code section that does not place (a function of the wrong size moves the ones after
        # it) is no obstacle: FUNC is placed at its own map address, its neighbours by the map
        code_names = {n for i, (n, _) in info.sections.items() if 'SHF_EXECINSTR' in (info.flags or {}).get(i, '')}
        errors = [e for e in errors if not any(f'section {n}:' in e or f'section {n} ' in e for n in code_names)]
        # a data section the image holds in several pieces (its symbols give different bases) is
        # resolved per symbol: a reference takes the base of the piece its target lies in
        self.extra = extra
        errors = [e for e in errors if 'its symbols disagree on its address' not in e
                  or any(f'section {n}:' in e for n in code_names)]
        if errors: raise SystemExit('base object does not place: ' + '; '.join(errors[:5]))
        bases, errs = objplace.bases_for(o, info, placements, unplaced)
        if errs: raise SystemExit('base object does not place: ' + '; '.join(errs[:5]))
        # as the link resolves a name: a map row's own name, then the source's at: lines, then
        # a renamed row's placeholder alias (so `g_XXXXXXXX` pinned by an at: line to another
        # address takes the pin, not the alias)
        self.table = {}
        ents = m.symdef_entries(set(defined), extra)
        for a, kind, n in ents:
            if n in m.by_name: self.table.setdefault(n, (a | 1 if kind == 'T' else a, kind))
        for n, a in extra.items():
            if n not in defined and n not in m.by_name:
                kind = 'D' if a >= 0x20000000 else 'T'
                self.table.setdefault(n, (a | 1 if kind == 'T' else a, kind))
        for a, kind, n in ents:
            self.table.setdefault(n, (a | 1 if kind == 'T' else a, kind))
        self.defined = defined
        # data-section bases by label (name, k-th of the name), so a variant whose section
        # indexes shift still finds them
        self.data_bases = {}
        for i, a in bases.items():
            s = o.sections[i]
            if 'SHF_EXECINSTR' not in s.flags: self.data_bases[self._label(o, i)] = a
        self.base_code = self._code_signature(o)
        # neighbours that are exact in the base build: a variant must keep them exact
        self.base_exact = set()
        for name in self.base_code:
            if name in m.by_name:
                try:
                    code, _, _ = self.place_function(o, name=name)
                    if code == img.bytes(m.by_name[name].addr, m.by_name[name].size): self.base_exact.add(name)
                except Exception:
                    pass
        self.cache = {}

    @staticmethod
    def _label(o, i):
        name = o.sections[i].name
        k = sum(1 for j in o.sections if j < i and o.sections[j].name == name)
        return name, k

    def _functions(self, o):
        """{name: (section index, start, end)} of the object's code symbols."""
        out = {}
        for idx, sec in o.sections.items():
            if 'SHF_EXECINSTR' not in sec.flags: continue
            funcs = sorted((s.value & ~1, s.name) for s in o.symbols.values() if s.sec == idx and s.type == 'Code')
            for k, (off, name) in enumerate(funcs):
                end = funcs[k + 1][0] if k + 1 < len(funcs) else len(sec.data)
                out[name] = (idx, off, end)
        return out

    def _code_signature(self, o):
        """{function: hash of its raw bytes and relocations} for every code symbol."""
        out = {}
        for name, (idx, off, end) in self._functions(o).items():
            if name == self.func: continue
            rel = tuple((r.offset - off, r.tname, r.sname) for r in o.relocs.get(idx, []) if off <= r.offset < end)
            out[name] = hash((bytes(o.sections[idx].data[off:end]), rel))
        return out

    def _data_base(self, o, sec, target=None):
        """A data section's address: from a symbol it defines that the base build placed (a
        cut file may number or split its sections differently), else by its label. A section
        the image holds in pieces has no single base: with target (an offset in the section) the
        base is that of the piece holding it, from the nearest symbol at or below it that has
        an address."""
        for x in o.symbols.values():
            if x.sec == sec and x.type == 'Data' and x.name in self.defined:
                return self.defined[x.name] - x.value
        lab = self._label(o, sec)
        if lab in self.data_bases: return self.data_bases[lab]
        if target is not None:
            best = None
            for x in o.symbols.values():
                if x.sec == sec and x.type == 'Data' and x.value <= target:
                    a = self.m.address(x.name, self.extra)
                    if a is not None and (best is None or x.value > best[0]): best = (x.value, a)
            if best: return best[1] - best[0]
        raise LookupError(f'section {lab} not placed')

    def place_function(self, o, funcs=None, name=None):
        """(placed bytes of FUNC (or of function name), data byte addresses, address) or raises."""
        funcs = funcs or self._functions(o)
        name = name or self.func
        if name not in funcs: raise LookupError(f'{name} not in the object')
        idx, off, end = funcs[name]
        sec = o.sections[idx]
        base = (self.addr if name == self.func else self.m.by_name[name].addr) - off
        b = bytearray(sec.data)
        m = self.m
        states = objplace.mapping_states(o, idx)
        for r in o.relocs.get(idx, []):
            if not off <= r.offset < end: continue
            s = o.symbols.get(r.sym)
            if s is None: raise LookupError(f'relocation to symbol #{r.sym}')
            if s.type == 'Code' and s.name in m.by_name:
                S, T = m.by_name[s.name].addr, s.value & 1 or 1
            elif isinstance(s.sec, int) and not (s.bind == 'Wk' and s.name in self.table):
                if s.sec == idx:
                    S, T = base + (s.value & ~1), (s.value & 1 if s.type == 'Code' else 0)
                else:
                    add = struct.unpack_from('<I', b, r.offset)[0] if r.tname == 'R_ARM_ABS32' else 0
                    S, T = self._data_base(o, s.sec, (s.value + add) & 0xFFFFFFFF) + s.value, 0
            elif s.sec == 'Abs':
                S, T = s.value, 0
            else:
                if s.name not in self.table: raise LookupError(f'unresolved symbol {s.name}')
                v, kind = self.table[s.name]
                S, T = (v & ~1, 1) if kind == 'T' else (v, 0)
            if r.tname == 'R_ARM_NONE': continue
            e = objplace.apply(r, b, S, T, base + r.offset)
            if e: raise LookupError(f'{r.tname} {s.name}: {e}')
        data = set()
        st = [(v, n) for v, n in states if v < end]
        cur = None; prev = off
        for v, n in st + [(end, None)]:
            if v <= off: cur = n; continue
            if cur == '$d': data.update(range(base + prev, base + v))
            cur, prev = n, v
        return bytes(b[off:end]), data, base + off

    def score(self, raw):
        h = hashlib.blake2b(raw, digest_size=16).digest()
        r = self.cache.get(h)
        if r is not None: return r
        try:
            o = self._read(raw)
            funcs = self._functions(o)
            code, data, at = self.place_function(o, funcs)
        except Exception as e:
            r = Result(error=f'{type(e).__name__}: {e}')
            self.cache[h] = r
            return r
        neigh = []
        for n, sig in self._code_signature(o).items():
            if n in self.base_exact and self.base_code[n] != sig:
                try:
                    c2, _, _ = self.place_function(o, funcs, name=n)
                    row = self.m.by_name[n]
                    if c2 != self.img.bytes(row.addr, row.size): neigh.append(n)
                except Exception:
                    neigh.append(n)
        neigh = tuple(sorted(neigh))
        if code == self.want:
            r = Result(True, True, 0, len(code), self.want_size, neigh, code=code)
        else:
            def word(a):
                k = a - at
                return struct.unpack_from('<I', code, k)[0] if 0 <= k <= len(code) - 4 else None
            rows = rows_of(code, at, data, word, self.fn_mask)
            got = normalise(rows, self.fn_mask)
            n, _ = diff_count(self.exp, got)
            st = 0 if Result.flat else diff_count(self.exp_s, normalise_struct(struct_rows(rows)))[0]
            if n == 0:
                # the same instructions, yet other bytes: a pc-relative displacement, an adr
                # offset or data no load names (a string). Count the differing halfwords so
                # that only equal bytes score 0.
                n = max(1, sum(1 for k in range(0, min(len(code), len(self.want)) - 1, 2)
                               if code[k:k + 2] != self.want[k:k + 2]))
            r = Result(True, False, n, len(code), self.want_size, neigh, code=code, struct=st)
        if len(self.cache) > 20000: self.cache.clear()
        self.cache[h] = r
        return r

    @staticmethod
    def _read(raw):
        o, _ = orcobj.read(raw=raw)
        return o

    def diff_offsets(self, raw):
        """(offsets from FUNC's start of the object's instructions that differ from the image, FUNC's
        placed bytes). An instruction the image has and the object lacks gives the object's
        instructions on both sides of the gap."""
        o = self._read(raw)
        code, data, at = self.place_function(o)

        def word(a):
            k = a - at
            return struct.unpack_from('<I', code, k)[0] if 0 <= k <= len(code) - 4 else None
        gr = rows_of(code, at, data, word, self.fn_mask)
        n, ops = diff_count(self.exp, normalise(gr, self.fn_mask))
        out = set()
        for _, i1, i2, j1, j2 in ops:
            for j in (range(j1, j2) if j2 > j1 else (j1 - 1, j1)):
                if 0 <= j < len(gr): out.add(gr[j][0] - at)
        return sorted(out), code

    def hot_lines(self, raw, raw_g, src_name=None):
        """Sorted source lines the differing instructions of FUNC come from, or None when they
        cannot be had: raw is the object scored, raw_g the same text compiled with -g, whose
        FUNC must place to the same bytes (so the line rows are those of the code scored).
        src_name: only rows of the file of this base name count (not a header's inline code)."""
        try:
            offs, code = self.diff_offsets(raw)
            og = self._read(raw_g)
            funcs = self._functions(og)
            code_g, _, _ = self.place_function(og, funcs)
            if code_g != code: return None
            idx, off, end = funcs[self.func]
            rows = [r for r in line_rows(og, raw_g).get(idx, []) if off <= r[0] < end]
        except Exception:
            return None
        if not rows: return None
        import bisect
        starts = [r[0] for r in rows]
        lines = set()
        for x in offs:
            k = bisect.bisect_right(starts, off + x) - 1
            if k < 0: continue
            _, line, name = rows[k]
            if src_name is None or os.path.basename(name.replace('\\', '/')) == src_name: lines.add(line)
        return sorted(lines)

    def struct_diff(self, raw, limit=30):
        """The structural differences as lines (`-` what the image has, `+` what the object has,
        registers masked), at most limit of them: the instructions that are wrong, missing or in
        another place, whatever the register ties."""
        try:
            o = self._read(raw)
            code, data, at = self.place_function(o)

            def word(a):
                k = a - at
                return struct.unpack_from('<I', code, k)[0] if 0 <= k <= len(code) - 4 else None
            gs = struct_rows(rows_of(code, at, data, word, self.fn_mask))
            n, ops = diff_count(self.exp_s, normalise_struct(gs))
        except Exception:
            return []

        def show(t):
            return ' '.join(x if isinstance(x, str) else hex(x) if isinstance(x, int) else '(' + ' '.join(str(y) for y in x) + ')' for x in t)
        out = []
        for _, i1, i2, j1, j2 in ops:
            out += [f'- {self.exp_srows[i][0]:08X} {show(self.exp_srows[i][1])}' for i in range(i1, i2)]
            out += [f'+ {gs[j][0]:08X} {show(gs[j][1])}' for j in range(j1, j2)]
            if len(out) >= limit: break
        return out[:limit]

    def listing_diff(self, raw):
        """Human-readable differing tokens (for --diff)."""
        o = self._read(raw)
        code, data, at = self.place_function(o)

        def word(a):
            k = a - at
            return struct.unpack_from('<I', code, k)[0] if 0 <= k <= len(code) - 4 else None
        er = rows_of(self.want, self.addr, self.idata,
                     lambda a: struct.unpack('<I', self.img.bytes(a, 4))[0], self.fn_mask)
        gr = rows_of(code, at, data, word, self.fn_mask)
        n, ops = diff_count(self.exp, normalise(gr, self.fn_mask))
        out = [f'{self.func}: {n} differing, size {len(code):#x}/{self.want_size:#x}']
        for _, i1, i2, j1, j2 in ops:
            out += ['  - %4d %08X %s' % (i, er[i][0], fmt(er[i])) for i in range(i1, i2)]
            out += ['  + %4d %08X %s' % (j, gr[j][0], fmt(gr[j])) for j in range(j1, j2)]
            out.append('')
        return '\n'.join(out)


def fmt(row):
    a, t, tgt = row
    def h(x): return ' '.join(f'{v:04X}' if isinstance(v, int) else str(v) for v in x)
    if isinstance(t[0], int): s = h(t)
    else: s = t[0] + ' ' + ' '.join(hex(v) if isinstance(v, int) else str(v) for v in t[1:])
    return s + (f' -> {tgt:#x}' if tgt is not None else '')


def main(argv):
    import config
    path, a = config.pop_config(argv)
    args = [x for x in a if not x.startswith('--')]
    if len(args) < 3: sys.exit(__doc__)
    cfg = config.load(path)
    src, func, obj = args[:3]
    with open(src, encoding='latin1') as f: text = f.read()
    with open(obj, 'rb') as f: raw = f.read()
    t = Target(cfg, text, func, raw, fn_mask='--fnscore' in a)
    r = t.score(raw)
    print(func, r.text())
    if '--diff' in a and r.ok and not r.exact: print(t.listing_diff(raw))
    return 0 if r.exact else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
