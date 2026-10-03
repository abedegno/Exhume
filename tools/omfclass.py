"""List or change the class names of an OMF object's segments.

    python3 tools/omfclass.py OBJ                          list each segment: name, class, length
    python3 tools/omfclass.py OBJ SEGNAME CLASS [-o OUT]   give segment SEGNAME the class CLASS
                                                           (writes OUT, default OBJ in place)

A linker uses a segment's class but no EXE stores it. TLINK lays segments out in blocks by
class and sets the overlay segment table's code flag from it (profiles/borland-tc101/
linker.md, "Segment classes"), so when a relink differs from the original only in that table's
flags or in the order of whole blocks of segments, the quick experiment is to relink with
some objects' classes changed, before touching their sources. This rewrites the object
directly, so no assembler is needed for the experiment; once a pattern links the original,
change the sources to match.

The class is an LNAMES entry the SEGDEF points to. When no other SEGDEF or GRPDEF uses that
entry it is rewritten in place; otherwise a new LNAMES record holding CLASS is added after the
last LNAMES record and the SEGDEF is pointed at it (which needs the SEGDEF to come after
that record). Each changed record's checksum is recomputed. Everything else is copied as is."""
import os, struct, sys
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
from omf import idx


def _records(d):
    """(position, type, body without checksum) for each record, to the module end."""
    p = 0
    while p + 3 <= len(d):
        t = d[p]; n = struct.unpack_from('<H', d, p + 1)[0]
        yield p, t, d[p + 3:p + 2 + n]
        p += 3 + n
        if t in (0x8A, 0x8B): return


def _record(t, body):
    r = bytearray([t]) + struct.pack('<H', len(body) + 1) + body + b'\0'
    r[-1] = (-sum(r)) & 0xFF
    return bytes(r)


def _enc(v):
    return bytes([v]) if v < 0x80 else bytes([0x80 | v >> 8, v & 0xFF])


def _segdef(t, b):
    """(name index, class index, offset of the class index in the body, its end)."""
    i = 1 + (3 if b[0] >> 5 == 0 else 0) + (4 if t == 0x99 else 2)
    sn, i = idx(b, i); c0 = i; cn, i = idx(b, i)
    return sn, cn, c0, i


def segments(d):
    """[(segment name, class, length)] in SEGDEF order."""
    names = ['']; out = []
    for p, t, b in _records(d):
        if t == 0x96:
            i = 0
            while i < len(b): l = b[i]; names.append(b[i + 1:i + 1 + l].decode('latin1')); i += 1 + l
        elif t in (0x98, 0x99):
            sn, cn, _, _ = _segdef(t, b)
            ln = struct.unpack_from('<I' if t == 0x99 else '<H', b, 1 + (3 if b[0] >> 5 == 0 else 0))[0]
            out.append((names[sn], names[cn], ln))
    return out


def set_class(d, segname, cls):
    """The object d with segment segname's class set to cls."""
    names = ['']; uses = {}; segdefs = []; last_lnames = None
    for p, t, b in _records(d):
        if t == 0x96:
            last_lnames = p; i = 0
            while i < len(b): l = b[i]; names.append(b[i + 1:i + 1 + l].decode('latin1')); i += 1 + l
        elif t in (0x98, 0x99):
            sn, cn, _, i = _segdef(t, b); on, _ = idx(b, i)
            segdefs.append((p, names[sn], cn))
            for k in (sn, cn, on): uses[k] = uses.get(k, 0) + 1
        elif t == 0x9A:
            g, _ = idx(b, 0); uses[g] = uses.get(g, 0) + 1
    mine = [(p, cn) for p, sn, cn in segdefs if sn == segname]
    if not mine: raise SystemExit(f'no segment {segname}: {sorted({s for _, s, _ in segdefs})}')
    shared = any(uses[cn] > 1 for _, cn in mine) or len({cn for _, cn in mine}) > 1
    new = len(names)                      # the index a new LNAMES entry would get
    if shared and any(p < last_lnames for p, _ in mine):
        raise SystemExit(f'{segname}: its class name is shared and its SEGDEF comes before the last LNAMES record')
    out = bytearray(); k = 0
    for p, t, b in _records(d):
        raw = d[p:p + 3 + len(b) + 1]
        if t == 0x96 and not shared:
            nb = bytearray(); i = 0
            while i < len(b):
                l = b[i]; nm = b[i + 1:i + 1 + l]; i += 1 + l; k += 1
                if any(k == cn for _, cn in mine): nm = cls.encode('latin1')
                nb += bytes([len(nm)]) + nm
            out += _record(t, nb)
        elif t in (0x98, 0x99) and shared and any(p == q for q, _ in mine):
            _, _, c0, c1 = _segdef(t, b)
            out += _record(t, b[:c0] + _enc(new) + b[c1:])
        else:
            out += raw
        if shared and p == last_lnames:
            out += _record(0x96, bytes([len(cls)]) + cls.encode('latin1'))
    end = sum(3 + struct.unpack_from('<H', d, p + 1)[0] for p, _, _ in _records(d))
    return bytes(out) + d[end:]


def main(argv):
    if not argv or argv[0].startswith('-'): sys.exit(__doc__)
    path = argv[0]; d = open(path, 'rb').read()
    if len(argv) == 1:
        for n, c, ln in segments(d): print(f'{n:<20} {c:<12} {ln:#06x}')
        return
    if len(argv) < 3: sys.exit(__doc__)
    out = argv[argv.index('-o') + 1] if '-o' in argv else path
    r = set_class(d, argv[1], argv[2])
    open(out, 'wb').write(r)
    for n, c, ln in segments(r):
        if n == argv[1]: print(f'{os.path.basename(out)}: {n} class {c}')


if __name__ == '__main__':
    main(sys.argv[1:])
