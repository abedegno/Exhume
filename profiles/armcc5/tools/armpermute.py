"""Random source permuter for one function: search for C that compiles to the image's bytes.

    python3 armpermute.py [--config PATH] SRC.c FUNC [--jobs N] [--iters N] [--seed S]
                          [--prefixes DIR]

Takes FUNC's definition in SRC.c and applies random text mutations that keep it C: swap two
adjacent statements, reorder or split local declarations, change a local's integer type, swap
the operands of a commutative operator or comparison, `x = x op y` against `x op= y`, `i++`
against `++i` and `i += 1`, add or drop a cast, read a global's field into a block-local
temporary first, add a test armcc deletes, invert an if/else, an if/else against a ternary, and
move a run of statements into a `static __inline` helper (inlined code is allocated after the
caller's own, so this moves registers; compiler.md). Each round compiles one variant per job
(armvariant.py: compile only, about a second under Wine) and scores FUNC against the image; a
variant that scores no worse is kept (so the search drifts along plateaus), now and then one a
little worse, and every 50 rounds the search restarts from the best. The best ever is written
to <build>/permute/FUNC/best.c, and a score of 0 stops the run.

Permuting finds register swaps that hand edits do not: two small changes far apart, each useless
alone. Reduce a result to the edits that matter with armreduce.py, then run match.py on it.
Integer types offered are C's own plus the scalar typedefs SRC and the headers it includes
(found next to it or in [project] include) define. SRC.c is never modified."""
import json, os, random, re, sys, shutil, tempfile, time
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import armvariant as V

# ---------------------------------------------------------------- the C this works on

C_INTS = ['unsigned char', 'signed char', 'unsigned short', 'short', 'unsigned int', 'int']
INT_TYPES = list(C_INTS)
_SCALAR_TD = re.compile(r'typedef\s+((?:(?:unsigned|signed|short|long|int|char|const|volatile)\s+)*'
                        r'(?:unsigned|signed|short|long|int|char))\s+(\w+)\s*;')


def scalar_typedefs(text):
    """{name: C type} for every `typedef <integer type> NAME;` in text."""
    return {m.group(2): m.group(1) for m in _SCALAR_TD.finditer(re.sub(r'/\*.*?\*/', ' ', text, flags=re.S))}


def known_types(src, include_dirs=()):
    """Integer typedef names visible in SRC: its own and its quoted includes', recursively."""
    seen, out, todo = set(), {}, [os.path.abspath(src)]
    while todo:
        p = todo.pop()
        if p in seen or not os.path.isfile(p): continue
        seen.add(p)
        with open(p, encoding='latin1') as f: text = f.read()
        out.update(scalar_typedefs(text))
        for inc in re.findall(r'^\s*#\s*include\s+"([^"]+)"', text, re.M):
            for d in (os.path.dirname(p),) + tuple(include_dirs):
                q = os.path.normpath(os.path.join(d, inc))
                if os.path.isfile(q): todo.append(q); break
    return out


def set_types(names):
    """Teach the mutations the project's integer typedefs (u8, uint16_t ...)."""
    global INT_TYPES, DECL, TYPES_RE
    INT_TYPES = list(C_INTS) + [n for n in names if n not in C_INTS]
    words = sorted({w for t in INT_TYPES for w in [t]} | {'unsigned', 'signed', 'char', 'short', 'long', 'int'},
                   key=len, reverse=True)
    TYPES_RE = '|'.join(re.escape(w) for w in words)
    DECL = re.compile(r'^([ \t]*)((?:const\s+|volatile\s+)*(?:(?:' + TYPES_RE + r')\s+)+)([^;()]*);[ \t]*$', re.M)


DECL = TYPES_RE = None
set_types([])
STMT = re.compile(r'^([ \t]*)([^\n{}]*;)[ \t]*$', re.M)


def find_func(text, func):
    """(start of the definition's first line, start of the body, end of the body): the body is
    text[lo:hi], without its braces."""
    m = re.compile(r'^[A-Za-z_][^\n;{}()]*\b' + re.escape(func) + r'\s*\(([^)]*)\)\s*\{', re.M).search(text)
    if not m: raise SystemExit(f'no definition of {func}')
    i = text.index('{', m.start()) + 1; lo, d = i, 1
    while d:
        d += {'{': 1, '}': -1}.get(text[i], 0); i += 1
    return m.start(), lo, i - 1


def signature(text, func):
    m = re.compile(r'^[A-Za-z_][^\n;{}()]*\b' + re.escape(func) + r'\s*\(([^)]*)\)\s*\{', re.M).search(text)
    return m.group(1) if m else ''

# ---------------------------------------------------------------- mutations (on the body text)


def m_swap_stmts(b, r):
    """Swap two adjacent simple statements at the same indentation (no declaration crossing)."""
    lines = b.split('\n')
    cand = [i for i in range(len(lines) - 1)
            if STMT.match(lines[i]) and STMT.match(lines[i + 1]) and not DECL.match(lines[i]) and not DECL.match(lines[i + 1])
            and STMT.match(lines[i]).group(1) == STMT.match(lines[i + 1]).group(1)
            and not re.search(r'\b(return|break|continue|goto|case|default)\b', lines[i] + lines[i + 1])]
    if not cand: return None
    i = r.choice(cand); lines[i], lines[i + 1] = lines[i + 1], lines[i]
    return '\n'.join(lines)


def m_decl_order(b, r):
    """Reorder declarators within one declaration, or swap two adjacent declaration lines."""
    ds = list(DECL.finditer(b))
    if not ds: return None
    if r.random() < 0.5:
        d = r.choice(ds)
        parts = [p.strip() for p in d.group(3).split(',')]
        if len(parts) < 2 or any('=' in p for p in parts): return None
        r.shuffle(parts)
        return b[:d.start()] + d.group(1) + d.group(2) + ', '.join(parts) + ';' + b[d.end():]
    lines = b.split('\n')
    idx = [i for i, l in enumerate(lines) if DECL.match(l)]
    pairs = [i for i in idx if i + 1 in idx]
    if not pairs: return None
    i = r.choice(pairs); lines[i], lines[i + 1] = lines[i + 1], lines[i]
    return '\n'.join(lines)


def m_split_decl(b, r):
    """Split a multi-declarator line into separate declarations."""
    ds = [d for d in DECL.finditer(b) if ',' in d.group(3) and '{' not in d.group(3)]
    if not ds: return None
    d = r.choice(ds)
    parts = [p.strip() for p in d.group(3).split(',')]
    return b[:d.start()] + '\n'.join(d.group(1) + d.group(2) + p + ';' for p in parts) + b[d.end():]


def m_retype(b, r):
    """Change one local's integer type."""
    ds = [d for d in DECL.finditer(b) if ',' not in d.group(3) and '[' not in d.group(3) and '*' not in d.group(3)]
    if not ds: return None
    d = r.choice(ds)
    return b[:d.start()] + d.group(1) + r.choice(INT_TYPES) + ' ' + d.group(3) + ';' + b[d.end():]


COMM = re.compile(r'(?<![\w\]\)])([A-Za-z_][\w\.\[\]>-]*|\d+)\s*(==|!=|\+|&|\||\^|\*)\s*([A-Za-z_][\w\.\[\]>-]*|\d+)(?![\w\[\(])')
REL = {'<': '>', '>': '<', '<=': '>=', '>=': '<='}
RELX = re.compile(r'(?<![\w\]\)])([A-Za-z_][\w\.\[\]>-]*|\d+)\s*(<=|>=|<|>)\s*([A-Za-z_][\w\.\[\]>-]*|\d+)(?![\w\[\(])')


def m_commute(b, r):
    """Swap the operands of a commutative operator, or of a comparison (flipping it)."""
    ms = [m for m in COMM.finditer(b)]
    ms += [m for m in RELX.finditer(b) if '<<' not in b[max(0, m.start() - 1):m.end() + 1]
           and '>>' not in b[max(0, m.start() - 1):m.end() + 1] and '->' not in m.group(0)]
    if not ms: return None
    m = r.choice(ms)
    return b[:m.start()] + f'{m.group(3)} {REL.get(m.group(2), m.group(2))} {m.group(1)}' + b[m.end():]


ASSIGN = re.compile(r'\b([A-Za-z_][\w\.\[\]>-]*)\s*=\s*\1\s*([-+&|^*]|<<|>>)\s*([^;]+);')
CASSIGN = re.compile(r'\b([A-Za-z_][\w\.\[\]>-]*)\s*([-+&|^*]|<<|>>)=\s*([^;]+);')


def m_compound(b, r):
    """`x = x op y` against `x op= y`."""
    ms = [('a', m) for m in ASSIGN.finditer(b)] + [('c', m) for m in CASSIGN.finditer(b)]
    if not ms: return None
    k, m = r.choice(ms)
    new = f'{m.group(1)} {m.group(2)}= {m.group(3)};' if k == 'a' else f'{m.group(1)} = {m.group(1)} {m.group(2)} ({m.group(3)});'
    return b[:m.start()] + new + b[m.end():]


def m_incr(b, r):
    """i++ against ++i against i += 1."""
    ms = list(re.finditer(r'\b([A-Za-z_]\w*)\+\+|\+\+([A-Za-z_]\w*)\b|\b([A-Za-z_]\w*)\s*\+=\s*1\b', b))
    if not ms: return None
    m = r.choice(ms); v = m.group(1) or m.group(2) or m.group(3)
    return b[:m.start()] + r.choice([f'{v}++', f'++{v}', f'{v} += 1']) + b[m.end():]


def m_dead(b, r):
    """Add a test armcc deletes (both outcomes reach the same code) after a statement: it can
    still move registers (compiler.md, "Dead code that steers allocation")."""
    lines = b.split('\n')
    locs = list(_decls(b))
    if not locs: return None
    cand = [i for i, l in enumerate(lines) if STMT.match(l) and not DECL.match(l)]
    if not cand: return None
    i = r.choice(cand); v = r.choice(locs); w = r.choice(locs + ['0', '1'])
    lines.insert(i + 1, f'{STMT.match(lines[i]).group(1)}if ({v} == {w}) {{ }}')
    return '\n'.join(lines)


def m_cast(b, r):
    """Add or remove a cast to an integer type on the right of an assignment. Never on a bare
    global scalar: a cast there narrows its load, which is a different program, not a spelling."""
    ms = list(re.finditer(r'(?<![=!<>])=\s*\((' + TYPES_RE + r'(?:\s+\w+)?)\)\s*', b))
    if ms and r.random() < 0.5:
        m = r.choice(ms); return b[:m.start()] + '= ' + b[m.end():]
    locs = set(_decls(b))
    ms = [m for m in re.finditer(r'(?<![=!<>+\-*/&|^%])=\s*(?!=)', b)]
    ms = [m for m in ms if not (re.match(r'([A-Za-z_]\w*)\b(?![.\[(]|->)', b[m.end():])
                                and re.match(r'([A-Za-z_]\w*)', b[m.end():]).group(1) not in locs)]
    if not ms: return None
    m = r.choice(ms)
    return b[:m.end()] + f'({r.choice(INT_TYPES)})' + b[m.end():]


def m_temp(b, r):
    """Read a struct field or array element on the right of an assignment into a block-local
    temporary first (a register's live range changes)."""
    lines = b.split('\n')
    locs = set(_decls(b))
    cand = []
    for i, l in enumerate(lines):
        if not STMT.match(l) or DECL.match(l): continue
        eq = l.find('=')
        if eq < 0 or l[eq + 1:eq + 2] == '=': continue
        for m in re.finditer(r'\b([A-Za-z_]\w*)(?:\.\w+|->\w+|\[[^\]]+\])+', l):
            if m.start() > eq and m.group(1) not in locs: cand.append((i, m.group(0)))
    if not cand: return None
    i, e = r.choice(cand)
    ind = STMT.match(lines[i]).group(1)
    name = f't{r.randrange(1000)}'
    lines[i] = lines[i].replace(e, name, 1)
    lines.insert(i, f'{ind}{{ {r.choice(INT_TYPES)} {name} = {e};')
    lines.insert(i + 2, f'{ind}}}')
    return '\n'.join(lines)


IFELSE1 = re.compile(r'\bif \(([^()\n]*(?:\([^()\n]*\)[^()\n]*)*)\) ([^;{}\n]+;) else ([^;{}\n]+;)')


def m_ifswap(b, r):
    """`if (c) A; else B;` against `if (!(c)) B; else A;` (branch order decides layout)."""
    ms = list(IFELSE1.finditer(b))
    if not ms: return None
    m = r.choice(ms); c = m.group(1)
    nc = c[2:-1] if c.startswith('!(') and c.endswith(')') else f'!({c})'
    return b[:m.start()] + f'if ({nc}) {m.group(3)} else {m.group(2)}' + b[m.end():]


TERN = re.compile(r'\bif \(([^()\n]*(?:\([^()\n]*\)[^()\n]*)*)\) ([A-Za-z_][\w\.\[\]>-]*) = ([^;\n]+); else \2 = ([^;\n]+);')
TERN2 = re.compile(r'\b([A-Za-z_][\w\.\[\]>-]*) = \(?([^?;\n]+?)\)? \? ([^:;\n]+) : ([^;\n]+);')


def m_ternary(b, r):
    """`if (c) x = A; else x = B;` against `x = c ? A : B;`."""
    ms = [('t', m) for m in TERN.finditer(b)] + [('i', m) for m in TERN2.finditer(b)]
    if not ms: return None
    k, m = r.choice(ms)
    if k == 't':
        return b[:m.start()] + f'{m.group(2)} = ({m.group(1)}) ? {m.group(3)} : {m.group(4)};' + b[m.end():]
    return b[:m.start()] + f'if ({m.group(2)}) {m.group(1)} = {m.group(3)}; else {m.group(1)} = {m.group(4)};' + b[m.end():]


SEP = '\n/*@body*/'          # a candidate is: helper functions, SEP, the function's body


def _decls(b):
    """{local: (type, declarator text)} for the declarations in body text b; the type is None
    for an array."""
    out = {}
    for d in DECL.finditer(b):
        for part in d.group(3).split(','):
            part = part.strip()
            m = re.match(r'(\*?)\s*([A-Za-z_]\w*)\s*(\[[^\]]*\])?', part)
            if not m: continue
            ty = re.sub(r'\s+', ' ', d.group(2).strip()) + (' *' if m.group(1) else '')
            out[m.group(2)] = (None if m.group(3) else ty, part)
    return out


def _params(sig):
    out = {}
    for p in sig.split(','):
        m = re.match(r'(.*?)([A-Za-z_]\w*)\s*$', p.strip())
        if m and m.group(1).strip() and m.group(1).strip() != 'void': out[m.group(2)] = (m.group(1).strip(), None)
    return out


def _written(v, t):
    return re.search(r'\b' + v + r'\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)|(?:\+\+|--)\s*' + v + r'\b|\b' + v +
                     r'\s*(?:\+\+|--)|&\s*' + v + r'\b(?!\s*&)', t)


def m_outline(b, r, sig=''):
    """Move a run of statements into a static __inline function called in their place."""
    if SEP not in b: return None
    helpers, body = b.split(SEP, 1)
    lines = body.split('\n')
    ind = lambda l: len(l) - len(l.lstrip())
    starts = [i for i, l in enumerate(lines) if l.strip() and not DECL.match(l)
              and not l.strip().startswith(('}', 'else', 'case', 'default'))]
    if not starts: return None
    for _ in range(10):
        i = r.choice(starts); k = r.choice([1, 1, 2, 2, 3, 4, 6, 8])
        run = lines[i:i + k]
        if len(run) < k: continue
        t = '\n'.join(run)
        if t.count('{') != t.count('}') or t.count('(') != t.count(')'): continue
        if any(l.strip() and ind(l) < ind(run[0]) for l in run) or any(DECL.match(l) for l in run): continue
        if i + k < len(lines) and lines[i + k].strip().startswith('else'): continue
        if re.search(r'\b(return|goto|case|default)\b', t): continue
        if re.search(r'\b(break|continue)\b', t) and not re.search(r'\b(for|while|do|switch)\b', t): continue
        rest = '\n'.join(lines[:i] + lines[i + k:])
        locs = dict(_params(sig)); locs.update(_decls(body))
        params, inner, ok = [], [], True
        for v, (ty, decl) in locs.items():
            if not re.search(r'\b' + v + r'\b', t): continue
            if ty is None: ok = False; break
            used_out = re.search(r'\b' + v + r'\b', '\n'.join(l for l in rest.split('\n') if not DECL.match(l)))
            if used_out or decl is None:
                if _written(v, t): ok = False; break
                params.append((ty, v))
            else: inner.append(f'{ty.rstrip(" *")} {decl}' if ty.endswith('*') and decl.startswith('*') else f'{ty} {decl}')
        if not ok: continue
        name = f'inl{helpers.count("static __inline void inl")}_{r.randrange(1000)}'
        d = ind(run[0])
        fn = (f'static __inline void {name}(' + (', '.join(f'{ty} {v}' for ty, v in params) or 'void') + ')\n{\n'
              + ''.join(f'    {x};\n' for x in inner) + '\n'.join('    ' + l[d:] for l in run) + '\n}\n')
        lines[i:i + k] = [' ' * d + f'{name}(' + ', '.join(v for _, v in params) + ');']
        return helpers + fn + SEP + '\n'.join(lines)
    return None


MUTATIONS = [(m_swap_stmts, 3), (m_decl_order, 3), (m_split_decl, 1), (m_retype, 2), (m_commute, 3),
             (m_compound, 1), (m_incr, 1), (m_dead, 1), (m_cast, 1), (m_temp, 1), (m_ifswap, 2), (m_ternary, 1),
             (m_outline, 2)]


def mutate(b, r, sig=''):
    """(new body, mutation name), or (None, None) when 20 tries changed nothing."""
    for _ in range(20):
        f = r.choices([m for m, _ in MUTATIONS], [w for _, w in MUTATIONS])[0]
        nb = f(b, r, sig) if f is m_outline else f(b, r)
        if nb and nb != b: return nb, f.__name__
    return None, None


class Source:
    """A file split around FUNC's body, so a mutated body (with helpers before the function)
    can be put back."""

    def __init__(self, text, func):
        start, lo, hi = find_func(text, func)
        self.sig = signature(text, func)
        self.pre, self.mid, self.tail = text[:start], text[start:lo], text[hi:]
        self.body = SEP + text[lo:hi]

    def whole(self, b):
        h, bd = b.split(SEP, 1)
        return self.pre + h + self.mid + bd + self.tail

# ---------------------------------------------------------------- search


def main(argv):
    sys.path.insert(0, os.path.join(V.EXHUME, 'tools'))
    import config
    path, a = config.pop_config(argv)
    if len(a) < 2: sys.exit(__doc__)
    cfg = config.load(path)
    src, func = os.path.abspath(a[0]), a[1]
    jobs = int(a[a.index('--jobs') + 1]) if '--jobs' in a else 4
    iters = int(a[a.index('--iters') + 1]) if '--iters' in a else 400
    seed = int(a[a.index('--seed') + 1]) if '--seed' in a else int(time.time())
    prefixes = a[a.index('--prefixes') + 1] if '--prefixes' in a else os.environ.get('EXHUME_ARMCC_PREFIXES')
    set_types(sorted(known_types(src, [cfg.include] if getattr(cfg, 'include', None) else [])))
    with open(src, encoding='latin1') as f: text = f.read()
    s = Source(text, func)
    t = V.Target.from_config(cfg, src, func)
    out = os.path.join(cfg.build, 'permute', func); os.makedirs(out, exist_ok=True)
    batches = [V.Batch(cfg, t, func, tempfile.mkdtemp(prefix='job-', dir=out),
                       os.path.join(os.path.abspath(os.path.expanduser(prefixes)), f'job{j}') if prefixes else None)
               for j in range(jobs)]
    (r0,), log = batches[0].compile([text])
    if not r0 or not r0[0]: sys.exit('the unmodified file does not compile:\n' + log[-2000:])
    t.adopt(*r0)
    base = t.score(*r0)
    print(f'{func}: start score {base} (0x{t.size:X} bytes); {jobs} jobs, {iters} rounds, seed {seed}', flush=True)
    best_score, best_body = cur_score, cur_body = base, s.body
    rng = random.Random(seed)
    with open(os.path.join(out, 'log.txt'), 'a') as log, ThreadPoolExecutor(jobs) as ex:
        for it in range(iters):
            cands = []
            for _ in range(jobs):
                b, how = cur_body, []
                for _ in range(rng.choice([1, 1, 1, 2, 3])):
                    nb, name = mutate(b, rng, s.sig)
                    if nb: b = nb; how.append(name)
                cands.append((b, how))
            res = list(ex.map(lambda p: batches[p[0]].scores([s.whole(p[1][0])])[0][0], enumerate(cands)))
            for (b, how), sc in zip(cands, res):
                if sc is None: continue
                if sc <= cur_score or (sc <= cur_score + 1 and rng.random() < 0.15):
                    if sc < cur_score: print(f'  round {it}: {cur_score} -> {sc} ({", ".join(how)})', flush=True)
                    cur_score, cur_body = sc, b
                if sc < best_score:
                    best_score, best_body = sc, b
                    with open(os.path.join(out, 'best.c'), 'w', encoding='latin1') as f: f.write(s.whole(best_body))
                    log.write(json.dumps({'round': it, 'score': sc, 'how': how}) + '\n'); log.flush()
            if best_score == 0: break
            if it % 50 == 49: cur_score, cur_body = best_score, best_body
    for b in batches: shutil.rmtree(b.work, ignore_errors=True)
    print(f'{func}: best score {best_score}' + (f' -> {out}/best.c' if best_score < base else ' (no improvement)'))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
