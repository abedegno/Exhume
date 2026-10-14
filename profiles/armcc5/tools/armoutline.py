"""Write an armbrute.py template of one function: its statement runs as inline helpers, or one
statement placed at every point of its body.

    python3 armoutline.py [--config PATH] SRC.c FUNC OUT.c [--max-run N] [--pairs] [--decls]
                          [--types]
    python3 armoutline.py [--config PATH] SRC.c FUNC OUT.c --sweep 'STMT' [--sweep-pairs]

Helpers (the default): every run of up to N (default 10) consecutive lines of FUNC's body that
can stand alone (balanced braces, no declarations, no return or goto, a break or continue only
inside its own loop) becomes one variant: the run moved into a `static __inline` function
called in its place. Locals used only in the run are declared inside the helper, read-only
ones passed in; a local the run writes and later code reads is either returned
(`x = inl_3(x, ...)`) or passed by address; a struct local is passed by address. armcc
allocates an inlined helper's code after its caller's own, so a helper the original source
had moves registers anywhere in the function without changing an instruction (compiler.md,
"Inline helpers"). --pairs adds every two non-overlapping runs; --decls multiplies each
variant by the orders of FUNC's declaration lines (up to five); --types gives each local's
type a choice point of its own (a #define T_name {{...}}: the C type it has and the integer
types). The product grows fast: --decls and --types together suit a short function, or
armbrute.py --climb.

Sweep (--sweep): one variant per statement boundary of FUNC's body with STMT inserted there
(--sweep-pairs: at every two boundaries). For a statement that should change nothing but the
allocation: a use of a value in one block (`{ u32 z; z = (u32)(p != 0); }`, compiler.md, "Dead
code that steers allocation"), a dead test, a block-local copy.

OUT.c is SRC.c with FUNC replaced by one {{@ ... @|@ ... @}} choice point listing every variant,
the first being FUNC unchanged. SRC.c is never touched."""
import itertools, os, re, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import armpermute as P

KEYWORDS = r'(?:return|goto|break|continue|case|default|else|do|sizeof|if|for|while|switch)\b'
# declarations of a struct, union or typedef'd non-integer type: `View v, w;`, `struct S *p;`
AGG = re.compile(r'^([ \t]*)((?:const\s+|volatile\s+)*(?:struct\s+|union\s+)?(?!' + KEYWORDS +
                 r')[A-Za-z_]\w*\s+)(\**\s*[A-Za-z_]\w*\s*(?:\[[^\]]*\]\s*)*(?:,\s*\**\s*[A-Za-z_]\w*\s*(?:\[[^\]]*\]\s*)*)*);[ \t]*$', re.M)


def is_decl(l):
    return P.DECL.match(l) or AGG.match(l)


def agg_decls(b):
    """({struct local: type}, {struct pointer local: (type, declarator)}) declared in body text b."""
    aggs, ptrs = {}, {}
    for d in AGG.finditer(b):
        if P.DECL.match(d.group(0)): continue
        for part in d.group(3).split(','):
            m = re.match(r'\s*(\*?)\s*([A-Za-z_]\w*)\s*(\[[^\]]*\])?\s*$', part)
            if not m or m.group(3): continue
            if m.group(1): ptrs[m.group(2)] = (d.group(2).strip() + ' *', part.strip())
            else: aggs[m.group(2)] = d.group(2).strip()
    return aggs, ptrs


def runs(lines, maxk):
    """(start, length) of every run of lines that can become a helper."""
    ind = lambda l: len(l) - len(l.lstrip())
    for i, l in enumerate(lines):
        if not l.strip() or is_decl(l) or l.strip().startswith(('}', 'else', 'case', 'default')): continue
        for k in range(1, maxk + 1):
            run = lines[i:i + k]
            if len(run) < k: break
            t = '\n'.join(run)
            if any(x.strip() and ind(x) < ind(run[0]) for x in run): break
            if any(is_decl(x) for x in run): break
            if t.count('{') != t.count('}') or t.count('(') != t.count(')'): continue
            if i + k < len(lines) and lines[i + k].strip().startswith('else'): continue
            if re.search(r'\b(return|goto|case|default)\b', t): continue
            if re.search(r'\b(break|continue)\b', t) and not re.search(r'\b(for|while|do|switch)\b', t): continue
            yield i, k


TYPES = False


def type_choices(known):
    """The integer types a --types choice point offers: each C type by the project's name for it."""
    by_c = {}
    for name, c in known.items(): by_c.setdefault(re.sub(r'\s+', ' ', c), name)
    return [by_c.get(c, c) for c in ('unsigned char', 'unsigned short', 'unsigned int', 'int')]


def typed_decls(lines):
    """Declaration lines rewritten one declarator per line, each typed T_<name>."""
    out = []
    for l in lines:
        d = P.DECL.match(l)
        if not d: out.append(l); continue
        for part in d.group(3).split(','):
            part = part.strip(); v = re.match(r'\*?\s*([A-Za-z_]\w*)', part).group(1)
            if re.match(r'\*?\s*\w+\s*\[', part):      # an array keeps its type (no T_ choice)
                out.append(f'{d.group(1)}{d.group(2).strip()} {part};'); continue
            out.append(f'{d.group(1)}T_{v} {part.lstrip("*").strip()};' if not part.startswith('*')
                       else f'{d.group(1)}T_{v} *{part[1:].strip()};')
    return out


def outline(lines, i, k, sig, name, mode='plain'):
    """(helper text, new lines) with lines[i:i+k] moved into `name`, or None. A local the run
    writes and later code reads makes mode 'plain' fail; mode 'ret' takes one such local as a
    parameter and returns it (x = name(x, ...)); mode 'ptr' passes each by address."""
    body = '\n'.join(lines)
    run = lines[i:i + k]; t = '\n'.join(run)
    rest = '\n'.join(l for l in lines[:i] + lines[i + k:] if not is_decl(l))
    aggs, ptrs = agg_decls(body)
    locs = dict(P._params(sig)); locs.update(P._decls(body))
    if TYPES: locs = {v: ((f'T_{v}' + (' *' if ty.endswith('*') else '')) if ty and decl is not None else ty, decl)
                      for v, (ty, decl) in locs.items()}
    locs.update(ptrs)          # struct pointers keep their type
    params, inner, out = [], [], []
    for v, (ty, decl) in locs.items():
        if not re.search(r'\b' + v + r'\b', t): continue
        if ty is None: return None
        used_out = re.search(r'\b' + v + r'\b', rest)
        if used_out or decl is None:
            if P._written(v, t) and used_out: out.append((ty, v))
            else: params.append((ty, v))
        else: inner.append(f'{ty} {decl}' if not decl.startswith('*') else f'{ty.rstrip(" *")} {decl}')
    if mode == 'plain' and out: return None
    if mode == 'ret' and len(out) != 1: return None
    if mode == 'ptr' and not out: return None
    d = len(run[0]) - len(run[0].lstrip())
    code = [l[d:] for l in run]
    sp = [(ty, v) for v, ty in aggs.items() if re.search(r'\b' + v + r'\b', t)]
    for _, v in sp: code = [re.sub(r'(?<![.>])\b' + v + r'\b', f'(*p_{v})', l) for l in code]
    if mode == 'ptr':
        for _, v in out: code = [re.sub(r'(?<![.>])\b' + v + r'\b', f'(*p_{v})', l) for l in code]
        ps = params + [(ty + ' *', 'p_' + v) for ty, v in out]
        args = [v for _, v in params] + ['&' + v for _, v in out]
    else:
        ps = params + out; args = [v for _, v in ps]
    ps = ps + [(ty + ' *', 'p_' + v) for ty, v in sp]; args = args + ['&' + v for _, v in sp]
    rty = out[0][0] if mode == 'ret' else 'void'
    fn = (f'static __inline {rty} {name}(' + (', '.join(f'{ty} {v}' for ty, v in ps) or 'void') + ')\n{\n'
          + ''.join(f'    {x};\n' for x in inner) + '\n'.join('    ' + l for l in code)
          + (f'\n    return {out[0][1]};' if mode == 'ret' else '') + '\n}\n')
    call = f'{name}(' + ', '.join(args) + ');'
    if mode == 'ret': call = f'{out[0][1]} = ' + call
    return fn, lines[:i] + [' ' * d + call] + lines[i + k:]


def boundaries(lines):
    """Indices where a statement can be inserted: after the declarations, after each line
    ending a statement or opening or closing a block, not before an `else`."""
    out, started = [], False
    for i, l in enumerate(lines):
        s = l.strip()
        if not s: continue
        if not started:
            if is_decl(l): continue
            started = True
            out.append(i)
        nxt = next((x.strip() for x in lines[i + 1:] if x.strip()), '')
        if s.endswith((';', '{', '}')) and not nxt.startswith(('else', 'while')) and not s.startswith(('case', 'default')):
            out.append(i + 1)
    return sorted(set(out))


def sweep(lines, stmt, pairs=False):
    """Bodies with stmt inserted at each boundary (or each two)."""
    ind = lambda j: next((len(x) - len(x.lstrip()) for x in lines[j:] if x.strip()), 4)
    bs = boundaries(lines)
    sets = [(b,) for b in bs] + (list(itertools.combinations(bs, 2)) if pairs else [])
    out = []
    for s in sets:
        nl = list(lines)
        for b in sorted(s, reverse=True): nl.insert(b, ' ' * ind(b) + stmt)
        out.append(nl)
    return out


def main(argv):
    global TYPES
    sys.path.insert(0, os.path.join(P.V.EXHUME, 'tools'))
    import config
    path, a = config.pop_config(argv)
    if len(a) < 3: sys.exit(__doc__)
    try:
        cfg = config.load(path); incs = [cfg.include] if getattr(cfg, 'include', None) else []
    except SystemExit:
        incs = []
    src, func, out = a[:3]
    TYPES = '--types' in a
    maxk = int(a[a.index('--max-run') + 1]) if '--max-run' in a else 10
    known = P.known_types(src, incs)
    P.set_types(sorted(known))
    with open(src, encoding='latin1') as f: text = f.read()
    start, lo, hi = P.find_func(text, func)
    sig = P.signature(text, func)
    # take a leading /* at: */ comment along, so every variant keeps it
    c = text.rfind('/* at: ' + func, 0, start)
    if c >= 0 and text[c:start].count('*/') == 1 and not text[text.index('*/', c) + 2:start].strip(): start = c
    head = text[start:lo]
    lines = text[lo:hi].split('\n')
    if '--sweep' in a:
        bodies = [('', lines)] + [('', b) for b in sweep(lines, a[a.index('--sweep') + 1], '--sweep-pairs' in a)]
    else:
        bodies = [('', lines)]
        rs = list(runs(lines, maxk))
        for n, (i, k) in enumerate(rs):
            for mode in ('plain', 'ret', 'ptr'):
                r = outline(lines, i, k, sig, f'inl_{n}', mode)
                if r: bodies.append(r)
        if '--pairs' in a:
            for (n1, (i1, k1)), (n2, (i2, k2)) in itertools.combinations(enumerate(rs), 2):
                if i2 < i1 + k1: continue
                r2 = outline(lines, i2, k2, sig, f'inl_{n2}')     # the later run first, so indices hold
                if not r2: continue
                r1 = outline(r2[1], i1, k1, sig, f'inl_{n1}')
                if r1: bodies.append((r2[0] + r1[0], r1[1]))
    # only the function's own declarations (before its first statement) are reordered: moving
    # one into or out of an inner block changes its scope
    decl_idx = []
    for j, l in enumerate(lines):
        if not l.strip() or l.strip() == '{': continue
        if is_decl(l): decl_idx.append(j)
        else: break
    variants = []
    for helpers, b in bodies:
        orders = [None]
        if '--decls' in a and 1 < len(decl_idx) <= 5:
            orders = list(itertools.permutations([b[j] for j in decl_idx]))
        for o in orders:
            bb = list(b)
            if o:
                for j, l in zip(decl_idx, o): bb[j] = l
            if TYPES: bb = typed_decls(bb)
            variants.append(helpers + head + '\n'.join(bb) + '}')
    defs = ''
    if TYPES:
        choices = type_choices(known)
        for v, (ty, decl) in P._decls('\n'.join(lines)).items():
            if ty is None: continue
            base = ty.rstrip(' *')
            ch = [base] + [c for c in choices if c != base] if not ty.endswith('*') else [base]
            defs += f'#define T_{v} {{{{' + '||'.join(ch) + '}}\n'
    for v in variants:
        if '@|@' in v or '{{@' in v or '@}}' in v: sys.exit('the source holds template syntax ({{@, @|@ or @}})')
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(out, 'w', encoding='latin1') as f:
        f.write(text[:start] + defs + '{{@' + '@|@'.join(variants) + '@}}' + text[hi + 1:])
    print(f'{func}: {len(bodies) - 1} variant bodies, {len(variants)} variants -> {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
