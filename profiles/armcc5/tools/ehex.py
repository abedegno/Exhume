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
