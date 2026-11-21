"""Source rewrite rules for armpermute2.py, on permc's tokens and statement structure.

Each rule takes a Ctx (one parsed base: the file's tokens, FUNC's statement tree, its locals
and parameters, the file's types and globals) and a random generator, and returns an Edit (a
list of token-span replacements on the whole file, plus a description of the site) or None
when it found no site. `everywhere=True` asks a rule that supports it to apply at every
matching site of the thing it chose at once (one local inlined at all its uses, one getter at
every read, every unsized extern sized), since several kinds of fix only work when made
everywhere and single-site search is what hand sweeps already cover.

The rules are the kinds of edit that produced matches in practice (a catalogue of 54 kinds,
written from the history of three firmware trees), each usable in both directions, plus the
generic passes of decomp-permuter worth having (MIT licence; the rules were reimplemented from
its pass names and descriptions, no code copied). They keep the program's meaning by
construction, except where marked 'probe' (dead code that armcc keeps to register allocation
and deletes afterwards) or 'type' (a local's type: the reader cannot prove the value fits).
IDs follow the catalogue: A* build and declarations, B* whole function, C* a local's
declaration, D* statements and expressions.

Facts the rules rely on (compiler.md): an empty `if (x == K) { }`, `(void)E`, a plain read or a
copy is deleted before allocation and changes nothing; the dead forms that survive are a
comparison stored in a block local, a comparison of two addresses and a shift of a constant
compared with 0. Two identical probes are merged, so a second probe needs another constant.
`static __inline` is only a hint at -O1: helpers are written `static __forceinline`."""
import random, re
from dataclasses import dataclass, field
import permc
from permc import Tok, texts

UINT = 'unsigned int'
INT_NAMES = {'unsigned char', 'signed char', 'unsigned short', 'short', 'unsigned int', 'int', 'char',
             'unsigned', 'signed', 'long', 'unsigned long', 'uchar', 'ushort', 'uint', 'u8', 'u16', 'u32',
             'int8_t', 'int16_t', 'int32_t', 'uint8_t', 'uint16_t', 'uint32_t', 's8', 's16', 's32', 'byte',
             'word', 'dword', 'undefined1', 'undefined2', 'undefined4', 'bool', 'BOOL', 'BYTE', 'WORD', 'DWORD'}


@dataclass
class Edit:
    spans: list                 # [(a, b, [Tok])] replacements on the file's tokens
    desc: str
    rule: str = ''
    meaning: str = 'same'       # same | probe | type
    parts: list = None          # a compound move's steps [(toks before, spans)], kept as separate patches


def apply(toks, spans):
    """toks with each span (a, b, new) replaced; spans must not overlap."""
    out = list(toks)
    last = None
    for a, b, new in sorted(spans, key=lambda s: (s[0], s[1]), reverse=True):
        if last is not None and b > last: raise ValueError('overlapping edits')
        out[a:b] = new
        last = a
    return out


@dataclass
class Local:
    name: str
    type: str
    stars: str
    dims: str
    node: object                # the declaration statement
    block: object
    init: tuple = None          # token span of the initialiser
    single: bool = True         # the only declarator of its statement


class Ctx:
    """One parsed base. Building one costs a few milliseconds on a large file."""

    def __init__(self, toks, tail, func, types, pair=None):
        self.toks, self.tail, self.func, self.types = toks, tail, func, types
        self.pair = pair or permc.match_brackets(toks)
        self.start, self.lb, self.rb = permc.find_function(toks, func, self.pair)
        self.root = permc.parse_body(toks, self.lb, self.pair)
        self.nodes = list(permc.walk(self.root))
        self.P = permc.ExprParser(toks, self.pair, types)
        self._expr = {}
        self.locals = {}
        self.dup_names = set()      # names declared more than once (block locals of probes): not
        self.params = {}            # one object, so the local rules leave them alone
        self._params()
        self._locals()
        self.ids = {}           # identifier -> [token indexes in the body]
        for i in range(self.lb, self.rb):
            if toks[i][0] == 'id': self.ids.setdefault(toks[i][1], []).append(i)
        self.consts = sorted({toks[i][1] for i in range(self.lb, self.rb) if toks[i][0] in ('num', 'chr')
                              and not re.search(r'[.eEfF]', toks[i][1].rstrip('uUlL').lower().replace('0x', 'x'))
                              or (toks[i][0] == 'num' and toks[i][1].lower().startswith('0x'))})

    # ---- helpers
    def src(self, a, b):
        """Source text of tokens a..b without the first token's whitespace."""
        if a >= b: return ''
        return (self.toks[a][1] + ''.join(t[2] + t[1] for t in self.toks[a + 1:b]))

    def indent(self, i):
        ws = self.toks[i][2]
        return ws[ws.rfind('\n') + 1:] if '\n' in ws else '    '

    def is_decl(self, n):
        return permc.is_decl(self.toks, n, self.types)

    def expr(self, a, b):
        k = (a, b)
        if k not in self._expr:
            try:
                self._expr[k] = self.P.parse(a, b)
            except Exception:
                self._expr[k] = None
        return self._expr[k]

    def stmt_expr(self, n):
        """The expression of an expression statement node (None for others)."""
        if n.kind != 'stmt' or self.is_decl(n): return None
        if self.toks[n.start][1] in ('return', 'break', 'continue', 'goto'): return None
        return self.expr(n.start, n.end - 1)

    def cond_expr(self, n):
        if n.kind in ('if', 'while', 'switch', 'do') and n.cond: return self.expr(n.cond[0] + 1, n.cond[1])
        return None

    def _params(self):
        lp = next(i for i in range(self.start, self.lb) if self.toks[i][1] == '(')
        rp = self.pair[lp]
        parts, cur = [], []
        for i in range(lp + 1, rp):
            if self.toks[i][1] == ',': parts.append(cur); cur = []
            else: cur.append(self.toks[i][1])
        parts.append(cur)
        for p in parts:
            ids = [w for w in p if re.match(r'[A-Za-z_]\w*$', w)]
            if len(ids) >= 2 and ids[-1] not in permc.QUALIFIERS:
                self.params[ids[-1]] = ' '.join(w for w in p[:p.index(ids[-1])] if w not in ('const',))

    def _locals(self):
        for blk in permc.blocks(self.root):
            for n in blk.children:
                if not self.is_decl(n): continue
                self._decl(n, blk)

    def _decl(self, n, blk):
        t = self.toks
        k = n.start; ty = []
        while k < n.end and (self.types.is_type_word(t[k][1]) or t[k][1] in ('struct', 'union', 'enum')):
            if t[k][1] in ('struct', 'union', 'enum'): ty.append(t[k][1]); k += 1
            ty.append(t[k][1]); k += 1
        if not ty and t[k][0] == 'id': ty.append(t[k][1]); k += 1
        decls, cur, depth = [], [], 0
        for i in range(k, n.end - 1):
            w = t[i][1]
            if w in '([{': depth += 1
            elif w in ')]}': depth -= 1
            if w == ',' and depth == 0: decls.append(cur); cur = []
            else: cur.append(i)
        decls.append(cur)
        single = len(decls) == 1
        for d in decls:
            if not d: continue
            eq = next((i for i in d if t[i][1] == '='), None)
            head = d if eq is None else d[:d.index(eq)]
            names = [i for i in head if t[i][0] == 'id' and t[i][1] not in permc.QUALIFIERS]
            if not names: continue
            stars = ''.join(t[i][1] for i in head if t[i][1] == '*')
            dims = ''
            if any(t[i][1] == '[' for i in head):
                b = next(i for i in head if t[i][1] == '[')
                dims = self.src(b, head[-1] + 1)
            init = (eq + 1, d[-1] + 1) if eq is not None else None
            ty_text = ' '.join(w for w in ty if w not in ('static', 'register', 'const', 'volatile'))
            if t[names[0]][1] in self.locals: self.dup_names.add(t[names[0]][1])
            self.locals[t[names[0]][1]] = Local(t[names[0]][1], ty_text, stars, dims, n, blk, init, single)

    # scope
    def enclosing(self, i):
        """The innermost node containing token i, walking down from the root."""
        n = self.root
        while True:
            for c in n.subs():
                if c.start <= i < c.end:
                    n = c; break
            else:
                return n

    def blocks_at(self, i):
        out = []
        n = self.enclosing(i)
        while n is not None:
            if n.kind == 'block': out.append(n)
            n = n.parent
        return out

    def visible(self, name, i):
        """Whether a local, parameter or global named name can be read at token i."""
        loc = self.locals.get(name)
        if loc is None:
            return name in self.params or name in self.types.globals
        return loc.block in self.blocks_at(i) and loc.node.end <= i

    def assigned_before(self, name, i):
        """Whether a local is written (or initialised) at some token before i (a rough check
        that a dead read of it is not of an uninitialised value)."""
        loc = self.locals.get(name)
        if loc is None: return True
        if loc.init: return True
        for j in self.ids.get(name, []):
            if j >= i: break
            nx = self.toks[j + 1][1] if j + 1 < len(self.toks) else ''
            pv = self.toks[j - 1][1]
            if nx in permc.ASSIGN or nx in ('++', '--') or pv in ('++', '--', '&'): return True
        return False

    def points(self):
        """Statement insertion points: (token index to insert before, block, child index)."""
        out = []
        for blk in permc.blocks(self.root):
            kids = blk.children
            first = 0
            while first < len(kids) and (self.is_decl(kids[first]) or kids[first].kind == 'pp'): first += 1
            for k in range(first, len(kids)):
                if kids[k].kind in ('label', 'pp'): continue
                out.append((kids[k].start, blk, k))
            out.append((blk.end - 1, blk, len(kids)))
        return out

    def insert_at(self, i, text):
        """A span edit putting statement text before token i, indented like it."""
        t = self.toks[i]
        ind = self.indent(i)
        if t[1] == '}':
            ws = t[2] if '\n' in t[2] else '\n' + ind
            new = permc.new(text, '\n' + ind + '    ') + [Tok(t[0], t[1], ws)]
        else:
            new = permc.new(text, t[2] if '\n' in t[2] else '\n' + ind) + [Tok(t[0], t[1], '\n' + ind)]
        return (i, i + 1, new)

    def replace(self, a, b, text, keep_ws=True):
        return (a, b, permc.new(text, self.toks[a][2] if keep_ws else ' '))

    def delete_stmt(self, n):
        return (n.start, n.end, [])

    def fresh(self, base):
        k = 0
        names = set(self.ids) | set(self.types.globals) | set(self.locals)
        allt = None
        while True:
            nm = f'{base}{k}'
            if nm not in names:
                if allt is None: allt = {t[1] for t in self.toks if t[0] == 'id'}
                if nm not in allt: return nm
            k += 1

    # type of a read expression, for caching it in a local
    def type_of(self, e):
        t = self.toks
        if e is None: return None
        if e.kind == 'paren': return self.type_of(e.kids[0])
        if e.kind == 'atom' and e.op == 'id':
            nm = t[e.start][1]
            if nm in self.locals:
                l = self.locals[nm]
                return None if l.dims else (l.type + (' ' + l.stars if l.stars else ''))
            if nm in self.params: return self.params[nm]
            g = self.types.globals.get(nm)
            if g and not g[1]: return g[0]
            return None
        if e.kind == 'index':
            base = e.kids[0]
            if base.kind == 'atom' and base.op == 'id':
                nm = t[base.start][1]
                g = self.types.globals.get(nm)
                if g and g[1]: return g[0]
                l = self.locals.get(nm)
                if l and l.dims: return l.type
                if l and l.stars == '*': return l.type
                p = self.params.get(nm)
                if p and p.endswith('*'): return p[:-1].strip()
            if base.kind == 'member':
                ft = self.types.fields.get(t[base.end - 1][1])
                if ft and ft.endswith('[]'): return ft[:-2]
            return None
        if e.kind == 'member':
            ft = self.types.fields.get(t[e.end - 1][1])
            if ft and not ft.endswith('[]'): return ft
            return None
        return None


def resolve_type(ctx, ty):
    """A type text with typedef names left as they are (armcc knows them); None if unknown."""
    if ty is None: return None
    ty = ty.replace('const ', '').replace('volatile ', '').strip()
    return ty or None


def is_int_type(ctx, ty):
    if ty is None: return False
    base = ty.replace('const ', '').replace('volatile ', '').strip()
    if base in INT_NAMES: return True
    td = ctx.types.typedefs.get(base)
    return td is not None and td in INT_NAMES


# ---------------------------------------------------------------- read expressions

def reads(ctx, e, out=None, parent=None):
    """Cacheable read sub-expressions of e (not stored to, not addressed): globals, members and
    constant-index elements reached from globals or parameters."""
    out = [] if out is None else out
    t = ctx.toks
    if e is None: return out
    if e.kind == 'assign':
        lhs, rhs = e.kids
        # the lhs's own index and base are reads, not the lhs itself
        for k in lhs.kids: reads(ctx, k, out, lhs)
        reads(ctx, rhs, out, e)
        return out
    if e.kind in ('postfix',) or (e.kind == 'unary' and e.op in ('++', '--', '&')):
        return out
    if cacheable(ctx, e):
        out.append(e)
        return out
    for k in e.kids: reads(ctx, k, out, e)
    return out


def cacheable(ctx, e):
    t = ctx.toks
    if e.kind == 'atom' and e.op == 'id':
        nm = t[e.start][1]
        g = ctx.types.globals.get(nm)
        return nm not in ctx.locals and nm not in ctx.params and g is not None and not g[1] and nm not in ctx.types.functions
    if e.kind == 'member':
        return _const_path(ctx, e.kids[0])
    if e.kind == 'index':
        return e.kids[1].kind == 'atom' and e.kids[1].op in ('num', 'chr', 'id') and _const_path(ctx, e.kids[0], allow_array=True) \
            and (e.kids[1].op != 'id' or t[e.kids[1].start][1] in ctx.locals or t[e.kids[1].start][1] in ctx.params)
    return False


def _const_path(ctx, e, allow_array=False):
    t = ctx.toks
    if e.kind == 'atom' and e.op == 'id':
        nm = t[e.start][1]
        return nm in ctx.types.globals or nm in ctx.params or (nm in ctx.locals and ctx.locals[nm].stars)
    if e.kind == 'member': return _const_path(ctx, e.kids[0], True)
    if e.kind == 'index':
        return e.kids[1].kind == 'atom' and e.kids[1].op in ('num', 'chr') and _const_path(ctx, e.kids[0], True)
    return False


def written_in(ctx, a, b, names):
    """Whether any identifier in names is assigned, incremented or address-taken in tokens a..b."""
    t = ctx.toks
    for i in range(a, b):
        if t[i][0] == 'id' and t[i][1] in names:
            nx = t[i + 1][1]; pv = t[i - 1][1]
            if nx in permc.ASSIGN or nx in ('++', '--') or pv in ('++', '--', '&'): return True
            # a store through a member or index of it: names.x = , names[k] =
            j = i + 1
            while t[j][1] in ('.', '->', '['):
                j = j + 2 if t[j][1] != '[' else ctx.pair[j] + 1
            if j != i + 1 and (t[j][1] in permc.ASSIGN or t[j][1] in ('++', '--')): return True
    return False


def has_call(ctx, a, b):
    """A function call in tokens a..b (a function-like macro of the file whose body calls
    nothing is not one)."""
    t = ctx.toks
    for i in range(a, b - 1):
        if t[i][0] == 'id' and t[i + 1][1] == '(' and t[i][1] not in permc.KEYWORDS and not _pure_macro(ctx, t[i][1]): return True
    return False


def needs_paren(ctx, x):
    """Whether an expression rewritten at x's place needs parentheses: not when it is already
    the whole of a parenthesised expression, a call argument, a condition, a statement, or the
    value of an assignment or return."""
    t = ctx.toks
    pv, nx = t[x.start - 1][1], t[x.end][1] if x.end < len(t) else ';'
    if pv == '(' and nx == ')' and ctx.pair.get(x.start - 1) == x.end: return False
    if pv in ('(', ',') and nx in (')', ',') and _in_call_args(ctx, x): return False
    if (pv in permc.ASSIGN or pv in ('return', ';', '{', '}')) and nx in (';', ')', ','): return False
    return True


def _in_call_args(ctx, x):
    j = x.start - 1
    depth = 0
    while j > ctx.lb:
        w = ctx.toks[j][1]
        if w in (')', ']'): j = ctx.pair[j] - 1; continue
        if w == '(':
            return ctx.toks[j - 1][0] == 'id' and ctx.toks[j - 1][1] not in permc.KEYWORDS
        if w in (';', '{', '}'): return False
        j -= 1
    return False


def optext(ctx, e, parent_op):
    """Source of operand e under binary operator parent_op, parenthesised only if needed."""
    s = ctx.src(e.start, e.end)
    if e.kind == 'paren':
        inner = e.kids[0]
        if inner.kind == 'bin' and permc.PREC.get(inner.op, 0) > permc.PREC.get(parent_op, 0) or inner.kind in ('atom', 'call', 'index', 'member'):
            return ctx.src(inner.start, inner.end)
        return s
    if e.kind == 'bin' and permc.PREC.get(e.op, 0) <= permc.PREC.get(parent_op, 0): return f'({s})'
    if e.kind in ('assign', 'cond', 'comma'): return f'({s})'
    return s


def wrap(ctx, x, text):
    return f'({text})' if needs_paren(ctx, x) else text


def paren(ctx, e):
    s = ctx.src(e.start, e.end)
    return s if e.kind in ('atom', 'paren', 'call', 'index', 'member') else f'({s})'


# ---------------------------------------------------------------- probes (D1, D2, D3)

PROBE = re.compile(r'^\{\s*(?:unsigned\s+int|uint|u32|unsigned)\s+(\w+)\s*;\s*\1\s*=\s*\((?:unsigned\s+int|uint|u32|unsigned)\)\s*(.*);\s*\}$', re.S)


def _probe_text(expr):
    return '{ unsigned int z; z = (unsigned int)(' + expr + '); }'


def _point_near(ctx, rng):
    """An insertion point; half the time the top of a block or case (where the recorded probes
    that decided matches sat), else any statement boundary."""
    pts = ctx.points()
    if not pts: return None
    if rng.random() < 0.5:
        tops = [p for p in pts if p[2] == 0 or (p[2] < len(p[1].children) and p[2] > 0 and p[1].children[p[2] - 1].kind in ('label',))
                or (p[2] > 0 and ctx.is_decl(p[1].children[p[2] - 1]))]
        if tops: return rng.choice(tops)
    return rng.choice(pts)


def _operands_near(ctx, i, rng, want_local=False):
    """Variable reads visible at token i, weighted to those used in nearby statements."""
    near = []
    for k in range(max(ctx.lb, i - 60), min(ctx.rb, i + 60)):
        w = ctx.toks[k]
        if w[0] == 'id' and w[1] not in permc.KEYWORDS and ctx.toks[k + 1][1] != '(' and ctx.visible(w[1], i):
            if w[1] in ctx.types.functions: continue
            loc = ctx.locals.get(w[1])
            if loc and (loc.dims or not ctx.assigned_before(w[1], i)): continue
            if want_local and not (loc or w[1] in ctx.params): continue
            g = ctx.types.globals.get(w[1])
            if g and g[1]: continue
            if not _int_name(ctx, w[1]): continue
            near.append(w[1])
    if not near:
        near = [n for n in list(ctx.locals) + list(ctx.params) if ctx.visible(n, i) and _int_name(ctx, n)
                and ctx.assigned_before(n, i)]
    return near


def _int_name(ctx, name):
    """Whether a local, parameter or global named name is an integer scalar."""
    l = ctx.locals.get(name)
    if l is not None: return not l.dims and not l.stars and is_int_type(ctx, l.type)
    if name in ctx.params: return is_int_type(ctx, ctx.params[name])
    g = ctx.types.globals.get(name)
    return g is not None and not g[1] and is_int_type(ctx, g[0])


def _const(ctx, rng, nonzero=False, at=None):
    """A constant of the function; usually one used near token at."""
    if at is not None and rng.random() < 0.7:
        near = [ctx.toks[k][1] for k in range(max(ctx.lb, at - 80), min(ctx.rb, at + 80))
                if ctx.toks[k][0] in ('num', 'chr') and ctx.toks[k][1] in ctx.consts and not (nonzero and ctx.toks[k][1] in ('0', '1'))]
        if near: return rng.choice(near)
    cs = [c for c in ctx.consts if not (nonzero and c in ('0', '1'))]
    if not nonzero: cs += ['0', '1']
    return rng.choice(cs) if cs else '0'


def r_probe_cmp(ctx, rng, everywhere=False):
    p = _point_near(ctx, rng)
    if not p: return None
    i = p[0]
    ops = _operands_near(ctx, i, rng)
    if not ops: return None
    x = rng.choice(ops)
    r = rng.random()
    if r < 0.6: e = f'{x} == {_const(ctx, rng, at=i)}'
    elif r < 0.8 and len(set(ops)) > 1:
        y = rng.choice([o for o in ops if o != x] or ops); e = f'{x} - {y} == 0'
    else:
        y = rng.choice(ops); e = f'{x} == {y}' if y != x else f'{x} == 0'
    return Edit([ctx.insert_at(i, _probe_text(e))], f'probe {e} @{_line(ctx, i)}', meaning='probe')


def r_probe_shift(ctx, rng, everywhere=False):
    p = _point_near(ctx, rng)
    if not p: return None
    i = p[0]
    ops = _operands_near(ctx, i, rng, want_local=True)
    if not ops: return None
    x = rng.choice(ops); k = _const(ctx, rng, nonzero=True, at=i)
    e = f'({k} << {x}) == 0'
    return Edit([ctx.insert_at(i, _probe_text(e))], f'probe {e} @{_line(ctx, i)}', meaning='probe')


def r_probe_addr(ctx, rng, everywhere=False):
    p = _point_near(ctx, rng)
    if not p: return None
    i = p[0]
    gs = sorted({w for w in ctx.ids if w in ctx.types.globals and w not in ctx.types.functions})
    if len(gs) < 2: return None
    a, b = rng.sample(gs, 2)
    e = f'(const void *)&{a} != (const void *)&{b}'
    return Edit([ctx.insert_at(i, _probe_text(e))], f'probe {e} @{_line(ctx, i)}', meaning='probe')


def find_probes(ctx):
    """[(kind, node or span, text)] of the probes in FUNC: stored comparisons in blocks, and the
    comma form `(t = (E)), C` in a condition."""
    out = []
    for n in ctx.nodes:
        if n.kind == 'block' and n is not ctx.root and len(n.children) == 2:
            s = ctx.src(n.start, n.end)
            if PROBE.match(' '.join(s.split())): out.append(('block', n, s))
        if n.kind in ('if', 'while') and n.cond:
            e = ctx.cond_expr(n)
            if e is not None and e.kind == 'comma' and e.kids[0].kind in ('paren', 'assign'):
                out.append(('comma', n, ctx.src(e.kids[0].start, e.kids[0].end)))
    return out


def r_probe_remove(ctx, rng, everywhere=False):
    ps = find_probes(ctx)
    if not ps: return None
    chosen = ps if everywhere else [rng.choice(ps)]
    spans = []
    for kind, n, s in chosen:
        if kind == 'block':
            spans.append((n.start, n.end, []))
        else:
            e = ctx.cond_expr(n)
            # drop `first,` keeping the condition
            spans.append((e.start, e.kids[1].start, []))
    return Edit(spans, 'remove probe ' + '; '.join(' '.join(s.split())[:60] for _, _, s in chosen), meaning='probe')


def r_probe_move(ctx, rng, everywhere=False):
    ps = [p for p in find_probes(ctx) if p[0] == 'block']
    if not ps: return None
    kind, n, s = rng.choice(ps)
    names = {w for w in re.findall(r'[A-Za-z_]\w*', s)} - {'unsigned', 'int', 'z', 'const', 'void', 'uint'}
    pts = [p for p in ctx.points() if not (n.start <= p[0] < n.end) and p[0] != n.end
           and all(ctx.visible(w, p[0]) or not (w in ctx.locals or w in ctx.params or w in ctx.types.globals) for w in names)]
    if not pts: return None
    i = rng.choice(pts)[0]
    text = ' '.join(s.split())
    return Edit([(n.start, n.end, []), ctx.insert_at(i, text)], f'move probe {text[30:70]} to @{_line(ctx, i)}', meaning='probe')


def _line(ctx, i):
    return 1 + sum(t[2].count('\n') for t in ctx.toks[ctx.lb:i + 1])


# ---------------------------------------------------------------- B1: locals against reading at each use

def r_local_inline(ctx, rng, everywhere=False):
    """B1 remove: a local assigned `v = E;` and read later is replaced at its reads by (T)(E),
    and the assignment goes; with everywhere, every assignment of the local."""
    t = ctx.toks
    cands = []
    for name, loc in ctx.locals.items():
        if loc.dims or loc.stars or name in ctx.dup_names: continue
        sites = []
        for n in ctx.nodes:
            e = ctx.stmt_expr(n)
            if e is None or e.kind != 'assign' or e.op != '=': continue
            lhs, rhs = e.kids
            if not (lhs.kind == 'atom' and t[lhs.start][1] == name): continue
            if permc.has_side_effects(rhs, t) or name in permc.idents(rhs, t): continue
            sites.append((n, rhs))
        if loc.init and loc.single:
            rhs = ctx.expr(*loc.init)
            if rhs is not None and not permc.has_side_effects(rhs, t) and name not in permc.idents(rhs, t):
                sites.append((loc.node, rhs))
        if sites: cands.append((name, loc, sites))
    if not cands: return None
    if everywhere and len(cands) > 1 and rng.random() < 0.3:
        # every eligible local at once (a run of byte locals removed in one step)
        eds = []
        sub = random.Random(rng.random())
        for nm, lc, st in cands:
            ctx1 = ctx
            ed = _inline_one(ctx1, sub, nm, lc, st, True, False)
            if ed: eds.append(ed)
        spans, used = [], []
        for ed in eds:
            if all(b <= a2 or a >= b2 for a, b, _ in ed.spans for a2, b2, _ in spans):
                spans += ed.spans; used.append(ed.desc)
        if not spans: return None
        return Edit(spans, 'inline every local: ' + '; '.join(used)[:200])
    name, loc, sites = rng.choice(cands)
    return _inline_one(ctx, rng, name, loc, sites, everywhere, None)


def _inline_one(ctx, rng, name, loc, sites, everywhere, single_use):
    t = ctx.toks
    chosen = sites if everywhere else [rng.choice(sites)]
    single_use_only = (everywhere and rng.random() < 0.5) if single_use is None else single_use
    spans, done = [], 0                                     # shape: those read exactly once
    for n, rhs in chosen:
        blk = n.parent if n is not loc.node else loc.block
        if blk is None or blk.kind != 'block': continue
        # uses: from the statement to the next write of name in this block's extent
        a, b = n.end, blk.end - 1
        uses = []
        for j in ctx.ids.get(name, []):
            if j < a or j >= b: continue
            nx = t[j + 1][1]; pv = t[j - 1][1]
            if nx in permc.ASSIGN or nx in ('++', '--') or pv in ('++', '--', '&'):
                b = j; break
            uses.append(j)
        uses = [j for j in uses if j < b]
        if not uses: continue
        if single_use_only and len(uses) != 1: continue
        last = uses[-1]
        deps = permc.idents(rhs, t)
        if written_in(ctx, a, b, deps): continue
        has_glob = any(d in ctx.types.globals for d in deps) or any(x.kind in ('member', 'index', 'unary') for x in permc.ewalk(rhs))
        # calls and stores before the last use's statement (a call taking the value as an
        # argument runs after it is read)
        lim = ctx.enclosing(last).start
        if has_glob and (has_call(ctx, a, lim) or _stores_between(ctx, a, lim, deps)): continue
        # a use inside a loop that does not contain the assignment would re-read: refuse
        if any(_in_loop_between(ctx, n, j) for j in uses): continue
        ty = loc.type
        val = ctx.src(rhs.start, rhs.end)
        if ctx.type_of(rhs) == ty or (rhs.kind == 'cast' and ctx.src(rhs.op[0], rhs.op[1]) == ty):
            rep = paren(ctx, rhs)
        elif is_int_type(ctx, ty) or ty in ('float', 'double'):
            rep = f'({ty})({val})' if rhs.kind not in ('atom', 'paren', 'call', 'index', 'member') else f'({ty}){val}'
        else:
            rep = f'({val})'
        spans.append((n.start, n.end, []))
        for j in uses:
            r_j = rep
            if t[j - 1][1] == '=' and t[j + 1][1] == ';' and ty in ('int', 'unsigned int', 'uint', 'unsigned') \
                    and rhs.kind in ('bin', 'unary', 'paren', 'cast'):
                r_j = val                      # the whole value of a store: the int local adds nothing
            spans.append((j, j + 1, permc.new(r_j, t[j][2])))
        done += 1
    if not done: return None
    # the declaration goes too if nothing else reads it
    remaining = set(ctx.ids.get(name, [])) - {j for a, b, _ in spans for j in range(a, b)}
    remaining.discard(next((i for i in range(loc.node.start, loc.node.end) if t[i][1] == name), None))
    if not remaining and loc.single and not any(a == loc.node.start for a, _, _ in spans):
        if not loc.init or not permc.has_side_effects(ctx.expr(*loc.init) or permc.E('atom', 0, 0), t):
            spans.append((loc.node.start, loc.node.end, []))
    # a block left holding one statement loses its braces (the cached form's inverse)
    blk = loc.block
    if not remaining and blk is not ctx.root and blk.parent is not None and blk.parent.kind == 'block' \
            and len(blk.children) == 2 and blk.children[0] is loc.node and any(a == loc.node.start for a, _, _ in spans):
        body = blk.children[1]
        inner = permc.render(apply(t[body.start:body.end], [(a - body.start, b - body.start, nw) for a, b, nw in spans
                                                             if body.start <= a and b <= body.end]))
        spans = [(blk.start, blk.end, permc.new(inner.strip(), t[blk.start][2]))]
    return Edit(spans, f'inline local {name} ({done} site{"s" if done > 1 else ""})')


def _stores_between(ctx, a, b, deps=None):
    """A store in tokens a..b that could change what deps read: through a pointer, or to one of
    deps itself (two named objects cannot alias; a store through a pointer may hit anything).
    Without deps, any store to anything but a plain local."""
    t = ctx.toks
    for i in range(a, b):
        if deps and t[i][0] == 'id' and t[i + 1][1] == '(' and t[i][1] in _macros(ctx):
            body = _macros(ctx)[t[i][1]]                # a macro's own stores
            if any(re.search(r'\b' + re.escape(d) + r'\b[^;=]*?(?<![=!<>])=(?!=)', body) for d in deps): return True
        if t[i][1] in permc.ASSIGN or t[i][1] in ('++', '--'):
            # find the lvalue's base identifier
            j = i - 1
            while j > a and t[j][1] in (']', ')'): j = ctx.pair[j] - 1
            while j > a and t[j - 1][1] in ('.', '->'): j -= 2
            base = t[j][1] if t[j][0] == 'id' else None
            if deps is None:
                if base is None or base not in ctx.locals or ctx.locals[base].stars: return True
                continue
            through_ptr = base is None or t[j - 1][1] == '*' or '->' in [x[1] for x in t[j:i]] \
                or (base in ctx.locals and ctx.locals[base].stars) or (base in ctx.params and ctx.params[base].endswith('*'))
            if through_ptr or base in deps: return True
    return False


def _in_loop_between(ctx, n, j):
    m = ctx.enclosing(j)
    while m is not None and m is not n.parent:
        if m.kind in ('for', 'while', 'do') and not (m.start <= n.start < m.end): return True
        m = m.parent
    return False


def _stmt_anchor(ctx, n):
    """The statement to wrap when caching a read used in node n."""
    return n


def r_local_cache(ctx, rng, everywhere=False):
    """B1 add: a read of a global, member or element in a statement is first copied into a
    block-scope local (or a function-scope one), and the statement reads the local."""
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        if n.parent is None or n.parent.kind != 'block': continue
        es = []
        if n.kind == 'stmt':
            e = ctx.stmt_expr(n)
            if e is None and t[n.start][1] == 'return' and n.end - n.start > 2: e = ctx.expr(n.start + 1, n.end - 1)
            if e is not None: es = reads(ctx, e)
        elif n.kind in ('if', 'while', 'switch'):
            e = ctx.cond_expr(n)
            if e is not None and n.kind != 'while': es = reads(ctx, e)
        es = [x for x in es if resolve_type(ctx, ctx.type_of(x)) and not ctx.type_of(x).endswith('*') or
              (ctx.type_of(x) or '').endswith('*')]
        es = [x for x in es if ctx.type_of(x)]
        if es: cands.append((n, es))
    if not cands: return None
    n, es = rng.choice(cands)
    mode = rng.random()
    if mode < 0.25 and n.kind == 'if':
        # both operands of a comparison in the condition (one rule in the catalogue's evidence)
        e = ctx.cond_expr(n)
        cmp_ = [x for x in permc.ewalk(e) if x.kind == 'bin' and x.op in ('<', '>', '<=', '>=', '==', '!=')
                and cacheable(ctx, x.kids[0]) and cacheable(ctx, x.kids[1]) and ctx.type_of(x.kids[0]) and ctx.type_of(x.kids[1])]
        if cmp_:
            c = rng.choice(cmp_)
            names = [ctx.fresh('pmt_a'), None]
            names[1] = names[0] + 'b'
            decl = ''.join(f'{resolve_type(ctx, ctx.type_of(k))} {nm} = {ctx.src(k.start, k.end)}; '
                           for k, nm in zip(c.kids, names))
            body = _replace_spans(ctx, n.start, n.end, [(k.start, k.end, nm) for k, nm in zip(c.kids, names)])
            return Edit([(n.start, n.end, permc.new('{ ' + decl + body + ' }', t[n.start][2]))],
                        f'cache both operands {ctx.src(c.start, c.end)[:50]} @{_line(ctx, n.start)}')
    x = rng.choice(es)
    text = ctx.src(x.start, x.end)
    same = [y for y in es if ctx.src(y.start, y.end) == text]
    use = same if rng.random() < 0.5 else [x]
    ty = resolve_type(ctx, ctx.type_of(x))
    nm = ctx.fresh('pmt_c')
    body = _replace_spans(ctx, n.start, n.end, [(y.start, y.end, nm) for y in use])
    if mode < 0.75:
        new = '{ ' + f'{ty} {nm} = {text}; ' + body + ' }'
        return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'cache {text[:40]} in a block local @{_line(ctx, n.start)}')
    # function-scope local: declared at the top, assigned just before the statement
    top = _decl_point(ctx, ctx.root)
    if top == n.start:
        spans = [(n.start, n.end, permc.new(f'{ty} {nm}; {nm} = {text}; ' + body, t[n.start][2]))]
    else:
        spans = [ctx.insert_at(top, f'{ty} {nm};'),
                 (n.start, n.end, permc.new(f'{nm} = {text}; ' + body, t[n.start][2]))]
    return Edit(spans, f'cache {text[:40]} in a function local @{_line(ctx, n.start)}')


def _replace_spans(ctx, a, b, reps):
    """Source of tokens a..b with sub-spans replaced by texts."""
    t = ctx.toks
    out = []
    i = a
    for s, e, txt in sorted(reps):
        out.append(ctx.src(i, s) if i == a else ''.join(x[2] + x[1] for x in t[i:s]))
        out.append((t[s][2] if s != a else '') + txt)
        i = e
    out.append(''.join(x[2] + x[1] for x in t[i:b]) if i != a else ctx.src(a, b))
    return ''.join(out)


def _decl_point(ctx, blk):
    """Token index before which a new declaration goes in blk (after its declarations)."""
    for c in blk.children:
        if not ctx.is_decl(c) and c.kind != 'pp': return c.start
    return blk.end - 1


def r_temp_expr(ctx, rng, everywhere=False):
    """decomp-permuter's temp_for_expr, typed: the value of a store `L = E;` (E an arithmetic
    expression with no side effects) computed first into a block local of L's type, int or
    unsigned int: `{ T t = E; L = t; }`."""
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        e = ctx.stmt_expr(n)
        if e is None or e.kind != 'assign' or e.op != '=' or n.parent is None or n.parent.kind != 'block': continue
        rhs = e.kids[1]
        if rhs.kind not in ('bin', 'cast', 'unary', 'cond') or permc.has_side_effects(rhs, t): continue
        cands.append((n, e, rhs))
    if not cands: return None
    n, e, rhs = rng.choice(cands)
    lt = resolve_type(ctx, ctx.type_of(e.kids[0]))
    types = ['int', 'unsigned int'] + ([lt] if lt and is_int_type(ctx, lt) else [])
    ty = rng.choice(types)
    if not (is_int_type(ctx, lt) or lt is None): return None
    nm = ctx.fresh('pmt_t')
    new = '{ ' + f'{ty} {nm} = {ctx.src(rhs.start, rhs.end)}; {ctx.src(e.kids[0].start, e.kids[0].end)} = {nm};' + ' }'
    return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'value of line {_line(ctx, n.start)} through a {ty} local')


# ---------------------------------------------------------------- B5, B4, B3: locals' declarations

def r_retype(ctx, rng, everywhere=False):
    t = ctx.toks
    ls = [l for l in ctx.locals.values() if l.single and not l.dims and not l.stars and is_int_type(ctx, l.type)]
    if not ls: return None
    l = rng.choice(ls)
    choices = [x for x in ('unsigned char', 'unsigned short', 'unsigned int', 'short', 'int', 'signed char')
               + tuple(n for n in ('uchar', 'ushort', 'uint', 'u8', 'u16', 'u32') if n in ctx.types.names)
               if x != l.type]
    new = rng.choice(choices)
    # the type words are the tokens before the name
    k = l.node.start
    name_at = next(i for i in range(l.node.start, l.node.end) if t[i][1] == l.name)
    keep = [t[i][1] for i in range(k, name_at) if t[i][1] in ('static', 'register', 'const', 'volatile')]
    return Edit([(k, name_at, permc.new(' '.join(keep + [new]), t[k][2]))], f'retype {l.name}: {l.type} -> {new}', meaning='type')


def r_decl_order(ctx, rng, everywhere=False):
    blks = [b for b in permc.blocks(ctx.root) if sum(1 for c in b.children if ctx.is_decl(c)) >= 2]
    if not blks: return None
    b = rng.choice(blks)
    decls = [c for c in b.children if ctx.is_decl(c)]
    x, y = rng.sample(decls, 2)
    if any(ctx.toks[i][1] == '=' for d in (x, y) for i in range(d.start, d.end)):
        # initialisers that read other locals must keep their order
        if has_call(ctx, x.start, x.end) or has_call(ctx, y.start, y.end): return None
    sx, sy = ctx.src(x.start, x.end), ctx.src(y.start, y.end)
    return Edit([(x.start, x.end, permc.new(sy, ctx.toks[x.start][2])), (y.start, y.end, permc.new(sx, ctx.toks[y.start][2]))],
                f'swap declarations {sx[:30]} / {sy[:30]}')


def r_split_decl(ctx, rng, everywhere=False):
    """B4: `T a, b;` into one declaration per line (the order kept)."""
    ds = {l.node for l in ctx.locals.values() if not l.single}
    if not ds: return None
    n = rng.choice(sorted(ds, key=lambda d: d.start))
    t = ctx.toks
    names = [l for l in ctx.locals.values() if l.node is n]
    names.sort(key=lambda l: next(i for i in range(n.start, n.end) if t[i][1] == l.name))
    text = ' '.join(f'{l.type} {l.stars}{l.name}{l.dims}' + (f' = {ctx.src(*l.init)}' if l.init else '') + ';' for l in names)
    return Edit([(n.start, n.end, permc.new(text, t[n.start][2]))], f'split declaration {ctx.src(n.start, n.end)[:40]}')


def r_scope(ctx, rng, everywhere=False):
    """B3: a function-scope local used in one block only moves into that block; or a block's
    local moves to function scope."""
    t = ctx.toks
    ls = [l for l in ctx.locals.values() if l.single and not l.init and l.name not in ctx.dup_names]
    if not ls: return None
    l = rng.choice(ls)
    uses = [j for j in ctx.ids.get(l.name, []) if not (l.node.start <= j < l.node.end)]
    if not uses: return None
    text = ctx.src(l.node.start, l.node.end)
    if l.block is ctx.root:
        # innermost block (below the root) holding every use
        cand = None
        for b in permc.blocks(ctx.root):
            if b is ctx.root: continue
            if all(b.start < j < b.end for j in uses) and (cand is None or b.start > cand.start):
                cand = b
        if cand is None: return None
        # loops: a value carried across iterations would be lost if moved into the loop body
        if cand.parent is not None and cand.parent.kind in ('for', 'while', 'do'): return None
        if any(m.kind in ('for', 'while', 'do') for m in _ancestors(cand) if m is not ctx.root) and not _assigned_first(ctx, l, cand, uses):
            return None
        if not _assigned_first(ctx, l, cand, uses): return None
        p = _decl_point(ctx, cand)
        return Edit([(l.node.start, l.node.end, []), ctx.insert_at(p, text)], f'scope {l.name} into the block at line {_line(ctx, cand.start)}')
    if any(o.name == l.name for o in ctx.locals.values() if o is not l) or l.name in ctx.params: return None
    p = _decl_point(ctx, ctx.root)
    if l.block.parent is not None and l.block.parent.kind in ('for', 'while', 'do'):
        pass                    # moving out of a loop body is safe for an uninitialised local
    return Edit([(l.node.start, l.node.end, []), ctx.insert_at(p, text)], f'scope {l.name} to function scope')


def _ancestors(n):
    while n is not None:
        yield n; n = n.parent


def _assigned_first(ctx, l, blk, uses):
    """The first use of l in blk is a plain assignment `l = ...` at blk's own level."""
    t = ctx.toks
    j = uses[0]
    if t[j + 1][1] != '=': return False
    n = ctx.enclosing(j)
    while n is not None and n.parent is not blk: n = n.parent
    return n is not None and n.kind == 'stmt' and n.start == j


# ---------------------------------------------------------------- B6 getters, B2 record access

def r_getter(ctx, rng, everywhere=True):
    """B6: reads of a global, member or element through a `static __forceinline` getter defined
    before FUNC; at every read of that expression, or (everywhere=False) one."""
    t = ctx.toks
    allr = []
    for n in ctx.nodes:
        e = ctx.stmt_expr(n) or ctx.cond_expr(n)
        if e is None and n.kind == 'stmt' and t[n.start][1] == 'return' and n.end - n.start > 2:
            e = ctx.expr(n.start + 1, n.end - 1)
        if e is None: continue
        for x in reads(ctx, e):
            if ctx.type_of(x) and _globals_only(ctx, x): allr.append(x)
    if not allr: return None
    x = rng.choice(allr)
    text = ctx.src(x.start, x.end)
    same = [y for y in allr if ctx.src(y.start, y.end) == text]
    use = same if everywhere or rng.random() < 0.7 else [x]
    seen = set(); use2 = []
    for y in use:
        if (y.start, y.end) not in seen: seen.add((y.start, y.end)); use2.append(y)
    ty = resolve_type(ctx, ctx.type_of(x))
    fn = ctx.fresh('pmt_get')
    spans = [(y.start, y.end, permc.new(f'{fn}()', t[y.start][2])) for y in use2]
    defn = f'static __forceinline {ty} {fn}(void) {{ return {text}; }}\n'
    s = ctx.start
    spans.append((s, s + 1, permc.new(defn, t[s][2]) + [Tok(t[s][0], t[s][1], '\n')]))
    return Edit(spans, f'getter for {text[:40]} at {len(use2)} read{"s" if len(use2) > 1 else ""}')


def _globals_only(ctx, e):
    t = ctx.toks
    for x in permc.ewalk(e):
        if x.kind == 'atom' and x.op == 'id':
            nm = t[x.start][1]
            if nm in ctx.locals or nm in ctx.params: return False
    return True


def r_pointer_direct(ctx, rng, everywhere=False):
    """B2: a pointer local `p = &X;` read as p->f / p[k] / *p becomes X.f / (&X)[k] / X."""
    t = ctx.toks
    cands = []
    for name, l in ctx.locals.items():
        if l.stars != '*' or l.dims: continue
        for n in ctx.nodes:
            e = ctx.stmt_expr(n)
            if e is None or e.kind != 'assign' or e.op != '=': continue
            lhs, rhs = e.kids
            if not (lhs.kind == 'atom' and t[lhs.start][1] == name): continue
            if rhs.kind == 'unary' and rhs.op == '&' and _const_path(ctx, rhs.kids[0], True):
                cands.append((name, n, rhs.kids[0]))
        if l.init:
            e = ctx.expr(*l.init)
            if e is not None and e.kind == 'unary' and e.op == '&' and _const_path(ctx, e.kids[0], True):
                cands.append((name, None, e.kids[0]))
    if not cands: return None
    name, n, target = rng.choice(cands)
    tx = ctx.src(target.start, target.end)
    a = n.end if n is not None else ctx.locals[name].node.end
    blk = (n.parent if n is not None else ctx.locals[name].block)
    b = blk.end
    spans = []
    for j in ctx.ids.get(name, []):
        if j < a or j >= b: continue
        nx, pv = t[j + 1][1], t[j - 1][1]
        if nx in permc.ASSIGN or nx in ('++', '--') or pv in ('++', '--', '&'): break
        if nx == '->':
            spans.append((j, j + 2, permc.new(f'{tx}.', t[j][2])))
        elif nx == '[':
            spans.append((j, j + 1, permc.new(f'(&{tx})', t[j][2])))
        elif pv == '*' and t[j - 2][1] in ('(', '=', ',', ';', '{', '}', 'return') or (pv == '*' and t[j - 2][0] == 'op' and t[j - 2][1] not in (')', ']')):
            spans.append((j - 1, j + 1, permc.new(f'({tx})', t[j - 1][2])))
        else:
            return None
    if not spans: return None
    l = ctx.locals[name]
    occ = [j for j in ctx.ids.get(name, []) if not (l.node.start <= j < l.node.end)]
    if n is None and l.single and all(any(a <= j < b for a, b, _ in spans) for j in occ):
        spans.append((l.node.start, l.node.end, []))       # the initialised pointer is not needed
    return Edit(spans, f'pointer {name} -> direct {tx[:40]} ({len(spans)} uses)')


# ---------------------------------------------------------------- C1 .. C5

def r_init_decl(ctx, rng, everywhere=False):
    """C1: `T x; ... x = K;` against `T x = K;` (both ways)."""
    t = ctx.toks
    if rng.random() < 0.5:
        # merge: first statement of the declaring block that assigns x from constants or
        # unwritten reads, with no read of x before it
        cands = []
        for l in ctx.locals.values():
            if l.init or not l.single or l.dims or l.name in ctx.dup_names: continue
            blk = l.block
            for c in blk.children:
                if c.start < l.node.end: continue
                e = ctx.stmt_expr(c)
                uses_before = [j for j in ctx.ids.get(l.name, []) if l.node.end <= j < c.start]
                if uses_before: break
                if e is not None and e.kind == 'assign' and e.op == '=' and e.kids[0].kind == 'atom' and t[e.kids[0].start][1] == l.name:
                    rhs = e.kids[1]
                    if permc.has_side_effects(rhs, t): break
                    deps = permc.idents(rhs, t)
                    if any(d in ctx.locals and ctx.locals[d].node.start >= l.node.start for d in deps): break
                    if (deps and (written_in(ctx, l.node.end, c.start, deps) or has_call(ctx, l.node.end, c.start)
                                  or _stores_between(ctx, l.node.end, c.start))):
                        break
                    cands.append((l, c, rhs))
                    break
                if c.kind != 'stmt' or has_call(ctx, c.start, c.end): break
        if not cands: return None
        l, c, rhs = rng.choice(cands)
        name_at = next(i for i in range(l.node.start, l.node.end) if t[i][1] == l.name)
        return Edit([(name_at, name_at + 1, permc.new(f'{l.name} = {ctx.src(rhs.start, rhs.end)}', t[name_at][2])),
                     (c.start, c.end, [])], f'initialise {l.name} at its declaration')
    cands = [l for l in ctx.locals.values() if l.init and l.single and not l.dims]
    if not cands: return None
    l = rng.choice(cands)
    eq = l.init[0] - 1
    val = ctx.src(*l.init)
    p = _decl_point(ctx, l.block)
    return Edit([(eq, l.init[1], []), ctx.insert_at(p, f'{l.name} = {val};')], f'split initialiser of {l.name}')


def r_const_local(ctx, rng, everywhere=False):
    """C2: a constant argument or stored constant passed through a narrow block local."""
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        if n.parent is None or n.parent.kind != 'block': continue
        e = ctx.stmt_expr(n)
        if e is None: continue
        for x in permc.ewalk(e):
            if x.kind == 'call':
                for a in x.kids[1:]:
                    v = permc.const_value(a, t)
                    if v is not None and 0 <= v < 1 << 31: cands.append((n, a, v))
            if x.kind == 'assign' and x.op == '=':
                v = permc.const_value(x.kids[1], t)
                if v is not None and 0 <= v < 1 << 31: cands.append((n, x.kids[1], v))
    if not cands: return None
    n, a, v = rng.choice(cands)
    ty = 'unsigned char' if v < 256 else 'unsigned short' if v < 65536 else 'unsigned int'
    nm = ctx.fresh('pmt_k')
    body = _replace_spans(ctx, n.start, n.end, [(a.start, a.end, nm)])
    if rng.random() < 0.5:
        new = '{ ' + f'{ty} {nm}; {nm} = {ctx.src(a.start, a.end)}; ' + body + ' }'
    else:
        new = '{ ' + f'{ty} {nm} = {ctx.src(a.start, a.end)}; ' + body + ' }'
    return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'constant {ctx.src(a.start, a.end)} through a local @{_line(ctx, n.start)}')


def r_dead_local(ctx, rng, everywhere=False):
    """C3: an unused local initialised from a global, declared at the top of a block."""
    gs = sorted({w for w in ctx.ids if w in ctx.types.globals and not ctx.types.globals[w][1]
                 and w not in ctx.types.functions and is_int_type(ctx, ctx.types.globals[w][0])})
    if not gs: return None
    g = rng.choice(gs)
    blk = rng.choice(permc.blocks(ctx.root))
    p = _decl_point(ctx, blk)
    nm = ctx.fresh('pmt_u')
    return Edit([ctx.insert_at(p, f'{resolve_type(ctx, ctx.types.globals[g][0])} {nm} = {g};')],
                f'unused local = {g} in the block at line {_line(ctx, blk.start)}', meaning='probe')


def r_ptr_store(ctx, rng, everywhere=False):
    """C5: `g = v;` through a block pointer `{ T *p = &g; *p = v; }`."""
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        e = ctx.stmt_expr(n)
        if e is None or e.kind != 'assign' or e.op != '=' or n.parent is None or n.parent.kind != 'block': continue
        lhs = e.kids[0]
        if lhs.kind == 'atom' and t[lhs.start][1] in ctx.types.globals:
            ty = ctx.type_of(lhs)
            if ty: cands.append((n, lhs, e.kids[1], ty))
    if not cands: return None
    n, lhs, rhs, ty = rng.choice(cands)
    nm = ctx.fresh('pmt_p')
    new = '{ ' + f'{resolve_type(ctx, ty)} *{nm} = &{ctx.src(lhs.start, lhs.end)}; *{nm} = {ctx.src(rhs.start, rhs.end)}; ' + '}'
    return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'store to {ctx.src(lhs.start, lhs.end)} through a pointer')


# ---------------------------------------------------------------- D4 redundant stores

def r_dup_store(ctx, rng, everywhere=False):
    t = ctx.toks
    stores = []
    for n in ctx.nodes:
        e = ctx.stmt_expr(n)
        if e is None or e.kind != 'assign' or e.op != '=' or n.parent is None or n.parent.kind != 'block': continue
        lhs, rhs = e.kids
        if permc.is_lvalue_simple(lhs, t) and permc.const_value(rhs, t) is not None and not _reads_local_index(ctx, lhs):
            stores.append((n, lhs, rhs))
    mode = rng.random()
    if mode < 0.3 and stores:
        n, lhs, rhs = rng.choice(stores)
        s = ctx.src(n.start, n.end)
        return Edit([ctx.insert_at(n.end, s)], f'repeat store {s}')
    if mode < 0.55 and stores:
        # a dead store before another store to the same place
        n, lhs, rhs = rng.choice(stores)
        lt = ctx.src(lhs.start, lhs.end)
        targets = []
        for m in ctx.nodes:
            e = ctx.stmt_expr(m)
            if e is not None and e.kind == 'assign' and e.op == '=' and ctx.src(e.kids[0].start, e.kids[0].end) == lt \
                    and lt.split('[')[0].split('.')[0] not in permc.idents(e.kids[1], t) and m.parent is not None and m.parent.kind == 'block' \
                    and not permc.has_side_effects(e.kids[1], t):      # a call there could read the dead value
                targets.append(m)
        if not targets: return None
        m = rng.choice(targets)
        k = rng.choice(['0'] + [ctx.src(rhs.start, rhs.end)])
        return Edit([ctx.insert_at(m.start, f'{lt} = {k};')], f'dead store {lt} = {k} before line {_line(ctx, m.start)}')
    # a store of the value a test or case just established
    regions = []
    for n in ctx.nodes:
        if n.kind == 'if':
            e = ctx.cond_expr(n)
            if e is not None and e.kind == 'bin' and e.op == '==' and permc.is_lvalue_simple(e.kids[0], t) \
                    and permc.const_value(e.kids[1], t) is not None and n.then.kind == 'block':
                regions.append((ctx.src(e.kids[0].start, e.kids[0].end), ctx.src(e.kids[1].start, e.kids[1].end), n.then, None))
        if n.kind == 'switch' and n.body.kind == 'block':
            e = ctx.cond_expr(n)
            if e is not None and permc.is_lvalue_simple(e, t):
                kids = n.body.children
                for k, c in enumerate(kids):
                    if c.kind == 'label' and t[c.start][1] == 'case':
                        regions.append((ctx.src(e.start, e.end), ctx.src(c.start + 1, c.end - 1), n.body, k))
    if not regions: return None
    var, val, blk, k = rng.choice(regions)
    if var.split('[')[0].split('.')[0].split('->')[0] in ctx.locals and rng.random() < 0.5: return None
    # points anywhere in the region (nested blocks too) before any write of var, call or jump
    base = var.split('[')[0].split('.')[0].split('->')[0]
    kids = blk.children
    if k is None:
        r0, r1 = blk.start + 1, blk.end - 1
    else:
        r0 = kids[k].end
        j = k + 1
        while j < len(kids) and kids[j].kind == 'label': j += 1
        while j < len(kids) and kids[j].kind != 'label': j += 1
        r1 = kids[j].start if j < len(kids) else blk.end - 1
    stop = r1
    for i in range(r0, r1):
        w = t[i][1]
        if w in ('break', 'return', 'goto', 'continue') or (t[i][0] == 'id' and i + 1 < len(t) and t[i + 1][1] == '(' and w not in permc.KEYWORDS):
            stop = i; break
        if t[i][0] == 'id' and w == base and written_in(ctx, i, i + 1, {base}):
            stop = i; break
        if (w in permc.ASSIGN or w in ('++', '--')) and _stores_between(ctx, i, i + 1, {base}):
            stop = i; break                     # a store through a pointer may change var
    pts = [q for q, _, _ in ctx.points() if r0 <= q <= stop]
    if not pts: return None
    p = rng.choice(pts)
    if rng.random() < 0.2:
        text = f'if ({var} == {val}) {var} = {val};'
    else:
        text = f'{var} = {val};'
    return Edit([ctx.insert_at(p, text)], f'redundant store {text} @{_line(ctx, p)}')


def _reads_local_index(ctx, e):
    t = ctx.toks
    return any(x.kind == 'index' and x.kids[1].kind == 'atom' and x.kids[1].op == 'id' for x in permc.ewalk(e))


# ---------------------------------------------------------------- D6 statement order

def _rw(ctx, n):
    """(reads, writes, has call, touches memory) identifiers of a simple statement, or None."""
    t = ctx.toks
    e = ctx.stmt_expr(n)
    if e is None: return None
    reads_, writes = set(), set()
    call = False
    for x in permc.ewalk(e):
        if x.kind == 'call': call = True
        if x.kind in ('assign', 'postfix') or (x.kind == 'unary' and x.op in ('++', '--')):
            tgt = x.kids[0]
            base = tgt
            while base.kind in ('member', 'index', 'paren', 'unary') and base.kids: base = base.kids[0]
            if base.kind == 'atom': writes.add(t[base.start][1])
            if tgt.kind in ('unary',) and tgt.op == '*' or (tgt.kind == 'member' and t[tgt.start + 1][1] == '->'):
                writes.add('*mem')
    for x in permc.ewalk(e):
        if x.kind == 'atom' and x.op == 'id': reads_.add(t[x.start][1])
        if (x.kind == 'unary' and x.op == '*') or (x.kind == 'member' and x.op == '->') or x.kind == 'index':
            reads_.add('*mem')
    return reads_, writes, call


def r_stmt_swap(ctx, rng, everywhere=False):
    t = ctx.toks
    pairs = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k in range(len(kids) - 1):
            a, b = kids[k], kids[k + 1]
            if a.kind != 'stmt' or b.kind != 'stmt' or ctx.is_decl(a) or ctx.is_decl(b): continue
            ra, rb = _rw(ctx, a), _rw(ctx, b)
            if ra is None or rb is None: continue
            (r1, w1, c1), (r2, w2, c2) = ra, rb
            if c1 and c2: continue
            glob = lambda s: {x for x in s if x not in ctx.locals or ctx.locals[x].stars or ctx.locals[x].dims} | ({'*mem'} if '*mem' in s else set())
            if c1 and (glob(r2) or glob(w2)): continue
            if c2 and (glob(r1) or glob(w1)): continue
            if w1 & (r2 | w2) or w2 & r1: continue
            if '*mem' in w1 and '*mem' in (r2 | w2) or '*mem' in w2 and '*mem' in r1: continue
            pairs.append((a, b))
    if not pairs: return None
    a, b = rng.choice(pairs)
    sa, sb = ctx.src(a.start, a.end), ctx.src(b.start, b.end)
    return Edit([(a.start, a.end, permc.new(sb, t[a.start][2])), (b.start, b.end, permc.new(sa, t[b.start][2]))],
                f'swap statements {sa[:30]} / {sb[:30]}')


# ---------------------------------------------------------------- D7 arms, nesting, test order

NEG = {'==': '!=', '!=': '==', '<': '>=', '>=': '<', '>': '<=', '<=': '>'}


def negate(ctx, e):
    t = ctx.toks
    if e.kind == 'paren': return negate(ctx, e.kids[0])
    if e.kind == 'bin' and e.op in NEG:
        return f'{ctx.src(e.kids[0].start, e.kids[0].end)} {NEG[e.op]} {ctx.src(e.kids[1].start, e.kids[1].end)}'
    if e.kind == 'unary' and e.op == '!':
        return ctx.src(e.kids[0].start, e.kids[0].end)
    return f'!({ctx.src(e.start, e.end)})'


def _arm(ctx, n, then_position=False):
    """An arm's source; braced when it is an if without else going before an else."""
    s = ctx.src(n.start, n.end)
    if n.kind == 'block': return s
    if then_position and any(m.kind == 'if' and m.els is None for m in permc.walk(n)): return '{ ' + s + ' }'
    return s


def r_if_swap(ctx, rng, everywhere=False):
    t = ctx.toks
    ifs = [n for n in ctx.nodes if n.kind == 'if' and n.els is not None and ctx.cond_expr(n) is not None]
    if not ifs: return None
    n = rng.choice(ifs)
    e = ctx.cond_expr(n)
    new = f'if ({negate(ctx, e)}) {_arm(ctx, n.els, True)} else {_arm(ctx, n.then)}'
    return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'swap arms of the if at line {_line(ctx, n.start)}')


def r_nest(ctx, rng, everywhere=False):
    """`if (a && b) S` against `if (a) { if (b) S }` (no else)."""
    t = ctx.toks
    fwd = [n for n in ctx.nodes if n.kind == 'if' and n.els is None and (ctx.cond_expr(n) is not None)
           and ctx.cond_expr(n).kind == 'bin' and ctx.cond_expr(n).op == '&&']
    inv = [n for n in ctx.nodes if n.kind == 'if' and n.els is None and n.then.kind == 'block'
           and len(n.then.children) == 1 and n.then.children[0].kind == 'if' and n.then.children[0].els is None]
    if not fwd and not inv: return None
    if fwd and (not inv or rng.random() < 0.5):
        n = rng.choice(fwd); e = ctx.cond_expr(n)
        a, b = e.kids
        new = f'if ({_strip(ctx, a)}) {{ if ({_strip(ctx, b)}) {_arm(ctx, n.then)} }}'
        return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'nest the && at line {_line(ctx, n.start)}')
    n = rng.choice(inv); inner = n.then.children[0]
    ea, eb = ctx.cond_expr(n), ctx.cond_expr(inner)
    if ea is None or eb is None: return None
    new = f'if ({optext(ctx, ea, "&&")} && {optext(ctx, eb, "&&")}) {_arm(ctx, inner.then)}'
    return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'join nested ifs at line {_line(ctx, n.start)} with &&')


def _strip(ctx, e):
    """Source of e without one layer of redundant parentheses."""
    if e.kind == 'paren': e = e.kids[0]
    return ctx.src(e.start, e.end)


def r_test_order(ctx, rng, everywhere=False):
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        e = ctx.cond_expr(n)
        if e is None: continue
        for x in permc.ewalk(e):
            if x.kind == 'bin' and x.op in ('&&', '||'):
                a, b = x.kids
                if any(permc.has_side_effects(k, t) for k in (a, b)): continue
                if any(y.kind in ('member', 'index', 'unary') and (y.kind != 'member' or y.op == '->') and (y.kind != 'unary' or y.op == '*')
                       for k in (a, b) for y in permc.ewalk(k) if y.kind != 'member' or y.op == '->'):
                    continue
                cands.append(x)
    if not cands: return None
    x = rng.choice(cands)
    a, b = x.kids
    new = f'{optext(ctx, b, x.op)} {x.op} {optext(ctx, a, x.op)}'
    return Edit([(x.start, x.end, permc.new(wrap(ctx, x, new), t[x.start][2]))], f'test order {ctx.src(x.start, x.end)[:50]}')


# ---------------------------------------------------------------- D8 if chain -> switch, D9 goto

def _has_loop_break(ctx, n):
    """A break or continue in n that would bind to a loop or switch outside n."""
    for m in permc.walk(n):
        if m.kind == 'stmt' and ctx.toks[m.start][1] in ('break', 'continue'):
            k = m.parent
            ok = False
            while k is not None and k is not n.parent:
                if k.kind in ('for', 'while', 'do') or (k.kind == 'switch' and ctx.toks[m.start][1] == 'break'): ok = True; break
                if k is n: break
                k = k.parent
            if not ok: return True
    return False


def r_if_switch(ctx, rng, everywhere=False):
    t = ctx.toks
    chains = []
    for n in ctx.nodes:
        if n.kind != 'if' or (n.parent is not None and n.parent.kind == 'if' and n.parent.els is n): continue
        arms, cur, var = [], n, None
        while cur is not None and cur.kind == 'if':
            e = ctx.cond_expr(cur)
            if e is None or e.kind != 'bin' or e.op != '==': break
            v = ctx.src(e.kids[0].start, e.kids[0].end)
            k = permc.const_value(e.kids[1], t)
            if k is None or not permc.is_lvalue_simple(e.kids[0], t): break
            if var is None: var = v
            elif v != var: break
            arms.append((ctx.src(e.kids[1].start, e.kids[1].end), cur.then))
            cur = cur.els
        if len(arms) >= 2 and len({a for a, _ in arms}) == len(arms):
            rest = cur
            if any(_has_loop_break(ctx, b) for _, b in arms) or (rest is not None and _has_loop_break(ctx, rest)): continue
            chains.append((n, var, arms, rest))
    if not chains: return None
    n, var, arms, rest = rng.choice(chains)
    body = ' '.join(f'case {k}: {_arm(ctx, b)} break;' for k, b in arms)
    if rest is not None: body += f' default: {_arm(ctx, rest)} break;'
    new = f'switch ({var}) {{ {body} }}'
    return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'if chain on {var} -> switch at line {_line(ctx, n.start)}')


def r_goto_or(ctx, rng, everywhere=False):
    """D9: `if (a) goto L; if (b) goto L; if (c) { L: S } else E` against `if (a || b || c) S else E`."""
    t = ctx.toks
    for blk in rng.sample(permc.blocks(ctx.root), len(permc.blocks(ctx.root))):
        kids = blk.children
        for k, c in enumerate(kids):
            if c.kind != 'if' or c.then.kind != 'block' or not c.then.children: continue
            lab = c.then.children[0]
            if lab.kind != 'label' or lab.extra != 'goto': continue
            L = t[lab.start][1]
            conds = []
            j = k - 1
            while j >= 0 and kids[j].kind == 'if' and kids[j].els is None:
                th = kids[j].then
                s = th if th.kind == 'stmt' else (th.children[0] if th.kind == 'block' and len(th.children) == 1 else None)
                if s is None or s.kind != 'stmt' or t[s.start][1] != 'goto' or t[s.start + 1][1] != L: break
                ce = ctx.cond_expr(kids[j])
                if ce is None: break
                conds.insert(0, optext(ctx, ce, '||')); j -= 1
            if not conds: continue
            if sum(1 for i in range(ctx.lb, ctx.rb) if t[i][1] == L) != len(conds) + 1: continue
            ce = ctx.cond_expr(c)
            if ce is None: continue
            allc = conds + [optext(ctx, ce, '||')]
            body = c.then.children[1:]
            inner = ''.join(x[2] + x[1] for x in t[lab.end:c.then.end - 1])
            if len(body) == 1 and body[0].kind == 'stmt':
                new = 'if (' + ' || '.join(allc) + ') ' + ctx.src(body[0].start, body[0].end)
            else:
                new = 'if (' + ' || '.join(allc) + ') {' + inner + ' }'
            if c.els is not None: new += ' else ' + ctx.src(c.els.start, c.els.end)
            return Edit([(kids[j + 1].start, c.end, permc.new(new, t[kids[j + 1].start][2]))], f'goto chain to {L} -> ||')
    # forward: an || test becomes a goto chain
    cands = [n for n in ctx.nodes if n.kind == 'if' and ctx.cond_expr(n) is not None and ctx.cond_expr(n).kind == 'bin'
             and ctx.cond_expr(n).op == '||' and n.parent is not None and n.parent.kind == 'block']
    if not cands: return None
    n = rng.choice(cands)
    ops = []

    def flat(e):
        if e.kind == 'bin' and e.op == '||': flat(e.kids[0]); flat(e.kids[1])
        else: ops.append(_strip(ctx, e))
    flat(ctx.cond_expr(n))
    L = ctx.fresh('pmt_L')
    head = ' '.join(f'if ({o}) goto {L};' for o in ops[:-1])
    then = ctx.src(n.then.start, n.then.end)
    then_in = then[1:-1] if n.then.kind == 'block' else then
    new = f'{head} if ({ops[-1]}) {{ {L}: {then_in} }}'
    if n.els is not None: new += ' else ' + ctx.src(n.els.start, n.els.end)
    return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'|| -> goto chain at line {_line(ctx, n.start)}')


# ---------------------------------------------------------------- D10 .. D14

def r_chain_store(ctx, rng, everywhere=False):
    t = ctx.toks
    fwd, inv = [], []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k in range(len(kids)):
            e = ctx.stmt_expr(kids[k])
            if e is None or e.kind != 'assign' or e.op != '=': continue
            if e.kids[1].kind == 'assign' and e.kids[1].op == '=' and permc.const_value(e.kids[1].kids[1], t) is not None:
                inv.append((kids[k], e))
            if k + 1 < len(kids):
                f = ctx.stmt_expr(kids[k + 1])
                if f is None or f.kind != 'assign' or f.op != '=': continue
                v1, v2 = permc.const_value(e.kids[1], t), permc.const_value(f.kids[1], t)
                if v1 is None or v1 != v2 or not 0 <= v1 < 128: continue
                if not (permc.is_lvalue_simple(e.kids[0], t) and permc.is_lvalue_simple(f.kids[0], t)): continue
                if ctx.src(e.kids[0].start, e.kids[0].end) == ctx.src(f.kids[0].start, f.kids[0].end): continue
                fwd.append((kids[k], kids[k + 1], e, f))
    if not fwd and not inv: return None
    if fwd and (not inv or rng.random() < 0.6):
        a, b, e, f = rng.choice(fwd)
        x, y = ctx.src(e.kids[0].start, e.kids[0].end), ctx.src(f.kids[0].start, f.kids[0].end)
        k = ctx.src(e.kids[1].start, e.kids[1].end)
        new = f'{x} = {y} = {k};' if rng.random() < 0.5 else f'{y} = {x} = {k};'
        return Edit([(a.start, b.end, permc.new(new, t[a.start][2]))], f'chain store {new}')
    n, e = rng.choice(inv)
    x = ctx.src(e.kids[0].start, e.kids[0].end); y = ctx.src(e.kids[1].kids[0].start, e.kids[1].kids[0].end)
    k = ctx.src(e.kids[1].kids[1].start, e.kids[1].kids[1].end)
    new = f'{y} = {k}; {x} = {k};'
    return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'split chained store {ctx.src(n.start, n.end)[:40]}')


def r_postinc_index(ctx, rng, everywhere=False):
    """D12: `a[n] = E; n++;` against `a[n++] = E;`; and the increment statement spellings."""
    t = ctx.toks
    fwd, inv, incs = [], [], []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, c in enumerate(kids):
            e = ctx.stmt_expr(c)
            if e is None: continue
            if e.kind == 'postfix' or (e.kind == 'unary' and e.op in ('++', '--')) or \
                    (e.kind == 'assign' and e.op in ('+=', '-=') and permc.const_value(e.kids[1], t) == 1):
                incs.append((c, e))
            if e.kind == 'assign' and e.kids[0].kind == 'index':
                idx = e.kids[0].kids[1]
                if idx.kind == 'postfix' and idx.op == '++' and idx.kids[0].kind == 'atom':
                    inv.append((c, e, idx))
                elif idx.kind == 'atom' and idx.op == 'id' and k + 1 < len(kids):
                    f = ctx.stmt_expr(kids[k + 1])
                    nm = t[idx.start][1]
                    if f is not None and _is_inc_of(ctx, f, nm) and nm not in permc.idents(e.kids[1], t) \
                            and nm not in permc.idents(e.kids[0].kids[0], t):
                        fwd.append((c, kids[k + 1], idx))
    r = rng.random()
    if fwd and (r < 0.4 or not (inv or incs)):
        c, d, idx = rng.choice(fwd)
        nm = t[idx.start][1]
        s = _replace_spans(ctx, c.start, c.end, [(idx.start, idx.end, nm + '++')])
        return Edit([(c.start, d.end, permc.new(s, t[c.start][2]))], f'{nm}++ into the index')
    if inv and (r < 0.7 or not incs):
        c, e, idx = rng.choice(inv)
        nm = t[idx.kids[0].start][1]
        s = _replace_spans(ctx, c.start, c.end, [(idx.start, idx.end, nm)])
        return Edit([(c.start, c.end, permc.new(s + f' {nm}++;', t[c.start][2]))], f'{nm}++ out of the index')
    if not incs: return None
    c, e = rng.choice(incs)
    target = e.kids[0]
    v = ctx.src(target.start, target.end)
    op = '++' if (e.op in ('++', '+=')) else '--'
    forms = [f'{v}{op};', f'{op}{v};', f'{v} {op[0]}= 1;', f'{v} = {v} {op[0]} 1;']
    cur = ctx.src(c.start, c.end)
    forms = [f for f in forms if ' '.join(f.split()) != ' '.join(cur.split())]
    new = rng.choice(forms)
    return Edit([(c.start, c.end, permc.new(new, t[c.start][2]))], f'increment spelling {new}')


def _is_inc_of(ctx, e, nm):
    t = ctx.toks
    if e.kind in ('postfix', 'unary') and e.op == '++' and e.kids[0].kind == 'atom' and t[e.kids[0].start][1] == nm: return True
    if e.kind == 'assign' and e.op == '+=' and e.kids[0].kind == 'atom' and t[e.kids[0].start][1] == nm and permc.const_value(e.kids[1], t) == 1:
        return True
    return False


def r_preinc_cond(ctx, rng, everywhere=False):
    """D13: `n++; if (n > K)` against `if (++n > K)`."""
    t = ctx.toks
    fwd, inv = [], []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, c in enumerate(kids):
            if c.kind == 'if':
                e = ctx.cond_expr(c)
                if e is None: continue
                for x in permc.ewalk(e):
                    if x.kind == 'unary' and x.op in ('++', '--') and x.kids[0].kind == 'atom':
                        inv.append((c, x))
            if k + 1 < len(kids) and kids[k + 1].kind == 'if':
                e = ctx.stmt_expr(c)
                if e is None or not (e.kind in ('postfix', 'unary') and e.op in ('++', '--')) or e.kids[0].kind != 'atom': continue
                nm = t[e.kids[0].start][1]
                ce = ctx.cond_expr(kids[k + 1])
                if ce is None: continue
                occ = [x for x in permc.ewalk(ce) if x.kind == 'atom' and t[x.start][1] == nm]
                if len(occ) == 1 and ce.kind == 'bin' and ce.kids[0] is occ[0]:
                    fwd.append((c, kids[k + 1], occ[0], e.op))
    if fwd and (not inv or rng.random() < 0.5):
        c, d, occ, op = rng.choice(fwd)
        s = _replace_spans(ctx, d.start, d.end, [(occ.start, occ.end, op + t[occ.start][1])])
        return Edit([(c.start, d.end, permc.new(s, t[c.start][2]))], f'{op} into the test at line {_line(ctx, d.start)}')
    if not inv: return None
    c, x = rng.choice(inv)
    nm = t[x.kids[0].start][1]
    s = _replace_spans(ctx, c.start, c.end, [(x.start, x.end, nm)])
    return Edit([(c.start, c.end, permc.new(f'{nm}{x.op}; ' + s, t[c.start][2]))], f'{x.op} out of the test at line {_line(ctx, c.start)}')


def r_range_test(ctx, rng, everywhere=False):
    """D14: `c >= A && c <= B` against `c - A_U <= B - A` (and the || complement)."""
    t = ctx.toks
    fwd, inv = [], []
    for n in ctx.nodes:
        e = ctx.cond_expr(n)
        if e is None: continue
        for x in permc.ewalk(e):
            if x.kind == 'bin' and x.op in ('&&', '||'):
                a, b = x.kids
                if a.kind == 'bin' and b.kind == 'bin':
                    want = ('>=', '<=') if x.op == '&&' else ('<', '>')
                    if (a.op, b.op) == want and ctx.src(a.kids[0].start, a.kids[0].end) == ctx.src(b.kids[0].start, b.kids[0].end) \
                            and not permc.has_side_effects(a.kids[0], t):
                        lo, hi = permc.const_value(a.kids[1], t), permc.const_value(b.kids[1], t)
                        if lo is not None and hi is not None and 0 <= lo < hi: fwd.append((x, a, lo, hi))
            if x.kind == 'bin' and x.op in ('<=', '>') and x.kids[0].kind == 'bin' and x.kids[0].op == '-':
                k = x.kids[0].kids[1]
                if t[k.start][0] == 'num' and t[k.start][1].upper().endswith('U') and permc.const_value(x.kids[1], t) is not None:
                    inv.append(x)
    if fwd and (not inv or rng.random() < 0.6):
        x, a, lo, hi = rng.choice(fwd)
        v = paren(ctx, a.kids[0])
        lo_s = t[a.kids[1].start][1].rstrip('uU')
        op = '<=' if x.op == '&&' else '>'
        new = f'{v} - {lo_s}U {op} {hi - lo}'
        return Edit([(x.start, x.end, permc.new(wrap(ctx, x, new), t[x.start][2]))], f'range test {new}')
    if not inv: return None
    x = rng.choice(inv)
    v = ctx.src(x.kids[0].kids[0].start, x.kids[0].kids[0].end)
    lo = permc.const_value(x.kids[0].kids[1], t) if False else int(t[x.kids[0].kids[1].start][1].rstrip('uU'), 0)
    span = permc.const_value(x.kids[1], t)
    lo_s = t[x.kids[0].kids[1].start][1].rstrip('uU')
    hi = lo + span
    hi_s = hex(hi) if lo_s.lower().startswith('0x') else str(hi)
    new = f'{v} >= {lo_s} && {v} <= {hi_s}' if x.op == '<=' else f'{v} < {lo_s} || {v} > {hi_s}'
    return Edit([(x.start, x.end, permc.new(wrap(ctx, x, new), t[x.start][2]))], f'range test as two compares')


def r_bit_test(ctx, rng, everywhere=False):
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        e = ctx.cond_expr(n)
        if e is None: continue
        for x in permc.ewalk(e):
            if x.kind == 'bin' and x.op in ('==', '!=') and x.kids[0].kind == 'paren' and x.kids[0].kids[0].kind == 'bin' \
                    and x.kids[0].kids[0].op == '&':
                m = permc.const_value(x.kids[0].kids[0].kids[1], t)
                v = permc.const_value(x.kids[1], t)
                if m and m & (m - 1) == 0 and v in (0, m): cands.append((x, m, v))
    if not cands: return None
    x, m, v = rng.choice(cands)
    inner = ctx.src(x.kids[0].start, x.kids[0].end)
    ms = ctx.src(x.kids[0].kids[0].kids[1].start, x.kids[0].kids[0].kids[1].end)
    true_when_set = (x.op == '==' and v == m) or (x.op == '!=' and v == 0)
    forms = [f'{inner} == {ms}', f'{inner} != 0'] if true_when_set else [f'{inner} != {ms}', f'{inner} == 0']
    cur = ' '.join(ctx.src(x.start, x.end).split())
    forms = [f for f in forms if ' '.join(f.split()) != cur]
    new = rng.choice(forms)
    return Edit([(x.start, x.end, permc.new(new, t[x.start][2]))], f'bit test {new}')


# ---------------------------------------------------------------- D18 select, D22 shift, D23 casts

def r_ternary(ctx, rng, everywhere=False):
    t = ctx.toks
    fwd, inv = [], []
    for n in ctx.nodes:
        if n.kind == 'if' and n.els is not None:
            a = _single_assign(ctx, n.then); b = _single_assign(ctx, n.els)
            if a and b and a[0] == b[0]: fwd.append((n, a, b))
        if n.kind == 'stmt':
            e = ctx.stmt_expr(n)
            if e is not None and e.kind == 'assign' and e.op == '=' and e.kids[1].kind == 'cond' and permc.is_lvalue_simple(e.kids[0], t):
                inv.append((n, e))
    if fwd and (not inv or rng.random() < 0.5):
        n, a, b = rng.choice(fwd)
        new = f'{a[0]} = {ctx.src(n.cond[0] + 1, n.cond[1])} ? {a[1]} : {b[1]};'
        return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'if/else -> ?: at line {_line(ctx, n.start)}')
    if not inv: return None
    n, e = rng.choice(inv)
    c, x, y = e.kids[1].kids
    lv = ctx.src(e.kids[0].start, e.kids[0].end)
    new = f'if ({ctx.src(c.start, c.end)}) {lv} = {ctx.src(x.start, x.end)}; else {lv} = {ctx.src(y.start, y.end)};'
    return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'?: -> if/else at line {_line(ctx, n.start)}')


def _single_assign(ctx, n):
    if n.kind == 'block':
        if len(n.children) != 1: return None
        n = n.children[0]
    e = ctx.stmt_expr(n)
    if e is None or e.kind != 'assign' or e.op != '=' or not permc.is_lvalue_simple(e.kids[0], ctx.toks): return None
    return ctx.src(e.kids[0].start, e.kids[0].end), ctx.src(e.kids[1].start, e.kids[1].end)


def r_shift_mul(ctx, rng, everywhere=False):
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        es = [ctx.stmt_expr(n), ctx.cond_expr(n)]
        for e in es:
            if e is None: continue
            for x in permc.ewalk(e):
                if x.kind == 'bin' and x.op == '<<':
                    k = permc.const_value(x.kids[1], t)
                    if k is not None and 0 < k < 31: cands.append((x, 'mul', k))
                if x.kind == 'bin' and x.op == '*':
                    k = permc.const_value(x.kids[1], t)
                    if k and k > 1 and k & (k - 1) == 0: cands.append((x, 'shl', k.bit_length() - 1))
                if x.kind == 'assign' and x.op in ('<<=', '*='):
                    k = permc.const_value(x.kids[1], t)
                    if k is not None and (x.op == '<<=' and 0 < k < 31 or x.op == '*=' and k > 1 and k & (k - 1) == 0):
                        cands.append((x, 'asg', k))
    if not cands: return None
    x, kind, k = rng.choice(cands)
    a = paren(ctx, x.kids[0])
    if kind == 'mul': new = f'{a} * {1 << k}'
    elif kind == 'shl': new = f'{a} << {k}'
    else:
        new = f'{ctx.src(x.kids[0].start, x.kids[0].end)} *= {1 << k}' if x.op == '<<=' else f'{ctx.src(x.kids[0].start, x.kids[0].end)} <<= {k.bit_length() - 1}'
    if kind != 'asg': new = wrap(ctx, x, new)
    return Edit([(x.start, x.end, permc.new(new, t[x.start][2]))], f'shift/multiply {ctx.src(x.start, x.end)[:30]} -> {new}')


def r_cast_assign(ctx, rng, everywhere=False):
    """D23: a store to an integer variable of known type T gets (T) on its value, or loses an
    outer (T) it has; or a & 0xff / 0xffff mask for an unsigned char / short target."""
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        e = ctx.stmt_expr(n)
        if e is None or e.kind != 'assign' or e.op != '=': continue
        ty = ctx.type_of(e.kids[0])
        if not is_int_type(ctx, ty): continue
        cands.append((e, ty))
    if not cands: return None
    e, ty = rng.choice(cands)
    rhs = e.kids[1]
    if rhs.kind == 'cast':
        inner = rhs.kids[0]
        if inner.kind == 'paren': inner = inner.kids[0]
        new = ctx.src(inner.start, inner.end)
        return Edit([(rhs.start, rhs.end, permc.new(new, t[rhs.start][2]))], f'drop cast on {ctx.src(e.start, e.end)[:40]}')
    if rhs.kind == 'atom': return None
    ty = resolve_type(ctx, ty)
    new = f'({ty})({ctx.src(rhs.start, rhs.end)})'
    return Edit([(rhs.start, rhs.end, permc.new(new, t[rhs.start][2]))], f'cast ({ty}) on {ctx.src(e.start, e.end)[:40]}')


def r_compound(ctx, rng, everywhere=False):
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        e = ctx.stmt_expr(n)
        if e is None or e.kind != 'assign': continue
        if e.op == '=' and e.kids[1].kind == 'bin' and e.kids[1].op in ('+', '-', '*', '|', '&', '^', '<<', '>>') \
                and ctx.src(e.kids[1].kids[0].start, e.kids[1].kids[0].end) == ctx.src(e.kids[0].start, e.kids[0].end) \
                and permc.is_lvalue_simple(e.kids[0], t):
            cands.append((e, 'fold'))
        elif e.op in ('+=', '-=', '*=', '|=', '&=', '^=', '<<=', '>>=') and permc.is_lvalue_simple(e.kids[0], t):
            cands.append((e, 'expand'))
    if not cands: return None
    e, kind = rng.choice(cands)
    lv = ctx.src(e.kids[0].start, e.kids[0].end)
    if kind == 'fold':
        new = f'{lv} {e.kids[1].op}= {ctx.src(e.kids[1].kids[1].start, e.kids[1].kids[1].end)}'
    else:
        new = f'{lv} = {lv} {e.op[:-1]} {paren(ctx, e.kids[1])}'
    return Edit([(e.start, e.end, permc.new(new, t[e.start][2]))], f'compound {new[:40]}')


FLIP = {'<': '>', '>': '<', '<=': '>=', '>=': '<=', '==': '==', '!=': '!='}


def r_operand_order(ctx, rng, everywhere=False):
    """D20: a comparison or commutative operator with its operands swapped."""
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        for e in (ctx.stmt_expr(n), ctx.cond_expr(n)):
            if e is None: continue
            for x in permc.ewalk(e):
                if x.kind == 'bin' and x.op in tuple(FLIP) + ('+', '*', '&', '|', '^') and not any(permc.has_side_effects(k, t) for k in x.kids):
                    cands.append(x)
    if not cands: return None
    x = rng.choice(cands)
    a, b = x.kids
    op = FLIP.get(x.op, x.op)
    new = f'{optext(ctx, b, x.op)} {op} {optext(ctx, a, x.op)}'
    if x.op in ('+', '*', '&', '|', '^'): new = wrap(ctx, x, new)
    return Edit([(x.start, x.end, permc.new(new, t[x.start][2]))], f'operand order {ctx.src(x.start, x.end)[:40]}')


# ---------------------------------------------------------------- D29 case labels

def _groups(ctx, sw):
    """[(first child index, last child index (exclusive), labels [node], ends with a jump)]."""
    kids = sw.body.children
    out = []
    k = 0
    while k < len(kids):
        if kids[k].kind != 'label': k += 1; continue
        a = k
        labels = []
        while k < len(kids) and kids[k].kind == 'label': labels.append(kids[k]); k += 1
        while k < len(kids) and kids[k].kind != 'label': k += 1
        body = kids[a + len(labels):k]
        last = body[-1] if body else None
        jump = last is not None and last.kind == 'stmt' and ctx.toks[last.start][1] in ('break', 'return', 'goto', 'continue')
        out.append((a, k, labels, jump, body))
    return out


def r_case_edit(ctx, rng, everywhere=False):
    t = ctx.toks
    sws = [n for n in ctx.nodes if n.kind == 'switch' and n.body.kind == 'block']
    if not sws: return None
    sw = rng.choice(sws)
    gs = _groups(ctx, sw)
    if not gs: return None
    kids = sw.body.children
    has_default = any(t[l.start][1] == 'default' for g in gs for l in g[2])
    default_empty = any(t[l.start][1] == 'default' and len(g[4]) == 1 and t[g[4][0].start][1] == 'break' for g in gs for l in g[2])
    values = []
    for g in gs:
        for l in g[2]:
            if t[l.start][1] == 'case':
                e = ctx.expr(l.start + 1, l.end - 1)
                v = permc.const_value(e, t) if e is not None else None
                if v is not None: values.append(v)
    empty = [g for g in gs if len(g[4]) == 1 and t[g[4][0].start][1] == 'break' and all(t[l.start][1] == 'case' for l in g[2])]
    r = rng.random()
    if r < 0.35 and (not has_default or default_empty) and values:
        # a new empty label beside an existing empty group (or a new empty group)
        near = sorted({v + d for v in values for d in (-2, -1, 1, 2)} - set(values))
        near = [v for v in near if v >= 0]
        if not near: return None
        v = rng.choice(near)
        lab = hex(v) if v > 9 else str(v)
        if empty and rng.random() < 0.7:
            g = rng.choice(empty)
            at = g[2][0].start
            return Edit([(at, at + 1, permc.new(f'case {lab}: ', t[at][2]) + [Tok(t[at][0], t[at][1], ' ')])], f'empty case {lab} into a group')
        at = kids[gs[-1][1] - 1].end if gs[-1][3] else None
        if at is None: return None
        return Edit([ctx.insert_at(at, f'case {lab}: break;') if at < sw.body.end - 1 else ctx.insert_at(sw.body.end - 1, f'case {lab}: break;')],
                    f'new empty case {lab}')
    if r < 0.55 and empty and (not has_default or default_empty):
        g = rng.choice(empty)
        if len(g[2]) > 1:
            l = rng.choice(g[2])
            return Edit([(l.start, l.end, [])], f'remove empty case {ctx.src(l.start, l.end)}')
        return Edit([(kids[g[0]].start, kids[g[1] - 1].end, [])], f'remove empty group {ctx.src(kids[g[0]].start, kids[g[0]].end)}')
    # move a whole group that ends in a jump and is not fallen into
    movable = []
    for k, g in enumerate(gs):
        if not g[3]: continue
        if k > 0 and not gs[k - 1][3]: continue
        movable.append(k)
    if len(movable) < 2: return None
    k = rng.choice(movable)
    dests = [j for j in range(len(gs) + 1) if j not in (k, k + 1) and (j == 0 or gs[j - 1][3])]
    if not dests: return None
    j = rng.choice(dests)
    g = gs[k]
    text = ''.join(x[2] + x[1] for x in t[kids[g[0]].start:kids[g[1] - 1].end])
    at = kids[gs[j][0]].start if j < len(gs) else sw.body.end - 1
    new = permc.new(text.lstrip(), t[at][2]) + [Tok(t[at][0], t[at][1], t[kids[g[0]].start][2])]
    return Edit([(kids[g[0]].start, kids[g[1] - 1].end, []), (at, at + 1, new)],
                f'move case group {ctx.src(kids[g[0]].start, kids[g[0]].end)} before group {j}')


# ---------------------------------------------------------------- A11, A6, D5, generic passes

def r_unprototyped(ctx, rng, everywhere=False):
    t = ctx.toks
    lp = next(i for i in range(ctx.start, ctx.lb) if t[i][1] == '(')
    rp = ctx.pair[lp]
    if rp == lp + 2 and t[lp + 1][1] == 'void':
        return Edit([(lp + 1, lp + 2, [])], f'{ctx.func}() unprototyped')
    if rp == lp + 1:
        return Edit([(rp, rp + 1, permc.new('void)', '') )], f'{ctx.func}(void)')
    return None


def r_sized_extern(ctx, rng, everywhere=False):
    """A6: `extern T x[];` gets its size from the map (needs ctx.array_sizes {name: N})."""
    sizes = getattr(ctx, 'array_sizes', None) or {}
    t = ctx.toks
    cands = []
    for i in range(0, ctx.start):
        if t[i][0] == 'id' and t[i][1] in sizes and t[i + 1][1] == '[' and t[i + 2][1] == ']':
            # file scope only: count braces
            cands.append(i)
    used = [i for i in cands if t[i][1] in ctx.ids]
    if not used: return None
    chosen = used if everywhere else [rng.choice(used)]
    spans = [(i + 1, i + 3, permc.new(f'[{sizes[t[i][1]]}]', t[i + 1][2])) for i in chosen]
    return Edit(spans, 'size extern ' + ', '.join(f'{t[i][1]}[{sizes[t[i][1]]}]' for i in chosen))


def r_volatile_read(ctx, rng, everywhere=False):
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        e = ctx.stmt_expr(n)
        if e is None or e.kind != 'assign': continue
        for x in reads(ctx, e.kids[1]):
            if x.kind == 'atom' and is_int_type(ctx, ctx.type_of(x)): cands.append(x)
    if not cands: return None
    x = rng.choice(cands)
    nm = t[x.start][1]
    new = f'*(volatile {resolve_type(ctx, ctx.type_of(x))} *)&{nm}'
    return Edit([(x.start, x.end, permc.new(f'({new})', t[x.start][2]))], f'volatile read of {nm}')


def r_cond_explicit(ctx, rng, everywhere=False):
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        if n.kind in ('if', 'while') and n.cond:
            e = ctx.cond_expr(n)
            if e is None: continue
            if e.kind == 'bin' and e.op == '!=' and permc.const_value(e.kids[1], t) == 0: cands.append((e, 'implicit'))
            elif e.kind in ('atom', 'index', 'member', 'paren'): cands.append((e, 'explicit'))
    if not cands: return None
    e, k = rng.choice(cands)
    new = ctx.src(e.kids[0].start, e.kids[0].end) if k == 'implicit' else f'{ctx.src(e.start, e.end)} != 0'
    return Edit([(e.start, e.end, permc.new(new, t[e.start][2]))], f'condition {new[:40]}')


def r_inequality(ctx, rng, everywhere=False):
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        e = ctx.cond_expr(n)
        if e is None: continue
        for x in permc.ewalk(e):
            if x.kind == 'bin' and x.op in ('<', '<=', '>', '>=') and permc.const_value(x.kids[1], t) is not None and t[x.kids[1].start][0] == 'num':
                cands.append(x)
    if not cands: return None
    x = rng.choice(cands)
    k = permc.const_value(x.kids[1], t)
    if x.op in ('<', '>='): op, k2 = ('<=' if x.op == '<' else '>'), k - 1
    else: op, k2 = ('<' if x.op == '<=' else '>='), k + 1
    if k2 < 0: return None
    ks = hex(k2) if t[x.kids[1].start][1].lower().startswith('0x') else str(k2)
    new = f'{ctx.src(x.kids[0].start, x.kids[0].end)} {op} {ks}'
    return Edit([(x.start, x.end, permc.new(new, t[x.start][2]))], f'inequality {new[:40]}')


def r_ins_block(ctx, rng, everywhere=False):
    t = ctx.toks
    cands = [n for n in ctx.nodes if n.kind == 'stmt' and not ctx.is_decl(n) and n.parent is not None and n.parent.kind == 'block']
    if not cands: return None
    n = rng.choice(cands)
    return Edit([(n.start, n.end, permc.new('{ ' + ctx.src(n.start, n.end) + ' }', t[n.start][2]))], f'braces around line {_line(ctx, n.start)}')


# ---------------------------------------------------------------- structural and compound moves
#
# The misses of the first regression were rewrites made of several edits that only pay off
# together: a temporary used by a store and the call after it; a group of locals inlined at
# every use plus a dead probe removed plus sized externs plus a flat else-if; a pointer local
# with arithmetic replaced by the fields it reaches, goto chains turned into one boolean test,
# and every local removed. The moves below are those kinds, each general, plus compound moves
# that apply a whole normalising pass in one step (the edits a person makes when they rewrite a
# fitted source plainly).

def remake(ctx, toks):
    """A Ctx of toks with the same file facts (types, array sizes, struct layouts)."""
    c = Ctx(toks, ctx.tail, ctx.func, ctx.types)
    for k in ('array_sizes', 'layouts'):
        if hasattr(ctx, k): setattr(c, k, getattr(ctx, k))
    return c


def diff_spans(old, new):
    """Span edits turning token list old into new: the changed middle, split into hunks with
    difflib when it is long (a compound move that touches a declaration and the function)."""
    if old == new: return []
    p, n = 0, min(len(old), len(new))
    while p < n and old[p] == new[p]: p += 1
    s = 0
    while s < n - p and old[len(old) - 1 - s] == new[len(new) - 1 - s]: s += 1
    return [(p, len(old) - s, list(new[p:len(new) - s]))]


def _edit_of(ctx, new, desc, meaning='same', parts=None):
    """One edit from ctx's tokens to new (the changed middle); a compound move's steps go in
    parts, so the move is kept as small patches rather than one span over the file."""
    sp = diff_spans(ctx.toks, new)
    return Edit(sp, desc, meaning=meaning, parts=parts) if sp else None


def _chain(ctx, rng, fns, stop_on_none=False):
    """Apply fns in turn, each on a fresh parse of the result of the last; (toks, descs, meaning).
    The steps (tokens before, spans) are collected in ctx._parts for _edit_of."""
    toks, descs, c, meaning = ctx.toks, [], ctx, 'same'
    parts = []
    for fn in fns:
        try:
            ed = fn(c, rng)
        except Exception:
            ed = None
        if ed is None or not ed.spans:
            if stop_on_none: break
            continue
        try:
            nt = apply(toks, ed.spans)
            c2 = remake(c, nt)
        except Exception:
            if stop_on_none: break
            continue
        parts += ed.parts if ed.parts else [(toks, ed.spans)]
        toks, c = nt, c2
        descs.append(ed.desc)
        if ed.meaning != 'same': meaning = ed.meaning
    ctx._parts = parts
    return toks, descs, meaning


def _fix(fn, limit=24):
    """fn applied until it finds no site, as one edit."""
    def g(ctx, rng):
        toks, descs, meaning = _chain(ctx, rng, [fn] * limit, stop_on_none=True)
        if not descs: return None
        return _edit_of(ctx, toks, '; '.join(descs)[:300], meaning, parts=getattr(ctx, '_parts', None))
    return g


def _macros(ctx):
    """{name: body} of the function-like macros defined in the file."""
    m = getattr(ctx, '_macro_cache', None)
    if m is None:
        m = {}
        for t in ctx.toks:
            if t[0] == 'pp':
                mm = re.match(r'#\s*define\s+(\w+)\(([^)]*)\)(.*)', t[1].replace('\\\n', ' '), re.S)
                if mm: m[mm.group(1)] = mm.group(3)
        ctx._macro_cache = m
    return m


def _pure_macro(ctx, name):
    body = _macros(ctx).get(name)
    if body is None: return False
    return not any(w not in permc.KEYWORDS and w not in _macros(ctx) for w in re.findall(r'\b([A-Za-z_]\w*)\s*\(', body)
                   if not ctx.types.is_type_word(w))


# ---- dead constructs (counted for the penalty, removed by remove_dead)

def _declarators(ctx, n):
    """(index after the type words, [(a, b, name)]) of a declaration statement."""
    t = ctx.toks
    k = n.start
    while k < n.end and (ctx.types.is_type_word(t[k][1]) or t[k][1] in ('struct', 'union', 'enum')):
        if t[k][1] in ('struct', 'union', 'enum'): k += 1
        k += 1
    if k == n.start and t[k][0] == 'id': k += 1
    out, a, depth = [], k, 0
    for i in range(k, n.end):
        w = t[i][1]
        if w in '([{': depth += 1
        elif w in ')]}': depth -= 1
        if (w == ',' and depth == 0) or i == n.end - 1:
            names = [j for j in range(a, i) if t[j][0] == 'id' and t[j][1] not in permc.QUALIFIERS]
            eq = next((j for j in range(a, i) if t[j][1] == '='), i)
            names = [j for j in names if j < eq]
            if names: out.append((a, i, t[names[0]][1]))
            a = i + 1
    return k, out


def _is_store(ctx, j):
    """Token j (an identifier) is the whole left side of a plain `=` statement."""
    t = ctx.toks
    if t[j + 1][1] != '=' or t[j - 1][1] in ('.', '->', '*', '&'): return None
    n = ctx.enclosing(j)
    if n.kind != 'stmt' or n.start != j or ctx.is_decl(n): return None
    return n


def _addr_taken(ctx, name):
    t = ctx.toks
    for j in ctx.ids.get(name, []):
        if t[j - 1][1] == '&' and (t[j - 2][0] not in ('id', 'num', 'chr') and t[j - 2][1] not in (')', ']')
                                   or t[j - 2][1] in permc.KEYWORDS):
            return True
    return False


def _loops_of(n):
    out = []
    while n is not None:
        if n.kind in ('for', 'while', 'do'): out.append(n)
        n = n.parent
    return out


def dead_stores(ctx):
    """Statements `v = E;` (v a local or parameter, E without side effects) whose value is not
    read before v's next store in the same block or the function's end."""
    t = ctx.toks
    out = []
    has_goto = any(t[i][1] == 'goto' for i in range(ctx.lb, ctx.rb))
    names = [nm for nm, l in ctx.locals.items() if not l.dims and nm not in ctx.dup_names] + [p for p in ctx.params if p not in ctx.locals]
    probe_blocks = {n for kind, n, _ in find_probes(ctx) if kind == 'block'}
    for name in names:
        occ = [j for j in ctx.ids.get(name, []) if t[j - 1][1] not in ('.', '->')]
        if not occ or _addr_taken(ctx, name): continue
        loc = ctx.locals.get(name)
        if loc is not None:
            occ = [j for j in occ if not (loc.node.start <= j < loc.node.end)]
        for j in occ:
            n = _is_store(ctx, j)
            if n is None or n.parent in probe_blocks: continue
            e = ctx.stmt_expr(n)
            if e is None or e.kind != 'assign' or e.op != '=' or permc.has_side_effects(e.kids[1], t): continue
            later = [k for k in occ if k >= n.end]
            live = False
            for k in later:
                m = _is_store(ctx, k)
                me = ctx.stmt_expr(m) if m is not None else None
                if me is not None and me.kind == 'assign' and m.parent is n.parent and name not in permc.idents(me.kids[1], t) \
                        and not any(c.kind == 'label' for c in n.parent.children
                                    if n.end <= c.start < m.start):
                    break                                   # overwritten first in straight line
                live = True; break
            if live: continue
            # a read earlier in an enclosing loop, or anywhere before when the function jumps
            earlier = [k for k in occ if k < n.start and _is_store(ctx, k) is None]
            if any(lp.start <= k < lp.end for lp in _loops_of(n) for k in earlier): continue
            if has_goto and earlier: continue
            out.append(n)
    return out


def repeated_stores(ctx):
    """The second of two equal sibling statements `L = E;` (E constant or a plain read that
    the store does not change)."""
    t = ctx.toks
    out = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k in range(1, len(kids)):
            a, b = kids[k - 1], kids[k]
            if a.kind != 'stmt' or b.kind != 'stmt' or ctx.is_decl(b): continue
            if texts(t, a.start, a.end) != texts(t, b.start, b.end): continue
            e = ctx.stmt_expr(b)
            if e is None or e.kind != 'assign' or e.op != '=': continue
            if permc.has_side_effects(e.kids[1], t) or not permc.is_lvalue_simple(e.kids[0], t): continue
            if permc.idents(e.kids[0], t) & permc.idents(e.kids[1], t): continue
            out.append(b)
    return out


def unused_locals(ctx):
    """[(declaration node, [names never used])], initialisers without side effects."""
    t = ctx.toks
    out = {}
    for name, l in ctx.locals.items():
        if name in ctx.dup_names: continue
        uses = [j for j in ctx.ids.get(name, []) if not (l.node.start <= j < l.node.end)]
        if uses: continue
        if l.init and permc.has_side_effects(ctx.expr(*l.init) or permc.E('atom', 0, 0), t): continue
        out.setdefault(l.node, []).append(name)
    return list(out.items())


def noop_casts(ctx):
    """Casts to the type their operand already has."""
    out = []
    for e in _all_exprs(ctx):
        for x in permc.ewalk(e):
            if x.kind == 'cast':
                ty = ' '.join(ctx.src(x.op[0], x.op[1]).split())
                ot = ctx.type_of(x.kids[0])
                if ot and ' '.join(ot.split()) == ty: out.append(x)
    return out


def _all_exprs(ctx):
    t = ctx.toks
    out = []
    for n in ctx.nodes:
        e = ctx.stmt_expr(n) or ctx.cond_expr(n)
        if e is None and n.kind == 'stmt' and t[n.start][1] == 'return' and n.end - n.start > 2:
            e = ctx.expr(n.start + 1, n.end - 1)
        if e is not None: out.append(e)
    return out


def single_read_helpers(ctx):
    """Rule-made getters and locals (pmt_ names) read once: a helper for a single read."""
    t = ctx.toks
    n = 0
    for name, occ in ctx.ids.items():
        if not name.startswith('pmt_'): continue
        if name.startswith(('pmt_get', 'pmt_at')):
            if len(occ) == 1: n += 1
            continue
        l = ctx.locals.get(name)
        if l is None: continue
        reads_ = [j for j in occ if not (l.node.start <= j < l.node.end) and _is_store(ctx, j) is None]
        if len(reads_) == 1: n += 1
    return n


DEAD_TEXT = re.compile(r'\*pmt_p\d+\s*=')


def dead_score(ctx):
    """Dead or redundant constructs in FUNC, weighted: stored comparisons and other probes, unused
    locals and pointer stores count 1; casts to the operand's own type and rule-made helpers or
    locals used for one read count 0.5; dead stores to locals and parameters and repeated stores
    count 0.25 (a redundant store can be the vendor's own code: the light weight only breaks
    ties towards the plainer source, and reduction of each new best decides)."""
    s = len(find_probes(ctx)) + 0.25 * (len(dead_stores(ctx)) + len(repeated_stores(ctx)))
    s += sum(len(v) for _, v in unused_locals(ctx))
    s += len(DEAD_TEXT.findall(' '.join(t[1] for t in ctx.toks[ctx.lb:ctx.rb])))
    s += 0.5 * (len(noop_casts(ctx)) + single_read_helpers(ctx))
    return s


def _remove_decls(ctx, groups):
    t = ctx.toks
    spans = []
    for n, names in groups:
        k, ds = _declarators(ctx, n)
        keep = [(a, b) for a, b, nm in ds if nm not in names]
        if not keep: spans.append((n.start, n.end, []))
        else:
            text = ctx.src(n.start, k) + ' ' + ', '.join(ctx.src(a, b) for a, b in keep) + ';'
            spans.append((n.start, n.end, permc.new(text, t[n.start][2])))
    return spans


def _rm_probes(ctx, rng): return r_probe_remove(ctx, rng, everywhere=True)


def _rm_dead_stores(ctx, rng):
    ds = dead_stores(ctx)
    return Edit([(n.start, n.end, []) for n in ds], f'remove {len(ds)} dead stores') if ds else None


def _rm_repeats(ctx, rng):
    rs = repeated_stores(ctx)
    return Edit([(n.start, n.end, []) for n in rs], f'remove {len(rs)} repeated stores') if rs else None


def _rm_unused(ctx, rng):
    us = unused_locals(ctx)
    return Edit(_remove_decls(ctx, us), 'remove unused ' + ', '.join(x for _, v in us for x in v)) if us else None


def _rm_casts(ctx, rng):
    cs = noop_casts(ctx)
    spans, last = [], []
    for x in sorted(cs, key=lambda x: x.start):
        if any(a <= x.start < b for a, b in last): continue
        inner = x.kids[0]
        spans.append((x.start, x.end, permc.new(ctx.src(inner.start, inner.end), ctx.toks[x.start][2])))
        last.append((x.start, x.end))
    return Edit(spans, f'remove {len(spans)} casts to the same type') if spans else None


def r_remove_dead(ctx, rng, everywhere=False):
    """Every dead construct removed in one move (probes, dead and repeated stores, unused locals,
    casts to the same type), or (not everywhere) one kind of them."""
    steps = [_rm_probes, _fix(_rm_dead_stores), _rm_repeats, _rm_casts, _fix(_rm_unused)]
    if not everywhere:
        steps = [rng.choice(steps)] + ([_fix(_rm_unused)] if rng.random() < 0.5 else [])
    toks, descs, meaning = _chain(ctx, rng, steps)
    if not descs: return None
    return _edit_of(ctx, toks, 'remove dead: ' + '; '.join(descs)[:250], 'probe', parts=getattr(ctx, '_parts', None))


# ---- common value temporary (B1 add with B5) and its inverse

def _underlying(ctx, lhs):
    """The object a store names: X for X, and for `*(T *)&X`."""
    if lhs.kind == 'unary' and lhs.op == '*':
        x = lhs.kids[0]
        while x.kind in ('cast', 'paren'): x = x.kids[0]
        if x.kind == 'unary' and x.op == '&' and permc.is_lvalue_simple(x.kids[0], ctx.toks): return x.kids[0]
        return None
    return lhs if permc.is_lvalue_simple(lhs, ctx.toks) else None


def r_common_temp(ctx, rng, everywhere=False):
    """`L = E; ... f(L or E);` against `{ T t = E; L = t; ... f(t); }`: a block local of a chosen
    type holds a value that a store and a later statement both use (the later statement read
    the stored object back, or computed E again). Inverse: such a block local goes, the store
    takes E and the later uses read the stored object."""
    t = ctx.toks
    fwd, inv = [], []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            e = ctx.stmt_expr(n)
            if e is None or e.kind != 'assign' or e.op != '=': continue
            lhs, rhs = e.kids
            obj = _underlying(ctx, lhs)
            if obj is None or permc.has_side_effects(rhs, t) or rhs.kind == 'atom' and rhs.op in ('num', 'chr'): continue
            otext = texts(t, obj.start, obj.end)
            etext = texts(t, rhs.start, rhs.end)
            deps = permc.idents(rhs, t) | {texts(t, obj.start, obj.start + 1)}
            uses = []
            for m in kids[k + 1:k + 5]:
                if m.kind in ('label', 'pp') or ctx.is_decl(m): break
                hits = []
                for x in _node_exprs(ctx, m):
                    for y in permc.ewalk(x):
                        tx = texts(t, y.start, y.end)
                        if (tx == otext or tx == etext) and not _is_lhs(ctx, y):
                            hits.append(y)
                hits = _outermost(hits)
                if hits: uses.append((m, hits))
                if written_in(ctx, m.start, m.end, deps) or _stores_between(ctx, m.start, m.end, deps): break
                if has_call(ctx, m.start, m.end) and hits: break
                if has_call(ctx, m.start, m.end): break
            if uses: fwd.append((blk, k, n, lhs, rhs, obj, uses))
        # inverse: { T t = E; X = t; ... }
        if blk is not ctx.root and len(blk.children) >= 3 and ctx.is_decl(blk.children[0]):
            d = blk.children[0]
            ls = [l for l in ctx.locals.values() if l.node is d]
            if len(ls) == 1 and ls[0].init and not ls[0].stars and not ls[0].dims:
                l = ls[0]
                s = ctx.stmt_expr(blk.children[1])
                if s is not None and s.kind == 'assign' and s.op == '=' and s.kids[1].kind == 'atom' \
                        and t[s.kids[1].start][1] == l.name and permc.is_lvalue_simple(s.kids[0], t):
                    rest = blk.children[2:]
                    if all(c.kind == 'stmt' or c.kind == 'if' for c in rest):
                        inv.append((blk, l, s))
    if not fwd and not inv: return None
    if fwd and (not inv or rng.random() < 0.7):
        blk, k, n, lhs, rhs, obj, uses = rng.choice(fwd)
        lt = resolve_type(ctx, ctx.type_of(obj))
        ty = rng.choice(['int', 'unsigned int'] + ([lt] if lt and is_int_type(ctx, lt) else [lt] if lt in ('float',) else []))
        nm = ctx.fresh('pmt_v')
        store_lhs = ctx.src(lhs.start, lhs.end)
        if obj is not lhs and rng.random() < 0.6: store_lhs = ctx.src(obj.start, obj.end)   # the cast store, plainly
        parts = [f'{ty} {nm} = {ctx.src(rhs.start, rhs.end)};', f'{store_lhs} = {nm};']
        last = uses[-1][0]
        between = [m for m in blk.children[k + 1:] if m.start <= last.start]
        for m in between:
            hits = next((h for mm, h in uses if mm is m), [])
            parts.append(_replace_spans(ctx, m.start, m.end, [(y.start, y.end, nm) for y in hits]))
        new = '{ ' + ' '.join(parts) + ' }'
        meaning = 'same' if ty == lt else 'type'
        return Edit([(n.start, last.end, permc.new(new, t[n.start][2]))],
                    f'common {ty} temporary for {ctx.src(rhs.start, rhs.end)[:40]} @{_line(ctx, n.start)}', meaning=meaning)
    blk, l, s = rng.choice(inv)
    x = ctx.src(s.kids[0].start, s.kids[0].end)
    val = ctx.src(*l.init)
    rest = blk.children[2:]
    uses = [j for j in ctx.ids.get(l.name, []) if j >= rest[0].start]
    if not uses: return None
    base = texts(t, s.kids[0].start, s.kids[0].start + 1)
    if written_in(ctx, rest[0].start, blk.end - 1, {base, l.name}) or _stores_between(ctx, rest[0].start, ctx.enclosing(uses[-1]).start, {base}): return None
    body = _replace_spans(ctx, rest[0].start, rest[-1].end, [(j, j + 1, x) for j in uses])
    new = f'{x} = {val}; ' + body
    lt = resolve_type(ctx, ctx.type_of(s.kids[0]))
    return Edit([(blk.start, blk.end, permc.new('{ ' + new + ' }' if blk.parent is not None and blk.parent.kind != 'block' else new,
                                                 t[blk.start][2]))],
                f'temporary {l.name} -> the stored {x[:40]}', meaning='same' if lt == l.type else 'type')


def _node_exprs(ctx, m):
    t = ctx.toks
    if m.kind == 'stmt':
        e = ctx.stmt_expr(m)
        if e is None and t[m.start][1] == 'return' and m.end - m.start > 2: e = ctx.expr(m.start + 1, m.end - 1)
        return [e] if e is not None else []
    if m.kind in ('if', 'switch', 'while'):
        out = [ctx.cond_expr(m)] if ctx.cond_expr(m) is not None else []
        for c in permc.walk(m):
            if c is not m and c.kind == 'stmt':
                e = ctx.stmt_expr(c)
                if e is not None: out.append(e)
        return out
    return []


def _is_lhs(ctx, y):
    t = ctx.toks
    j = y.end
    return j < len(t) and (t[j][1] in permc.ASSIGN or t[j][1] in ('++', '--')) or t[y.start - 1][1] in ('&', '++', '--') \
        and t[y.start - 2][1] in ('(', ',', '=', ';', '{', '}', 'return')


def _outermost(xs):
    xs = sorted(xs, key=lambda y: (y.start, -y.end))
    out = []
    for y in xs:
        if out and out[-1].start <= y.start and y.end <= out[-1].end: continue
        out.append(y)
    return out


# ---- a group of locals inlined at every use

def _local_runs(ctx):
    """Runs of two or more consecutive statements assigning distinct plain locals."""
    t = ctx.toks
    runs = []
    for blk in permc.blocks(ctx.root):
        cur = []
        for c in blk.children + [None]:
            e = ctx.stmt_expr(c) if c is not None else None
            nm = None
            if e is not None and e.kind == 'assign' and e.op == '=' and e.kids[0].kind == 'atom':
                nm = t[e.kids[0].start][1]
                l = ctx.locals.get(nm)
                if l is None or l.stars or l.dims or nm in cur or nm in ctx.dup_names: nm = None
            if nm: cur.append(nm)
            else:
                if len(cur) >= 2: runs.append(cur)
                cur = [nm] if nm else []
        # declarations with initialisers in a row count too
        inits = [l.name for l in ctx.locals.values() if l.block is blk and l.init and not l.stars and not l.dims
                 and l.name not in ctx.dup_names]
        if len(inits) >= 2: runs.append(inits)
    return runs


def _inline_name(name, single=None):
    def g(ctx, rng):
        loc = ctx.locals.get(name)
        if loc is None or loc.dims or loc.stars or name in ctx.dup_names: return None
        t = ctx.toks
        if not loc.init and not any(t[j + 1][1] == '=' for j in ctx.ids.get(name, [])): return None
        sites = []
        for n in ctx.nodes:
            e = ctx.stmt_expr(n)
            if e is None or e.kind != 'assign' or e.op != '=': continue
            lhs, rhs = e.kids
            if not (lhs.kind == 'atom' and t[lhs.start][1] == name): continue
            if permc.has_side_effects(rhs, t) or name in permc.idents(rhs, t): continue
            sites.append((n, rhs))
        if loc.init and loc.single:
            rhs = ctx.expr(*loc.init)
            if rhs is not None and not permc.has_side_effects(rhs, t) and name not in permc.idents(rhs, t):
                sites.append((loc.node, rhs))
        if not sites: return None
        return _inline_one(ctx, rng, name, loc, sites, True, single)
    return g


def r_inline_group(ctx, rng, everywhere=False):
    """B1 remove for several locals at once: a run of locals assigned one after another (or
    every such run, or every local read once) is inlined at all their uses, and the
    declarations that are left unused go (a multi-name declaration loses those names)."""
    runs = _local_runs(ctx)
    mode = rng.random()
    if everywhere and mode < 0.4:
        names = [nm for r in runs for nm in r]
        what = 'every run'
    elif mode < 0.75 and runs:
        names = rng.choice(runs); what = 'run'
    else:
        names = [nm for nm in ctx.locals]; what = 'every local read once'
        toks, descs, meaning = _chain(ctx, rng, [_inline_name(nm, True) for nm in names] + [_fix(_rm_unused)])
        if not descs: return None
        return _edit_of(ctx, toks, 'inline group (' + what + '): ' + '; '.join(descs)[:240], parts=getattr(ctx, '_parts', None))
    if not names: return None
    toks, descs, meaning = _chain(ctx, rng, [_inline_name(nm, False) for nm in names] + [_fix(_rm_unused)])
    if len(descs) < 1: return None
    return _edit_of(ctx, toks, f'inline group ({what}): ' + '; '.join(descs)[:240], parts=getattr(ctx, '_parts', None))


# ---- else { if } against else if

def r_flatten_else(ctx, rng, everywhere=False):
    """`else { if (c) S else T }` (the block holding only the if) against `else if (c) S else T`."""
    t = ctx.toks
    fwd = [n for n in ctx.nodes if n.kind == 'if' and n.els is not None and n.els.kind == 'block'
           and len(n.els.children) == 1 and n.els.children[0].kind == 'if']
    inv = [n for n in ctx.nodes if n.kind == 'if' and n.els is not None and n.els.kind == 'if']
    if not fwd and not inv: return None
    if fwd and (everywhere or not inv or rng.random() < 0.6):
        chosen = fwd if everywhere else [rng.choice(fwd)]
        spans = []
        for n in sorted(chosen, key=lambda n: n.start):
            if any(a <= n.els.start < b for a, b, _ in spans): continue
            inner = n.els.children[0]
            spans.append((n.els.start, n.els.end, permc.new(ctx.src(inner.start, inner.end), t[n.els.start][2])))
        return Edit(spans, f'else {{ if }} -> else if ({len(spans)})')
    n = rng.choice(inv)
    return Edit([(n.els.start, n.els.end, permc.new('{ ' + ctx.src(n.els.start, n.els.end) + ' }', t[n.els.start][2]))],
                f'else if -> else {{ if }} at line {_line(ctx, n.start)}')


# ---- pointer local against the indexed object (needs ctx.layouts)

SCALAR_SIZE = {'char': 1, 'unsigned char': 1, 'signed char': 1, 'uchar': 1, 'u8': 1, 'uint8_t': 1, 'int8_t': 1, 's8': 1,
               'byte': 1, 'BYTE': 1, 'bool': 1, 'short': 2, 'unsigned short': 2, 'ushort': 2, 'u16': 2, 'uint16_t': 2,
               'int16_t': 2, 's16': 2, 'word': 2, 'WORD': 2, 'int': 4, 'unsigned int': 4, 'unsigned': 4, 'uint': 4,
               'u32': 4, 'uint32_t': 4, 'int32_t': 4, 's32': 4, 'long': 4, 'unsigned long': 4, 'float': 4, 'DWORD': 4}


def _scalar_size(ctx, ty):
    ty = (ty or '').replace('const ', '').replace('volatile ', '').strip()
    if ty in SCALAR_SIZE: return SCALAR_SIZE[ty]
    td = ctx.types.typedefs.get(ty)
    return SCALAR_SIZE.get(td) if td else None


def _obj_struct(ctx, x):
    """(object source, struct type name) for G, G[k] (k constant) naming a struct global."""
    t = ctx.toks
    if x.kind == 'paren': return _obj_struct(ctx, x.kids[0])
    base = x
    if x.kind == 'index':
        if permc.const_value(x.kids[1], t) is None: return None
        base = x.kids[0]
    if base.kind != 'atom' or base.op != 'id': return None
    g = ctx.types.globals.get(t[base.start][1])
    if g is None: return None
    ty = g[0].replace('struct ', '').strip()
    if ty not in (getattr(ctx, 'layouts', None) or {}): return None
    if x.kind == 'atom' and g[1]: return None
    return ctx.src(x.start, x.end), ty


def _field_at(ctx, ty, off, size):
    for f, o, sz, esz, cnt in ctx.layouts[ty]:
        if cnt is None and o == off and sz == size: return f
        if cnt is not None and esz == size and o <= off < o + sz and (off - o) % esz == 0:
            return f'{f}[{(off - o) // esz}]'
    return None


def r_ptr_index(ctx, rng, everywhere=False):
    """B2 for pointer arithmetic: a pointer local set to the address of a struct object or of
    one of its fields (`p = &G[0].f;`, `p = (T *)&G[0]; p += K;`) and read as p[k] or *p is
    replaced at each read by the field at that offset (`G[0].g`), and its assignments and
    declaration go. Needs the struct layouts (ctx.layouts: offsets from one probe compile)."""
    t = ctx.toks
    if not getattr(ctx, 'layouts', None): return None
    cands = []
    for name, l in ctx.locals.items():
        if l.stars != '*' or l.dims: continue
        es = _scalar_size(ctx, l.type)
        if not es: continue
        occ = [j for j in ctx.ids.get(name, []) if not (l.node.start <= j < l.node.end)]
        state = None
        spans = []
        ok = True
        if l.init:
            r = _ptr_base(ctx, ctx.expr(*l.init))
            if r is None: continue
            state = (r[0], r[1], r[2], l.block)
        for j in occ:
            n = _is_store(ctx, j)
            if n is not None:
                e = ctx.stmt_expr(n)
                rhs = e.kids[1]
                if rhs.kind == 'bin' and rhs.op == '+' and rhs.kids[0].kind == 'atom' and t[rhs.kids[0].start][1] == name \
                        and state is not None and permc.const_value(rhs.kids[1], t) is not None:
                    state = (state[0], state[1], state[2] + es * permc.const_value(rhs.kids[1], t), state[3])
                else:
                    r = _ptr_base(ctx, rhs)
                    if r is None: ok = False; break
                    state = (r[0], r[1], r[2], n.parent)
                spans.append((n.start, n.end, []))
                continue
            if t[j + 1][1] in ('+=', '-=') and state is not None:
                n = ctx.enclosing(j)
                e = ctx.stmt_expr(n)
                k = permc.const_value(e.kids[1], t) if e is not None and n.start == j else None
                if k is None: ok = False; break
                state = (state[0], state[1], state[2] + es * (k if t[j + 1][1] == '+=' else -k), state[3])
                spans.append((n.start, n.end, []))
                continue
            if state is None or not (state[3].start <= j < state[3].end): ok = False; break
            if any(lp.start <= j < lp.end and not (lp.start <= state[3].start < lp.end) for lp in _loops_of(ctx.enclosing(j))):
                ok = False; break
            obj, ty, off = state[0], state[1], state[2]
            if t[j + 1][1] == '[':
                rb = ctx.pair[j + 1]
                ie = ctx.expr(j + 2, rb)
                k = permc.const_value(ie, t) if ie is not None else None
                if k is None: ok = False; break
                f = _field_at(ctx, ty, off + k * es, es)
                if f is None: ok = False; break
                spans.append((j, rb + 1, permc.new(f'{obj}.{f}', t[j][2])))
            elif t[j - 1][1] == '*' and (t[j - 2][0] == 'op' and t[j - 2][1] not in (')', ']') or t[j - 2][1] in ('return',)):
                f = _field_at(ctx, ty, off, es)
                if f is None: ok = False; break
                spans.append((j - 1, j + 1, permc.new(f'{obj}.{f}', t[j - 1][2])))
            else:
                ok = False; break
        if ok and spans and any(s[2] for s in spans):
            if l.single: spans.append((l.node.start, l.node.end, []))
            else: spans += _remove_decls(ctx, [(l.node, [name])])
            cands.append((name, spans))
    if not cands: return None
    chosen = cands if everywhere else [rng.choice(cands)]
    spans = []
    for nm, sp in chosen:
        if all(b <= a2 or a >= b2 for a, b, _ in sp for a2, b2, _ in spans): spans += sp
    return Edit(spans, 'pointer arithmetic -> fields: ' + ', '.join(nm for nm, _ in chosen))


def _ptr_base(ctx, e):
    """(object, struct type, byte offset) of `&G[k]`, `&G[k].f`, `(T *)&...`, or None."""
    t = ctx.toks
    if e is None: return None
    while e.kind in ('cast', 'paren'): e = e.kids[0]
    if e.kind != 'unary' or e.op != '&': return None
    x = e.kids[0]
    while x.kind == 'paren': x = x.kids[0]
    if x.kind == 'member' and x.op == '.':
        os_ = _obj_struct(ctx, x.kids[0])
        if os_ is None: return None
        f = t[x.end - 1][1]
        for name, o, sz, esz, cnt in ctx.layouts[os_[1]]:
            if name == f: return os_[0], os_[1], o
        return None
    os_ = _obj_struct(ctx, x)
    return (os_[0], os_[1], 0) if os_ else None


def r_obj_ptr(ctx, rng, everywhere=False):
    """B2 inverse: the fields of one struct object (`G[k].f`) read through a pointer local
    initialised to its address (`T *p = &G[k]; ... p->f`) at every use."""
    t = ctx.toks
    groups = {}
    for e in _all_exprs(ctx):
        for x in permc.ewalk(e):
            if x.kind == 'member' and x.op == '.':
                os_ = _obj_struct(ctx, x.kids[0]) if getattr(ctx, 'layouts', None) else None
                if os_ is None:
                    b = x.kids[0]
                    if b.kind == 'index' and b.kids[0].kind == 'atom' and permc.const_value(b.kids[1], t) is not None:
                        g = ctx.types.globals.get(t[b.kids[0].start][1])
                        if g and g[1] and g[0] in ctx.types.struct_fields: os_ = (ctx.src(b.start, b.end), g[0])
                    elif b.kind == 'atom' and b.op == 'id':
                        g = ctx.types.globals.get(t[b.start][1])
                        if g and not g[1] and g[0] in ctx.types.struct_fields: os_ = (ctx.src(b.start, b.end), g[0])
                if os_: groups.setdefault(os_, []).append(x)
    groups = {k: _outermost_members(v) for k, v in groups.items() if len(v) >= 2}
    if not groups: return None
    (obj, ty), xs = rng.choice(sorted(groups.items()))
    nm = ctx.fresh('pmt_r')
    spans = [(x.kids[0].start, x.kids[0].end + 1, permc.new(f'{nm}->', t[x.kids[0].start][2])) for x in xs]
    top = _decl_point(ctx, ctx.root)
    spans.append(ctx.insert_at(top, f'{ty} *{nm} = &{obj};'))
    return Edit(spans, f'{obj} through a pointer local ({len(xs)} uses)')


def _outermost_members(xs):
    seen, out = set(), []
    for x in sorted(xs, key=lambda x: x.start):
        if (x.start, x.end) in seen: continue
        seen.add((x.start, x.end)); out.append(x)
    return out


# ---- goto chain to the else arm against && ; boolean tests

def _not(ctx, e):
    """Source of the negation of e, as a person writes it: a flipped comparison, x == 0 for a
    value, or the operand of a !."""
    if e.kind == 'paren': return _not(ctx, e.kids[0])
    if e.kind == 'bin' and e.op in NEG:
        return f'{optext(ctx, e.kids[0], NEG[e.op])} {NEG[e.op]} {optext(ctx, e.kids[1], NEG[e.op])}'
    if e.kind == 'unary' and e.op == '!': return _strip(ctx, e.kids[0])
    if e.kind in ('bin',) and e.op in ('&&', '||'): return f'!({ctx.src(e.start, e.end)})'
    return f'{paren(ctx, e)} == 0'


def _goto_chain_to_and(ctx, rng):
    """`if (a) goto L; if (b) S else { L: T }` -> `if (!a && b) S else T`."""
    t = ctx.toks
    for blk in rng.sample(permc.blocks(ctx.root), len(permc.blocks(ctx.root))):
        kids = blk.children
        for k, c in enumerate(kids):
            if c.kind != 'if' or c.els is None or c.els.kind != 'block' or not c.els.children: continue
            lab = c.els.children[0]
            if lab.kind != 'label' or lab.extra != 'goto': continue
            L = t[lab.start][1]
            conds = []
            j = k - 1
            while j >= 0 and kids[j].kind == 'if' and kids[j].els is None:
                th = kids[j].then
                s = th if th.kind == 'stmt' else (th.children[0] if th.kind == 'block' and len(th.children) == 1 else None)
                if s is None or s.kind != 'stmt' or t[s.start][1] != 'goto' or t[s.start + 1][1] != L: break
                ce = ctx.cond_expr(kids[j])
                if ce is None: break
                conds.insert(0, _not(ctx, ce)); j -= 1
            if not conds: continue
            if sum(1 for i in range(ctx.lb, ctx.rb) if t[i][1] == L) != len(conds) + 1: continue
            ce = ctx.cond_expr(c)
            if ce is None: continue
            allc = [c_ if not re.search(r'\|\||\?', c_) else f'({c_})' for c_ in conds] + [optext(ctx, ce, '&&')]
            body = c.els.children[1:]
            if len(body) == 1 and body[0].kind == 'stmt':
                els = ctx.src(body[0].start, body[0].end)
            else:
                els = '{' + ''.join(x[2] + x[1] for x in t[lab.end:c.els.end - 1]) + ' }'
            new = 'if (' + ' && '.join(allc) + ') ' + _arm(ctx, c.then, True) + ' else ' + els
            return Edit([(kids[j + 1].start, c.end, permc.new(new, t[kids[j + 1].start][2]))], f'goto chain to {L} (else arm) -> &&')
    return None


def r_goto_and(ctx, rng, everywhere=False):
    """D9 for the else arm: `if (a) goto L; if (b) S else { L: T }` against `if (!a && b) S else T`."""
    t = ctx.toks
    ed = _goto_chain_to_and(ctx, rng)
    if ed is not None and (rng.random() < 0.7 or everywhere): return ed
    cands = [n for n in ctx.nodes if n.kind == 'if' and n.els is not None and ctx.cond_expr(n) is not None
             and ctx.cond_expr(n).kind == 'bin' and ctx.cond_expr(n).op == '&&' and n.parent is not None and n.parent.kind == 'block']
    if not cands: return ed
    n = rng.choice(cands)
    ops = []

    def flat(e):
        if e.kind == 'bin' and e.op == '&&': flat(e.kids[0]); flat(e.kids[1])
        else: ops.append(e)
    flat(ctx.cond_expr(n))
    L = ctx.fresh('pmt_L')
    head = ' '.join(f'if ({_not(ctx, o)}) goto {L};' for o in ops[:-1])
    els = ctx.src(n.els.start, n.els.end)
    els_in = els[1:-1] if n.els.kind == 'block' else els
    new = f'{head} if ({_strip(ctx, ops[-1])}) {_arm(ctx, n.then, True)} else {{ {L}: {els_in} }}'
    return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'&& -> goto chain to the else arm at line {_line(ctx, n.start)}')


def _goto_chain_to_or(ctx, rng):
    """r_goto_or's backward direction only."""
    ed = r_goto_or(ctx, rng)
    return ed if ed is not None and ed.desc.startswith('goto chain') else None


def _idempotent_store(ctx, s):
    """The target base name of a statement that can run twice with the effect of once."""
    t = ctx.toks
    if s.kind == 'block' and len(s.children) == 1: s = s.children[0]
    if s.kind != 'stmt': return None
    e = ctx.stmt_expr(s)
    if e is None or e.kind != 'assign' or e.op not in ('=', '|=', '&='): return None
    if not permc.is_lvalue_simple(e.kids[0], t) or permc.has_side_effects(e.kids[1], t): return None
    if permc.idents(e.kids[0], t) & permc.idents(e.kids[1], t): return None
    if e.op != '=' and permc.const_value(e.kids[1], t) is None: return None
    return permc.idents(e.kids[0], t)


def r_merge_ifs(ctx, rng, everywhere=False):
    """`if (a) S if (b) S` (S a store that can run twice, b not reading what S writes) against
    `if (a || b) S`."""
    t = ctx.toks
    fwd, inv = [], []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k in range(len(kids)):
            a = kids[k]
            if a.kind != 'if' or a.els is not None: continue
            w = _idempotent_store(ctx, a.then)
            ce = ctx.cond_expr(a)
            if w is None or ce is None: continue
            if ce.kind == 'bin' and ce.op == '||' and not permc.has_side_effects(ce, t) and not (permc.idents(ce, t) & w) \
                    and not has_call(ctx, a.cond[0], a.cond[1]):
                inv.append(a)
            run = [a]
            for b in kids[k + 1:]:
                if b.kind != 'if' or b.els is not None or texts(t, b.then.start, b.then.end) != texts(t, a.then.start, a.then.end): break
                cb = ctx.cond_expr(b)
                if cb is None or permc.has_side_effects(cb, t) or permc.idents(cb, t) & w or has_call(ctx, b.cond[0], b.cond[1]): break
                run.append(b)
            if len(run) >= 2 and (k == 0 or not (kids[k - 1] in [x for r in fwd for x in r[1:]])):
                fwd.append(run)
    if not fwd and not inv: return None
    if fwd and (not inv or rng.random() < 0.6):
        run = rng.choice(fwd)
        conds = [optext(ctx, ctx.cond_expr(x), '||') for x in run]
        new = 'if (' + ' || '.join(conds) + ') ' + ctx.src(run[0].then.start, run[0].then.end)
        return Edit([(run[0].start, run[-1].end, permc.new(new, t[run[0].start][2]))], f'{len(run)} ifs with one body -> || at line {_line(ctx, run[0].start)}')
    n = rng.choice(inv)
    ops = []

    def flat(e):
        if e.kind == 'bin' and e.op == '||': flat(e.kids[0]); flat(e.kids[1])
        else: ops.append(_strip(ctx, e))
    flat(ctx.cond_expr(n))
    body = ctx.src(n.then.start, n.then.end)
    new = ' '.join(f'if ({o}) {body}' for o in ops)
    return Edit([(n.start, n.end, permc.new(new, t[n.start][2]))], f'|| -> {len(ops)} ifs with one body at line {_line(ctx, n.start)}')


def r_flag_else(ctx, rng, everywhere=False):
    """`X = J; if (c) X = K;` against `if (c) X = K; else X = J;` (c not reading X, no calls)."""
    t = ctx.toks
    fwd, inv = [], []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k in range(len(kids)):
            b = kids[k]
            if b.kind != 'if': continue
            ce = ctx.cond_expr(b)
            if ce is None or permc.has_side_effects(ce, t) or has_call(ctx, b.cond[0], b.cond[1]): continue
            th = b.then.children[0] if b.then.kind == 'block' and len(b.then.children) == 1 else b.then
            e2 = ctx.stmt_expr(th) if th.kind == 'stmt' else None
            if e2 is None or e2.kind != 'assign' or e2.op != '=' or not permc.is_lvalue_simple(e2.kids[0], t): continue
            x = texts(t, e2.kids[0].start, e2.kids[0].end)
            base = permc.idents(e2.kids[0], t)
            if permc.idents(ce, t) & base: continue
            if b.els is None and k > 0:
                e1 = ctx.stmt_expr(kids[k - 1])
                if e1 is not None and e1.kind == 'assign' and e1.op == '=' and texts(t, e1.kids[0].start, e1.kids[0].end) == x \
                        and not permc.has_side_effects(e1.kids[1], t) and not (permc.idents(e1.kids[1], t) & base):
                    fwd.append((kids[k - 1], b, th))
            if b.els is not None:
                el = b.els.children[0] if b.els.kind == 'block' and len(b.els.children) == 1 else b.els
                e3 = ctx.stmt_expr(el) if el.kind == 'stmt' else None
                if e3 is not None and e3.kind == 'assign' and e3.op == '=' and texts(t, e3.kids[0].start, e3.kids[0].end) == x \
                        and not permc.has_side_effects(e3.kids[1], t) and not (permc.idents(e3.kids[1], t) & base):
                    inv.append((b, th, el))
    if not fwd and not inv: return None
    if fwd and (not inv or rng.random() < 0.6):
        a, b, th = rng.choice(fwd)
        new = f'if ({_strip(ctx, ctx.cond_expr(b))}) {ctx.src(b.then.start, b.then.end)} else {ctx.src(a.start, a.end)}'
        return Edit([(a.start, b.end, permc.new(new, t[a.start][2]))], f'store then conditional store -> if/else at line {_line(ctx, a.start)}')
    b, th, el = rng.choice(inv)
    new = f'{ctx.src(el.start, el.end)} if ({_strip(ctx, ctx.cond_expr(b))}) {ctx.src(b.then.start, b.then.end)}'
    return Edit([(b.start, b.end, permc.new(new, t[b.start][2]))], f'if/else store -> store then conditional store at line {_line(ctx, b.start)}')


# ---- one helper for every occurrence of an expression pattern (B6)

def r_index_helper(ctx, rng, everywhere=False):
    """B6 for a pattern: every read `A[K]` (K constant; or only those that are call arguments)
    through `static __forceinline T get(unsigned int k) { return A[k]; }`, or every store
    `A[K] = E;` through a `set(k, v)` helper."""
    t = ctx.toks
    reads_, stores = {}, {}
    for e in _all_exprs(ctx):
        for x in permc.ewalk(e):
            if x.kind == 'index' and x.kids[0].kind == 'atom' and x.kids[0].op == 'id' and permc.const_value(x.kids[1], t) is not None:
                nm = t[x.kids[0].start][1]
                g = ctx.types.globals.get(nm)
                if not g or not g[1] or nm in ctx.locals: continue
                if _is_lhs(ctx, x):
                    if e.kind == 'assign' and e.kids[0] is x and e.op == '=': stores.setdefault(nm, []).append(e)
                    continue
                reads_.setdefault(nm, []).append(x)
    opts = [('get', k, v) for k, v in reads_.items() if len(v) >= 2] + [('set', k, v) for k, v in stores.items() if len(v) >= 2]
    if not opts: return None
    kind, nm, xs = rng.choice(opts)
    ty = resolve_type(ctx, ctx.types.globals[nm][0])
    kt = rng.choice(['unsigned int', 'int', 'unsigned char'])
    s = ctx.start
    if kind == 'get':
        if rng.random() < 0.4:
            xs = [x for x in xs if _in_call_args(ctx, x) and ctx.toks[x.start - 1][1] in ('(', ',')] or xs
        fn = ctx.fresh('pmt_at')
        seen, spans = set(), []
        for x in xs:
            if (x.start, x.end) in seen: continue
            seen.add((x.start, x.end))
            spans.append((x.start, x.end, permc.new(f'{fn}({ctx.src(x.kids[1].start, x.kids[1].end)})', t[x.start][2])))
        defn = f'static __forceinline {ty} {fn}({kt} k) {{ return {nm}[k]; }}\n'
    else:
        fn = ctx.fresh('pmt_set')
        spans = []
        for e in xs:
            x = e.kids[0]
            spans.append((e.start, e.end, permc.new(f'{fn}({ctx.src(x.kids[1].start, x.kids[1].end)}, {ctx.src(e.kids[1].start, e.kids[1].end)})', t[e.start][2])))
        defn = f'static __forceinline void {fn}({kt} k, {ty} v) {{ {nm}[k] = v; }}\n'
    spans.append((s, s + 1, permc.new(defn, t[s][2]) + [Tok(t[s][0], t[s][1], '\n')]))
    return Edit(spans, f'{kind} helper for {nm}[k] at {len(spans) - 1} sites')


def r_helper_inline(ctx, rng, everywhere=False):
    """B6 inverse: a `static __forceinline` helper of one statement (`return E;` or a store)
    defined in the file is written out at every call in FUNC (arguments substituted), and its
    definition goes when nothing else calls it."""
    t = ctx.toks
    helpers = []
    i = 0
    while i < ctx.start:
        if t[i][1] == '__forceinline' and any(t[k][1] == 'static' for k in range(max(0, i - 2), i)):
            j = i
            while j < ctx.start and t[j][1] != '(': j += 1
            if j >= ctx.start: break
            name = t[j - 1][1]
            rp = ctx.pair[j]
            if rp + 1 < len(t) and t[rp + 1][1] == '{':
                rb = ctx.pair[rp + 1]
                params = []
                for k in range(j + 1, rp):
                    if t[k][0] == 'id' and t[k + 1][1] in (',', ')') and t[k][1] != 'void': params.append(t[k][1])
                body = t[rp + 2:rb]
                st = 0
                while st < len(body) and body[st][1] != ';': st += 1
                if st == len(body) - 1:
                    a = i
                    while a > 0 and t[a - 1][1] in ('static', '__inline', 'inline'): a -= 1
                    helpers.append((name, params, body[:-1], a, rb + 1))
                i = rb + 1; continue
        i += 1
    helpers = [h for h in helpers if h[0] in ctx.ids]
    if not helpers: return None
    name, params, body, a, b = rng.choice(helpers)
    ret = body and body[0][1] == 'return'
    btoks = body[1:] if ret else body
    spans = []
    for j in ctx.ids[name]:
        if t[j + 1][1] != '(': return None
        rp = ctx.pair[j + 1]
        args, cur, depth = [], [], 0
        for k in range(j + 2, rp):
            w = t[k][1]
            if w in '([': depth += 1
            elif w in ')]': depth -= 1
            if w == ',' and depth == 0: args.append(cur); cur = []
            else: cur.append(k)
        if cur: args.append(cur)
        if len(args) != len(params): return None
        sub = {p: '(' + ctx.src(ar[0], ar[-1] + 1) + ')' if len(ar) > 1 else ctx.src(ar[0], ar[-1] + 1) for p, ar in zip(params, args)}
        text = ' '.join(sub.get(x[1], x[1]) if x[0] == 'id' else x[1] for x in btoks)
        if ret and not (t[j - 1][1] in ('(', ',', '=', 'return') and t[rp + 1][1] in (')', ',', ';')): text = f'({text})'
        spans.append((j, rp + 1, permc.new(text, t[j][2])))
    others = sum(1 for k in range(0, len(t)) if t[k][1] == name) - len(ctx.ids[name]) - 1
    if others == 0:
        spans.append((a, b, []))
    return Edit(spans, f'helper {name} written out at {len(ctx.ids[name])} calls')


# ---- compound moves

def _size_all(ctx, rng): return r_sized_extern(ctx, rng, everywhere=True)
def _ptr_all(ctx, rng): return r_ptr_index(ctx, rng, everywhere=True)
def _flatten_all(ctx, rng): return r_flatten_else(ctx, rng, everywhere=True) if any(
    n.kind == 'if' and n.els is not None and n.els.kind == 'block' and len(n.els.children) == 1 and n.els.children[0].kind == 'if'
    for n in ctx.nodes) else None
def _dead_all(ctx, rng): return r_remove_dead(ctx, rng, everywhere=True)


def _inline_mode(mode):
    def g(ctx, rng):
        runs = _local_runs(ctx)
        if mode == 'runs': names = [nm for r in runs for nm in r]
        elif mode == 'run': names = rng.choice(runs) if runs else []
        else: names = list(ctx.locals)
        if not names: return None
        toks, descs, _ = _chain(ctx, rng, [_inline_name(nm, mode == 'single') for nm in names] + [_fix(_rm_unused)])
        return _edit_of(ctx, toks, f'inline {mode}: ' + '; '.join(descs)[:200], parts=getattr(ctx, '_parts', None)) if descs else None
    return g


NORMALISE = [('dead', _dead_all), ('size', _size_all), ('ptr', _fix(_ptr_all, 6)), ('goto_or', _fix(_goto_chain_to_or, 12)),
             ('goto_and', _fix(_goto_chain_to_and, 12)), ('inline', None), ('flatten', _fix(_flatten_all, 6)), ('dead2', _dead_all)]


def normalise_steps(rng, full=False, imode=None):
    steps = []
    imode = imode or rng.choice(['runs', 'run', 'single', 'every'])
    for name, fn in NORMALISE:
        if name == 'inline': fn = _inline_mode(imode)
        if full or rng.random() < 0.6: steps.append((name if name != 'inline' else 'inline ' + imode, fn))
    return steps


def r_normalise(ctx, rng, everywhere=False, full=False, imode=None):
    """A plainer source in one move: a random selection (all, with full) of: every dead construct
    removed, every sizeable extern sized, pointer arithmetic to fields, goto chains to one
    boolean test, locals inlined (a run, every run, every local read once, or every local),
    else { if } flattened. Each part alone often changes nothing or makes things worse; a fitted
    source hides the plain one, and these only pay off together."""
    steps = normalise_steps(rng, full, imode)
    toks, descs, meaning = _chain(ctx, rng, [fn for _, fn in steps])
    if not descs: return None
    return _edit_of(ctx, toks, 'normalise: ' + ' / '.join(d[:60] for d in descs)[:300], 'probe', parts=getattr(ctx, '_parts', None))


def r_size_and(ctx, rng, everywhere=False):
    """A6 with a function-level move in one step: every sizeable extern sized, then one
    structural move (locals inlined, a probe removed, pointers to fields, goto chains, a common
    temporary, else-if) on the result: the size changes what the move does."""
    if not getattr(ctx, 'array_sizes', None): return None
    second = rng.choice([lambda c, r: r_inline_group(c, r, True), lambda c, r: r_remove_dead(c, r, rng.random() < 0.5),
                         _fix(_ptr_all, 6), _fix(_goto_chain_to_or, 12), _fix(_goto_chain_to_and, 12),
                         lambda c, r: r_common_temp(c, r), lambda c, r: r_local_inline(c, r, True), _fix(_flatten_all, 6),
                         lambda c, r: r_normalise(c, r)])
    toks, descs, meaning = _chain(ctx, rng, [_size_all, second])
    if len(descs) < 2: return None
    return _edit_of(ctx, toks, 'size and: ' + ' / '.join(d[:80] for d in descs)[:300], meaning, parts=getattr(ctx, '_parts', None))


# ---------------------------------------------------------------- registry

@dataclass
class Rule:
    id: str
    kinds: str
    weight: float
    fn: object
    everywhere: bool = False


# Weights: the catalogue's counts of deciding edits (section 1), lowered for what its section 2
# says never decided anything (declaration order and local types on large functions, condition
# spellings, operand order, volatile, plain unread reads).
RULES = [
    Rule('probe_cmp', 'D1', 23, r_probe_cmp),
    Rule('probe_shift', 'D3', 10, r_probe_shift),
    Rule('probe_addr', 'D2', 8, r_probe_addr),
    Rule('probe_remove', 'D1-D3 inverse', 8, r_probe_remove, True),
    Rule('probe_move', 'D1-D3', 6, r_probe_move),
    Rule('local_inline', 'B1 remove', 13, r_local_inline, True),
    Rule('local_cache', 'B1 add', 13, r_local_cache),
    Rule('getter', 'B6', 12, r_getter, True),
    Rule('pointer_direct', 'B2', 7, r_pointer_direct),
    Rule('stmt_swap', 'D6', 12, r_stmt_swap),
    Rule('if_swap', 'D7', 8, r_if_swap),
    Rule('nest', 'D7', 4, r_nest),
    Rule('test_order', 'D7', 2, r_test_order),
    Rule('retype', 'B5', 4, r_retype),
    Rule('init_decl', 'C1', 7, r_init_decl),
    Rule('const_local', 'C2', 5, r_const_local),
    Rule('temp_expr', 'B1 add, B5', 5, r_temp_expr),
    Rule('dead_local', 'C3', 3, r_dead_local),
    Rule('ptr_store', 'C5', 2, r_ptr_store),
    Rule('dup_store', 'D4', 6, r_dup_store),
    Rule('if_switch', 'D8', 4, r_if_switch),
    Rule('goto_or', 'D9', 2, r_goto_or),
    Rule('chain_store', 'D10', 5, r_chain_store),
    Rule('postinc_index', 'D12', 5, r_postinc_index),
    Rule('preinc_cond', 'D13', 3, r_preinc_cond),
    Rule('range_test', 'D14', 5, r_range_test),
    Rule('bit_test', 'D16', 3, r_bit_test),
    Rule('ternary', 'D18', 6, r_ternary),
    Rule('shift_mul', 'D22', 5, r_shift_mul),
    Rule('cast_assign', 'D23', 5, r_cast_assign),
    Rule('compound', 'D31', 2, r_compound),
    Rule('case_edit', 'D29', 4, r_case_edit),
    Rule('scope', 'B3', 4, r_scope),
    Rule('decl_order', 'B4', 1.5, r_decl_order),
    Rule('split_decl', 'B4', 1, r_split_decl),
    Rule('unprototyped', 'A11', 1, r_unprototyped),
    Rule('sized_extern', 'A6', 3, r_sized_extern, True),
    Rule('operand_order', 'D20', 1.5, r_operand_order),
    Rule('volatile_read', 'D5', 1, r_volatile_read),
    Rule('cond_explicit', 'generic', 0.5, r_cond_explicit),
    Rule('inequality', 'generic', 0.5, r_inequality),
    Rule('ins_block', 'generic', 0.5, r_ins_block),
    Rule('common_temp', 'B1 add, B5', 6, r_common_temp),
    Rule('inline_group', 'B1 remove', 3, r_inline_group, True),
    Rule('remove_dead', 'D1-D4 inverse', 4, r_remove_dead, True),
    Rule('flatten_else', 'D7', 2, r_flatten_else, True),
    Rule('ptr_index', 'B2', 5, r_ptr_index, True),
    Rule('obj_ptr', 'B2 inverse', 2, r_obj_ptr),
    Rule('goto_and', 'D9', 2, r_goto_and),
    Rule('merge_ifs', 'D7', 3, r_merge_ifs),
    Rule('flag_else', 'D7', 3, r_flag_else),
    Rule('index_helper', 'B6', 5, r_index_helper),
    Rule('helper_inline', 'B6 inverse', 2, r_helper_inline),
    Rule('normalise', 'compound', 1.5, r_normalise),
    Rule('size_and', 'A6 + compound', 1.5, r_size_and),
]
BY_ID = {r.id: r for r in RULES}
MEANING = {'retype': 'type', 'volatile_read': 'same'}
