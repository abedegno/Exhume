"""armlink 5.06's RW data compression, both ways: what __decompress in Arm's C library reads.

A control byte b: lit = b & 7 (0: the next byte), n = b >> 4 (0: the next byte); then lit - 1
literal bytes; then, if b & 8, an offset byte and a copy of n + 2 bytes from out[-offset],
else n zero bytes. compress() makes the same choices as armlink (checked against armlink's own
output and a real firmware's stream): a back-reference when it is at least 3 bytes and longer
than the zero run at that point (window 255, nearest offset on a tie), else a zero run of at
least one byte, else a literal; literals ride on the next token."""


def decompress(src, out_len):
    """(bytes, input bytes used)."""
    out = bytearray(); i = 0
    while len(out) < out_len:
        b = src[i]; i += 1
        lit = b & 7
        if lit == 0: lit = src[i]; i += 1
        n = b >> 4
        if n == 0: n = src[i]; i += 1
        out += src[i:i + lit - 1]; i += lit - 1
        if b & 8:
            off = src[i]; i += 1
            for _ in range(n + 2): out.append(out[-off])
        else:
            out += bytes(n)
    return bytes(out), i


def compress(raw, window=255, minmatch=3):
    out = bytearray(); i = 0; lits = bytearray()

    def best_match(i):
        bl, bo = 0, 0
        for off in range(1, min(window, i) + 1):
            l = 0
            while i + l < len(raw) and raw[i + l - off] == raw[i + l] and l < 257: l += 1
            if l > bl: bl, bo = l, off
        return bl, bo

    def token(kind, n, off=None):
        nonlocal lits
        lit = len(lits) + 1
        out.append((0 if lit > 7 else lit) | (8 if kind == 'm' else 0) | ((n if 0 < n <= 15 else 0) << 4))
        if lit > 7: out.append(lit)
        if n > 15 or n == 0: out.append(n)
        out.extend(lits); lits = bytearray()
        if kind == 'm': out.append(off)

    while i < len(raw):
        z = 0
        while i + z < len(raw) and raw[i + z] == 0 and z < 255: z += 1
        ml, mo = best_match(i)
        if ml >= minmatch and ml > z:
            token('m', ml - 2, mo); i += ml
        elif z >= 1:
            token('z', z); i += z
        else:
            lits.append(raw[i]); i += 1
            if len(lits) == 254: token('z', 0)
    if lits: token('z', 0)
    return bytes(out)
