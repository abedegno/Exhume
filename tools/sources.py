"""Where a project's sources are, for every tool that looks one up.

Sources may sit in subdirectories of [project] src (src/game, src/gfx ...), with shared
headers in [project] include (default src/include), which is not searched for sources. A
source is known by its stem, the file name without the extension in upper case: the stem
names its object (<build>/STEM/STEM.OBJ) and its module in the link, so stems must be unique
across the tree, and all_sources() stops when two collide. A source's DOS segment is its
`/* target: */` line. Tools that need a particular segment's source (a link order, the
overlay manager) should ask for it by segment, never by file name, so that renaming or
moving a file needs no tool change.

    all_sources(cfg)          every .C and .ASM under src except the include directory and
                              [project] exclude's directories, by path
    stem(path)                'GAMESTRN' for src/ui/GAMESTRN.C
    target(path)              the /* target: */ segment, or None
    by_stem(cfg)              {stem: path}
    by_segment(seg, cfg)      the stem whose target is seg, or seg plus a paragraph
                              ('seg039' finds seg039_3452; 'ovr154' finds ovr154)
    family(seg, cfg)          the stems of a segment split into several modules, in segment
                              order: targets SEG_PARA_OFFSET ('seg003' gives the stems whose
                              targets are seg003_0272_0, seg003_0272_EC, ...)
    find(name, cfg)           a source named on a command line: a path, or a bare stem or
                              file name looked up anywhere in the tree

    python3 tools/sources.py [--config PATH] [SEGMENT ...]
        lists every source with its stem and target, or the source of each segment named
"""
import os, re, sys, glob
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config

_TARGET = re.compile(r'/\*\s*target:\s*(\w+)\s*\*/')


def _cfg(cfg):
    return cfg if cfg is not None else config.load()


def all_sources(cfg=None):
    cfg = _cfg(cfg)
    inc = os.path.normpath(cfg.include)
    skip = [inc] + list(getattr(cfg, 'exclude', []))
    out = []
    for ext in ('C', 'ASM', 'c', 'asm'):
        for p in glob.glob(os.path.join(cfg.src, '**', '*.' + ext), recursive=True):
            p = os.path.normpath(p)
            if any(os.path.commonpath([p, d]) == d for d in skip): continue
            out.append(p)
    out = sorted(set(out))
    seen = {}
    for p in out:
        s = stem(p)
        if s in seen: raise SystemExit(f'two sources with the stem {s}: {seen[s]} and {p}')
        seen[s] = p
    return out


def stem(path):
    return os.path.splitext(os.path.basename(path))[0].upper()


def head(path, n=4000):
    return open(path, encoding='latin1').read(n)


def target(path):
    m = _TARGET.search(head(path))
    return m.group(1) if m else None


def by_stem(cfg=None):
    return {stem(p): p for p in all_sources(cfg)}


def targets(cfg=None):
    """{stem: target or None} for every source."""
    return {stem(p): target(p) for p in all_sources(cfg)}


def by_segment(seg, cfg=None, tmap=None):
    t = tmap if tmap is not None else targets(cfg)
    hits = [s for s, x in t.items() if x and re.fullmatch(re.escape(seg) + r'(?:_[0-9A-Fa-f]{4})?', x)]
    if len(hits) != 1: raise SystemExit(f'sources.py: {len(hits)} sources have the target {seg}: {hits}')
    return hits[0]


def family(seg, cfg=None, tmap=None):
    t = tmap if tmap is not None else targets(cfg)
    parts = []
    for s, x in t.items():
        m = x and re.fullmatch(re.escape(seg) + r'_[0-9A-Fa-f]{4}_([0-9A-Fa-f]+)', x)
        if m: parts.append((int(m.group(1), 16), s))
    return [s for _, s in sorted(parts)]


def find(name, cfg=None):
    """A source named on a command line. An existing path is returned as it is; otherwise a
    stem or file name (`SKILLS`, `SKILLS.C`, `src/SKILLS.C` after a move) is looked up in
    the tree, so old paths in notes and scripts keep working after files move."""
    if os.path.exists(name): return name
    s = stem(name)
    p = by_stem(cfg).get(s)
    if not p: raise SystemExit(f'no source {name} (stem {s}) under {_cfg(cfg).src}')
    return p


if __name__ == '__main__':
    path, a = config.pop_config(sys.argv[1:])
    c = config.load(path)
    if a:
        tm = targets(c); bs = by_stem(c)
        for seg in a:
            fam = family(seg, c, tm)
            for s in (fam or [by_segment(seg, c, tm)]): print(f'{seg}\t{s}\t{os.path.relpath(bs[s], c.root)}')
    else:
        for p in all_sources(c):
            print(f'{stem(p)}\t{target(p) or "-"}\t{os.path.relpath(p, c.root)}')
