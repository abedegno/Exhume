"""Fingerprint a C2000 runtime library against the image: where each member's sections are.

    python3 c28libfp.py [--lib PATH] [--libs GLOB] [--tsv]       (--config PATH, or $EXHUME_CONFIG)

The library (rts2800_fpu32.lib of the project's compiler by default, [c2000] rtslib) is
extracted with ar2000 into <build>/rts/<version>/, and every member's code and constant
sections are searched in the image word for word, with the bits that relocations write masked
(a call's target, a data page, an address). A section is placed when it fits exactly one place.
A section of fewer than 4 words fits too many places by chance, so it is placed only from a call:
when a placed member calls it (LCR) and the words at the target fit. A member's constants are
kept only when its code is in the image.

Prints one line per placed section, ADDR SIZE MEMBER SECTION SYMBOLS(offset), and a total.
--libs GLOB runs the search for every library the glob matches and prints one line each:
how many sections and words each places. The library that places the most is the image's; it can
be older than the compiler (compiler.md, "Fingerprinting the version"). --tsv prints map rows
(functions.tsv) for the placed code."""
import argparse, glob, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import c28tc


def extract(tc, lib):
    """Extract lib's members into <build>/rts/<name> (once); (lib, directory)."""
    ver = os.path.basename(os.path.dirname(os.path.dirname(lib))) or 'lib'
    out = os.path.join(tc.s.build, 'rts', '%s_%s' % (ver, c28tc.stem(lib)))
    if not os.path.isdir(out) or not glob.glob(os.path.join(out, '*.obj')):
        os.makedirs(out, exist_ok=True)
        tc.run_native('ar2000', ['-x', os.path.abspath(lib)], cwd=out)
    return lib, out


def index_pairs(ws):
    """{(word, next word): [offsets]} for a quick first match."""
    idx = {}
    for i in range(len(ws) - 1): idx.setdefault((ws[i], ws[i + 1]), []).append(i)
    return idx


def search(data, bits, img, idx):
    """Image addresses where data fits, the bits relocations write (bits[k]) masked. The first two
    neighbouring words no relocation touches anchor the search; without such a pair every place
    is tried."""
    n = len(data)
    if n == 0: return []
    anchor = next((k for k in range(n - 1) if not bits[k] and not bits[k + 1]), None)
    cands = range(len(img.w) - n + 1) if anchor is None else [p - anchor for p in idx.get((data[anchor], data[anchor + 1]), [])]
    hits = []
    for s in cands:
        if s < 0 or s + n > len(img.w): continue
        if all((img.w[s + k] ^ data[k]) & ~bits[k] & 0xFFFF == 0 for k in range(n)): hits.append(img.base + s)
    return hits


def symbols_in(o, secname):
    return sorted((y['value'], y['name']) for y in o.symbols
                  if y['section'] == secname and y['sclass'] in ('C_EXT', 'C_STAT') and y['kind'] == 'defined'
                  and not y['name'].startswith(('$', '.')))


def fingerprint(members, img, small=4):
    """members: {member name: Obj}. The placed sections, sorted by address:
    [{addr, size, member, section, kind, syms, how}]."""
    idx = index_pairs(img.w)
    found, later = [], []
    for name, o in sorted(members.items()):
        for sec in o.sections.values():
            if sec.kind not in ('text', 'data') or not sec.data or sec.name.startswith('.cinit'): continue
            hits = search(sec.data, c28tc.reloc_masks(sec), img, idx)
            if len(sec.data) < small: continue                  # placed from a call below
            if len(hits) != 1:
                if sec.kind == 'data' and hits: later.append((name, sec, hits))
                continue
            found.append(dict(addr=hits[0], size=len(sec.data), member=name, section=sec.name, kind=sec.kind,
                              syms=symbols_in(o, sec.name), how='words'))
    # a constant section that fits several places: the one beside its member's other constants
    for name, sec, hits in later:
        near = [h for h in hits for f in found if f['member'] == name and f['kind'] == 'data'
                and -32 <= h - f['addr'] <= f['size'] + 32]
        if len(near) == 1:
            found.append(dict(addr=near[0], size=len(sec.data), member=name, section=sec.name, kind='data',
                              syms=symbols_in(members[name], sec.name), how='beside its constants'))
    # a section too small to place by its words is placed where a placed member calls it
    defs = {}
    for name, o in members.items():
        for sym, (secname, off) in o.defined().items(): defs.setdefault(sym, (name, secname, off))
    placed = {(f['member'], f['section']) for f in found}
    changed = True
    while changed:
        changed = False
        for f in list(found):
            sec = members[f['member']].sections[f['section']]
            for off, typ, sym in sec.relocs:
                if typ != 'R_REL22' or sym not in defs: continue
                at = f['addr'] + off
                if not img.contains(at, 2): continue
                tgt = c28tc.decode('R_REL22', img.words(at, 2), 0)
                name, secname, v = defs[sym]
                if (name, secname) in placed: continue
                s2 = members[name].sections[secname]
                start = tgt - v
                if s2.kind != 'text' or not img.contains(start, len(s2.data)): continue
                bits = c28tc.reloc_masks(s2)
                if all((img.W(start + k) ^ s2.data[k]) & ~b & 0xFFFF == 0 for k, b in enumerate(bits)):
                    found.append(dict(addr=start, size=len(s2.data), member=name, section=secname, kind='text',
                                      syms=symbols_in(members[name], secname), how='called from ' + f['member']))
                    placed.add((name, secname)); changed = True
    code = {f['member'] for f in found if f['kind'] == 'text'}
    return sorted([f for f in found if f['kind'] == 'text' or f['member'] in code], key=lambda f: f['addr'])


def load_members(tc, d):
    return {os.path.basename(p): tc.obj(p) for p in sorted(glob.glob(os.path.join(d, '*.obj')))}


def find(tc, img, lib=None):
    """(lib, directory, members, placed sections) for the project's library or another."""
    lib, d = extract(tc, lib or tc.s.rtslib)
    members = load_members(tc, d)
    return lib, d, members, fingerprint(members, img)


def main(argv):
    sys.path.insert(0, os.path.join(c28tc.EXHUME, 'tools'))
    import config
    path, argv = config.pop_config(argv)
    ap = argparse.ArgumentParser(prog='c28libfp.py', description=__doc__.split('\n\n')[0])
    ap.add_argument('--lib'); ap.add_argument('--libs'); ap.add_argument('--tsv', action='store_true')
    a = ap.parse_args(argv)
    s = c28tc.settings(path); tc = c28tc.Toolchain(s); img = c28tc.Image.of(s)
    if a.libs:
        for lib in sorted(glob.glob(os.path.expanduser(a.libs))):
            _, _, members, found = find(tc, img, lib)
            print('%-70s %3d sections %6d words' % (lib, len(found), sum(f['size'] for f in found)))
        return 0
    lib, d, members, found = find(tc, img, a.lib)
    if a.tsv:
        for f in found:
            if f['kind'] != 'text': continue
            for i, (v, n) in enumerate(f['syms']):
                end = f['syms'][i + 1][0] if i + 1 < len(f['syms']) else f['size']
                print('%06X\t%d\t%s\t%s\t%s' % (f['addr'] + v, end - v, n[1:] if n.startswith('_') else n, 'rts',
                                                'member=' + f['member']))
        return 0
    print('# ' + lib)
    for f in found:
        print('%06X %5d  %-16s %-16s %s%s' % (f['addr'], f['size'], f['member'], f['section'],
                                             ' '.join('%s(+%d)' % (n, v) for v, n in f['syms']),
                                             '' if f['how'] == 'words' else '  [%s]' % f['how']))
    print('# %d sections, %d words' % (len(found), sum(f['size'] for f in found)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
