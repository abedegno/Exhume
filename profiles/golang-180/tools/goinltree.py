#!/usr/bin/env python3
"""goinltree.py: the inlining tree of functions in a Go >= 1.9 ELF binary.

    goinltree.py BINARY NAME_REGEX [-l]

Go 1.9 and later record every inlined call for the runtime's tracebacks (FUNCDATA_InlTree = 2,
PCDATA_InlTreeIndex = 1), and `-ldflags "-s -w"` does not remove them. For each matching function
this prints its size and line range, and for every inlined call the callee's name, the line of the
call in the caller and the parent call when calls are nested. -l adds the pc ranges by line.

Use on a LATER release of the same program when the target itself was built with Go 1.8 or
earlier, which keep no such record (compiler.md 2d: the line table cannot show an inlined helper).
If the later release shows no inlined helper in a function, a fitted empty call in the old one
stands for something else. It also works as a quick way to compare one function across
releases: same size and line range under the same compiler means the same source.
"""
import re, struct, sys

def main(path, rx):
    d = open(path, "rb").read()
    assert d[:4] == b"\x7fELF" and d[4] == 2, "ELF64 only"
    shoff, = struct.unpack_from("<Q", d, 0x28); shentsize, shnum, shstrndx = struct.unpack_from("<HHH", d, 0x3A)
    secs = []
    for i in range(shnum):
        nm, ty, fl, addr, off, size = struct.unpack_from("<IIQQQQ", d, shoff + i * shentsize)
        secs.append((nm, ty, addr, off, size))
    stroff = secs[shstrndx][3]
    name = lambda n: d[stroff + n:d.index(b"\0", stroff + n)].decode()
    by = {name(s[0]): s for s in secs}
    def v2o(va):
        for nm, ty, addr, off, size in secs:
            if addr and addr <= va < addr + size and ty != 8: return off + (va - addr)
        raise KeyError(hex(va))
    _, _, pva, poff, psize = by[".gopclntab"]
    P = d[poff:poff + psize]
    magic, = struct.unpack_from("<I", P, 0); assert magic == 0xFFFFFFFB, hex(magic)
    quantum, ptr = P[6], P[7]; assert ptr == 8
    nfunc, = struct.unpack_from("<Q", P, 8)
    cstr = lambda o: P[o:P.index(b"\0", o)].decode(errors="replace")
    fileoff, = struct.unpack_from("<I", P, 8 + 8 + nfunc * 16 + 8)
    nfile, = struct.unpack_from("<I", P, fileoff)
    fname = lambda i: cstr(struct.unpack_from("<I", P, fileoff + 4 * i)[0]) if 0 < i < nfile else "?"
    def uvar(o):
        v = s = 0
        while True:
            b = P[o]; o += 1; v |= (b & 0x7F) << s; s += 7
            if b < 0x80: return v, o
    def pcvalue(off, entry):
        out, pc, val, o, first = [], entry, -1, off, True
        while True:
            uv, o = uvar(o)
            if uv == 0 and not first: break
            first = False
            val += (-(uv >> 1) - 1) if uv & 1 else (uv >> 1)
            dp, o = uvar(o); start = pc; pc += dp * quantum
            out.append((start, pc, val))
        return out
    pat = re.compile(rx)
    for i in range(nfunc):
        pc, foff = struct.unpack_from("<QQ", P, 16 + i * 16)
        entry, nameoff, args, _, pcsp, pcfile, pcln, npcdata, nfuncdata = struct.unpack_from("<QiiiiiiiI", P, foff)
        nm = cstr(nameoff)
        if not pat.search(nm): continue
        end, = struct.unpack_from("<Q", P, 16 + (i + 1) * 16)
        pcd = struct.unpack_from("<%di" % npcdata, P, foff + 40)
        fdo = (foff + 40 + 4 * npcdata + 7) & ~7
        fd = struct.unpack_from("<%dQ" % nfuncdata, P, fdo)
        lines = pcvalue(pcln, entry); files = pcvalue(pcfile, entry)
        print(f"FUNC {nm}  {end - entry} bytes  entry {entry:#x}  lines {min(v for _,_,v in lines)}-{max(v for _,_,v in lines)}  file {fname(files[0][2])}")
        if npcdata > 1 and nfuncdata > 2 and fd[2] and pcd[1]:
            idx = pcvalue(pcd[1], entry); mx = max(v for _, _, v in idx)
            to = v2o(fd[2]); tree = [struct.unpack_from("<iiii", d, to + 16 * k) for k in range(mx + 1)]
            def lineat(pc):
                for a, b, v in lines:
                    if a <= pc < b: return v
            for k, (parent, fi, line, fn) in enumerate(tree):
                rng = [(a - entry, b - entry) for a, b, v in idx if v == k]
                inner = sorted({lineat(a + entry) for a, b in rng})
                print(f"  INL {k}: {cstr(fn)}  called at {fname(fi).split('/')[-1]}:{line}  parent {parent}  pc {rng[:4]}{'...' if len(rng) > 4 else ''}  callee lines {inner[:6]}")
        else:
            print("  no inlining tree")
        if "-l" in sys.argv:
            print("  lines:", " ".join(f"{a - entry:#x}:{v}" for a, b, v in lines))

main(sys.argv[1], sys.argv[2])
