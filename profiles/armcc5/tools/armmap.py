"""The map of a linked image: map/functions.tsv and map/data.tsv, names to addresses, and the
symdefs and scatter files the matcher links with.

Rows: addr and size in hex without 0x; a function's size includes its literal pool. A
placeholder name carries its address (sub_ code, g_ RAM, c_ flash data); a source names a
symbol the map does not know yet with /* at: name 0xADDR, name2 0xADDR */."""
import os, re
from dataclasses import dataclass

HEADER = 'addr\tsize\tname\tkind\tfile'
_PH = re.compile(r'^(sub|g|c)_([0-9A-Fa-f]{8})$')
_AT = re.compile(r'/\*\s*at:\s*(.*?)\*/|^\s*;\s*at:\s*(.*)$', re.S | re.M)       # C comment, or an assembler ; line
_ATENTRY = re.compile(r'([A-Za-z_]\w*)\s+(0x[0-9A-Fa-f]+)')
_DROPPED = re.compile(r'/\*\s*dropped:\s*(.*?)\*/', re.S)


@dataclass
class Row:
    addr: int
    size: int
    name: str
    kind: str
    file: str


def load(path):
    if not os.path.exists(path): return []
    out = []
    with open(path) as f: lines = f.read().splitlines()
    for n, line in enumerate(lines):
        if n == 0 or not line.strip(): continue
        f = line.split('\t') + [''] * 5
        out.append(Row(int(f[0], 16), int(f[1], 16), f[2], f[3], f[4]))
    return sorted(out, key=lambda r: r.addr)


def save(path, rows):
    tmp = path + '.tmp'
    with open(tmp, 'w') as f:
        f.write(HEADER + '\n')
        for r in sorted(rows, key=lambda r: r.addr):
            f.write(f'{r.addr:08X}\t{r.size:X}\t{r.name}\t{r.kind}\t{r.file}\n')
    os.replace(tmp, path)


def placeholder(name):
    m = _PH.match(name)
    return int(m.group(2), 16) if m else None


def annotations(text):
    out = {}
    for c_block, asm_line in _AT.findall(text):
        for name, addr in _ATENTRY.findall(c_block or asm_line): out[name] = int(addr, 16)
    return out


def dropped(text):
    """Names a source marks /* dropped: a, b */: data the original's linker left out of the image
    because nothing referenced it, so it has no address and is never compared."""
    out = set()
    for block in _DROPPED.findall(text): out.update(re.findall(r'[A-Za-z_]\w*', block))
    return out


_UNUSED = re.compile(r'/\*\s*unused:\s*dropped\s*\*/|^\s*;\s*unused:\s*dropped\s*$', re.M)


def unused_dropped(text):
    """A file marked /* unused: dropped */ (a library source) may hold functions the image lacks:
    armlink removed them as unreferenced."""
    return bool(_UNUSED.search(text))


class Map:
    def __init__(self, functions, data):
        self.functions, self.data = functions, data
        self.by_name = {r.name: r for r in functions + data}
        self.code_names = {r.name for r in functions}

    @classmethod
    def from_dir(cls, mapdir):
        return cls(load(os.path.join(mapdir, 'functions.tsv')), load(os.path.join(mapdir, 'data.tsv')))

    def address(self, name, extra={}):
        if name in extra: return extra[name]
        if name in self.by_name: return self.by_name[name].addr
        return placeholder(name)

    def row_at(self, addr):
        for r in self.functions + self.data:
            if r.addr == addr: return r
        return None

    def symdef_entries(self, exclude, extra):
        out = []
        for r in self.functions + self.data:
            if r.name in exclude: continue
            out.append((r.addr, 'T' if r.name in self.code_names else 'D', r.name))
        for name, addr in extra.items():
            if name in exclude or name in self.by_name: continue
            out.append((addr, 'D' if addr >= 0x20000000 else 'T', name))
        return out


def symdefs_text(entries):
    lines = ['#<SYMDEFS># ARM Linker, 5060960: Last Updated: Thu Jan  1 00:00:00 1970']
    for addr, kind, name in sorted(entries):
        lines.append(f'0x{addr | (1 if kind == "T" else 0):08x} {kind} {name}')
    return '\n'.join(lines) + '\n'


# 1-3 pad bytes of each kind (RO, RW, ZI), each count in its own section, to put a section on an
# address that isn't 4-aligned. The pad must be the section's own kind: armlink starts an
# execution region's RW part after its RO part on a 4-byte boundary.
PAD_SOURCE = """/* exhume: pads for sections on addresses that aren't 4-aligned */
__attribute__((section(".padro1"))) const unsigned char exhume_padro1[1] = { 0 };
__attribute__((section(".padro2"))) const unsigned char exhume_padro2[2] = { 0 };
__attribute__((section(".padro3"))) const unsigned char exhume_padro3[3] = { 0 };
__attribute__((section(".padrw1"))) unsigned char exhume_padrw1[1] = { 1 };
__attribute__((section(".padrw2"))) unsigned char exhume_padrw2[2] = { 1 };
__attribute__((section(".padrw3"))) unsigned char exhume_padrw3[3] = { 1 };
__attribute__((section(".padzi1"), zero_init)) unsigned char exhume_padzi1[1];
__attribute__((section(".padzi2"), zero_init)) unsigned char exhume_padzi2[2];
__attribute__((section(".padzi3"), zero_init)) unsigned char exhume_padzi3[3];
"""
PAD_OBJ = 'exhume_pad.o'


def scatter_text(obj_name, placements):
    """One load and execution region per section, each FIXED at its address. armlink wants
    regions on 4-byte boundaries, and a .data of halfwords can sit on 2: such a section goes in an
    execution region at the aligned address below, after 1-3 bytes from exhume_pad.o (PAD_SOURCE),
    all in one load region out of the way (link with --datacompressor off). The pad bytes come
    first in that region's output; the matcher drops them (addr % 4)."""
    lines, regions, unaligned = [], {}, []
    for i, (section, addr) in enumerate(placements, 1):
        regions[f'ER_{i}'] = section
        if addr % 4:
            kind = 'rw' if section == '.data' else 'zi' if section == '.bss' else 'ro'
            unaligned.append(f'  ER_{i} 0x{addr & ~3:08X} {{ {PAD_OBJ}(.pad{kind}{addr % 4}, +FIRST) {obj_name}({section}) }}')
            continue
        lines += [f'LR_{i} 0x{addr:08X} {{', f'  ER_{i} 0x{addr:08X} FIXED {{ {obj_name}({section}) }}', '}']
    if unaligned:
        # the pads no section needs still have to go somewhere (the matcher links with --keep *)
        lines += ['LR_UNALIGNED 0x0E000000 {'] + unaligned + [f'  ER_PADS 0x0F800000 {{ {PAD_OBJ}(+RO +RW +ZI) }}', '}']
    return '\n'.join(lines) + '\n', regions
