"""Unpack an LZEXE 0.90/0.91-packed DOS executable from its bytes alone (the decompressor
inside the file is never run).  Format as described by UNLZEXE (Mitugu Kurizono, 1991).

    python3 unlzexe.py PACKED.EXE OUT.EXE
"""
import struct, sys

def unpack(data):
    if data[0x1C:0x20] not in (b'LZ91', b'LZ09'):
        raise SystemExit('not LZEXE 0.90/0.91')
    v91 = data[0x1C:0x20] == b'LZ91'
    hdr_paras = struct.unpack_from('<H', data, 8)[0]
    init_ip, init_cs = struct.unpack_from('<HH', data, 0x14)
    image = hdr_paras * 16
    loader = image + init_cs * 16
    ip, cs, sp, ss, comp_paras, inc_paras, loader_size, checksum = struct.unpack_from('<8H', data, loader)
    # the compressed data: from the image's start, read forward
    src = image
    out = bytearray()
    def getw():
        nonlocal src
        w = struct.unpack_from('<H', data, src)[0]
        src += 2
        return w
    def getb():
        nonlocal src
        b = data[src]
        src += 1
        return b
    buf, count = getw(), 16
    def bit():
        nonlocal buf, count
        b = buf & 1
        count -= 1
        if count == 0:
            buf, count = getw(), 16
        else:
            buf >>= 1
        return b
    while True:
        if bit():
            out.append(getb())
            continue
        if not bit():
            length = (bit() << 1) | bit()
            length += 2
            span = getb() | 0xFF00
        else:
            span = getb()
            length = getb()
            span |= ((length & ~7) << 5) | 0xE000
            length = (length & 7) + 2
            if length == 2:
                length = getb()
                if length == 0:
                    break
                if length == 1:
                    continue
                length += 1
        span -= 0x10000                     # a negative 16-bit distance
        for _ in range(length):
            out.append(out[span])
    # relocations
    relocs = []
    if v91:
        p = loader + 0x158
        seg = off = 0
        while True:
            span = data[p]; p += 1
            if span == 0:
                span = struct.unpack_from('<H', data, p)[0]; p += 2
                if span == 0:
                    seg += 0x0FFF
                    continue
                if span == 1:
                    break
            off += span
            seg += (off & ~0x0F) >> 4
            off &= 0x0F
            relocs.append((off, seg))
    else:
        p = loader + 0x19D
        seg = 0
        while True:
            n = struct.unpack_from('<H', data, p)[0]; p += 2
            for _ in range(n):
                o = struct.unpack_from('<H', data, p)[0]; p += 2
                relocs.append((o, seg))
            if seg >= 0xF000:
                break
            seg += 0x1000
    # a plain MZ around the image
    header_paras = (0x1C + 4 * len(relocs) + 15) // 16
    size = header_paras * 16 + len(out)
    min_alloc = max(0, inc_paras - (len(out) + 15) // 16 + struct.unpack_from('<H', data, 0x0A)[0])
    hdr = bytearray(header_paras * 16)
    struct.pack_into('<2sHHHHHHHHHHHHH', hdr, 0, b'MZ', size % 512, (size + 511) // 512, len(relocs),
                     header_paras, min_alloc, 0xFFFF, ss, sp, 0, ip, cs, 0x1C, 0)
    for k, (o, s) in enumerate(relocs):
        struct.pack_into('<HH', hdr, 0x1C + 4 * k, o, s)
    return bytes(hdr) + bytes(out), relocs, (cs, ip, ss, sp)

if __name__ == '__main__':
    data = open(sys.argv[1], 'rb').read()
    exe, relocs, regs = unpack(data)
    open(sys.argv[2], 'wb').write(exe)
    print(f'{sys.argv[1]}: {len(data)} bytes packed, {len(exe)} unpacked, {len(relocs)} relocations, '
          f'CS:IP {regs[0]:04X}:{regs[1]:04X} SS:SP {regs[2]:04X}:{regs[3]:04X}')
