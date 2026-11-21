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
edit that breaks a neighbour is seen."""
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


class Result:
    __slots__ = ('ok', 'exact', 'diff', 'size', 'want_size', 'neighbours', 'error', 'code')

    def __init__(self, ok=False, exact=False, diff=None, size=None, want_size=None, neighbours=(), error='', code=b''):
        self.ok, self.exact, self.diff, self.size, self.want_size = ok, exact, diff, size, want_size
        self.neighbours, self.error, self.code = neighbours, error, code

    @property
    def size_ok(self):
        return self.size == self.want_size

    def key(self):
        """Sort key: exact first, then right size, then differing instructions."""
        if not self.ok: return (3, 0, 10 ** 9)
        if self.exact and not self.neighbours: return (0, 0, 0)
        return (1 if self.size_ok and not self.neighbours else 2, abs(self.size - self.want_size), self.diff)

    def text(self):
        if not self.ok: return 'FAILED ' + self.error
        s = 'EXACT' if self.exact else f'{self.diff}'
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
        self.exp = normalise(rows_of(self.want, row.addr, self.idata, word, fn_mask), fn_mask)
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
            got = normalise(rows_of(code, at, data, word, self.fn_mask), self.fn_mask)
            n, _ = diff_count(self.exp, got)
            if n == 0:
                # the same instructions, yet other bytes: a pc-relative displacement, an adr
                # offset or data no load names (a string). Count the differing halfwords so
                # that only equal bytes score 0.
                n = max(1, sum(1 for k in range(0, min(len(code), len(self.want)) - 1, 2)
                               if code[k:k + 2] != self.want[k:k + 2]))
            r = Result(True, False, n, len(code), self.want_size, neigh, code=code)
        if len(self.cache) > 20000: self.cache.clear()
        self.cache[h] = r
        return r

    @staticmethod
    def _read(raw):
        o, _ = orcobj.read(raw=raw)
        return o

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
