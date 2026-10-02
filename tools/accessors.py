"""Shared accessor macros in place of per-file copies and open-coded field reads.

    python3 tools/accessors.py [--config PATH] inventory [names]
    python3 tools/accessors.py [--config PATH] unify HEADER [-n]
    python3 tools/accessors.py [--config PATH] inline HEADER [--prefix OBJ_] [-n]

Packed fields (an item id in bits 0-8 of a word, a heading in bits 7-9) are read and written
through macros, and matched files each grew their own: OBJ_ITEM in one file is GET_ID in
another and an open-coded `(o->id & 0x1FF)` in a third. Step 4 of skills/readability-pass
puts one set in a shared header and uses it everywhere. Each command edits sources in place
(-n reports only); run the gate after each: two macros with the same tokens can still compile
differently (a cast before the mask keeps an `and` the plain form merges), and such a file
keeps its own spelling, which then needs a second canonical name.

  inventory   every function-like macro in the sources and headers, grouped by its body
              with the parameters numbered ($0, $1): the same body under several names is a
              candidate for one shared macro. `names` groups by name instead: one name with
              several bodies is a conflict to settle first.
  unify       for each source, its local macros whose body equals a macro in HEADER (after
              numbering parameters, dropping `(unsigned)` casts and replacing HEADER's
              `#define NAME 0x...` constants by their values) are deleted and their uses
              renamed to HEADER's name. Local macros on the same fields that match nothing
              are listed for a decision.
  inline      open-coded reads that are token for token the body of one of HEADER's getters,
              `#define P_NAME(o) (((o)->field & MASK) >> SHIFT)` or `((o)->field & MASK)`,
              become the macro: `(x->id & ID_ITEM)` becomes `OBJ_ITEM(x)`. --prefix selects
              the getters (default OBJ_).
"""
import os, re, sys, glob, collections
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config, sources

RX = re.compile(r'^[ \t]*#define[ \t]+(\w+)\(([^)]*)\)[ \t]*(.*?)[ \t]*(/\*.*)?$')


def c_sources(cfg):
    return [p for p in sources.all_sources(cfg) if p.upper().endswith('.C')]


def headers(cfg):
    return sorted(glob.glob(os.path.join(cfg.include, '*.h')) + glob.glob(os.path.join(cfg.include, '*.H')))


def numbered(args, body):
    for k, a in enumerate(args):
        if a: body = re.sub(r'\b%s\b' % re.escape(a), '$%d' % k, body)
    return body


def inventory(cfg, by_name):
    byname = collections.defaultdict(list); bybody = collections.defaultdict(list)
    for f in c_sources(cfg) + headers(cfg):
        lines = open(f, encoding='latin-1').read().split('\n')
        for i, l in enumerate(lines):
            m = RX.match(l)
            if not m: continue
            body = m.group(3); j = i
            while body.endswith('\\') and j + 1 < len(lines): j += 1; body = body[:-1] + lines[j].strip()
            nb = re.sub(r'\s+', '', numbered([a.strip() for a in m.group(2).split(',')], body))
            fn = os.path.relpath(f, cfg.src)
            byname[m.group(1)].append((fn, nb)); bybody[nb].append((fn, m.group(1)))
    if by_name:
        for n, v in sorted(byname.items()):
            bodies = collections.Counter(b for _, b in v)
            print(f'{n} ({len(v)} files, {len(bodies)} bodies)')
            for b, c in bodies.items(): print(f'    {c}x {b}   [{" ".join(sorted(fn for fn, bb in v if bb == b))}]')
    else:
        for b, v in sorted(bybody.items(), key=lambda x: -len(x[1])):
            print(f'{len(v)} {b}  :: {dict(collections.Counter(n for _, n in v))}')


def unify(cfg, header, dry):
    hdr = open(header, encoding='latin-1').read()
    consts = {m.group(1): int(m.group(2), 16) for m in re.finditer(r'#define\s+(\w+)\s+0x([0-9A-Fa-f]+)\b', hdr)}

    def norm(args, body):
        body = numbered(args, body).replace('(unsigned)', '')
        body = re.sub(r'\b[A-Z_][A-Z_0-9]+\b', lambda m: str(consts[m.group(0)]) if m.group(0) in consts else m.group(0), body)
        body = re.sub(r'\b0x([0-9A-Fa-f]+)\b', lambda m: str(int(m.group(1), 16)), body)
        return re.sub(r'\s+', '', body)
    canon = {}; fields = set()
    for l in hdr.split('\n'):
        m = RX.match(l)
        if m:
            nb = norm([a.strip() for a in m.group(2).split(',')], m.group(3))
            if nb in canon: print(f'{header}: {m.group(1)} has the same body as {canon[nb]}; the first is used')
            canon.setdefault(nb, m.group(1))
            fields.update(re.findall(r'->\s*(\w+)', m.group(3)))
    fieldrx = re.compile(r'->\s*(%s)\b' % '|'.join(map(re.escape, sorted(fields)))) if fields else None
    report = collections.defaultdict(list)
    inc = os.path.basename(header)
    for f in c_sources(cfg):
        lines = open(f, encoding='latin-1').read().split('\n')
        ren = {}; drop = []
        for i, l in enumerate(lines):
            m = RX.match(l)
            if not m or l.rstrip().endswith('\\'): continue
            nb = norm([a.strip() for a in m.group(2).split(',')], m.group(3))
            if nb in canon: ren[m.group(1)] = canon[nb]; drop.append(i)
            elif fieldrx and fieldrx.search(m.group(3)): report['local macros on the same fields, matching none'].append(f'{os.path.relpath(f, cfg.src)}: {l.strip()}')
        if not ren: continue
        txt = '\n'.join(l for i, l in enumerate(lines) if i not in drop)
        txt = re.compile(r'\b(%s)(?=\s*\()' % '|'.join(map(re.escape, ren))).sub(lambda m: ren[m.group(1)], txt)
        changed = {k: v for k, v in ren.items() if k != v}
        report['files'].append(f'{os.path.relpath(f, cfg.src)}: dropped {len(drop)}; renamed {changed}')
        if f'include "{inc}"' not in txt: report[f'files that must include {inc} now'].append(os.path.relpath(f, cfg.src))
        if not dry: open(f, 'w', encoding='latin-1', newline='').write(txt)
    for k, v in report.items():
        print('==', k); print('\n'.join(v))


def inline(cfg, header, prefix, dry):
    hdr = open(header, encoding='latin-1').read()
    getters = []
    for m in re.finditer(r'^#define (%s\w+)\(o\)\s+(.*?)\s*(?:/\*.*)?$' % re.escape(prefix), hdr, re.M):
        name, body = m.group(1), m.group(2)
        mm = re.fullmatch(r'\(\(\(o\)->([\w.]+) & (\w+)\) >> (\w+)\)', body)
        if mm: getters.append((name, mm.group(1), mm.group(2), mm.group(3))); continue
        mm = re.fullmatch(r'\(\(o\)->([\w.]+) & (\w+)\)', body)
        if mm: getters.append((name, mm.group(1), mm.group(2), None))
    X = r'(?P<x>[A-Za-z_]\w*(?:(?:->|\.)\w+|\[[^\[\]()]*\])*)'
    rules = []
    getters.sort(key=lambda g: g[3] is None)       # shift forms first
    for name, field, mask, shift in getters:
        fe = re.escape(field)
        inner = (r'\(\s*' + X + r'->' + fe + r'\s*&\s*' + re.escape(mask) + r'\s*\)\s*>>\s*' + re.escape(shift)) if shift \
            else (X + r'->' + fe + r'\s*&\s*' + re.escape(mask))
        rules.append((name, False, re.compile(r'(?P<pre>[\w\)\]]?)\(\s*' + inner + r'\s*\)')))
        if shift:
            rules.append((name, True, re.compile(r'(?P<lead>(?:^|[^\w\s\)\]])\s*|\breturn\s+)' + inner
                                                 + r'(?=\s*(?:[^\s+\-*/%<>]|<=|>=|<(?!<)|>(?!>)|$))')))
    total = 0
    for fpath in c_sources(cfg):
        lines = open(fpath, encoding='latin-1').read().split('\n'); n = [0]
        for i, l in enumerate(lines):
            if l.lstrip().startswith('#'): continue
            for name, bare, rx in rules:
                def rep(m, name=name, bare=bare):
                    if bare:
                        lead = m.group('lead'); core = lead.strip()
                        if core.endswith(('<<', '>>', '+', '-', '*', '/', '%', '~', '!')) and not core.endswith('->'): return m.group(0)
                        n[0] += 1; return lead + name + '(' + m.group('x') + ')'
                    n[0] += 1
                    return f'{m.group("pre")}({name}({m.group("x")}))' if m.group('pre') else f'{name}({m.group("x")})'
                l = rx.sub(rep, l)
            lines[i] = l
        if n[0]:
            total += n[0]; print(os.path.relpath(fpath, cfg.src), n[0])
            if not dry: open(fpath, 'w', encoding='latin-1', newline='').write('\n'.join(lines))
    print('total', total, '(not written: -n)' if dry else '')


def main(argv):
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    if not a: sys.exit(__doc__)
    dry = '-n' in a
    prefix = 'OBJ_'
    if '--prefix' in a: i = a.index('--prefix'); prefix = a[i + 1]; del a[i:i + 2]
    rest = [x for x in a if not x.startswith('-')]
    if rest[0] == 'inventory': inventory(cfg, 'names' in rest); return 0
    if len(rest) < 2: sys.exit(__doc__)
    h = rest[1] if os.path.exists(rest[1]) else os.path.join(cfg.include, rest[1])
    if rest[0] == 'unify': unify(cfg, h, dry); return 0
    if rest[0] == 'inline': inline(cfg, h, prefix, dry); return 0
    sys.exit(__doc__)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
