"""Move the declarations a decompilation's sources share into per-subsystem headers.

    python3 tools/headergen.py [--config PATH] PLAN.toml [--base DIR] [--exclude FILE] [--write] [--loop N]

Matched sources usually declare what they use themselves, file by file: the same prototype
in forty files, a struct copied fifty times with different field names. This generates one
header per subsystem holding every declaration that can be shared, deletes the sources' own
copies, and adds the #include lines, keeping every byte of the build (skills/readability-pass,
step 2). It reads the sources from --base, a copy of the source tree taken before the pass
(default: [project] src itself), so it can be rerun from the same starting point as the plan
changes, and writes the headers into [project] include and the sources into [project] src.
Without --write it only reports.

A name moves to a header when every declaration and the definition agree after
normalising (or `allow_divergent` is set, below), and none of these holds; each reason is
counted in the report:
  plan                     listed in the plan's exclude (or --exclude FILE)
  static somewhere         a file has a static of that name: two different objects
  macro/typedef/tag        the name is also a macro, typedef or tag
  compiler header name     the compiler's own headers declare it ([toolchain] home)
  defined twice            two non-static definitions
  old-style                declared `f()` somewhere (unless allow_divergent)
  divergent                the forms differ (unless allow_divergent or forced)
  far variable             defined `far`: its declaration must stay where its segment is defined
  uses a typedef           its declaration names a file-local typedef
  struct object type       a variable or parameter of a struct type no header defines
  tie                      Turbo C orders publics and _BSS by a hash of the name, ties in order
                           of first sight, and a header is the first sight: a name whose tie
                           partner in its file stays out and was seen first stays out too
                           (needs the profile's bssorder.py)
  tag kind                 a tag is a struct in one file and a union in another

With allow_divergent, a name whose files disagree is declared as its defining file defines it.
Some files then compile differently (a `char` parameter where a caller pushed an `int`).
Those files are listed per name in <report>/suspects.json, and --loop N regenerates up to N
times: write, run `tools/gate.py check --fast`, and exclude every suspect name of every
failing source, until the fast gate passes. Then run the full gate. A name excluded this way
may only have broken one file: put it back by hand, declaring it locally in that file.

PLAN.toml:
    umbrella = "game.h"                # optional: included by every header, holds the specs
                                       # whose HEADER is this name (shared basic types)
    umbrella_comment = "what every header includes"
    default_header = "sys.h"           # for names whose owner has no header
    allow_divergent = true
    structs = "structs"                # optional: struct specs (tools/structrec.py), relative to the plan
    exclude = ["name", ...]
    comment_drop = "regex"             # comments above a moved declaration that match are not
                                       # moved into the header (phrases like "defined later")
    [force]                            # a declaration to use for a name, as written
    name = "int far name(int x);"
    [titles]                           # header section titles by owner stem, else the owner's
    STEM = "what the file does"        # first comment after its /* target: */ and /* opts: */
    [[header]]
    name = "sys.h"
    desc = "start-up, the main loop, memory"
    stems = ["MAINLOOP", "seg021"]     # the sources whose names it declares, each by stem or by
                                       # target segment (all the modules of a split segment);
                                       # their order is the order of the header's sections

The report directory (build/headergen) gets eligible.json (names moved and why the rest
were not), suspects.json and removed_comments.json.
"""
import os, re, sys, json, glob, shutil, subprocess, importlib.util, tomllib
from collections import defaultdict, Counter, OrderedDict
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config, sources, cparse
from declinv import n2, n2v, order_key, read_names


def load_specs(d):
    out = []
    if not d or not os.path.isdir(d): return out
    active = os.path.join(d, 'active.json')
    names = json.load(open(active)) if os.path.exists(active) else sorted(f for f in os.listdir(d) if f.endswith('.py'))
    for fn in names:
        sp = importlib.util.spec_from_file_location('spec_' + fn[:-3], os.path.join(d, fn))
        m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m)
        if getattr(m, 'EMIT', True): out.append(m)
    return out


def tags_in(t): return set(re.findall(r'\b(struct|union|enum)\s+([A-Za-z_]\w*)', t))


def wrap(line, width=100):
    if len(line) <= width or '(' not in line: return line
    i = line.find('(')
    out = []; cur = line[:i + 1]; ind = ' ' * (i + 1)
    parts = cparse.split_top(line[i + 1:line.rfind(')')]); tail = line[line.rfind(')'):]
    for k, p in enumerate(parts):
        p = p.strip() + (',' if k < len(parts) - 1 else tail)
        if len(cur) + 1 + len(p) > width and cur.strip() and not cur.endswith('('):
            out.append(cur.rstrip()); cur = ind + p
        else:
            cur = cur + ('' if cur.endswith('(') else ' ') + p
    out.append(cur)
    return '\n'.join(out)


def wrap_comment(text, width=92):
    words = text.split(); lines = []; cur = '/*'
    for w in words:
        if len(cur) + 1 + len(w) > width and cur != '/*': lines.append(cur); cur = '  '
        cur += ' ' + w
    lines.append(cur + ' */')
    return lines


def clean(text):
    t = cparse.strip_comments(text)
    t = re.sub(r'\bregister\s+', '', t)
    t = re.sub(r'^\s*(static|extern)\s+', '', t)
    return re.sub(r'\s+', ' ', t).strip().rstrip(';').strip()


class Gen:
    def __init__(self, cfg, plan_path, base, extra_exclude=()):
        self.cfg = cfg
        self.plan = tomllib.load(open(plan_path, 'rb'))
        pdir = os.path.dirname(os.path.abspath(plan_path))
        self.base = os.path.abspath(base or cfg.src)
        inc_rel = os.path.relpath(cfg.include, cfg.src)
        self.files = {}      # relative path -> base path, for every .C source
        for p in sorted(glob.glob(os.path.join(self.base, '**', '*.[Cc]'), recursive=True)):
            r = os.path.relpath(p, self.base)
            if r.startswith(inc_rel + os.sep): continue
            self.files[r] = p
        self.inv = {r: cparse.analyse(p) for r, p in self.files.items()}
        self.stem = {r: sources.stem(r) for r in self.files}
        self.target = {r: sources.target(p) for r, p in self.files.items()}
        # header of each source
        self.headers = OrderedDict()
        self.order_of_stem = {}
        for h in self.plan.get('header', []):
            self.headers[h['name']] = h
        self.umbrella = self.plan.get('umbrella')
        self.default = self.plan.get('default_header', next(iter(self.headers), 'common.h'))
        self.excl = set(self.plan.get('exclude', [])) | set(extra_exclude)
        self.force = self.plan.get('force', {})
        self.allow_div = self.plan.get('allow_divergent', False)
        self.specs = load_specs(os.path.join(pdir, self.plan['structs'])) if self.plan.get('structs') else []
        self.canon = {(m.KIND, m.TAG): m for m in self.specs}
        for m in self.specs:
            for t in getattr(m, 'ALSO_TAGS', []): self.canon[('struct', t)] = m
        self.build_symbols()

    # ---- which header a source belongs to ---------------------------------------------------
    @staticmethod
    def matches(entry, stem, target):
        """A plan entry names a source by its stem or by its target segment, which takes in
        every module of a split segment (seg003 for seg003_0272_0, seg003_0272_EC ...)."""
        e = entry.lower(); t = (target or '').lower()
        return entry.upper() == stem or bool(t) and (t == e or t.startswith(e + '_'))

    def entries(self):
        for h, d in self.headers.items():
            for s in d.get('stems', []) + d.get('segments', []): yield h, s

    def header_of_file(self, rel_or_stem, target=None):
        stem = sources.stem(rel_or_stem)
        for h, s in self.entries():
            if self.matches(s, stem, target): return h
        return None

    def file_order(self, stem, target):
        for k, (h, s) in enumerate(self.entries()):
            if self.matches(s, stem, target): return k
        return 10 ** 6

    # ---- the symbol table ------------------------------------------------------------------
    def build_symbols(self):
        cfg = self.cfg
        self.sym = sym = defaultdict(lambda: dict(kind=None, defs=[], decls=[], statics=[], other=[]))
        for f, ents in self.inv.items():
            for i, e in enumerate(ents):
                k = e['kind']
                if k == 'func_def':
                    (sym[e['name']]['statics'] if e['static'] else sym[e['name']]['defs']).append((f, i, n2(e['norm'])))
                    sym[e['name']]['kind'] = 'func'
                elif k == 'proto':
                    if e['static']: sym[e['name']]['statics'].append((f, i, n2(e['norm'])))
                    else: sym[e['name']]['decls'].append((f, i, n2(e['norm']), e['oldstyle']))
                    sym[e['name']]['kind'] = 'func'
                elif k in ('extern', 'var_def', 'static_var'):
                    for j, x in enumerate(e['names']):
                        s = sym[x['name']]; s['kind'] = s['kind'] or 'var'
                        if k == 'extern': s['decls'].append((f, i, n2v(x['decl']), j))
                        elif k == 'var_def': s['defs'].append((f, i, n2v(x['decl']), j, x['decl']))
                        else: s['statics'].append((f, i, n2v(x['decl'])))
                elif k in ('typedef', 'pp_define', 'struct_def', 'union_def', 'enum_def'):
                    if e.get('name'): sym[e['name']]['other'].append((f, i, k))
        # names the compiler's headers declare
        self.ccnames = set()
        if cfg.home:
            for h in glob.glob(os.path.join(cfg.home, '*.[Hh]')) + glob.glob(os.path.join(cfg.home, 'INCLUDE', '*.[Hh]')):
                t = open(h, encoding='latin1').read()
                self.ccnames.update(re.findall(r'\b([A-Za-z_]\w*)\s*\(', t))
                self.ccnames.update(re.findall(r'\bextern\b[^;(]*?\b([A-Za-z_]\w*)\s*[;\[]', t))
        # assembly publics: their owner for the header choice
        self.asmown = {}
        for p in sources.all_sources(cfg):
            if not p.upper().endswith('.ASM'): continue
            for m in re.finditer(r'(?im)^\s*public\s+([^;\n]+)', open(p, encoding='latin1').read()):
                for n in m.group(1).split(','):
                    n = n.strip()
                    if n.startswith('_'): self.asmown[n[1:]] = p
        self.tagkinds = defaultdict(set)
        for f, ents in self.inv.items():
            for e in ents:
                if e['kind'] in ('struct_def', 'union_def', 'enum_def') and e.get('name'):
                    self.tagkinds[e['name']].add(e['kind'][:-4])
        self.decide()

    def is_far_var(self, decl, name):
        pre = decl[:decl.rfind(name)]
        if '(' in pre: return False
        return bool(re.search(r'\bfar\b', pre.rsplit('*', 1)[-1]))

    def decl_text(self, name):
        s = self.sym[name]
        if name in self.force: return self.force[name]
        if s['kind'] == 'func':
            if s['defs']:
                f, i, _ = s['defs'][0]
                t = self.inv[f][i]['text']; return wrap(clean(t[:t.find('{')]) + ';')
            c = Counter(clean(self.inv[d[0]][d[1]]['text']) for d in s['decls'])
            return wrap(c.most_common(1)[0][0] + ';')
        if s['defs']: return 'extern ' + re.sub(r'\s+', ' ', s['defs'][0][4]).strip() + ';'
        c = Counter(re.sub(r'\s+', ' ', self.inv[d[0]][d[1]]['names'][d[3]]['decl']).strip() for d in s['decls'])
        return 'extern ' + c.most_common(1)[0][0] + ';'

    def decide(self):
        sym = self.sym
        eligible = {}; why = {}; self.divergent = set()
        for name, s in sym.items():
            if not s['decls']: continue
            if name in self.excl: why[name] = 'plan'; continue
            if s['statics']: why[name] = 'static somewhere'; continue
            if s['other']: why[name] = 'macro/typedef/tag'; continue
            if name in self.ccnames: why[name] = 'compiler header name'; continue
            if len(s['defs']) > 1: why[name] = 'defined twice'; continue
            forms = Counter(d[2] for d in s['decls']) + Counter(d[2] for d in s['defs'])
            if s['kind'] == 'func':
                if any(d[3] for d in s['decls']) and not self.allow_div: why[name] = 'old-style'; continue
            elif s['defs'] and self.is_far_var(s['defs'][0][4], name): why[name] = 'far variable'; continue
            if len(forms) > 1 and name not in self.force and not self.allow_div: why[name] = 'divergent'; continue
            if len(forms) > 1: self.divergent.add(name)
            eligible[name] = s
        self.eligible, self.why = eligible, why
        typedefs = {n for n, x in sym.items() if any(o[2] == 'typedef' for o in x['other'])}
        for name in list(eligible):
            if any(re.search(r'\b' + re.escape(t) + r'\b', self.decl_text(name)) for t in typedefs):
                del eligible[name]; why[name] = 'uses a typedef'
        for name in list(eligible):
            if self._objtype(name): del eligible[name]; why[name] = 'struct object type'
        key = order_key(self.cfg)
        if key:
            for f, ents in self.inv.items():
                pubs, bss = [], []
                for e in ents:
                    if e['kind'] == 'func_def' and not e['static']: pubs.append(e['name'])
                    if e['kind'] in ('var_def', 'static_var'):
                        for x in e['names']:
                            if e['kind'] == 'var_def': pubs.append(x['name'])
                            if not x['init']: bss.append(x['name'])
                fs = None
                for group in (pubs, bss):
                    for n in group:
                        if n not in eligible: continue
                        if fs is None: fs = self.first_sight(f)
                        bad = [m for m in group if m != n and m not in eligible and key(m) == key(n)
                               and fs.get(m, 1e9) < fs.get(n, 1e9)]
                        if bad: del eligible[n]; why[n] = 'tie'
        for name in list(eligible):
            if any(self.tagkinds.get(t, {k}) - {k} for k, t in tags_in(self.decl_text(name))):
                del eligible[name]; why[name] = 'tag kind'

    def _objtype(self, name):
        t = self.decl_text(name)
        for m in re.finditer(r'\b(struct|union)\s+([A-Za-z_]\w*)\s*(far\s+|near\s+)?(?![\s\w]*\*)', t):
            if (m.group(1), m.group(2)) in self.canon: continue
            rest = t[m.end():]
            if not re.match(r'\s*\*', rest) and not re.match(r'(far|near)\s*\*', rest.strip()): return True
        return False

    def first_sight(self, f):
        t = cparse.strip_comments(open(self.files[f], encoding='latin1').read())
        pos = {}
        for m in re.finditer(r'[A-Za-z_]\w*', t): pos.setdefault(m.group(0), m.start())
        return pos

    def owner(self, name):
        s = self.sym[name]
        if s['defs']: return s['defs'][0][0]
        return self.asmown.get(name)

    def header_of(self, name):
        o = self.owner(name)
        if o is None:
            c = Counter(self.header_of_file(d[0], self.target.get(d[0])) or self.default for d in self.sym[name]['decls'])
            return c.most_common(1)[0][0]
        t = self.target[o] if o in self.target else sources.target(o)
        return self.header_of_file(o, t) or self.default

    # ---- rewriting the sources -----------------------------------------------------------------
    def trailing(self, f, e):
        raw = open(self.files[f], encoding='latin1').read()
        nl = raw.find('\n', e['end']); nl = len(raw) if nl < 0 else nl
        m = re.match(r'[ \t]*(/\*.*?\*/)[ \t]*$', raw[e['end']:nl])
        return m.group(1) if m else None

    def rewrite(self):
        removed_comments = []; notes = defaultdict(list); newtext = {}
        eligible = self.eligible
        for f, ents in self.inv.items():
            raw = open(self.files[f], encoding='latin1').read()
            spans = []; incs = set()
            for i, e in enumerate(ents):
                if e['kind'] == 'proto' and not e['static'] and e['name'] in eligible:
                    spans.append((e['start'], e['end'], '', [e['name']])); incs.add(self.header_of(e['name']))
                    c = self.trailing(f, e)
                    if c: notes[e['name']].append(c)
                elif e['kind'] == 'extern':
                    gone = [x for x in e['names'] if x['name'] in eligible]
                    if not gone: continue
                    for x in gone: incs.add(self.header_of(x['name']))
                    c = self.trailing(f, e)
                    if c:
                        for x in gone: notes[x['name']].append(c)
                    keep = [x for x in e['names'] if x['name'] not in eligible]
                    if not keep: spans.append((e['start'], e['end'], '', [x['name'] for x in gone]))
                    else: spans.append((e['start'], e['end'], ' '.join('extern ' + x['decl'] + ';' for x in keep), []))
            code = cparse.strip_comments(raw)
            ftoks = set(re.findall(r'[A-Za-z_]\w*', code))
            ftags = tags_in(code)
            for n in ftoks & set(eligible): ftags |= tags_in(self.decl_text(n))
            for t in ftags:     # the header defining each struct the file names (the umbrella's too)
                if t in self.canon: incs.add(self.canon[t].HEADER)
            if not spans and not incs: continue
            out = []; pos = 0; spans.sort()
            for sid, (s0, s1, rep, nms) in enumerate(spans):
                out.append(raw[pos:s0]); out.append(rep if rep else '\x00%d\x01' % sid); pos = s1
                if not rep:
                    nl = raw.find('\n', pos); nl = len(raw) if nl < 0 else nl
                    if re.match(r'[ \t]*(/\*.*?\*/)?[ \t]*$', raw[pos:nl]): pos = nl
            out.append(raw[pos:])
            lines = ''.join(out).split('\n')
            kinds = ['blank' if not l.strip() else 'removed' if not re.sub('\x00\\d+\x01', '', l).strip() else 'other' for l in lines]
            comment_only = [False] * len(lines); incom = False
            for k, l in enumerate(lines):
                st = l.strip()
                if incom:
                    comment_only[k] = True
                    if '*/' in st: incom = False
                    continue
                if st.startswith('/*'):
                    end = st.find('*/')
                    if end < 0: comment_only[k] = True; incom = True
                    elif end == len(st) - 2: comment_only[k] = True
            keep_line = [True] * len(lines)
            k = 0
            while k < len(lines):
                if kinds[k] == 'blank': k += 1; continue
                j = k
                while j < len(lines) and kinds[j] != 'blank': j += 1
                if any(kinds[q] == 'removed' for q in range(k, j)):
                    q = k; units = []
                    while q < j:
                        u0 = q
                        while q < j and comment_only[q]: q += 1
                        while q < j and not comment_only[q]: q += 1
                        units.append(range(u0, q))
                    for u in units:
                        codel = [q for q in u if not comment_only[q]]
                        if not codel or not any(kinds[q] == 'removed' for q in codel): continue
                        for q in codel:
                            if kinds[q] == 'removed': keep_line[q] = False
                        cm = [q for q in u if comment_only[q]]
                        if all(kinds[q] == 'removed' for q in codel):
                            if cm:
                                mm = re.search('\x00(\\d+)\x01', lines[codel[0]])
                                first = spans[int(mm.group(1))][3][0] if mm and spans[int(mm.group(1))][3] else None
                                removed_comments.append((f, '\n'.join(lines[q] for q in cm), first))
                            for q in u: keep_line[q] = False
                k = j
            lines = [re.sub('\x00\\d+\x01', '', l) for l, kp in zip(lines, keep_line) if kp]
            res = []
            for l in lines:
                if not l.strip() and res and not res[-1].strip(): continue
                res.append(l.rstrip() if not l.strip() else l)
            incl = ['#include "%s"' % h for h in sorted(incs)]
            last = None
            for q, l in enumerate(res[:80]):
                if re.match(r'\s*#\s*include\b', l): last = q
            if last is not None: res[last + 1:last + 1] = incl
            else:
                q = 0
                while q < len(res) and res[q].strip().startswith('/*'):
                    while '*/' not in res[q]: q += 1
                    q += 1
                res[q:q] = ([''] if q and res[q - 1].strip() else []) + incl + ([''] if q < len(res) and res[q].strip() else [])
            newtext[f] = '\n'.join(res)
        return newtext, removed_comments, notes

    def section_title(self, owner):
        if owner is None: return 'Declared by the sources, defined where no source has it yet: data the link takes from the program.'
        stem = sources.stem(owner); f = os.path.basename(owner)
        if stem in self.plan.get('titles', {}): return f'{f}: {self.plan["titles"][stem]}'
        if not f.upper().endswith('.C'): return f
        t = open(self.files[owner] if owner in self.files else owner, encoding='latin1').read(4000)
        for m in re.finditer(r'/\*(.*?)\*/', t, re.S):
            c = ' '.join(m.group(1).split())
            if re.match(r'(target|opts):', c): continue
            head = re.split(r'[:.;]', c)[0].strip()
            if 3 < len(head) <= 70: return f'{f}: {head[0].lower() + head[1:] if not head[:2].isupper() else head}'
            break
        return f

    def make_headers(self, notes, pre):
        byh = defaultdict(list)
        for name in self.eligible: byh[self.header_of(name)].append(name)
        order = {}
        for name in self.eligible:
            o = self.owner(name)
            if o is None: order[name] = (10 ** 7, '', name)
            elif o in self.inv:
                d = self.sym[name]['defs'][0]
                first = min([self.inv[o][d[1]]['start']] + [self.inv[o][x[1]]['start'] for x in self.sym[name]['decls'] if x[0] == o])
                order[name] = (self.file_order(self.stem[o], self.target[o]), '%08d' % first, name)
            else:
                order[name] = (self.file_order(sources.stem(o), sources.target(o)), o, name)
        files = {}
        names_by_header = list(self.headers) + sorted(set(byh) - set(self.headers))
        for h in names_by_header:
            desc = self.headers.get(h, {}).get('desc', 'declarations')
            names = sorted(byh.get(h, []), key=lambda n: order[n])
            mine = [m for m in self.specs if m.HEADER == h]
            if not names and not mine: continue
            guard = re.sub(r'\W', '_', h.upper())
            L = wrap_comment('%s: %s.' % (h, desc)) + ['#ifndef %s' % guard, '#define %s' % guard]
            if self.umbrella and h != self.umbrella: L += ['', '#include "%s"' % self.umbrella]
            req = set(r for m in mine for r in getattr(m, 'REQUIRES', []))
            named = set()
            for n in names: named |= tags_in(self.decl_text(n))
            for m in mine: named |= tags_in(cparse.strip_comments(m.TEXT))
            for t in named:
                if t in self.canon and self.canon[t].HEADER != self.umbrella: req.add(self.canon[t].HEADER)
            umb_tags = {(m.KIND, m.TAG) for m in self.specs if m.HEADER == self.umbrella}
            fwd = sorted(t for t in named if t[0] != 'enum' and t not in umb_tags)
            if fwd: L += [''] + ['%s %s;' % t for t in fwd]
            if req - {h}: L.append('')
            for r in sorted(req - {h}): L.append('#include "%s"' % r)
            for m in mine: L += ['', m.TEXT.strip('\n')]
            cur = 'unset'
            for n in names:
                o = self.owner(n)
                if o != cur:
                    L.append(''); L += wrap_comment(self.section_title(o)); cur = o
                t = self.decl_text(n)
                if n in pre: L.append(pre[n])
                cs = list(dict.fromkeys(' '.join(c.split()) for c in notes.get(n, [])))
                if len(cs) == 1 and '\n' not in t and len(t) + 2 + len(cs[0]) <= 100: L.append(t + '  ' + cs[0])
                else:
                    for c in cs: L += wrap_comment(c[2:-2].strip())
                    L.append(t)
            L += ['', '#endif', '']
            files[h] = '\n'.join(L)
        if self.umbrella:
            U = wrap_comment('%s: %s' % (self.umbrella, self.plan.get('umbrella_comment', 'what every header includes.')))
            guard = re.sub(r'\W', '_', self.umbrella.upper())
            U += ['#ifndef %s' % guard, '#define %s' % guard]
            for m in self.specs:
                if m.HEADER == self.umbrella: U += ['', m.TEXT.strip('\n')]
            U += ['', '#endif', '']
            files[self.umbrella] = '\n'.join(U)
        return files

    def suspects(self):
        """{source: [names]}: divergent names moved to a header whose form in that source differs."""
        sus = defaultdict(list)
        for n in self.divergent:
            if n not in self.eligible: continue
            s = self.sym[n]
            canon = s['defs'][0][2] if s['defs'] else Counter(d[2] for d in s['decls']).most_common(1)[0][0]
            for d in s['decls']:
                if d[2] != canon: sus[d[0]].append(n)
        return {k: sorted(set(v)) for k, v in sus.items()}

    def run(self, write, report):
        os.makedirs(report, exist_ok=True)
        newtext, rc, notes = self.rewrite()
        drop = re.compile(self.plan['comment_drop'], re.I) if self.plan.get('comment_drop') else None
        pre = {n: c for f, c, n in rc if n and not (drop and drop.search(c))}
        files = self.make_headers(notes, pre)
        json.dump({'eligible': sorted(self.eligible), 'why': self.why}, open(os.path.join(report, 'eligible.json'), 'w'), indent=0)
        json.dump(self.suspects(), open(os.path.join(report, 'suspects.json'), 'w'), indent=1)
        json.dump(rc, open(os.path.join(report, 'removed_comments.json'), 'w'), indent=1)
        print(f'{len(self.eligible)} names to headers; left in their files: ' +
              ', '.join(f'{k} {v}' for k, v in Counter(self.why.values()).most_common()))
        print(f'{len(newtext)} of {len(self.files)} sources change; headers: {" ".join(sorted(files))}')
        if write:
            os.makedirs(self.cfg.include, exist_ok=True)
            for h, t in files.items(): open(os.path.join(self.cfg.include, h), 'w', encoding='latin1').write(t)
            for f, p in self.files.items():
                dst = os.path.join(self.cfg.src, f)
                t = newtext.get(f)
                if t is None:
                    if os.path.abspath(p) != os.path.abspath(dst): shutil.copyfile(p, dst)
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                open(dst, 'w', encoding='latin1', newline='').write(t)
            print(f'wrote {len(files)} headers to {self.cfg.include} and {len(newtext)} sources under {self.cfg.src}')


def main(argv):
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    def take(flag):
        if flag in a: i = a.index(flag); v = a[i + 1]; del a[i:i + 2]; return v
    base = take('--base'); exf = take('--exclude'); loop = take('--loop')
    report = take('--report') or os.path.join(cfg.build, 'headergen')
    write = '--write' in a or loop is not None
    rest = [x for x in a if not x.startswith('--')]
    if not rest: sys.exit(__doc__)
    if write and not base:
        sys.exit('--write needs --base DIR, a copy of the sources taken before the pass: the pass rewrites [project] src')
    extra = read_names(exf)
    for it in range(int(loop or 1)):
        g = Gen(cfg, rest[0], base, extra)
        g.run(write, report)
        if loop is None: return 0
        r = subprocess.run([sys.executable, os.path.join(here, 'gate.py'), '--config', cfg.file, 'check', '--fast'],
                           capture_output=True, text=True, cwd=cfg.root)
        if 'FAST CHECK PASSED' in r.stdout:
            print(f'round {it}: the fast gate passes; run the full gate (gate.py check) next'); return 0
        fails = re.findall(r'^FAIL (\S+?):', r.stdout, re.M)
        sus = g.suspects()
        add = set()
        for f in fails:
            add |= set(sus.get(os.path.relpath(os.path.join(cfg.root, f), cfg.src), []))
        add -= extra
        print(f'round {it}: {len(fails)} sources fail; excluding {len(add)} names: {" ".join(sorted(add))}')
        if not add:
            print('no suspects to exclude for: ' + ' '.join(fails) + ' (read their match and verify output)'); return 1
        extra |= add
        open(os.path.join(report, 'exclude.txt'), 'w').write('\n'.join(sorted(extra)) + '\n')
    return 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
