"""Images, symbol tables and C definitions for the emulator tools (difftest.py, sysemu.py).

An image is the flash as the chip holds it: an Intel HEX or raw file (ehex.read), or an ELF's
loadable segments at their load addresses (so initialised RAM data sits in flash where the
startup code copies it from). Symbols come from an ELF's .symtab (armlink's .axf or GNU ld's
.elf), from the project's map (map/functions.tsv and map/data.tsv), or from a TSV."""
import glob, os, re, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import ehex

RUNTIME = re.compile(r'^(__aeabi_|__rt_|__ARM_|__scatter|__main|__decompress|_main_|memcpy$|memset$|memmove$|'
                     r'memclr|strlen$|strcmp$|__udivmoddi4$|__divmoddi4$)')


class Image:
    def __init__(self, base, data):
        self.base, self.data = base, bytes(data)
        self.end = self.base + len(self.data)

    def has(self, addr, n=1):
        return self.base <= addr and addr + n <= self.end

    def bytes(self, addr, n):
        return self.data[addr - self.base:addr - self.base + n]

    def _disasm(self, start, size):
        from capstone import Cs, CS_ARCH_ARM, CS_MODE_THUMB, CS_MODE_MCLASS
        md = Cs(CS_ARCH_ARM, CS_MODE_THUMB | CS_MODE_MCLASS)
        return [i.address for i in md.disasm(self.bytes(start, size), start)]

    def block_instructions(self, blocks):
        """The instruction addresses in executed blocks, given as (address, size)."""
        cache = self.__dict__.setdefault('_bcache', {})
        out = set()
        for a, n in set(blocks):
            if (a, n) not in cache: cache[(a, n)] = self._disasm(a, n)
            out.update(cache[(a, n)])
        return out

    def instructions_covered(self, start, size, covered):
        """The share of the instructions in [start, start+size) whose address is in covered. A
        literal pool decodes as instructions too, so a function never reaches 100%."""
        addrs = self._disasm(start, size)
        return sum(1 for a in addrs if a in covered) / len(addrs) if addrs else 0.0


def is_elf(path):
    with open(path, 'rb') as f: return f.read(4) == b'\x7fELF'


def _sections(raw):
    shoff, = struct.unpack_from('<I', raw, 0x20)
    shentsize, shnum, shstrndx = struct.unpack_from('<HHH', raw, 0x2E)
    secs = [struct.unpack_from('<IIIIIIIIII', raw, shoff + i * shentsize) for i in range(shnum)]
    names = secs[shstrndx][4] if shnum else 0
    out = []
    for sh in secs:
        e = raw.index(b'\0', names + sh[0])
        out.append((raw[names + sh[0]:e].decode('latin1'),) + sh)
    return out          # (name, sh_name, type, flags, addr, offset, size, link, info, align, entsize)


def load_image(path):
    """The flash bytes of a HEX, raw or ELF file. Gaps are 0xFF, as erased flash reads."""
    if not is_elf(path):
        return Image(*ehex.read(path))
    with open(path, 'rb') as f: raw = f.read()
    phoff, = struct.unpack_from('<I', raw, 0x1C)
    phentsize, phnum = struct.unpack_from('<HH', raw, 0x2A)
    mem = []
    for i in range(phnum):
        typ, off, vaddr, paddr, filesz, memsz, flags, align = struct.unpack_from('<8I', raw, phoff + i * phentsize)
        if typ == 1 and filesz: mem.append((paddr, raw[off:off + filesz]))
    if not mem: raise ValueError(f'{path}: no loadable segments')
    # segments far above the flash (RAM loaded in place, never the case for a firmware) are dropped
    lo = min(a for a, _ in mem)
    mem = [(a, d) for a, d in mem if a - lo < 0x1000000]
    hi = max(a + len(d) for a, d in mem)
    data = bytearray(b'\xff' * (hi - lo))
    for a, d in mem: data[a - lo:a - lo + len(d)] = d
    return Image(lo, data)


class Symbols:
    def __init__(self):
        self.funcs = {}   # name -> (addr, size), addr without the Thumb bit
        self.data = {}    # name -> (addr, size)

    def all(self):
        return {**self.data, **self.funcs}


def elf_symbols(raw):
    """(name, value, size, 'func'|'object'|'other') from a 32-bit little-endian ELF's .symtab."""
    secs = _sections(raw)
    for s in secs:
        if s[2] == 2:                                   # SHT_SYMTAB
            strtab = secs[s[7]][5]
            for off in range(s[5], s[5] + s[6], 16):
                nm, value, size, info, other, shndx = struct.unpack_from('<IIIBBH', raw, off)
                if not nm: continue
                name = raw[strtab + nm:raw.index(b'\0', strtab + nm)].decode('latin1')
                if name.startswith('$'): continue       # mapping symbols ($t, $d)
                yield name, value, size, {2: 'func', 1: 'object'}.get(info & 0xF, 'other')


def load_symbols(path):
    """A build's symbols: an ELF, a map directory (functions.tsv, data.tsv), a map-format TSV
    (addr, size, name, kind, file, with its header), or a TSV of name, addr, size[, code|data]."""
    s = Symbols()
    if os.path.isdir(path):
        for fn, dest in (('functions.tsv', s.funcs), ('data.tsv', s.data)):
            p = os.path.join(path, fn)
            if os.path.exists(p): _map_tsv(p, dest)
        return s
    with open(path, 'rb') as f: raw = f.read()
    if raw[:4] == b'\x7fELF':
        for name, value, size, kind in elf_symbols(raw):
            if kind == 'func': s.funcs[name] = (value & ~1, size)
            elif kind == 'object': s.data[name] = (value, size)
        return s
    text = raw.decode('latin1')
    if text.startswith('addr\tsize\tname'):
        for name, (a, n, kind) in _map_rows(path):
            (s.data if kind in ('ram', 'ro', 'data') else s.funcs)[name] = (a, n)
        return s
    for line in text.split('\n'):
        r = line.rstrip('\r').split('\t')
        if len(r) < 3 or r[0] in ('name', '') or r[0].startswith('#'): continue
        a, n = int(r[1], 16), int(r[2], 16)
        code = r[3] in ('code', 'func') if len(r) > 3 else bool(a & 1)
        (s.funcs if code else s.data)[r[0]] = (a & ~1, n) if code else (a, n)
    return s


def _map_rows(path):
    with open(path, encoding='latin1') as f: text = f.read()
    for n, line in enumerate(text.split('\n')):
        f = line.rstrip('\r').split('\t')
        if n == 0 or len(f) < 3 or not f[0]: continue
        yield f[2], (int(f[0], 16), int(f[1], 16), f[3] if len(f) > 3 else '')


def _map_tsv(path, dest):
    for name, (a, n, kind) in _map_rows(path): dest[name] = (a, n)


def map_kinds(mapdir):
    """name -> kind (code, asm, lib, ram, ro) from a map directory."""
    out = {}
    for fn in ('functions.tsv', 'data.tsv'):
        p = os.path.join(mapdir, fn)
        if os.path.exists(p):
            for name, (a, n, kind) in _map_rows(p): out[name] = kind
    return out


def ram_init(path, ram):
    """{address: bytes} of the RAM a build's startup initialises (RW data and zeroed ZI), keyed by
    the start of each region. An ELF gives its writable allocated sections in RAM; an armlink
    image gives the regions its scatter-load table describes (rwdata.py, MicroLIB's handlers)."""
    lo, hi = ram
    if is_elf(path):
        with open(path, 'rb') as f: raw = f.read()
        out = {}
        for name, _, typ, flags, addr, off, size, *_ in _sections(raw):
            if flags & 3 == 3 and lo <= addr < hi and size:          # SHF_WRITE | SHF_ALLOC
                out[addr] = raw[off:off + size] if typ == 1 else bytes(size)
        return out
    import rwdata
    try:
        r = rwdata.ram_image(ehex.Image(path))
    except ValueError:
        return {}
    return {s: bytes(d) for s, d in r.parts}


def init_bytes(regions, addr, size):
    """The initial bytes of [addr, addr+size) from ram_init's regions, or None if not covered."""
    for s, d in regions.items():
        if s <= addr and addr + size <= s + len(d): return d[addr - s:addr - s + size]
    return None


_DEF = re.compile(r'(?m)^([A-Za-z_][^\n;{}()#=]*?)\b([A-Za-z_]\w*)\s*\(([^()]*)\)\s*\{')
_KEYWORDS = {'if', 'while', 'for', 'switch', 'return', 'sizeof', 'do', 'else'}


def c_definitions(dirs):
    """name -> (return type text, parameter text, file) for every function defined at the start
    of a line in the .c files under dirs. Enough for difftest's argument and return widths."""
    out = {}
    for d in dirs:
        if not d or not os.path.isdir(d): continue
        for p in sorted(glob.glob(os.path.join(d, '**', '*.c'), recursive=True)):
            with open(p, encoding='latin1') as f: text = f.read()
            text = re.sub(r'/\*.*?\*/', lambda m: ' ' * len(m.group(0)), text, flags=re.S)
            for m in _DEF.finditer(text):
                head, name, params = m.groups()
                if name in _KEYWORDS or head.strip() in _KEYWORDS: continue
                out.setdefault(name, (head.strip(), params.strip(), p))
    return out
