"""Make a port's icon files from its two SVG sources ([package] icon_dir, icon; docs/port.md,
"Packages for players").

    python3 tools/icons.py [--config PATH]     (needs node with puppeteer: npm install in Exhume;
                                                iconutil, on macOS, for the .icns)

ICON.svg is the icon, ICON-small.svg the same design simplified for 16 to 32 pixels, both in
[package] icon_dir (default tools/dist/icon, ICON default the [package] launcher name). The
project draws its own: no game artwork goes into a package. Written there, to be committed,
so that no build needs a renderer:

  png/ICON-N.png    N = 16, 24, 32, 48, 64, 128, 256, 512, 1024 (16..32 from ICON-small.svg)
  ICON.ico          Windows: 16, 24, 32, 48, 64 and 256 (the program's resource, portbuild.py)
  ICON.icns         macOS: the .app's icon (iconutil, so only on macOS; package.py copies it)
  ICON.png          Linux: 256 px for the .desktop file and the AppImage (package.py)
  [package] icon_header   48 px as RGBA bytes, for the runtime's PLAT_ICON (plat.h;
                    SDL_SetWindowIcon, not used on macOS)
"""
import os, sys, struct, zlib, shutil, subprocess, tempfile

here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
EXHUME = os.path.dirname(here)
import portcfg

SMALL = (16, 24, 32)
LARGE = (48, 64, 128, 256, 512, 1024)
WINDOW = 48


def render(svg, outdir, sizes):
    subprocess.run(['node', os.path.join(here, 'render-icon.mjs'), svg, outdir] + [str(s) for s in sizes],
                   check=True, cwd=EXHUME)


def read_rgba(path):
    """An 8-bit RGBA, non-interlaced PNG (as Chrome writes them): (w, h, bytes)."""
    d = open(path, 'rb').read(); p = 8; idat = b''; w = h = 0
    while p < len(d):
        n = struct.unpack_from('>I', d, p)[0]; t = d[p + 4:p + 8]; b = d[p + 8:p + 8 + n]; p += 12 + n
        if t == b'IHDR':
            w, h, depth, ctype, _, _, inter = struct.unpack('>IIBBBBB', b)
            if (depth, ctype, inter) != (8, 6, 0): sys.exit(f'{path}: not 8-bit RGBA without interlace')
        elif t == b'IDAT': idat += b
    raw = zlib.decompress(idat); bpp = 4; stride = w * bpp; out = bytearray(); prev = bytearray(stride)
    for y in range(h):
        f = raw[y * (stride + 1)]; line = bytearray(raw[y * (stride + 1) + 1:(y + 1) * (stride + 1)])
        for x in range(stride):
            a = line[x - bpp] if x >= bpp else 0; b = prev[x]; c = prev[x - bpp] if x >= bpp else 0
            if f == 1: line[x] = (line[x] + a) & 255
            elif f == 2: line[x] = (line[x] + b) & 255
            elif f == 3: line[x] = (line[x] + (a + b) // 2) & 255
            elif f == 4:
                pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                line[x] = (line[x] + (a if pa <= pb and pa <= pc else b if pb <= pc else c)) & 255
        out += line; prev = line
    return w, h, bytes(out)


def write_ico(path, pngs):
    """An .ico of PNG entries (Windows Vista and later read them at every size)."""
    data = [open(p, 'rb').read() for _, p in pngs]
    head = struct.pack('<HHH', 0, 1, len(pngs)); off = 6 + 16 * len(pngs); dirs = b''
    for (s, _), d in zip(pngs, data):
        dirs += struct.pack('<BBBBHHII', s % 256, s % 256, 0, 0, 1, 32, len(d), off); off += len(d)
    open(path, 'wb').write(head + dirs + b''.join(data))


def write_header(path, w, h, rgba, svg):
    rows = [', '.join(f'0x{b:02x}' for b in rgba[i:i + 16]) for i in range(0, len(rgba), 16)]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w') as f:
        f.write(f'/* {os.path.basename(path)}: written by Exhume\'s tools/icons.py from {svg}; do not edit.\n'
                f'   The window icon (PLAT_ICON, the runtime\'s platform/plat.h; SDL_SetWindowIcon): {w} by {h}\n'
                '   pixels, RGBA, rows from the top. */\n'
                f'#define PLAT_ICON_W {w}\n#define PLAT_ICON_H {h}\n'
                'static const unsigned char plat_icon_rgba[] = {\n    ' + ',\n    '.join(rows) + '\n};\n')


def main():
    cfg, argv = portcfg.cli()
    K = portcfg.package(cfg)
    d, n = K.icon_dir, K.icon
    big, small = os.path.join(d, n + '.svg'), os.path.join(d, n + '-small.svg')
    for s in (big, small):
        if not os.path.exists(s): sys.exit(f'icons.py: no {os.path.relpath(s, cfg.root)} ([package] icon_dir, icon)')
    tmp = tempfile.mkdtemp()
    try:
        render(small, tmp, SMALL)
        render(big, tmp, LARGE)
        pd = os.path.join(d, 'png'); os.makedirs(pd, exist_ok=True)
        png = {s: os.path.join(pd, f'{n}-{s}.png') for s in SMALL + LARGE}
        for s, p in png.items(): shutil.copy(os.path.join(tmp, f'{s}.png'), p)
        write_ico(os.path.join(d, n + '.ico'), [(s, png[s]) for s in (16, 24, 32, 48, 64, 256)])
        shutil.copy(png[256], os.path.join(d, n + '.png'))
        w, h, rgba = read_rgba(png[WINDOW])
        write_header(K.icon_header, w, h, rgba, os.path.relpath(big, cfg.root))
        if shutil.which('iconutil'):
            iset = os.path.join(tmp, n + '.iconset'); os.makedirs(iset)
            for s in (16, 32, 128, 256, 512):
                shutil.copy(png[s], os.path.join(iset, f'icon_{s}x{s}.png'))
                shutil.copy(png[s * 2] if s * 2 in png else png[1024], os.path.join(iset, f'icon_{s}x{s}@2x.png'))
            subprocess.run(['iconutil', '-c', 'icns', iset, '-o', os.path.join(d, n + '.icns')], check=True)
        else:
            print(f'icons.py: no iconutil (macOS only); {n}.icns not remade')
        for f in sorted(os.listdir(d)) + ['png/' + f for f in sorted(os.listdir(pd))]:
            p = os.path.join(d, f)
            if os.path.isfile(p) and not f.endswith('.svg'): print(f'{f}: {os.path.getsize(p)} bytes')
        print(f'{os.path.relpath(K.icon_header, cfg.root)}: {os.path.getsize(K.icon_header)} bytes')
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
