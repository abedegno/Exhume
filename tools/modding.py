"""The modding build's generic parts: a link that lets sources change size.

The exact link proves the sources by rebuilding the original EXE, and for that it works out
where every object's data sits by comparing the objects with the EXE's bytes (verify.py).
Once a source changes, its object no longer matches those bytes, so that comparison cannot
place it. The modding build therefore works from a snapshot the last exact link left:

- write_snapshot(): after an exact link in which every object verified, keep in
  <build>/LINK/base a copy of each matched object, the SHA-1 of the source it was built from,
  and the layout the link derived for it (layout.json).
- changed_sources(): the sources whose text differs from the snapshot's, each compiled with its
  own switches into a separate directory keyed by the source's hash, so the matched objects
  in <build> are never touched and a source is not recompiled while its text is unchanged.
- listing_code_offsets() and code_offset_tables(): near code offsets that the extracted data
  holds as plain words (handler tables a module jumps through), found from the IDA listing's
  `dw offset` lines, so that a link can write them as `dw offset NAME`.

A link reference implementation (examples/uw2/extract.py and link.py) calls these: the exact
run writes the snapshot, the --mod run lays the image out from it, links the changed objects
in place of the matched ones, and skips the checks that only an exact link can pass (overlay
stub order, exediff). docs/link.md, "The modding build", has the reasons."""
import os, sys, re, json, copy, hashlib, shutil, struct

here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)

SNAPSHOT = 'layout.json'


def sha1(path):
    return hashlib.sha1(open(path, 'rb').read()).hexdigest()


def write_snapshot(cfg, base, objects):
    """objects: {stem: (object path, source path, layout dict or None)}. Copies each object
    into base and writes base/layout.json: {'objects': {stem: layout}, 'sources': {stem:
    [source relative to the project root, SHA-1]}}. Call it only when every object verified:
    the layouts are where verify.py found each object's data in the original EXE."""
    os.makedirs(base, exist_ok=True)
    lay = {'objects': {}, 'sources': {}}
    for stem, (obj, src, layout) in objects.items():
        shutil.copyfile(obj, os.path.join(base, stem + '.OBJ'))
        if layout is not None: lay['objects'][stem] = layout
        lay['sources'][stem] = [os.path.relpath(src, cfg.root), sha1(src)]
    json.dump(lay, open(os.path.join(base, SNAPSHOT), 'w'), indent=1, sort_keys=True)


def load_snapshot(base, how_to_make):
    p = os.path.join(base, SNAPSHOT)
    if not os.path.exists(p):
        sys.exit(f'no snapshot in {base}: {how_to_make}')
    return json.load(open(p))


def changed_sources(cfg, base, outdir, sources, how_to_make):
    """{stem: object path} for each of sources (paths) whose text differs from the snapshot's.
    Each is built with its own /* opts: */ into outdir/STEM/STEM.OBJ, with SOURCE.SHA1 beside
    it; an object already built from the same text is reused. A source the snapshot does not
    have stops the build: the link order has no place for it yet."""
    import build
    known = load_snapshot(base, how_to_make)['sources']
    out = {}; todo = []
    for src in sources:
        stem = cfg.stem(src)
        if stem not in known:
            sys.exit(f'{stem}: a source the exact link did not have; a new file needs a place in the link order first')
        h = sha1(src)
        if h == known[stem][1]: continue
        obj = os.path.join(outdir, stem, stem + '.OBJ'); shafile = os.path.join(outdir, stem, 'SOURCE.SHA1')
        if not (os.path.exists(obj) and os.path.exists(shafile) and open(shafile).read() == h):
            todo.append((stem, src, h))
        out[stem] = obj
    if todo:
        c2 = copy.copy(cfg); c2.build = outdir
        for stem, src, _ in todo: print(f'compiling {src} ({cfg.directives(src)[1]})')
        res = build.build(c2, [s for _, s, _ in todo])
        for stem, src, h in todo:
            ok, msgs = res[stem]
            if not ok: sys.exit(f'{stem}: build failed\n' + '\n'.join(msgs))
            open(os.path.join(outdir, stem, 'SOURCE.SHA1'), 'w').write(h)
    return out


def listing_code_offsets(cfg, exe):
    """{file offset: (target paragraph or None, target offset)} for every `dw offset LABEL`
    line of the IDA listing whose label names a place by IDA's convention NAME_PPPP_OOOO (or
    NAME_OOOO, no paragraph). Segments are found by the load paragraph in IDA's segment name
    (`seg012_1D31`, less [binary] listing_para_bias). A line with no label of its own follows
    the previous word, so a typed table is read whole as far as IDA typed it."""
    if not cfg.listing or not os.path.exists(cfg.listing): return {}
    hdr = struct.unpack_from('<H', exe, 8)[0] * 16; bias = cfg.listing_para_bias
    found = {}; seg = None; at = None
    for raw in open(cfg.listing, 'rb'):
        l = raw.decode('latin1').split(';')[0].rstrip()
        m = re.match(r'^(\S+)\s+segment\b', l)
        if m:
            q = re.search(r'_([0-9A-Fa-f]{4})$', m.group(1)); seg = None; at = None
            if q: seg = (m.group(1), int(q.group(1), 16) - bias)
            continue
        if seg is None: continue
        m = re.match(r'^(%s_([0-9A-Fa-f]+))?\s*dw offset (\w+?)(?:_([0-9A-Fa-f]{4}))?_([0-9A-Fa-f]+)\s*$' % re.escape(seg[0]), l)
        if m and (m.group(1) or at is not None):
            at = int(m.group(2), 16) if m.group(1) else at + 2
            tpara = int(m.group(4), 16) - bias if m.group(4) else None
            found[hdr + seg[1] * 16 + at] = (tpara, int(m.group(5), 16))
        else: at = None
    return found


def code_offset_tables(found, exe, inside, name_at):
    """{file offset: NAME} for the words of extracted data that are near code offsets of named
    code, so they can be written `dw offset NAME`.

    found: listing_code_offsets(); inside(f): whether the word at file offset f is in the
    extracted data; name_at(paragraph, offset): the name defined there, or None.

    A word counts when the listing types it as an offset into a named place and the EXE holds
    that offset there. IDA often types only the start of a table, so a table goes on while the
    next word is the offset of a name in the same code segment; one unnamed entry between two
    named ones is passed over and stays a number, and a zero word ends the table (a name at
    offset 0 is no evidence)."""
    w16 = lambda i: struct.unpack_from('<H', exe, i)[0]
    ida = dict(found)
    # an entry IDA names without a segment (nullsub_3) is in the segment of the next one
    for f in sorted(ida, reverse=True):
        if ida[f][0] is None and f + 2 in ida and ida[f + 2][0] is not None: ida[f] = (ida[f + 2][0], w16(f))
    near = {}; tables = {}
    for f, (tpara, toff) in sorted(ida.items()):
        if tpara is None or not inside(f) or w16(f) != toff: continue
        n = name_at(tpara, toff)
        if n: near[f] = n; tables.setdefault(tpara, set()).add(f)
    for tpara, fs in tables.items():
        for f in sorted(fs):
            g = f + 2
            while inside(g):
                if not w16(g): break
                if g in near: g += 2; continue
                n = name_at(tpara, w16(g))
                if not n:
                    if name_at(tpara, w16(g + 2)): g += 2; continue      # one unnamed entry
                    break
                near[g] = n; g += 2
    return near
