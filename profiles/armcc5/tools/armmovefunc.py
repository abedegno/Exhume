"""Move functions between source files of an armcc tree, with the declarations they need.

    python3 armmovefunc.py [--config PATH] FUNC... --to DEST [--write]
    python3 armmovefunc.py [--config PATH] --file MOVES.tsv [--write]     (FUNC, DEST per line)

Matched files are often cut by address (a unit of consecutive functions), not as the original
was written: armlink laid functions out by name, so one subsystem's functions are spread over
many such files. This moves a function, with its comments, into DEST (a path under the root,
created if missing; placed in address order among DEST's functions) and carries what it needs
from its old file: typedefs, macros, prototypes and extern declarations it uses. A data
definition becomes an extern (data stays in the file that owns it, since data is laid out per
file), DEST gets the old file's #includes, and the old file keeps a prototype if its other
functions call the moved one. The map's file column follows.

armcc compiles a call to a function defined anywhere in the same file using what it knows of
the callee's registers (compiler.md, "What a file's contents decide"), so a move that puts a
caller and a callee from different files into one file changes the caller's code: move
functions joined by calls together, and let the gate say. DEST's functions are then ordered
conservatively, a callee from another file after its caller and calls within one file as they
were: in tests the order made no difference, but it costs nothing.

Refused: a function that uses a static of its old file (data or a non-inline function), needs
a type or declaration DEST has in another form, or comes from a file whose /* opts: */ differ
from DEST's (a new DEST takes the old file's). A file left with no functions or data is
deleted. Without --write only the plan is printed. Run the gate after every move."""
import collections, os, re, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import armmap


OPTS = re.compile(r'/\*\s*opts:\s*[^*]*?\*/')


def read(p):
    with open(p, encoding='latin1') as f: return f.read()


def write_text(p, s):
    with open(p, 'w', encoding='latin1') as f: f.write(s)

KEYWORDS = set('''auto break case char const continue default do double else enum extern float for goto
if int long register return short signed sizeof static struct switch typedef union unsigned void
volatile while __packed __inline __asm __declspec __attribute__ __weak __irq'''.split())


def code_only(s):
    """comments and strings blanked, same length"""
    out = list(s); i = 0; n = len(s)
    while i < n:
        c = s[i]
        if s.startswith('/*', i):
            j = s.find('*/', i + 2); j = n if j < 0 else j + 2
            for k in range(i, j):
                if out[k] != '\n': out[k] = ' '
            i = j
        elif s.startswith('//', i):
            j = s.find('\n', i); j = n if j < 0 else j
            for k in range(i, j): out[k] = ' '
            i = j
        elif c in '"\'':
            j = i + 1
            while j < n and s[j] != c:
                j += 2 if s[j] == '\\' else 1
            for k in range(i + 1, min(j, n)):
                if out[k] != '\n': out[k] = ' '
            i = j + 1
        else: i += 1
    return ''.join(out)


class Item:
    def __init__(self, text, kind, names, used, start):
        self.text, self.kind, self.names, self.used, self.start = text, kind, names, used, start
    def __repr__(self): return f'<{self.kind} {sorted(self.names)}>'


def split_items(s):
    """top-level items, each with the comments and blank lines before it"""
    c = code_only(s)
    items, i, n, start = [], 0, len(s), 0
    depth = 0; par = 0
    while i < n:
        ch = c[i]
        if depth == 0 and par == 0 and ch == '#' and (i == 0 or c[i - 1] == '\n' or c[:i].rstrip(' \t').endswith('\n') or c[:i].strip() == ''):
            # preprocessor line (with continuations)
            j = i
            while True:
                e = s.find('\n', j); e = n if e < 0 else e
                if s[e - 1:e] == '\\': j = e + 1; continue
                break
            items.append((start, e + 1 if e < n else n, 'pp')); i = e + 1; start = i; continue
        if ch == '(': par += 1
        elif ch == ')': par -= 1
        elif ch == '{': depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and par == 0:
                # end of a function body? (a ')' before the opening '{' and no '=' / typedef)
                head = c[start:i]
                ob = _open_brace(c, start, i)
                pre = c[start:ob]
                if re.search(r'\)\s*$', pre) and not re.search(r'^\s*(typedef|struct|union|enum)\b', pre.strip() and pre.strip().split('\n')[-1] or '') and '=' not in pre.split(')')[-1]:
                    e = i + 1
                    # the rest of the line
                    nl = s.find('\n', e); e = n if nl < 0 else nl + 1
                    if s[i + 1:e].strip(): e = i + 1
                    items.append((start, e, 'func')); i = e; start = i; continue
        elif ch == ';' and depth == 0 and par == 0:
            e = i + 1
            nl = s.find('\n', e)
            if nl >= 0 and not s[e:nl].strip(): e = nl + 1
            items.append((start, e, 'decl')); i = e; start = i; continue
        i += 1
    if start < n: items.append((start, n, 'tail'))
    out = []
    for a, b, k in items:
        out.append(make_item(s[a:b], k, a))
    return out


def _open_brace(c, start, close):
    d = 0
    for k in range(close, start - 1, -1):
        if c[k] == '}': d += 1
        elif c[k] == '{':
            d -= 1
            if d == 0: return k
    return start


def make_item(text, kind, start):
    c = code_only(text)
    idents = set(re.findall(r'\b[A-Za-z_]\w*\b', c)) - KEYWORDS
    names = set()
    if kind == 'pp':
        m = re.match(r'\s*#\s*define\s+(\w+)', c.strip() and c[c.index('#'):] or '')
        if m: names.add(m.group(1)); kind = 'define'
        elif re.search(r'#\s*include', c): kind = 'include'
    elif kind == 'func':
        ob = c.index('{')
        m = re.search(r'(\w+)\s*\([^()]*(?:\([^()]*\)[^()]*)*\)\s*$', c[:ob])
        if m: names.add(m.group(1))
        if re.search(r'\bstatic\b', c[:ob]): kind = 'sfunc'
    elif kind == 'decl':
        body = c.strip()
        if re.match(r'typedef\b', body):
            kind = 'typedef'
            tail = body[body.rfind('}') + 1:] if '}' in body else body[len('typedef'):]
            for d in tail.rstrip(';').split(','):
                m = re.search(r'(\w+)\s*(\[[^\]]*\])*\s*$', d.replace('*', ' ').replace(')', ' ').strip())
                if m: names.add(m.group(1))
                m2 = re.search(r'\(\s*\*\s*(\w+)\s*\)', d)
                if m2: names.add(m2.group(1))
            m = re.match(r'typedef\s+(?:__packed\s+)?(?:struct|union|enum)\s+(\w+)\s*\{', body)
            if m: names.add('struct ' + m.group(1))
        elif re.match(r'(?:__packed\s+)?(struct|union|enum)\s+(\w+)\s*\{', body) and body.rstrip(';').rstrip().endswith('}'):
            kind = 'tag'; names.add('struct ' + re.match(r'(?:__packed\s+)?(?:struct|union|enum)\s+(\w+)', body).group(1))
        else:
            # declarations: names of the declarators (not their types)
            static = bool(re.match(r'static\b', body))
            ext = bool(re.match(r'extern\b', body))
            # enum constants
            for m in re.finditer(r'enum\s*\w*\s*\{([^}]*)\}', body):
                for e in m.group(1).split(','):
                    e = e.split('=')[0].strip()
                    if e: names.add(e)
            flat = re.sub(r'\{[^{}]*\}', '{}', body)
            while re.search(r'\{[^{}]*\}', flat) and '{}' not in flat: flat = re.sub(r'\{[^{}]*\}', '{}', flat)
            flat = flat.split('=')[0] if '=' in flat and '(' not in flat.split('=')[0][-3:] else flat
            proto = False
            for d in _top_commas(flat.rstrip(';')):
                d = d.split('=')[0]
                m = re.search(r'\(\s*\*\s*(\w+)\s*\)\s*\(', d)
                if m: names.add(m.group(1)); continue
                m = re.search(r'(\w+)\s*\([^()]*(?:\([^()]*\)[^()]*)*\)\s*(?:__attribute__\s*\(\(.*\)\))?\s*$', d)
                if m and m.group(1) not in KEYWORDS: names.add(m.group(1)); proto = True; continue
                m = re.search(r'(\w+)\s*(\[[^\]]*\]\s*)*$', d.strip())
                if m and m.group(1) not in KEYWORDS: names.add(m.group(1))
            kind = 'static' if static else ('proto' if proto and not ext else ('extern' if ext else 'data'))
            if proto and ext: kind = 'proto'
    used = idents - names
    used |= {'struct ' + t for t in re.findall(r'\b(?:struct|union|enum)\s+(\w+)', c)}
    return Item(text, kind, names, used, start)


def _top_commas(s):
    out, d, cur = [], 0, ''
    for ch in s:
        if ch in '([{': d += 1
        elif ch in ')]}': d -= 1
        if ch == ',' and d == 0: out.append(cur); cur = ''
        else: cur += ch
    out.append(cur)
    return out


def as_extern(item):
    """a data definition as an extern declaration (initializer dropped)"""
    c = item.text
    lead = c[:len(c) - len(c.lstrip())]
    body = c.strip()
    # drop the initializer: everything from the top-level '=' to the final ';'
    cc = code_only(body); d = 0; cut = None
    for k, ch in enumerate(cc):
        if ch in '([{': d += 1
        elif ch in ')]}': d -= 1
        elif ch == '=' and d == 0: cut = k; break
    decl = (body[:cut].rstrip() + ';') if cut is not None else body
    decl = re.sub(r'\[\s*\]', '[]', decl)
    # strip comments inside, keep it on one line when short
    return 'extern ' + decl + '\n'


def proto_of(item):
    c = item.text
    cc = code_only(c)
    ob = cc.index('{')
    head = c[:ob].strip()
    # drop leading comments
    hc = code_only(head)
    k = len(hc) - len(hc.lstrip())
    lines = head.split('\n')
    # keep from the first line that has code
    hc_lines = hc.split('\n')
    first = next(i for i, l in enumerate(hc_lines) if l.strip())
    head = '\n'.join(lines[first:]).strip()
    return re.sub(r'\s+', ' ', head) + ';\n'


def addr_of(name, rows):
    for r in rows:
        if r.name == name: return r.addr
    return 0


def reorder(text, rows, before):
    """DEST's functions in a conservative order: a call that crossed files before the move has its
    callee later in the file, and a call within one file keeps its order. BEFORE: name -> file
    before this move. Prototypes go first for functions defined after a use."""
    items = split_items(text)
    funcs = [it for it in items if it.kind in ('func', 'sfunc') and it.names]
    if len(funcs) < 2: return text
    name = {id(it): next(iter(it.names)) for it in funcs}
    pos = {name[id(it)]: k for k, it in enumerate(funcs)}
    succ = collections.defaultdict(set); indeg = collections.Counter()
    for a in funcs:
        na = name[id(a)]
        for b in funcs:
            nb = name[id(b)]
            if a is b or nb not in a.used: continue
            # a calls b
            if before.get(na) == before.get(nb) and before.get(na):
                x, y = (nb, na) if pos[nb] < pos[na] else (na, nb)     # as it was
            else:
                x, y = na, nb                                           # caller first
            if y not in succ[x]: succ[x].add(y); indeg[y] += 1
    order, ready = [], sorted((pos[n], n) for n in pos if indeg[n] == 0)
    ready = [n for _, n in ready]
    while ready:
        n = ready.pop(0); order.append(n)
        for y in sorted(succ[n], key=lambda z: pos[z]):
            indeg[y] -= 1
            if indeg[y] == 0: ready.append(y); ready.sort(key=lambda z: pos[z])
    if len(order) != len(funcs): return text          # a cycle: leave the order alone
    byname = {name[id(it)]: it for it in funcs}
    # rebuild: everything up to the first function, then the functions in the new order, each
    # with what sat between it and the previous function (declarations it needs)
    chunks, cur, lead = {}, '', ''
    first = funcs[0].start
    lead = text[:first]
    rest = items[items.index(funcs[0]):]
    pending = ''
    for it in rest:
        if it.kind in ('func', 'sfunc') and it.names:
            chunks[name[id(it)]] = pending + it.text; pending = ''
        else: pending += it.text
    out = lead + ''.join(chunks[n] if chunks[n].endswith('\n') else chunks[n] + '\n' for n in order) + pending
    # prototypes for functions defined after a use
    items2 = split_items(out)
    seen, need = set(), []
    for it in items2:
        if it.kind in ('proto', 'extern'): seen |= it.names
        if it.kind in ('func', 'sfunc') and it.names:
            for u in it.used:
                if u in byname and u not in seen and u not in it.names and proto_of(byname[u]) not in need:
                    need.append(proto_of(byname[u]))
            seen |= it.names
    if need:
        f0 = next(it for it in items2 if it.kind in ('func', 'sfunc'))
        out = out[:f0.start] + ''.join(need) + '\n' + out[f0.start:]
    return out


def move(cfg, funcs, dest, write=False):
    """Move funcs (names) into dest (a path from the root); returns an error message or ''."""
    root = os.path.realpath(cfg.root)
    mp = os.path.join(cfg.map, 'functions.tsv')
    rows = armmap.load(mp)
    filecol = {r.name: r.file for r in rows}
    before = dict(filecol)
    dpath = os.path.join(root, dest)
    dtext = read(dpath) if os.path.exists(dpath) else None
    texts = {}
    plan = []
    for fn in funcs:
        src = filecol.get(fn)
        if not src: return f'{fn}: not in the map'
        if src == dest: continue
        plan.append((fn, src))
    if not plan: return ''
    new_file = dtext is None
    for fn, src in plan:
        if src not in texts: texts[src] = read(os.path.join(root, src))
    # one file compiles with one set of options: a function keeps its old file's
    opts = {src: OPTS.search(texts[src]) for _, src in plan}
    opts = {src: m.group(0) if m else '' for src, m in opts.items()}
    want = OPTS.search(dtext).group(0) if dtext and OPTS.search(dtext) else ''
    if new_file:
        want = next(iter(opts.values()))
        dtext = want + '\n' if want else ''
    for src, o in opts.items():
        if o != want: return f'{src} has {o or "no opts line"}, {dest} {want or "none"}: options must agree'
    report = []
    for fn, src in plan:
        s = texts[src]
        items = split_items(s)
        fi = [it for it in items if it.kind in ('func', 'sfunc') and fn in it.names]
        if not fi: return f'{fn}: no definition in {src}'
        fitem = fi[0]
        if fitem.kind == 'sfunc': return f'{fn}: static in {src}'
        byname = {}
        for it in items:
            if it is fitem: continue
            for nme in it.names: byname.setdefault(nme, []).append(it)
        need, todo, seen = [], list(fitem.used), set()
        while todo:
            nme = todo.pop()
            if nme in seen: continue
            seen.add(nme)
            for it in byname.get(nme, []):
                if it.kind == 'static': return f'{fn}: uses {nme}, static in {src}'
                if it.kind == 'sfunc':
                    if '__inline' not in it.text and 'inline' not in it.text: return f'{fn}: calls static {nme} in {src}'
                if it not in need:
                    need.append(it)
                    if it.kind in ('typedef', 'tag', 'define', 'extern', 'proto', 'sfunc', 'data'):
                        todo.extend(it.used)
        # what DEST already declares
        ditems = split_items(dtext) if dtext else []
        dnames = {}
        for it in ditems:
            for nme in it.names: dnames.setdefault(nme, []).append(it)
        add = []
        for it in sorted(need, key=lambda x: x.start):
            if it.kind == 'func':
                txt = proto_of(it); key = it.names
            elif it.kind == 'data':
                txt = as_extern(it); key = it.names
            else:
                txt = it.text.lstrip('\n'); key = it.names
            if key and all(k in dnames for k in key):
                if it.kind in ('typedef', 'tag', 'define', 'sfunc', 'extern', 'proto', 'data'):
                    other = dnames[next(iter(key))][0]
                    mine = txt if it.kind in ('func', 'data') else it.text
                    norm = lambda x: re.sub(r'\s+', ' ', re.sub(r'^extern\s+', '', code_only(x).strip())).strip()
                    if other.kind in ('func', 'sfunc'): pass          # DEST defines it: fine
                    elif norm(other.text) != norm(mine):
                        return f'{fn}: {sorted(key)} declared differently in {dest}'
                continue
            if not key: continue
            add.append(txt)
            for k in key: dnames.setdefault(k, []).append(it)
        # includes
        incs = [l.strip() for it in items if it.kind == 'include' for l in it.text.split('\n') if l.strip().startswith('#include')]
        for inc in incs:
            # a quoted include found next to the old file is found from DEST's directory too
            m = re.match(r'#include\s+"([^"]+)"', inc)
            if m and os.path.isfile(os.path.join(root, os.path.dirname(src), m.group(1))):
                q = os.path.relpath(os.path.join(root, os.path.dirname(src), m.group(1)), os.path.dirname(dpath))
                inc = f'#include "{q}"'
            if inc not in dtext:
                # first include position
                m = list(re.finditer(r'(?m)^#include .*\n', dtext))
                pos = m[-1].end() if m else (dtext.index('*/\n') + 3 if dtext.startswith('/*') else 0)
                dtext = dtext[:pos] + inc + '\n' + dtext[pos:]
        # place the function in address order among DEST's functions
        ditems = split_items(dtext) if dtext else []
        a = addr_of(fn, rows)
        after = None
        for it in ditems:
            if it.kind in ('func', 'sfunc') and it.names:
                if addr_of(next(iter(it.names)), rows) < a: after = it
        ftext = fitem.text.strip('\n') + '\n'
        block = ''.join(add) + ('\n' if add else '') + ftext
        if after is None:
            # before the first function, after the declarations
            firstf = next((it for it in ditems if it.kind in ('func', 'sfunc')), None)
            pos = firstf.start if firstf else len(dtext)
            # keep the function's leading comments with it: firstf.start includes them already
        else:
            pos = after.start + len(after.text)
        if not dtext.endswith('\n') and pos == len(dtext): dtext += '\n'; pos += 1
        dtext = dtext[:pos] + ('\n' if pos and dtext[pos - 1:pos] == '\n' and not dtext[:pos].endswith('\n\n') else '') + block + dtext[pos:]
        # the old file: drop the function, keep a prototype if its other functions call it
        s2 = s[:fitem.start] + s[fitem.start + len(fitem.text):]
        rest = split_items(s2)
        callers = [it for it in rest if it.kind in ('func', 'sfunc') and fn in it.used]
        declared = [it for it in rest if it.kind in ('proto', 'extern') and fn in it.names]
        if callers and not declared:
            pr = proto_of(fitem)
            first = next(it for it in rest if it.kind in ('func', 'sfunc'))
            s2 = s2[:first.start] + pr + s2[first.start:]
        texts[src] = s2
        filecol[fn] = dest
        report.append(f'{fn}: {src} -> {dest} (+{len(add)} declarations)')
    dtext = reorder(dtext, rows, before)
    if write:
        os.makedirs(os.path.dirname(dpath), exist_ok=True)
        write_text(dpath, dtext)
        for src, s in texts.items():
            items = split_items(s)
            alive = [it for it in items if it.kind in ('func', 'sfunc', 'data', 'static')]
            if not alive:
                tracked = os.path.isdir(os.path.join(root, '.git')) and subprocess.run(
                    ['git', 'ls-files', '--error-unmatch', src], cwd=root, capture_output=True).returncode == 0
                if tracked: subprocess.run(['git', 'rm', '-q', '-f', src], cwd=root)
                else: os.remove(os.path.join(root, src))
                report.append(f'{src}: empty, removed')
            else:
                write_text(os.path.join(root, src), s)
        for r in rows:
            if r.name in filecol: r.file = filecol[r.name]
        armmap.save(mp, rows)
    print('\n'.join(report))
    return ''


def main(argv):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), 'tools'))
    import config
    path, argv = config.pop_config(argv)
    cfg = config.load(path)
    write = '--write' in argv
    argv = [a for a in argv if a != '--write']
    if not argv: sys.exit(__doc__)
    if argv[0] == '--file':
        groups = {}
        for line in read(argv[1]).splitlines():
            if not line.strip() or line.startswith('#'): continue
            fn, dest = line.split()[:2]
            groups.setdefault(dest, []).append(fn)
        for dest, fns in groups.items():
            err = move(cfg, fns, dest, write)
            if err: sys.exit('refused: ' + err)
        return 0
    if '--to' not in argv: sys.exit(__doc__)
    k = argv.index('--to')
    err = move(cfg, argv[:k], argv[k + 1], write)
    if err: sys.exit('refused: ' + err)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
