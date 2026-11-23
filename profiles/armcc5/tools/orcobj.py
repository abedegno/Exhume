"""Read an armcc object file in-process, without fromelf: sections, symbols, relocations.

    python3 orcobj.py OBJ          (prints the sections, symbols and relocations)

An object from a licensed armcc is plain ELF. MDK-Lite's armcc writes the same ELF with two
parts shuffled, and the file starts `\\xf7ORC` with e_machine 8 in place of EM_ARM (40), and
eight more bytes after the 52-byte header (the words 2 and 8). The section contents, the local
symbols, the string tables and the relocation entries are stored plainly; what is shuffled is

- each 40-byte section header, and
- each 16-byte global symbol entry (from the symbol table's sh_info to its end).

Each record's bytes are permuted. The permutation comes from the SHA-1 digest d of a 32-bit
key (little-endian): for c in 0..n-2, a[n-2-c] = d[c % 20] % (c + 2); then, from the list
0..n-1, the b-th output position is the element at a[b] (removed from the list), and the last
is the one left; byte j of the stored record is byte p[j] of the real one. The keys are
76 * e_shoff - 1 + 320 * i for section header i, and 5248 * count - 1312 * j for symbol j,
where count is the number of symbols. This was read from fromelf's own reader, so that a
search can score each variant without starting fromelf; test_orcobj.py checks it against
fromelf's listing of the same objects whenever a toolchain is available.

The result is an objplace.Obj (with section bytes filled) and an armtc.ObjectInfo, the two
views objplace and armmatch use."""
import hashlib, os, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path: sys.path.insert(0, HERE)

ORC_MAGIC = b'\xf7ORC'
ELF_MAGIC = b'\x7fELF'

SHT = {0: 'SHT_NULL', 1: 'SHT_PROGBITS', 2: 'SHT_SYMTAB', 3: 'SHT_STRTAB', 4: 'SHT_RELA', 8: 'SHT_NOBITS',
       9: 'SHT_REL', 0x70000003: 'SHT_ARM_ATTRIBUTES', 17: 'SHT_GROUP'}
RELOC = {0: 'R_ARM_NONE', 2: 'R_ARM_ABS32', 3: 'R_ARM_REL32', 10: 'R_ARM_THM_CALL', 30: 'R_ARM_THM_JUMP24',
         47: 'R_ARM_THM_MOVW_ABS_NC', 48: 'R_ARM_THM_MOVT_ABS', 51: 'R_ARM_THM_JUMP19', 102: 'R_ARM_THM_JUMP11',
         103: 'R_ARM_THM_JUMP8', 40: 'R_ARM_V4BX', 42: 'R_ARM_PREL31', 25: 'R_ARM_BASE_PREL', 11: 'R_ARM_THM_PC8',
         5: 'R_ARM_THM_ABS5', 7: 'R_ARM_THM_PC22'}

_PERM = {}


def permutation(key, n):
    """The output position of each stored byte of an n-byte record shuffled with key."""
    k = (key & 0xFFFFFFFF, n)
    p = _PERM.get(k)
    if p is None:
        d = hashlib.sha1(struct.pack('<I', k[0])).digest()
        a = [0] * (n - 1)
        for c in range(n - 1):
            a[n - 2 - c] = d[c % 20] % (c + 2)
        rest = list(range(n)); p = []
        for b in range(n - 1):
            p.append(rest.pop(a[b]))
        p.append(rest[0])
        p = _PERM[k] = tuple(p)
    return p


def unshuffle(rec, key):
    p = permutation(key, len(rec))
    out = bytearray(len(rec))
    for j, b in enumerate(rec):
        out[p[j]] = b
    return bytes(out)


def _flags(f):
    names = [n for bit, n in ((2, 'SHF_ALLOC'), (1, 'SHF_WRITE'), (4, 'SHF_EXECINSTR')) if f & bit]
    order = {'SHF_ALLOC': 0, 'SHF_WRITE': 1, 'SHF_EXECINSTR': 2}
    return (' + '.join(sorted(names, key=order.get)) if names else 'None') + f' (0x{f:08X})'


class Raw:
    """The ELF structure of one object: headers [(name, type, flags, addr, offset, size, link,
    info, align, entsize)], symbols [(name, value, size, info, other, shndx)]."""

    def __init__(self, raw, path=''):
        self.raw, self.path = raw, path
        magic = raw[:4]
        if magic not in (ORC_MAGIC, ELF_MAGIC): raise ValueError(f'{path}: not an ELF or ORC object')
        self.orc = magic == ORC_MAGIC
        shoff, = struct.unpack_from('<I', raw, 0x20)
        shnum, shstrndx = struct.unpack_from('<HH', raw, 0x30)
        hdrs = []
        for i in range(shnum):
            rec = raw[shoff + 40 * i:shoff + 40 * i + 40]
            if self.orc: rec = unshuffle(rec, 76 * shoff - 1 + 320 * i)
            hdrs.append(struct.unpack('<10I', rec))
        self.headers = hdrs
        sh = hdrs[shstrndx]
        self.shstr = raw[sh[4]:sh[4] + sh[5]]
        self.names = [self._str(self.shstr, h[0]) for h in hdrs]
        self.symtab = next((i for i, h in enumerate(hdrs) if h[1] == 2), None)
        self.symbols = []
        if self.symtab is not None:
            h = hdrs[self.symtab]
            strtab = hdrs[h[6]]
            self.strtab = raw[strtab[4]:strtab[4] + strtab[5]]
            count = h[5] // 16
            for j in range(count):
                rec = raw[h[4] + 16 * j:h[4] + 16 * j + 16]
                if self.orc and j >= h[7]: rec = unshuffle(rec, 5248 * count - 1312 * j)
                name, value, size, info, other, shndx = struct.unpack('<IIIBBH', rec)
                self.symbols.append((self._str(self.strtab, name), value, size, info, other, shndx))

    @staticmethod
    def _str(tab, off):
        e = tab.find(b'\0', off)
        return tab[off:e if e >= 0 else len(tab)].decode('latin1')

    def section_bytes(self, i):
        h = self.headers[i]
        return self.raw[h[4]:h[4] + h[5]]

    def relocations(self, i):
        """[(offset, symbol index, type)] of relocation section i (SHT_REL)."""
        h = self.headers[i]
        data = self.raw[h[4]:h[4] + h[5]]
        return [(o, inf >> 8, inf & 0xFF) for o, inf in struct.iter_unpack('<II', data)]


def _bind(info):
    return {0: 'Lc', 1: 'Gb', 2: 'Wk'}.get(info >> 4, 'Lc')


def _type(info, name):
    t = info & 0xF
    return {1: 'Data', 2: 'Code', 3: 'Sect', 4: 'File'}.get(t, '--')


def _sec(shndx):
    if shndx == 0: return 'Ref'
    if shndx == 0xFFF1: return 'Abs'
    return shndx


def read(path=None, raw=None):
    """(objplace.Obj, armtc.ObjectInfo) of an object file (or its bytes)."""
    import objplace, armtc
    if raw is None:
        with open(path, 'rb') as f: raw = f.read()
    r = Raw(raw, path or '')
    secs = {}
    for i, h in enumerate(r.headers):
        if i == 0: continue
        name, typ, flags, addr, off, size, link, info, align, es = h
        s = objplace.Sec(r.names[i], SHT.get(typ, f'0x{typ:08X}'), _flags(flags), off, size, align)
        if typ == 9: s.info = info
        if flags & 2 and typ != 8: s.data = r.raw[off:off + size]
        secs[i] = s
    syms = {}
    for j, (name, value, size, info, other, shndx) in enumerate(r.symbols):
        if j == 0: continue
        syms[j] = objplace.Sym(name, value, _bind(info), _sec(shndx), _type(info, name))
    rels = {}
    for i, h in enumerate(r.headers):
        if h[1] != 9: continue
        tgt = h[7]
        rels[tgt] = [objplace.Rel(o, t, RELOC.get(t, f'R_ARM_{t}'), si, syms[si].name if si in syms else '')
                     for o, si, t in r.relocations(i)]
    o = objplace.Obj(path or '', secs, syms, rels)
    isyms = [armtc.Sym(name, value, _bind(info), _sec(shndx), _type(info, name), size or None)
             for j, (name, value, size, info, other, shndx) in enumerate(r.symbols) if j]
    info = armtc.ObjectInfo({i: (s.name, s.size) for i, s in secs.items()}, isyms,
                            {i: s.align for i, s in secs.items()}, {i: s.flags for i, s in secs.items()},
                            {i: s.type for i, s in secs.items()})
    return o, info


def main(argv):
    if not argv: sys.exit(__doc__)
    o, info = read(argv[0])
    for i, s in sorted(o.sections.items()):
        print(f'#{i:<3} {s.name:24} {s.type:18} {s.flags:36} off {s.offset:#x} size {s.size:#x} align {s.align}')
    for j, s in sorted(o.symbols.items()):
        print(f'  {j:4} {s.name:32} {s.value:#010x} {s.bind} {s.sec!s:4} {s.type}')
    for t, rs in sorted(o.relocs.items()):
        for r in rs:
            print(f'  rel #{t} {r.offset:#x} {r.tname} {r.sname}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
