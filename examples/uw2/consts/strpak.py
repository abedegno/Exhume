import struct, sys, json
def load(path):
    d = open(path,'rb').read()
    n, = struct.unpack_from('<H', d, 0)
    nodes = [struct.unpack_from('<BBBB', d, 2+4*i) for i in range(n)]
    p = 2+4*n
    nb, = struct.unpack_from('<H', d, p); p += 2
    blocks = {}
    for i in range(nb):
        bid, off = struct.unpack_from('<HI', d, p+6*i)
        ns, = struct.unpack_from('<H', d, off)
        offs = struct.unpack_from('<%dH' % ns, d, off+2)
        base = off+2+2*ns
        strs = []
        for so in offs:
            pos = base+so; bit = 0; s = []
            while True:
                node = n-1
                while nodes[node][2] != 0xFF:
                    b = (d[pos] >> (7-bit)) & 1
                    bit += 1
                    if bit == 8: bit = 0; pos += 1
                    node = nodes[node][3] if b else nodes[node][2]
                c = chr(nodes[node][0])
                if c == '|': break
                s.append(c)
            strs.append(''.join(s))
        blocks[bid] = strs
    return blocks
if __name__ == '__main__':
    b = load(sys.argv[1])
    json.dump({str(k): v for k, v in b.items()}, open(sys.argv[2], 'w'), indent=0)
    for k in sorted(b): print(hex(k), len(b[k]), b[k][:3])
