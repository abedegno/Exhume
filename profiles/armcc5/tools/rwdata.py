"""The initial RAM image of an armlink image: the region table __scatterload walks, each
region decompressed, copied or zeroed. Found by __scatterload's own code, never guessed.
Both C libraries are recognised: MicroLIB's __scatterload (--library_type=microlib), and the
standard library's (__scatterload_rt2), which finds its table through an adr and two offsets and
has region handlers of its own (__decompress1 and a zeroinit that stores 16 bytes at a time)."""
import struct
from dataclasses import dataclass
import lz77

# __scatterload in Arm's C library: ldr r4,[pc,#24]; ldr r5,[pc,#28]; b 2f; 1: ldr r0,[r4,#12];
# orr r3,r0,#1; ldmia r4,{r0-r2}; blx r3; adds r4,#16; 2: cmp r4,r5; bcc 1b
SCATTERLOAD = bytes.fromhex('064c074d06e0e06840f0010394e8070098471034ac42f6d3')
# the standard library's: adr r0,T; ldmia.w r0,{r10,r11}; add r10,r0; add r11,r0; sub.w r7,r10,#1;
# cmp r10,r11 (the adr's offset varies, so the pattern starts after it); T holds the table's base
# and limit as offsets from T
SCATTERLOAD_RT2 = bytes.fromhex('90e8000c82448344aaf10107da45')
# the region handlers, by their code (MicroLIB's first, then the standard library's)
HANDLERS = {
    'compressed': bytes.fromhex('70b58c1810f8015b15f0070301d110f8013b2a11'),   # __decompress
    'zero': bytes.fromhex('002001e001c1121f002afbd17047'),                      # __scatterload_zeroinit
    'copy': bytes.fromhex('02e008c8121f08c1002afad17047'),                      # __scatterload_copy
    'compressed ': bytes.fromhex('0a444ff0000c10f8013b13f0070408bf10f8014b1d11'),   # __decompress1 (standard)
    'zero ': bytes.fromhex('0023002400250026103a28bf78c1fbd8'),                 # __scatterload_zeroinit (standard)
}


@dataclass
class Region:
    load: int
    exec: int
    size: int
    kind: str          # compressed, copy, zero


def _kind(img, fn):
    a = fn & ~1
    for kind, code in HANDLERS.items():
        try:
            if img.bytes(a, len(code)) == code: return kind.strip()
        except ValueError:
            pass
    raise ValueError(f'region handler 0x{fn:08X} is not one of MicroLIB\'s')


def _table_rt2(img):
    """(start, end) of the standard library's region table, or None"""
    at = img.data.find(SCATTERLOAD_RT2)
    if at < 2: return None
    adr = struct.unpack_from('<H', img.data, at - 2)[0]
    if adr & 0xF800 != 0xA000 or (adr >> 8) & 7 != 0: return None      # adr r0, #imm8*4
    pc = img.base + at - 2
    t = ((pc + 4) & ~3) + (adr & 0xFF) * 4
    lo, hi = struct.unpack_from('<II', img.data, t - img.base)
    return (t + lo) & 0xFFFFFFFF, (t + hi) & 0xFFFFFFFF


def find_table(img):
    at = img.data.find(SCATTERLOAD)
    rt2 = _table_rt2(img) if at < 0 else None
    if at < 0 and rt2 is None: raise ValueError('no __scatterload region table found')
    if rt2: start, end = rt2
    else:
        pc = img.base + at
        start, end = struct.unpack_from('<II', img.data, ((pc + 4) & ~3) + 24 - img.base)
    if not (img.base <= start <= end <= img.end) or (end - start) % 16:
        raise ValueError(f'region table 0x{start:08X}-0x{end:08X} is not inside the image')
    regions = []
    for a in range(start, end, 16):
        load, exe, size, fn = struct.unpack_from('<IIII', img.data, a - img.base)
        regions.append(Region(load, exe, size, _kind(img, fn)))
    return regions


class RamImage:
    def __init__(self, img, regions):
        self.parts = []          # (exec start, bytes)
        self.kinds = []          # (exec start, end, kind)
        self.used_compressed = None
        for r in regions:
            if r.kind == 'compressed':
                data, used = lz77.decompress(img.data[r.load - img.base:], r.size)
                self.used_compressed = (r.load, r.load + used)
            elif r.kind == 'copy':
                data = img.bytes(r.load, r.size)
            else:
                data = bytes(r.size)
            self.parts.append((r.exec, data))
            self.kinds.append((r.exec, r.exec + r.size, r.kind))

    def _at(self, x):
        for s, d in self.parts:
            if s <= x < s + len(d): return d[x - s]
        return None

    def kind_at(self, addr):
        return next((k for s, e, k in self.kinds if s <= addr < e), None)

    def covers(self, addr, n):
        return all(self._at(x) is not None for x in range(addr, addr + n))

    def bytes(self, addr, n):
        out = bytearray()
        for x in range(addr, addr + n):
            v = self._at(x)
            if v is None: raise ValueError(f'0x{x:08X} is in no RAM region')
            out.append(v)
        return bytes(out)


def ram_image(img):
    return RamImage(img, find_table(img))
