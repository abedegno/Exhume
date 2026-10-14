"""The target image: Intel HEX (the .ehex files some vendors ship) or a raw binary."""


def read(path):
    """(base address, bytes) of an Intel HEX file; gaps are 0xFF. A file that does not start
    with ':' is a raw image at address 0."""
    with open(path, 'rb') as f: raw = f.read()
    if not raw.startswith(b':'): return 0, raw
    upper, mem = 0, {}
    for n, line in enumerate(raw.decode('ascii').replace('\r', '\n').split('\n'), 1):
        line = line.strip()
        if not line: continue
        if not line.startswith(':'): raise ValueError(f'{path}:{n}: not an Intel HEX record')
        b = bytes.fromhex(line[1:])
        if len(b) < 5 or len(b) != b[0] + 5 or sum(b) & 0xFF:
            raise ValueError(f'{path}:{n}: bad length or checksum')
        kind, addr, payload = b[3], (b[1] << 8) | b[2], b[4:-1]
        if kind == 0:
            for i, x in enumerate(payload): mem[upper + addr + i] = x
        elif kind == 4: upper = int.from_bytes(payload, 'big') << 16
        elif kind == 2: upper = int.from_bytes(payload, 'big') << 4
        elif kind == 1: break
    if not mem: raise ValueError(f'{path}: no data records')
    lo, hi = min(mem), max(mem)
    return lo, bytes(mem.get(a, 0xFF) for a in range(lo, hi + 1))


class Image:
    def __init__(self, path):
        self.path = path
        self.base, self.data = read(path)
        self.end = self.base + len(self.data)

    def bytes(self, addr, n):
        if addr < self.base or addr + n > self.end:
            raise ValueError(f'0x{addr:08X}+0x{n:X} is outside the image 0x{self.base:08X}-0x{self.end:08X}')
        return self.data[addr - self.base:addr - self.base + n]


def _record(kind, addr, payload):
    b = bytes([len(payload), (addr >> 8) & 0xFF, addr & 0xFF, kind]) + payload
    return ':' + (b + bytes([(-sum(b)) & 0xFF])).hex().upper()


def layout(path):
    """How an Intel HEX file is written, so another can be written the same way: (bytes per
    data record, line end, start address or None). fromelf --i32 writes 16-byte records, an
    extended linear address record at the start and at each 64 KB boundary, a start linear
    address record (the entry point), the end record, CRLF."""
    with open(path, 'rb') as f: raw = f.read()
    end = '\r\n' if b'\r\n' in raw else '\n'
    width = entry = None
    for line in raw.decode('ascii').split():
        if not line.startswith(':'): continue
        b = bytes.fromhex(line[1:])
        if b[3] == 0 and width is None: width = b[0]
        if b[3] == 5: entry = int.from_bytes(b[4:8], 'big')
    return width or 16, end, entry


def write(base, data, like=None, width=16, end='\r\n', entry=None):
    """Intel HEX text for data at base; like: an Intel HEX file whose layout to copy."""
    if like: width, end, entry = layout(like)
    lines, upper, off = [], None, 0
    while off < len(data):
        a = base + off
        n = min(width, len(data) - off, 0x10000 - (a & 0xFFFF))    # a record never crosses 64 KB
        if a >> 16 != upper:
            upper = a >> 16
            lines.append(_record(4, 0, upper.to_bytes(2, 'big')))
        lines.append(_record(0, a & 0xFFFF, data[off:off + n]))
        off += n
    if entry is not None: lines.append(_record(5, 0, entry.to_bytes(4, 'big')))
    lines.append(_record(1, 0, b''))
    return end.join(lines) + end
