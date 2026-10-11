"""The map of a linked image: map/functions.tsv and map/data.tsv, names to addresses, and the
symdefs and scatter files the matcher links with.

Rows: addr and size in hex without 0x; a function's size includes its literal pool. A
placeholder name carries its address (sub_ code, g_ RAM, c_ flash data); a source names a
symbol the map does not know yet with /* at: name 0xADDR, name2 0xADDR */."""
import os, re
from dataclasses import dataclass

HEADER = 'addr\tsize\tname\tkind\tfile'
_PH = re.compile(r'^(sub|g|c)_([0-9A-Fa-f]{8})$')
_AT = re.compile(r'/\*\s*at:\s*(.*?)\*/|^[ \t]*;[ \t]*at:([^\n]*)', re.S | re.M)       # C comment, or an assembler ; line
_ATENTRY = re.compile(r'([A-Za-z_]\w*)\s+(0x[0-9A-Fa-f]+)')
_DROPPED = re.compile(r'/\*\s*dropped:\s*(.*?)\*/|^[ \t]*;[ \t]*dropped:([^\n]*)', re.S | re.M)


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
    for c_block, asm_line in _DROPPED.findall(text): out.update(re.findall(r'[A-Za-z_]\w*', c_block or asm_line))
    return out


_UNUSED = re.compile(r'/\*\s*unused:\s*dropped\s*\*/|^[ \t]*;[ \t]*unused:[ \t]*dropped[ \t]*$', re.M)


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
            kind = 'T' if r.name in self.code_names else 'D'
            if r.name not in exclude: out.append((r.addr, kind, r.name))
            # a renamed row keeps its placeholder as an alias, so older sources still link
            alias = (('sub_' if kind == 'T' else 'g_' if r.addr >= 0x20000000 else 'c_') + f'{r.addr:08X}')
            if placeholder(r.name) is None and alias not in exclude: out.append((r.addr, kind, alias))
        for name, addr in extra.items():
            if name in exclude or name in self.by_name: continue
            out.append((addr, 'D' if addr >= 0x20000000 else 'T', name))
        return out


def symdefs_text(entries):
    lines = ['#<SYMDEFS># ARM Linker, 5060960: Last Updated: Thu Jan  1 00:00:00 1970']
    for addr, kind, name in sorted(entries):
        lines.append(f'0x{addr | (1 if kind == "T" else 0):08x} {kind} {name}')
    return '\n'.join(lines) + '\n'


def _kind(section):
    return 'rw' if section == '.data' else 'zi' if section == '.bss' else 'ro'


def groups(placements):
    """Placements (section, addr, size) in address order, grouped where a section starts off a
    4-byte boundary exactly where the one before it ends: one region holds the group (its first
    section pinned +FIRST), so armlink never rounds one section's region up over the next. Every
    other section has a region of its own, so names never decide placement."""
    out = []
    for sec, addr, size in sorted(placements, key=lambda p: p[1]):
        if out:
            psec, paddr, psize = out[-1][-1]
            end = paddr + (psize or 0)
            same_space = (addr >= 0x20000000) == (paddr >= 0x20000000)
            # only a section that starts off a 4-byte boundary right where the one before ends
            # needs that one's region (armlink rounds a region's end up to 4)
            if same_space and addr % 4 and addr == end:
                out[-1].append((sec, addr, size)); continue
        out.append([(sec, addr, size)])
    return out


def pad_source(placements):
    """C for the pads the unaligned groups need, one section each (a section can be placed
    once): pad k for the k-th group that starts off a 4-byte boundary, as many bytes as it is past
    one, and of its first section's kind: armlink starts an execution region's RW part after its
    RO part on a 4-byte boundary, so the pad must match."""
    lines = ['/* exhume: pads for sections on addresses that are not 4-aligned */']
    k = 0
    for g in groups(placements):
        section, addr, _ = g[0]
        if not addr % 4: continue
        k += 1; n = addr % 4; kind = _kind(section)
        if kind == 'ro':
            lines.append(f'__attribute__((section(".pad{k}"))) const unsigned char exhume_pad{k}[{n}] = {{ 0 }};')
        elif kind == 'rw':
            lines.append(f'__attribute__((section(".pad{k}"))) unsigned char exhume_pad{k}[{n}] = {{ 1 }};')
        else:
            lines.append(f'__attribute__((section(".pad{k}"), zero_init)) unsigned char exhume_pad{k}[{n}];')
    return '\n'.join(lines) + '\n'


PAD_OBJ = 'exhume_pad.o'


def scatter_text(obj_name, placements):
    """Regions for placements (section, addr, size). Each group of adjacent sections (groups())
    is one execution region at its first address, FIXED, in its own load region; a group that
    starts off a 4-byte boundary goes in a region at the aligned address below, after a pad
    `+FIRST` (pad_source), in a load region of its own out of the way (link with
    --datacompressor off). Returns (text, {region: [(section, addr), ...]}); a region's output
    starts at its first address rounded down to 4."""
    lines, regions, k = [], {}, 0
    for i, g in enumerate(groups(placements), 1):
        regions[f'ER_{i}'] = [(sec, addr) for sec, addr, _ in g]
        first = g[0][1]
        pin = len(g) > 1 and not first % 4          # an unaligned group's pad is already +FIRST
        sel = ' '.join(f'{obj_name}({sec}{", +FIRST" if k == 0 and pin else ""})' for k, (sec, _, _) in enumerate(g))
        if first % 4:
            k += 1
            lines += [f'LR_{i} 0x{0x0E000000 + 0x10000 * k:08X} {{',
                      f'  ER_{i} 0x{first & ~3:08X} {{ {PAD_OBJ}(.pad{k}, +FIRST) {sel} }}', '}']
        else:
            lines += [f'LR_{i} 0x{first:08X} {{', f'  ER_{i} 0x{first:08X} FIXED {{ {sel} }}', '}']
    return '\n'.join(lines) + '\n', regions
