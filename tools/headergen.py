"""Move the declarations a decompilation's sources share into per-subsystem headers.

    python3 tools/headergen.py [--config PATH] PLAN.toml [--base DIR] [--exclude FILE] [--write] [--loop N]
    python3 tools/headergen.py [--config PATH] --update PLAN.toml [--base DIR] [--exclude FILE] [--write] [--loop N]

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

Both modes add `struct X;` to a header wherever a parameter list names a tag the header has
not declared at file scope before it (itself, or through a header it includes that does not
include it back): such a tag has prototype scope, and Turbo C then calls the definition a
"Type mismatch in redeclaration". The tie checks count a #define of a name as a sighting
(declinv.sights()), and the parser knows the project's typedefs, empty macros and parameter
list macros (tools/cparse.py, setup()).

--update: rewrite headers that already exist, in place. For a tree whose headers came from
elsewhere (UW1's began as UW2's) with structs, constants and comments that must survive.
BASE (a copy of src taken before the step, include directory and all) is read whole, and
[project] src and include are written. Without --write it only reports. In order:
  drop      a header declaration of a name the project neither defines (C or assembly) nor
            uses (in a source, or in a header macro's body) goes, as do the plan's drop
  far       a far variable a header declares is handled as excluded (below), each file
            declaring it as defined
  exclude   the plan's exclude (and --exclude): the header declarations go, and each source
            that uses the name without declaring it gets the header's form, below its
            includes, as it compiled
  retype    every other header declaration takes its definition's form (or the plan's
            force); forms are compared after declinv's normalising, with the typedefs the
            headers define one way only written out, and an array bound one side leaves out
  copies    the sources' own declarations of header names go (a form other than the
            definition's makes the file a suspect), as do renames around the includes
            (`#define N OTHER` ... `#include` ... `#undef N`) of declarations, with the comment
            directly above a run of them; a rename of a header's tag, typedef or macro stays
  add       a name declared only in sources goes to its owner's header, with a comment on its
            declaration's line, or above it when that comment was on it alone, unless it is
            static somewhere, also a macro, typedef or tag, a compiler header's name, defined
            twice, a far variable, uses a file's typedef, or is a struct object of a tag no
            header defines (each counted under "not shared")
  includes  a source gets the headers its deleted declarations now come from
  structs   a source's struct or union definition identical to one in a header it includes
            goes
  tags      `struct X;` where needed (above)
  fixup     the plan's line moves
Everything else in a header stays: comments, constants, structs, macros, its order. A
comment directly above deleted code goes with it when every declaration it introduced went
(the removed ones are in removed_comments.json); a section left empty loses its title. The
tie check compares each source's tie groups in order of first sight before and after, and
a group that changed makes its names suspects. Names declared through a parameter list
macro (OLDSTYLE) are left as written, as are the plan's keep. Forms that differ do not keep
a name out of a header (allow_divergent is implied): the file is a suspect, and --loop
excludes its suspects while the fast gate fails. The report directory gets update.json
(every change by kind, and the suspects), suspects.json and removed_comments.json.

PLAN.toml for --update: exclude, force, comment_drop, [[header]] (stems and segments name
the sources whose names each header holds) as above, and
    keep = ["name", ...]               # left exactly as written, in headers and sources
    drop = ["name", ...]               # header declarations removed although known
    default_header = "sys.h"
    [stems]                            # header = the sources (file names) it holds
    "file.h" = ["GAMEWRAP.C", "ARC.C"]
    [dirs]                             # else by the source's directory under src
    gfx = "gfx.h"
    [titles]                           # a new section's title by owner stem (else the
    STEM = "what the file does"        # owner's first comment)
    [[fixup]]                          # after the rewrite, move the first line holding
    file = "gfx/CUTS.C"                # `move` to just above the first holding `before`
    move = "int far cutsop_wait("
    before = "int far cutsop_skip("
A header the plan names that BASE lacks is created.
"""
import os, re, sys, json, glob, shutil, subprocess, importlib.util, tomllib
from collections import defaultdict, Counter, OrderedDict
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config, sources, cparse
from declinv import n2, n2v, order_key, read_names, sights


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


def remove_spans(raw, spans):
    """Apply spans (start, end, replacement, names) to raw: a span with no replacement is
    deleted with the rest of its line when only a comment follows, and a comment block
    directly above deleted code goes with it when every code line it introduces went. Blank
    lines are collapsed. -> (lines, [(removed comment, first name of its spans, number of names)])."""
    removed = []
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
                        ids = {int(x) for q in codel for x in re.findall('\x00(\\d+)\x01', lines[q])}
                        nn = sum(len(spans[x][3]) or 1 for x in ids)
                        removed.append(('\n'.join(lines[q] for q in cm), first, nn))
                    for q in u: keep_line[q] = False
        k = j
    lines = [re.sub('\x00\\d+\x01', '', l) for l, kp in zip(lines, keep_line) if kp]
    res = []
    for l in lines:
        if not l.strip() and res and not res[-1].strip(): continue
        res.append(l.rstrip() if not l.strip() else l)
    return res, removed


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
        binc = os.path.join(self.base, inc_rel)
        self.binc = binc if os.path.isdir(binc) else cfg.include; self.sight_cache = {}
        cparse.setup(cfg, sorted(glob.glob(os.path.join(self.binc, '*.[Hh]'))))
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
        """{name: rank} of first sight in the base source, through the headers it includes
        and counting #defines (declinv.sights)."""
        return {n: r for n, (r, _) in sights(self.files[f], self.binc, self.sight_cache).items()}

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
            res, rcs = remove_spans(raw, spans)
            removed_comments += [(f, c, n) for c, n, _ in rcs]
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
        texts = {os.path.basename(p): open(p, encoding='latin1').read() for p in glob.glob(os.path.join(self.binc, '*.[Hh]'))}
        texts.update(files)
        inc_by = includes_tags(texts)
        for h in list(files):
            files[h], added = forward_tags(files[h], inc_by(h))
            if added: print(f'{h}: forward declarations added: ' + ' '.join('%s %s' % t for t in added))
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


# ---- forward declarations of tags (both modes) ------------------------------------------------
TAG_RE = re.compile(r'\b(struct|union)\s+([A-Za-z_]\w*)\b(\s*\{)?')


def param_lists(t):
    """[(start, end)] of the parameter lists in t (comments stripped): a `(` after `)` or after
    an identifier that is not a keyword (`f(`, `M((`), not a declarator's `(far *p)`."""
    out = []; stack = []
    for k, c in enumerate(t):
        if c == '(':
            j = k - 1
            while j >= 0 and t[j].isspace(): j -= 1
            is_par = False
            if j >= 0 and t[j] == ')': is_par = True
            elif j >= 0 and (t[j].isalnum() or t[j] == '_'):
                i = j
                while i >= 0 and (t[i].isalnum() or t[i] == '_'): i -= 1
                w = t[i + 1:j + 1]
                is_par = w not in cparse.KW and not w[0].isdigit()
            stack.append((k, is_par))
        elif c == ')' and stack:
            k0, is_par = stack.pop()
            if is_par: out.append((k0, k))
    return out


def file_scope_tags(text):
    """The struct and union tags a header's text declares at file scope: every mention that
    is not inside a parameter list (a struct body's mentions are file scope in C)."""
    t = cparse.blank_empty_macros(cparse.strip_comments(text)); out = set()
    for s, e, c in cparse.chunks(t):
        if c.startswith('#'): continue
        pls = param_lists(c)
        for m in TAG_RE.finditer(c):
            if m.group(3) or not any(a < m.start() < b for a, b in pls): out.add((m.group(1), m.group(2)))
    return out


def forward_tags(text, included=lambda name: set()):
    """text with `struct X;` added for every tag that a parameter list names before the header
    declares it at file scope: such a tag has prototype scope, and the definition met later
    is another type to Turbo C ("Type mismatch in redeclaration"). included(name) gives the
    tags a header named by `#include "name"` declares (empty for one that includes this
    header back: with a cycle it may not have been read yet). -> (text, [tags added])."""
    t = cparse.blank_empty_macros(cparse.strip_comments(text))
    known = set(); need = []; first = None
    for s, e, c in cparse.chunks(t):
        if c.startswith('#'):
            m = re.match(r'#\s*include\s*"([^"]+)"', c)
            if m: known |= included(m.group(1)) or set()
            continue
        pls = param_lists(c)
        for m in TAG_RE.finditer(c):
            tag = (m.group(1), m.group(2))
            if not m.group(3) and any(a < m.start() < b for a, b in pls):
                if tag not in known and tag not in need:
                    need.append(tag); first = s if first is None else first
            else: known.add(tag)
    if not need: return text, []
    lines = text.split('\n'); stop = text.count('\n', 0, first)
    at = None; guard = None
    for q in range(stop):
        l = lines[q]
        if re.match(r'\s*(struct|union)\s+\w+\s*;\s*$', l) or re.match(r'\s*#\s*include\b', l): at = q + 1
        if guard is None and re.match(r'\s*#\s*define\b', l): guard = q + 1
    at = at if at is not None else guard if guard is not None else 0
    add = ['%s %s;' % x for x in sorted(need)]
    if at and re.match(r'\s*#', lines[at - 1]): add = [''] + add
    if at < len(lines) and lines[at].strip() and not re.match(r'\s*(struct|union)\s+\w+\s*;', lines[at]): add += ['']
    lines[at:at] = add
    return '\n'.join(lines), need


def includes_tags(texts):
    """texts: {header file name: text}. -> included_by(me) for forward_tags: the tags a header
    named in me's `#include` declares at file scope, its own and its includes', or none when
    it includes me back (with a cycle it may not have been read when me is)."""
    own = {h: file_scope_tags(t) for h, t in texts.items()}
    incs = {h: re.findall(r'^\s*#\s*include\s*"([^"]+)"', cparse.strip_comments(t), re.M) for h, t in texts.items()}
    def reach(h):
        out = set(); todo = [h]
        while todo:
            x = todo.pop()
            for y in incs.get(x, []):
                if y in own and y not in out: out.add(y); todo.append(y)
        return out
    rc = {h: reach(h) for h in own}
    def included_by(me):
        def f(name):
            if name not in own or name == me or me in rc[name]: return set()
            tags = set(own[name])
            for y in rc[name]: tags |= own[y]
            return tags
        return f
    return included_by


# ---- --update: existing headers rewritten in place -------------------------------------------
NO_OWNER = 'Declared by the sources, defined where no source has it yet: data the link takes from the program.'


def cmp(s):
    """A normalised declaration for comparing two forms: declinv's n2 with the typedefs the
    headers define one way only written out (cparse.expand)."""
    return n2(cparse.expand(n2(s)))


def same(a, b):
    """Two compared forms are one declaration: equal, or equal but for an array bound one
    of them leaves out."""
    if a == b: return True
    da = re.findall(r'\[([^\]]*)\]', a); db = re.findall(r'\[([^\]]*)\]', b)
    if re.sub(r'\[[^\]]*\]', '[]', a) != re.sub(r'\[[^\]]*\]', '[]', b) or len(da) != len(db): return False
    return all(x.strip() == y.strip() or not x.strip() or not y.strip() for x, y in zip(da, db))


class Update:
    """Rewrite the headers that exist (in BASE's include directory) to the sources'
    declarations, in place: see the module's docstring, "--update"."""

    def __init__(self, cfg, plan_path, base, extra_exclude=()):
        self.cfg = cfg
        self.plan = tomllib.load(open(plan_path, 'rb'))
        self.base = os.path.abspath(base or cfg.src)
        self.inc_rel = os.path.relpath(cfg.include, cfg.src)
        self.binc = os.path.join(self.base, self.inc_rel)
        skip = [os.path.relpath(x, cfg.src) for x in cfg.exclude]
        self.files = {}
        for p in sorted(glob.glob(os.path.join(self.base, '**', '*.[Cc]'), recursive=True)):
            r = os.path.relpath(p, self.base)
            if r.startswith(self.inc_rel + os.sep) or any(r == x or r.startswith(x + os.sep) for x in skip): continue
            self.files[r] = p
        self.headers = {os.path.join(self.inc_rel, os.path.basename(p)): p
                        for p in sorted(glob.glob(os.path.join(self.binc, '*.[Hh]')))}
        self.path = {**self.files, **self.headers}
        cparse.setup(cfg, sorted(self.headers.values()))
        self.raw = {r: open(p, encoding='latin1').read() for r, p in self.path.items()}
        self.inv = {}
        for r in self.path:
            ents = []
            for e in cparse.analyse_text(self.raw[r]):
                if e.get('name') == '?': continue
                if e['kind'] in ('extern', 'var_def', 'static_var') and all(x['name'] == '?' for x in e['names']): continue
                ents.append(e)
            self.inv[r] = ents
        self.excl = set(self.plan.get('exclude', [])) | set(extra_exclude)
        self.force = self.plan.get('force', {})
        self.drop = set(self.plan.get('drop', []))
        self.headers_plan = OrderedDict((h['name'], h) for h in self.plan.get('header', []))
        self.symbols()

    # ---- what the tree declares --------------------------------------------------------------
    def symbols(self):
        cfg = self.cfg
        code = {r: cparse.strip_comments(self.raw[r]) for r in self.path}
        self.tricks = defaultdict(set)
        for f in self.files:
            L = code[f].split('\n')
            for k, l in enumerate(L):
                m = re.match(r'\s*#\s*define\s+([A-Za-z_]\w*)\s+([A-Za-z_]\w*)\s*$', l)
                if not m: continue
                u = next((q for q in range(k + 1, len(L)) if re.match(r'\s*#\s*undef\s+%s\b' % m.group(1), L[q])), None)
                if u is not None and any(re.match(r'\s*#\s*include\b', L[q]) for q in range(k, u)):
                    self.tricks[f].add(m.group(1))
        self.defs = {}; self.ndefs = Counter(); self.statics = set(); self.other = set()
        self.ldecl = defaultdict(list); self.hdecl = defaultdict(list); self.keep = set(self.plan.get('keep', []))
        for f in self.path:
            ish = f in self.headers
            for i, e in enumerate(self.inv[f]):
                k = e['kind']
                if k == 'func_def':
                    if e['static']: self.statics.add(e['name'])
                    elif not ish: self.ndefs[e['name']] += 1; self.defs.setdefault(e['name'], (f, i, None))
                elif k == 'proto':
                    if e.get('wrapper'): self.keep.add(e['name'])
                    if e['static']: self.statics.add(e['name'])
                    else: (self.hdecl if ish else self.ldecl)[e['name']].append((f, i, None))
                elif k in ('extern', 'var_def', 'static_var'):
                    for j, x in enumerate(e['names']):
                        n = x['name']
                        if n == '?': continue
                        if k == 'static_var': self.statics.add(n)
                        elif k == 'var_def' and not ish: self.ndefs[n] += 1; self.defs.setdefault(n, (f, i, j))
                        elif k == 'extern': (self.hdecl if ish else self.ldecl)[n].append((f, i, j))
                elif k in ('typedef', 'pp_define', 'struct_def', 'union_def', 'enum_def') and e.get('name'):
                    if not (k == 'pp_define' and e['name'] in self.tricks.get(f, ())): self.other.add(e['name'])
        self.asm = {}
        for p in sources.all_sources(cfg):
            if not p.upper().endswith('.ASM'): continue
            for m in re.finditer(r'(?im)^\s*public\s+([^;\n]+)', open(p, encoding='latin1').read()):
                for n in m.group(1).split(','):
                    n = n.strip()
                    if n.startswith('_'): self.asm[n[1:]] = os.path.relpath(p, cfg.src)
        self.code = {}; self.users = defaultdict(set)
        for f in self.files:
            t = re.sub(r'"(\\.|[^"\\])*"', '""', code[f]); self.code[f] = t
            for n in set(re.findall(r'[A-Za-z_]\w*', t)): self.users[n].add(f)
        self.hmacro = set()
        for h in self.headers:
            for e in self.inv[h]:
                if e['kind'] == 'pp_define': self.hmacro |= set(re.findall(r'[A-Za-z_]\w*', e['norm']))
        self.incl = {r: re.findall(r'^\s*#\s*include\s*"([^"]+)"', code[r], re.M) for r in self.path}
        self.tdefs_src = {e['name'] for f in self.files for e in self.inv[f] if e['kind'] == 'typedef'}
        self.htags = {e['name'] for h in self.headers for e in self.inv[h] if e['kind'] in ('struct_def', 'union_def') and e['name']}
        self.htypes = {e['name'] for h in self.headers for e in self.inv[h]
                       if e['kind'] in ('struct_def', 'union_def', 'enum_def', 'typedef', 'pp_define') and e.get('name')}
        self.ccnames = set()
        if cfg.home:
            for h in glob.glob(os.path.join(cfg.home, '*.[Hh]')) + glob.glob(os.path.join(cfg.home, 'INCLUDE', '*.[Hh]')):
                t = open(h, encoding='latin1').read()
                self.ccnames.update(re.findall(r'\b([A-Za-z_]\w*)\s*\(', t))
                self.ccnames.update(re.findall(r'\bextern\b[^;(]*?\b([A-Za-z_]\w*)\s*[;\[]', t))

    def hrel(self, name): return os.path.join(self.inc_rel, name)

    def closure(self, f, extra=()):
        """The headers f includes, directly or through other headers, in the order met."""
        out = []; seen = set()
        def go(h):
            if h in seen: return
            seen.add(h)
            if self.hrel(h) not in self.headers: return
            for s in self.incl.get(self.hrel(h), []): go(s)
            out.append(h)
        for h in list(self.incl.get(f, [])) + list(extra): go(h)
        return out

    def ent(self, ref): return self.inv[ref[0]][ref[1]]

    def name_of(self, ref):
        e = self.ent(ref); return e['name'] if ref[2] is None else e['names'][ref[2]]['name']

    def text_of(self, ref):
        e = self.ent(ref)
        return clean(e['text']) if ref[2] is None else 'extern ' + e['names'][ref[2]]['decl']

    def norm_of(self, ref):
        e = self.ent(ref)
        return cmp(e['norm'] if ref[2] is None else e['names'][ref[2]]['decl'])

    def canon_text(self, n):
        """The declaration a name gets: the plan's force, else its definition's form, else the
        header's, else the sources' most common."""
        if n in self.force: return self.force[n].strip().rstrip(';').strip()
        if n in self.defs:
            f, i, j = self.defs[n]; e = self.inv[f][i]
            if j is None: t = e['text']; return clean(t[:t.find('{')])
            return 'extern ' + e['names'][j]['decl']
        if self.hdecl.get(n): return self.text_of(self.hdecl[n][0])
        return Counter(self.text_of(r) for r in self.ldecl[n]).most_common(1)[0][0]

    def canon_norm(self, n):
        t = self.canon_text(n)
        es = cparse.analyse_text(t + ';')
        e = es[-1] if es else {}
        if e.get('kind') == 'proto': return cmp(e['norm'])
        for x in e.get('names', []):
            if x['name'] == n: return cmp(x['decl'])
        return cmp(t)

    def is_far_var(self, n):
        if n not in self.defs or self.defs[n][2] is None: return False
        f, i, j = self.defs[n]; d = self.inv[f][i]['names'][j]['decl']
        pre = d[:d.rfind(n)]
        return '(' not in pre and bool(re.search(r'\bfar\b', pre.rsplit('*', 1)[-1]))

    def known(self, n):
        return n in self.defs or n in self.asm or n in self.users or n in self.hmacro

    def owner(self, n):
        if n in self.defs: return self.defs[n][0]
        return self.asm.get(n)

    def header_for_file(self, rel):
        """The owner's header: the plan's [[header]] stems or segments, its [stems] table
        (header = [file names]), its [dirs] table (top directory under src = header), else
        default_header."""
        stem = sources.stem(rel)
        p = self.path.get(rel) or os.path.join(self.cfg.src, rel)
        tgt = sources.target(p) if os.path.exists(p) else None
        for h, d in self.headers_plan.items():
            for s in d.get('stems', []) + d.get('segments', []):
                if Gen.matches(s, stem, tgt): return h
        for h, lst in self.plan.get('stems', {}).items():
            if stem in [sources.stem(x) for x in lst]: return h
        top = rel.split(os.sep)[0]
        return self.plan.get('dirs', {}).get(top, self.plan.get('default_header', 'sys.h'))

    def section_title(self, owner):
        if owner is None: return NO_OWNER
        stem = sources.stem(owner); b = os.path.basename(owner)
        if stem in self.plan.get('titles', {}): return f'{b}: {self.plan["titles"][stem]}'
        p = self.path.get(owner) or os.path.join(self.cfg.src, owner)
        if not os.path.exists(p): return b
        t = open(p, encoding='latin1').read(5000)
        for m in re.finditer(r'/\*(.*?)\*/', t, re.S):
            c = ' '.join(m.group(1).split())
            if re.match(r'(target|opts):', c): continue
            head = re.split(r'(?<=[a-z0-9)])[.;](\s|$)', c)[0].strip()
            if 3 < len(head) <= 80: return f'{b}: {head[0].lower() + head[1:] if not head[:2].isupper() else head}'
            break
        return b

    # ---- the rewrite ---------------------------------------------------------------------
    def new_header(self, h):
        """A header the plan names that BASE does not have: a guard, the umbrella, and the
        plan's [[header]] desc as its comment."""
        b = os.path.basename(h); guard = re.sub(r'\W', '_', b.upper())
        desc = self.headers_plan.get(b, {}).get('desc', 'declarations')
        L = wrap_comment('%s: %s.' % (b, desc)) + ['#ifndef %s' % guard, '#define %s' % guard]
        if self.plan.get('umbrella') and self.plan['umbrella'] != b: L += ['', '#include "%s"' % self.plan['umbrella']]
        self.raw[h] = '\n'.join(L + ['', '#endif', ''])
        self.headers[h] = self.path[h] = os.path.join(self.binc, b); self.inv[h] = []; self.incl[h] = []

    def plan_edits(self):
        rep = defaultdict(list); E = defaultdict(list); sus = defaultdict(set); cause = defaultdict(set)
        keep, excl = self.keep, set(self.excl)
        deleted = set(); inheader = {n: refs[0][0] for n, refs in self.hdecl.items()}; farv = set()
        rel = lambda f: f
        # header declarations of names the project neither defines nor uses, and the plan's drop
        for n, refs in self.hdecl.items():
            if n in keep: continue
            if n in self.drop or not self.known(n):
                for r in refs: E[r[0]].append(('del', r))
                deleted.add(n); rep['dropped from headers'].append(n)
        # far variables stay with their definition
        for n in self.hdecl:
            if n not in keep and n not in deleted and self.is_far_var(n):
                excl.add(n); farv.add(n); rep['far variable, per file'].append(n)
        # header declarations retyped to the definition; the sources' copies go
        for n, refs in sorted(self.hdecl.items()):
            if n in keep or n in deleted: continue
            if n in excl:
                for r in refs: E[r[0]].append(('del', r))
                deleted.add(n); rep['excluded: declared per file'].append(n)
                # each file declares it as the header did, which is how it compiled; a far
                # variable, excluded only for where its declaration may stand, as defined
                old = self.canon_text(n) if n in farv else self.text_of(refs[0])
                for f in sorted(self.users.get(n, ())):
                    if any(r[0] == f for r in self.ldecl[n]) or n in self.tricks[f]: continue
                    own = old
                    if n in self.defs and self.defs[n][0] == f:
                        dpos = self.inv[f][self.defs[n][1]]['start']
                        if not re.search(r'\b%s\b' % re.escape(n), self.code[f][:dpos]): continue
                        own = self.canon_text(n)
                    E[f].append(('ins', (refs[0][0], self.inv[refs[0][0]][refs[0][1]]['start'], wrap(own + ';'))))
                    rep['declared per file'].append(f'{n}:{f}')
                continue
            cn = self.canon_norm(n)
            if not same(self.norm_of(refs[0]), cn):
                E[refs[0][0]].append(('repl', refs[0], self.canon_text(n))); rep['retyped'].append(n)
                for f in self.users.get(n, ()):
                    if not any(r[0] == f for r in self.ldecl[n]) and n not in self.tricks[f]: sus[f].add(n)
            for r in refs[1:]:
                E[r[0]].append(('del', r)); rep['duplicate in headers'].append(n)
            for r in self.ldecl.get(n, []):
                E[r[0]].append(('del', r)); cause[r[0]].add(n)
                rep['local copies removed' if same(self.norm_of(r), cn) else 'local copies removed, another form'].append(f'{n}:{r[0]}')
                if not same(self.norm_of(r), cn): sus[r[0]].add(n)
        # renames around the includes (#define N OTHER ... #include ... #undef N)
        for f, tr in self.tricks.items():
            for n in sorted(tr):
                # a rename of a declaration goes (the headers now give one form, or none); one
                # of a header's tag, typedef or macro (a file's own struct of the same name)
                # stays
                if n in keep or n in self.htypes: continue
                E[f].append(('untrick', n)); sus[f].add(n); rep['renames around includes removed'].append(f'{n}:{f}')
        # names declared only in sources go to their owner's header, with the comments on
        # their declarations' lines
        self.adds = defaultdict(list); self.notes = defaultdict(list)
        for n in sorted(self.ldecl):
            if n in keep or n in excl or n in self.hdecl: continue
            why = None; t = self.canon_text(n)
            if n in self.statics: why = 'static somewhere'
            elif n in self.other: why = 'macro/typedef/tag'
            elif n in self.ccnames: why = 'compiler header name'
            elif self.ndefs[n] > 1: why = 'defined twice'
            elif self.is_far_var(n): why = 'far variable'
            elif any(re.search(r'\b%s\b' % re.escape(td), t) for td in self.tdefs_src): why = 'uses a file typedef'
            else:
                for m in re.finditer(r'\b(struct|union)\s+(\w+)\s*(far\s+|near\s+)?(?![\s\w]*\*)', t):
                    if m.group(2) not in self.htags: why = 'struct object type'
            if why: rep['not shared: ' + why].append(n); continue
            o = self.owner(n)
            h = self.hrel(self.header_for_file(o or self.ldecl[n][0][0]))
            if h not in self.headers: self.new_header(h); rep['headers created'].append(os.path.basename(h))
            self.adds[h].append((n, o)); inheader[n] = h; rep['added to headers'].append(n)
            cn = self.canon_norm(n)
            for r in self.ldecl[n]:
                E[r[0]].append(('del', r)); cause[r[0]].add(n)
                if not same(self.norm_of(r), cn): sus[r[0]].add(n)
                c = self.trailing(r[0], self.ent(r))
                if c: self.notes[n].append(c)
        # the includes the deletions need
        self.added_incs = defaultdict(set)
        for f in self.files:
            need = set()
            for ed in E.get(f, []):
                if ed[0] != 'del': continue
                n = self.name_of(ed[1])
                if n in inheader and n not in deleted: need.add(os.path.basename(inheader[n]))
            for h in sorted(need - set(self.closure(f))):
                E[f].append(('include', h)); self.added_incs[f].add(h); rep['includes added'].append(f'{h}:{f}')
                sus[f] |= {n for n in cause[f] if os.path.basename(inheader.get(n, '')) == h}
        # local struct and union definitions identical to one a header the file includes has
        hdefs = {}
        for h in self.headers:
            for i, e in enumerate(self.inv[h]):
                if e['kind'] in ('struct_def', 'union_def') and e['name']:
                    hdefs[(e['kind'], e['name'])] = (os.path.basename(h), re.sub(r'\s+', ' ', cparse.strip_comments(e['text'])).strip())
        for f in self.files:
            cl = set(self.closure(f, self.added_incs[f]))
            for i, e in enumerate(self.inv[f]):
                k = (e['kind'], e.get('name'))
                if k in hdefs and hdefs[k][0] in cl and \
                   re.sub(r'\s+', ' ', cparse.strip_comments(e['text'])).strip() == hdefs[k][1]:
                    E[f].append(('delent', i)); rep['local struct removed'].append(f'{e["name"]}:{f}')
        self.E, self.rep, self.sus, self.deleted, self.inheader = E, rep, sus, deleted, inheader

    def span_of(self, f, e, extend=False):
        """An entry's span; extend takes in the empty macros on the lines around it
        (HOST_LAYOUT_BEGIN before a struct, HOST_LAYOUT_END after it)."""
        raw = self.raw[f]; s, en = e['start'], e['end']
        if not extend or not cparse.EMPTY_MACROS: return s, en
        pat = r'(?:%s)' % '|'.join(map(re.escape, cparse.EMPTY_MACROS))
        m = re.search(r'(?:^|\n)([ \t]*%s[ \t]*\n\s*)$' % pat, raw[:s])
        if m:
            s = m.start(1)
            m2 = re.match(r'[ \t]*\n[ \t]*%s[ \t]*(?=\n|$)' % pat, raw[en:])
            if m2: en += m2.end()
        return s, en

    def apply(self, f):
        raw = self.raw[f]; spans = []; ins = []; incs = []; untricks = set(); byent = defaultdict(list)
        for ed in self.E.get(f, []):
            if ed[0] in ('del', 'repl'): byent[ed[1][1]].append(ed)
            elif ed[0] == 'ins': ins.append(ed[1])
            elif ed[0] == 'include': incs.append(ed[1])
            elif ed[0] == 'untrick': untricks.add(ed[1])
            elif ed[0] == 'delent':
                e = self.inv[f][ed[1]]; s, en = self.span_of(f, e, True); spans.append((s, en, '', [e['name']]))
        for i, eds in byent.items():
            e = self.inv[f][i]; s, en = e['start'], e['end']
            if e['kind'] == 'extern' and len(e['names']) > 1:
                gone = {ed[1][2] for ed in eds if ed[0] == 'del'}
                repl = {ed[1][2]: ed[2] for ed in eds if ed[0] == 'repl'}
                kept = [(j, x) for j, x in enumerate(e['names']) if j not in gone]
                if not kept: spans.append((s, en, '', [x['name'] for x in e['names']])); continue
                spans.append((s, en, ' '.join(repl.get(j, 'extern ' + x['decl']) + ';' for j, x in kept), []))
            else:
                ed = ([x for x in eds if x[0] == 'del'] or eds)[0]
                spans.append((s, en, '', [e.get('name')]) if ed[0] == 'del' else (s, en, wrap(ed[2] + ';'), []))
        for n in untricks:
            for m in re.finditer(r'(?m)^[ \t]*#[ \t]*(define[ \t]+%s[ \t]+[A-Za-z_]\w*|undef[ \t]+%s\b)[^\n]*' % (re.escape(n), re.escape(n)), raw):
                spans.append((m.start(), m.end(), '', [n]))
        spans += self.rename_comments(f, untricks)
        spans = sorted(set((a, b, c, tuple(d)) for a, b, c, d in spans)); clean_spans = []; pos = 0
        for sp in spans:
            if sp[0] < pos: continue
            clean_spans.append(sp); pos = sp[1]
        lines, rcs = remove_spans(raw, clean_spans) if clean_spans else (raw.split('\n'), [])
        self.removed_comments += [(f, c, n) for c, n, _ in rcs]
        drop = re.compile(self.plan['comment_drop'], re.I) if self.plan.get('comment_drop') else None
        for c, n, nn in rcs:     # a comment on one declaration alone goes with it to the header
            if nn == 1 and n in self.added and n not in self.pre and not (drop and drop.search(c)): self.pre[n] = c
        t = '\n'.join(lines)
        if f in self.adds: t = self.add_to_header(t, self.adds[f])
        if ins or incs:
            lines = t.split('\n'); last = None
            for q, l in enumerate(lines[:250]):
                if re.match(r'\s*#\s*(include|undef)\b', l): last = q
            at = (last + 1) if last is not None else 0
            block = ['#include "%s"' % h for h in sorted(incs)]
            if ins:
                block += ['', '/* Declared in each file that uses it, its own way (no header). */']
                block += [x[2] for x in sorted(set(ins))]
                if at < len(lines) and lines[at].strip(): block.append('')
            lines[at:at] = block
            t = '\n'.join(lines)
        return re.sub(r'\n{3,}', '\n\n', t)

    def rename_comments(self, f, gone):
        """Spans of the comments that introduce a run of renames (#define N OTHER lines)
        all of which go: the comment ending on the line directly above the run's first line,
        unless it is the file's opening comment."""
        raw = self.raw[f]; code = cparse.strip_comments(raw); L = raw.split('\n'); C = code.split('\n')
        off = [0]
        for l in L: off.append(off[-1] + len(l) + 1)
        isren = lambda q: re.match(r'\s*#\s*define\s+([A-Za-z_]\w*)\s+[A-Za-z_]\w*\s*$', C[q])
        out = []; q = 0
        while q < len(L):
            if not isren(q): q += 1; continue
            r = q
            while r < len(L) and isren(r): r += 1
            names = {isren(x).group(1) for x in range(q, r)}
            if names <= gone and q > 0 and L[q - 1].rstrip().endswith('*/') and not C[q - 1].strip():
                a = q - 1
                while a > 0 and '/*' not in L[a]: a -= 1
                if L[a].lstrip().startswith('/*') and raw[:off[a]].strip():
                    out.append((off[a], off[q] - 1, '', [sorted(names)[0]]))
            q = r
        return out

    def add_to_header(self, t, news):
        byowner = defaultdict(list)
        for n, o in news: byowner[o].append(n)
        for o, ns in sorted(byowner.items(), key=lambda kv: kv[0] or '￿'):
            ns = sorted(ns, key=lambda n: (self.inv[self.defs[n][0]][self.defs[n][1]]['start'] if n in self.defs else 0, n))
            decls = []
            for n in ns:
                d = wrap(self.canon_text(n) + ';')
                if n in self.pre: decls += self.pre[n].split('\n')
                cs = list(dict.fromkeys(' '.join(c.split()) for c in self.notes.get(n, [])))
                if len(cs) == 1 and '\n' not in d and len(d) + 2 + len(cs[0]) <= 100: d += '  ' + cs[0]
                else:
                    for c in cs: decls += wrap_comment(c[2:-2].strip())
                decls.append(d)
            lines = t.split('\n'); at = None
            for q, l in enumerate(lines):
                hit = re.match(r'/\* %s(:| |\*|$)' % re.escape(os.path.basename(o)), l) if o else \
                    re.search(r'defined where no source has it yet', l, re.I) and l.startswith('/*')
                if hit:
                    r = q + 1
                    while r < len(lines) and lines[r].strip() and not re.match(r'/\* [A-Za-z0-9_]+\.(C|ASM|c|asm)\b', lines[r]) \
                            and not lines[r].startswith('#endif'):
                        r += 1
                    at = r; break
            if at is not None:
                lines[at:at] = decls; t = '\n'.join(lines); continue
            end = max(q for q, l in enumerate(lines) if re.match(r'\s*#\s*endif\b', l)) if any(
                re.match(r'\s*#\s*endif\b', l) for l in lines) else len(lines)
            k = end; top = 0                 # never above the include guard's #define
            for q, l in enumerate(lines):
                if re.match(r'\s*#\s*(define|include)\b', l): top = q + 1; break
            while k > top and (not lines[k - 1].strip() or re.match(r'\s*#\s*(define|include)\b', lines[k - 1])): k -= 1
            block = [''] + wrap_comment(self.section_title(o)) + decls + ['']
            lines[k:k] = block; t = '\n'.join(lines)
        return t

    def tie_changes(self, texts):
        """{source: {names}} for tie groups whose order of first sight the rewrite changes."""
        key = order_key(self.cfg)
        if key is None: return {}
        newc = {}
        for r, t in texts.items(): newc[self.path[r]] = cparse.strip_comments(t)
        oldc = {}; out = defaultdict(set)
        for f in self.files:
            pubs, bss = [], []
            for e in self.inv[f]:
                if e['kind'] == 'func_def' and not e['static']: pubs.append(e['name'])
                if e['kind'] in ('var_def', 'static_var'):
                    for x in e['names']:
                        if e['kind'] == 'var_def': pubs.append(x['name'])
                        if not x['init']: bss.append(x['name'])
            groups = []
            for g in (pubs, bss):
                by = defaultdict(list)
                for n in dict.fromkeys(g): by[key(n)].append(n)
                groups += [ns for ns in by.values() if len(ns) > 1]
            if not groups: continue
            sb = sights(self.files[f], self.binc, oldc); sn = sights(self.files[f], self.binc, newc)
            for ns in groups:
                ob = sorted(ns, key=lambda n: sb.get(n, (1e12,))[0]); on = sorted(ns, key=lambda n: sn.get(n, (1e12,))[0])
                if ob != on: out[f] |= set(ns); self.rep['tie order changed'].append(f'{f}: {" ".join(ob)} -> {" ".join(on)}')
        return out

    def trailing(self, f, e):
        raw = self.raw[f]; nl = raw.find('\n', e['end']); nl = len(raw) if nl < 0 else nl
        m = re.match(r'[ \t]*(/\*.*?\*/)[ \t]*$', raw[e['end']:nl])
        return m.group(1) if m else None

    def rewrite(self):
        self.removed_comments = []; self.pre = {}
        self.plan_edits()
        self.added = {n for v in self.adds.values() for n, _ in v}
        texts = {}
        for f in self.path:
            if f in self.E or f in self.adds: texts[f] = self.apply(f)
        full = {r: texts.get(r, self.raw[r]) for r in self.path}
        inc_by = includes_tags({os.path.basename(h): full[h] for h in self.headers})
        for h in self.headers:
            t, added = forward_tags(full[h], inc_by(os.path.basename(h)))
            if added:
                texts[h] = full[h] = t
                self.rep['forward tags'] += [f'{k} {n}:{os.path.basename(h)}' for k, n in added]
        for fx in self.plan.get('fixup', []):
            f = fx['file']
            if f not in full: continue
            L = full[f].split('\n')
            a = [q for q, l in enumerate(L) if fx['move'] in l]; b = [q for q, l in enumerate(L) if fx['before'] in l]
            if a and b and a[0] > b[0]:
                L.insert(b[0], L.pop(a[0])); texts[f] = full[f] = '\n'.join(L); self.rep['fixups'].append(f)
        for f, ns in self.tie_changes(full).items(): self.sus[f] |= ns
        return {r: t for r, t in texts.items() if t != self.raw[r]}

    def suspects(self):
        return {f: sorted(v) for f, v in self.sus.items() if v}

    def run(self, write, report):
        os.makedirs(report, exist_ok=True)
        texts = self.rewrite()
        rep = {k: sorted(set(v)) for k, v in self.rep.items()}
        json.dump({'report': rep, 'suspects': self.suspects()}, open(os.path.join(report, 'update.json'), 'w'), indent=1)
        json.dump(self.suspects(), open(os.path.join(report, 'suspects.json'), 'w'), indent=1)
        json.dump(self.removed_comments, open(os.path.join(report, 'removed_comments.json'), 'w'), indent=1)
        print('; '.join(f'{k}: {len(v)}' for k, v in rep.items()))
        nh = sum(1 for r in texts if r in self.headers)
        print(f'{len(texts) - nh} of {len(self.files)} sources and {nh} of {len(self.headers)} headers change')
        if write:
            for r, p in self.path.items():
                dst = os.path.join(self.cfg.src, r)
                t = texts.get(r)
                if t is None:
                    if os.path.abspath(p) != os.path.abspath(dst): shutil.copyfile(p, dst)
                    continue
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                open(dst, 'w', encoding='latin1', newline='').write(t)
            print(f'wrote {len(texts)} files under {self.cfg.src}')


def main(argv):
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    def take(flag):
        if flag in a: i = a.index(flag); v = a[i + 1]; del a[i:i + 2]; return v
    base = take('--base'); exf = take('--exclude'); loop = take('--loop')
    Mode = Update if '--update' in a else Gen
    report = take('--report') or os.path.join(cfg.build, 'headergen')
    write = '--write' in a or loop is not None
    rest = [x for x in a if not x.startswith('--')]
    if not rest: sys.exit(__doc__)
    if write and not base:
        sys.exit('--write needs --base DIR, a copy of the sources taken before the pass: the pass rewrites [project] src')
    extra = read_names(exf)
    for it in range(int(loop or 1)):
        g = Mode(cfg, rest[0], base, extra)
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
