"""Count the pixels that differ between two screenshots, for bisecting a layout fault.

    python3 tools/pngdiff.py A.png B.png [X0 Y0 X1 Y1]

Prints the number of differing pixels (in the rectangle X0..X1, Y0..Y1 when given; otherwise
the whole image) and exits 0 when there are none. Reads the 8-bit RGB and RGBA PNGs that
tools/rungame.mjs writes, with no library. A modding build that draws the same scene as the
exact build at the same point gives 0 when nothing in the scene animates; pick a rectangle
that holds what went wrong (UW2: the 3D view's objects) and nothing that moves on its own."""
import sys, struct, zlib


def load(path):
    d = open(path, 'rb').read()
    if d[:8] != b'\x89PNG\r\n\x1a\n': sys.exit(f'{path}: not a PNG')
    i = 8; idat = b''; w = h = ct = None
    while i < len(d):
        n = struct.unpack('>I', d[i:i + 4])[0]; t = d[i + 4:i + 8]; c = d[i + 8:i + 8 + n]; i += 12 + n
        if t == b'IHDR':
            w, h, bd, ct = struct.unpack('>IIBB', c[:10])
            if bd != 8 or ct not in (2, 6): sys.exit(f'{path}: only 8-bit RGB or RGBA PNGs are read')
        elif t == b'IDAT': idat += c
    raw = zlib.decompress(idat); bpp = 3 if ct == 2 else 4; stride = w * bpp
    rows = []; prev = bytearray(stride); p = 0
    for _ in range(h):
        f = raw[p]; line = bytearray(raw[p + 1:p + 1 + stride]); p += 1 + stride
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0; b = prev[x]; c = prev[x - bpp] if x >= bpp else 0
            if f == 1: line[x] = (line[x] + a) & 255
            elif f == 2: line[x] = (line[x] + b) & 255
            elif f == 3: line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        rows.append(bytes(line)); prev = line
    return w, h, bpp, rows


def main(argv):
    if len(argv) not in (2, 6): sys.exit(__doc__)
    wa, ha, ba, ra = load(argv[0]); wb, hb, bb, rb = load(argv[1])
    if (wa, ha) != (wb, hb): sys.exit(f'sizes differ: {wa}x{ha} and {wb}x{hb}')
    x0, y0, x1, y1 = map(int, argv[2:]) if len(argv) == 6 else (0, 0, wa, ha)
    n = 0
    for y in range(max(0, y0), min(ha, y1)):
        for x in range(max(0, x0), min(wa, x1)):
            if ra[y][x * ba:x * ba + 3] != rb[y][x * bb:x * bb + 3]: n += 1
    print(n)
    return 0 if n == 0 else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
