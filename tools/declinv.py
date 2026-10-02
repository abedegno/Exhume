"""Inventory every top-level declaration across a project's C sources and shared headers, and
report what stops a name from having one shared declaration.

    python3 tools/declinv.py [--config PATH] [--json OUT] [--exclude FILE] [--verbose] [NAME ...]

The first step of moving a decompilation's declarations into shared headers (skills/
readability-pass, step 2), and the measure of it afterwards. Sources come from
tools/sources.py, headers from [project] include. With NAMEs, prints every declaration of
each name and stops.

Reported:
- Counts: function definitions; prototypes, extern lines and struct, union and enum
  definitions in the sources (the clutter headers remove) and in the headers.
- Conflicts, the names that cannot simply move to a header, each with its forms and files:
    divergent     declarations or the definition differ after normalising (parameter names,
                  `register`, `unsigned int` for `unsigned`, near data pointers in the medium
                  model): a `char` parameter where a caller's prototype says `int`, another
                  return type, another view of the same data. Matching often depends on the
                  difference: these stay local, each file declaring the name its own way.
    old-style     declared `f()` somewhere: the callers compile without argument conversion
    static        static in one file and declared elsewhere: two different objects
    defined twice two non-static definitions
    tag kind      struct in one file, union in another
    local+header  a header declares the name and a source declares it again itself; deliberate
                  when the source's form differs (the shared form would change its bytes),
                  leftover when it is the same
- Struct tags defined in more than one place, with each definition's size (laid out by
  Turbo C's rules, tools/cparse.py): the input to struct reconciliation (tools/structrec.py).
- Order ties (with the profile's bssorder.py): Turbo C lists a file's publics, and lays out
  its uninitialised globals, by a hash of the name, equal hashes in order of first sight, and
  a header declaration is the first sight. A tie group in one file where some names are
  declared in a header and some are not can come out in another order than the original.
  Each such group is listed; keep tie partners together, all in headers or all out.

--exclude FILE: names to leave out of the conflict report, one per line (# comments) or a
JSON list, or a TOML plan with an `exclude` list (tools/headergen.py's plan).
"""
import os, re, sys, json, glob, importlib.util
from collections import defaultdict, Counter
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config, sources, cparse


def n2(s):
    """A declaration normalised for comparison."""
    s = re.sub(r'\bregister\s+', '', s)
    s = re.sub(r'\bnear\s*\*', '*', s)          # data pointers are near in the medium model
    s = re.sub(r'\s*\*\s*', '*', s); s = re.sub(r'\bunsigned int\b', 'unsigned', s)
    s = re.sub(r'^(extern|static)\s+', '', s); s = re.sub(r'\s+', ' ', s)
    s = re.sub(r'\(\s*', '(', s); s = re.sub(r'\s*\)', ')', s)
    return s.strip()


def n2v(s): return re.sub(r'\[[^\]]*\]', '[]', n2(s))


def order_key(cfg):
    """The profile's public/_BSS order key (profiles/NAME/bssorder.py key()), or None."""
    p = os.path.join(cfg.profile_dir, 'bssorder.py')
    if not os.path.exists(p): return None
    sp = importlib.util.spec_from_file_location('bssorder', p); m = importlib.util.module_from_spec(sp)
    sp.loader.exec_module(m)
    return getattr(m, 'key', None)


def read_names(path):
    if not path: return set()
    t = open(path).read()
    if path.endswith('.json'): return set(json.loads(t))
    if path.endswith('.toml'):
        import tomllib
        return set(tomllib.loads(t).get('exclude', []))
    return {l.split('#')[0].strip() for l in t.splitlines() if l.split('#')[0].strip()}


class Inventory:
    def __init__(self, cfg, files=None):
        self.cfg = cfg
        self.files = files or [p for p in sources.all_sources(cfg) if p.upper().endswith('.C')]
        self.headers = sorted(glob.glob(os.path.join(cfg.include, '*.h')) + glob.glob(os.path.join(cfg.include, '*.H')))
        self.rel = lambda p: os.path.relpath(p, cfg.src)
        self.inv = {p: cparse.analyse(p) for p in self.files + self.headers}
        self.sym = defaultdict(lambda: dict(kind=None, defs=[], decls=[], statics=[], other=[]))
        for f, ents in self.inv.items():
            for i, e in enumerate(ents):
                k = e['kind']; s = self.sym[e.get('name')] if e.get('name') else None
                if k == 'func_def':
                    (s['statics'] if e['static'] else s['defs']).append((f, i, n2(e['norm']), False)); s['kind'] = 'func'
                elif k == 'proto':
                    (s['statics'] if e['static'] else s['decls']).append((f, i, n2(e['norm']), e['oldstyle'])); s['kind'] = 'func'
                elif k in ('extern', 'var_def', 'static_var'):
                    for j, x in enumerate(e['names']):
                        s = self.sym[x['name']]
                        fm = re.search(r'\b' + re.escape(x['name']) + r'\s*\((.*)\)\s*$', x['decl'])
                        if fm and not x['init']:
                            # one of several functions declared in one statement
                            # (`int far f(), far g();`): a prototype, not a variable
                            s['kind'] = 'func'
                            (s['statics'] if k == 'static_var' else s['decls']).append(
                                (f, i, n2(x['decl']), not fm.group(1).strip()))
                            continue
                        s['kind'] = s['kind'] or 'var'
                        lst = s['decls'] if k == 'extern' else s['defs'] if k == 'var_def' else s['statics']
                        lst.append((f, i, n2v(x['decl']), False))
                elif k in ('typedef', 'pp_define', 'struct_def', 'union_def', 'enum_def') and s is not None:
                    s['other'].append((f, i, k))

    def is_header(self, f): return f in self.headers

    def counts(self):
        c = Counter()
        for f, ents in self.inv.items():
            where = 'headers' if self.is_header(f) else 'sources'
            for e in ents:
                k = e['kind']
                if k == 'func_def': c[f'{where}: function definitions'] += 1
                elif k == 'proto':
                    c[f'{where}: prototypes'] += 1
                    if e['static']: c[f'{where}: prototypes of statics'] += 1
                elif k == 'extern': c[f'{where}: extern lines'] += e['endline'] - e['line'] + 1
                elif k in ('struct_def', 'union_def'): c[f'{where}: struct and union definitions'] += 1
                elif k == 'enum_def': c[f'{where}: enum definitions'] += 1
                elif k == 'pp_define': c[f'{where}: #defines'] += 1
        return c

    def conflicts(self, exclude=()):
        out = {}
        tagkinds = defaultdict(set)
        for f, ents in self.inv.items():
            for e in ents:
                if e['kind'] in ('struct_def', 'union_def', 'enum_def') and e.get('name'):
                    tagkinds[e['name']].add(e['kind'][:-4])
        for name, s in self.sym.items():
            if name in exclude or not (s['decls'] or len(s['defs']) > 1 or s['statics'] and (s['decls'] or s['defs'])): continue
            why = []
            forms = defaultdict(set)
            for d in s['decls'] + s['defs']: forms[d[2]].add(d[0])
            if len(forms) > 1: why.append('divergent')
            if any(d[3] for d in s['decls']): why.append('old-style')
            if s['statics'] and (s['decls'] or s['defs']): why.append('static')
            if len([d for d in s['defs'] if not self.is_header(d[0])]) > 1: why.append('defined twice')
            hdr = {d[0] for d in s['decls'] if self.is_header(d[0])}
            local = {d[0] for d in s['decls'] if not self.is_header(d[0])}
            if hdr and local: why.append('local+header')
            if why: out[name] = (why, forms)
        tags = {t: k for t, k in tagkinds.items() if len(k) > 1}
        return out, tags

    def struct_defs(self):
        """{(kind, tag): [(file, size or error)]} for tags defined in more than one place."""
        defs = defaultdict(list)
        hctx = cparse.Ctx()
        for h in self.headers:
            try: cparse.layout_text(open(h, encoding='latin1').read(), hctx)
            except Exception: pass
        for f in self.files + self.headers:
            ctx = cparse.Ctx(dict(hctx.tags))
            for e in self.inv[f]:
                if e['kind'] not in ('struct_def', 'union_def') or not e['name']: continue
                k = e['kind'][:-4]; t = cparse.strip_comments(e['text'])
                try:
                    fl = cparse.layout(t[t.find('{') + 1:t.rfind('}')], ctx, k)
                    ctx.tags[f'{k} {e["name"]}'] = (cparse.struct_size(fl), fl); size = cparse.struct_size(fl)
                except Exception as ex: size = f'? ({ex})'
                defs[(k, e['name'])].append((f, size))
        return {t: v for t, v in defs.items() if len(v) > 1}

    def ties(self, key):
        """[(file, kind, [(name, in a header?)])] for tie groups that mix header and local names."""
        if key is None: return []
        in_header = set()
        for h in self.headers:
            for e in self.inv[h]:
                if e.get('name'): in_header.add(e['name'])
                for x in e.get('names', []): in_header.add(x['name'])
        out = []
        for f in self.files:
            pubs, bss = [], []
            for e in self.inv[f]:
                if e['kind'] == 'func_def' and not e['static']: pubs.append(e['name'])
                if e['kind'] in ('var_def', 'static_var'):
                    for x in e['names']:
                        if e['kind'] == 'var_def': pubs.append(x['name'])
                        if not x['init']: bss.append(x['name'])
            for kind, group in (('publics', pubs), ('_BSS', bss)):
                by = defaultdict(list)
                for n in dict.fromkeys(group): by[key(n)].append(n)
                for k, ns in by.items():
                    if len(ns) > 1 and len({n in in_header for n in ns}) > 1:
                        out.append((f, kind, [(n, n in in_header) for n in ns]))
        return out


def main(argv):
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    def take(flag):
        if flag in a: i = a.index(flag); v = a[i + 1]; del a[i:i + 2]; return v
    jout = take('--json'); excl = read_names(take('--exclude'))
    verbose = '--verbose' in a
    names = [x for x in a if not x.startswith('--')]
    inv = Inventory(cfg)
    rel = inv.rel
    if names:
        for n in names:
            s = inv.sym.get(n)
            if not s: print(f'{n}: not declared anywhere'); continue
            print(f'{n} ({s["kind"]})')
            for lab in ('defs', 'decls', 'statics'):
                for d in s[lab]: print(f'    {lab[:-1]:6} {rel(d[0])}:{inv.inv[d[0]][d[1]]["line"]}  {d[2]}')
        return 0
    c = inv.counts()
    print(f'{len(inv.files)} C sources, {len(inv.headers)} headers')
    for k in sorted(c): print(f'    {k:45} {c[k]}')
    conf, tags = inv.conflicts(excl)
    why = Counter(w for ws, _ in conf.values() for w in ws)
    print(f'\nconflicts: {len(conf)} names' + (f' ({len(excl)} excluded)' if excl else '') + ': '
          + ', '.join(f'{k} {v}' for k, v in why.most_common()))
    deliberate = [n for n, (ws, _) in conf.items() if ws != ['local+header']]
    for n in sorted(conf, key=str.lower):
        ws, forms = conf[n]
        if ws == ['local+header'] and not verbose: continue
        print(f'  {n}: {", ".join(ws)}')
        if len(forms) > 1 or verbose:
            for form, fs in sorted(forms.items(), key=lambda kv: -len(kv[1])):
                fl = sorted(rel(f) for f in fs)
                print(f'      {form}    [{", ".join(fl[:6])}{" ..." if len(fl) > 6 else ""}]')
    lh = [n for n, (ws, _) in conf.items() if ws == ['local+header']]
    if lh and not verbose:
        print(f'  ... and {len(lh)} names a header declares that sources declare again in the same form'
              ' (leftovers: --verbose lists them)')
    if tags: print('\ntag kind conflicts: ' + ', '.join(f'{t} ({"/".join(sorted(k))})' for t, k in sorted(tags.items())))
    sd = inv.struct_defs()
    print(f'\nstruct and union tags defined in more than one place: {len(sd)}')
    for (k, t), v in sorted(sd.items(), key=lambda kv: -len(kv[1])):
        sizes = Counter(str(s) for _, s in v)
        print(f'  {k} {t}: {len(v)} definitions, sizes ' + ', '.join(f'{s} x{n}' for s, n in sizes.most_common())
              + (f'  [{", ".join(rel(f) for f, _ in v)}]' if verbose or len(v) <= 4 else ''))
    ties = inv.ties(order_key(cfg))
    print(f'\norder ties mixing header and local names: {len(ties)}')
    for f, kind, ns in ties:
        print(f'  {rel(f)} {kind}: ' + ', '.join(f'{n}{" (header)" if h else ""}' for n, h in ns))
    if jout:
        json.dump(dict(counts=c, conflicts={n: dict(why=ws, forms={k: sorted(rel(f) for f in v) for k, v in fm.items()})
                                            for n, (ws, fm) in conf.items()},
                       structs={f'{k} {t}': [(rel(f), s) for f, s in v] for (k, t), v in sd.items()},
                       ties=[(rel(f), k, ns) for f, k, ns in ties]), open(jout, 'w'), indent=1)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
