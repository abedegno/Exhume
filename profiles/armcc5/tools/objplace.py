"""Prove a source by object placement: a small linker for one armcc object.

    python3 objplace.py [--config PATH] match SRC          (place and compare, armlink not used)
    python3 objplace.py [--config PATH] check SRC...       (armlink's bytes and the placement's must agree)
    python3 objplace.py [--config PATH] show SRC [SECTION=0xADDR ...]   (differences, function by function)

MDK-Lite's armlink refuses a link of more than 32 KB of RO and RW data (L6050U; zero-init does
not count), so armmatch cannot link a large source file at its address even when every byte is
right. A file built without --split_sections is one `.text` holding all its functions, and such a
file can pass 32 KB. Nor can it link an object holding two sections of one name that belong at
different addresses (armcc can give a file two .data sections): its scatter file selects a
section by name, and armlink refuses the two selectors as ambiguous (L6223E). This does
armlink's job for that one object instead:

- the object is compiled with the source's own options (as armmatch does) and fromelf lists its
  sections (with their file offsets), its symbols and its relocations (`fromelf --text -v -r -s`).
  MDK-Lite's objects are not plain ELF (the file starts `\\xf7ORC`, and fromelf will not write
  them out as ELF under the Lite licence), but the section contents are stored plainly, so each
  allocatable section's bytes are read from the object file at the offset fromelf gives. The
  number of relocations read must equal the relocation section's size divided by 8;
- each section goes at the address armmatch.place gives it (the map, at: lines), exactly as the
  armlink route places it;
- one object can hold several sections of one name (armcc can split a file's initialised data
  into two .data sections, one of them a table of pointers). Placements are kept by section
  index, so each gets its own address and size; a repeated name is shown as name#index;
- every relocation is applied: R_ARM_ABS32, R_ARM_REL32, R_ARM_THM_CALL, R_ARM_THM_JUMP24,
  R_ARM_THM_JUMP19, R_ARM_THM_JUMP11 and R_ARM_THM_JUMP8 (the narrow B and B<cond>, which armcc
  emits for a branch to another section), R_ARM_THM_MOVW_ABS_NC, R_ARM_THM_MOVT_ABS,
  R_ARM_NONE. The addend is the
  one in place (REL). A symbol defined in the object resolves to its section's address plus its
  value (bit 0 marks Thumb code); a weak definition gives way to a symdefs one, as armlink lets a
  strong definition override a weak one; any other symbol comes from the symdefs the armlink
  route would get (the map with its placeholder aliases, the at: lines, and any extra symbols a
  caller passes, such as a library link's). An unresolved symbol, an unknown relocation type, a
  branch to a target that is not Thumb code or out of range, and a relocation the mapping symbols
  put in the wrong state ($t for branches and MOVW/MOVT, $d for literal words) are errors, never
  skipped;
- the placed bytes are compared with the image byte for byte, nothing masked, as armmatch
  compares armlink's bytes (the same row sizes, alignment padding and RAM rules).

  match  the placement alone: what armmatch would report, for a file armlink cannot link
  check  for each source, armmatch (armlink) and the placement on the same object, compared
         section by section: they must give the same bytes. Run it over every source that
         armlink can link before trusting the placement with one it cannot (a file with a
         repeated section name is one it cannot: check reports armlink's side as empty).
  show   each function of the unit set against the image at its map address, with every
         relocated field recomputed for that address, so a function the build moved is still
         compared exactly; a function whose size differs gets an instruction diff.
         SECTION=0xADDR places a section by hand (diagnostics only)."""
import os, re, struct, sys
from dataclasses import dataclass, field

HERE = os.path.dirname(os.path.abspath(__file__))
EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
for p in (HERE, os.path.join(EXHUME, 'tools')):
    if p not in sys.path: sys.path.insert(0, p)
import armmap, armmatch, ehex, rwdata          # noqa: E402

RAM = 0x20000000


@dataclass
class Sec:
    name: str
    type: str
    flags: str
    offset: int
    size: int
    align: int
    data: bytes = b''
    info: int = None             # a relocation section: the section it applies to


@dataclass
class Sym:
    name: str
    value: int
    bind: str                    # Lc, Gb, Wk
    sec: object                  # section index, 'Abs' or 'Ref'
    type: str                    # Code, Data, Sect, File, --


@dataclass
class Rel:
    offset: int
    type: int
    tname: str
    sym: int
    sname: str


@dataclass
class Obj:
    """What the placement needs from one object: plain data, so a gate can cache it."""
    path: str
    sections: dict               # index -> Sec
    symbols: dict                # index -> Sym
    relocs: dict = field(default_factory=dict)       # target section index -> [Rel]


# ---- reading an object through fromelf ---------------------------------------------------------
_SEC = re.compile(r'^\*\* Section #(\d+)\s*$')
_FIELD = re.compile(r'^\s{4}(Name|Type|Flags|File Offset|Size|Alignment|Info)\s*:\s*(.*)$')
_REL = re.compile(r'^\s*(\d+)\s+0x([0-9a-fA-F]+)\s+(\d+)\s+(R_ARM_\w+)\s+(\d+)\s+(\S+)')
_SYMLINE = re.compile(r'^\s*(\d+)\s+(\S+)\s+0x([0-9a-fA-F]+)\s+(Lc|Gb|Wk)\s+(\d+|Abs|Ref)\s+(\S+)\s+\S+(?:\s+0x[0-9a-fA-F]+)?\s*$')
_SYMNAME = re.compile(r'^\s*(\d+)\s+(\S+)\s*$')
_SYMREST = re.compile(r'^\s+0x([0-9a-fA-F]+)\s+(Lc|Gb|Wk)\s+(\d+|Abs|Ref)\s+(\S+)\s+\S+(?:\s+0x[0-9a-fA-F]+)?\s*$')


def parse(text, path):
    """Obj from `fromelf --text -v -r -s` of one object (section bytes not read yet)."""
    secs, syms, rels = {}, {}, {}
    cur = None; in_syms = False; pending = None
    for line in text.splitlines():
        m = _SEC.match(line)
        if m:
            cur = int(m.group(1)); secs[cur] = Sec('', '', '', 0, 0, 1); in_syms = False; pending = None; continue
        if cur is None: continue
        m = _FIELD.match(line)
        if m and not in_syms:
            k, v = m.groups(); s = secs[cur]
            if k == 'Name': s.name = v.strip()
            elif k == 'Type': s.type = v.split()[0]
            elif k == 'Flags': s.flags = v
            elif k == 'File Offset': s.offset = int(v.split()[0])
            elif k == 'Size': s.size = int(v.split()[0])
            elif k == 'Alignment': s.align = int(v.split()[0])
            elif k == 'Info':
                mm = re.match(r'Section (\d+)', v.strip())
                if mm: s.info = int(mm.group(1))
            continue
        if line.strip().startswith('Symbol table '): in_syms = True; continue
        if secs[cur].type == 'SHT_REL':
            m = _REL.match(line)
            if m:
                rels.setdefault(secs[cur].info, []).append(
                    Rel(int(m.group(2), 16), int(m.group(3)), m.group(4), int(m.group(5)), m.group(6)))
            continue
        if in_syms:
            if pending is not None:
                m = _SYMREST.match(line)
                if m:
                    val, bind, sec, typ = m.groups()
                    syms[pending[0]] = Sym(pending[1], int(val, 16), bind, int(sec) if sec.isdigit() else sec, typ)
                pending = None; continue
            m = _SYMLINE.match(line)
            if m:
                i, name, val, bind, sec, typ = m.groups()
                syms[int(i)] = Sym(name, int(val, 16), bind, int(sec) if sec.isdigit() else sec, typ); continue
            m = _SYMNAME.match(line)
            if m: pending = (int(m.group(1)), m.group(2))
    return Obj(path, secs, syms, rels)


def fill(o, raw):
    """Read each allocatable section's bytes from the object file's contents; check that every
    relocation section was listed whole (8 bytes per SHT_REL entry)."""
    for s in o.sections.values():
        if 'SHF_ALLOC' in s.flags and s.type != 'SHT_NOBITS':
            s.data = raw[s.offset:s.offset + s.size]
            if len(s.data) != s.size: raise RuntimeError(f'{o.path}: section {s.name} runs past the end of the file')
    for s in o.sections.values():
        if s.type == 'SHT_REL' and len(o.relocs.get(s.info, [])) != s.size // 8:
            raise RuntimeError(f'{o.path}: {s.name}: {len(o.relocs.get(s.info, []))} relocations read, {s.size // 8} in the section')
    return o


def read_object(tc, obj):
    """Obj for an object file: fromelf's view of it, the allocatable sections' bytes from the file."""
    rc, out = tc.run('fromelf', ['--text', '-v', '-r', '-s', tc.inside(obj)], os.path.dirname(obj))
    if rc: raise RuntimeError(out)
    with open(obj, 'rb') as f: raw = f.read()
    return fill(parse(out, obj), raw)


# ---- linking ----------------------------------------------------------------------------------
def symbol_table(entries):
    """{name: (value, kind)} from symdefs entries [(addr, kind, name)]; value has bit 0 for 'T',
    as the symdefs file armlink reads has it."""
    out = {}
    for a, kind, n in entries:
        v = a | 1 if kind == 'T' else a
        if n in out and out[n] != (v, kind): raise ValueError(f'symbol {n} defined twice: 0x{out[n][0]:08X} and 0x{v:08X}')
        out[n] = (v, kind)
    return out


def _bl_get(h1, h2):
    s = (h1 >> 10) & 1; i1 = 1 ^ (((h2 >> 13) & 1) ^ s); i2 = 1 ^ (((h2 >> 11) & 1) ^ s)
    off = (s << 24) | (i1 << 23) | (i2 << 22) | ((h1 & 0x3FF) << 12) | ((h2 & 0x7FF) << 1)
    return off - (1 << 25) if s else off


def _bl_put(h1, h2, off):
    s = (off >> 24) & 1; i1 = (off >> 23) & 1; i2 = (off >> 22) & 1
    j1 = (1 ^ i1) ^ s; j2 = (1 ^ i2) ^ s
    return (h1 & 0xF800) | (s << 10) | ((off >> 12) & 0x3FF), (h2 & 0xD000) | (j1 << 13) | (j2 << 11) | ((off >> 1) & 0x7FF)


def _b19_get(h1, h2):
    s = (h1 >> 10) & 1; j1 = (h2 >> 13) & 1; j2 = (h2 >> 11) & 1
    off = (s << 20) | (j2 << 19) | (j1 << 18) | ((h1 & 0x3F) << 12) | ((h2 & 0x7FF) << 1)
    return off - (1 << 21) if s else off


def _b19_put(h1, h2, off):
    s = (off >> 20) & 1; j2 = (off >> 19) & 1; j1 = (off >> 18) & 1
    return (h1 & 0xFBC0) | (s << 10) | ((off >> 12) & 0x3F), (h2 & 0xD000) | (j1 << 13) | (j2 << 11) | ((off >> 1) & 0x7FF)


def _mov_get(h1, h2):
    return ((h1 & 0xF) << 12) | (((h1 >> 10) & 1) << 11) | (((h2 >> 12) & 7) << 8) | (h2 & 0xFF)


def _mov_put(h1, h2, v):
    return (h1 & 0xFBF0) | ((v >> 12) & 0xF) | (((v >> 11) & 1) << 10), (h2 & 0x8F00) | (((v >> 8) & 7) << 12) | (v & 0xFF)


def mapping_states(o, idx):
    """[(offset, '$t' | '$d' | '$a')] of a section, in order."""
    return sorted((s.value, s.name) for s in o.symbols.values() if s.sec == idx and s.name in ('$t', '$d', '$a'))


def _state_at(states, off):
    st = None
    for v, n in states:
        if v <= off: st = n
        else: break
    return st


def apply(r, b, S, T, P):
    """Write relocation r's field in bytearray b (the addend is the field's own value): None, or
    what is wrong."""
    if r.tname in ('R_ARM_THM_JUMP11', 'R_ARM_THM_JUMP8'):
        # a 16-bit B (imm11) or B<cond> (imm8); the addend is the field, in halfwords
        if not T: return 'the target is not Thumb code'
        h = struct.unpack_from('<H', b, r.offset)[0]
        bits = 11 if r.tname == 'R_ARM_THM_JUMP11' else 8
        if bits == 11 and h & 0xF800 != 0xE000: return f'not a narrow unconditional branch ({h:04X})'
        if bits == 8 and (h & 0xF000 != 0xD000 or h & 0xF00 >= 0xE00): return f'not a narrow conditional branch ({h:04X})'
        mask = (1 << bits) - 1
        addend = h & mask
        if addend & (1 << (bits - 1)): addend -= 1 << bits
        off = S + 2 * addend - P
        if off & 1 or not -(1 << bits) <= off < (1 << bits): return f'out of range ({off:+#x})'
        struct.pack_into('<H', b, r.offset, (h & ~mask) | ((off >> 1) & mask))
        return None
    w = struct.unpack_from('<I', b, r.offset)[0]
    h1, h2 = struct.unpack_from('<HH', b, r.offset)
    if r.tname == 'R_ARM_ABS32':
        struct.pack_into('<I', b, r.offset, ((S + w) | T) & 0xFFFFFFFF)
    elif r.tname == 'R_ARM_REL32':
        struct.pack_into('<I', b, r.offset, (((S + w) | T) - P) & 0xFFFFFFFF)
    elif r.tname in ('R_ARM_THM_CALL', 'R_ARM_THM_JUMP24'):
        if not T: return 'the target is not Thumb code (armlink would need BLX or a veneer)'
        if r.tname == 'R_ARM_THM_CALL' and (h2 & 0xD000) != 0xD000: return f'not a BL instruction ({h1:04X} {h2:04X})'
        if r.tname == 'R_ARM_THM_JUMP24' and (h2 & 0xD000) != 0x9000: return f'not a B.W instruction ({h1:04X} {h2:04X})'
        off = ((S + _bl_get(h1, h2)) | T) - P
        if not -(1 << 24) <= off < (1 << 24): return f'out of range ({off:+#x})'
        struct.pack_into('<HH', b, r.offset, *_bl_put(h1, h2, off))
    elif r.tname == 'R_ARM_THM_JUMP19':
        if not T: return 'the target is not Thumb code'
        off = ((S + _b19_get(h1, h2)) | T) - P
        if not -(1 << 20) <= off < (1 << 20): return f'out of range ({off:+#x})'
        struct.pack_into('<HH', b, r.offset, *_b19_put(h1, h2, off))
    elif r.tname in ('R_ARM_THM_MOVW_ABS_NC', 'R_ARM_THM_MOVT_ABS'):
        a = _mov_get(h1, h2)
        a = a - 0x10000 if a & 0x8000 else a
        v = (S + a) | T if r.tname == 'R_ARM_THM_MOVW_ABS_NC' else (S + a) >> 16
        struct.pack_into('<HH', b, r.offset, *_mov_put(h1, h2, v & 0xFFFF))
    else:
        return f'relocation type {r.type} ({r.tname}) is not handled'
    return None


THM_BRANCH = {'R_ARM_THM_CALL', 'R_ARM_THM_JUMP24', 'R_ARM_THM_JUMP19', 'R_ARM_THM_JUMP11', 'R_ARM_THM_JUMP8'}


def relocation_width(r):
    """The bytes relocation r's field covers: 2 for a narrow branch, else 4."""
    return 2 if r.tname in ('R_ARM_THM_JUMP11', 'R_ARM_THM_JUMP8') else 4


def link(o, bases, table):
    """Place and relocate every section in bases {index: addr}: ({index: bytes}, errors, count).

    table is {name: (value, kind)} for the symbols the object does not define (symdefs)."""
    out, errors, count = {}, [], 0

    def resolve(si):
        """(S, T) of symbol index si, or an error string."""
        s = o.symbols.get(si)
        if s is None: return f'relocation to symbol #{si}, which is not in the symbol table'
        if isinstance(s.sec, int) and not (s.bind == 'Wk' and s.name in table):
            if s.sec not in bases:
                return f'{s.name} is in section #{s.sec} ({o.sections[s.sec].name}), which is not placed'
            if s.type == 'Code': return bases[s.sec] + (s.value & ~1), s.value & 1
            return bases[s.sec] + s.value, 0
        if s.sec == 'Abs': return s.value, 0
        if s.name not in table: return f'unresolved symbol {s.name}'
        v, kind = table[s.name]
        return (v & ~1, 1) if kind == 'T' else (v, 0)

    for idx, base in bases.items():
        sec = o.sections[idx]
        if sec.type == 'SHT_NOBITS': out[idx] = bytes(sec.size); continue
        b = bytearray(sec.data)
        code = 'SHF_EXECINSTR' in sec.flags
        states = mapping_states(o, idx) if code else []
        for r in sorted(o.relocs.get(idx, []), key=lambda r: r.offset):
            where = f'{sec.name}+0x{r.offset:X} {r.tname} {r.sname}'
            if r.tname == 'R_ARM_NONE':
                x = resolve(r.sym)
                if isinstance(x, str): errors.append(f'{where}: {x}')
                count += 1; continue
            if r.offset + relocation_width(r) > len(b): errors.append(f'{where}: past the end of the section'); continue
            if code:
                st = _state_at(states, r.offset)
                want = '$t' if r.tname in THM_BRANCH or 'MOV' in r.tname else '$d'
                if st != want: errors.append(f'{where}: in a {st or "unmarked"} part of the code, not {want}'); continue
            x = resolve(r.sym)
            if isinstance(x, str): errors.append(f'{where}: {x}'); continue
            S, T = x
            e = apply(r, b, S, T, base + r.offset)
            if e: errors.append(f'{where}: {e}'); continue
            count += 1
        out[idx] = bytes(b)
    # relocations against sections that are allocatable but not placed are a bug in the caller
    for idx, rs in o.relocs.items():
        s = o.sections.get(idx)
        if s and 'SHF_ALLOC' in s.flags and idx not in bases and rs:
            errors.append(f'section {s.name} has {len(rs)} relocations but is not placed')
    return out, errors, count


def section_indexes(info, placements):
    """([(section index, addr)], errors) for armmatch.place's placements [(name, addr)].

    armmatch.place names a section, but one object can hold several sections of one name. It
    walks the sections in index order and places each by its own symbols, so the k-th placement
    of a repeated name is the k-th section of that name. That holds only when every section of
    the name is placed (none left unplaced); otherwise it is an error."""
    by_name = {}
    for i, (n, _) in sorted(info.sections.items()):
        by_name.setdefault(n, []).append(i)
    count = {}
    for name, _ in placements:
        count[name] = count.get(name, 0) + 1
    out, errors, seen = [], [], {}
    for name, a in placements:
        ix = by_name.get(name, [])
        if not ix:
            errors.append(f'section {name}: no section of that name'); continue
        if len(ix) > 1 and count[name] != len(ix):
            errors.append(f'section {name}: {len(ix)} sections of that name, {count[name]} placed'); continue
        if len(ix) == 1 and count[name] > 1:
            errors.append(f'section {name}: placed {count[name]} times'); continue
        k = seen.get(name, 0); seen[name] = k + 1
        out.append((ix[k], a))
    return out, errors


def section_label(info, idx):
    """A section's name, or name#index when the object holds several sections of that name."""
    n = info.sections[idx][0]
    return n if sum(1 for x, _ in info.sections.values() if x == n) == 1 else f'{n}#{idx}'


def label_index(o, label):
    """The section index a section_label names in object o."""
    if '#' in label:
        return int(label.rsplit('#', 1)[1])
    return next(i for i, s in o.sections.items() if s.name == label)


def sized_placements(info, placements):
    """[(name, addr, size)] for placements [(name, addr)], each with its own section's size (a
    size looked up by name would give every section of a repeated name the last one's size)."""
    ix, _ = section_indexes(info, placements)
    return [(info.sections[i][0], a, info.sections[i][1] or 0) for i, a in ix]


def bases_for(o, info, placements, unplaced):
    """{section index: addr} for armmatch.place's placements; the unplaced sections go one after
    another from armmatch.UNPLACED (as the armlink route links them, out of the way)."""
    by_name = {}
    for i, s in o.sections.items():
        by_name.setdefault(s.name, []).append(i)
    ix, errors = section_indexes(info, placements)
    bases = dict(ix)
    at = armmatch.UNPLACED
    for name in unplaced:
        for i in by_name.get(name, []):
            if i in bases: continue
            al = max(o.sections[i].align, 1)
            at = (at + al - 1) // al * al
            bases[i] = at; at += o.sections[i].size
    # fromelf's two views of the object must agree
    for i, (n, z) in info.sections.items():
        if i in o.sections and (o.sections[i].name != n or (z is not None and o.sections[i].size != z)):
            errors.append(f'section #{i}: {n} ({z}) in the symbol listing, {o.sections[i].name} ({o.sections[i].size}) in the header listing')
    return bases, errors


# ---- comparing (as armmatch.match compares armlink's bytes) ------------------------------------
@dataclass
class Result:
    whole: bool = False
    lines: list = field(default_factory=list)
    defined: dict = field(default_factory=dict)
    obj: object = None           # the Obj, for a gate's image
    placed: dict = field(default_factory=dict)          # section label (section_label) -> (addr, bytes)


def compare(info, m, img, ram, defined, placements, out):
    """armmatch.match's comparison over placed bytes: (lines, whole, compared bytes, good bytes)."""
    lines = []
    names = {}
    for s in sorted(info.symbols, key=lambda s: -s.value):          # a section is named after its first function
        if s.type == 'Code' and isinstance(s.sec, int): names[info.sections[s.sec][0]] = s.name
    good = total = 0; whole = True
    ix, _ = section_indexes(info, placements)
    for idx, a in sorted(ix, key=lambda p: p[1]):
        section = info.sections[idx][0]
        got = out[idx]
        label = names.get(section, section_label(info, idx))
        zi = armmatch.is_zi(info, idx)
        problems = []; padded = 0
        if armmatch.is_code(info, idx):
            row = m.row_at(a)
            if row is not None:
                own = {defined[sym.name] for sym in info.symbols
                       if sym.sec == idx and sym.name in defined and sym.type == 'Code'}
                rows_in = [r for r in m.functions if r.addr in own and a <= r.addr < a + len(got) + 4]
                end = max([r.addr + r.size for r in rows_in] + [row.addr + row.size])
                row = armmap.Row(a, end - a, row.name, row.kind, row.file)
            if row is None:
                problems.append('no map row (size unknown)'); n = len(got)
            else:
                n = row.size
                if row.size != len(got):
                    try:
                        full = img.bytes(a, row.size)
                    except ValueError:
                        full = b''
                    if armmatch.padding_ok(got, full, a):
                        padded = row.size - len(got); got = got + bytes(padded)
                    else:
                        problems.append(f'size 0x{len(got):X} want 0x{row.size:X}')
        else:
            n = len(got)
            if a >= RAM and ram is not None and n:
                kinds = {ram.kind_at(x) for x in range(a, a + n)}
                if zi and kinds != {'zero'}: problems.append('zero-init section over initialised RAM in the original')
                if not zi and 'zero' in kinds: problems.append('initialised section over zero-init RAM in the original')
            for sym in info.symbols:
                if sym.sec != idx or sym.type != 'Data' or sym.size is None: continue
                row = m.row_at(a + armmatch.offset(sym))
                if row is None: problems.append(f'{sym.name}: no map row (size unknown)')
                elif not armmatch.size_fits(sym.size, armmatch.offset(sym), row.size, len(got)):
                    problems.append(f'{sym.name}: size 0x{sym.size:X} want 0x{row.size:X}')
        if a >= RAM and ram is None:
            lines.append(f'{label:28} 0x{a:08X}  placed (RAM, not compared: the image has no region table)'); whole = False; continue
        try:
            want = ram.bytes(a, n) if a >= RAM else img.bytes(a, n)
        except ValueError as e:
            lines.append(f'{label:28} 0x{a:08X}  {e}'); whole = False; continue
        bad = armmatch.compare(got, want)
        total += len(got)
        if not bad and not problems:
            good += len(got); state = 'MATCH' + (f' (+{padded} padding)' if padded else '')
        else:
            whole = False
            state = '  '.join(([f'{len(bad)} bytes differ, first at +0x{bad[0]:X}'] if bad else []) + problems)
        lines.append(f'{label:28} 0x{a:08X}  {state}')
    return lines, whole, total, good


def place_object(cfg, tc, src, obj, info, m, libsyms=None, force=None, o=None):
    """Place and compare a compiled source (armmatch.match's steps after the compile, with this
    linker in place of armlink). libsyms {name: addr} adds code symbols the symdefs lack (a
    library link's); force {section: addr} overrides placement (diagnostics only); o is an Obj
    already read."""
    res = Result()
    with open(src, encoding='latin1') as f: text = f.read()
    extra = armmap.annotations(text)
    unused_ok = armmap.unused_dropped(text)
    if o is None:
        try:
            o = read_object(tc, obj)
        except Exception as e:
            res.lines.append(f'ERROR reading the object: {e}'); return res
    res.obj = o
    placements, defined, errors, unplaced = armmatch.place(info, m, extra, unused_ok)
    if force:
        errors = [e for e in errors if not any(f'section {s}:' in e or f'section {s} ' in e for s in force)]
        placements = [(s, a) for s, a in placements if s not in force] + list(force.items())
        unplaced = [s for s in unplaced if s not in force]
        for s, a in force.items():
            idx = next(i for i, (n, _) in info.sections.items() if n == s)
            for sym in info.symbols:
                if sym.sec == idx and sym.type in ('Code', 'Data'): defined[sym.name] = a + armmatch.offset(sym)
    res.defined = defined
    if errors:
        res.lines += ['ERROR ' + e for e in errors] + ['-- not placed']; return res
    lines, whole, placed, err2 = finish(cfg, info, o, m, text, extra, placements, defined, unplaced, libsyms)
    res.lines += lines
    res.placed = placed
    res.whole = whole and not err2
    return res


def finish(cfg, info, o, m, text, extra, placements, defined, unplaced, libsyms=None):
    """Link and compare: (lines, whole, {section: (addr, bytes)}, link errors)."""
    lines = []
    bases, errs = bases_for(o, info, placements, unplaced)
    entries = m.symdef_entries(set(defined), extra)
    if libsyms:
        have = {e[2] for e in entries} | set(defined)
        entries += [(a, 'T', n) for n, a in libsyms.items() if n not in have]
    try:
        table = symbol_table(entries)
    except ValueError as e:
        errs.append(str(e)); table = {}
    out, lerr, count = link(o, bases, table) if not errs else ({}, [], 0)
    errs += lerr
    if errs:
        lines += ['ERROR ' + e for e in errs[:40]] + ([f'... and {len(errs) - 40} more'] if len(errs) > 40 else [])
        lines.append('-- not placed'); return lines, False, {}, errs
    blocked = False
    dead = armmap.dropped(text); unused_ok = armmap.unused_dropped(text)
    sec_index = {n: i for i, (n, _) in info.sections.items()}
    for x in unplaced:
        idx = sec_index[x]
        syms = [s.name for s in info.symbols if s.sec == idx and s.type in ('Code', 'Data')]
        if armmatch.is_code(info, idx) and unused_ok:
            lines.append(f'NOTE section {x} ({", ".join(syms)}) is not in the image: unused, dropped by armlink')
        elif syms and set(syms) <= dead:
            lines.append(f'NOTE section {x} ({", ".join(syms)}) is marked dropped: placed at 0x{bases[idx]:08X}, not compared')
        else:
            blocked = True
            lines.append(f'UNPLACED section {x} ({", ".join(syms) or "none"}) is not in the map: not compared, so not a whole match')
    img = ehex.Image(cfg.image)
    try:
        ram = rwdata.ram_image(img)
    except ValueError:
        ram = None
    cl, whole, total, good = compare(info, m, img, ram, defined, placements, out)
    lines += cl
    whole = whole and total > 0 and not blocked
    lines.append(f'-- {good}/{total} bytes matched, {count} relocations resolved (object placement)' +
                 ('; WHOLE FILE MATCHES' if whole else ''))
    ix, _ = section_indexes(info, placements)
    placed = {section_label(info, i): (a, out[i]) for i, a in ix if a < armmatch.UNPLACED or a >= RAM}
    return lines, whole, placed, []


def compile_source(cfg, tc, src, obj):
    """The compile armmatch.match does: (ok, log)."""
    with open(src, encoding='latin1') as f: text = f.read()
    if src.endswith('.s'):
        mo = re.search(r'^\s*;\s*opts:\s*(.*)$', text, re.M)
        return tc.assemble(src, obj, mo.group(1) if mo else (cfg.asm_opts or '--cpu Cortex-M3'))
    _, opts = cfg.directives(src)
    return tc.compile(src, obj, opts or cfg.c_opts)


def placed_match(cfg, src, compile=True, libsyms=None, force=None):
    """armmatch.match by object placement: compile (unless the object is already built), place,
    link, compare. Work directory build/objplace/<stem>, apart from armmatch's. Returns (Result,
    ObjectInfo, object path)."""
    import armtc
    src = os.path.abspath(src)
    stem = os.path.splitext(os.path.basename(src))[0]
    work = os.path.join(cfg.build, 'objplace', stem); os.makedirs(work, exist_ok=True)
    tc = armtc.toolchain(cfg)
    obj = os.path.join(work, stem + '.o')
    res = Result()
    if compile or not os.path.exists(obj):
        ok, log = compile_source(cfg, tc, src, obj)
        if log.strip(): res.lines += log.strip().splitlines()
        if not ok: res.lines.append('-- compile failed'); return res, None, obj
    info = tc.symbols(obj)
    m = armmap.Map.from_dir(cfg.map)
    r = place_object(cfg, tc, src, obj, info, m, libsyms, force)
    r.lines = res.lines + r.lines
    return r, info, obj


# ---- command line -----------------------------------------------------------------------------
def check(cfg, srcs):
    """For each source: armmatch (armlink) and placement on the same object; compare every placed
    section's bytes between the two, and each with the image."""
    import armtc
    tc = armtc.toolchain(cfg)
    m = armmap.Map.from_dir(cfg.map)
    bad = 0
    for src in srcs:
        src = os.path.abspath(src)
        stem = os.path.splitext(os.path.basename(src))[0]
        r = armmatch.match(cfg, src)                      # compiles, places, links with armlink, compares
        work = os.path.join(cfg.build, stem); obj = os.path.join(work, stem + '.o')
        if not os.path.exists(obj):
            print(f'{src}: no object ({r.lines[-1] if r.lines else "?"})'); bad += 1; continue
        info = tc.symbols(obj)
        p = place_object(cfg, tc, src, obj, info, m)
        # armlink's bytes per section, from armmatch's regions
        with open(src, encoding='latin1') as f: text = f.read()
        placements, _, _, _ = armmatch.place(info, m, armmap.annotations(text), armmap.unused_dropped(text))
        sized = sized_placements(info, placements)
        _, regions = armmap.scatter_text(os.path.basename(obj), sized)
        ix, _ = section_indexes(info, placements)
        at = {(info.sections[i][0], a): i for i, a in ix}
        bindir = os.path.join(work, 'bin')
        bins = {}
        if os.path.isdir(bindir):
            for n in os.listdir(bindir):
                with open(os.path.join(bindir, n), 'rb') as f: bins[n] = f.read()
        same = diff = 0; notes = []
        for region, members in regions.items():
            rbase = members[0][1] & ~3
            for sec, a in members:
                i = at.get((sec, a))
                label = section_label(info, i) if i is not None else sec
                if label not in p.placed: notes.append(f'{label}: not placed by objplace'); diff += 1; continue
                pa, pb = p.placed[label]
                size = info.sections[i][1] or 0
                lb = bytes(size) if armmatch.is_zi(info, i) else bins.get(region, b'')[a - rbase:a - rbase + size]
                if pa != a or lb != pb:
                    k = next((i for i in range(min(len(lb), len(pb))) if lb[i] != pb[i]), min(len(lb), len(pb)))
                    notes.append(f'{label} 0x{a:08X}: armlink and placement differ at +0x{k:X} (armlink {len(lb)} bytes, placement {len(pb)})'); diff += 1
                else:
                    same += len(pb)
        nrel = re.search(r'(\d+) relocations resolved', p.lines[-1]) if p.lines else None
        state = 'SAME' if not diff and r.whole == p.whole else 'DIFFERENT'
        if state != 'SAME': bad += 1
        print(f'{os.path.relpath(src, cfg.root)}: {len(placements)} sections, {same} bytes, '
              f'{nrel.group(1) if nrel else "?"} relocations: armlink and placement {state}; '
              f'armmatch {"whole" if r.whole else "NOT whole"}, placement {"whole" if p.whole else "NOT whole"}')
        for n in notes: print('    ' + n)
        if not p.whole:
            for l in p.lines[-8:]: print('    ' + l)
    return 1 if bad else 0


def _off(buf, n):
    """The offset of instruction n of buf, as armmatch.disasm walks it."""
    lines = armmatch.disasm(buf, 0)
    return int(lines[n].split()[0], 16) if n < len(lines) else len(buf)


def show(cfg, src, force):
    """A unit's differences, function by function. The strict result (every byte of each placed
    section against the image) comes first. Then each function's bytes where the placement put it
    are set against the image at the function's map address: bytes outside relocated fields must
    be equal, and each relocated field must equal what the relocation gives at the map address with
    every symbol at its map address (so a function the build moved is still compared exactly).
    A function whose size differs gets an instruction diff instead."""
    import difflib
    r, info, obj = placed_match(cfg, src, compile=True, force=force)
    for l in r.lines:
        if not l.startswith('"') and 'Warning' not in l and not l.startswith(' '): print(l)
    if not r.placed: return 1
    img = ehex.Image(cfg.image)
    m = armmap.Map.from_dir(cfg.map)
    with open(src, encoding='latin1') as f: extra = armmap.annotations(f.read())
    o = r.obj
    table = symbol_table(m.symdef_entries(set(r.defined), extra))
    sec_at = {label_index(o, n): a for n, (a, _) in r.placed.items()}

    def at_map(si):
        """(S, T) of a symbol with every named symbol at its map address."""
        s = o.symbols[si]
        if isinstance(s.sec, int) and s.type in ('Code', 'Data') and m.address(s.name, extra) is not None:
            return m.address(s.name, extra), (s.value & 1 if s.type == 'Code' else 0)
        if isinstance(s.sec, int): return sec_at[s.sec] + (s.value & ~1 if s.type == 'Code' else s.value), (s.value & 1 if s.type == 'Code' else 0)
        v, kind = table[s.name]
        return (v & ~1, 1) if kind == 'T' else (v, 0)

    def dis(buf):
        # branch targets and pc-relative offsets move with any change of size before them: the
        # diff sees them as 'L' and '[pc, #]' (the bytes were compared exactly above)
        out = []
        for l in armmatch.disasm(buf, 0):
            t = l.split('  ', 1)[1]
            if re.match(r'(b|bl|cbz|cbnz|b\.w|b[a-z][a-z](\.w)?) ', t): t = re.sub(r'#0x[0-9a-f]+$', 'L', t)
            t = re.sub(r'\[pc, #0x[0-9a-f]+\]', '[pc, #]', t)
            out.append(t)
        return out
    total_bad = 0
    for sec, (a, data) in sorted(r.placed.items(), key=lambda x: x[1][0]):
        idx = label_index(o, sec)
        if 'SHF_EXECINSTR' not in o.sections[idx].flags: continue
        funcs = sorted((s.value & ~1, s.name) for s in o.symbols.values() if s.sec == idx and s.type == 'Code')
        rels = sorted(o.relocs.get(idx, []), key=lambda x: x.offset)
        rows = [m.by_name[n] for _, n in funcs if n in m.by_name]
        if rows:
            end = max(x.addr + x.size for x in rows)
            print(f"\nsection {sec}: placed at 0x{a:08X}, 0x{len(data):X} bytes; its functions' map rows end at "
                  f'0x{end:08X} (0x{end - a:X} bytes)')
        print(f'{"function":16} {"map addr":>10} {"build at":>10} {"shift":>6} {"map size":>8} {"build size":>10}  result')
        diffs = []
        for k, (off, name) in enumerate(funcs):
            end = funcs[k + 1][0] if k + 1 < len(funcs) else len(data)
            row = m.by_name.get(name)
            if row is None: print(f'{name:16} no map row'); continue
            got = data[off:end]; want = img.bytes(row.addr, row.size)
            shift = a + off - row.addr
            if len(got) == len(want):
                # relocated fields: what this relocation gives at the map address
                exp = bytearray(got); fields = set(); badrel = []
                for rr in rels:
                    if off <= rr.offset < end:
                        if rr.tname == 'R_ARM_NONE': continue
                        width = relocation_width(rr)
                        fields |= set(range(rr.offset - off, rr.offset - off + width))
                        b0 = bytearray(o.sections[idx].data[rr.offset:rr.offset + width])
                        tmp = Rel(0, rr.type, rr.tname, rr.sym, rr.sname)
                        S, T = at_map(rr.sym)
                        error = apply(tmp, b0, S, T, row.addr + rr.offset - off)
                        exp[rr.offset - off:rr.offset - off + width] = b0
                        if error or bytes(b0) != want[rr.offset - off:rr.offset - off + width]: badrel.append(rr)
                nb = sum(1 for i in range(len(got)) if i not in fields and got[i] != want[i])
                if nb == 0 and not badrel: res = 'same bytes' if shift == 0 else 'same bytes at its map address (relocated fields agree)'
                else:
                    res = (f'{nb} bytes differ outside relocated fields' if nb else '') + \
                          (f'{"; " if nb else ""}{len(badrel)} relocated fields differ: ' +
                           ', '.join(f'+0x{x.offset - off:X} {x.sname}' for x in badrel[:4]) if badrel else '')
                    diffs.append((name, row.addr, bytes(exp), want))
                    total_bad += 1
            else:
                res = f'size differs by {len(got) - len(want):+d}'
                diffs.append((name, row.addr, got, want)); total_bad += 1
            print(f'{name:16} 0x{row.addr:08X} 0x{a + off:08X} {shift:+6d} {row.size:8X} {len(got):10X}  {res}')
        for name, addr, got, want in diffs:
            dg, dw = dis(got), dis(want)
            sm = difflib.SequenceMatcher(None, dw, dg, autojunk=False)
            ops = [op for op in sm.get_opcodes() if op[0] != 'equal']
            ni = sum(max(i2 - i1, j2 - j1) for _, i1, i2, j1, j2 in ops)
            print(f'\n{name} (image 0x{addr:08X}): {len(ops)} places, {ni} instructions differ (image -, build +; offsets in the function)')
            for tag, i1, i2, j1, j2 in ops[:12]:
                print(f'  @ image +0x{_off(want, i1):X}, build +0x{_off(got, j1):X}')
                for l in dw[i1:i2]: print(f'  - {l}')
                for l in dg[j1:j2]: print(f'  + {l}')
            if len(ops) > 12: print(f'  ... {len(ops) - 12} more places')
    print(f'\n{total_bad} functions differ')
    return 0 if r.whole else 1


def main(argv):
    import config
    path, a = config.pop_config(argv)
    if not a or a[0] not in ('match', 'check', 'show') or len(a) < 2: sys.exit(__doc__)
    cfg = config.load(path)
    if a[0] == 'check': return check(cfg, a[1:])
    if a[0] == 'match':
        r, _, _ = placed_match(cfg, a[1])
        print('\n'.join(r.lines))
        return 0 if r.lines and 'bytes matched' in r.lines[-1] else 1
    force, rest = {}, []
    for x in a[1:]:
        if '=' in x and x.split('=', 1)[1].startswith('0x'):
            s, v = x.split('=', 1); force[s] = int(v, 16)
        else: rest.append(x)
    return show(cfg, rest[0], force)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
