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
        while True:
            nm = f'{base}{k}'
            if nm not in names:
                allt = getattr(self, '_allt', None)
                if allt is None: allt = self._allt = {t[1] for t in self.toks if t[0] == 'id'}
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
        _target_reads(ctx, lhs, out)
        reads(ctx, rhs, out, e)
        return out
    if e.kind in ('postfix',) or (e.kind == 'unary' and e.op in ('++', '--', '&')):
        return out
    if cacheable(ctx, e):
        out.append(e)
        return out
    for k in e.kids: reads(ctx, k, out, e)
    return out


def _target_reads(ctx, lhs, out):
    """The reads a store target makes: its index expressions and the pointers it goes through. The
    object stored into is not one (`s.f = v` does not read s: a copy of s would take the store)."""
    k = lhs.kind
    if k == 'paren': _target_reads(ctx, lhs.kids[0], out)
    elif k == 'index':
        _target_reads(ctx, lhs.kids[0], out)
        reads(ctx, lhs.kids[1], out, lhs)
    elif k == 'member':
        if lhs.op == '->': reads(ctx, lhs.kids[0], out, lhs)
        else: _target_reads(ctx, lhs.kids[0], out)
    elif k == 'unary' and lhs.op == '*': reads(ctx, lhs.kids[0], out, lhs)
    elif k == 'cast': _target_reads(ctx, lhs.kids[0], out)


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


# ---- enumeration of the probe rules, for the permuter's sweep of single edits
# A rule function may carry `enumerate(ctx)`: a list of (tier, Edit) or (tier, Edit, family)
# covering what the rule can make, not a random draw of it. The tiers order the work when there
# is not time for all of it: every insertion point gets its tier 1 probes before any point gets
# its tier 2 ones. A family names what an edit shares with others at other points (the constant
# a shift probe uses, the operand a comparison reads): the sweep tries the rest of a family
# sooner once one of its members has improved the score.

def _names_near(ctx, i, want_local=False, with_globals=True):
    """Variable reads visible at token i (as _operands_near), each once, nearest use first."""
    best = {}
    for k in range(max(ctx.lb, i - 60), min(ctx.rb, i + 60)):
        w = ctx.toks[k]
        if w[0] == 'id' and w[1] not in permc.KEYWORDS and ctx.toks[k + 1][1] != '(' and ctx.visible(w[1], i):
            if w[1] in ctx.types.functions: continue
            loc = ctx.locals.get(w[1])
            if loc and (loc.dims or not ctx.assigned_before(w[1], i)): continue
            if w[1] in ctx.dup_names: continue
            is_local = bool(loc or w[1] in ctx.params)
            if want_local and not is_local: continue
            if not with_globals and not is_local: continue
            g = ctx.types.globals.get(w[1])
            if g and g[1]: continue
            if not _int_name(ctx, w[1]): continue
            d = abs(k - i)
            if d < best.get(w[1], 1 << 30): best[w[1]] = d
    if not best:
        return [n for n in list(ctx.locals) + list(ctx.params) if ctx.visible(n, i) and _int_name(ctx, n)
                and ctx.assigned_before(n, i) and n not in ctx.dup_names]
    return sorted(best, key=lambda n: (best[n], n))


def _consts_near(ctx, i, nonzero=False):
    """The function's constants used within 80 tokens of token i, each once, nearest first."""
    best = {}
    for k in range(max(ctx.lb, i - 80), min(ctx.rb, i + 80)):
        w = ctx.toks[k]
        if w[0] in ('num', 'chr') and w[1] in ctx.consts and not (nonzero and w[1] in ('0', '1')):
            d = abs(k - i)
            if d < best.get(w[1], 1 << 30): best[w[1]] = d
    return sorted(best, key=lambda c: (best[c], c))


def _probe_points(ctx):
    """The insertion points for a new probe: every statement boundary not inside a probe."""
    inside = [(n.start, n.end) for kind, n, _ in find_probes(ctx) if kind == 'block']
    return [p for p in ctx.points() if not any(a < p[0] < b for a, b in inside)]


def _probe_edit(ctx, i, e):
    return Edit([ctx.insert_at(i, _probe_text(e))], f'probe {e} @{_line(ctx, i)}', meaning='probe')


def _tests_near(ctx, i, limit=4):
    """Comparisons with a constant that the function makes within 120 tokens of token i, as
    `E == K` texts, nearest first: a probe that repeats a test near it (`model == 'C'` again in
    the arm that tested it) is the form most recorded probes had."""
    tests = getattr(ctx, '_pmt_tests', None)
    if tests is None:
        tests = ctx._pmt_tests = []
        for top in _all_exprs(ctx):
            for e in permc.ewalk(top):
                if e.kind != 'bin' or e.op not in ('==', '!=', '<', '>', '<=', '>='): continue
                l, r = e.kids
                if r.kind != 'atom' or ctx.toks[r.start][0] not in ('num', 'chr'): continue
                if permc.has_side_effects(l, ctx.toks): continue
                names = [ctx.toks[k][1] for k in range(l.start, l.end) if ctx.toks[k][0] == 'id']
                if any(n in ctx.dup_names for n in names): continue
                tests.append((e.start, names, f'{ctx.src(l.start, l.end)} == {ctx.toks[r.start][1]}'))
    out = []
    for at, names, text in sorted(tests, key=lambda x: abs(x[0] - i)):
        if abs(at - i) > 120 or len(out) >= limit: break
        if text in out: continue
        if all(ctx.visible(n, i) and ctx.assigned_before(n, i) for n in names if n in ctx.locals): out.append(text)
    return out


def _shared_consts(ctx, limit=10):
    """Constants the function uses more than once (candidates for one shared register), most
    used first; not 0 or 1."""
    cnt = {}
    for k in range(ctx.lb, ctx.rb):
        w = ctx.toks[k]
        if w[0] in ('num', 'chr') and w[1] in ctx.consts and w[1] not in ('0', '1'): cnt[w[1]] = cnt.get(w[1], 0) + 1
    return [c for c in sorted(cnt, key=lambda c: (-cnt[c], c)) if cnt[c] > 1][:limit]


def enum_probe_cmp(ctx):
    """Tier 1: at each point, the tests made near it repeated, and the two nearest operands
    compared with 0. Tier 2: the other operands with 0, the two nearest with the nearest
    constant. Tier 3: every operand with the two nearest constants, the difference of the two
    nearest. Tier 4: further constants (up to six) and operand pairs."""
    out = []
    for i, _, _ in _probe_points(ctx):
        ops = _names_near(ctx, i)
        ks = [k for k in _consts_near(ctx, i) if k != '0']
        made = set()

        def put(tier, e):
            if e not in made:
                made.add(e); out.append((tier, _probe_edit(ctx, i, e), 'cmp ' + e.split(' ')[0]))
        for e in _tests_near(ctx, i): put(1, e)
        for x in ops[:2]: put(1, f'{x} == 0')
        for x in ops[2:]: put(2, f'{x} == 0')
        for x in ops[:2]:
            for k in ks[:1]: put(2, f'{x} == {k}')
        if len(ops) > 1: put(3, f'{ops[0]} - {ops[1]} == 0')
        for x in ops:
            for k in ks[:2]: put(3, f'{x} == {k}')
        for x in ops:
            for k in ks[2:6]: put(4, f'{x} == {k}')
        for a in range(min(len(ops), 4)):
            for b in range(a + 1, min(len(ops), 4)): put(4, f'{ops[a]} - {ops[b]} == 0')
    return out


def enum_probe_shift(ctx):
    """Tier 1: at each point, the nearest constant shifted by the nearest local or parameter
    (or, with none, the nearest global). Tier 1.5: each constant the function shares (used more
    than once) at every eighth point, to learn which constants answer. Tier 2: each shared
    constant at every point, shifted by the nearest local and the nearest global; the other
    locals with the nearest constant. Tier 3: the next two near constants by each local, the
    nearest by each global. The family is the constant."""
    out = []
    shared = _shared_consts(ctx)
    for n, (i, _, _) in enumerate(_probe_points(ctx)):
        ks = _consts_near(ctx, i, nonzero=True) or shared[:1] or [c for c in ctx.consts if c not in ('0', '1')][:1]
        if not ks: continue
        loc = _names_near(ctx, i, want_local=True)
        glob = [x for x in _names_near(ctx, i) if x not in loc]
        made = set()

        def put(tier, k, x):
            e = f'({k} << {x}) == 0'
            if e not in made:
                made.add(e); out.append((tier, _probe_edit(ctx, i, e), 'shift ' + k))
        first = (loc or glob)[:1]
        for x in first: put(1, ks[0], x)
        if n % 8 == 0:
            for x in first:
                for k in shared: put(1.5, k, x)
        for x in dict.fromkeys(first + glob[:1]):
            for k in shared: put(2, k, x)
        for x in loc[1:]: put(2, ks[0], x)
        for x in loc:
            for k in ks[1:3]: put(3, k, x)
        for x in glob[:4]: put(3, ks[0], x)
    return out


def enum_probe_addr(ctx):
    """Tier 3: at each point, the addresses of the two globals named nearest to it compared.
    Tier 4: the nearest with each of the next two."""
    out = []
    gs = {w for w in ctx.ids if w in ctx.types.globals and w not in ctx.types.functions}
    if len(gs) < 2: return out
    for i, _, _ in _probe_points(ctx):
        near = sorted(gs, key=lambda g: (min(abs(k - i) for k in ctx.ids[g]), g))[:4]
        for n, b in enumerate(near[1:]):
            out.append((3 if n == 0 else 4, _probe_edit(ctx, i, f'(const void *)&{near[0]} != (const void *)&{b}')))
    return out


def enum_probe_remove(ctx):
    """Tier 0: each probe removed alone, and all of them."""
    ps = find_probes(ctx)
    out = []
    for chosen in [[p] for p in ps] + ([ps] if len(ps) > 1 else []):
        spans = []
        for kind, n, s in chosen:
            if kind == 'block': spans.append((n.start, n.end, []))
            else:
                e = ctx.cond_expr(n)
                spans.append((e.start, e.kids[1].start, []))
        out.append((0, Edit(spans, 'remove probe ' + '; '.join(' '.join(s.split())[:60] for _, _, s in chosen), meaning='probe')))
    return out


# any block that only declares one local and stores a side-effect-free value in it (the value is
# never read): the stored-comparison probe however it is spelt (with or without the cast, the
# value given at the declaration or after it)
PROBE_ANY = re.compile(r'^\{\s*(?:(?:unsigned|signed|const|volatile|register)\s+)*\w+\s+(\w+)\s*'
                       r'(?:=\s*([^;{}]*))?;\s*(?:\1\s*=\s*([^;{}]*);\s*)?\}$', re.S)
_PROBE_TYPES = ('unsigned', 'signed', 'int', 'uint', 'char', 'uchar', 'short', 'ushort', 'long', 'ulong',
                'u8', 'u16', 'u32', 's8', 's16', 's32', 'sizeof', 'void', 'const')
_CALLISH = re.compile(r'\b(\w+)\s*\(')
_ASSIGNISH = re.compile(r'\+\+|--|(?<![=!<>])=(?!=)')


def is_probe_block(text):
    """True for a block that is only a write to a local of its own (see PROBE_ANY)."""
    s = ' '.join(text.split())
    if PROBE.match(s): return True
    m = PROBE_ANY.match(s)
    if not m or (m.group(2) is None and m.group(3) is None): return False
    for e in (m.group(2), m.group(3)):
        if e is None: continue
        if _ASSIGNISH.search(e): return False
        if any(n not in _PROBE_TYPES for n in _CALLISH.findall(e)): return False
        if re.search(r'\b%s\b' % re.escape(m.group(1)), e): return False
    return True


def find_probes(ctx):
    """[(kind, node or span, text)] of the probes in FUNC: stored comparisons in blocks, and the
    comma form `(t = (E)), C` in a condition."""
    out = []
    for n in ctx.nodes:
        if n.kind == 'block' and n is not ctx.root and len(n.children) in (1, 2):
            s = ctx.src(n.start, n.end)
            if is_probe_block(s): out.append(('block', n, s))
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
    decl_site = False
    for n, rhs in chosen:
        blk = n.parent if n is not loc.node else loc.block
        if blk is None or blk.kind != 'block': continue
        # uses: from the statement to the next write of name in this block's extent
        a, b = n.end, blk.end - 1
        uses = []
        redefined = clean = False
        for j in ctx.ids.get(name, []):
            if j < a or j >= b: continue
            nx = t[j + 1][1]; pv = t[j - 1][1]
            if nx in permc.ASSIGN or nx in ('++', '--') or pv in ('++', '--', '&'):
                # the value stops here only when this is a statement of the same block that gives a
                # new value without reading the old one (not `v = v + 1`, `v += 1`, a store in an arm)
                m = ctx.enclosing(j)
                clean = nx == '=' and pv not in ('++', '--', '&', '.', '->', '*') and m.kind == 'stmt' and m.start == j \
                    and m.parent is blk and all(t[q][1] != name for q in range(j + 2, m.end))
                redefined = True
                b = j; break
            uses.append(j)
        uses = [j for j in uses if j < b]
        if not uses: continue
        if redefined and not clean: continue
        between = [c for c in blk.children if a <= c.start < b]
        if redefined and any(_escapes(ctx, c, ret=False) for c in between): continue      # the value can leave past the new one
        if any(t[q][1] == 'goto' for q in range(a, b)): continue
        if any(c.kind == 'label' for c in between) or any(a <= q < b for q in _goto_labels(ctx)): continue    # another value can come in
        if not redefined:
            # the value runs to the end of the block: nothing after the block, and nothing earlier in an
            # enclosing loop, may read it
            occ = ctx.ids.get(name, [])
            if any(j >= blk.end for j in occ): continue
            if any(lp.start <= j < lp.end and not (n.start <= j < blk.end) for lp in _loops_of(n) for j in occ
                   if not (loc.node.start <= j < loc.node.end)): continue
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
        if n is loc.node: decl_site = True               # the initialiser goes; the declaration only with the last mention
        else: spans.append((n.start, n.end, []))
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
    if decl_site:
        spans.append((loc.node.start, loc.node.end, []) if not remaining else (loc.init[0] - 1, loc.init[1], []))
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
        es = [x for x in es if ctx.type_of(x) and _hoist_safe(ctx, e, x)]
        if es: cands.append((n, es))
    if not cands: return None
    n, es = rng.choice(cands)
    mode = rng.random()
    if mode < 0.25 and n.kind == 'if':
        # both operands of a comparison in the condition (one rule in the catalogue's evidence)
        e = ctx.cond_expr(n)
        cmp_ = [x for x in permc.ewalk(e) if x.kind == 'bin' and x.op in ('<', '>', '<=', '>=', '==', '!=')
                and cacheable(ctx, x.kids[0]) and cacheable(ctx, x.kids[1]) and ctx.type_of(x.kids[0]) and ctx.type_of(x.kids[1])
                and _hoist_safe(ctx, e, x.kids[0]) and _hoist_safe(ctx, e, x.kids[1])]
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
    use = same if rng.random() < 0.5 and not _fx(ctx, x.start, x.end).vol else [x]       # (each read of a volatile stays a read)
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


def _hoist_safe(ctx, root, x):
    """Reading x before the statement of expression root is the same as reading it in place: no call,
    store or step elsewhere in root (other than the operators x is an operand of, which run after it)
    can change what x reads. (`send(bump(), g)` may call bump before it reads g.)"""
    T = None
    for a in permc.ewalk(root):
        if a.kind in ('assign', 'call', 'postfix') or (a.kind == 'unary' and a.op in ('++', '--')):
            if (a.start <= x.start and x.end <= a.end) or (x.start <= a.start and a.end <= x.end): continue
            if T is None: T = _fx(ctx, x.start, x.end)
            if not _store_indep(_fx(ctx, a.start, a.end), T): return False
    return True


def _has_float(ctx, e):
    """e mentions a float constant or a float variable (so it may not be an integer expression)."""
    t = ctx.toks
    for x in permc.ewalk(e):
        if x.kind == 'atom' and x.op == 'num' and not t[x.start][1].lower().startswith('0x') and re.search(r'[.eEfF]', t[x.start][1]): return True
        if x.kind in ('atom', 'index', 'member') and (ctx.type_of(x) or '').replace('const ', '').strip() in ('float', 'double'): return True
        if x.kind == 'cast' and ctx.src(x.op[0], x.op[1]) in ('float', 'double'): return True
    return False


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
    """B4: two declarations of a block change places, or two names of one declaration (`u8 n, i, v;`
    against `u8 i, n, v;`: the order counts within a declarator list too)."""
    blks = [b for b in permc.blocks(ctx.root) if sum(1 for c in b.children if ctx.is_decl(c)) >= 2]
    multi = sorted({l.node for l in ctx.locals.values() if not l.single}, key=lambda d: d.start)
    multi = [n for n in multi if not any(ctx.toks[i][1] == '=' for i in range(n.start, n.end))]
    if multi and (not blks or rng.random() < 0.3):
        n = rng.choice(multi)
        _, ds = _declarators(ctx, n)
        if len(ds) >= 2:
            i, j = sorted(rng.sample(range(len(ds)), 2))
            (a1, b1, n1), (a2, b2, n2) = ds[i], ds[j]
            return Edit([(a1, b1, permc.new(ctx.src(a2, b2), ctx.toks[a1][2])), (a2, b2, permc.new(ctx.src(a1, b1), ctx.toks[a2][2]))],
                        f'swap declarators {n1} / {n2}')
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
            if ctx.stmt_expr(a) is None or ctx.stmt_expr(b) is None: continue
            # the effect summaries (Fx): a store through a pointer or a call may touch any object but
            # a local whose address is never taken, so it does not pass a read of a global either
            if not _commute(_nfx(ctx, a), _nfx(ctx, b)): continue
            pairs.append((a, b))
    if not pairs: return None
    a, b = rng.choice(pairs)
    sa, sb = ctx.src(a.start, a.end), ctx.src(b.start, b.end)
    return Edit([(a.start, a.end, permc.new(sb, t[a.start][2])), (b.start, b.end, permc.new(sa, t[b.start][2]))],
                f'swap statements {sa[:30]} / {sb[:30]}')


# ---------------------------------------------------------------- D32 loop form

def _split_top(ctx, a, b, sep):
    """Token ranges of a..b split at sep outside brackets."""
    t, out, s, i = ctx.toks, [], a, a
    while i < b:
        w = t[i][1]
        if w in ('(', '[', '{'): i = ctx.pair[i]
        elif w == sep: out.append((s, i)); s = i + 1
        i += 1
    out.append((s, b))
    return out


def _elem_ids(ctx, a, b):
    return {ctx.toks[i][1] for i in range(a, b) if ctx.toks[i][0] == 'id'}


def _elem_writes(ctx, a, b):
    """Identifiers an expression a..b stores to (None when it is not a plain store or step)."""
    e = ctx.expr(a, b)
    if e is None: return None
    t, w = ctx.toks, set()
    for x in permc.ewalk(e):
        if x.kind == 'call': return None
        if x.kind in ('assign', 'postfix') or (x.kind == 'unary' and x.op in ('++', '--')):
            base = x.kids[0]
            if base.kind != 'atom': return None
            w.add(t[base.start][1])
    return w or None


def _independent(ctx, x, y):
    wx, wy = _elem_writes(ctx, *x), _elem_writes(ctx, *y)
    if wx is None or wy is None: return False
    return not (wx & _elem_ids(ctx, *y)) and not (wy & _elem_ids(ctx, *x))


def r_loop_form(ctx, rng, everywhere=False):
    """for-header forms: the statement before a for into its init, the body's last statement into its
    step, the reverse of both, the order of comma elements, and for against while."""
    t = ctx.toks
    cands = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            if n.kind not in ('for', 'while'): continue
            prev = kids[k - 1] if k and kids[k - 1].kind == 'stmt' and ctx.stmt_expr(kids[k - 1]) is not None \
                and _elem_writes(ctx, kids[k - 1].start, kids[k - 1].end - 1) else None
            body = n.body
            has_cont = any(t[i][1] == 'continue' for i in range(body.start, body.end))
            last = None
            if body.kind == 'block' and len(body.children) >= 2 and not has_cont:
                c = body.children[-1]
                if c.kind == 'stmt' and ctx.stmt_expr(c) is not None and _elem_writes(ctx, c.start, c.end - 1): last = c
            lp, rp = n.cond
            if n.kind == 'for':
                parts = _split_top(ctx, lp + 1, rp, ';')
                if len(parts) != 3: continue
                init = [x for x in _split_top(ctx, *parts[0], ',') if x[0] < x[1]]
                step = [x for x in _split_top(ctx, *parts[2], ',') if x[0] < x[1]]
                if any(t[i][1] in ('int', 'char', 'short', 'long', 'unsigned') for i in range(*parts[0])): continue
                ops = []
                if prev: ops += ['init_in'] * 3
                if last: ops += ['step_in'] * 3
                if prev and last: ops += ['both_in'] * 3
                if init: ops.append('init_out')
                if step and not has_cont: ops.append('step_out')
                if len(init) >= 2 and all(_independent(ctx, init[i], init[j]) for i in range(len(init)) for j in range(i)): ops.append('swap_init')
                if len(step) >= 2 and all(_independent(ctx, step[i], step[j]) for i in range(len(step)) for j in range(i)): ops.append('swap_step')
                if not has_cont and parts[1][0] < parts[1][1]: ops.append('to_while')
                for o in ops: cands.append((o, n, prev, last, parts, init, step))
            else:
                ops = ['to_for']
                if prev: ops.append('to_for_init')
                if last: ops.append('to_for_step')
                if prev and last: ops += ['to_for_both'] * 2
                for o in ops: cands.append((o, n, prev, last, [(lp + 1, lp + 1), (lp + 1, rp), (rp, rp)], [], []))
    if not cands: return None
    op, n, prev, last, parts, init, step = rng.choice(cands)
    body = n.body
    S = lambda r: ctx.src(*r)
    ini = [S(x) for x in init]; stp = [S(x) for x in step]
    cond = S(parts[1])
    pre = []                                    # statements put before the loop
    a = n.start
    body_txt = ctx.src(body.start, body.end)
    ind = ctx.indent(n.start)

    def body_without_last():
        return ctx.src(body.start, last.start) + permc.render(t[last.end:body.end])

    def body_plus(extra):
        if body.kind == 'block':
            inner = ctx.indent(body.children[-1].start) if body.children else ind + '    '
            return ctx.src(body.start, body.end - 1) + '\n' + inner + extra + permc.render(t[body.end - 1:body.end])
        return '{\n' + ind + '    ' + body_txt + '\n' + ind + '    ' + extra + '\n' + ind + '}'

    def join(old, new, new_first):
        if old and not all(_independent(ctx, x, new_rng) for x in old_rng): new_first = keep_order
        return ([new] + old) if new_first else (old + [new])

    kw = 'for'
    if op in ('init_in', 'both_in', 'to_for_init', 'to_for_both'):
        new_rng = (prev.start, prev.end - 1); old_rng = init; keep_order = True
        ini = join(ini, S(new_rng), rng.random() < 0.5)
        a = prev.start
    if op in ('step_in', 'both_in', 'to_for_step', 'to_for_both'):
        new_rng = (last.start, last.end - 1); old_rng = step; keep_order = True
        stp = join(stp, S(new_rng), rng.random() < 0.5)
        body_txt = body_without_last()
    if op == 'init_out':
        k = rng.randrange(len(ini)) if all(_independent(ctx, init[i], init[j]) for i in range(len(init)) for j in range(i)) else 0
        if rng.random() < 0.5 or k: pre = [ini.pop(k)]
        else: pre, ini = ini, []
    if op == 'step_out':
        indep = all(_independent(ctx, step[i], step[j]) for i in range(len(step)) for j in range(i))
        if rng.random() < 0.5 and len(stp) > 1:
            k = rng.randrange(len(stp)) if indep else 0
            body_txt = body_plus(stp.pop(k) + ';')
        else:
            body_txt = body_plus(' '.join(x + ';' for x in stp)); stp = []
    if op == 'swap_init': ini.reverse()
    if op == 'swap_step': stp.reverse()
    if op == 'to_while':
        pre = ini; kw = 'while'
        if stp: body_txt = body_plus(' '.join(x + ';' for x in stp))
    head = ''.join(x + ';\n' + ind for x in pre)
    if kw == 'while': text = head + f'while ({cond}) ' + body_txt
    else: text = head + f'for ({", ".join(ini)}; {cond}; {", ".join(stp)}) ' + body_txt
    return Edit([(a, n.end, permc.new(text, t[a][2]))], f'loop form {op} @{_line(ctx, n.start)}')



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
                    if k and k > 1 and k & (k - 1) == 0 and not _has_float(ctx, x.kids[0]): cands.append((x, 'shl', k.bit_length() - 1))
                if x.kind == 'assign' and x.op in ('<<=', '*='):
                    k = permc.const_value(x.kids[1], t)
                    if k is not None and (x.op == '<<=' and 0 < k < 31 or x.op == '*=' and k > 1 and k & (k - 1) == 0
                                          and not _has_float(ctx, x.kids[0])):
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
        # only a cast to the target's own type changes nothing when it goes (`int lo = (short)x` keeps its cast)
        if _tkind(ctx, ctx.src(rhs.op[0], rhs.op[1])) is None or _tkind(ctx, ctx.src(rhs.op[0], rhs.op[1])) != _tkind(ctx, ty): return None
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
        # not when the file calls it with arguments (legal for `f()`, an error for `f(void)`)
        if any(t[i][1] == ctx.func and t[i + 1][1] == '(' and t[i + 2][1] != ')' for i in range(len(t) - 2)): return None
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
    for k in ('array_sizes', 'layouts', 'c_opts'):
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
                if me is not None and me.kind == 'assign' and m.parent is n.parent and n.parent.kind == 'block' \
                        and name not in permc.idents(me.kids[1], t) \
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
    t = ctx.toks                # a store that is the unbraced arm of an if or a loop's body leaves its ;
    return Edit([(n.start, n.end, [] if n.parent.kind == 'block' else [Tok('op', ';', t[n.start][2])]) for n in ds],
                f'remove {len(ds)} dead stores') if ds else None


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
                dirty = written_in(ctx, m.start, m.end, deps) or _stores_between(ctx, m.start, m.end, deps) or has_call(ctx, m.start, m.end)
                # in one statement the reads come before its store or call; in an if or a loop that
                # stores or calls, a read can come after (or on the next turn): it is left alone
                if hits and not (dirty and m.kind != 'stmt'): uses.append((m, hits))
                if dirty: break
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
    kmax = max(permc.const_value((x.kids[0] if kind == 'set' else x).kids[1], t) for x in xs)
    kt = rng.choice(['unsigned int', 'int'] + (['unsigned char'] if 0 <= kmax < 256 else []))       # the index must fit its type
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


# ================================================================ movement, locals, further catalogue kinds
#
# The rules below move statements over more than one neighbour, join and split locals, and cover the
# catalogue kinds that had no rule (D11, D15, D17, D21, D24 to D28, B7, A7, A9) plus a few spellings
# of decomp-permuter. Those that claim the same meaning prove it from the tokens with the effect
# summary below (Fx) and refuse when they cannot: a store through a pointer or a call may touch any
# object that is not a local whose address is never taken; two named objects never alias; an
# identifier that is a macro is read through its body; two volatile accesses keep their order.

IKIND = {'char': (8, None), 'unsigned char': (8, False), 'uchar': (8, False), 'u8': (8, False), 'uint8_t': (8, False),
         'byte': (8, False), 'BYTE': (8, False), 'undefined1': (8, False),
         'signed char': (8, True), 'int8_t': (8, True), 's8': (8, True),
         'unsigned short': (16, False), 'ushort': (16, False), 'u16': (16, False), 'uint16_t': (16, False),
         'word': (16, False), 'WORD': (16, False), 'undefined2': (16, False), 'unsigned short int': (16, False),
         'short': (16, True), 'int16_t': (16, True), 's16': (16, True), 'signed short': (16, True), 'short int': (16, True),
         'unsigned int': (32, False), 'unsigned': (32, False), 'uint': (32, False), 'u32': (32, False),
         'uint32_t': (32, False), 'unsigned long': (32, False), 'dword': (32, False), 'DWORD': (32, False),
         'undefined4': (32, False), 'ulong': (32, False),
         'int': (32, True), 'signed': (32, True), 'signed int': (32, True), 'long': (32, True), 'int32_t': (32, True),
         's32': (32, True)}
CANON = {(8, False): 'unsigned char', (8, True): 'signed char', (16, False): 'unsigned short', (16, True): 'short',
         (32, False): 'unsigned int', (32, True): 'int'}
_SEQ_OPS = ('&&', '||')


def _tkind(ctx, ty):
    """(bits, signed) of an integer type text of at most 32 bits (typedefs of the file followed), or None."""
    if ty is None: return None
    ty = ' '.join(w for w in ty.split() if w not in ('const', 'volatile', 'register', 'static', 'extern'))
    for _ in range(4):
        td = ctx.types.typedefs.get(ty)
        if td is None: break
        ty = ' '.join(w for w in td.split() if w not in ('const', 'volatile'))
    return IKIND.get(ty)


def _fields(ctx):
    """{field name: {(type, dims)}} over the structs of the file (bit-fields are not in it)."""
    c = getattr(ctx, '_fld', None)
    if c is None:
        c = ctx._fld = {}
        for fl in ctx.types.struct_fields.values():
            for f, ty, dims in fl: c.setdefault(f, set()).add((ty, dims))
    return c


def _vols(ctx):
    v = getattr(ctx, '_volset', None)
    if v is None: v = ctx._volset = set(getattr(ctx.types, 'volatiles', ())) - ctx.types.names
    return v


def _array_params(ctx):
    """Parameters declared with [] or as function pointers (their type text is not their type)."""
    s = getattr(ctx, '_arrp', None)
    if s is None:
        t = ctx.toks
        lp = next(i for i in range(ctx.start, ctx.lb) if t[i][1] == '(')
        s = ctx._arrp = {t[i][1] for i in range(lp + 1, ctx.pair[lp]) if t[i][0] == 'id' and t[i + 1][1] in ('[', '(')}
        s |= {t[i][1] for i in range(lp + 1, ctx.pair[lp]) if t[i][0] == 'id' and t[i - 1][1] == '*' and t[i - 2][1] == '('}
    return s


def _field_type(ctx, name):
    """The type of struct field name when every struct of the file that has one agrees on it."""
    s = _fields(ctx).get(name)
    if s is None: return ctx.types.fields.get(name)         # a field of a struct with a tag only
    return ctx.types.fields.get(name) if len(s) == 1 else None


def _etype(ctx, e):
    """ctx.type_of, refusing a name declared twice and a field whose type depends on the struct."""
    t = ctx.toks
    while e.kind == 'paren': e = e.kids[0]
    if e.kind == 'atom' and e.op == 'id' and (t[e.start][1] in ctx.dup_names or t[e.start][1] in _array_params(ctx)
                                              and t[e.start][1] not in ctx.locals): return None
    if e.kind == 'member' and _field_type(ctx, t[e.end - 1][1]) is None: return None
    if e.kind == 'index' and e.kids[0].kind == 'member' and _field_type(ctx, t[e.kids[0].end - 1][1]) is None: return None
    if e.kind == 'index' and e.kids[0].kind == 'atom' and t[e.kids[0].start][1] in ctx.dup_names: return None
    if e.kind == 'index' and e.kids[0].kind == 'atom':
        nm = t[e.kids[0].start][1]
        if nm not in ctx.locals and nm not in ctx.params:
            g = ctx.types.globals.get(nm)
            if g and g[1] and g[1].count('[') != 1: return None
        l = ctx.locals.get(nm)
        if l and l.dims and l.dims.count('[') != 1: return None
    return ctx.type_of(e)


def _promote(k):
    return (32, True) if k[0] < 32 else k


def _ikind(ctx, e):
    """(bits, signed) of an expression that is provably an integer of at most 32 bits, else None."""
    t = ctx.toks
    k = e.kind
    if k == 'paren': return _ikind(ctx, e.kids[0])
    if k == 'atom':
        if e.op == 'chr': return (32, True)
        if e.op == 'num':
            s = t[e.start][1].lower()
            body = s.rstrip('ul'); suf = s[len(body):]
            if 'll' in suf: return None
            hexa = body.startswith('0x')
            if not hexa and re.search(r'[.ef]', body): return None
            try: v = int(body, 0)
            except ValueError:
                try: v = int(body, 8)
                except ValueError: return None
            if v > 0xffffffff: return None
            if 'u' in suf or v > 0x7fffffff: return (32, False)
            return (32, True)
        if e.op == 'id':
            if t[e.start][1] in _all_macros(ctx): return None
            return _tkind(ctx, _etype(ctx, e))
        return None
    if k in ('index', 'member'): return _tkind(ctx, _etype(ctx, e))
    if k == 'cast': return _tkind(ctx, ctx.src(e.op[0], e.op[1]))
    if k == 'sizeof': return (32, False)
    if k == 'unary':
        if e.op == '!': return (32, True)
        if e.op in ('-', '+', '~'):
            a = _ikind(ctx, e.kids[0])
            return _promote(a) if a else None
        return None
    if k == 'bin':
        if e.op in NEG or e.op in _SEQ_OPS: return (32, True)
        a, b = _ikind(ctx, e.kids[0]), _ikind(ctx, e.kids[1])
        if a is None or b is None: return None
        a, b = _promote(a), _promote(b)
        if e.op in ('<<', '>>'): return a
        return (32, a[1] and b[1])
    if k == 'cond':
        a, b = _ikind(ctx, e.kids[1]), _ikind(ctx, e.kids[2])
        if a is None or b is None: return None
        return (32, _promote(a)[1] and _promote(b)[1])
    if k == 'assign': return _ikind(ctx, e.kids[0])
    if k == 'comma': return _ikind(ctx, e.kids[-1])
    return None


def _all_macros(ctx):
    """{name: (parameters or None, body)}: the #defines of the file and of the headers read."""
    m = getattr(ctx, '_allmac', None)
    if m is None:
        m = dict(getattr(ctx.types, 'macros', {}))
        for tk in ctx.toks:
            if tk[0] == 'pp':
                mm = _DEF.match(tk[1].replace('\\\r\n', ' ').replace('\\\n', ' '))
                if mm: m.setdefault(mm.group(1), (mm.group(2), permc.strip_comments(mm.group(3)).strip()))
        ctx._allmac = m
    return m


_DEF = re.compile(r'#\s*define\s+(\w+)(\([^)\n]*\))?(.*)', re.S)


def _macro_info(ctx, name, depth=0):
    """(identifiers read, opaque, reads memory, volatile) of a macro's body, through the macros it uses.
    Opaque: the body stores, steps, or cannot be read."""
    c = ctx.__dict__.setdefault('_mi', {})
    if name in c: return c[name]
    c[name] = (frozenset(), True, True, True)                      # while it is being read: opaque
    params, body = _all_macros(ctx)[name]
    pset = set(re.findall(r'\w+', params or ''))
    ids, opaque, mem, vol = set(), False, False, False
    try:
        toks, _ = permc.tokenize(body)
    except ValueError:
        toks, opaque = [], True
    if depth > 6: opaque = True
    for i, tk in enumerate(toks):
        if tk[0] == 'pp': opaque = True
        elif tk[0] == 'id':
            w = tk[1]
            if w == 'volatile': vol = True
            if w in permc.KEYWORDS or w in pset or ctx.types.is_type_word(w): continue
            if i and toks[i - 1][1] in ('.', '->'): continue
            if w in _all_macros(ctx):
                a, b, c2, d = _macro_info(ctx, w, depth + 1)
                ids |= a; opaque |= b; mem |= c2; vol |= d
            elif i + 1 < len(toks) and toks[i + 1][1] == '(': opaque = True          # a call
            else: ids.add(w)
        elif tk[0] == 'op':
            if tk[1] in permc.ASSIGN or tk[1] in ('++', '--'): opaque = True
            if tk[1] in ('*', '->', '['): mem = True
    c[name] = (frozenset(ids), opaque, mem, vol)
    return c[name]


def _addr_names(ctx):
    """Names in FUNC whose address is taken (&x, &x.f, &x[k])."""
    s = getattr(ctx, '_addr', None)
    if s is None:
        s, t = set(), ctx.toks
        for i in range(ctx.lb, ctx.rb):
            if t[i][1] == '&' and not _operand_end(ctx, i - 1):
                j = i + 1
                while t[j][1] == '(': j += 1
                if t[j][0] == 'id': s.add(t[j][1])
        ctx._addr = s
    return s


def _operand_end(ctx, j):
    """Token j ends an operand (so a following * or & is binary)."""
    k, w, _ = ctx.toks[j]
    if k in ('num', 'chr', 'str'): return True
    if k == 'id': return w not in permc.KEYWORDS
    if w == ']': return True
    if w == ')':
        o = ctx.pair[j]
        inner = ctx.toks[o + 1:j]
        # a cast (T) or (T *): what follows is its operand
        if inner and (inner[-1][1] == '*' or all(x[0] == 'id' and ctx.types.is_type_word(x[1]) for x in inner)): return False
        return True
    return False


def _private(ctx):
    """Locals and parameters no store through a pointer and no call can reach: scalars, structs and
    pointers (as variables) whose address is never taken, declared once."""
    s = getattr(ctx, '_priv', None)
    if s is None:
        ad = _addr_names(ctx)
        # (a block's local can share its name with a global used outside the block: then it is not one object)
        s = {n for n, l in ctx.locals.items() if not l.dims and n not in ad and n not in ctx.dup_names
             and all(l.block.start < j < l.block.end for j in ctx.ids.get(n, ()))}
        s |= {n for n in ctx.params if n not in ad and n not in ctx.locals}
        ctx._priv = s
    return s


def _is_array(ctx, j):
    """Identifier token j, followed by [, names an array object (or an array field), not a pointer."""
    t = ctx.toks
    w = t[j][1]
    if t[j - 1][1] in ('.', '->'):
        ft = _field_type(ctx, w)
        return bool(ft) and ft.endswith('[]')
    l = ctx.locals.get(w)
    if l is not None: return bool(l.dims) and w not in ctx.dup_names
    if w in ctx.params: return False
    g = ctx.types.globals.get(w)
    return bool(g and g[1])


class Fx:
    """What a token range does: identifiers read or written (ids), named objects stored to (writes),
    reads and stores through pointers, calls, volatile accesses; ext: it names an object that is not
    a private local; wext: it stores to one."""
    __slots__ = ('ids', 'writes', 'memr', 'memw', 'call', 'vol', 'ext', 'wext')

    def __repr__(self):
        return 'Fx(' + ', '.join(f'{k}={getattr(self, k)}' for k in self.__slots__) + ')'


def _target(ctx, j):
    """(named object stored to or None, the store goes through a pointer) of the lvalue ending at token j."""
    t = ctx.toks
    mem = False
    while True:
        k, w, _ = t[j]
        if w == ']':
            j = ctx.pair[j] - 1
            if t[j][1] == ']': continue
            if t[j][0] != 'id': return None, True
            if not _is_array(ctx, j): mem = True
            continue
        if k == 'id' and w not in permc.KEYWORDS:
            if t[j - 1][1] == '->': mem = True; j -= 2; continue
            if t[j - 1][1] == '.': j -= 2; continue
            if t[j - 1][1] == '*' and not _operand_end(ctx, j - 2): return None, True
            return (None, True) if mem else (w, False)
        return None, True


def _fx(ctx, a, b):
    c = ctx.__dict__.setdefault('_fxc', {})
    r = c.get((a, b))
    if r is not None: return r
    t = ctx.toks
    ids, writes = set(), set()
    memr = memw = call = vol = False
    macros = _all_macros(ctx)
    vols = _vols(ctx)
    tw = ctx.types.is_type_word
    for i in range(a, b):
        k, w, _ = t[i]
        if k == 'id':
            if w in permc.KEYWORDS:
                if w == 'volatile': vol = True
                continue
            if w in vols: vol = True
            if t[i - 1][1] in ('.', '->'): continue
            if tw(w) and w not in ctx.locals and w not in ctx.params: continue      # (a typedef's name can be a local's too)
            if w in macros and w not in ctx.locals and w not in ctx.params:
                mi, op, mm, mv = _macro_info(ctx, w)
                ids |= mi; ids.add(w)
                if op: call = memw = True
                memr |= mm; vol |= mv or bool(mi & vols)
                continue
            if t[i + 1][1] == '(' and w not in ctx.locals and w not in ctx.params:
                call = True
                continue
            ids.add(w)
        elif k == 'pp':
            call = memw = memr = True
        elif k == 'op':
            if w == '->': memr = True
            elif w == '*':
                if not _operand_end(ctx, i - 1): memr = True
            elif w == '[':
                p = t[i - 1]
                if p[0] == 'id' and p[1] not in permc.KEYWORDS:
                    if not _is_array(ctx, i - 1): memr = True
                elif p[1] != ']': memr = True
            elif w in permc.ASSIGN or w in ('++', '--'):
                if w in permc.ASSIGN or _operand_end(ctx, i - 1):
                    nm, mem = _target(ctx, i - 1)
                else:                                           # prefix: the lvalue follows
                    j = i + 1
                    if t[j][0] == 'id' and t[j][1] not in permc.KEYWORDS:
                        while t[j + 1][1] in ('.', '->', '['):
                            j = j + 2 if t[j + 1][1] != '[' else ctx.pair[j + 1]
                        nm, mem = _target(ctx, j)
                    else: nm, mem = None, True
                if nm is not None and nm in macros and nm not in ctx.locals and nm not in ctx.params: nm, mem = None, True; call = True
                if nm is not None: writes.add(nm)
                if mem: memw = True
    priv = _private(ctx)
    r = Fx()
    r.ids, r.writes, r.memr, r.memw, r.call, r.vol = ids, writes, memr, memw, call, vol
    r.ext = any(x not in priv for x in ids)
    r.wext = any(x not in priv for x in writes)
    c[(a, b)] = r
    return r


def _commute(A, B):
    """Two ranges can run in either order with the same result."""
    if A.call and (B.call or B.ext or B.memr or B.memw or B.vol): return False
    if B.call and (A.ext or A.memr or A.memw or A.vol): return False
    if A.writes & B.ids or B.writes & A.ids: return False
    if A.memw and (B.ext or B.memr or B.memw): return False
    if B.memw and (A.ext or A.memr or A.memw): return False
    if (A.wext and B.memr) or (B.wext and A.memr): return False
    if A.vol and B.vol: return False
    return True


def _store_indep(W, T):
    """W's stores cannot change what T reads, and T does nothing but read."""
    return not (T.call or T.writes or T.memw or W.writes & T.ids or (W.memw and (T.ext or T.memr)) or (W.wext and T.memr)
                or (W.call and (T.ext or T.memr)))


def _escapes(ctx, n, loop=False, sw=False, ret=True):
    """Node n can pass control somewhere other than its end, or be entered other than at its start:
    a goto or label, a break or continue of an enclosing statement, (with ret) a return."""
    k = n.kind
    if k == 'stmt':
        w = ctx.toks[n.start][1]
        if w == 'goto': return True
        if w == 'return': return ret
        if w == 'break': return not (loop or sw)
        if w == 'continue': return not loop
        return _macro_hides(ctx, n.start, n.end)[0]             # a macro whose body jumps
    if k == 'label': return n.extra == 'goto' or not sw
    if k == 'pp': return True
    if k in ('for', 'while', 'do'): return _escapes(ctx, n.body, True, sw, ret)
    if k == 'switch': return _escapes(ctx, n.body, loop, True, ret)
    return any(_escapes(ctx, c, loop, sw, ret) for c in n.subs())


def _macro_hides(ctx, a, b):
    """(a macro used in tokens a..b has return, break, continue or goto in its body; the locals and
    parameters of FUNC that the bodies of the macros used there name)."""
    c = ctx.__dict__.setdefault('_mh', {})
    r = c.get((a, b))
    if r is None:
        t, macros = ctx.toks, _all_macros(ctx)
        jump, names = False, set()
        for i in range(a, b):
            w = t[i][1]
            if t[i][0] == 'id' and w in macros and w not in ctx.locals and w not in ctx.params:
                body = macros[w][1]
                if re.search(r'\b(return|break|continue|goto)\b', body): jump = True
                names |= {x for x in _macro_info(ctx, w)[0] if x in ctx.locals or x in ctx.params}
        r = c[(a, b)] = (jump, names)
    return r


def _movable(ctx, n):
    c = ctx.__dict__.setdefault('_mv', {})
    r = c.get(n.start)
    if r is None:
        r = c[n.start] = n.kind in ('stmt', 'if', 'for', 'while', 'do', 'switch', 'block') \
            and not (n.kind == 'stmt' and ctx.is_decl(n)) and not _escapes(ctx, n)
    return r


def _nfx(ctx, n):
    return _fx(ctx, n.start, n.end)


def _order_edit(ctx, kids, order, desc):
    """The contiguous sibling nodes kids rewritten in the given order (each takes the white space of
    the place it moves to)."""
    t = ctx.toks
    new = []
    for slot, n in zip(kids, order):
        seg = list(t[n.start:n.end])
        seg[0] = Tok(seg[0][0], seg[0][1], t[slot.start][2])
        new += seg
    return Edit([(kids[0].start, kids[-1].end, new)], desc)


def _short(ctx, n, k=30):
    return ' '.join(ctx.src(n.start, n.end).split())[:k]


def r_stmt_move(ctx, rng, everywhere=False):
    """D6 over a distance: a simple statement moved up or down past two or more statements, or past a
    compound one (an if, a loop, a switch, a block), each of which it commutes with. Nothing with a
    jump out, a label or a declaration is crossed."""
    c = getattr(ctx, '_sm', None)
    if c is None:
        c = ctx._sm = [(blk, k) for blk in permc.blocks(ctx.root) if len(blk.children) >= 2
                       for k, s in enumerate(blk.children) if s.kind == 'stmt']
    if not c: return None
    for _ in range(24):
        blk, k = rng.choice(c)
        kids = blk.children
        s = kids[k]
        if ctx.stmt_expr(s) is None or not _movable(ctx, s): continue
        fs = _nfx(ctx, s)
        dests = []
        for step in (1, -1):
            j = k + step
            while 0 <= j < len(kids) and _movable(ctx, kids[j]) and _commute(fs, _nfx(ctx, kids[j])):
                if abs(j - k) >= 2 or kids[j].kind != 'stmt': dests.append(j)
                j += step
        if not dests: continue
        j = rng.choice(dests)
        lo, hi = min(j, k), max(j, k)
        seg = kids[lo:hi + 1]
        order = seg[1:] + [s] if j > k else [s] + seg[:-1]
        return _order_edit(ctx, seg, order, f'move {_short(ctx, s)} {"down" if j > k else "up"} {abs(j - k)} @{_line(ctx, s.start)}')
    return None


def r_compound_swap(ctx, rng, everywhere=False):
    """D6 for compound statements: two adjacent ifs, loops, switches or blocks that commute change places."""
    c = getattr(ctx, '_cs', None)
    if c is None:
        c = ctx._cs = [(blk, k) for blk in permc.blocks(ctx.root) for k in range(len(blk.children) - 1)
                       if blk.children[k].kind in ('if', 'for', 'while', 'do', 'switch', 'block')
                       and blk.children[k + 1].kind in ('if', 'for', 'while', 'do', 'switch', 'block')]
    if not c: return None
    for _ in range(12):
        blk, k = rng.choice(c)
        a, b = blk.children[k], blk.children[k + 1]
        if _movable(ctx, a) and _movable(ctx, b) and _commute(_nfx(ctx, a), _nfx(ctx, b)):
            return _order_edit(ctx, [a, b], [b, a], f'swap {a.kind} @{_line(ctx, a.start)} with {b.kind} @{_line(ctx, b.start)}')
    return None


def _declared(ctx, blk):
    """Names declared directly in a block."""
    out = set()
    if blk.kind != 'block': return out
    for c in blk.children:
        if ctx.is_decl(c): out |= {nm for _, _, nm in _declarators(ctx, c)[1]}
    return out


def _same_text(ctx, x, y):
    t = ctx.toks
    return x.end - x.start == y.end - y.start and all(t[x.start + i][1] == t[y.start + i][1] for i in range(x.end - x.start))


def _jump_stmt(ctx, n):
    return n.kind == 'stmt' and ctx.toks[n.start][1] in ('return', 'break', 'continue', 'goto')


def r_if_hoist(ctx, rng, everywhere=False):
    """D33 (new kind): a statement that begins both arms of an if/else moves to before the if, one that
    ends both arms to after it, and the reverse of each (the statement before or after an if/else
    goes into both arms). The leading form needs the statement to commute with the condition."""
    t = ctx.toks
    cands = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            if n.kind != 'if' or n.els is None or n.els.kind == 'if' or n.then.kind == 'if': continue
            A, B = n.then, n.els
            if A.kind not in ('block', 'stmt') or B.kind not in ('block', 'stmt'): continue
            if any(c.kind in ('label', 'pp') for x in (A, B) for c in x.subs()): continue
            dn = _declared(ctx, A) | _declared(ctx, B)
            fc = _fx(ctx, n.cond[0], n.cond[1] + 1)
            if A.kind == 'block' and B.kind == 'block' and len(A.children) >= 2 and len(B.children) >= 2:
                x, y = A.children[0], B.children[0]
                if x.kind == 'stmt' and ctx.stmt_expr(x) is not None and _same_text(ctx, x, y) \
                        and not (_elem_ids(ctx, x.start, x.end) & dn) and _commute(_nfx(ctx, x), fc):
                    cands.append(('head_out', n, x, y))
                x, y = A.children[-1], B.children[-1]
                if x.kind == 'stmt' and not ctx.is_decl(x) and _same_text(ctx, x, y) and not (_elem_ids(ctx, x.start, x.end) & dn):
                    cands.append(('tail_out', n, x, y))
            arms_ok = all(x.kind == 'stmt' and not ctx.is_decl(x) or x.kind == 'block' for x in (A, B))
            if not arms_ok: continue
            if k > 0:
                p = kids[k - 1]
                if p.kind == 'stmt' and ctx.stmt_expr(p) is not None and not (_elem_ids(ctx, p.start, p.end) & dn) \
                        and not any(x.kind == 'block' and x.children and ctx.is_decl(x.children[0]) for x in (A, B)) \
                        and _commute(_nfx(ctx, p), fc):
                    cands.append(('head_in', n, p, None))
            if k + 1 < len(kids):
                q = kids[k + 1]
                if q.kind == 'stmt' and not ctx.is_decl(q) and not (_elem_ids(ctx, q.start, q.end) & dn) \
                        and not any(_jump_stmt(ctx, (x.children[-1] if x.kind == 'block' and x.children else x)) for x in (A, B)):
                    cands.append(('tail_in', n, q, None))
    if not cands: return None
    op, n, x, y = rng.choice(cands)
    s = ctx.src(x.start, x.end)
    desc = f'{op} {" ".join(s.split())[:30]} @{_line(ctx, n.start)}'
    if op == 'head_out':
        return Edit([ctx.insert_at(n.start, s), (x.start, x.end, []), (y.start, y.end, [])], desc)
    if op == 'tail_out':
        return Edit([(x.start, x.end, []), (y.start, y.end, []), ctx.insert_at(n.end, s)], desc)
    spans = [(x.start, x.end, [])]
    for arm in (n.then, n.els):
        if arm.kind == 'block':
            at = arm.children[0].start if (op == 'head_in' and arm.children) else arm.end - 1
            spans.append(ctx.insert_at(at, s))
        else:
            body = ctx.src(arm.start, arm.end)
            new = '{ ' + (s + ' ' + body if op == 'head_in' else body + ' ' + s) + ' }'
            spans.append((arm.start, arm.end, permc.new(new, t[arm.start][2])))
    return Edit(spans, desc)


def _is_intish_cmp(ctx, e):
    return e.kind == 'bin' and e.op in NEG and _ikind(ctx, e.kids[0]) is not None and _ikind(ctx, e.kids[1]) is not None


def _neg(ctx, e):
    """Source of !e: a flipped comparison only for integers (a float comparison is not its flip), the
    operand of a !, else !(e)."""
    while e.kind == 'paren': e = e.kids[0]
    if _is_intish_cmp(ctx, e):
        return f'{ctx.src(e.kids[0].start, e.kids[0].end)} {NEG[e.op]} {ctx.src(e.kids[1].start, e.kids[1].end)}'
    if e.kind == 'unary' and e.op == '!':
        k = e.kids[0]
        while k.kind == 'paren' and k.kids[0].kind != 'comma': k = k.kids[0]
        return ctx.src(k.start, k.end)
    return f'!({ctx.src(e.start, e.end)})'


def _first_true(ctx, prev, cond):
    """prev is `x = K;` and cond a comparison of the private integer local x with a constant that
    holds for x == K: the loop's first test is known to pass."""
    t = ctx.toks
    if prev is None or prev.kind != 'stmt' or cond is None: return False
    e = ctx.stmt_expr(prev)
    if e is None or e.kind != 'assign' or e.op != '=' or e.kids[0].kind != 'atom': return False
    x = t[e.kids[0].start][1]
    return _true_for(ctx, x, e.kids[1], cond)


def _true_for(ctx, x, init, cond):
    t = ctx.toks
    if x not in _private(ctx) or _tkind(ctx, _type_key(ctx, x)) is None or x in _array_params(ctx): return False
    K = permc.const_value(init, t)
    if K is None or not 0 <= K <= 127: return False
    cond = _unparen(cond)
    if cond.kind != 'bin' or cond.op not in NEG: return False
    l, r = cond.kids
    lx = l.kind == 'atom' and t[l.start][1] == x
    rx = r.kind == 'atom' and t[r.start][1] == x
    if lx == rx: return False
    v = permc.const_value(r if lx else l, t)
    if v is None or not 0 <= v < 1 << 31: return False
    a, b = (K, v) if lx else (v, K)
    return {'<': a < b, '>': a > b, '<=': a <= b, '>=': a >= b, '==': a == b, '!=': a != b}[cond.op]


def _const_true(ctx, a, b):
    e = ctx.expr(a, b) if a < b else None
    v = permc.const_value(e, ctx.toks) if e is not None else None
    return v is not None and v != 0


def r_loop_test(ctx, rng, everywhere=False):
    """D34 (new kind): where a loop is tested. `while (c) B` against `if (c) do B while (c);` (always
    the same program); `do B while (c);` against `while (c) B` when `x = K;` stands before it and c
    compares x with a constant so that the first test passes; a conditioned loop against
    `while (1)` / `for (;;)` with `if (!c) break;` first (or, for do-while, last when the body has no
    continue), and back."""
    t = ctx.toks
    cands = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            if n.kind not in ('while', 'for', 'do', 'if'): continue
            prev = kids[k - 1] if k else None
            if n.kind == 'if':
                d = n.then
                if d.kind == 'block' and len(d.children) == 1: d = d.children[0]
                if n.els is None and d.kind == 'do' and [x[1] for x in t[n.cond[0]:n.cond[1] + 1]] == [x[1] for x in t[d.cond[0]:d.cond[1] + 1]]:
                    cands.append(('ifdo2while', n, d, prev))
                continue
            body = n.body
            lp, rp = n.cond
            has_cont = any(t[i][1] == 'continue' for i in range(body.start, body.end))
            dn = _declared(ctx, body)
            first_decl = body.kind == 'block' and body.children and (ctx.is_decl(body.children[0]) or body.children[0].kind in ('label', 'pp'))
            if n.kind == 'while':
                ce = ctx.cond_expr(n)
                if ce is None: continue
                if _const_true(ctx, lp + 1, rp):
                    cands += _break_forms(ctx, n, body, has_cont, dn, True)
                    continue
                cands.append(('while2ifdo', n, None, prev))
                if _first_true(ctx, prev, ce): cands.append(('while2do', n, None, prev))
                if not first_decl and not (_elem_ids(ctx, lp + 1, rp) & dn): cands.append(('while2break', n, None, prev))
            elif n.kind == 'do':
                ce = ctx.cond_expr(n)
                if ce is None: continue
                if _first_true(ctx, prev, ce): cands.append(('do2while', n, None, prev))
                if not has_cont and not (_elem_ids(ctx, lp + 1, rp) & dn) and not _const_true(ctx, lp + 1, rp) \
                        and not (body.kind == 'block' and body.children and body.children[-1].kind == 'label'):
                    cands.append(('do2break', n, None, prev))
            else:
                parts = _split_top(ctx, lp + 1, rp, ';')
                if len(parts) != 3 or any(ctx.types.is_type_word(t[i][1]) for i in range(*parts[0])): continue
                if parts[1][0] == parts[1][1]:
                    cands += _break_forms(ctx, n, body, has_cont, dn, parts[0][0] == parts[0][1] and parts[2][0] == parts[2][1])
                    continue
                ce = ctx.expr(*parts[1])
                if ce is None: continue
                if not first_decl and not (_elem_ids(ctx, *parts[1]) & dn): cands.append(('for2break', n, parts, prev))
                ie = ctx.expr(*parts[0]) if parts[0][0] < parts[0][1] else None
                if ie is not None and ie.kind == 'assign' and ie.op == '=' and ie.kids[0].kind == 'atom' and not has_cont \
                        and _true_for(ctx, t[ie.kids[0].start][1], ie.kids[1], ce) \
                        and not (body.kind == 'block' and body.children and body.children[-1].kind == 'label') \
                        and not (_elem_ids(ctx, *parts[2]) & dn):
                    cands.append(('for2do', n, parts, prev))
    if not cands: return None
    op, n, x, prev = rng.choice(cands)
    ind = ctx.indent(n.start)
    S = ctx.src
    body = n.body if n.kind != 'if' else x.body
    btxt = S(body.start, body.end)

    def with_first(stmt):
        if body.kind == 'block':
            if not body.children: return '{ ' + stmt + ' }'
            c0 = body.children[0]
            return S(body.start, c0.start) + t[c0.start][2] + stmt + permc.render(t[c0.start:body.end])
        return '{\n' + ind + '    ' + stmt + '\n' + ind + '    ' + btxt + '\n' + ind + '}'

    def with_last(stmt):
        if body.kind == 'block':
            inner = ctx.indent(body.children[-1].start) if body.children else ind + '    '
            return S(body.start, body.end - 1) + '\n' + inner + stmt + permc.render(t[body.end - 1:body.end])
        return '{\n' + ind + '    ' + btxt + '\n' + ind + '    ' + stmt + '\n' + ind + '}'

    def without(c):
        return S(body.start, c.start) + permc.render(t[c.end:body.end])

    a = n.start
    if op == 'ifdo2while':
        text = f'while ({S(n.cond[0] + 1, n.cond[1])}) ' + btxt
    elif op == 'while2ifdo':
        c = S(n.cond[0] + 1, n.cond[1])
        text = f'if ({c}) do {btxt} while ({c});'
    elif op == 'while2do':
        text = f'do {btxt} while ({S(n.cond[0] + 1, n.cond[1])});'
    elif op == 'do2while':
        text = f'while ({S(n.cond[0] + 1, n.cond[1])}) ' + btxt
    elif op == 'while2break':
        kw = 'while (1)' if rng.random() < 0.6 else 'for (;;)'
        text = kw + ' ' + with_first(f'if ({_neg(ctx, ctx.cond_expr(n))}) break;')
    elif op == 'do2break':
        kw = 'while (1)' if rng.random() < 0.6 else 'for (;;)'
        text = kw + ' ' + with_last(f'if ({_neg(ctx, ctx.cond_expr(n))}) break;')
    elif op == 'for2break':
        text = f'for ({S(*x[0])}; ; {S(*x[2])}) ' + with_first(f'if ({_neg(ctx, ctx.expr(*x[1]))}) break;')
    elif op == 'for2do':
        step = S(*x[2])
        text = S(*x[0]) + ';\n' + ind + 'do ' + (with_last(step + ';') if step else btxt) + f' while ({S(*x[1])});'
    elif op == 'break2cond':
        c = _neg(ctx, ctx.cond_expr(x))
        if n.kind == 'while': text = f'while ({c}) ' + without(x)
        else:
            parts = _split_top(ctx, n.cond[0] + 1, n.cond[1], ';')
            text = f'for ({S(*parts[0])}; {c}; {S(*parts[2])}) ' + without(x)
    else:                                                               # break2do
        text = 'do ' + without(x) + f' while ({_neg(ctx, ctx.cond_expr(x))});'
    return Edit([(a, n.end, permc.new(text, t[a][2]))], f'loop test {op} @{_line(ctx, n.start)}')


def _break_forms(ctx, n, body, has_cont, dn, bare):
    """The ways an unconditioned loop n whose body starts or ends with `if (X) break;` can be given a condition."""
    t = ctx.toks
    out = []
    if body.kind != 'block' or len(body.children) < 2: return out
    for c, op in ((body.children[0], 'break2cond'), (body.children[-1], 'break2do')):
        if c.kind != 'if' or c.els is not None: continue
        th = c.then
        if th.kind == 'block' and len(th.children) == 1: th = th.children[0]
        if not (th.kind == 'stmt' and t[th.start][1] == 'break'): continue
        if _elem_ids(ctx, c.cond[0], c.cond[1]) & dn or ctx.cond_expr(c) is None: continue
        if op == 'break2do' and (has_cont or not bare or body.children[-2].kind == 'label'): continue
        out.append((op, n, c, None))
    return out

# ---- locals joined, split and reused (B8, B9)

def _plain_local(ctx, name):
    """A local that is one object with no surprises: declared once, no array, address never taken, not
    static, volatile, extern or const, and its name means nothing else in FUNC."""
    l = ctx.locals.get(name)
    if l is None or l.dims or name in ctx.dup_names or name in _addr_names(ctx) or name in ctx.params: return False
    t = ctx.toks
    if any(not (l.block.start < j < l.block.end) for j in ctx.ids.get(name, ())): return False      # the name is also something else
    return not any(t[i][1] in ('static', 'volatile', 'extern', 'const') for i in range(l.node.start, l.node.end))


def _type_key(ctx, name):
    l = ctx.locals.get(name)
    if l is not None: return (l.type + ' ' + l.stars).strip()
    p = ctx.params.get(name)
    return ' '.join(p.split()) if p else None


def _occ(ctx, name):
    """Token indexes of name in FUNC, without its declarator; and whether the declaration gives a value."""
    l = ctx.locals.get(name)
    occ = ctx.ids.get(name, [])
    if l is None: return list(occ), True
    d = next((i for i in range(l.node.start, l.node.end) if ctx.toks[i][1] == name), None)
    return [j for j in occ if j != d and ctx.toks[j - 1][1] not in ('.', '->')], l.init is not None


def _loops(ctx):
    c = getattr(ctx, '_lp', None)
    if c is None: c = ctx._lp = [(n.start, n.end) for n in ctx.nodes if n.kind in ('for', 'while', 'do')]
    return c


def _goto_labels(ctx):
    c = getattr(ctx, '_gl', None)
    if c is None: c = ctx._gl = [n.start for n in ctx.nodes if n.kind == 'label' and n.extra == 'goto']
    return c


def _plain_def(ctx, n, name):
    """Child statement n begins by storing into name a value that does not read it: `name = e;`, or a
    for whose initialiser holds `name = e` (and no other mention of name)."""
    t = ctx.toks
    if n.kind == 'stmt':
        if t[n.start][1] != name or t[n.start + 1][1] != '=': return False
        return all(t[i][1] != name for i in range(n.start + 2, n.end))
    if n.kind == 'for':
        lp, rp = n.cond
        parts = _split_top(ctx, lp + 1, rp, ';')
        if len(parts) != 3: return False
        occ = [i for i in range(*parts[0]) if t[i][1] == name]
        return len(occ) == 1 and t[occ[0] + 1][1] == '=' and t[occ[0] - 1][1] in ('(', ',')
    return False


def _regions(ctx, name):
    """Def-use regions of name: [(a, b, block, first child index, last child index, bounded)]. A region
    starts at a child statement that plainly defines name and holds every use its values can reach:
    up to the next plain definition among the later siblings (bounded), or every later mention when
    none of them is outside the block or in an enclosing loop outside the region. No label lies in a
    region, so it is entered only through its first statement."""
    c = ctx.__dict__.setdefault('_rg', {})
    if name in c: return c[name]
    occ, _ = _occ(ctx, name)
    out = []
    labels = _goto_labels(ctx)
    for blk in permc.blocks(ctx.root):
        if not any(blk.start < j < blk.end for j in occ): continue
        kids = blk.children
        for i, n in enumerate(kids):
            if not _plain_def(ctx, n, name): continue
            j = i + 1
            while j < len(kids) and not _plain_def(ctx, kids[j], name): j += 1
            bounded = j < len(kids)
            lim = kids[j].start if bounded else blk.end
            inside = [o for o in occ if n.start <= o < lim]
            last = i
            for q in range(i, j):
                if any(kids[q].start <= o < kids[q].end for o in inside): last = q
            seg = kids[i:last + 1]
            if any(x.kind in ('label', 'pp') for x in seg): continue
            if bounded:
                # values leave only by falling into the next definition (a return ends the local)
                if any(_escapes(ctx, x, ret=False) for x in kids[i:j]) or any(x.kind in ('label', 'pp') for x in kids[i:j]): continue
            else:
                if any(_has_goto(ctx, x) for x in seg): continue
                if any(o >= blk.end for o in occ): continue
                if any(ls <= n.start < le and any(ls <= o < le and not (n.start <= o < lim) for o in occ) for ls, le in _loops(ctx)): continue
                if any(p < n.start for p in labels): continue
            out.append((n.start, kids[last].end, blk, i, last, bounded))
    c[name] = out
    return out


def _has_goto(ctx, n):
    """A goto, a goto label, or a case label that is not inside a switch of n (break and continue are allowed)."""
    return _escapes(ctx, n, True, False, False)


def _drop_declarator(ctx, l):
    """Span edit removing local l's declarator (the whole statement when it is the only one)."""
    t = ctx.toks
    n = l.node
    if l.single: return (n.start, n.end, [])
    k, ds = _declarators(ctx, n)
    keep = [ctx.src(a, b) for a, b, nm in ds if nm != l.name]
    return (n.start, n.end, permc.new(ctx.src(n.start, k) + ' ' + ', '.join(keep) + ';', t[n.start][2]))


def _decl_after(ctx, name, text):
    """Span edit declaring text right after name's declaration (a parameter: first in the body)."""
    t = ctx.toks
    l = ctx.locals.get(name)
    if l is None:
        i = ctx.lb
        return (i, i + 1, [t[i]] + permc.new(text, '\n    '))
    i = l.node.end - 1
    return (i, i + 1, [t[i]] + permc.new(text, '\n' + ctx.indent(l.node.start)))


def _rename(ctx, occ, new):
    t = ctx.toks
    return [(j, j + 1, [Tok('id', new, t[j][2])]) for j in occ]


def _splittable(ctx):
    c = getattr(ctx, '_spl', None)
    if c is None:
        ad = _addr_names(ctx)
        c = [n for n in ctx.locals if _plain_local(ctx, n)]
        c += [n for n in ctx.params if n not in ad and n not in ctx.locals and '(' not in ctx.params[n] and '[' not in ctx.params[n]
              and n not in _array_params(ctx)]
        ctx._spl = c = [n for n in c if len(ctx.ids.get(n, ())) >= 2]
    return c


def r_local_split(ctx, rng, everywhere=False):
    """B8 (new kind), split: a local (or parameter) that is given a fresh value at a statement
    `x = e;` or `for (x = e; ...)` becomes two locals: the uses that value reaches take a new local of
    the same type. Needs a def-use region (see _regions) and a use of x outside it."""
    names = _splittable(ctx)
    if not names: return None
    for _ in range(8):
        x = rng.choice(names)
        occ, _ = _occ(ctx, x)
        rs = [r for r in _regions(ctx, x) if any(not (r[0] <= o < r[1]) for o in occ)]
        if not rs: continue
        a, b, blk, i, last, bounded = rng.choice(rs)
        new = ctx.fresh('pmt_s')
        l = ctx.locals.get(x)
        decl = f'{l.type} {l.stars}{new};' if l else f'{ctx.params[x]} {new};'
        spans = _rename(ctx, [o for o in occ if a <= o < b], new) + [_decl_after(ctx, x, decl)]
        return Edit(spans, f'split local {x} at line {_line(ctx, a)}')
    return None


def _dead_over(ctx, y, region):
    """Local y holds nothing that is needed while or after the region runs: it is not mentioned in the
    region, and either every mention is before the region with no way back to it (no enclosing loop
    that mentions y, no goto label before the region), or the first later sibling that mentions y
    plainly redefines it and the region is left only by falling out of its end (or a return)."""
    a, b, blk, i, last, bounded = region
    occ, init = _occ(ctx, y)
    l = ctx.locals[y]
    if any(a <= o < b for o in occ) or a <= l.node.start < b: return False
    back = any(ls <= a < le and any(ls <= o < le for o in occ) for ls, le in _loops(ctx)) or any(p < a for p in _goto_labels(ctx))
    if all(o < a for o in occ) and not back: return True
    kids = blk.children
    if any(_escapes(ctx, x, ret=False) for x in kids[i:last + 1]): return False
    for q in range(last + 1, len(kids)):
        if any(kids[q].start <= o < kids[q].end for o in occ):
            return _plain_def(ctx, kids[q], y) and kids[q].kind != 'label'
        if kids[q].kind in ('label', 'pp') or _escapes(ctx, kids[q], ret=False): return False
    return False


def _encloses(outer, inner):
    return any(b is outer for b in _ancestors(inner))


def r_local_reuse(ctx, rng, everywhere=False):
    """B9 (new kind): over one def-use region of a local (or parameter) x, the value is kept in another
    existing local of the same type that is dead there (the vendor habit of one temporary for several
    jobs). x's declaration goes when nothing else mentions it."""
    names = _splittable(ctx)
    if not names: return None
    for _ in range(10):
        x = rng.choice(names)
        key = _type_key(ctx, x)
        rs = _regions(ctx, x)
        if not rs: continue
        r = rng.choice(rs)
        a, b, blk = r[0], r[1], r[2]
        ys = [y for y in ctx.locals if y != x and _type_key(ctx, y) == key and _plain_local(ctx, y)
              and _encloses(ctx.locals[y].block, blk) and ctx.locals[y].node.end <= a and _dead_over(ctx, y, r)]
        if not ys: continue
        y = rng.choice(ys)
        occ, init = _occ(ctx, x)
        inside = [o for o in occ if a <= o < b]
        spans = _rename(ctx, inside, y)
        l = ctx.locals.get(x)
        if l is not None and not init and len(inside) == len(occ): spans.append(_drop_declarator(ctx, l))
        return Edit(spans, f'reuse local {y} for {x} at line {_line(ctx, a)}')
    return None


def r_local_merge(ctx, rng, everywhere=False):
    """B8 (new kind), merge: two locals of the same type whose lives do not overlap become one. Every
    mention of the first comes before every mention of the second, no loop holds mentions of both, no
    goto label lies before the first's last mention, and the second has no initialiser."""
    c = getattr(ctx, '_mg', None)
    if c is None:
        by = {}
        for n in ctx.locals:
            if _plain_local(ctx, n) and len(ctx.ids.get(n, ())) >= 2: by.setdefault(_type_key(ctx, n), []).append(n)
        c = ctx._mg = [g for g in by.values() if len(g) >= 2]
    if not c: return None
    for _ in range(12):
        a, b = rng.sample(rng.choice(c), 2)
        oa, ia = _occ(ctx, a)
        ob, ib = _occ(ctx, b)
        if not oa or not ob: continue
        if min(oa) > min(ob): a, b, oa, ob, ia, ib = b, a, ob, oa, ib, ia
        la, lb_ = ctx.locals[a], ctx.locals[b]
        if ib or max(oa) >= min(ob) or (ia and la.node.start > min(ob)): continue
        if any(any(ls <= o < le for o in oa) and any(ls <= o < le for o in ob) for ls, le in _loops(ctx)): continue
        if any(p <= max(oa) for p in _goto_labels(ctx)): continue
        ways = []
        if _encloses(la.block, lb_.block) and la.node.end <= min(ob): ways.append((a, b, ob, lb_))
        if _encloses(lb_.block, la.block) and not ia and lb_.node.end <= min(oa): ways.append((b, a, oa, la))
        if not ways: continue
        keep, drop, occ, dl = rng.choice(ways)
        return Edit(_rename(ctx, occ, keep) + [_drop_declarator(ctx, dl)], f'merge local {drop} into {keep}')
    return None

# ---- D11 assignment inside an expression

def _root_expr(ctx, n):
    """The expression a statement evaluates first and once: an expression statement's, a return's, an
    if's or a switch's condition."""
    t = ctx.toks
    if n.kind == 'stmt':
        e = ctx.stmt_expr(n)
        if e is None and t[n.start][1] == 'return' and n.end - n.start > 2: e = ctx.expr(n.start + 1, n.end - 1)
        return e
    if n.kind in ('if', 'switch'): return ctx.cond_expr(n)
    return None


def _seq_path(ctx, root, target):
    """Token ranges of the operands evaluated in no fixed order with target (a sub-expression of
    root), or None when target may not be evaluated at all, or only after another operand (the right
    side of && || ?: or a comma), or sits in a sizeof."""
    sib = []
    e = root
    while e is not target:
        nxt = next((k for k in e.kids if k.start <= target.start and target.end <= k.end), None)
        if nxt is None or e.kind == 'sizeof': return None
        if e.kind in ('cond', 'comma') or (e.kind == 'bin' and e.op in _SEQ_OPS):
            if nxt is not e.kids[0]: return None
        else:
            for i, k in enumerate(e.kids):
                if k is nxt or (e.kind == 'call' and i == 0 and k.kind == 'atom'): continue
                sib.append((k.start, k.end))
        e = nxt
    return sib


def _pure_outside(ctx, root, x):
    """Nothing in root outside x stores, steps or calls, except the operators x is an operand of."""
    for a in permc.ewalk(root):
        if a.kind in ('assign', 'call', 'postfix') or (a.kind == 'unary' and a.op in ('++', '--')):
            if a.start <= x.start and x.end <= a.end and a is not x: continue          # an ancestor: it runs after x
            if x.start <= a.start and a.end <= x.end: continue
            return False
    return True


def _is_macro(ctx, name):
    return name in _all_macros(ctx) and name not in ctx.locals and name not in ctx.params


def _vol_name(ctx, name):
    return name in _vols(ctx)


def r_embed_assign(ctx, rng, everywhere=False):
    """D11: `k = v; f(k);` against `f(k = v);` and `x = e; if (x > 3)` against `if ((x = e) > 3)`, both
    ways. The statement after the store must read k exactly once, at a place evaluated always and
    before any sequence point of that expression, and the store must commute with the operands
    evaluated around it; k is not volatile."""
    t = ctx.toks
    cands = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            root = _root_expr(ctx, n)
            if root is None: continue
            if k > 0 and kids[k - 1].kind == 'stmt':
                s = kids[k - 1]
                e = ctx.stmt_expr(s)
                if e is not None and e.kind == 'assign' and e.op == '=' and e.kids[0].kind == 'atom' and e.kids[0].op == 'id':
                    x = t[e.kids[0].start][1]
                    occ = [a for a in permc.ewalk(root) if a.kind == 'atom' and a.op == 'id' and t[a.start][1] == x]
                    if len(occ) == 1 and not _is_macro(ctx, x) and not _vol_name(ctx, x):
                        g = occ[0]
                        if not (t[g.end][1] in permc.ASSIGN or t[g.end][1] in ('++', '--', '.', '[', '(')
                                or t[g.start - 1][1] in ('++', '--', '&', '.', '->')):
                            sib = _seq_path(ctx, root, g)
                            fs = _nfx(ctx, s)
                            if sib is not None and all(_commute(fs, _fx(ctx, a, b)) for a, b in sib):
                                cands.append(('in', s, n, g, e.kids[1]))
            for a in permc.ewalk(root):
                if a.kind != 'assign' or a.op != '=' or a is root or a.kids[0].kind != 'atom' or a.kids[0].op != 'id': continue
                x = t[a.kids[0].start][1]
                if _is_macro(ctx, x) or _vol_name(ctx, x) or a.start - 1 <= n.start: continue
                sib = _seq_path(ctx, root, a)
                fa = _fx(ctx, a.start, a.end)
                if sib is not None and all(_commute(fa, _fx(ctx, p, q)) for p, q in sib):
                    cands.append(('out', None, n, a, None))
    if not cands: return None
    op, s, n, g, rhs = rng.choice(cands)
    if op == 'in':
        x = t[g.start][1]
        v = ctx.src(rhs.start, rhs.end)
        rep = f'{x} = ({v})' if rhs.kind == 'comma' else f'{x} = {v}'
        if not (t[g.start - 1][1] in ('(', ',') and t[g.end][1] in (',', ')') and _in_call_args(ctx, g)): rep = f'({rep})'
        return Edit([(s.start, s.end, []), (g.start, g.end, permc.new(rep, t[g.start][2]))], f'store {x} inside line {_line(ctx, n.start)}')
    x = t[g.kids[0].start][1]
    a, b = g.start, g.end
    if t[a - 1][1] == '(' and ctx.pair.get(a - 1) == b and not (t[a - 2][0] == 'id' and t[a - 2][1] not in permc.KEYWORDS): a, b = a - 1, b + 1
    return Edit([ctx.insert_at(n.start, ctx.src(g.start, g.end) + ';'), (a, b, permc.new(x, t[a][2]))],
                f'store {x} before line {_line(ctx, n.start)}')


# ---- D15 abs

def _include_span(ctx, name, header):
    """A span edit adding `#include <header>` unless name is mentioned before FUNC or the include is
    there; None when nothing is needed."""
    t = ctx.toks
    last = None
    for i in range(ctx.start):
        if t[i][0] == 'pp':
            if re.match(r'#\s*include\b', t[i][1]):
                if header in t[i][1]: return None
                last = i
        elif t[i][1] == name: return None
    line = Tok('pp', f'#include <{header}>', '\n')
    if last is not None: return (last, last + 1, [t[last], line])
    ws = t[0][2]
    if ws and not ws.endswith('\n'): ws += '\n'
    return (0, 1, [Tok('pp', line[1], ws), Tok(t[0][0], t[0][1], '\n')])


def _abs_ok(ctx, e):
    """abs(e) and e < 0 ? -e : e are the same: e is a side-effect-free integer that is not a 32-bit
    unsigned (abs takes an int) and not volatile."""
    k = _ikind(ctx, e)
    f = _fx(ctx, e.start, e.end)
    return k is not None and k != (32, False) and not permc.has_side_effects(e, ctx.toks) and not f.vol and not f.call


def _unparen(e):
    while e.kind == 'paren' and e.kids[0].kind != 'comma': e = e.kids[0]
    return e


def _eq(ctx, a, b):
    a, b = _unparen(a), _unparen(b)
    return [x[1] for x in ctx.toks[a.start:a.end]] == [x[1] for x in ctx.toks[b.start:b.end]]


def _neg_select(ctx, e):
    """The operand E when e is `E < 0 ? -E : E` (or the >= / > spellings), else None."""
    t = ctx.toks
    if e.kind != 'cond': return None
    c, x, y = (_unparen(k) for k in e.kids)
    if c.kind != 'bin' or permc.const_value(c.kids[1], t) != 0 or t[c.kids[1].start][0] != 'num': return None
    if c.op in ('>=', '>'): x, y = y, x
    elif c.op != '<': return None
    if x.kind == 'unary' and x.op == '-' and _eq(ctx, x.kids[0], y) and _eq(ctx, c.kids[0], y): return y
    return None


def r_abs_form(ctx, rng, everywhere=False):
    """D15: `abs(E)` against `E < 0 ? -E : E`, and the statements `x = abs(x);` / `x = x < 0 ? -x : x;`
    against `if (x < 0) x = -x;`. E is a side-effect-free integer no wider than int and not unsigned
    int. `#include <stdlib.h>` is added when abs is not declared in the file."""
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        root = _root_expr(ctx, n) if n.kind != 'if' else ctx.cond_expr(n)
        if root is not None:
            for a in permc.ewalk(root):
                if a.kind == 'call' and len(a.kids) == 2 and a.kids[0].kind == 'atom' and t[a.kids[0].start][1] == 'abs' and _abs_ok(ctx, a.kids[1]):
                    cands.append(('to_sel', a, a.kids[1], n))
                if a.kind == 'cond':
                    y = _neg_select(ctx, a)
                    if y is not None and _abs_ok(ctx, y): cands.append(('to_abs', a, y, n))
        if n.parent is None or n.parent.kind != 'block': continue
        if n.kind == 'stmt' and root is not None and root.kind == 'assign' and root.op == '=' and permc.is_lvalue_simple(root.kids[0], t):
            r = _unparen(root.kids[1])
            y = r.kids[1] if (r.kind == 'call' and len(r.kids) == 2 and r.kids[0].kind == 'atom' and t[r.kids[0].start][1] == 'abs') else _neg_select(ctx, r)
            if y is not None and _eq(ctx, y, root.kids[0]) and _abs_ok(ctx, y): cands.append(('to_if', root, y, n))
        if n.kind == 'if' and n.els is None:
            c = ctx.cond_expr(n)
            th = n.then
            if th.kind == 'block' and len(th.children) == 1: th = th.children[0]
            e = ctx.stmt_expr(th) if th.kind == 'stmt' else None
            if c is not None and e is not None and e.kind == 'assign' and e.op == '=':
                c = _unparen(c); v = _unparen(e.kids[1])
                if c.kind == 'bin' and c.op == '<' and permc.const_value(c.kids[1], t) == 0 and t[c.kids[1].start][0] == 'num' \
                        and v.kind == 'unary' and v.op == '-' and _eq(ctx, v.kids[0], e.kids[0]) and _eq(ctx, c.kids[0], e.kids[0]) \
                        and permc.is_lvalue_simple(e.kids[0], t) and _abs_ok(ctx, e.kids[0]):
                    cands.append(('from_if', None, e.kids[0], n))
    if not cands: return None
    op, a, y, n = rng.choice(cands)
    ys = ctx.src(_unparen(y).start, _unparen(y).end)
    yp = paren(ctx, _unparen(y))
    sel = f'{yp} < 0 ? -{yp} : {yp}'
    inc = None
    if op == 'to_sel':
        spans = [(a.start, a.end, permc.new(wrap(ctx, a, sel), t[a.start][2]))]
    elif op == 'to_abs':
        inc = _include_span(ctx, 'abs', 'stdlib.h')
        spans = [(a.start, a.end, permc.new(f'abs({ys})', t[a.start][2]))]
    elif op == 'to_if':
        spans = [(n.start, n.end, permc.new(f'if ({yp} < 0) {ys} = -{yp};', t[n.start][2]))]
    else:
        if rng.random() < 0.5:
            inc = _include_span(ctx, 'abs', 'stdlib.h')
            spans = [(n.start, n.end, permc.new(f'{ys} = abs({ys});', t[n.start][2]))]
        else:
            spans = [(n.start, n.end, permc.new(f'{ys} = {sel};', t[n.start][2]))]
    if inc: spans.append(inc)
    return Edit(spans, f'abs form {op} of {ys[:30]} @{_line(ctx, n.start)}')


# ---- D27 one expression against several statements

_SPLIT_OPS = {'=': None, '+=': '+-', '-=': '+-', '|=': '|', '&=': '&', '^=': '^'}
_FLIP = {'+': '-', '-': '+'}


def _op_chain(e, ops):
    """[(operator before the term or None, term)] of the left-associated chain of ops at the top of e."""
    out = []
    while e.kind == 'bin' and e.op in ops:
        out.append((e.op, e.kids[1])); e = e.kids[0]
    out.append((None, e)); out.reverse()
    return out


def _opnd(ctx, e, op, right=False):
    """Source of e as an operand of binary op: parenthesised only when the grammar needs it."""
    e = _unparen(e)
    s = ctx.src(e.start, e.end)
    if e.kind in ('assign', 'cond', 'comma'): return f'({s})'
    if e.kind == 'bin':
        p, q = permc.PREC[e.op], permc.PREC[op]
        if p < q or (right and p == q): return f'({s})'
    return s


def _split_site(ctx, n):
    """(assign, lhs text, [(op, term)]) when statement n is `L = t1 op t2 ...` or `L op= t1 + t2 ...` that
    can be written as one statement per term: integers of at most 32 bits throughout (the arithmetic is
    then the same modulo 2^32 however it is grouped), terms without side effects, and no term after
    the first reading what the statement stores."""
    t = ctx.toks
    e = ctx.stmt_expr(n)
    if e is None or e.kind != 'assign' or e.op not in _SPLIT_OPS or n.parent is None or n.parent.kind != 'block': return None
    lhs, rhs = e.kids[0], _unparen(e.kids[1])
    if rhs.kind != 'bin' or not permc.is_lvalue_simple(lhs, t) or _ikind(ctx, lhs) is None: return None
    ops = _SPLIT_OPS[e.op] or ('+-' if rhs.op in '+-' else rhs.op if rhs.op in ('|', '&', '^') else None)
    if ops is None or rhs.op not in ops: return None
    terms = _op_chain(rhs, ops)
    if len(terms) < 2 or not _terms_ok(ctx, n, lhs, [x for _, x in terms], 1): return None
    return e, terms


def _terms_ok(ctx, n, lhs, terms, first_free):
    t = ctx.toks
    W = _fx(ctx, lhs.start, lhs.end + 1)                 # the lvalue with its assignment operator: a store to it
    if W.vol or W.call: return False
    for i, x in enumerate(terms):
        if _ikind(ctx, x) is None or permc.has_side_effects(x, t): return False
        f = _fx(ctx, x.start, x.end)
        if f.vol or f.call: return False
        if i >= first_free and not _store_indep(W, f): return False
    return True


def r_expr_split(ctx, rng, everywhere=False):
    """D27: `s += a + b + c;` against `s += a; s += b; s += c;` and `v = a | b << 8;` against
    `v = a; v |= b << 8;` (also - & ^), both ways. Everything is an integer of at most 32 bits, the
    terms have no side effects and (after the first) do not read what is stored; nothing is volatile."""
    t = ctx.toks
    cands = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            if n.kind != 'stmt': continue
            s = _split_site(ctx, n)
            if s: cands.append(('split', n, s))
            if k + 1 < len(kids) and kids[k + 1].kind == 'stmt':
                e1, e2 = ctx.stmt_expr(n), ctx.stmt_expr(kids[k + 1])
                if e1 is not None and e2 is not None and e1.kind == 'assign' and e1.op in ('=', '+=', '-=') and e2.kind in ('postfix', 'unary') \
                        and e2.op in ('++', '--') and _eq(ctx, e1.kids[0], e2.kids[0]) and permc.is_lvalue_simple(e1.kids[0], t) \
                        and _ikind(ctx, e1.kids[0]) is not None and _terms_ok(ctx, n, e1.kids[0], [e1.kids[1]], 1):
                    cands.append(('merge1', n, (kids[k + 1], e1, e2)))          # `i = g >> 4; i++;` against `i = (g >> 4) + 1;`
                if e1 is None or e2 is None or e1.kind != 'assign' or e2.kind != 'assign' or not _eq(ctx, e1.kids[0], e2.kids[0]): continue
                if e1.op not in _SPLIT_OPS or e2.op not in _SPLIT_OPS or e2.op == '=': continue
                o2 = e2.op[0]
                if e1.op != '=' and not (e1.op[0] in '+-' and o2 in '+-' or e1.op == e2.op): continue
                lhs = e1.kids[0]
                if not permc.is_lvalue_simple(lhs, t) or _ikind(ctx, lhs) is None: continue
                if not _terms_ok(ctx, n, lhs, [e1.kids[1], e2.kids[1]], 1): continue
                cands.append(('merge', n, (kids[k + 1], e1, e2)))
    if not cands: return None
    op, n, s = rng.choice(cands)
    ind = ctx.indent(n.start)
    if op == 'split':
        e, terms = s
        L = ctx.src(e.kids[0].start, e.kids[0].end)
        cut = 1 if rng.random() < 0.6 else rng.randrange(1, len(terms))
        head = ctx.src(terms[0][1].start, terms[cut - 1][1].end)
        if cut == 1: head = ctx.src(_unparen(terms[0][1]).start, _unparen(terms[0][1]).end)
        out = [f'{L} {e.op} {head};']
        for o, x in terms[cut:]:
            if e.op == '-=': o = _FLIP[o]
            x = _unparen(x)
            if o in '+-' and x.kind == 'atom' and t[x.start][1] == '1' and rng.random() < 0.5: out.append(f'{L}{o}{o};')
            else: out.append(f'{L} {o}= {ctx.src(x.start, x.end)};')
        return Edit([(n.start, n.end, permc.new(('\n' + ind).join(out), t[n.start][2]))], f'split {L} {e.op} into {len(out)} statements @{_line(ctx, n.start)}')
    m, e1, e2 = s
    L = ctx.src(e1.kids[0].start, e1.kids[0].end)
    if op == 'merge1':
        o = e2.op[0] if e1.op != '-=' else _FLIP[e2.op[0]]
        text = f'{L} {e1.op} {_opnd(ctx, e1.kids[1], o)} {o} 1;'
        return Edit([(n.start, m.end, permc.new(text, t[n.start][2]))], f'join the step into {L} {e1.op} @{_line(ctx, n.start)}')
    o = e2.op[0]
    if e1.op == '-=': o = _FLIP[o]
    text = f'{L} {e1.op} {_opnd(ctx, e1.kids[1], o)} {o} {_opnd(ctx, e2.kids[1], o, True)};'
    return Edit([(n.start, m.end, permc.new(text, t[n.start][2]))], f'join two statements into {L} {e1.op} @{_line(ctx, n.start)}')


# ---- D24 direct return against a result local

def _ret_type(ctx):
    t = ctx.toks
    at = next((i for i in range(ctx.start, ctx.lb) if t[i][1] == ctx.func and t[i + 1][1] == '('), None)
    if at is None: return None
    ws = [t[i] for i in range(ctx.start, at) if t[i][1] not in ('static', 'extern', 'inline', '__inline', '__forceinline', '__weak',
                                                                 'register', 'const', 'volatile', '__irq')]
    if not ws or any(w[0] != 'id' and w[1] != '*' for w in ws): return None
    ty = ' '.join(w[1] for w in ws)
    return None if ty == 'void' else ty


def r_return_local(ctx, rng, everywhere=False):
    """D24: `return E;` against `r = E; return r;` through a local of the function's return type (a new
    one, or any plain local of that type: it is dead after the return), and back: `r = E; return r;`
    becomes `return E;` (with a cast to r's type when that is another integer type)."""
    t = ctx.toks
    rt = _ret_type(ctx)
    if rt is None: return None
    ins, outs = [], []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            if n.kind != 'stmt' or t[n.start][1] != 'return' or n.end - n.start <= 2: continue
            e = ctx.expr(n.start + 1, n.end - 1)
            if e is None: continue
            p = kids[k - 1] if k else None
            pe = ctx.stmt_expr(p) if p is not None and p.kind == 'stmt' else None
            if e.kind == 'atom' and pe is not None and pe.kind == 'assign' and pe.op == '=' and pe.kids[0].kind == 'atom' \
                    and t[pe.kids[0].start][1] == t[e.start][1] and _plain_local(ctx, t[e.start][1]) \
                    and t[e.start][1] not in permc.idents(pe.kids[1], t):
                outs.append((p, n, pe))
            elif not (e.kind == 'atom' and t[e.start][1] in ctx.locals):
                ins.append((n, e))
    for n in ctx.nodes:                     # a return that is an unbraced arm
        if n.kind == 'stmt' and t[n.start][1] == 'return' and n.end - n.start > 2 and n.parent is not None and n.parent.kind != 'block':
            e = ctx.expr(n.start + 1, n.end - 1)
            if e is not None and not (e.kind == 'atom' and t[e.start][1] in ctx.locals): ins.append((n, e))
    if outs and (not ins or rng.random() < 0.5):
        p, n, pe = rng.choice(outs)
        r = t[pe.kids[0].start][1]
        l = ctx.locals[r]
        v = pe.kids[1]
        key = _type_key(ctx, r)
        if key == rt: text = ctx.src(v.start, v.end)
        elif _tkind(ctx, key) is not None: text = f'({key}){paren(ctx, v)}'
        else: return None
        spans = [(p.start, n.end, permc.new(f'return {text};', t[p.start][2]))]
        occ, init = _occ(ctx, r)
        if not init and all(p.start <= o < n.end for o in occ): spans.append(_drop_declarator(ctx, l))
        return Edit(spans, f'return the value of {r} directly @{_line(ctx, n.start)}')
    if not ins: return None
    n0, _ = rng.choice(ins)
    sites = ins if everywhere else [(n0, _)]
    made = [x for x in ctx.locals if re.match(r'pmt_r\d+$', x) and _type_key(ctx, x) == rt and _plain_local(ctx, x)
            and ctx.locals[x].block is ctx.root]
    own = [x for x in ctx.locals if _type_key(ctx, x) == rt and _plain_local(ctx, x) and ctx.locals[x].block is ctx.root]
    spans = []
    if made: r = made[0]
    elif own and rng.random() < 0.5: r = rng.choice(own)
    else:
        r = ctx.fresh('pmt_r')
        spans.append((ctx.lb, ctx.lb + 1, [t[ctx.lb]] + permc.new(f'{rt} {r};', '\n    ')))
    for n, e in sites:
        text = f'{r} = {ctx.src(e.start, e.end)}; return {r};'
        if n.parent.kind != 'block': text = '{ ' + text + ' }'
        spans.append((n.start, n.end, permc.new(text, t[n.start][2])))
    return Edit(spans, f'return through local {r} at {len(sites)} return{"s" if len(sites) > 1 else ""} @{_line(ctx, n0.start)}')


# ---- D26 pointer-base arithmetic

def _pointee(ctx, name):
    """(element type text, is an array object) of an identifier that is a pointer or a one-dimension
    array of a known type; None otherwise."""
    if not re.fullmatch(r'\w+', name):                       # a member path: its field's declared type
        f = re.findall(r'\w+', name)[-1]
        ft = _field_type(ctx, f) if len(_fields(ctx).get(f, ())) == 1 else None
        if not ft: return None
        if ft.endswith('[]'): return (ft[:-2].strip(), True) if '*' not in ft and next(iter(_fields(ctx)[f]))[1].count('[') == 1 else None
        return (ft[:-1].strip(), False) if ft.endswith('*') and not ft[:-1].strip().endswith('*') else None
    if name in ctx.dup_names: return None
    l = ctx.locals.get(name)
    if l is not None:
        if l.dims and not l.stars and l.dims.count('[') == 1: return l.type, True
        if l.stars == '*' and not l.dims: return l.type, False
        return None
    p = ctx.params.get(name)
    if p is not None:
        p = p.strip()
        if name in _array_params(ctx): return None
        return (p[:-1].strip(), False) if p.endswith('*') and not p[:-1].strip().endswith('*') else None
    g = ctx.types.globals.get(name)
    if g is None or _is_macro(ctx, name): return None
    ty = g[0].replace('volatile ', '').replace('const ', '').strip()
    if g[1]: return (ty, True) if g[1].count('[') == 1 and '*' not in ty else None
    return (ty[:-1].strip(), False) if ty.endswith('*') and not ty[:-1].strip().endswith('*') else None


def _base_off(ctx, e):
    """(base identifier, offset expression or None, cast type text or None) of `&B[K]`, `B + K`, `B`,
    `&B`, `(T *)&B`, `(T *)B`, `&B[0]`."""
    t = ctx.toks
    cast = None
    e = _unparen(e)
    # a member array or pointer (`p->buf`, `s.buf`) can be the base too
    mem = lambda z: z.kind == 'member' and permc.is_lvalue_simple(z, t) and not _fx(ctx, z.start, z.end).vol
    if e.kind == 'cast':
        cast = ' '.join(x[1] for x in t[e.op[0]:e.op[1]]); e = _unparen(e.kids[0])
    if e.kind == 'unary' and e.op == '&':
        k = _unparen(e.kids[0])
        if k.kind == 'index' and k.kids[0].kind == 'atom' and k.kids[0].op == 'id': return t[k.kids[0].start][1], k.kids[1], cast, 'index'
        if k.kind == 'index' and mem(k.kids[0]): return ctx.src(k.kids[0].start, k.kids[0].end), k.kids[1], cast, 'index'
        if k.kind == 'atom' and k.op == 'id': return t[k.start][1], None, cast, 'addr'
        return None
    if e.kind == 'bin' and e.op == '+' and e.kids[0].kind == 'atom' and e.kids[0].op == 'id':
        return t[e.kids[0].start][1], e.kids[1], cast, 'plus'
    if e.kind == 'bin' and e.op == '+' and mem(e.kids[0]): return ctx.src(e.kids[0].start, e.kids[0].end), e.kids[1], cast, 'plus'
    if e.kind == 'atom' and e.op == 'id': return t[e.start][1], None, cast, 'name'
    if mem(e): return ctx.src(e.start, e.end), None, cast, 'name'
    return None


def r_ptr_base(ctx, rng, everywhere=False):
    """D26: `&b[K]` against `b + K` anywhere (always the same), and the statement `p = &b[K];` against
    `p = b; p += K;` or `p = (T *)&b; p += K;` when p points to the element type of b (so the step is
    the same size); K has no side effects and does not read p."""
    t = ctx.toks
    cands = []
    for e in _all_exprs(ctx):
        for x in permc.ewalk(e):
            if x.kind == 'unary' and x.op == '&' and _unparen(x.kids[0]).kind == 'index':
                cands.append(('plus', x, None))
            if x.kind == 'bin' and x.op == '+' and x.kids[0].kind == 'atom' and x.kids[0].op == 'id' \
                    and _pointee(ctx, t[x.kids[0].start][1]) and _ikind(ctx, x.kids[1]) is not None:
                cands.append(('index', x, None))
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            e = ctx.stmt_expr(n) if n.kind == 'stmt' else None
            if e is None or e.kind != 'assign' or e.op != '=' or e.kids[0].kind != 'atom' or e.kids[0].op != 'id': continue
            p = t[e.kids[0].start][1]
            pt = _pointee(ctx, p)
            bo = _base_off(ctx, e.kids[1])
            if pt is None or pt[1] or bo is None or bo[0] == p or _vol_name(ctx, p): continue
            bt = _pointee(ctx, bo[0])
            if bt is None or bt[0] != pt[0] or _vol_name(ctx, bo[0]): continue
            b, K, cast, form = bo
            if cast is not None and cast != f'{pt[0]} *': continue
            if form == 'addr' and not (bt[1] and cast is not None): continue          # &ptr is another thing
            W = _fx(ctx, e.kids[0].start, e.kids[0].end + 1)
            okK = lambda z: not permc.has_side_effects(z, t) and p not in permc.idents(z, t) and _ikind(ctx, z) is not None \
                and _store_indep(W, _fx(ctx, z.start, z.end)) and not _fx(ctx, z.start, z.end).vol
            if K is not None and permc.const_value(K, t) != 0:
                if okK(K): cands.append(('two', n, (p, b, K, bt)))
            elif k + 1 < len(kids) and kids[k + 1].kind == 'stmt':
                e2 = ctx.stmt_expr(kids[k + 1])
                if e2 is not None and e2.kind == 'assign' and e2.op == '+=' and _eq(ctx, e2.kids[0], e.kids[0]) and okK(e2.kids[1]):
                    cands.append(('one', n, (p, b, e2.kids[1], kids[k + 1])))
    if not cands: return None
    op, x, d = rng.choice(cands)
    if op == 'plus':
        ix = _unparen(x.kids[0])
        text = f'{paren(ctx, ix.kids[0])} + {_opnd(ctx, ix.kids[1], "+", True)}'
        return Edit([(x.start, x.end, permc.new(wrap(ctx, x, text), t[x.start][2]))], f'{ctx.src(x.start, x.end)[:30]} -> {text[:30]}')
    if op == 'index':
        text = f'&{ctx.src(x.kids[0].start, x.kids[0].end)}[{ctx.src(_unparen(x.kids[1]).start, _unparen(x.kids[1]).end)}]'
        if t[x.start - 1][1] in ('&', '&&'): text = f'({text})'
        return Edit([(x.start, x.end, permc.new(text, t[x.start][2]))], f'{ctx.src(x.start, x.end)[:30]} -> {text[:30]}')
    if op == 'two':
        p, b, K, bt = d
        first = f'{p} = ({bt[0]} *)&{b};' if bt[1] and rng.random() < 0.5 else f'{p} = {b};'
        K = _unparen(K)
        text = first + '\n' + ctx.indent(x.start) + f'{p} += {ctx.src(K.start, K.end)};'
        return Edit([(x.start, x.end, permc.new(text, t[x.start][2]))], f'{p} = base, then += @{_line(ctx, x.start)}')
    p, b, K, m = d
    text = f'{p} = &{b}[{ctx.src(_unparen(K).start, _unparen(K).end)}];' if rng.random() < 0.6 else f'{p} = {b} + {_opnd(ctx, K, "+", True)};'
    return Edit([(x.start, m.end, permc.new(text, t[x.start][2]))], f'{text[:40]} in one statement @{_line(ctx, x.start)}')


# ---- D21 two-byte read

def _is_byte(ctx, e):
    e = _unparen(e)
    if _ikind(ctx, e) == (8, False) and e.kind in ('atom', 'index', 'member', 'cast'): return True
    if e.kind == 'bin' and e.op == '&':
        v = permc.const_value(e.kids[1], ctx.toks)
        return v is not None and 0 <= v <= 0xff and _ikind(ctx, e.kids[0]) is not None
    return False


def _byte_pair(ctx, x):
    """(high part, low part, high first, shifted, operator) of `H << 8 | L` in any of its spellings, H and
    L integers of at most 32 bits."""
    t = ctx.toks
    if x.kind != 'bin' or x.op not in ('|', '+'): return None
    for hk in (0, 1):
        h, l = _unparen(x.kids[hk]), _unparen(x.kids[1 - hk])
        if h.kind != 'bin' or _ikind(ctx, l) is None: continue
        if h.op == '<<' and permc.const_value(h.kids[1], t) == 8 and _ikind(ctx, h.kids[0]) is not None: return h.kids[0], l, hk == 0, True, x.op
        if h.op == '*':
            for a, b in ((0, 1), (1, 0)):
                if permc.const_value(h.kids[b], t) == 256 and _ikind(ctx, h.kids[a]) is not None: return h.kids[a], l, hk == 0, False, x.op
    return None


def _pair_text(hs, ls, hi_first, shift, op):
    h = f'{hs} << 8' if shift else f'{hs} * 256'
    if shift and op == '+': h = f'({h})'
    return f'{h} {op} {ls}' if hi_first else f'{ls} {op} {h}'


def r_byte_read(ctx, rng, everywhere=False):
    """D21: a two-byte read `H << 8 | L` (H and L integers without side effects) respelt: `<< 8` against
    `* 256`, high part first against low part first, `|` against `+` (only when L is provably an
    unsigned byte, so no bit of it meets H's), or read into two block locals first, in either order
    (unsigned char for a byte, else the operand's promoted type), when nothing else in the statement
    has a side effect."""
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        root = _root_expr(ctx, n)
        if root is None: continue
        for x in permc.ewalk(root):
            bp = _byte_pair(ctx, x)
            if bp is None: continue
            f = _fx(ctx, x.start, x.end)
            if f.vol or f.call or permc.has_side_effects(x, t): continue
            cands.append((n, root, x, bp))
    if not cands: return None
    n, root, x, (hb, lb_, hi_first, shift, op) = rng.choice(cands)
    hs, ls = paren(ctx, _unparen(hb)), paren(ctx, _unparen(lb_))
    kinds = ['spell', 'spell', 'order']
    if n.kind == 'stmt' and _pure_outside(ctx, root, x) and _seq_path(ctx, root, x) is not None: kinds.append('locals')
    kind = rng.choice(kinds)
    if kind == 'spell':
        shift2, op2 = rng.choice([c for c in ((True, '|'), (True, '+'), (False, '|'), (False, '+'))
                                  if c != (shift, op) and (c[1] == op or _is_byte(ctx, lb_))])
        text = _pair_text(hs, ls, hi_first, shift2, op2)
    elif kind == 'order':
        text = _pair_text(hs, ls, not hi_first, shift, op)
    else:
        hn, ln = ctx.fresh('pmt_hi'), ctx.fresh('pmt_lo')
        ty = lambda z: 'unsigned char' if _is_byte(ctx, z) else CANON[_promote(_ikind(ctx, z))]
        d = [f'{ty(hb)} {hn} = {ctx.src(_unparen(hb).start, _unparen(hb).end)};', f'{ty(lb_)} {ln} = {ctx.src(_unparen(lb_).start, _unparen(lb_).end)};']
        if rng.random() < 0.5: d.reverse()
        body = _replace_spans(ctx, n.start, n.end, [(x.start, x.end, wrap(ctx, x, _pair_text(hn, ln, hi_first, shift, op)))])
        return Edit([(n.start, n.end, permc.new('{ ' + ' '.join(d) + ' ' + body + ' }', t[n.start][2]))],
                    f'two-byte read through byte locals @{_line(ctx, n.start)}')
    return Edit([(x.start, x.end, permc.new(wrap(ctx, x, text), t[x.start][2]))], f'two-byte read {kind}: {text[:40]} @{_line(ctx, n.start)}')


# ---- D25 constant type (not provably the same value: meaning 'type')

def r_const_type(ctx, rng, everywhere=False):
    """D25: an integer constant operand `K` against `Ku`; `(unsigned int)` on an operand of * / % or on
    the left of >>, or such a cast removed; a float constant with or without its `f`. These change the arithmetic
    (udiv against sdiv, float against double), so the edit is marked 'type', not 'same'."""
    t = ctx.toks
    cands = []
    ar = ('*', '/', '%', '+', '-', '<', '>', '<=', '>=', '==', '!=', '&', '|', '^')
    for e in _all_exprs(ctx):
        for x in permc.ewalk(e):
            if x.kind == 'bin' and x.op == '>>':                    # asrs against lsrs for a promoted u16
                k = x.kids[0]
                if k.kind == 'cast' and ' '.join(y[1] for y in t[k.op[0]:k.op[1]]) in ('unsigned int', 'uint', 'unsigned', 'u32'):
                    cands.append((k, ctx.src(k.kids[0].start, k.kids[0].end)))
                elif _ikind(ctx, k) is not None and (k.kind in ('index', 'member', 'paren') or k.kind == 'atom' and k.op == 'id'):
                    cands.append((k, f'(unsigned int){ctx.src(k.start, k.end)}'))
            if not (x.kind == 'bin' and x.op in ar or x.kind == 'assign'): continue
            for i, k in enumerate(x.kids):
                if x.kind == 'assign' and i == 0: continue
                if k.kind == 'atom' and k.op == 'num':
                    s = t[k.start][1]
                    hexa = s.lower().startswith('0x')
                    if not hexa and re.search(r'[.eE]', s):
                        cands.append((k, s[:-1] if s[-1] in 'fF' else s + 'f'))
                    elif not hexa and s[-1] in 'fF': pass
                    elif s[-1] in 'uU': cands.append((k, s[:-1]))
                    elif s[-1] not in 'lL': cands.append((k, s + 'u'))
                elif x.kind == 'bin' and x.op in ('*', '/', '%'):
                    if k.kind == 'cast' and ' '.join(y[1] for y in t[k.op[0]:k.op[1]]) in ('unsigned int', 'uint', 'unsigned', 'u32'):
                        cands.append((k, ctx.src(k.kids[0].start, k.kids[0].end)))
                    elif _ikind(ctx, k) is not None and k.kind in ('atom', 'index', 'member', 'paren', 'call'):
                        cands.append((k, f'(unsigned int){ctx.src(k.start, k.end)}'))
    if not cands: return None
    k, new = rng.choice(cands)
    return Edit([(k.start, k.end, permc.new(new, t[k.start][2]))], f'constant type {ctx.src(k.start, k.end)[:24]} -> {new[:30]} @{_line(ctx, k.start)}',
                meaning='type')


# ---- D28 an argument through a block local

def _protos(ctx):
    """{function: [parameter type text or None]} from the file's declarations and definitions (None for
    a function declared without a prototype or with ...)."""
    c = getattr(ctx, '_pr', None)
    if c is not None: return c
    c = ctx._pr = {}
    t = ctx.toks
    i, n = 0, len(t)
    while i < n - 1:
        w = t[i][1]
        if w == '{': i = ctx.pair[i] + 1; continue
        if t[i][0] == 'id' and t[i + 1][1] == '(' and w not in permc.KEYWORDS and i and (t[i - 1][0] == 'id' or t[i - 1][1] == '*'):
            rp = ctx.pair[i + 1]
            ps, proto = [], True
            for a, b in _split_top(ctx, i + 2, rp, ','):
                ws = [x[1] for x in t[a:b]]
                if not ws: proto = False; break
                if ws == ['void']: break
                if '...' in ws: proto = False; break
                if '(' in ws or '[' in ws: ps.append(None); continue
                if len(ws) >= 2 and t[b - 1][0] == 'id' and not ctx.types.is_type_word(ws[-1]): ws = ws[:-1]
                ps.append(' '.join(x for x in ws if x not in ('const', 'volatile', 'register')))
            if w not in c: c[w] = ps if proto else None
            i = rp + 1; continue
        i += 1
    return c


def _arg_types(ctx, call, i, arg):
    """Types a block local may have so that passing it is the same as passing arg directly."""
    t = ctx.toks
    f = t[call.kids[0].start][1]
    pr = _protos(ctx).get(f, 0)
    ak = _ikind(ctx, arg)
    if pr == 0: return []                                       # no declaration seen
    if pr is None:                                              # unprototyped: the promoted type
        return [CANON[_promote(ak)]] if ak else []
    if i >= len(pr) or pr[i] is None: return []
    out = [pr[i]]
    if _tkind(ctx, pr[i]) is not None and ak is not None: out += ['int', 'unsigned int']
    return out


def r_arg_local(ctx, rng, everywhere=False):
    """D28: `f(E, ...)` against `{ T t = (E); f(t, ...); }`, both ways. T is the parameter's declared
    type (always the same value), or int / unsigned int when E is an integer of at most 32 bits and
    the parameter is one too. E has no side effects, is evaluated always, and nothing else in the
    statement stores or calls except what E is an argument of."""
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        if n.kind != 'stmt': continue
        root = _root_expr(ctx, n)
        if root is None: continue
        for c in permc.ewalk(root):
            if c.kind != 'call' or c.kids[0].kind != 'atom' or _is_macro(ctx, t[c.kids[0].start][1]): continue
            for i, a in enumerate(c.kids[1:]):
                if a.kind == 'atom' and (a.op != 'id' or t[a.start][1] in ctx.locals or t[a.start][1] in ctx.params): continue
                if permc.has_side_effects(a, t) or _fx(ctx, a.start, a.end).vol: continue
                tys = _arg_types(ctx, c, i, a)
                if tys and _pure_outside(ctx, root, a) and _seq_path(ctx, root, a) is not None: cands.append(('in', n, a, tys))
    for b in ctx.nodes:
        if b.kind != 'block' or b is ctx.root or len(b.children) != 2 or not ctx.is_decl(b.children[0]) or b.children[1].kind != 'stmt': continue
        d, n = b.children
        l = next((x for x in ctx.locals.values() if x.node is d), None)
        root = _root_expr(ctx, n)
        if l is None or not l.single or not l.init or l.dims or root is None or l.name in ctx.dup_names: continue
        if any(t[i][1] in ('static', 'volatile') for i in range(d.start, d.end)): continue
        uses = [x for x in permc.ewalk(root) if x.kind == 'atom' and x.op == 'id' and t[x.start][1] == l.name]
        init = ctx.expr(*l.init)
        if len(uses) != 1 or init is None or permc.has_side_effects(init, t) or _fx(ctx, *l.init).vol: continue
        u = uses[0]
        call = next((c for c in permc.ewalk(root) if c.kind == 'call' and c.kids[0].kind == 'atom' and any(k is u for k in c.kids[1:])), None)
        if call is None or _is_macro(ctx, t[call.kids[0].start][1]): continue
        i = next(i for i, k in enumerate(call.kids[1:]) if k is u)
        key = (l.type + ' ' + l.stars).strip()
        tys = _arg_types(ctx, call, i, _unparen(init))
        kk = _tkind(ctx, key)
        ok = key in tys or (kk is not None and kk[0] == 32 and CANON[kk] in tys)
        if ok and _pure_outside(ctx, root, u) and _seq_path(ctx, root, u) is not None: cands.append(('out', b, u, init))
    if not cands: return None
    op, n, a, x = rng.choice(cands)
    if op == 'in':
        ty = rng.choice(x)
        nm = ctx.fresh('pmt_a')
        v = ctx.src(a.start, a.end)
        if a.kind not in ('atom', 'paren', 'index', 'member', 'call'): v = f'({v})'
        body = _replace_spans(ctx, n.start, n.end, [(a.start, a.end, nm)])
        return Edit([(n.start, n.end, permc.new('{ ' + f'{ty} {nm} = {v}; ' + body + ' }', t[n.start][2]))],
                    f'argument {v[:30]} through a {ty} local @{_line(ctx, n.start)}')
    ini = _unparen(x)
    v = ctx.src(ini.start, ini.end)
    if ini.kind in ('comma', 'assign') or (ini.kind in ('bin', 'cond', 'cast', 'unary') and not (t[a.start - 1][1] in ('(', ',') and t[a.end][1] in (',', ')'))):
        v = f'({v})'
    s = n.children[1]
    body = _replace_spans(ctx, s.start, s.end, [(a.start, a.end, v)])
    return Edit([(n.start, n.end, permc.new(body, t[n.start][2]))], f'argument local written out: {v[:30]} @{_line(ctx, n.start)}')

# ---- B7 setter helpers

def _toknorm(text):
    return ' '.join(x[1] for x in permc.tokenize(text)[0])


def _tool_setters(ctx):
    """{name: (first token, end token, parameter text, body text)} of the setter helpers this tool wrote
    before FUNC (`static __forceinline void pmt_putN(...)` / `pmt_setiN(...)`)."""
    c = getattr(ctx, '_ts', None)
    if c is not None: return c
    c = ctx._ts = {}
    t = ctx.toks
    i = 0
    while i < ctx.start:
        if t[i][1] == '{': i = ctx.pair[i] + 1; continue
        if t[i][0] == 'id' and t[i][1].startswith('pmt_') and re.match(r'pmt_(put|seti)\d+$', t[i][1]) and t[i + 1][1] == '(' and i >= 3 \
                and [x[1] for x in t[i - 3:i]] == ['static', '__forceinline', 'void']:
            rp = ctx.pair[i + 1]
            if t[rp + 1][1] == '{':
                rb = ctx.pair[rp + 1]
                c[t[i][1]] = (i - 3, rb + 1, ' '.join(x[1] for x in t[i + 2:rp]), ' '.join(x[1] for x in t[rp + 2:rb]))
                i = rb + 1; continue
        i += 1
    return c


def _setter_type(ctx, lhs):
    """The declared type of a store target that a `T *` may point at: a global scalar, an element of a
    one-dimension global array, or a field that is not a bit-field and has one type in the file."""
    t = ctx.toks
    f = _fx(ctx, lhs.start, lhs.end)
    if f.vol or f.call: return None
    if lhs.kind == 'atom':
        nm = t[lhs.start][1]
        if lhs.op != 'id' or nm in ctx.locals or nm in ctx.params or _is_macro(ctx, nm): return None
    elif lhs.kind == 'member':
        if len(_fields(ctx).get(t[lhs.end - 1][1], ())) != 1: return None
    elif lhs.kind == 'index':
        if lhs.kids[0].kind != 'atom' or t[lhs.kids[0].start][1] in ctx.locals or t[lhs.kids[0].start][1] in ctx.params: return None
    else: return None
    raw = _etype(ctx, lhs)
    if raw is None or 'volatile' in raw or raw.endswith('[]') or '(' in raw: return None
    return resolve_type(ctx, raw)


def r_setter(ctx, rng, everywhere=False):
    """B7: a store `x = E;` through `static __forceinline void pmt_putN(T *p, T v) { *p = v; }` (or with
    the value first), a store `A[i] = E;` through an index setter `pmt_setiN(i, v)`, and a call of
    such a helper written back as the store (the helper's definition goes with its last call). T is
    the target's declared type, so the value is converted as the store converts it; volatile targets
    and bit-fields are left alone."""
    t = ctx.toks
    hs = _tool_setters(ctx)
    puts, setis, backs = [], [], []
    for n in ctx.nodes:
        e = ctx.stmt_expr(n) if n.kind == 'stmt' else None
        if e is None: continue
        if e.kind == 'call' and e.kids[0].kind == 'atom' and t[e.kids[0].start][1] in hs and len(e.kids) == 3:
            backs.append((n, e))
        if e.kind != 'assign' or e.op != '=': continue
        lhs = e.kids[0]
        ty = _setter_type(ctx, lhs)
        if ty is None: continue
        puts.append((n, e, ty))
        if lhs.kind == 'index' and _pointee(ctx, t[lhs.kids[0].start][1]) and _pointee(ctx, t[lhs.kids[0].start][1])[1]:
            k = _ikind(ctx, lhs.kids[1])
            if k is not None: setis.append((n, e, ty, k))
    opts = [x for x, c in (('put', puts), ('seti', setis), ('back', backs)) if c]
    if not opts: return None
    op = rng.choice(opts)
    if op == 'back':
        n, e = rng.choice(backs)
        fn = t[e.kids[0].start][1]
        a, b, params, body = hs[fn]
        x, y = e.kids[1], e.kids[2]
        S = lambda z: ctx.src(z.start, z.end)
        if fn.startswith('pmt_seti'):
            text = f'{body.split(" ")[0]}[{S(_unparen(x))}] = {S(y)};'
        else:
            if not params.split(' , ')[0].endswith('* p'): x, y = y, x
            u = _unparen(x)
            tgt = S(u.kids[0]) if u.kind == 'unary' and u.op == '&' else f'*{paren(ctx, u)}'
            text = f'{tgt} = {S(y)};'
        spans = [(n.start, n.end, permc.new(text, t[n.start][2]))]
        if sum(1 for x in t if x[1] == fn) == 2: spans.append((a, b, []))
        return Edit(spans, f'setter {fn} written out as {text[:40]} @{_line(ctx, n.start)}')
    S = lambda z: ctx.src(z.start, z.end)
    arg = lambda z: f'({S(z)})' if z.kind == 'comma' else S(z)
    if op == 'put':
        n, e, ty = rng.choice(puts)
        first = rng.random() < 0.7
        ps = f'{ty} *p, {ty} v' if first else f'{ty} v, {ty} *p'
        body = '*p = v;'
        sites = [(m, f, None) for m, f, ty2 in puts if ty2 == ty] if everywhere else [(n, e, None)]
        call = lambda f, _: f'(&{S(f.kids[0])}, {arg(f.kids[1])})' if first else f'({arg(f.kids[1])}, &{S(f.kids[0])})'
        base = 'pmt_put'
    else:
        n, e, ty, k = rng.choice(setis)
        A = t[e.kids[0].kids[0].start][1]
        kt = 'int' if _promote(k)[1] else 'unsigned int'
        if k[0] < 32 and k[1] is False and rng.random() < 0.5: kt = 'unsigned int'
        ps = f'{kt} i, {ty} v'
        body = f'{A}[i] = v;'
        sites = [(m, f, None) for m, f, ty2, k2 in setis if t[f.kids[0].kids[0].start][1] == A and k2 == k] if everywhere else [(n, e, None)]
        call = lambda f, _: f'({arg(f.kids[0].kids[1])}, {arg(f.kids[1])})'
        base = 'pmt_seti'
    fn = next((nm for nm, (_, _, p, b) in hs.items() if p == _toknorm(ps) and b == _toknorm(body) and nm.startswith(base)), None)
    spans = []
    if fn is None:
        fn = ctx.fresh(base)
        s = ctx.start
        spans.append((s, s + 1, permc.new(f'static __forceinline void {fn}({ps}) {{ {body} }}\n', t[s][2]) + [Tok(t[s][0], t[s][1], '\n')]))
    for m, f, _ in sites:
        spans.append((m.start, m.end, permc.new(fn + call(f, None) + ';', t[m.start][2])))
    return Edit(spans, f'{fn} for {S(e.kids[0])[:30]} at {len(sites)} store{"s" if len(sites) > 1 else ""} @{_line(ctx, n.start)}')


# ---- D17 loop copy against memcpy

def _copy_loop(ctx, n):
    """(index local, count, count text, destination, source) of `for (i = 0; i < N; i++) d[i] = s[i];`."""
    t = ctx.toks
    lp, rp = n.cond
    parts = _split_top(ctx, lp + 1, rp, ';')
    if len(parts) != 3 or any(p[0] >= p[1] for p in parts): return None
    ie, ce, se = (ctx.expr(*p) for p in parts)
    if ie is None or ce is None or se is None: return None
    if not (ie.kind == 'assign' and ie.op == '=' and ie.kids[0].kind == 'atom' and permc.const_value(ie.kids[1], t) == 0): return None
    i = t[ie.kids[0].start][1]
    k = _tkind(ctx, _type_key(ctx, i)) if _plain_local(ctx, i) else None
    if k is None: return None
    is_i = lambda z: z.kind == 'atom' and t[z.start][1] == i
    if not (ce.kind == 'bin' and ce.op == '<' and is_i(ce.kids[0])): return None
    N = permc.const_value(ce.kids[1], t)
    if N is None or not 0 < N < (1 << (k[0] - 1)): return None
    if not ((se.kind in ('postfix', 'unary') and se.op == '++' and is_i(se.kids[0]))
            or (se.kind == 'assign' and se.op == '+=' and is_i(se.kids[0]) and permc.const_value(se.kids[1], t) == 1)): return None
    b = n.body
    if b.kind == 'block' and len(b.children) == 1: b = b.children[0]
    e = ctx.stmt_expr(b) if b.kind == 'stmt' else None
    if e is None or e.kind != 'assign' or e.op != '=': return None
    d, s = e.kids
    if not all(z.kind == 'index' and z.kids[0].kind == 'atom' and z.kids[0].op == 'id' and is_i(z.kids[1]) for z in (d, s)): return None
    D, Sn = t[d.kids[0].start][1], t[s.kids[0].start][1]
    if D == Sn or i in (D, Sn): return None
    return i, N, ctx.src(ce.kids[1].start, ce.kids[1].end), D, Sn


def _copy_types(ctx, D, S):
    """(element type, element size, both are distinct array objects) when D and S hold the same scalar type."""
    pd, ps = _pointee(ctx, D), _pointee(ctx, S)
    if pd is None or ps is None or pd[0] != ps[0] or _vol_name(ctx, D) or _vol_name(ctx, S): return None
    size = _scalar_size(ctx, pd[0])
    return (pd[0], size, pd[1] and ps[1]) if size else None


def r_memcpy_loop(ctx, rng, everywhere=False):
    """D17: `for (i = 0; i < N; i++) d[i] = s[i];` against `memcpy(d, s, N);` for a constant N and the
    same scalar element type. The loop to memcpy keeps `i = N;` after it unless i is not mentioned
    again; it is the same program when d and s are two array objects, and is marked 'type' when either
    is a pointer (they could overlap). memcpy back to a loop is always the same. `#include <string.h>`
    is added when memcpy is not declared in the file."""
    t = ctx.toks
    cands = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            if n.kind == 'for':
                c = _copy_loop(ctx, n)
                ct = _copy_types(ctx, c[3], c[4]) if c else None
                if ct and not _is_macro(ctx, 'memcpy'): cands.append(('to', n, c, ct, None))
            elif n.kind == 'stmt':
                e = ctx.stmt_expr(n)
                if e is None or e.kind != 'call' or e.kids[0].kind != 'atom' or t[e.kids[0].start][1] != 'memcpy' or len(e.kids) != 4: continue
                d, s, cnt = e.kids[1:]
                if not (d.kind == 'atom' and s.kind == 'atom' and d.op == 'id' and s.op == 'id') or _is_macro(ctx, 'memcpy'): continue
                D, Sn = t[d.start][1], t[s.start][1]
                ct = _copy_types(ctx, D, Sn)
                if ct is None or D == Sn: continue
                cnt = _unparen(cnt)
                N = None
                v = permc.const_value(cnt, t)
                if v is not None and v % ct[1] == 0: N = v // ct[1]
                elif cnt.kind == 'bin' and cnt.op == '*':
                    for a, b in ((0, 1), (1, 0)):
                        z = cnt.kids[b]
                        if z.kind == 'sizeof' and z.op == 'type' and ' '.join(x[1] for x in t[z.start + 2:z.end - 1]) == ct[0]:
                            N = permc.const_value(cnt.kids[a], t)
                if N is None or N < 1: continue
                nx = kids[k + 1] if k + 1 < len(kids) else None
                cands.append(('from', n, (N, D, Sn), ct, nx))
    if not cands: return None
    op, n, c, ct, nx = rng.choice(cands)
    ind = ctx.indent(n.start)
    if op == 'to':
        i, N, ntxt, D, Sn = c
        size = ntxt if ct[1] == 1 else f'{ntxt} * sizeof({ct[0]})'
        occ, _ = _occ(ctx, i)
        dead = not any(o >= n.end for o in occ) and not any(p < n.start for p in _goto_labels(ctx)) \
            and not any(ls <= n.start < le and (ls, le) != (n.start, n.end) and any(ls <= o < le and not (n.start <= o < n.end) for o in occ)
                        for ls, le in _loops(ctx))
        text = f'memcpy({D}, {Sn}, {size});'
        if not dead or rng.random() < 0.3: text += '\n' + ind + f'{i} = {ntxt};'
        spans = [(n.start, n.end, permc.new(text, t[n.start][2]))]
        inc = _include_span(ctx, 'memcpy', 'string.h')
        if inc: spans.append(inc)
        return Edit(spans, f'copy loop -> memcpy({D}, {Sn}, {size}) @{_line(ctx, n.start)}', meaning='same' if ct[2] else 'type')
    N, D, Sn = c
    e2 = ctx.stmt_expr(nx) if nx is not None and nx.kind == 'stmt' else None
    if e2 is not None and e2.kind == 'assign' and e2.op == '=' and e2.kids[0].kind == 'atom' and permc.const_value(e2.kids[1], t) == N:
        i = t[e2.kids[0].start][1]
        k = _tkind(ctx, _type_key(ctx, i)) if _plain_local(ctx, i) else None
        if k is not None and N < (1 << (k[0] - 1)) and i not in (D, Sn):
            text = f'for ({i} = 0; {i} < {ctx.src(e2.kids[1].start, e2.kids[1].end)}; {i}++) {D}[{i}] = {Sn}[{i}];'
            return Edit([(n.start, nx.end, permc.new(text, t[n.start][2]))], f'memcpy -> copy loop on {i} @{_line(ctx, n.start)}')
    i = ctx.fresh('pmt_i')
    text = '{ ' + f'unsigned int {i}; for ({i} = 0; {i} < {N}; {i}++) {D}[{i}] = {Sn}[{i}];' + ' }'
    return Edit([(n.start, n.end, permc.new(text, t[n.start][2]))], f'memcpy -> copy loop @{_line(ctx, n.start)}')


# ---- A7 a global's declared type, A9 a callee's linkage (declarations in the file; meaning 'type')

def _file_decls(ctx):
    """{name: [(statement start, end (the ;), index after the type words, (declarator start, end))]} of
    the object declarations at file scope before FUNC."""
    c = getattr(ctx, '_fd', None)
    if c is not None: return c
    c = ctx._fd = {}
    t = ctx.toks
    i = s = 0
    while i < ctx.start:
        k, w, _ = t[i]
        if k == 'pp': s = i + 1
        elif w == '{':
            fn = i and t[i - 1][1] == ')'
            i = ctx.pair[i]
            s = i + 1 if fn else -1                         # a struct or an initialiser: not a plain declaration
        elif w == ';':
            if s >= 0 and i > s and t[s][1] != 'typedef':
                j = s
                while j < i and ctx.types.is_type_word(t[j][1]) and t[j][1] not in ('struct', 'union', 'enum'): j += 1
                if j > s and j < i and not any(t[q][1] in ('(', 'struct', 'union', 'enum') for q in range(s, i)):
                    for a, b in _split_top(ctx, j, i, ','):
                        if a < b and t[a][0] == 'id': c.setdefault(t[a][1], []).append((s, i, j, (a, b)))
            s = i + 1
        i += 1
    return c


def r_global_type(ctx, rng, everywhere=False):
    """A7: the declared type of a global FUNC uses, edited at its declaration in the file: the sign
    changed within the same size (uchar / signed char / char, ushort / short, uint / int: the layout
    stays), or volatile added or removed. A declarator that shares its statement is given a statement
    of its own. Not for a name that a header also mentions. Marked 'type': the values can change."""
    t = ctx.toks
    fd = _file_decls(ctx)
    hid = getattr(ctx.types, 'header_ids', ())
    names = sorted(n for n in fd if n in ctx.ids and len(fd[n]) == 1 and n not in ctx.locals and n not in ctx.params and n not in hid)
    if not names: return None
    nm = rng.choice(names)
    s, e, j, (a, b) = fd[nm][0]
    words = [t[q][1] for q in range(s, j)]
    quals = [w for w in words if w in ('extern', 'static', 'const', '__packed', 'register')]
    vol = 'volatile' in words
    base = ' '.join(w for w in words if w not in quals and w != 'volatile')
    k = _tkind(ctx, base)
    opts = [(quals + ([] if vol else ['volatile']), base)]
    if k is not None:
        alts = [CANON[(k[0], not k[1])]] if k[1] is not None else ['unsigned char', 'signed char']
        if k[0] == 8 and k[1] is not None: alts.append('char')
        for alt in alts:
            td = next((n for n, v in ctx.types.typedefs.items() if v == alt and n in ('uchar', 'ushort', 'uint', 'u8', 'u16', 'u32', 's8', 's16', 's32')), None)
            opts += [(quals + (['volatile'] if vol else []), td or alt)] * 2
    q, ty = rng.choice(opts)
    head = ' '.join(q + [ty])
    all_d = [x[3] for v in fd.values() for x in v if x[0] == s]
    if len(all_d) == 1:
        spans = [(s, j, permc.new(head, t[s][2]))]
    else:
        rest = ', '.join(ctx.src(x, y) for x, y in sorted(all_d) if (x, y) != (a, b))
        spans = [(s, e + 1, permc.new(f'{ctx.src(s, j)} {rest};\n{head} {ctx.src(a, b)};', t[s][2]))]
    return Edit(spans, f'global {nm}: {" ".join(words)} -> {head}', meaning='type')


def _fn_decls(ctx, names):
    """{name: [(first token of the declaration, name token, is a definition)]} for the functions named."""
    t = ctx.toks
    out = {}
    i, n = 0, len(t)
    while i < n - 1:
        w = t[i][1]
        if w == '{': i = ctx.pair[i] + 1; continue
        if w in names and t[i][0] == 'id' and t[i + 1][1] == '(':
            rp = ctx.pair[i + 1]
            s = i
            while s > 0 and t[s - 1][0] != 'pp' and t[s - 1][1] not in (';', '}'): s -= 1
            nxt = t[rp + 1][1] if rp + 1 < n else ''
            if s < i and all(t[q][0] == 'id' or t[q][1] == '*' for q in range(s, i)) and nxt in ('{', ';'):
                out.setdefault(w, []).append((s, i, nxt == '{'))
            else: out.setdefault(w, []).append(None)
            i = rp + 1; continue
        i += 1
    return out


def r_callee_linkage(ctx, rng, everywhere=False):
    """A9: a function FUNC calls and the file defines gets `static` on every declaration of it, or
    loses it, or its definition gets or loses `__weak`. Not for inline helpers, nor (adding static)
    for a name a header mentions. Marked 'type': the link can change, the function's code cannot be
    proved to stay as it is."""
    t = ctx.toks
    called = {t[i][1] for i in range(ctx.lb, ctx.rb) if t[i][0] == 'id' and t[i + 1][1] == '(' and t[i][1] not in permc.KEYWORDS
              and t[i][1] not in ctx.locals and t[i][1] != ctx.func}
    if not called: return None
    hid = getattr(ctx.types, 'header_ids', ())
    cands = []
    for nm, ds in sorted(_fn_decls(ctx, called).items()):
        if None in ds or not any(d[2] for d in ds): continue
        pre = [[t[q][1] for q in range(s, i)] for s, i, _ in ds]
        if any(w in p for p in pre for w in ('inline', '__inline', '__forceinline')): continue
        if any('static' in p for p in pre): cands.append(('unstatic', nm, ds))
        else:
            if nm not in hid and nm != 'main': cands.append(('static', nm, ds))
            cands.append(('unweak' if any('__weak' in p for p in pre) else 'weak', nm, ds))
    if not cands: return None
    op, nm, ds = rng.choice(cands)
    spans = []
    add = lambda s, w: (s, s + 1, [Tok('id', w, t[s][2]), Tok(t[s][0], t[s][1], ' ')])
    for s, i, is_def in ds:
        if op == 'static': spans.append(add(s, 'static'))
        elif op == 'weak':
            if is_def: spans.append(add(s, '__weak'))
        else:
            w = 'static' if op == 'unstatic' else '__weak'
            for q in range(s, i):
                if t[q][1] == w: spans.append((q, q + 2, [Tok(t[q + 1][0], t[q + 1][1], t[q][2])]))
    if not spans: return None
    return Edit(spans, f'{nm}: {op}', meaning='type')


# ---- spellings (decomp-permuter's passes that survive armcc's front end)

def r_sub_neg(ctx, rng, everywhere=False):
    """generic: `a - b` against `a + -b` (`a - 1` against `a + -1`), for integers of at most 32 bits."""
    t = ctx.toks
    cands = []
    for e in _all_exprs(ctx):
        for x in permc.ewalk(e):
            if x.kind != 'bin' or x.op not in ('-', '+') or _ikind(ctx, x.kids[0]) is None or _ikind(ctx, x.kids[1]) is None: continue
            r = x.kids[1]
            if x.op == '-': cands.append((x, f'+ -{paren(ctx, r)}'))
            elif r.kind == 'unary' and r.op == '-': cands.append((x, f'- {ctx.src(r.kids[0].start, r.kids[0].end)}'))
    if not cands: return None
    x, tail = rng.choice(cands)
    new = f'{ctx.src(x.kids[0].start, x.kids[0].end)} {tail}'
    return Edit([(x.start, x.end, permc.new(new, t[x.start][2]))], f'{ctx.src(x.start, x.end)[:30]} -> {new[:30]} @{_line(ctx, x.start)}')


def r_factor(ctx, rng, everywhere=False):
    """generic: `a * K + b * K` against `(a + b) * K`, and the same for a shift (`<<` with + - | & ^),
    for side-effect-free integers of at most 32 bits (equal modulo 2^32)."""
    t = ctx.toks
    cands = []
    ok = lambda z: _ikind(ctx, z) is not None and not permc.has_side_effects(z, t) and not _fx(ctx, z.start, z.end).vol \
        and not _fx(ctx, z.start, z.end).call
    for e in _all_exprs(ctx):
        for x in permc.ewalk(e):
            if x.kind != 'bin': continue
            if x.op in ('+', '-', '|', '&', '^'):
                l, r = _unparen(x.kids[0]), _unparen(x.kids[1])
                if l.kind == 'bin' and r.kind == 'bin' and l.op == r.op and l.op in ('*', '<<') and (l.op == '<<' or x.op in '+-') \
                        and _eq(ctx, l.kids[1], r.kids[1]) and all(ok(z) for z in (l.kids[0], r.kids[0], l.kids[1])):
                    cands.append(('factor', x, l, r))
            if x.op in ('*', '<<'):
                l = _unparen(x.kids[0])
                if x.kids[0].kind == 'paren' and l.kind == 'bin' and (l.op in '+-' or x.op == '<<' and l.op in ('|', '&', '^')) \
                        and all(ok(z) for z in (l.kids[0], l.kids[1], x.kids[1])):
                    cands.append(('spread', x, l, None))
    if not cands: return None
    op, x, l, r = rng.choice(cands)
    if op == 'factor':
        new = f'({_opnd(ctx, l.kids[0], x.op)} {x.op} {_opnd(ctx, r.kids[0], x.op, True)}) {l.op} {paren(ctx, _unparen(l.kids[1]))}'
    else:
        K = paren(ctx, _unparen(x.kids[1]))
        a, b = (f'{_opnd(ctx, z, x.op)} {x.op} {K}' for z in l.kids)
        new = f'({a}) {l.op} ({b})' if x.op == '<<' else f'{a} {l.op} {b}'
    return Edit([(x.start, x.end, permc.new(wrap(ctx, x, new), t[x.start][2]))], f'{op} {ctx.src(x.start, x.end)[:30]} -> {new[:30]} @{_line(ctx, x.start)}')


def r_arrow_deref(ctx, rng, everywhere=False):
    """generic: `p->f` against `(*p).f`."""
    t = ctx.toks
    cands = []
    for e in _all_exprs(ctx):
        for x in permc.ewalk(e):
            if x.kind != 'member': continue
            k = x.kids[0]
            if x.op == '->': cands.append((x, f'(*{paren(ctx, k)}).{t[x.end - 1][1]}'))
            elif k.kind == 'paren' and k.kids[0].kind == 'unary' and k.kids[0].op == '*':
                cands.append((x, f'{paren(ctx, k.kids[0].kids[0])}->{t[x.end - 1][1]}'))
    if not cands: return None
    x, new = rng.choice(cands)
    return Edit([(x.start, x.end, permc.new(new, t[x.start][2]))], f'{ctx.src(x.start, x.end)[:30]} -> {new[:30]} @{_line(ctx, x.start)}')


def r_index_deref(ctx, rng, everywhere=False):
    """generic: `a[i]` against `*(a + i)`."""
    t = ctx.toks
    cands = []
    for e in _all_exprs(ctx):
        for x in permc.ewalk(e):
            if x.kind == 'index':
                new = f'*({_opnd(ctx, x.kids[0], "+")} + {_opnd(ctx, x.kids[1], "+", True)})'
                if t[x.end][1] in ('[', '.', '->', '(', '++', '--') or t[x.start - 1][1] in ('/', '*', '&', '&&', '++', '--', 'sizeof'): new = f'({new})'
                cands.append((x, new))
            elif x.kind == 'unary' and x.op == '*' and x.kids[0].kind == 'paren' and x.kids[0].kids[0].kind == 'bin' and x.kids[0].kids[0].op == '+':
                b = x.kids[0].kids[0]
                if _ikind(ctx, b.kids[0]) is None and _ikind(ctx, b.kids[1]) is not None:
                    cands.append((x, f'{paren(ctx, b.kids[0])}[{ctx.src(_unparen(b.kids[1]).start, _unparen(b.kids[1]).end)}]'))
    if not cands: return None
    x, new = rng.choice(cands)
    return Edit([(x.start, x.end, permc.new(new, t[x.start][2]))], f'{ctx.src(x.start, x.end)[:30]} -> {new[:30]} @{_line(ctx, x.start)}')


def r_pad_local(ctx, rng, everywhere=False):
    """generic (dead code, 'probe'): an unused local scalar or array declared in FUNC, which can move
    the other locals in the stack frame (decomp-permuter's pad variable)."""
    blk = ctx.root if rng.random() < 0.7 else rng.choice(permc.blocks(ctx.root))
    if blk.parent is not None and blk.parent.kind == 'switch': blk = ctx.root
    if blk is not ctx.root and rng.random() < 0.5:
        # the copy-paste declaration family: a block opens with a copy of a declaration line of the function
        t = ctx.toks
        ds = [c for c in ctx.root.children if ctx.is_decl(c) and not any(t[i][1] in ('=', 'static', 'extern') for i in range(c.start, c.end))
              and not any(blk.start < j < blk.end for _, _, nm in _declarators(ctx, c)[1] for j in ctx.ids.get(nm, ()))
              and not any(nm in _declared(ctx, blk) for _, _, nm in _declarators(ctx, c)[1])]
        if ds:
            d = rng.choice(ds)
            return Edit([ctx.insert_at(_decl_point(ctx, blk), ctx.src(d.start, d.end))],
                        f'unused copy of the declaration {_short(ctx, d)} in the block at line {_line(ctx, blk.start)}', meaning='probe')
    ty = rng.choice(['int', 'unsigned char', 'unsigned short', 'unsigned int'])
    nm = ctx.fresh('pmt_pad')
    dims = '' if rng.random() < 0.4 else f'[{rng.choice([1, 2, 3, 4, 8, 16])}]'
    return Edit([ctx.insert_at(_decl_point(ctx, blk), f'{ty} {nm}{dims};')], f'unused local {ty} {nm}{dims} in the block at line {_line(ctx, blk.start)}',
                meaning='probe')




# ================================================================ levers of compiler.md that had no rule
#
# permuter-coverage.md lists every source lever of compiler.md with the rule that makes it. The rules
# below are the ones that audit added. Same standards as above: 'same' only when the tokens prove it.

# ---- what a variable is known to hold (a constant against the variable that holds it)

def _fits(k, v):
    """Integer v is a value of kind k (bits, signed) that reads back unchanged."""
    if k is None: return False
    return 0 <= v < (1 << (k[0] - 1)) if k[1] is not False else 0 <= v < (1 << k[0])


def _name_kind(ctx, name):
    """Integer kind of a variable (local, parameter or global scalar) by name; None when unknown."""
    if name in ctx.dup_names or _is_macro(ctx, name) or name in _array_params(ctx): return None
    l = ctx.locals.get(name)
    if l is not None: return None if (l.dims or l.stars) else _tkind(ctx, l.type)
    if name in ctx.params: return _tkind(ctx, ctx.params[name])
    g = ctx.types.globals.get(name)
    return _tkind(ctx, g[0]) if g and not g[1] else None


def _trackable(ctx, name):
    return _name_kind(ctx, name) is not None and not _vol_name(ctx, name)


def _eq_fact(ctx, cond, want_eq):
    """(name, K) when cond is `name == K` (want_eq) or `name != K` (not want_eq), name a trackable variable."""
    t = ctx.toks
    if cond is None: return None
    c = _unparen(cond)
    if c.kind != 'bin' or c.op != ('==' if want_eq else '!='): return None
    for a, b in ((0, 1), (1, 0)):
        x, k = _unparen(c.kids[a]), c.kids[b]
        v = permc.const_value(k, t)
        if x.kind == 'atom' and x.op == 'id' and v is not None and _trackable(ctx, t[x.start][1]) and _fits(_name_kind(ctx, t[x.start][1]), v):
            return t[x.start][1], v
    return None


def _kill(ctx, M, f):
    """Facts M after code with effects f."""
    if f.call or f.memw:
        priv = _private(ctx)
        M = {k: v for k, v in M.items() if k in priv}
    return {k: v for k, v in M.items() if k not in f.writes}


def _known(ctx):
    """{statement start: {variable: constant}}: what each statement-level node can rely on when control
    reaches it. A fact starts at `v = K;` (or `T v = K;`), in the arm of `if (v == K)`, or after a single
    `case K:` of `switch (v)` that is not fallen into; it follows `v++` and `v += c`; it ends at any
    store to v, at a call or a store through a pointer (unless v is a private local), at a label, and
    it does not enter a loop that writes v."""
    c = getattr(ctx, '_kn', None)
    if c is not None: return c
    c = ctx._kn = {}
    t = ctx.toks

    def step(n, M):
        """Facts after statement n ran, given M before it."""
        e = ctx.stmt_expr(n) if n.kind == 'stmt' else None
        f = _nfx(ctx, n)
        if n.kind == 'label' or n.kind == 'pp': return {}
        if _escapes(ctx, n, True, True, False) and n.kind != 'stmt': M = {}        # a goto label inside: other paths join
        if e is not None and e.kind in ('assign', 'postfix', 'unary') and e.kids and e.kids[0].kind == 'atom' and e.kids[0].op == 'id':
            v = t[e.kids[0].start][1]
            k = _name_kind(ctx, v)
            new = None
            if _trackable(ctx, v):
                if e.kind == 'assign':
                    kv = permc.const_value(e.kids[1], t) if not permc.has_side_effects(e.kids[1], t) else None
                    if e.op == '=' and kv is not None: new = kv
                    elif e.op in ('+=', '-=') and kv is not None and v in M: new = M[v] + (kv if e.op == '+=' else -kv)
                elif e.op in ('++', '--') and v in M: new = M[v] + (1 if e.op == '++' else -1)
            M = _kill(ctx, M, f)
            if new is not None and _fits(k, new): M[v] = new
            return M
        if n.kind == 'stmt' and ctx.is_decl(n):
            M = _kill(ctx, M, f)
            for l in ctx.locals.values():
                if l.node is n and l.single and l.init and _trackable(ctx, l.name):
                    ie = ctx.expr(*l.init)
                    kv = permc.const_value(ie, t) if ie is not None else None
                    if kv is not None and _fits(_name_kind(ctx, l.name), kv): M[l.name] = kv
            return M
        return _kill(ctx, M, f)

    def node(n, M):
        c[n.start] = M
        k = n.kind
        if k == 'block': block(n, M)
        elif k == 'if':
            ce = ctx.cond_expr(n)
            Mc = _kill(ctx, M, _fx(ctx, n.cond[0], n.cond[1] + 1))
            for arm, eq in ((n.then, True), (n.els, False)):
                if arm is None: continue
                Ma = dict(Mc)
                fact = _eq_fact(ctx, ce, eq)
                if fact and not permc.has_side_effects(ce, t): Ma[fact[0]] = fact[1]
                node(arm, Ma)
        elif k in ('for', 'while', 'do'):
            node(n.body, _kill(ctx, M, _nfx(ctx, n)))
        elif k == 'switch':
            Ms = _kill(ctx, M, _nfx(ctx, n))
            ce = ctx.cond_expr(n)
            x = _unparen(ce) if ce is not None else None
            sel = t[x.start][1] if x is not None and x.kind == 'atom' and x.op == 'id' and _trackable(ctx, t[x.start][1]) \
                and not _nfx(ctx, n.body).writes & {t[x.start][1]} else None
            if n.body.kind != 'block': node(n.body, Ms); return
            c[n.body.start] = Ms
            kids = n.body.children
            cur = dict(Ms)
            for i, ch in enumerate(kids):
                if ch.kind == 'label':
                    cur = dict(Ms)
                    single = (i == 0 or kids[i - 1].kind != 'label') and (i + 1 >= len(kids) or kids[i + 1].kind != 'label')
                    entered = i == 0 or _jump_stmt(ctx, kids[i - 1])
                    if sel and single and entered and t[ch.start][1] == 'case' and ch.extra != 'goto':
                        le = ctx.expr(ch.start + 1, ch.end - 1)
                        kv = permc.const_value(le, t) if le is not None else None
                        if kv is not None and _fits(_name_kind(ctx, sel), kv): cur[sel] = kv
                    c[ch.start] = cur
                    continue
                node(ch, dict(cur))
                cur = step(ch, cur)

    def block(b, M):
        cur = dict(M)
        for ch in b.children:
            node(ch, dict(cur))
            cur = step(ch, cur)

    node(ctx.root, {})
    return c


def _stmt_level(ctx, i, arms=False):
    """The innermost node holding token i whose parent is a block; with arms, a plain statement that is
    the unbraced arm of an if or the body of a loop counts too (the caller then puts braces round it)."""
    n = ctx.enclosing(i)
    while n is not None and (n.parent is None or n.parent.kind != 'block'):
        if arms and n.kind == 'stmt' and n.parent is not None and n.parent.kind in ('if', 'for', 'while', 'do') \
                and not ctx.is_decl(n) and ctx.toks[n.start][1] not in ('break', 'continue', 'goto'): return n
        n = n.parent
    return n


def _reads_safely(ctx, root, x, name):
    """Variable name still holds its known value when x (inside root) is evaluated: nothing in root
    outside x's own operators stores it, and (when it is not a private local) nothing calls or stores
    through a pointer."""
    priv = name in _private(ctx)
    for a in permc.ewalk(root):
        if a.kind in ('assign', 'call', 'postfix') or (a.kind == 'unary' and a.op in ('++', '--')):
            if a.start <= x.start and x.end <= a.end and a.kind == 'call': continue        # the call x is an argument of
            f = _fx(ctx, a.start, a.end)
            if a.kind == 'assign' and a.start <= x.start and x.end <= a.end and a.kids[1].start <= x.start:
                f = _fx(ctx, a.kids[0].start, a.kids[1].start)                               # x is in the value: only the target counts
                if name in f.writes and a.op == '=': continue
            if name in f.writes or (not priv and (f.call or f.memw)): return False
    return True


def _is_read_atom(ctx, x):
    t = ctx.toks
    return not (t[x.end][1] in permc.ASSIGN or t[x.end][1] in ('++', '--') or t[x.start - 1][1] in ('++', '--', '&', '.', '->', 'sizeof'))


def _lit(v, like=None):
    return hex(v) if (like or '').lower().startswith('0x') or v > 255 else str(v)


def _known_sites(ctx):
    """[(kind, statement, expression node, variable, constant)] for r_known_const."""
    c = getattr(ctx, '_ks', None)
    if c is not None: return c
    c = ctx._ks = []
    t = ctx.toks
    kn = _known(ctx)
    for n in ctx.nodes:
        M = kn.get(n.start)
        if not M or n.kind not in ('stmt', 'if', 'switch') or (n.kind == 'stmt' and ctx.is_decl(n)): continue
        root = _root_expr(ctx, n)
        if root is None: continue
        if n.kind == 'stmt' and root.kind in ('postfix', 'unary', 'assign') and root.kids and root.kids[0].kind == 'atom':
            v = t[root.kids[0].start][1]
            if v in M and n.parent is not None:
                d = None
                if root.kind == 'assign' and root.op in ('+=', '-='):
                    kv = permc.const_value(root.kids[1], t)
                    if kv is not None and not permc.has_side_effects(root.kids[1], t): d = kv if root.op == '+=' else -kv
                elif root.kind != 'assign' and root.op in ('++', '--'): d = 1 if root.op == '++' else -1
                if d is not None and _fits(_name_kind(ctx, v), M[v] + d): c.append(('step', n, root, v, M[v] + d))
        byval = {}
        for v, k in M.items(): byval.setdefault(k, []).append(v)
        for x in permc.ewalk(root):
            if x.kind == 'atom' and x.op == 'id' and t[x.start][1] in M and _is_read_atom(ctx, x) and _reads_safely(ctx, root, x, t[x.start][1]):
                c.append(('lit', n, x, t[x.start][1], M[t[x.start][1]]))
        for p in permc.ewalk(root):
            for i, x in enumerate(p.kids):
                if x.kind != 'atom' or x.op != 'num': continue
                # only where an integer variable can stand for the literal (not a null pointer, not a shift count)
                if p.kind == 'bin': ok = p.op not in _SEQ_OPS and not (p.op in ('<<', '>>') and i == 1) and _ikind(ctx, p.kids[1 - i]) is not None
                elif p.kind == 'assign': ok = i == 1 and p.op in ('=', '+=', '-=', '|=', '&=', '^=', '*=') and _ikind(ctx, p.kids[0]) is not None
                elif p.kind == 'index': ok = i == 1
                elif p.kind == 'call':
                    tys = _arg_types(ctx, p, i - 1, x) if i >= 1 and p.kids[0].kind == 'atom' else []
                    ok = bool(tys) and _tkind(ctx, tys[0]) is not None
                else: ok = False
                s = t[x.start][1]
                kv = permc.const_value(x, t)
                if not ok or kv is None or s[-1] in 'lL': continue
                uns = s[-1] in 'uU'
                for v in byval.get(kv, ()):
                    kd = _name_kind(ctx, v)
                    if (kd == (32, False)) != uns or not ctx.visible(v, x.start): continue      # the operand's type stays what it was
                    if _reads_safely(ctx, root, x, v): c.append(('var', n, x, v, kv))
                if not uns and p.kind in ('assign', 'call'):
                    for v, k0 in M.items():                # `dmin = cmin - 5;` for a constant near one a variable holds
                        if 0 < abs(kv - k0) <= 8 and _name_kind(ctx, v) != (32, False) and ctx.visible(v, x.start) and _reads_safely(ctx, root, x, v):
                            c.append(('rel', n, x, v, kv - k0))
    return c


def _known_edit(ctx, site):
    t = ctx.toks
    kind, n, x, v, k = site
    if kind == 'step':
        return Edit([(n.start, n.end, permc.new(f'{v} = {_lit(k)};', t[n.start][2]))], f'{v} = {_lit(k)} for its step @{_line(ctx, n.start)}')
    if kind == 'rel':
        s = f'{v} {"+" if k > 0 else "-"} {abs(k)}'
        if t[x.start - 1][1] not in ('=', '(', ',') or t[x.end][1] not in (';', ')', ','): s = f'({s})'
        return Edit([(x.start, x.end, permc.new(s, t[x.start][2]))], f'{t[x.start][1]} -> {s} (from what {v} holds) @{_line(ctx, x.start)}')
    if kind == 'lit':
        s = _lit(k) + ('u' if _name_kind(ctx, v) == (32, False) else '')
        return Edit([(x.start, x.end, permc.new(s, t[x.start][2]))], f'{v} -> {s} (known) @{_line(ctx, x.start)}')
    return Edit([(x.start, x.end, permc.new(v, t[x.start][2]))], f'{t[x.start][1]} -> {v} (holds it) @{_line(ctx, x.start)}')


def r_known_const(ctx, rng, everywhere=False):
    """C2 (extended): a constant taken from a variable that provably holds it at that point (`g = 0;
    f(a, g, g)` for `f(a, 0, 0)`; `flag = enable;` after `enable = 1;`; `g = x;` in the arm of
    `if (x == 8)`), the reverse (the variable's read written as the constant), and `v++;` written as
    `v = K + 1;` where v is known. The variable is a non-volatile integer scalar of at most 32 bits;
    its promoted type is the literal's (an unsigned int only stands for a `u` literal); see _known for
    how far a fact reaches."""
    sites = _known_sites(ctx)
    if not sites: return None
    return _known_edit(ctx, rng.choice(sites))


def enum_known_const(ctx):
    return [({'var': 1, 'rel': 3}.get(s[0], 2), _known_edit(ctx, s), s[3]) for s in _known_sites(ctx)]


r_known_const.enumerate = enum_known_const


def _const_eval(ctx, e, M):
    """Value of a side-effect-free integer expression whose variables are all known (M), computed only
    while every operand and result stays in 0 .. 2^31-1 (where C's int and Python agree); else None."""
    t = ctx.toks
    e = _unparen(e)
    if e.kind == 'atom':
        if e.op == 'num': v = permc.const_value(e, t)
        elif e.op == 'id': v = M.get(t[e.start][1])
        else: v = None
    elif e.kind == 'bin' and e.op in ('+', '-', '*', '/', '%', '<<', '>>', '&', '|', '^'):
        a, b = _const_eval(ctx, e.kids[0], M), _const_eval(ctx, e.kids[1], M)
        if a is None or b is None or (e.op in ('/', '%') and b == 0) or (e.op in ('<<', '>>') and b > 30): return None
        v = {'+': a + b, '-': a - b, '*': a * b, '/': a // b if b else 0, '%': a % b if b else 0, '<<': a << b, '>>': a >> b,
             '&': a & b, '|': a | b, '^': a ^ b}[e.op]
    else: return None
    return v if v is not None and 0 <= v < (1 << 31) else None


def r_formula_copy(ctx, rng, everywhere=False):
    """D35 (new kind): a constant store `x = K;` written with the formula another store to x uses in
    the same function, where every variable of the formula is known at that point and the formula
    comes to K there (`frames = (length - 1) / 8 + 1;` for `frames = 1;` just after `length = 5;`):
    armcc folds it late and leaves its pieces. All values stay in 0 .. 2^31-1."""
    t = ctx.toks
    kn = _known(ctx)
    forms = {}
    for n in ctx.nodes:
        e = ctx.stmt_expr(n) if n.kind == 'stmt' else None
        if e is None or e.kind != 'assign' or e.op != '=' or not permc.is_lvalue_simple(e.kids[0], t): continue
        r = e.kids[1]
        if permc.const_value(r, t) is None and not permc.has_side_effects(r, t) and _ikind(ctx, r) is not None:
            forms.setdefault(' '.join(x[1] for x in t[e.kids[0].start:e.kids[0].end]), {})[' '.join(x[1] for x in t[r.start:r.end])] = r
    cands = []
    for n in ctx.nodes:
        e = ctx.stmt_expr(n) if n.kind == 'stmt' else None
        M = kn.get(n.start)
        if e is None or not M or e.kind != 'assign' or e.op != '=' or t[e.kids[1].start][0] != 'num': continue
        kv = permc.const_value(e.kids[1], t)
        fs = forms.get(' '.join(x[1] for x in t[e.kids[0].start:e.kids[0].end]))
        if kv is None or not fs: continue
        base = _target(ctx, e.kids[0].end - 1)[0]
        for r in fs.values():
            ids = permc.idents(r, t)
            if base in ids or not all(i in M and ctx.visible(i, n.start) for i in ids): continue
            if _const_eval(ctx, r, M) == kv: cands.append((n, e, r))
    if not cands: return None
    n, e, r = rng.choice(cands)
    text = ctx.src(r.start, r.end)
    return Edit([(e.kids[1].start, e.kids[1].end, permc.new(text, t[e.kids[1].start][2]))],
                f'{ctx.src(e.kids[1].start, e.kids[1].end)} written as {text[:40]} @{_line(ctx, n.start)}')


# ---- where an assignment stands: once at the top, or before each use (and the reverse)

def _assign_site(ctx, n):
    """(variable, value expression, is a declaration) of `v = E;` or `T v = E;` with v a plain local and E
    a side-effect-free value that does not read v and is not volatile."""
    t = ctx.toks
    if n.kind != 'stmt': return None
    if ctx.is_decl(n):
        l = next((x for x in ctx.locals.values() if x.node is n), None)
        if l is None or not l.single or not l.init or not _plain_local(ctx, l.name): return None
        v, e, decl = l.name, ctx.expr(*l.init), True
    else:
        a = ctx.stmt_expr(n)
        if a is None or a.kind != 'assign' or a.op != '=' or a.kids[0].kind != 'atom' or not _plain_local(ctx, t[a.kids[0].start][1]): return None
        v, e, decl = t[a.kids[0].start][1], a.kids[1], False
    if e is None or permc.has_side_effects(e, t) or v in permc.idents(e, t): return None
    f = _fx(ctx, e.start, e.end)
    if f.vol or f.call: return None
    return v, e, decl


def _writes_of(ctx, v, a, b):
    """Token indexes in a..b where local v is stored to or stepped."""
    t = ctx.toks
    return [j for j in ctx.ids.get(v, ()) if a <= j < b and t[j - 1][1] not in ('.', '->')
            and (t[j + 1][1] in permc.ASSIGN or t[j + 1][1] in ('++', '--') or t[j - 1][1] in ('++', '--'))]


def _path_ranges(ctx, a, x):
    """Token ranges that can run between token a and node x (after a, in a's block or deeper): a..x
    without the then arm of each if whose else arm holds x."""
    segs = [(a, x.start)]
    m = x.parent
    child = x
    while m is not None and m.start >= a:
        if m.kind == 'if' and m.els is not None and m.els.start <= child.start < m.els.end:
            out = []
            for p, q in segs:
                if q <= m.then.start or p >= m.then.end: out.append((p, q))
                else:
                    if p < m.then.start: out.append((p, m.then.start))
                    if q > m.then.end: out.append((m.then.end, q))
            segs = out
        child, m = m, m.parent
    return segs


def r_sink_assign(ctx, rng, everywhere=False):
    """C1 / D6 (extended): `v = E;` (or the initialiser of `T v = E;`) moved down to just before the
    statement of each of its uses (one copy per use site: a value read at the top against the same
    assignment repeated in the branches that use it), and the reverse (equal assignments in several
    places made once before the first). v is a plain local written nowhere else in between; E has no
    side effects and nothing between the two places can change what it reads; no use is in a loop the
    assignment is outside of; no goto label lies between the two places."""
    t = ctx.toks
    labels = _goto_labels(ctx)
    downs, ups = [], []
    same = {}
    for blk in permc.blocks(ctx.root):
        for n in blk.children:
            s = _assign_site(ctx, n)
            if s is None: continue
            v, e, decl = s
            if not decl: same.setdefault(' '.join(x[1] for x in t[n.start:n.end]), []).append((n, v, e))
            occ = [j for j in _occ(ctx, v)[0] if j >= n.end]
            if not occ or any(not (blk.start < j < blk.end) for j in occ): continue      # (a use outside the block can see other values too)
            tg = {}
            for j in occ:
                s_u = _stmt_level(ctx, j, arms=True)
                if s_u is None or ctx.is_decl(s_u) or s_u.kind in ('label', 'pp'): tg = None; break
                tg[s_u.start] = s_u
            if not tg: continue
            tg = [x for x in tg.values() if not any(y is not x and y.start <= x.start and x.end <= y.end for y in tg.values())]
            last = max(x.start for x in tg)
            if _writes_of(ctx, v, n.end, max(x.end for x in tg)): continue
            if any(n.start <= q < max(x.end for x in tg) for q in labels): continue             # another path could come in between
            if any(ls <= x.start < le and not (ls <= n.start < le) for x in tg for ls, le in _loops(ctx)): continue
            if any(x.parent.kind != 'block' and x.parent.kind != 'if' for x in tg): continue          # (a loop's unbraced body is in the loop)
            fe = _fx(ctx, e.start, e.end)
            if not all(_store_indep(_fx(ctx, a, b), fe) for x in tg for a, b in _path_ranges(ctx, n.end, x)): continue
            if any(not ctx.visible(i, x.start) for x in tg for i in permc.idents(e, t) if i in ctx.locals): continue
            kids = blk.children
            if not decl and len(tg) == 1 and tg[0] is (kids[kids.index(n) + 1] if kids.index(n) + 1 < len(kids) else None): continue
            downs.append((n, v, e, decl, tg))
    for text, group in same.items():
        if len(group) < 2: continue
        group.sort(key=lambda g: g[0].start)
        v, e = group[0][1], group[0][2]
        first = group[0][0]
        K = first.parent
        while K is not None and not (K.kind == 'block' and all(K.start < g[0].start < K.end for g in group)): K = K.parent
        if K is None: continue
        P = next(c for c in K.children if c.start <= first.start < c.end)
        if ctx.is_decl(P) or P.kind in ('label', 'pp'): continue
        copies = {g[0].start for g in group}
        lastc = group[-1][0]
        if any(j not in copies for j in _writes_of(ctx, v, P.start, lastc.end)): continue
        if any(P.start <= q < lastc.end for q in labels): continue
        if any(ls <= g[0].start < le and not (ls <= P.start < le) for g in group for ls, le in _loops(ctx)): continue
        if not _store_indep(_fx(ctx, P.start, lastc.start), _fx(ctx, e.start, e.end)): continue
        ok = True
        for j in _occ(ctx, v)[0]:
            if j < P.start or j in copies: continue
            if not any(g[0].start < j and g[0].parent.start < j < g[0].parent.end for g in group): ok = False; break
        if ok and all(ctx.visible(i, P.start) for i in permc.idents(e, t) if i in ctx.locals) and ctx.visible(v, P.start):
            ups.append((group, P, v))
    if not downs and not ups: return None
    if downs and (not ups or rng.random() < 0.6):
        n, v, e, decl, tg = rng.choice(downs)
        text = f'{v} = {ctx.src(e.start, e.end)};'
        spans = [(e.start - 1, e.end, [])] if decl else [(n.start, n.end, [])]
        for x in tg:
            if x.parent.kind == 'block': spans.append(ctx.insert_at(x.start, text))
            else: spans.append((x.start, x.end, permc.new('{ ' + text + ' ' + ctx.src(x.start, x.end) + ' }', t[x.start][2])))
        return Edit(spans, f'{text[:40]} moved to its {len(tg)} use site{"s" if len(tg) > 1 else ""} @{_line(ctx, n.start)}')
    group, P, v = rng.choice(ups)
    text = ctx.src(group[0][0].start, group[0][0].end)
    spans = [(g[0].start, g[0].end, []) for g in group if g[0] is not P]
    if all(g[0] is not P for g in group): spans.append(ctx.insert_at(P.start, text))
    return Edit(spans, f'{len(group)} copies of {text[:40]} made once @{_line(ctx, P.start)}')


# ---- a step folded into the expression that reads the variable (`*p++`, `f(n++)`, `arr[--a]`)

def _step_stmt(ctx, n):
    """(variable, '++' or '--') when n is `v++;`, `++v;`, `v += 1;` (or the -- forms) on a private local."""
    t = ctx.toks
    e = ctx.stmt_expr(n) if n is not None and n.kind == 'stmt' else None
    if e is None or not e.kids or e.kids[0].kind != 'atom' or e.kids[0].op != 'id': return None
    v = t[e.kids[0].start][1]
    if v not in _private(ctx) or _vol_name(ctx, v): return None
    if e.kind in ('postfix', 'unary') and e.op in ('++', '--'): return v, e.op
    if e.kind == 'assign' and e.op in ('+=', '-=') and permc.const_value(e.kids[1], t) == 1 and t[e.kids[1].start][0] == 'num':
        return v, '++' if e.op == '+=' else '--'
    return None


def _one_read(ctx, root, v):
    """The only mention of v in root, when it is a read that is always evaluated."""
    t = ctx.toks
    occ = [a for a in permc.ewalk(root) if a.kind == 'atom' and a.op == 'id' and t[a.start][1] == v]
    if len(occ) != 1 or not _is_read_atom(ctx, occ[0]) or _seq_path(ctx, root, occ[0]) is None: return None
    return occ[0]


def _pre_text(ctx, x, op):
    t = ctx.toks
    s = op + t[x.start][1]
    return f'({s})' if t[x.end][1] in ('[', '.', '->', '(') or t[x.start - 1][1] in ('+', '-', '++', '--') else s


def r_step_fold(ctx, rng, everywhere=False):
    """D12 / D13 (extended): `x = *p; p++;` against `x = *p++;`, `f(n); n++;` against `f(n++);`, and
    `a--; y = arr[a];` against `y = arr[--a];`, both ways, for any statement that reads the variable
    exactly once at a place that is always evaluated. The variable is a private local (no other
    code can see the step move)."""
    t = ctx.toks
    cands = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            root = _root_expr(ctx, n)
            if root is None: continue
            nx = _step_stmt(ctx, kids[k + 1]) if k + 1 < len(kids) and n.kind == 'stmt' and ctx.stmt_expr(n) is not None else None
            if nx:
                x = _one_read(ctx, root, nx[0])
                if x is not None and root is not x: cands.append(('post_in', n, kids[k + 1], x, nx[1]))
            pv = _step_stmt(ctx, kids[k - 1]) if k else None
            if pv:
                x = _one_read(ctx, root, pv[0])
                if x is not None: cands.append(('pre_in', n, kids[k - 1], x, pv[1]))
            for a in permc.ewalk(root):
                if a is root or a.kind not in ('postfix', 'unary') or a.op not in ('++', '--') or a.kids[0].kind != 'atom': continue
                v = t[a.kids[0].start][1]
                if v not in _private(ctx) or _vol_name(ctx, v) or _seq_path(ctx, root, a) is None: continue
                if sum(1 for y in permc.ewalk(root) if y.kind == 'atom' and y.op == 'id' and t[y.start][1] == v) != 1: continue
                if a.kind == 'postfix' and n.kind == 'stmt' and ctx.stmt_expr(n) is not None: cands.append(('post_out', n, None, a, a.op))
                if a.kind == 'unary': cands.append(('pre_out', n, None, a, a.op))
    if not cands: return None
    kind, n, m, x, op = rng.choice(cands)
    if kind == 'post_in':
        v = t[x.start][1]
        return Edit([(x.start, x.end, permc.new(v + op, t[x.start][2])), (m.start, m.end, [])], f'{v}{op} into line {_line(ctx, n.start)}')
    if kind == 'pre_in':
        v = t[x.start][1]
        return Edit([(m.start, m.end, []), (x.start, x.end, permc.new(_pre_text(ctx, x, op), t[x.start][2]))], f'{op}{v} into line {_line(ctx, n.start)}')
    v = t[x.kids[0].start][1]
    a, b = x.start, x.end
    if t[a - 1][1] == '(' and ctx.pair.get(a - 1) == b and not (t[a - 2][0] == 'id' and t[a - 2][1] not in permc.KEYWORDS) and a - 1 > n.start:
        a, b = a - 1, b + 1
    if a <= n.start: return None
    rep = (a, b, permc.new(v, t[a][2]))
    if kind == 'post_out': return Edit([rep, ctx.insert_at(n.end, f'{v}{op};')], f'{v}{op} out of line {_line(ctx, n.start)}')
    return Edit([ctx.insert_at(n.start, f'{v}{op};'), rep], f'{op}{v} out of line {_line(ctx, n.start)}')


def r_loop_step(ctx, rng, everywhere=False):
    """D13 in a loop's test: `do { B; x--; } while (x != 0);` against `do { B } while (--x != 0);`, both
    ways (no `continue` in B, x a private local read once in the test), and
    `for (x = K; x != 0; x--) B` against `x = K; do B while (--x);` when the first test is known to pass
    (K a constant, as in loop_test) and B has no `continue`."""
    t = ctx.toks
    cands = []
    for blk in permc.blocks(ctx.root):
        for n in blk.children:
            if n.kind not in ('do', 'for'): continue
            body = n.body
            if any(t[i][1] == 'continue' for i in range(body.start, body.end)): continue
            if n.kind == 'do':
                ce = ctx.cond_expr(n)
                if ce is None: continue
                if body.kind == 'block' and len(body.children) >= 2:
                    st = _step_stmt(ctx, body.children[-1])
                    x = _one_read(ctx, ce, st[0]) if st else None
                    if x is not None and st[0] not in _declared(ctx, body): cands.append(('in', n, body.children[-1], x, st[1]))
                for a in permc.ewalk(ce):
                    if a.kind == 'unary' and a.op in ('++', '--') and a.kids[0].kind == 'atom' and t[a.kids[0].start][1] in _private(ctx) \
                            and _seq_path(ctx, ce, a) is not None and t[a.kids[0].start][1] not in _declared(ctx, body) \
                            and sum(1 for y in permc.ewalk(ce) if y.kind == 'atom' and y.op == 'id' and t[y.start][1] == t[a.kids[0].start][1]) == 1 \
                            and not (body.kind == 'block' and body.children and body.children[-1].kind == 'label'):
                        cands.append(('out', n, None, a, a.op))
            else:
                lp, rp = n.cond
                parts = _split_top(ctx, lp + 1, rp, ';')
                if len(parts) != 3 or any(p[0] >= p[1] for p in parts): continue
                ie, ce, se = (ctx.expr(*p) for p in parts)
                if ie is None or ce is None or se is None or ie.kind != 'assign' or ie.op != '=' or ie.kids[0].kind != 'atom': continue
                v = t[ie.kids[0].start][1]
                stp = se.kind in ('postfix', 'unary') and se.op in ('++', '--') and se.kids[0].kind == 'atom' and t[se.kids[0].start][1] == v
                if not stp or v not in _private(ctx) or not _true_for(ctx, v, ie.kids[1], ce) or v in _declared(ctx, body): continue
                x = _one_read(ctx, ce, v)
                if x is not None: cands.append(('for', n, parts, x, se.op))
    if not cands: return None
    kind, n, m, x, op = rng.choice(cands)
    S = ctx.src
    body = n.body
    lp, rp = n.cond
    if kind == 'in':
        cond = _replace_spans(ctx, lp + 1, rp, [(x.start, x.end, _pre_text(ctx, x, op))])
        text = 'do ' + S(body.start, m.start) + permc.render(t[m.end:body.end]) + f' while ({cond});'
    elif kind == 'out':
        v = t[x.kids[0].start][1]
        cond = _replace_spans(ctx, lp + 1, rp, [(x.start, x.end, v)])
        ind = ctx.indent(n.start)
        if body.kind == 'block':
            inner = ctx.indent(body.children[-1].start) if body.children else ind + '    '
            b2 = S(body.start, body.end - 1) + '\n' + inner + f'{v}{op};' + permc.render(t[body.end - 1:body.end])
        else: b2 = '{ ' + S(body.start, body.end) + f' {v}{op}; ' + '}'
        text = 'do ' + b2 + f' while ({cond});'
    else:
        ce = ctx.expr(*m[1])
        c = _unparen(ce)
        pre = _pre_text(ctx, x, op)
        if c.kind == 'bin' and c.op == '!=' and c.kids[0] is x and permc.const_value(c.kids[1], t) == 0 and rng.random() < 0.6: cond = op + t[x.start][1]
        else: cond = _replace_spans(ctx, m[1][0], m[1][1], [(x.start, x.end, pre)])
        text = S(*m[0]) + ';\n' + ctx.indent(n.start) + 'do ' + S(body.start, body.end) + f' while ({cond});'
    return Edit([(n.start, n.end, permc.new(text, t[n.start][2]))], f'loop step {kind} @{_line(ctx, n.start)}')


# ---- else after a jump, early return

def _ends_in_jump(ctx, arm):
    last = arm.children[-1] if arm.kind == 'block' and arm.children else arm
    return _jump_stmt(ctx, last)


def _splice_text(ctx, arm):
    """Source of an arm's statements for the enclosing block (its braces dropped when it declares nothing)."""
    if arm.kind != 'block': return ctx.src(arm.start, arm.end)
    if not arm.children: return ''
    if any(ctx.is_decl(c) or c.kind in ('label', 'pp') for c in arm.children): return ctx.src(arm.start, arm.end)
    return ctx.src(arm.children[0].start, arm.children[-1].end)


def r_early_exit(ctx, rng, everywhere=False):
    """D7 (extended): `if (c) { A; return; } else { B }` against `if (c) { A; return; } B` (an else after
    a jump, both ways), and `if (c) A else B` as the last statement of a void function or of a loop
    body against `if (c) { A; return; } B` (`continue;` in a loop), both ways."""
    t = ctx.toks
    void = _ret_type(ctx) is None
    cands = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        tail = 'return' if (blk is ctx.root and void) else 'continue' if (blk.parent is not None and blk.parent.kind in ('for', 'while', 'do')) else None
        for k, n in enumerate(kids):
            if n.kind != 'if': continue
            last = k == len(kids) - 1
            rest = kids[k + 1:]
            if n.els is not None:
                if _ends_in_jump(ctx, n.then): cands.append(('unwrap', n, None, None))
                elif last and tail and n.then.kind in ('block', 'stmt') and not (n.then.kind == 'stmt' and ctx.is_decl(n.then)):
                    cands.append(('early', n, tail, None))
            elif rest and not any(ctx.is_decl(c) or c.kind in ('label', 'pp') for c in rest) and _ends_in_jump(ctx, n.then):
                cands.append(('wrap', n, None, rest))
                lastj = n.then.children[-1] if n.then.kind == 'block' and n.then.children else n.then
                if tail and t[lastj.start][1] == tail and lastj.end - lastj.start == 2 and n.then.kind == 'block' and len(n.then.children) >= 2 \
                        and n.then.children[-2].kind != 'label':
                    cands.append(('late', n, lastj, rest))
    if not cands: return None
    op, n, x, rest = rng.choice(cands)
    S = ctx.src
    ind = ctx.indent(n.start)
    head = S(n.start, n.then.start) + ' '
    if op == 'unwrap':
        text = head + S(n.then.start, n.then.end) + '\n' + ind + _splice_text(ctx, n.els)
        return Edit([(n.start, n.end, permc.new(text, t[n.start][2]))], f'else after a jump dropped @{_line(ctx, n.start)}')
    if op == 'early':
        th = n.then
        if th.kind == 'block': arm = S(th.start, th.end - 1) + f' {x}; ' + '}'
        else: arm = '{ ' + S(th.start, th.end) + f' {x}; ' + '}'
        text = head + arm + '\n' + ind + _splice_text(ctx, n.els)
        return Edit([(n.start, n.end, permc.new(text, t[n.start][2]))], f'early {x} for the else @{_line(ctx, n.start)}')
    body = S(rest[0].start, rest[-1].end)
    if op == 'wrap':
        text = S(n.start, n.end) + ' else { ' + body + ' }'
    else:
        text = head + S(n.then.start, x.start) + permc.render(t[x.end:n.then.end]) + ' else { ' + body + ' }'
    return Edit([(n.start, rest[-1].end, permc.new(text, t[n.start][2]))], f'{"else around" if op == "wrap" else "jump dropped, else around"} the rest @{_line(ctx, n.start)}')


# ---- clamp forms

def r_clamp_form(ctx, rng, everywhere=False):
    """D18 (extended): `if (f > K) v = K; else v = f;` against `v = f; if (v > K) v = K;`, both ways (the
    ternary rule gives the third form). Same program when v and f are integers of the same kind (the
    copy changes no value); marked 'type' otherwise. f has no side effects, is not volatile and does
    not read v; K is a constant."""
    t = ctx.toks
    cands = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            if n.kind != 'if': continue
            c = _unparen(ctx.cond_expr(n)) if ctx.cond_expr(n) is not None else None
            if c is None or c.kind != 'bin' or c.op not in ('<', '>', '<=', '>=') or permc.const_value(c.kids[1], t) is None: continue
            a = _single_assign(ctx, n.then)
            if not a or ' '.join(a[1].split()) != ' '.join(ctx.src(c.kids[1].start, c.kids[1].end).split()): continue
            th = n.then.children[0] if n.then.kind == 'block' else n.then
            ae = ctx.stmt_expr(th)
            v, f = ae.kids[0], c.kids[0]
            if n.els is not None:
                b = _single_assign(ctx, n.els)
                if not b or b[0] != a[0] or ' '.join(b[1].split()) != ' '.join(ctx.src(f.start, f.end).split()): continue
                cands.append(('seq', n, None, v, f, c))
            elif k and kids[k - 1].kind == 'stmt' and _eq(ctx, f, v):
                pe = ctx.stmt_expr(kids[k - 1])
                if pe is not None and pe.kind == 'assign' and pe.op == '=' and _eq(ctx, pe.kids[0], v):
                    cands.append(('arms', n, kids[k - 1], v, pe.kids[1], c))
    out = []
    for op, n, p, v, f, c in cands:
        ff = _fx(ctx, f.start, f.end)
        if permc.has_side_effects(f, t) or ff.vol or ff.call or permc.has_side_effects(v, t) or _fx(ctx, v.start, v.end).vol: continue
        base = _target(ctx, v.end - 1)[0]
        if base is None or base in permc.idents(f, t) or (base not in _private(ctx) and ff.memr): continue
        kv, kf = _ikind(ctx, v), _ikind(ctx, f)
        out.append((op, n, p, v, f, c, 'same' if kv is not None and kv == kf and kv[1] is not None else 'type'))
    if not out: return None
    op, n, p, v, f, c, meaning = rng.choice(out)
    S = lambda z: ctx.src(z.start, z.end)
    if op == 'seq':
        text = f'{S(v)} = {S(f)};\n{ctx.indent(n.start)}if ({S(v)} {c.op} {S(c.kids[1])}) {S(v)} = {S(c.kids[1])};'
        return Edit([(n.start, n.end, permc.new(text, t[n.start][2]))], f'clamp: copy then test @{_line(ctx, n.start)}', meaning=meaning)
    text = f'if ({_opnd(ctx, f, c.op)} {c.op} {S(c.kids[1])}) {S(v)} = {S(c.kids[1])}; else {S(v)} = {S(f)};'
    return Edit([(p.start, n.end, permc.new(text, t[p.start][2]))], f'clamp: a store in each arm @{_line(ctx, p.start)}', meaning=meaning)


# ---- a statement that ends every arm of an if / else-if chain

def r_chain_tail(ctx, rng, everywhere=False):
    """D33 for a chain: a statement that ends every arm of `if ... else if ... else` moves to after the
    chain (the store three paths end with, written once), and the reverse. As if_hoist's tail forms:
    equal text, no arm-local names, no labels in the arms, and no arm that ends in a jump when sinking."""
    t = ctx.toks
    cands = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            if n.kind != 'if' or n.els is None or n.els.kind != 'if': continue
            arms, cur = [], n
            while cur is not None and cur.kind == 'if': arms.append(cur.then); cur = cur.els
            if cur is None: continue
            arms.append(cur)
            if any(a.kind not in ('block', 'stmt') or (a.kind == 'stmt' and ctx.is_decl(a)) for a in arms): continue
            if any(c.kind in ('label', 'pp') for a in arms for c in a.subs()): continue
            dn = set().union(*[_declared(ctx, a) for a in arms])
            if all(a.kind == 'block' and len(a.children) >= 2 for a in arms):
                x = arms[0].children[-1]
                if x.kind == 'stmt' and not ctx.is_decl(x) and all(_same_text(ctx, x, a.children[-1]) for a in arms[1:]) \
                        and not (_elem_ids(ctx, x.start, x.end) & dn):
                    cands.append(('out', n, x, arms))
            if k + 1 < len(kids):
                q = kids[k + 1]
                if q.kind == 'stmt' and not ctx.is_decl(q) and not (_elem_ids(ctx, q.start, q.end) & dn) \
                        and not any(_ends_in_jump(ctx, a) for a in arms):
                    cands.append(('in', n, q, arms))
    if not cands: return None
    op, n, x, arms = rng.choice(cands)
    s = ctx.src(x.start, x.end)
    desc = f'chain tail {op} {" ".join(s.split())[:30]} @{_line(ctx, n.start)}'
    if op == 'out':
        return Edit([(a.children[-1].start, a.children[-1].end, []) for a in arms] + [ctx.insert_at(n.end, s)], desc)
    spans = []
    for a in arms:
        if a.kind == 'block': spans.append(ctx.insert_at(a.end - 1, s))
        else: spans.append((a.start, a.end, permc.new('{ ' + ctx.src(a.start, a.end) + ' ' + s + ' }', t[a.start][2])))
    return Edit(spans + [(x.start, x.end, [])], desc)

# ---- another view of one read or store (D5 extended)

def _roots(ctx):
    """[(statement-level node, its expression)] for expression statements, returns, if and switch tests."""
    c = getattr(ctx, '_rts', None)
    if c is None:
        c = ctx._rts = [(n, e) for n in ctx.nodes for e in [_root_expr(ctx, n)] if e is not None]
    return c


def _global_scalar(ctx, x):
    """Type text of atom x when it names a non-volatile integer global scalar declared in the file."""
    t = ctx.toks
    if x.kind != 'atom' or x.op != 'id': return None
    nm = t[x.start][1]
    if nm in ctx.locals or nm in ctx.params or _is_macro(ctx, nm) or _vol_name(ctx, nm): return None
    g = ctx.types.globals.get(nm)
    if not g or g[1] or 'volatile' in g[0] or _tkind(ctx, g[0]) is None: return None
    return resolve_type(ctx, g[0])


def _view_sites(ctx):
    c = getattr(ctx, '_vs', None)
    if c is not None: return c
    c = ctx._vs = []
    t = ctx.toks
    for n, root in _roots(ctx):
        for x in permc.ewalk(root):
            ty = _global_scalar(ctx, x)
            if ty and t[x.start - 1][1] not in ('&', '++', '--', '.', '->', 'sizeof') and t[x.end][1] not in ('++', '--', '(', '[', '.', '->'):
                store = t[x.end][1] in permc.ASSIGN
                if store and t[x.end][1] != '=': continue
                k = _tkind(ctx, ty)
                for op in ('vol', 'cast') + (('wide',) if not store and k in ((8, False), (16, False)) else ()) \
                        + (('sign',) if k[1] is not None else ()): c.append((op, n, x, ty))
            if x.kind == 'unary' and x.op == '*' and x.kids[0].kind == 'cast':
                a = _unparen(x.kids[0].kids[0])
                if a.kind == 'unary' and a.op == '&':
                    ty = _global_scalar(ctx, _unparen(a.kids[0]))
                    ct = ' '.join(y[1] for y in t[x.kids[0].op[0]:x.kids[0].op[1]])
                    if ty and ct in (f'{ty} *', f'volatile {ty} *'): c.append(('plain', n, x, t[_unparen(a.kids[0]).start][1]))
    return c


def _view_edit(ctx, site):
    t = ctx.toks
    op, n, x, ty = site
    if op == 'plain':
        a, b = x.start, x.end
        if t[a - 1][1] == '(' and ctx.pair.get(a - 1) == b and not (t[a - 2][0] == 'id' and t[a - 2][1] not in permc.KEYWORDS): a, b = a - 1, b + 1
        return Edit([(a, b, permc.new(ty, t[a][2]))], f'plain access to {ty} @{_line(ctx, x.start)}')
    nm = t[x.start][1]
    if op == 'vol': new = f'(*(volatile {ty} *)&{nm})'
    elif op == 'cast': new = f'(*({ty} *)&{nm})'
    elif op == 'sign':
        k = _tkind(ctx, ty)
        new = f'(*({CANON[(k[0], not k[1])]} *)&{nm})'
    else:
        w = 'unsigned short' if _tkind(ctx, ty)[0] == 8 else 'unsigned int'
        new = f'(({ty})*({w} *)&{nm})'
    if t[x.end][1] in permc.ASSIGN and t[x.start - 1][1] in (';', '{', '}'): new = new[1:-1]
    return Edit([(x.start, x.end, permc.new(new, t[x.start][2]))], f'{op} view of {nm} @{_line(ctx, x.start)}',
                meaning='type' if op in ('wide', 'sign') else 'same')


def r_view_access(ctx, rng, everywhere=False):
    """D5 (extended): one read or store of an integer global through another view of it:
    `*(volatile T *)&x` (any read, call arguments too, or a store), `*(T *)&x`, or for a read of a byte
    or halfword the wider `(T)*(W *)&x` (the next bytes are read as well: marked 'type'); and such a
    view written back as the plain name. A view of the other sign (`*(short *)&g = v;`) is 'type' too."""
    s = _view_sites(ctx)
    return _view_edit(ctx, rng.choice(s)) if s else None


r_view_access.enumerate = lambda ctx: [(2.5 if s[0] != 'plain' else 1, _view_edit(ctx, s), s[0]) for s in _view_sites(ctx)]


# ---- casts and masks that change no value (D23 extended), mask spellings (D16)

_RING = ('+', '-', '*', '|', '&', '^')


def _ring_nodes(ctx, e, out, top=True):
    """Sub-expressions of e that feed its low bits only through + - * | & ^, unary - ~ and the left of
    <<: narrowing any of them to n bits leaves e's low n bits as they were."""
    if _ikind(ctx, e) is None: return
    if not top: out.append(e)
    if e.kind == 'paren': _ring_nodes(ctx, e.kids[0], out, False)
    elif e.kind == 'bin' and e.op in _RING:
        for k in e.kids: _ring_nodes(ctx, k, out, False)
    elif e.kind == 'bin' and e.op == '<<': _ring_nodes(ctx, e.kids[0], out, False)
    elif e.kind == 'unary' and e.op in ('-', '~'): _ring_nodes(ctx, e.kids[0], out, False)


def r_narrow_operand(ctx, rng, everywhere=False):
    """D23 (extended): a cast or mask that changes no value. An operand that is provably an unsigned
    byte or halfword gets `& 0xFF` / `& 0xFFFF` (or loses it); in a store to an integer target of 8 or
    16 bits, a sub-expression reached only through + - * | & ^ and << gets a cast to the target's type
    (or loses one): the stored low bits are the same either way."""
    t = ctx.toks
    cands = []
    for n, root in _roots(ctx):
        for p in permc.ewalk(root):
            if p.kind == 'bin':
                for i, k in enumerate(p.kids):
                    kd = _ikind(ctx, k) if k.kind in ('atom', 'index', 'member') and (k.kind != 'atom' or k.op == 'id') else None
                    if kd in ((8, False), (16, False)) and not (p.op == '&' and permc.const_value(p.kids[1 - i], t) in (0xff, 0xffff)):
                        cands.append(('mask', k, '0xFF' if kd[0] == 8 else '0xFFFF', None))
                if p.op == '&':
                    v = permc.const_value(p.kids[1], t)
                    kd = _ikind(ctx, p.kids[0]) if _unparen(p.kids[0]).kind in ('atom', 'index', 'member', 'cast') else None
                    if v in (0xff, 0xffff) and kd == ((8, False) if v == 0xff else (16, False)): cands.append(('unmask', p, None, p.kids[0]))
        if n.kind == 'stmt' and root.kind == 'assign' and root.op == '=':
            kd = _ikind(ctx, root.kids[0])
            ty = resolve_type(ctx, _etype(ctx, root.kids[0]))
            if kd and kd[0] < 32 and ty and _tkind(ctx, ty) == kd:
                ring = []
                _ring_nodes(ctx, root.kids[1], ring)
                for s in ring:
                    if s.kind == 'cast':
                        if ' '.join(y[1] for y in t[s.op[0]:s.op[1]]) == ty: cands.append(('uncast', s, None, s.kids[0]))
                    elif s.kind in ('bin', 'paren'): cands.append(('cast', s, ty, None))
    if not cands: return None
    op, x, arg, inner = rng.choice(cands)
    S = lambda z: ctx.src(z.start, z.end)
    if op == 'mask': new = f'({S(x)} & {arg})'
    elif op == 'unmask': new = S(_unparen(inner)) if _unparen(inner).kind in ('atom', 'index', 'member') else f'({S(_unparen(inner))})'
    elif op == 'cast': new = f'({arg}){S(x)}' if x.kind == 'paren' else f'({arg})({S(x)})'
    else:
        new = S(inner)
        u = _unparen(inner)
        # the cast's parentheses go too where the operators beside it bind less tightly than what was cast
        binop = lambda w: permc.PREC.get(w, 99) if w in permc.PREC else (0 if w in ('(', ',', ')', ';', '=', 'return') else 99)
        if inner.kind == 'paren' and u.kind == 'bin' and permc.PREC[u.op] > max(binop(t[x.start - 1][1]), binop(t[x.end][1])) \
                and not (t[x.start - 1][1] in permc.PREC and _operand_end(ctx, x.start - 2) is False):
            new = S(u)
    return Edit([(x.start, x.end, permc.new(new, t[x.start][2]))], f'{op} {S(x)[:30]} -> {new[:30]} @{_line(ctx, x.start)}')


def r_cast_neg(ctx, rng, everywhere=False):
    """D23: `(T)-E` against `-(T)E` (the order of the narrowing and the negation). The same value only
    as the whole of a store to a target no wider than T; otherwise marked 'type'."""
    t = ctx.toks
    cands = []
    for n, root in _roots(ctx):
        for x in permc.ewalk(root):
            if x.kind == 'cast' and x.kids[0].kind == 'unary' and x.kids[0].op == '-': form = 'in'
            elif x.kind == 'unary' and x.op == '-' and x.kids[0].kind == 'cast': form = 'out'
            else: continue
            ct = x if form == 'in' else x.kids[0]
            ty = ctx.src(ct.op[0], ct.op[1])
            kt = _tkind(ctx, ty)
            if kt is None: continue
            same = n.kind == 'stmt' and root.kind == 'assign' and root.op == '=' and _unparen(root.kids[1]) is x \
                and _ikind(ctx, root.kids[0]) is not None and _ikind(ctx, root.kids[0])[0] <= kt[0]
            cands.append((form, x, ty, same))
    if not cands: return None
    form, x, ty, same = rng.choice(cands)
    inner = x.kids[0].kids[0]
    new = f'-({ty}){paren(ctx, inner)}' if form == 'in' else f'({ty})-{paren(ctx, inner)}'
    return Edit([(x.start, x.end, permc.new(wrap(ctx, x, new), t[x.start][2]))], f'{ctx.src(x.start, x.end)[:30]} -> {new[:30]} @{_line(ctx, x.start)}',
                meaning='same' if same else 'type')


def _mask_const(ctx, e):
    """(value, written as ~K) of a mask constant `K` or `~K`."""
    t = ctx.toks
    e = _unparen(e)
    if e.kind == 'atom' and e.op == 'num': return permc.const_value(e, t), False
    if e.kind == 'unary' and e.op == '~' and e.kids[0].kind == 'atom' and e.kids[0].op == 'num':
        v = permc.const_value(e.kids[0], t)
        return (None, True) if v is None else (~v, True)
    return None, False


def _width(k):
    return k[0] if k[1] is False else 32


def r_mask_form(ctx, rng, everywhere=False):
    """D16 (extended): `x & 0xf0` against `x & ~0x0f` (and against bic; the complement is taken in the
    operand's width when it is an unsigned byte or halfword, else in 32 bits), and a mask or set
    statement in two steps: `v &= 0x1fbe;` against `v &= ~0x41; v &= ~0xe000;`, `v |= 0x30;` against
    `v |= 0x10; v |= 0x20;` (a load can then stand between them). Integers of at most 32 bits, v
    without side effects and not volatile."""
    t = ctx.toks
    cands = []
    for n, root in _roots(ctx):
        for x in permc.ewalk(root):
            if not (x.kind == 'bin' and x.op == '&' or x.kind == 'assign' and x.op == '&='): continue
            v, neg = _mask_const(ctx, x.kids[1])
            kd = _ikind(ctx, x.kids[0])
            if v is None or kd is None: continue
            w = _width(kd)
            full = (1 << w) - 1
            other = ~v & full
            if other == 0 or (not neg and not 0 <= v <= full) or kd[1] is not False: continue     # (both spellings then have one type)
            cands.append(('not', n, x, (f'{v & full:#x}' if neg else f'~{other:#x}')))
        if n.kind == 'stmt' and root.kind == 'assign' and root.op in ('&=', '|=') and n.parent is not None and n.parent.kind == 'block' \
                and permc.is_lvalue_simple(root.kids[0], t) and not _fx(ctx, root.start, root.end).vol:
            v, neg = _mask_const(ctx, root.kids[1])
            kd = _ikind(ctx, root.kids[0])
            if v is None or kd is None: continue
            full = (1 << _width(kd)) - 1
            bits = [b for b in range(_width(kd)) if ((~v if root.op == '&=' else v) & full) >> b & 1]
            if len(bits) >= 2: cands.append(('two', n, root, bits))
    if not cands: return None
    op, n, x, arg = rng.choice(cands)
    if op == 'not':
        k = x.kids[1]
        return Edit([(k.start, k.end, permc.new(arg, t[k.start][2]))], f'mask {ctx.src(k.start, k.end)} -> {arg} @{_line(ctx, k.start)}')
    cut = rng.randrange(1, len(arg))
    if rng.random() < 0.5: cut = 1 if rng.random() < 0.5 else len(arg) - 1
    a, b = sum(1 << i for i in arg[:cut]), sum(1 << i for i in arg[cut:])
    L = ctx.src(x.kids[0].start, x.kids[0].end)
    f = (lambda m: f'~{m:#x}') if x.op == '&=' else (lambda m: f'{m:#x}')
    text = f'{L} {x.op} {f(a)};\n{ctx.indent(n.start)}{L} {x.op} {f(b)};'
    return Edit([(n.start, n.end, permc.new(text, t[n.start][2]))], f'{L} {x.op} in two steps @{_line(ctx, n.start)}')


# ---- a value through the store's own target, a sub-expression through a local or a helper

def r_via_target(ctx, rng, everywhere=False):
    """D27 (extended): `w = v * 102 / 100;` against `w = v * 102; w = w / 100;` (any binary operator at
    the top of the value), both ways. w is an int or unsigned int and the first operand has exactly
    w's type after promotion, so the copy through w changes nothing; the second operand has no side
    effects and cannot be changed by the store to w; nothing is volatile."""
    t = ctx.toks
    cands = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            e = ctx.stmt_expr(n) if n.kind == 'stmt' else None
            if e is None or e.kind != 'assign' or e.op != '=' or not permc.is_lvalue_simple(e.kids[0], t): continue
            L = e.kids[0]
            kl = _ikind(ctx, L)
            if kl is None or kl[0] != 32: continue
            W = _fx(ctx, L.start, L.end + 1)
            if W.vol or W.call: continue
            r = _unparen(e.kids[1])
            ok2 = lambda z: _ikind(ctx, z) is not None and not permc.has_side_effects(z, t) and _store_indep(W, _fx(ctx, z.start, z.end)) \
                and not _fx(ctx, z.start, z.end).vol
            ok1 = lambda z: _ikind(ctx, z) is not None and _promote(_ikind(ctx, z)) == kl and not permc.has_side_effects(z, t) \
                and not _fx(ctx, z.start, z.end).vol and not _fx(ctx, z.start, z.end).call
            if r.kind == 'bin' and r.op in permc.PREC and r.op not in _SEQ_OPS and r.op not in NEG and ok1(r.kids[0]) and ok2(r.kids[1]):
                cands.append(('two', n, None, L, r))
            if k + 1 < len(kids) and kids[k + 1].kind == 'stmt' and ok1(e.kids[1]):
                f = ctx.stmt_expr(kids[k + 1])
                if f is None or f.kind != 'assign' or not _eq(ctx, f.kids[0], L): continue
                if f.op == '=':
                    g = _unparen(f.kids[1])
                    if g.kind == 'bin' and g.op in permc.PREC and g.op not in _SEQ_OPS and g.op not in NEG and _eq(ctx, g.kids[0], L) and ok2(g.kids[1]):
                        cands.append(('one', n, kids[k + 1], e.kids[1], (g.op, g.kids[1])))
                elif f.op[:-1] in ('/', '%', '<<', '>>', '*') and ok2(f.kids[1]):
                    cands.append(('one', n, kids[k + 1], e.kids[1], (f.op[:-1], f.kids[1])))
    if not cands: return None
    op, n, m, a, b = rng.choice(cands)
    S = lambda z: ctx.src(z.start, z.end)
    if op == 'two':
        Ls, r = S(a), b
        second = f'{Ls} = {Ls} {r.op} {_opnd(ctx, r.kids[1], r.op, True)};' if rng.random() < 0.6 else f'{Ls} {r.op}= {S(_unparen(r.kids[1]))};'
        text = f'{Ls} = {S(_unparen(r.kids[0]))};\n{ctx.indent(n.start)}{second}'
        return Edit([(n.start, n.end, permc.new(text, t[n.start][2]))], f'{Ls} takes its value in two steps @{_line(ctx, n.start)}')
    e = ctx.stmt_expr(n)
    text = f'{S(e.kids[0])} = {_opnd(ctx, a, b[0])} {b[0]} {_opnd(ctx, b[1], b[0], True)};'
    return Edit([(n.start, m.end, permc.new(text, t[n.start][2]))], f'{S(e.kids[0])} takes its value in one statement @{_line(ctx, n.start)}')


def _sub_sites(ctx):
    """[(statement, root, sub-expression)]: arithmetic sub-expressions (not the whole value) that are
    side-effect-free integers, always evaluated."""
    c = getattr(ctx, '_ss', None)
    if c is not None: return c
    c = ctx._ss = []
    t = ctx.toks
    for n, root in _roots(ctx):
        top = _unparen(root.kids[1]) if root.kind == 'assign' else _unparen(root)
        for x in permc.ewalk(root):
            if x.kind != 'bin' or x.op in _SEQ_OPS or x is top or _ikind(ctx, x) is None or permc.has_side_effects(x, t): continue
            if permc.const_value(x.kids[0], t) is not None and permc.const_value(x.kids[1], t) is not None: continue
            f = _fx(ctx, x.start, x.end)
            if f.vol or f.call or _seq_path(ctx, root, x) is None: continue
            c.append((n, root, x))
    return c


def _sub_local_edit(ctx, n, root, x, ty, meaning='same'):
    t = ctx.toks
    nm = ctx.fresh('pmt_x')
    body = _replace_spans(ctx, n.start, n.end, [(x.start, x.end, nm)])
    return Edit([(n.start, n.end, permc.new('{ ' + f'{ty} {nm} = {ctx.src(x.start, x.end)}; ' + body + ' }', t[n.start][2]))],
                f'{ctx.src(x.start, x.end)[:30]} through a {ty} local @{_line(ctx, n.start)}', meaning=meaning)


def r_subexpr_local(ctx, rng, everywhere=False):
    """B1 add (extended): an arithmetic sub-expression of a statement computed first into a block local
    (`k = f + 2731; v = (k + 5) / 10;` for `v = (f + 2731 + 5) / 10;`). The local has the
    sub-expression's own promoted type (int or unsigned int): the same program; with another integer
    type the edit is marked 'type'. The sub-expression is always evaluated and no call or store
    beside it in the statement can change what it reads."""
    s = [(n, r, x) for n, r, x in _sub_sites(ctx) if n.kind == 'stmt' and _hoist_safe(ctx, r, x)]
    if not s: return None
    n, root, x = rng.choice(s)
    exact = CANON[_promote(_ikind(ctx, x))]
    if rng.random() < 0.7: return _sub_local_edit(ctx, n, root, x, exact)
    ty = rng.choice([y for y in ('int', 'unsigned int', 'unsigned short', 'short', 'unsigned char') if y != exact])
    return _sub_local_edit(ctx, n, root, x, ty, 'type')


r_subexpr_local.enumerate = lambda ctx: [(2.5, _sub_local_edit(ctx, n, r, x, CANON[_promote(_ikind(ctx, x))]))
                                         for n, r, x in _sub_sites(ctx) if n.kind == 'stmt' and _hoist_safe(ctx, r, x)]


def _helper_params(ctx, a, b):
    """[(type text, name)] for the locals and parameters tokens a..b mention, or None when one of them
    cannot be passed by value under its declared type (an array, a name declared twice, unknown type)."""
    t = ctx.toks
    out, seen = [], set()
    if _macro_hides(ctx, a, b)[1]: return None                  # a macro there uses a local by name
    for i in range(a, b):
        w = t[i][1]
        if t[i][0] != 'id' or w in seen or t[i - 1][1] in ('.', '->') or not (w in ctx.locals or w in ctx.params): continue
        seen.add(w)
        l = ctx.locals.get(w)
        if l is not None and a <= l.node.start < b and w not in ctx.dup_names: continue          # declared inside the range itself
        if w not in _private(ctx) or w in _array_params(ctx): return None
        if l is not None and (l.dims or any(t[q][1] in ('static', 'register') for q in range(l.node.start, l.node.end))): return None
        key = _type_key(ctx, w)
        if not key or '(' in key: return None
        out.append((key, w))
    return out


def _helper_def(ctx, text):
    """Span edit that defines a helper before FUNC."""
    t = ctx.toks
    s = ctx.start
    return (s, s + 1, permc.new(text + '\n', t[s][2]) + [Tok(t[s][0], t[s][1], '\n')])


def r_expr_helper(ctx, rng, everywhere=False):
    """B6 (extended): an arithmetic sub-expression through a `static __forceinline` helper that returns
    it (`rated_x8()` for `rated * 8`, `clamp(v)`), the locals it reads passed by value under their
    declared types, the result in the expression's own promoted type. helper_inline is the inverse."""
    t = ctx.toks
    if rng.random() < 0.2:
        # an identity helper round one read of a local (`id(x)`): a value of its own, made after the caller's
        ats = [x for n, r in _roots(ctx) for x in permc.ewalk(r) if x.kind == 'atom' and x.op == 'id' and t[x.start][1] in _private(ctx)
               and _is_read_atom(ctx, x) and t[x.end][1] not in ('[', '(', '.', '->') and _name_kind(ctx, t[x.start][1]) is not None]
        if ats:
            x = rng.choice(ats)
            ty = _type_key(ctx, t[x.start][1])
            want = f'static __forceinline {ty} pmt_id{{}}({ty} x) {{ return x; }}'
            fn = next((t[i][1] for i in range(ctx.start) if t[i][0] == 'id' and re.match(r'pmt_id\d+$', t[i][1]) and t[i + 1][1] == '('
                       and [y[1] for y in t[i - 3:i]] == ['static', '__forceinline'] + [ty] and t[i + 2][1] == ty), None)
            spans = [(x.start, x.end, permc.new(f'{fn or ctx.fresh("pmt_id")}({t[x.start][1]})', t[x.start][2]))]
            if fn is None: spans.append(_helper_def(ctx, f'static __forceinline {ty} {ctx.fresh("pmt_id")}({ty} x) {{ return x; }}'))
            return Edit(spans, f'{t[x.start][1]} through an identity helper @{_line(ctx, x.start)}')
    s = [(n, r, x) for n, r, x in _sub_sites(ctx) if _helper_params(ctx, x.start, x.end) is not None]
    if not s: return None
    n, root, x = rng.choice(s)
    ps = _helper_params(ctx, x.start, x.end)
    fn = ctx.fresh('pmt_ex')
    rt = CANON[_promote(_ikind(ctx, x))]
    sig = ', '.join(f'{ty} {nm}' for ty, nm in ps) or 'void'
    d = _helper_def(ctx, f'static __forceinline {rt} {fn}({sig}) {{ return {ctx.src(x.start, x.end)}; }}')
    call = f'{fn}({", ".join(nm for _, nm in ps)})'
    return Edit([d, (x.start, x.end, permc.new(call, t[x.start][2]))], f'{ctx.src(x.start, x.end)[:30]} through helper {fn} @{_line(ctx, x.start)}')


def r_addr_helper(ctx, rng, everywhere=False):
    """B6 / B7 (extended): a store target or read through a helper that returns its address
    (`*flag_word(i) = val;` with `static __forceinline T *flag_word(unsigned int i) { return &flags[i]; }`),
    or a global struct through a view helper (`view()->f` for `g.f`, at one use or with everywhere at
    all). T is the declared type (as for setter); index locals are passed by value. A call of such a
    helper made here is written back when its arguments are the helper's own parameter names."""
    t = ctx.toks
    elems, views, backs = [], {}, []
    made = {}
    i = 0
    while i < ctx.start:
        if t[i][1] == '{': i = ctx.pair[i] + 1; continue
        if t[i][0] == 'id' and re.match(r'pmt_(ad|vw)\d+$', t[i][1]) and t[i + 1][1] == '(':
            rp = ctx.pair[i + 1]
            if t[rp + 1][1] == '{':
                rb = ctx.pair[rp + 1]
                s = i
                while s > 0 and t[s - 1][0] != 'pp' and t[s - 1][1] not in (';', '}'): s -= 1
                ps = [t[q][1] for q in range(i + 2, rp) if t[q][0] == 'id' and t[q + 1][1] in (',', ')') and t[q][1] != 'void']
                body = t[rp + 2:rb]
                if len(body) > 3 and body[0][1] == 'return' and body[1][1] == '&' and body[-1][1] == ';':
                    made[t[i][1]] = (s, rb + 1, ps, ''.join(x[2] + x[1] for x in body[2:-1]).strip())
                i = rb + 1; continue
        i += 1
    for n, root in _roots(ctx):
        for x in permc.ewalk(root):
            if x.kind == 'call' and x.kids[0].kind == 'atom' and t[x.kids[0].start][1] in made:
                fn = t[x.kids[0].start][1]
                if [ctx.src(a.start, a.end) for a in x.kids[1:]] == made[fn][2]: backs.append((x, fn))
            if x.kind == 'member' and x.op == '.' and x.kids[0].kind == 'atom':
                g = t[x.kids[0].start][1]
                gt = ctx.types.globals.get(g)
                if gt and not gt[1] and '*' not in gt[0] and 'volatile' not in gt[0] and g not in ctx.locals and g not in ctx.params \
                        and not _is_macro(ctx, g) and gt[0].replace('const ', '').strip() in ctx.types.struct_fields:
                    views.setdefault(g, []).append(x)
            if x.kind not in ('index', 'member') or t[x.end][1] in ('.', '[', '->', '('): continue
            if t[x.start - 1][1] in ('&', 'sizeof') or permc.has_side_effects(x, t): continue
            ty = _setter_type(ctx, x)
            ps = _helper_params(ctx, x.start, x.end)
            if ty and ps is not None: elems.append((x, ty, ps))
    opts = [o for o, c in (('elem', elems), ('view', views), ('back', backs)) if c]
    if not opts: return None
    op = rng.choice(opts)
    if op == 'back':
        x, fn = rng.choice(backs)
        s, e, ps, body = made[fn]
        a, b = x.start, x.end
        if fn.startswith('pmt_ad'):
            if t[a - 1][1] != '*': return None
            a -= 1
            if t[a - 1][1] == '(' and ctx.pair.get(a - 1) == b: a, b = a - 1, b + 1
            spans = [(a, b, permc.new(body, t[a][2]))]
        else:
            if t[b][1] != '->': return None
            spans = [(a, b + 1, permc.new(body + '.', t[a][2]))]
        if sum(1 for y in t if y[1] == fn) == 2: spans.append((s, e, []))
        return Edit(spans, f'helper {fn} written out @{_line(ctx, x.start)}')
    if op == 'view':
        g = rng.choice(sorted(views))
        fn = ctx.fresh('pmt_vw')
        ty = ctx.types.globals[g][0].replace('const ', '').strip()
        uses = views[g] if everywhere else [rng.choice(views[g])]
        seen, spans = set(), []
        for x in uses:
            if x.kids[0].start in seen: continue
            seen.add(x.kids[0].start)
            spans.append((x.kids[0].start, x.kids[0].end + 1, permc.new(f'{fn}()->', t[x.kids[0].start][2])))
        spans.append(_helper_def(ctx, f'static __forceinline {ty} *{fn}(void) {{ return &{g}; }}'))
        return Edit(spans, f'{g} through view helper {fn} at {len(spans) - 1} use{"s" if len(spans) > 2 else ""}')
    x, ty, ps = rng.choice(elems)
    fn = ctx.fresh('pmt_ad')
    sig = ', '.join(f'{pt} {nm}' for pt, nm in ps) or 'void'
    call = f'*{fn}({", ".join(nm for _, nm in ps)})'
    if t[x.end][1] in ('++', '--') or t[x.start - 1][1] in ('++', '--', '/', '*'): call = f'({call})'
    d = _helper_def(ctx, f'static __forceinline {ty} *{fn}({sig}) {{ return &{ctx.src(x.start, x.end)}; }}')
    return Edit([d, (x.start, x.end, permc.new(call, t[x.start][2]))], f'{ctx.src(x.start, x.end)[:30]} through address helper {fn} @{_line(ctx, x.start)}')


# ---- a run of statements as an inline helper (B6 extended), and several reads cached at once

def _outline_sites(ctx, maxlen=5):
    """[(block, first child index, count)]: runs of statements a helper can hold: nothing jumps out or in,
    no declaration, every local they mention can be passed by value, at most one local is stored to."""
    c = getattr(ctx, '_ol', None)
    if c is not None: return c
    c = ctx._ol = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for i in range(len(kids)):
            for j in range(i, min(len(kids), i + maxlen)):
                if not _movable(ctx, kids[j]): break
                if _outline_ok(ctx, blk, i, j - i + 1): c.append((blk, i, j - i + 1))
    return c


def _outline_ok(ctx, blk, i, cnt):
    kids = blk.children
    if i + cnt > len(kids) or not all(_movable(ctx, kids[q]) for q in range(i, i + cnt)) or (cnt == 1 and kids[i].kind == 'block'): return False
    a, b = kids[i].start, kids[i + cnt - 1].end
    ps = _helper_params(ctx, a, b)
    if ps is None: return False
    if any(nm in _addr_names(ctx) for _, nm in ps): return False
    return not any(ctx.toks[q][1] in ('__asm', 'return') for q in range(a, b))


def _outline_edit(ctx, site, everywhere=False, addr=None):
    t = ctx.toks
    blk, i, cnt = site
    kids = blk.children
    a, b = kids[i].start, kids[i + cnt - 1].end
    ps = _helper_params(ctx, a, b)
    f = _fx(ctx, a, b)
    # a local the run alone mentions is declared in the helper; one it stores to is returned, or (two or
    # more, or by choice) passed by address
    inner = [(ty, nm) for ty, nm in ps if nm in ctx.locals and not ctx.locals[nm].init
             and all(a <= o < b for o in _occ(ctx, nm)[0])]
    ps = [p for p in ps if p not in inner]
    wr = [nm for _, nm in ps if nm in f.writes]
    if addr is None: addr = len(wr) > 1
    if len(wr) > 1: addr = True
    body = ctx.src(a, b)
    fn = ctx.fresh('pmt_fn')
    args = list(ps)
    pre = ''.join(f'{ty} {nm}; ' for ty, nm in inner)
    post = ''
    rt = 'void'
    w = wr[0] if wr and not addr else None
    if w is not None:
        rt = _type_key(ctx, w)
        first = next(q for q in range(i, i + cnt) if any(kids[q].start <= o < kids[q].end for o in ctx.ids[w]))
        if _plain_def(ctx, kids[first], w):
            args = [p for p in ps if p[1] != w]
            pre += f'{rt} {w}; '
        post = f' return {w};'
    if addr and wr:
        body = _replace_spans(ctx, a, b, [(o, o + 1, f'(*{nm})') for nm in wr for o in _occ(ctx, nm)[0] if a <= o < b])
    sig = ', '.join(f'{ty} *{nm}' if (addr and nm in wr) else f'{ty} {nm}' for ty, nm in args) or 'void'
    call = f'{fn}({", ".join(("&" + nm) if (addr and nm in wr) else nm for _, nm in args)});'
    if w is not None: call = f'{w} = {call}'
    spans = [_helper_def(ctx, f'static __forceinline {rt} {fn}({sig}) {{ {pre}{body}{post} }}'), (a, b, permc.new(call, t[a][2]))]
    n = 1
    if everywhere and not inner:
        want = [x[1] for x in t[a:b]]
        for blk2, i2, c2 in _outline_sites(ctx):
            k2 = blk2.children
            a2, b2 = k2[i2].start, k2[i2 + c2 - 1].end
            if c2 == cnt and (a2 >= b or b2 <= a) and [x[1] for x in t[a2:b2]] == want and not any(a2 < s[1] and s[0] < b2 for s in spans[1:]):
                spans.append((a2, b2, permc.new(call, t[a2][2]))); n += 1
    return Edit(spans, f'{cnt} statement{"s" if cnt > 1 else ""} at line {_line(ctx, a)} into helper {fn}' + (f' ({n} places)' if n > 1 else '')
                + (' (by address)' if addr and wr else ''))


def r_outline_block(ctx, rng, everywhere=False):
    """B6 (extended): a run of one to five statements moved into a `static __forceinline` helper called in
    its place (armcc allocates the inlined code after the caller's own). The locals the run reads are
    passed by value; a local only the run mentions is declared inside the helper; a local it stores to
    is returned (`x = helper(...)`, declared inside the helper when the run sets it first) or, when
    there are two or more or by choice, passed by address (`helper(&x)`); with everywhere, every other run with the same text calls the same
    helper. The run has no jump out, label or declaration at its level. Inverse: a helper made here,
    called with its own parameter names, is written out again."""
    t = ctx.toks
    backs = []
    for n, root in _roots(ctx):
        if n.kind != 'stmt': continue
        c = root.kids[1] if root.kind == 'assign' and root.op == '=' else root
        if c.kind == 'call' and c.kids[0].kind == 'atom' and re.match(r'pmt_fn\d+$', t[c.kids[0].start][1]) and n.parent is not None:
            backs.append((n, root, c))
    blks = getattr(ctx, '_olb', None)
    if blks is None: blks = ctx._olb = [b for b in permc.blocks(ctx.root) if b.children]
    sites = []
    for _ in range(16):                         # (the sweep lists every run; a draw checks a few)
        if not blks: break
        blk = rng.choice(blks)
        i, cnt = rng.randrange(len(blk.children)), rng.choice((1, 1, 2, 2, 2, 3, 3, 4, 5))
        if _outline_ok(ctx, blk, i, cnt): sites.append((blk, i, cnt)); break
    if backs and (not sites or rng.random() < 0.3):
        n, root, c = rng.choice(backs)
        fn = t[c.kids[0].start][1]
        i = next((q for q in range(ctx.start) if t[q][1] == fn and t[q + 1][1] == '(' and t[ctx.pair[q + 1] + 1][1] == '{'), None)
        if i is None: return None
        rp = ctx.pair[i + 1]
        rb = ctx.pair[rp + 1]
        ps = [t[q][1] for q in range(i + 2, rp) if t[q][0] == 'id' and t[q + 1][1] in (',', ')') and t[q][1] != 'void']
        if [ctx.src(x.start, x.end) for x in c.kids[1:]] != ps: return None
        a, b = rp + 2, rb
        if root.kind == 'assign':
            w = ctx.src(root.kids[0].start, root.kids[0].end)
            if [x[1] for x in t[b - 3:b]] != ['return', w, ';']: return None
            b -= 3
            if t[a + 1][1] == w and t[a + 2][1] == ';': a += 3
            elif w not in ps: return None
        s = i
        while s > 0 and t[s - 1][0] != 'pp' and t[s - 1][1] not in (';', '}'): s -= 1
        spans = [(n.start, n.end, permc.new(ctx.src(a, b), t[n.start][2]))]
        if n.parent.kind != 'block': spans = [(n.start, n.end, permc.new('{ ' + ctx.src(a, b) + ' }', t[n.start][2]))]
        if sum(1 for y in t if y[1] == fn) == 2: spans.append((s, rb + 1, []))
        return Edit(spans, f'helper {fn} written out @{_line(ctx, n.start)}')
    if not sites: return None
    return _outline_edit(ctx, rng.choice(sites), everywhere, True if rng.random() < 0.3 else None)


r_outline_block.enumerate = lambda ctx: [(2 if s[2] in (2, 3) else 3, _outline_edit(ctx, s)) for s in _outline_sites(ctx)]


def _cache_runs(ctx):
    """[(block, first index, last index)]: maximal runs of two or more plain expression statements."""
    out = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        i = 0
        while i < len(kids):
            j = i
            while j < len(kids) and kids[j].kind == 'stmt' and ctx.stmt_expr(kids[j]) is not None: j += 1
            if j - i >= 2: out.append((blk, i, j - 1))
            i = max(j, i + 1)
    return out


def _cache_group_edit(ctx, run, pick=None):
    t = ctx.toks
    blk, i, j = run
    kids = blk.children
    found = {}
    for q in range(i, j + 1):
        e = ctx.stmt_expr(kids[q])
        for x in reads(ctx, e):
            ty = resolve_type(ctx, _etype(ctx, x))
            if not ty or ty.endswith('*') or _fx(ctx, x.start, x.end).vol: continue
            key = ' '.join(y[1] for y in t[x.start:x.end])
            T = _fx(ctx, x.start, x.end)
            ok = _hoist_safe(ctx, e, x) and all(_store_indep(_nfx(ctx, kids[p]), T) for p in range(i, q))
            ent = found.setdefault(key, [ty, [], True])
            ent[1].append(x); ent[2] = ent[2] and ok
    good = [(k, v) for k, v in found.items() if v[2]]
    if pick is not None: good = pick(good)
    if len(good) < 2: return None
    good.sort(key=lambda kv: kv[1][1][0].start)
    decl, reps = [], []
    base = ctx.fresh('pmt_g')
    for n, (key, (ty, xs, _)) in enumerate(good):
        nm = f'{base}_{n}'
        decl.append(f'{ty} {nm} = {ctx.src(xs[0].start, xs[0].end)};')
        seen = set()
        for x in xs:
            if x.start not in seen: seen.add(x.start); reps.append((x.start, x.end, nm))
    a, b = kids[i].start, kids[j].end
    return Edit([(a, b, permc.new('{ ' + ' '.join(decl) + ' ' + _replace_spans(ctx, a, b, reps) + ' }', t[a][2]))],
                f'{len(good)} reads cached before the run at line {_line(ctx, a)}')


def r_cache_group(ctx, rng, everywhere=False):
    """B1 add, several at once: in a run of plain statements, each global, field or element read is
    loaded into a block local before the run and the statements read the locals (several fields
    loaded before any store). A read is taken only when no statement of the run before its use, and
    nothing beside it in its own statement, can change it. Every such read, or a random half."""
    runs = _cache_runs(ctx)
    if not runs: return None
    for _ in range(6):
        run = rng.choice(runs)
        ed = _cache_group_edit(ctx, run, None if rng.random() < 0.6 else (lambda g: rng.sample(g, max(2, len(g) // 2)) if len(g) >= 2 else g))
        if ed is not None: return ed
    return None


r_cache_group.enumerate = lambda ctx: [(2.5, ed) for ed in (_cache_group_edit(ctx, r) for r in _cache_runs(ctx)) if ed is not None]


# ---- smaller forms

def r_scope_init(ctx, rng, everywhere=False):
    """B3 (extended): a local with a constant initialiser (`char s[8] = "...";`) moved between function
    scope and the one block that uses it. The block is not in a loop and no goto label lies before its
    end, so the initialiser runs once before the first use either way."""
    t = ctx.toks
    labels = _goto_labels(ctx)
    cands = []
    for l in ctx.locals.values():
        if not l.single or not l.init or l.name in ctx.dup_names or l.name in ctx.params: continue
        if any(t[q][0] == 'id' or t[q][1] in ('(', '&', '*') for q in range(*l.init)): continue
        if any(t[q][1] == 'static' for q in range(l.node.start, l.node.end)): continue
        occ = [j for j in ctx.ids.get(l.name, []) if not (l.node.start <= j < l.node.end)]
        if not occ: continue
        if l.block is ctx.root:
            best = None
            for b in permc.blocks(ctx.root):
                if b is not ctx.root and all(b.start < j < b.end for j in occ) and (best is None or b.start > best.start): best = b
            if best is None or any(ls <= best.start < le for ls, le in _loops(ctx)) or any(q < best.end for q in labels): continue
            if best.parent is not None and best.parent.kind == 'switch': continue
            cands.append((l, best))
        elif not any(ls <= l.block.start < le for ls, le in _loops(ctx)) and all(l.block.start < j < l.block.end for j in occ) \
                and not any(q < l.block.end for q in labels):
            cands.append((l, ctx.root))
    if not cands: return None
    l, dest = rng.choice(cands)
    text = ctx.src(l.node.start, l.node.end)
    at = dest.children[0].start if dest.children else dest.end - 1
    if dest is ctx.root: at = _decl_point(ctx, ctx.root)
    if l.node.start <= at < l.node.end: return None
    return Edit([(l.node.start, l.node.end, []), ctx.insert_at(at, text)],
                f'initialised local {l.name} to {"function scope" if dest is ctx.root else "the block at line " + str(_line(ctx, dest.start))}')


def r_union_split(ctx, rng, everywhere=False):
    """D21 (extended): the two bytes of a 16-bit value taken through a union local (`u.w = x;` then
    `u.b[1]` for `x >> 8` and `u.b[0]` for `x & 0xff` or `(uchar)x`), which goes through the stack. x is
    a side-effect-free unsigned halfword read that nothing beside it in the statement can change; the
    target is little-endian."""
    t = ctx.toks
    cands = []
    for n, root in _roots(ctx):
        if n.kind != 'stmt': continue
        got = {}
        for p in permc.ewalk(root):
            x = which = None
            if p.kind == 'bin' and p.op == '>>' and permc.const_value(p.kids[1], t) == 8: x, which = _unparen(p.kids[0]), 1
            elif p.kind == 'bin' and p.op == '&' and permc.const_value(p.kids[1], t) == 0xff: x, which = _unparen(p.kids[0]), 0
            elif p.kind == 'cast' and _tkind(ctx, ctx.src(p.op[0], p.op[1])) == (8, False): x, which = _unparen(p.kids[0]), 0
            if x is None or x.kind not in ('atom', 'index', 'member') or _ikind(ctx, x) != (16, False) or permc.has_side_effects(x, t): continue
            if _fx(ctx, x.start, x.end).vol or not _hoist_safe(ctx, root, x): continue
            got.setdefault(' '.join(y[1] for y in t[x.start:x.end]), []).append((p, which, x))
        for key, uses in got.items(): cands.append((n, uses))
    if not cands: return None
    n, uses = rng.choice(cands)
    nm = ctx.fresh('pmt_un')
    x = uses[0][2]
    reps, seen = [], []
    for p, which, _ in sorted(uses, key=lambda u: u[0].start):
        if any(a <= p.start < b for a, b in seen): continue
        seen.append((p.start, p.end))
        reps.append((p.start, p.end, f'{nm}.b[{which}]'))
    body = _replace_spans(ctx, n.start, n.end, reps)
    text = '{ ' + f'union {{ unsigned short w; unsigned char b[2]; }} {nm}; {nm}.w = {ctx.src(x.start, x.end)}; ' + body + ' }'
    return Edit([(n.start, n.end, permc.new(text, t[n.start][2]))], f'bytes of {ctx.src(x.start, x.end)[:30]} through a union @{_line(ctx, n.start)}')


def r_repeat_test(ctx, rng, everywhere=False):
    """D5 (extended): an operand of || or && that reads a volatile written twice in a row
    (`flags[0] != 0 || flags[0] != 0 || ...`: the image reads the byte twice), or such a repeat
    removed. The number of volatile reads changes: marked 'type'."""
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        e = ctx.cond_expr(n)
        if e is None: continue
        for x in permc.ewalk(e):
            if x.kind != 'bin' or x.op not in _SEQ_OPS: continue
            l, r = x.kids
            ll = l.kids[1] if l.kind == 'bin' and l.op == x.op else l
            if _eq(ctx, ll, r) and not permc.has_side_effects(r, t): cands.append(('drop', x, l))
            for k in (l, r):
                if not (k.kind == 'bin' and k.op in _SEQ_OPS) and not permc.has_side_effects(k, t) and _fx(ctx, k.start, k.end).vol:
                    cands.append(('twice', x, k))
    if not cands: return None
    op, x, k = rng.choice(cands)
    if op == 'drop': return Edit([(x.start, x.end, permc.new(ctx.src(k.start, k.end), t[x.start][2]))], f'repeated test dropped @{_line(ctx, x.start)}', meaning='type')
    s = optext(ctx, k, x.op)
    return Edit([(k.start, k.end, permc.new(f'{s} {x.op} {s}', t[k.start][2]))], f'test {s[:30]} twice @{_line(ctx, k.start)}', meaning='type')


def r_index_split(ctx, rng, everywhere=False):
    """D26 (extended): `p[0x47]` against `(p + 0x40)[7]` (a constant index in two parts), both ways."""
    t = ctx.toks
    cands = []
    for n, root in _roots(ctx):
        for x in permc.ewalk(root):
            if x.kind != 'index': continue
            k = permc.const_value(x.kids[1], t)
            b = x.kids[0]
            if k is not None and k >= 2 and t[x.kids[1].start][0] == 'num' and x.kids[1].end - x.kids[1].start == 1: cands.append(('split', x, k))
            if k is not None and b.kind == 'paren' and b.kids[0].kind == 'bin' and b.kids[0].op == '+' and _ikind(ctx, b.kids[0].kids[0]) is None:
                k1 = permc.const_value(b.kids[0].kids[1], t)
                if k1 is not None and k1 >= 0 and k >= 0: cands.append(('join', x, k + k1))
    if not cands: return None
    op, x, k = rng.choice(cands)
    hexa = t[x.kids[1].start][1].lower().startswith('0x')
    f = (lambda v: hex(v)) if hexa or k > 15 else str
    if op == 'split':
        k1 = rng.choice([v for v in sorted({k & ~0xf, k & ~0x3, k - 1, k // 2, 1}) if 0 < v < k] or [1])
        new = f'({paren(ctx, x.kids[0])} + {f(k1)})[{f(k - k1)}]'
    else:
        new = f'{paren(ctx, x.kids[0].kids[0].kids[0])}[{f(k)}]'
    return Edit([(x.start, x.end, permc.new(new, t[x.start][2]))], f'{ctx.src(x.start, x.end)[:30]} -> {new[:30]} @{_line(ctx, x.start)}')


def r_cmp_diff(ctx, rng, everywhere=False):
    """generic: `a == b` against `a - b == 0` (and !=), and a stored value written `E + K - K`, for
    integers of at most 32 bits (equal modulo 2^32)."""
    t = ctx.toks
    cands = []
    for n, root in _roots(ctx):
        for x in permc.ewalk(root):
            if x.kind == 'bin' and x.op in ('==', '!=') and _ikind(ctx, x.kids[0]) is not None and _ikind(ctx, x.kids[1]) is not None:
                l = _unparen(x.kids[0])
                if permc.const_value(x.kids[1], t) == 0 and l.kind == 'bin' and l.op == '-':
                    cands.append((x, f'{_opnd(ctx, l.kids[0], x.op)} {x.op} {_opnd(ctx, l.kids[1], x.op, True)}'))
                elif permc.const_value(x.kids[1], t) != 0 and permc.const_value(x.kids[0], t) is None:
                    cands.append((x, f'{_opnd(ctx, x.kids[0], "-")} - {_opnd(ctx, x.kids[1], "-", True)} {x.op} 0'))
        if n.kind == 'stmt' and root.kind == 'assign' and root.op == '=' and _ikind(ctx, root.kids[1]) is not None and _ikind(ctx, root.kids[0]) is not None:
            r = _unparen(root.kids[1])
            if r.kind == 'bin' and r.op == '-' and _unparen(r.kids[0]).kind == 'bin' and _unparen(r.kids[0]).op == '+' \
                    and permc.const_value(r.kids[1], t) is not None and _eq(ctx, _unparen(r.kids[0]).kids[1], r.kids[1]):
                z = _unparen(r.kids[0]).kids[0]
                cands.append((root.kids[1], ctx.src(z.start, z.end)))
            elif rng.random() < 0.3 and permc.const_value(r, t) is None:
                cands.append((root.kids[1], f'{_opnd(ctx, r, "+")} + 0x100 - 0x100'))
    if not cands: return None
    x, new = rng.choice(cands)
    return Edit([(x.start, x.end, permc.new(wrap(ctx, x, new) if x.kind == 'bin' and x.op in NEG else new, t[x.start][2]))],
                f'{ctx.src(x.start, x.end)[:30]} -> {new[:34]} @{_line(ctx, x.start)}')


def r_index_step(ctx, rng, everywhere=False):
    """D12 for a global or field index: `g.buf[g.n] = c; g.n++;` against `g.buf[g.n++] = c;`, both ways.
    The index is a non-volatile integer lvalue mentioned once in the statement, and nothing else in
    the statement calls or can read or change it."""
    t = ctx.toks
    cands = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            e = ctx.stmt_expr(n) if n.kind == 'stmt' else None
            if e is None or e.kind != 'assign' or e.kids[0].kind != 'index': continue
            idx = e.kids[0].kids[1]
            post = idx.kind == 'postfix' and idx.op in ('++', '--')
            v = idx.kids[0] if post else idx
            if v.kind == 'atom' and (v.op != 'id' or t[v.start][1] in _private(ctx)): continue           # (locals: postinc_index, step_fold)
            if not permc.is_lvalue_simple(v, t) or _ikind(ctx, v) is None or _fx(ctx, v.start, v.end).vol: continue
            key = [y[1] for y in t[v.start:v.end]]
            # the value and the array's own expression only read, and not through a pointer (which could be the index)
            rest = _fx(ctx, e.kids[1].start, e.kids[1].end), _fx(ctx, e.kids[0].kids[0].start, e.kids[0].kids[0].end)
            if any(r.call or r.memw or r.writes or r.memr for r in rest) or permc.has_side_effects(e.kids[1], t): continue
            if _target(ctx, v.end - 1)[0] is None: continue
            if sum(1 for q in range(n.start, n.end - len(key) + 1) if [y[1] for y in t[q:q + len(key)]] == key) != 1: continue
            if post: cands.append(('out', n, None, idx, v))
            elif k + 1 < len(kids) and kids[k + 1].kind == 'stmt':
                f = ctx.stmt_expr(kids[k + 1])
                if f is not None and f.kind in ('postfix', 'unary') and f.op in ('++', '--') and _eq(ctx, f.kids[0], v): cands.append(('in', n, kids[k + 1], idx, f.op))
    if not cands: return None
    op, n, m, idx, a = rng.choice(cands)
    if op == 'in':
        return Edit([(idx.start, idx.end, permc.new(ctx.src(idx.start, idx.end) + a, t[idx.start][2])), (m.start, m.end, [])],
                    f'{ctx.src(idx.start, idx.end)}{a} into the index @{_line(ctx, n.start)}')
    s = ctx.src(a.start, a.end)
    return Edit([(idx.start, idx.end, permc.new(s, t[idx.start][2])), ctx.insert_at(n.end, f'{s}{idx.op};')], f'{s}{idx.op} out of the index @{_line(ctx, n.start)}')

# ---- declarations in the file: packing, a struct as a plain array, field and signature types

def _struct_defs(ctx):
    """{typedef name: (index of `typedef`, index of {, index of }, is __packed)} for the `typedef struct { }
    Name;` definitions in the file before FUNC."""
    c = getattr(ctx, '_sd', None)
    if c is not None: return c
    c = ctx._sd = {}
    t = ctx.toks
    i = 0
    while i < ctx.start:
        if t[i][1] == 'typedef':
            j = i + 1
            packed = False
            while t[j][1] in ('__packed', 'const', 'volatile'): packed |= t[j][1] == '__packed'; j += 1
            if t[j][1] == 'struct':
                j += 1
                if t[j][0] == 'id' and t[j + 1][1] == '{': j += 1
                if t[j][1] == '{':
                    rb = ctx.pair[j]
                    if t[rb + 1][0] == 'id' and t[rb + 2][1] == ';': c[t[rb + 1][1]] = (i, j, rb, packed)
                    i = rb; continue
        elif t[i][1] == '{': i = ctx.pair[i]
        i += 1
    return c


def _natural_layout(ctx, name):
    """[(field, offset, size, count)] and the struct's size when every field is an integer scalar (or a
    one-dimension array of one) of known size; plus whether the natural layout has any padding. None
    when a field is something else."""
    fl = ctx.types.struct_fields.get(name)
    td = ctx.types.typedefs.get(name, '')
    if not fl or ':' in td: return None
    off, out, pad, align = 0, [], False, 1
    for f, ty, dims in fl:
        sz = _scalar_size(ctx, ty) if _tkind(ctx, ty) is not None else None
        if sz is None: return None
        cnt = 1
        if dims:
            m = re.fullmatch(r'\[\s*(\d+|0[xX][0-9a-fA-F]+)\s*\]', dims.strip())
            if not m: return None
            cnt = int(m.group(1), 0)
        if off % sz: pad = True; off += sz - off % sz
        out.append((f, off, sz, cnt, ty))
        off += sz * cnt
        align = max(align, sz)
    if off % align: pad = True; off += align - off % align
    return out, off, pad


def _used_types(ctx):
    """Struct typedef names of the globals, locals and parameters FUNC mentions."""
    t = ctx.toks
    out = set()
    for w in ctx.ids:
        for ty in (ctx.types.globals.get(w, ('',))[0], ctx.params.get(w, ''), ctx.locals[w].type if w in ctx.locals else ''):
            for x in ty.replace('*', ' ').split(): out.add(x)
    return out


def r_packed_struct(ctx, rng, everywhere=False):
    """A7 (extended): `__packed` put on, or taken off, a `typedef struct { ... } S;` of the file that FUNC
    uses (loads from a packed struct are scheduled earlier and reloaded more). Only a struct of integer
    scalars that no other struct of the file contains and no header mentions. The same program when
    its natural layout has no padding; marked 'type' when packing would move a field."""
    t = ctx.toks
    used = _used_types(ctx)
    hid = getattr(ctx.types, 'header_ids', ())
    cands = []
    for name, (i, lb, rb, packed) in sorted(_struct_defs(ctx).items()):
        lay = _natural_layout(ctx, name)
        if name not in used or name in hid or lay is None: continue
        if any(x[1] == name and not (q == rb + 1) and any(a < q < b for _, a, b, _ in _struct_defs(ctx).values())
               for q, x in enumerate(t[:ctx.start])): continue
        cands.append((name, i, packed, lay[2]))
    if not cands: return None
    name, i, packed, pad = rng.choice(cands)
    if packed:
        q = next(j for j in range(i, i + 4) if t[j][1] == '__packed')
        span = (q, q + 2, [Tok(t[q + 1][0], t[q + 1][1], t[q][2])])
    else:
        span = (i + 1, i + 2, [Tok('id', '__packed', ' '), t[i + 1]])
    return Edit([span], f'struct {name}: {"plain" if packed else "__packed"}', meaning='type' if pad else 'same')


def r_struct_array(ctx, rng, everywhere=False):
    """A8 (in part): a global struct whose fields all have one integer type, declared instead as a plain
    array of that type (`extern T g[N];`) with every `g.f` in the file written `g[k]` (type-based
    aliasing: a struct's field stays in a register across stores to that type, an array element is
    reloaded). Only when the file declares g once, no header mentions it, and every mention of g in
    the file is a field access (an array field always indexed)."""
    t = ctx.toks
    fd = _file_decls(ctx)
    hid = getattr(ctx.types, 'header_ids', ())
    cands = []
    for g in sorted(w for w in ctx.ids if w in fd and len(fd[w]) == 1 and w not in ctx.locals and w not in ctx.params and w not in hid):
        s, e, j, (a, b) = fd[g][0]
        if b - a != 1 or sum(1 for v in fd.values() for x in v if x[0] == s) != 1: continue
        words = [t[q][1] for q in range(s, j)]
        base = [w for w in words if w not in ('extern', 'static')]
        if len(base) != 1 or base[0] not in _struct_defs(ctx): continue
        lay = _natural_layout(ctx, base[0])
        if lay is None or lay[2] or len({x[4] for x in lay[0]}) != 1 or _struct_defs(ctx)[base[0]][3]: continue
        fields = {f: (off, sz, cnt) for f, off, sz, cnt, ty in lay[0]}
        spans, ok = [], True
        for q in range(len(t)):
            if t[q][1] != g or t[q][0] != 'id' or q == a: continue
            if t[q - 1][1] in ('.', '->') : continue
            if t[q + 1][1] != '.' or t[q + 2][1] not in fields: ok = False; break
            off, sz, cnt = fields[t[q + 2][1]]
            k = off // sz
            if cnt > 1 or ctx.types.struct_fields[base[0]][[x[0] for x in lay[0]].index(t[q + 2][1])][2]:
                if t[q + 3][1] != '[': ok = False; break
                rbk = ctx.pair[q + 3]
                spans.append((q + 1, q + 4, permc.new(f'[{k} + (', '')))
                spans.append((rbk, rbk + 1, permc.new(')]', '')))
            else:
                spans.append((q + 1, q + 3, permc.new(f'[{k}]', '')))
        if not ok or not spans: continue
        ty = lay[0][0][4]
        spans.append((s, e, permc.new(' '.join([w for w in words if w in ('extern', 'static')] + [ty]) + f' {g}[{lay[1] // lay[0][0][2]}]', t[s][2])))
        cands.append((g, base[0], spans))
    if not cands: return None
    g, sname, spans = rng.choice(cands)
    return Edit(spans, f'global {g}: struct {sname} declared as a plain array')


def _int_alts(ctx, k):
    """Other spellings an integer type of kind k may take: same size other sign first, then other sizes."""
    td = lambda alt: next((n for n, v in ctx.types.typedefs.items() if v == alt and n in ('uchar', 'ushort', 'uint', 'u8', 'u16', 'u32', 's8', 's16', 's32')), alt)
    out = []
    for kk in ((8, False), (8, True), (16, False), (16, True), (32, False), (32, True)):
        if kk != k: out.append(td(CANON[kk]))
    return out


def r_field_type(ctx, rng, everywhere=False):
    """A7 (extended): the declared type of one integer field of a struct the file defines, changed to the
    other sign of the same size (a signed product stored to a `u16` field is masked, to a `short`
    field it is not). A field FUNC mentions, declared alone in its statement, in a struct no header
    mentions. Marked 'type'."""
    t = ctx.toks
    hid = getattr(ctx.types, 'header_ids', ())
    names = {t[i][1] for i in range(ctx.lb, ctx.rb) if t[i][0] == 'id' and t[i - 1][1] in ('.', '->')}
    cands = []
    for sname, (i, lb, rb, packed) in sorted(_struct_defs(ctx).items()):
        if sname in hid: continue
        for a, b in _split_top(ctx, lb + 1, rb, ';'):
            if b - a < 2 or any(t[q][1] in (',', ':', '*', '(', '{') for q in range(a, b)): continue
            q = a
            while q < b and ctx.types.is_type_word(t[q][1]): q += 1
            if q == a or q >= b or t[q][0] != 'id' or t[q][1] not in names: continue
            k = _tkind(ctx, ' '.join(x[1] for x in t[a:q]))
            if k is not None and k[1] is not None: cands.append((sname, t[q][1], a, q, k))
    if not cands: return None
    sname, f, a, q, k = rng.choice(cands)
    alt = CANON[(k[0], not k[1])]
    alt = next((n for n, v in ctx.types.typedefs.items() if v == alt and n in ('uchar', 'ushort', 'uint', 'u8', 'u16', 'u32', 's8', 's16', 's32')), alt)
    return Edit([(a, q, permc.new(alt, t[a][2]))], f'field {sname}.{f}: {ctx.src(a, q)} -> {alt}', meaning='type')


def r_signature_type(ctx, rng, everywhere=False):
    """A11 (extended): an integer parameter type or the return type changed, for FUNC (in its definition and
    every declaration of it in the file) or for a function FUNC calls that the file only declares (the
    caller masks a result, and narrows an argument, by the prototype it saw). Not when a header
    mentions the function. Marked 'type'."""
    t = ctx.toks
    hid = getattr(ctx.types, 'header_ids', ())
    called = {t[i][1] for i in range(ctx.lb, ctx.rb) if t[i][0] == 'id' and t[i + 1][1] == '(' and t[i][1] not in permc.KEYWORDS and t[i][1] not in ctx.locals}
    ds = _fn_decls(ctx, called | {ctx.func})
    cands = []
    for nm, decls in sorted(ds.items()):
        if None in decls or nm in hid: continue
        if nm != ctx.func and any(d[2] for d in decls): continue
        slots = None
        for s, i, is_def in decls:
            rp = ctx.pair[i + 1]
            quals = ('static', 'extern', 'inline', '__inline', '__forceinline', '__weak', 'register', '__irq')
            ra = next((q for q in range(s, i) if t[q][1] not in quals), i)
            cur = [(ra, i)]
            for a, b in _split_top(ctx, i + 2, rp, ','):
                e = b - 1 if (b - a >= 2 and t[b - 1][0] == 'id' and not ctx.types.is_type_word(t[b - 1][1])) else b
                cur.append((a, e))
            sig = [' '.join(x[1] for x in t[a:b]) for a, b in cur]
            if slots is None: slots = (sig, [cur])
            elif slots[0] != sig: slots = None; break
            else: slots[1].append(cur)
        if not slots: continue
        for n_, ty in enumerate(slots[0]):
            k = _tkind(ctx, ty)
            if k is not None: cands.append((nm, n_, ty, k, [c[n_] for c in slots[1]]))
    if not cands: return None
    nm, n_, ty, k, spans = rng.choice(cands)
    alt = rng.choice(_int_alts(ctx, k)[:3] if rng.random() < 0.7 else _int_alts(ctx, k))
    return Edit([(a, b, permc.new(alt, t[a][2])) for a, b in spans], f'{nm}: {"return type" if n_ == 0 else "parameter " + str(n_)} {ty} -> {alt}', meaning='type')


# ---- dead code that can survive to allocation: tests whose two outcomes meet, pointers, array operands

def _dead_test_points(ctx):
    """[(token to insert before, jump word, the dead test already there or None)]: places where
    `if (E) jump;` changes nothing: the end of a loop's body (continue), just before a case's own break
    (break), the end of a void function (return)."""
    t = ctx.toks
    out = []
    void = _ret_type(ctx) is None

    def old(n, jump):
        if n is None or n.kind != 'if' or n.els is not None or n.then.kind != 'stmt' or t[n.then.start][1] != jump or n.then.end - n.then.start != 2: return None
        ce = ctx.cond_expr(n)
        return n if ce is not None and not permc.has_side_effects(ce, t) and not _fx(ctx, n.cond[0], n.cond[1]).vol else None
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        if not kids: continue
        last = kids[-1]
        if blk.parent is not None and blk.parent.kind in ('for', 'while', 'do') and not _jump_stmt(ctx, last) and last.kind != 'label':
            out.append((blk.end - 1, 'continue', old(last, 'continue')))
        if blk is ctx.root and void and not _jump_stmt(ctx, last) and last.kind != 'label': out.append((blk.end - 1, 'return', old(last, 'return')))
        if blk.parent is not None and blk.parent.kind == 'switch':
            for k, c in enumerate(kids):
                if c.kind == 'stmt' and t[c.start][1] == 'break' and k and kids[k - 1].kind != 'label' and not ctx.is_decl(kids[k - 1]):
                    out.append((c.start, 'break', old(kids[k - 1], 'break')))
    return out


def _dead_test_edit(ctx, i, jump, e):
    return Edit([ctx.insert_at(i, f'if ({e}) {jump};')], f'dead test if ({e}) {jump}; @{_line(ctx, i)}', meaning='probe')


def r_probe_test(ctx, rng, everywhere=False):
    """D36 (new kind, dead code): a test armcc deletes because both outcomes reach the same code, which
    still counts as a use of the locals it reads: `if (E) continue;` at the end of a loop's body,
    `if (E) break;` just before a case's own break, `if (E) return;` at the end of a void function. E
    compares a local or parameter with a constant or another local. Also removes such a test."""
    pts = _dead_test_points(ctx)
    olds = [n for _, _, n in pts if n is not None]
    if olds and rng.random() < 0.25:
        n = rng.choice(olds)
        return Edit([(n.start, n.end, [])], f'dead test removed @{_line(ctx, n.start)}', meaning='probe')
    if not pts: return None
    i, jump, _ = rng.choice(pts)
    ops = [o for o in _operands_near(ctx, i, rng, want_local=True) if o in _private(ctx)]
    if not ops: return None
    x = rng.choice(ops)
    others = [o for o in set(ops) if o != x]
    e = f'{x} == {rng.choice(sorted(others))}' if others and rng.random() < 0.3 else f'{x} == {_const(ctx, rng, at=i)}'
    return _dead_test_edit(ctx, i, jump, e)


def enum_probe_test(ctx):
    out = []
    for i, jump, _ in _dead_test_points(ctx):
        names = [x for x in _names_near(ctx, i, want_local=True) if x in _private(ctx) and _name_kind(ctx, x) is not None][:3]
        for n_, x in enumerate(names):
            out.append((2 + n_, _dead_test_edit(ctx, i, jump, f'{x} == 0'), x))
            for y in names[n_ + 1:]: out.append((3 + n_, _dead_test_edit(ctx, i, jump, f'{x} == {y}'), x))
    return out


r_probe_test.enumerate = enum_probe_test


def _elem_forms(ctx, i):
    """Probe expressions at token i that compare the address of an array element at a local index."""
    t = ctx.toks
    arrs = sorted({w for w in ctx.ids if w not in ctx.locals and w not in ctx.params and (ctx.types.globals.get(w) or ('', None))[1]},
                  key=lambda g: min(abs(k - i) for k in ctx.ids[g]))[:2]
    idx = [x for x in _names_near(ctx, i, want_local=True) if _name_kind(ctx, x) is not None][:2]
    gs = sorted({w for w in ctx.ids if w in ctx.types.globals and w not in ctx.types.functions and not ctx.types.globals[w][1]
                 and w not in ctx.locals and w not in ctx.params}, key=lambda g: min(abs(k - i) for k in ctx.ids[g]))[:1]
    out = []
    for a in arrs:
        for x in idx:
            out.append(f'&{a}[{x} + 1] != 0')
            for g in gs: out.append(f'(const void *)&{g} != (const void *)&{a}[{x} + 1]')
    return out


def r_probe_elem(ctx, rng, everywhere=False):
    """D2 (extended, dead code): an unread comparison of an array element's address, stored in a block
    local: `&tbl[i + 1] != 0`, or a global's address against `&tbl[i + 1]` (the array's base, or the
    global's address, is then a value used at that point: in a loop it becomes a loop value)."""
    pts = _probe_points(ctx)
    for _ in range(8):
        if not pts: return None
        i = rng.choice(pts)[0]
        fs = _elem_forms(ctx, i)
        if fs: return _probe_edit(ctx, i, rng.choice(fs))
    return None


r_probe_elem.enumerate = lambda ctx: [(3 + n, _probe_edit(ctx, p[0], f)) for p in _probe_points(ctx) for n, f in enumerate(_elem_forms(ctx, p[0])[:2])]


def _ptr_probe_edit(ctx, blk, i, g, other):
    t = ctx.toks
    nm = ctx.fresh('pmt_q')
    d = _decl_point(ctx, blk)
    decl = f'const void *{nm} = (const void *)&{g};'
    probe = _probe_text(f'{nm} != (const void *)&{other}')
    if d == i: return Edit([ctx.insert_at(i, decl + ' ' + probe)], f'pointer {nm} = &{g}, compared at once @{_line(ctx, i)}', meaning='probe')
    return Edit([ctx.insert_at(d, decl), ctx.insert_at(i, probe)], f'pointer {nm} = &{g} held to line {_line(ctx, i)}', meaning='probe')


def _ptr_probe_sites(ctx, step=1):
    out = []
    gs = sorted(w for w in ctx.ids if w in ctx.types.globals and w not in ctx.types.functions and w not in ctx.locals and w not in ctx.params
                and not _is_macro(ctx, w))
    if len(gs) < 2: return out
    for blk in permc.blocks(ctx.root):
        kids = [c for c in blk.children if not ctx.is_decl(c) and c.kind not in ('label', 'pp')]
        if blk.parent is not None and blk.parent.kind == 'switch': continue
        for k in range(1, len(kids), step):
            i = kids[k].start
            near = sorted(gs, key=lambda g: (min(abs(q - i) for q in ctx.ids[g]), g))[:3]
            out.append((blk, i, near))
    return out


def r_probe_ptr(ctx, rng, everywhere=False):
    """C4 (dead code): an unread pointer local set to a global's address at the top of a block and
    compared, unread, with another global's address later in the block: the pointer holds a register
    through allocation from the block's start to the comparison."""
    s = _ptr_probe_sites(ctx)
    if not s: return None
    blk, i, near = rng.choice(s)
    a, b = rng.sample(near, 2)
    return _ptr_probe_edit(ctx, blk, i, a, b)


r_probe_ptr.enumerate = lambda ctx: [(4, _ptr_probe_edit(ctx, blk, i, near[0], near[1])) for blk, i, near in _ptr_probe_sites(ctx, 2)]


def _array_store_sites(ctx):
    t = ctx.toks
    out = []
    for n, root in _roots(ctx):
        if n.kind != 'stmt' or root.kind != 'assign' or root.kids[0].kind != 'index': continue
        ix = root.kids[0]
        b = ix.kids[0]
        if b.kind == 'atom' and b.op == 'id':
            p = _pointee(ctx, t[b.start][1])
            if p and p[1] and t[b.start][1] not in ctx.locals and not _vol_name(ctx, t[b.start][1]) and not permc.has_side_effects(ix.kids[1], t):
                out.append(('add', n, ix, p[0]))
        elif b.kind == 'paren' and b.kids[0].kind in ('comma', 'assign'):
            c = b.kids[0]
            a = c.kids[0] if c.kind == 'comma' else c
            if a.kind == 'assign' and a.kids[0].kind == 'atom' and re.match(r'pmt_(ac|ap)\d+$', t[a.kids[0].start][1]):
                out.append(('drop', n, ix, c))
    return out


def _array_operand_edit(ctx, site, form):
    t = ctx.toks
    op, n, ix, arg = site
    S = lambda z: ctx.src(z.start, z.end)
    if op == 'drop':
        c = arg
        base = S(c.kids[1])
        names = [t[(c.kids[0] if c.kind == 'comma' else c).kids[0].start][1]]
        idx = ix.kids[1]
        if idx.kind == 'assign' and idx.kids[0].kind == 'atom' and t[idx.kids[0].start][1].startswith('pmt_ak'):
            names.append(t[idx.kids[0].start][1]); idx = idx.kids[1]
        spans = [(ix.start, ix.end, permc.new(f'{base}[{S(idx)}]', t[ix.start][2]))]
        for nm in names:
            l = ctx.locals.get(nm)
            if l is not None and l.single and len(ctx.ids.get(nm, ())) == 2: spans.append((l.node.start, l.node.end, []))
        return Edit(spans, f'array operand plain again @{_line(ctx, n.start)}', meaning='probe')
    base, idx = S(ix.kids[0]), S(ix.kids[1])
    if form == 'value':
        root = ctx.stmt_expr(n)
        v = root.kids[1]
        nm = ctx.fresh('pmt_ac')
        return Edit([(ctx.lb, ctx.lb + 1, [t[ctx.lb]] + permc.new(f'unsigned int {nm};', '\n    ')),
                     (v.start, v.end, permc.new(f'({nm} = 0, {S(v)})', t[v.start][2]))],
                    f'comma on the value stored to {base}[{idx[:20]}] @{_line(ctx, n.start)}', meaning='probe')
    if form == 'comma':
        nm = ctx.fresh('pmt_ac')
        decl = f'unsigned int {nm};'
        new = f'({nm} = 0, {base})[{idx}]'
    else:
        nm, k = ctx.fresh('pmt_ap'), ctx.fresh('pmt_ak')
        kt = CANON[_promote(_ikind(ctx, ix.kids[1]))]
        decl = f'{arg} *{nm}; {kt} {k};'
        new = f'({nm} = {base})[{k} = {idx}]'
    return Edit([(ctx.lb, ctx.lb + 1, [t[ctx.lb]] + permc.new(decl, '\n    ')), (ix.start, ix.end, permc.new(new, t[ix.start][2]))],
                f'{form} in the array operand of {base}[{idx[:20]}] @{_line(ctx, n.start)}', meaning='probe')


def r_array_operand(ctx, rng, everywhere=False):
    """D37 (new kind, dead code): a side effect in the array operand of a store, which makes armcc create
    the array's address before the index (the literal pool's order): `(c = 0, buf)[idx] = val;` or
    `(p = buf)[k = idx] = val;`, with c, p and k new locals that are never read (also the reverse); or the
    side effect on the value, `buf[idx] = (c = 0, val);`, which makes the value last."""
    sites = _array_store_sites(ctx)
    if not sites: return None
    s = rng.choice(sites)
    forms = ['comma'] + (['ptr'] if s[0] == 'add' and _ikind(ctx, s[2].kids[1]) is not None else []) \
        + (['value'] if s[0] == 'add' and ctx.stmt_expr(s[1]).op == '=' and ctx.stmt_expr(s[1]).kids[1].kind != 'comma' else [])
    return _array_operand_edit(ctx, s, rng.choice(forms))


r_array_operand.enumerate = lambda ctx: [(2, _array_operand_edit(ctx, s, f)) for s in _array_store_sites(ctx)
                                         for f in (['comma', 'ptr'] if s[0] == 'add' and _ikind(ctx, s[2].kids[1]) is not None else ['comma'])]


def _nop_sites(ctx):
    """[(form, node)] for r_nop_form."""
    t = ctx.toks
    out = []
    for n in ctx.nodes:
        blockchild = n.parent is not None and n.parent.kind == 'block'
        if n.kind in ('if', 'while') and ctx.cond_expr(n) is not None: out.append(('and1', n))
        if n.kind == 'stmt' and blockchild and ctx.stmt_expr(n) is not None: out.append(('do0', n))
        if n.kind == 'if' and n.els is None and blockchild and n.then.kind in ('block', 'stmt') and not (n.then.kind == 'stmt' and ctx.is_decl(n.then)):
            out.append(('elsevoid', n))
        if n.kind == 'switch' and n.body.kind == 'block' and n.body.children and not any(c.kind == 'label' and t[c.start][1] == 'default' for c in n.body.children) \
                and (_jump_stmt(ctx, n.body.children[-1])):
            out.append(('default', n))
    return out


def _nop_edit(ctx, form, n):
    t = ctx.toks
    S = ctx.src
    if form == 'and1':
        lp, rp = n.cond
        e = ctx.cond_expr(n)
        return Edit([(lp + 1, rp, permc.new(f'{optext(ctx, e, "&&")} && 1', t[lp + 1][2]))], f'&& 1 on the test at line {_line(ctx, n.start)}', meaning='probe')
    if form == 'do0':
        return Edit([(n.start, n.end, permc.new('do { ' + S(n.start, n.end) + ' } while (0);', t[n.start][2]))], f'do {{ }} while (0) round line {_line(ctx, n.start)}', meaning='probe')
    if form == 'elsevoid':
        return Edit([(n.start, n.end, permc.new(S(n.start, n.end) + ' else (void)0;', t[n.start][2]))], f'else (void)0 at line {_line(ctx, n.start)}', meaning='probe')
    return Edit([ctx.insert_at(n.body.end - 1, 'default: break;')], f'default: break; in the switch at line {_line(ctx, n.start)}', meaning='probe')


def r_nop_form(ctx, rng, everywhere=False):
    """D38 (new kind, dead code, for -O0 builds): forms that leave only a `nop` or a branch at -O0:
    `&& 1` on a test, a statement in `do { } while (0)`, `else (void)0;` on an if without else,
    `default: break;` after a switch's last case. (At -O1 they compile to nothing.)"""
    s = _nop_sites(ctx)
    return _nop_edit(ctx, *rng.choice(s)) if s else None


r_nop_form.enumerate = lambda ctx: [(3.5, _nop_edit(ctx, f, n), f) for f, n in _nop_sites(ctx)]


# ---- build settings a source carries: a pragma round the function, the file's options line

_PRAGMAS = ('O0', 'O1', 'O2', 'O3', 'Otime', 'Ospace')


def r_func_pragma(ctx, rng, everywhere=False):
    """D30: `#pragma push` / `#pragma O0` (or O1 O2 O3 Otime Ospace) / `#pragma pop` round FUNC, or such a
    pragma changed or removed. The program is the same; its code is not."""
    t = ctx.toks
    s = ctx.start
    have = s >= 2 and t[s - 1][0] == 'pp' and re.match(r'#\s*pragma\s+O\w+$', t[s - 1][1]) and re.match(r'#\s*pragma\s+push$', t[s - 2][1])
    if have:
        after = ctx.rb + 1
        if after >= len(t) or not re.match(r'#\s*pragma\s+pop$', t[after][1]): return None
        if rng.random() < 0.4:
            return Edit([(s - 2, s, []), (after, after + 1, [])], f'pragma removed from {ctx.func}')
        new = rng.choice([p for p in _PRAGMAS if not t[s - 1][1].endswith(' ' + p)])
        return Edit([(s - 1, s, [Tok('pp', f'#pragma {new}', t[s - 1][2])])], f'#pragma {new} for {ctx.func}')
    if ctx.rb + 1 < len(t) and '\n' not in t[ctx.rb + 1][2]: return None
    p = rng.choice(('O0', 'O0', 'O0') + _PRAGMAS)
    ws = t[s][2] if t[s][2].endswith('\n') or not t[s][2] else t[s][2] + '\n'
    head = [Tok('pp', '#pragma push', ws or '\n'), Tok('pp', f'#pragma {p}', '\n'), Tok(t[s][0], t[s][1], '\n')]
    return Edit([(s, s + 1, head), (ctx.rb, ctx.rb + 1, [t[ctx.rb], Tok('pp', '#pragma pop', '\n')])], f'#pragma {p} for {ctx.func}')


_OPTS_LINE = re.compile(r'/\*\s*opts:\s*([^*]+?)\s*\*/')
_OPT_CHOICES = ('--no_data_reorder', '--bss_threshold=64', '--bss_threshold=16', '--bss_threshold=32')
OPTS_MARK = 'PMT_FILE_OPTS'         # the options line lives in a comment: this #define makes the variant a new text


def r_file_opts(ctx, rng, everywhere=False):
    """A3: the file's own options line (`/* opts: ... */`) with `--no_data_reorder` or a
    `--bss_threshold=N` added, changed or removed (the order and the section of the file's own data,
    and so how the function reaches it). permrun's workers compile a variant that carries the
    PMT_FILE_OPTS mark with the options of its own line. A file with no such line needs the project's
    options on the base (ctx.c_opts, set by the permuter). Marked 'type': a build setting."""
    t = ctx.toks
    hold = next((i for i in range(min(len(t), 400)) if _OPTS_LINE.search(t[i][2])), None)
    base = getattr(ctx, 'c_opts', None)
    if hold is None and not base: return None
    cur = _OPTS_LINE.search(t[hold][2]).group(1).split() if hold is not None else base.split()
    if any(len(o) > 200 for o in cur): return None
    opt = rng.choice(_OPT_CHOICES)
    key = opt.split('=')[0]
    had = [o for o in cur if o.split('=')[0] == key]
    new = [o for o in cur if o.split('=')[0] != key]
    if not had or (had[0] != opt and '=' in opt): new.append(opt)
    line = '/* opts: ' + ' '.join(new) + ' */'
    if hold is None: hold = 0
    ws = t[hold][2]
    ws = _OPTS_LINE.sub(lambda m: line, ws, count=1) if _OPTS_LINE.search(ws) else line + '\n' + ws
    spans = [(hold, hold + 1, [Tok(t[hold][0], t[hold][1], ws)])]
    mark = next((i for i in range(ctx.start) if t[i][0] == 'pp' and OPTS_MARK in t[i][1]), None)
    tag = f'#define {OPTS_MARK} "{" ".join(sorted(o for o in new if o.split("=")[0] in ("--no_data_reorder", "--bss_threshold")))}"'
    s = ctx.start
    nl = lambda w: w if w.endswith('\n') or not w else w + '\n'
    if mark is not None:
        if mark == hold: spans = [(mark, mark + 1, [Tok('pp', tag, ws)])]
        else: spans.append((mark, mark + 1, [Tok('pp', tag, t[mark][2])]))
    elif s == hold: spans = [(s, s + 1, [Tok('pp', tag, nl(ws)), Tok(t[s][0], t[s][1], '\n')])]
    else: spans.append((s, s + 1, [Tok('pp', tag, nl(t[s][2])), Tok(t[s][0], t[s][1], '\n')]))
    return Edit(spans, f'file options: {" ".join(o for o in new if o.startswith(("--no_data", "--bss"))) or "defaults"}', meaning='type')



# ---- idioms of the older tools (armidioms.py): a dead zero before a store, a literal through a local

def _dead_zero_sites(ctx):
    """Plain stores `L = E;` in a block before which a dead `L = 0;` can stand: L a simple non-volatile
    lvalue, E without side effects and not reading L's object (so nothing sees the zero)."""
    c = getattr(ctx, '_dz', None)
    if c is not None: return c
    c = ctx._dz = []
    t = ctx.toks
    for blk in permc.blocks(ctx.root):
        for n in blk.children:
            e = ctx.stmt_expr(n) if n.kind == 'stmt' else None
            if e is None or e.kind != 'assign' or e.op != '=' or not permc.is_lvalue_simple(e.kids[0], t): continue
            if permc.const_value(e.kids[1], t) == 0 or permc.has_side_effects(e.kids[1], t) or _ikind(ctx, e.kids[0]) is None: continue
            W, T = _fx(ctx, e.kids[0].start, e.kids[0].end + 1), _fx(ctx, e.kids[1].start, e.kids[1].end)
            if W.vol or W.call or T.vol or not _store_indep(W, T): continue
            c.append((n, e))
    return c


def _dead_zero_edit(ctx, n, e):
    L = ctx.src(e.kids[0].start, e.kids[0].end)
    return Edit([ctx.insert_at(n.start, f'{L} = 0;')], f'dead store {L} = 0 before line {_line(ctx, n.start)}')


def r_dead_zero(ctx, rng, everywhere=False):
    """D4 (extended): a dead `L = 0;` just before a store `L = E;` (armcc deletes it, but the function's
    shared zero is then made at that point). Any integer target, not only one that also has a constant
    store elsewhere; E has no side effects and cannot read what the zero wrote."""
    s = _dead_zero_sites(ctx)
    return _dead_zero_edit(ctx, *rng.choice(s)) if s else None


r_dead_zero.enumerate = lambda ctx: [(2, _dead_zero_edit(ctx, n, e)) for n, e in _dead_zero_sites(ctx)]


def _literal_sites(ctx):
    """[(statement, root, literal atom, value)]: integer literals of expression statements where a
    variable of integer type can stand (an operand of arithmetic with an integer, a stored value, a
    call argument for an integer parameter, an index), not a shift count."""
    c = getattr(ctx, '_ls', None)
    if c is not None: return c
    c = ctx._ls = []
    t = ctx.toks
    for n, root in _roots(ctx):
        if n.kind != 'stmt' or n.parent is None or n.parent.kind != 'block' or ctx.stmt_expr(n) is None: continue
        for p in permc.ewalk(root):
            for i, x in enumerate(p.kids):
                if x.kind != 'atom' or x.op != 'num': continue
                if p.kind == 'bin': ok = p.op not in _SEQ_OPS and not (p.op in ('<<', '>>') and i == 1) and _ikind(ctx, p.kids[1 - i]) is not None
                elif p.kind == 'assign': ok = i == 1 and p.op in ('=', '+=', '-=', '|=', '&=', '^=', '*=') and _ikind(ctx, p.kids[0]) is not None
                elif p.kind == 'index': ok = i == 1
                elif p.kind == 'call':
                    tys = _arg_types(ctx, p, i - 1, x) if i >= 1 and p.kids[0].kind == 'atom' else []
                    ok = bool(tys) and _tkind(ctx, tys[0]) is not None
                else: ok = False
                s = t[x.start][1]
                kv = permc.const_value(x, t)
                if ok and kv is not None and s[-1] not in 'lLuU' and 0 <= kv < (1 << 31) and _seq_path(ctx, root, x) is not None:
                    c.append((n, root, x, kv))
    return c


def _literal_local_edit(ctx, site, form, ty=None, reuse=None):
    t = ctx.toks
    n, root, x, kv = site
    lit = t[x.start][1]
    if form == 'reuse':
        return Edit([ctx.insert_at(n.start, f'{reuse} = {lit};'), (x.start, x.end, permc.new(reuse, t[x.start][2]))],
                    f'literal {lit} through the local {reuse} @{_line(ctx, n.start)}')
    nm = ctx.fresh('pmt_k')
    decl = (ctx.lb, ctx.lb + 1, [t[ctx.lb]] + permc.new(f'{ty} {nm};', '\n    '))
    at = _decl_point(ctx, ctx.root) if form == 'top' else n.start
    if at == n.start or form == 'before':
        return Edit([decl, ctx.insert_at(n.start, f'{nm} = {lit};'), (x.start, x.end, permc.new(nm, t[x.start][2]))],
                    f'literal {lit} through a {ty} local set before line {_line(ctx, n.start)}')
    return Edit([decl, ctx.insert_at(at, f'{nm} = {lit};'), (x.start, x.end, permc.new(nm, t[x.start][2]))],
                f'literal {lit} of line {_line(ctx, n.start)} through a {ty} local set at the top')


def r_literal_local(ctx, rng, everywhere=False):
    """C2 (extended): an integer literal of a statement held in a function-scope local: a new narrow
    local assigned just before the statement or at the top of the body, or an existing local of the
    function that is dead there, assigned just before the statement (`value = 0x80;` then
    `write(value, 0);`). The local's type holds the value and promotes to the literal's int; an
    existing local must hold nothing that is needed while or after the statement runs."""
    sites = _literal_sites(ctx)
    if not sites: return None
    for _ in range(6):
        site = rng.choice(sites)
        n, root, x, kv = site
        r = rng.random()
        if r < 0.4:
            blk = n.parent
            i = blk.children.index(n)
            region = (n.start, n.end, blk, i, i, True)
            ys = [y for y in ctx.locals if _plain_local(ctx, y) and _fits(_name_kind(ctx, y), kv) and _name_kind(ctx, y) != (32, False)
                  and _encloses(ctx.locals[y].block, blk) and ctx.locals[y].node.end <= n.start and _dead_over(ctx, y, region)]
            if ys: return _literal_local_edit(ctx, site, 'reuse', reuse=rng.choice(sorted(ys)))
            continue
        tys = [ty for ty, k in (('unsigned char', (8, False)), ('unsigned short', (16, False)), ('int', (32, True))) if _fits(k, kv)]
        return _literal_local_edit(ctx, site, 'before' if r < 0.8 else 'top', ty=rng.choice(tys))
    return None


r_literal_local.enumerate = lambda ctx: [(2.5, _literal_local_edit(ctx, s, 'before', ty='unsigned char' if s[3] < 256 else 'unsigned short' if s[3] < 65536 else 'int'))
                                         for s in _literal_sites(ctx)]


# ---- one edit at every look-alike site (the vendor's copy-paste idioms)

class _FocusRng(random.Random):
    """A random generator whose choice() prefers the candidates that lie in a token range: a rule that
    picks its site with rng.choice then makes its edit there when it has one there."""

    def aim(self, a, b):
        self._a, self._b = a, b
        return self

    def _in(self, x, depth=0):
        s = getattr(x, 'start', None)
        if isinstance(s, int): return self._a <= s < self._b
        if depth < 2 and isinstance(x, (tuple, list)): return any(self._in(y, depth + 1) for y in x)
        return False

    def choice(self, seq):
        near = [x for x in seq if self._in(x)]
        return super().choice(near if near else seq)


_NOT_UNIFORM = {'uniform', 'extra_param', 'normalise', 'size_and', 'remove_dead', 'inline_group', 'file_opts', 'func_pragma', 'unprototyped', 'sized_extern',
                'global_type', 'callee_linkage', 'signature_type', 'field_type', 'packed_struct', 'struct_array', 'macro_body', 'macro_fold',
                'file_order', 'probe_remove', 'decl_order', 'split_decl', 'retype', 'scope', 'pad_local'}


def _shape(ctx, a, b):
    """Token shape of a..b: keywords, operators, type words and called names as they are; every other
    identifier by where it first appears in the range; constants as N; one-token array indices as #."""
    t = ctx.toks
    out, first = [], {}
    for i in range(a, b):
        k, w, _ = t[i]
        if k == 'id':
            if w in permc.KEYWORDS or ctx.types.is_type_word(w) and w not in ctx.locals and w not in ctx.params: out.append(w)
            elif t[i + 1][1] == '(' and w not in ctx.locals and w not in ctx.params: out.append(w)
            elif t[i - 1][1] == '[' and t[i + 1][1] == ']': out.append('#')
            else: out.append(f'I{first.setdefault(w, len(first))}')
        elif k in ('num', 'chr'):
            out.append('#' if t[i - 1][1] == '[' and t[i + 1][1] == ']' else 'N')
        elif k == 'str': out.append('S')
        else: out.append(w)
    return tuple(out)


def _look_alikes(ctx):
    """[[(start, end)]]: groups of three or more statements, or runs of two statements, of FUNC with one
    token shape (at least four tokens), each group in source order."""
    c = getattr(ctx, '_la', None)
    if c is not None: return c
    by = {}
    for blk in permc.blocks(ctx.root):
        kids = [k for k in blk.children]
        for i, n in enumerate(kids):
            if n.kind in ('label', 'pp', 'empty') or (n.kind == 'stmt' and ctx.is_decl(n)): continue
            if n.end - n.start >= 4: by.setdefault((1, _shape(ctx, n.start, n.end)), []).append((n.start, n.end))
            if i + 1 < len(kids) and kids[i + 1].kind == 'stmt' and n.kind == 'stmt' and not ctx.is_decl(kids[i + 1]):
                by.setdefault((2, _shape(ctx, n.start, kids[i + 1].end)), []).append((n.start, kids[i + 1].end))
    out = []
    for (k, sh), ms in sorted(by.items(), key=lambda kv: kv[1][0]):
        if k == 2:                                  # runs of two must not overlap one another
            keep, end = [], -1
            for a, b in ms:
                if a >= end: keep.append((a, b)); end = b
            ms = keep
        if len(ms) >= 3: out.append(ms)
    c = ctx._la = out
    return c


def _rel_sig(ctx, ed, a, b):
    """An edit that lies wholly in tokens a..b, written relative to that range: its spans from a, and its
    new tokens with each token of the range named by its place there (so the same edit at a look-alike
    range has the same signature). None when a span lies outside the range."""
    t = ctx.toks
    pos = {}
    for i in range(a, b):
        if t[i][0] in ('id', 'num', 'chr', 'str'): pos.setdefault(t[i][1], i - a)
    sig = []
    for x, y, new in sorted(ed.spans, key=lambda s: (s[0], s[1])):
        if x < a or y > b + 1: return None
        tail = ()
        if y == b + 1:                              # a statement put after the range: the next token written again
            if not new or new[-1][1] != t[b][1]: return None
            new, tail = new[:-1], ('$next',)
        sig.append((x - a, y - a, tuple(f'@{pos[w[1]]}' if w[1] in pos else re.sub(r'^(pmt_[a-z]+)\d+(_\d+)?$', r'\1', w[1]) for w in new) + tail))
    return tuple(sig)


def _edit_at(ctx, rule, a, b, want=None, seeds=4, salt=0):
    """An edit of the rule that lies in tokens a..b (with want: the one with that relative signature),
    found by aiming the rule's own site choice there; (edit, signature) or None."""
    for k in range(seeds):
        rng = _FocusRng(f'{rule.id}:{a}:{salt}:{k}').aim(a, b)
        try:
            ed = rule.fn(ctx, rng, everywhere=False)
        except Exception:
            ed = None
        if ed is None or not ed.spans: continue
        sig = _rel_sig(ctx, ed, a, b)
        if sig is not None and (want is None or sig == want): return ed, sig
    return None


def _uniform_edit(ctx, rule, group, first, ed0, sig, pick=None, seeds=3):
    """The rule's edit ed0 (made at group[first]) made at the other members of the group too, each by the
    rule itself (so under its own preconditions there); a member where the rule does not make it is
    left as it is. pick chooses which members to try (None: all)."""
    idx = [k for k in range(len(group)) if k != first]
    if pick is not None: idx = [k for k in idx if k in pick]
    spans, meaning, got = list(ed0.spans), ed0.meaning, [first]
    for k in idx:
        r = _edit_at(ctx, rule, group[k][0], group[k][1], sig, seeds, salt=1)
        if r is None or any(x < q and p < y for x, y, _ in r[0].spans for p, q, _ in spans): continue
        spans += r[0].spans; got.append(k)
        if r[0].meaning != 'same' and meaning == 'same': meaning = r[0].meaning
        if r[0].meaning == 'type': meaning = 'type'
    if len(got) < 2: return None
    asked = len(idx) + 1
    note = f'at {len(got)} of {len(group)} look-alike sites' + (f' ({asked - len(got)} refused)' if len(got) < asked else '') \
        + ('' if pick is None else ', a part of the group')
    return Edit(spans, f'uniform {rule.id}: {ed0.desc[:70]} {note} from line {_line(ctx, group[min(got)][0])}', meaning=meaning)


def r_uniform(ctx, rng, everywhere=False):
    """B10 (new kind): one rule's edit made at every look-alike site. Statements (or runs of two) with one
    token shape form a group when there are three or more; an edit a rule makes inside one member is
    made at the same tokens of the others, each time by the rule itself aimed at that member, so its
    preconditions hold at each (a member where it refuses is skipped and counted in the description).
    All members, or a contiguous range of them, or a random half (the idiom may change half-way down a
    function)."""
    groups = _look_alikes(ctx)
    if not groups: return None
    rules = getattr(ctx, '_ur', None)
    if rules is None: rules = ctx._ur = [r for r in RULES if r.id not in _NOT_UNIFORM]
    ws = [r.weight for r in rules]
    for _ in range(5):
        group = rng.choice(groups)
        first = rng.randrange(len(group))
        rule = rng.choices(rules, ws)[0]
        r = _edit_at(ctx, rule, group[first][0], group[first][1], None, 2, salt=rng.randrange(1 << 20))
        if r is None: continue
        ed0, sig = r
        mode = rng.random()
        pick = None
        if mode > 0.6 and len(group) >= 4:
            if mode < 0.8:
                lo = rng.randrange(len(group) - 1); hi = rng.randrange(lo + 1, len(group))
                pick = set(range(lo, hi + 1)) | {first}
            else: pick = set(rng.sample(range(len(group)), max(2, len(group) // 2))) | {first}
        if len(pick if pick is not None else group) > 9:    # a long group: a contiguous eight more members a call
            base = sorted(pick if pick is not None else range(len(group)))
            lo = rng.randrange(len(base) - 7)
            pick = set(base[lo:lo + 8]) | {first}
        ed = _uniform_edit(ctx, rule, group, first, ed0, sig, pick, seeds=1)
        if ed is not None: return ed
    return None


def enum_uniform(ctx):
    """For each group and each rule: every edit the rule makes inside the group's first member (found by a
    few aimed draws), made at all members. Tier 1 when every member took it, 2 otherwise."""
    out = []
    rules = [r for r in RULES if r.id not in _NOT_UNIFORM]
    for group in _look_alikes(ctx)[:30]:
        a, b = group[0]
        for rule in rules:
            seen = set()
            for salt in range(3):
                r = _edit_at(ctx, rule, a, b, None, 1, salt=100 + salt)
                if r is None or r[1] in seen: continue
                seen.add(r[1])
                ed = _uniform_edit(ctx, rule, group[:24], 0, r[0], r[1], None, seeds=2)
                if ed is not None: out.append((1 if f'at {len(group[:24])} of' in ed.desc else 2, ed, rule.id))
    return out


r_uniform.enumerate = enum_uniform



# ---- levers of the project notes that had no rule

def _copy_facts(ctx):
    """[(defining statement, variable, value expression, later sibling statements in which the fact
    `variable == (its type)value` still holds)]: after `A = E;` (A a non-volatile integer variable, E
    without side effects and not reading A), up to the first sibling that can change A or what E reads,
    or a label."""
    c = getattr(ctx, '_cf', None)
    if c is not None: return c
    c = ctx._cf = []
    t = ctx.toks
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for i, n in enumerate(kids):
            e = ctx.stmt_expr(n) if n.kind == 'stmt' else None
            if e is None or e.kind != 'assign' or e.op != '=' or e.kids[0].kind != 'atom' or e.kids[0].op != 'id': continue
            a = t[e.kids[0].start][1]
            r = e.kids[1]
            if not _trackable(ctx, a) or permc.has_side_effects(r, t) or a in permc.idents(r, t) or permc.const_value(r, t) is not None: continue
            fr = _fx(ctx, r.start, r.end)
            if fr.vol or fr.call or _ikind(ctx, r) is None: continue
            W = _fx(ctx, e.kids[0].start, e.kids[0].end + 1)
            if not _store_indep(W, fr): continue                    # (the store to A itself may not change E)
            live = []
            for m in kids[i + 1:]:
                if m.kind in ('label', 'pp') or (m.kind == 'stmt' and ctx.is_decl(m)): break
                live.append(m)
                f = _nfx(ctx, m)
                if a in f.writes or not _store_indep(f, fr) or (a not in _private(ctx) and (f.call or f.memw)) \
                        or _escapes(ctx, m, True, True, False): break
            if live: c.append((n, a, r, live))
    return c


def _copy_sites(ctx):
    c = getattr(ctx, '_cps', None)
    if c is not None: return c
    c = ctx._cps = []
    t = ctx.toks
    for n, a, r, live in _copy_facts(ctx):
        ka = _name_kind(ctx, a)
        exact = _promote(_ikind(ctx, r)) == ka or (_unparen(r).kind in ('atom', 'index', 'member') and _ikind(ctx, r) == ka)
        key = [x[1] for x in t[_unparen(r).start:_unparen(r).end]]
        fr = _fx(ctx, r.start, r.end)
        for k, m in enumerate(live):
            root = _root_expr(ctx, m)
            if root is None: continue
            last = k == len(live) - 1                 # the statement that ended the fact: only what it reads before it stores
            for x in permc.ewalk(root):
                if x.kind == 'atom' and x.op == 'id' and t[x.start][1] == a and _is_read_atom(ctx, x) and _reads_safely(ctx, root, x, a) \
                        and _hoist_safe(ctx, root, x) and _expr_still(ctx, root, x, fr):
                    c.append(('expr', m, x, a, r, exact))
                elif exact and x is not root and [y[1] for y in t[x.start:x.end]] == key and x.kind == _unparen(r).kind \
                        and not (t[x.end][1] in permc.ASSIGN or t[x.end][1] in ('++', '--') or t[x.start - 1][1] in ('&', '++', '--')) \
                        and _reads_safely(ctx, root, x, a) and _expr_still(ctx, root, x, fr):
                    c.append(('var', m, x, a, r, exact))
    return c


def _expr_still(ctx, root, x, fr):
    """Nothing in root that runs before x (anything but the operators x is an operand of) changes what the
    expression with effects fr reads."""
    for s in permc.ewalk(root):
        if s.kind in ('assign', 'call', 'postfix') or (s.kind == 'unary' and s.op in ('++', '--')):
            if s.start <= x.start and x.end <= s.end: continue
            if not _store_indep(_fx(ctx, s.start, s.end), fr): return False
    return True


def _copy_edit(ctx, site):
    t = ctx.toks
    kind, m, x, a, r, exact = site
    if kind == 'var':
        return Edit([(x.start, x.end, permc.new(a, t[x.start][2]))], f'{ctx.src(x.start, x.end)[:30]} read from {a}, which holds it @{_line(ctx, x.start)}')
    u = _unparen(r)
    s = ctx.src(u.start, u.end)
    if exact: new = s if u.kind in ('atom', 'index', 'member') else f'({s})'
    else:
        l = ctx.locals.get(a)
        ty = l.type if l is not None else ctx.params.get(a) or resolve_type(ctx, ctx.types.globals[a][0])
        new = f'({ty}){s}' if u.kind in ('atom', 'index', 'member') else f'({ty})({s})'
        if t[x.end][1] in ('[', '(', '.', '->'): new = f'({new})'
    return Edit([(x.start, x.end, permc.new(new, t[x.start][2]))], f'{a} written out as {new[:30]} @{_line(ctx, x.start)}')


def r_copy_source(ctx, rng, everywhere=False):
    """B1 (extended): after `a = E;`, one later read of a written as E again (`end = s + n; if (s + n > K)`;
    `g1 = v; g2 = v;` for `g2 = g1;`), with a cast to a's type when E's own type is another; and the
    reverse, a later copy of E read from a (only when E has a's type exactly). The assignment stays.
    Between the two places nothing stores a or anything E reads (see _copy_facts); a and E are
    non-volatile integers of at most 32 bits."""
    s = _copy_sites(ctx)
    return _copy_edit(ctx, rng.choice(s)) if s else None


r_copy_source.enumerate = lambda ctx: [(2, _copy_edit(ctx, s), s[3]) for s in _copy_sites(ctx)]


def _guard_sites(ctx):
    c = getattr(ctx, '_gs', None)
    if c is not None: return c
    c = ctx._gs = []
    kn = _known(ctx)
    t = ctx.toks
    for blk in permc.blocks(ctx.root):
        for n in blk.children:
            M = kn.get(n.start)
            if not M or n.kind != 'stmt' or ctx.stmt_expr(n) is None: continue
            for v, k in sorted(M.items()):
                if ctx.visible(v, n.start): c.append((n, v, k))
    return c


def _guard_edit(ctx, n, v, k, ne=False):
    t = ctx.toks
    cond = f'{v} != {_lit(1 if k == 0 else 0)}' if ne else f'{v} == {_lit(k)}'
    return Edit([(n.start, n.end, permc.new(f'if ({cond}) ' + ctx.src(n.start, n.end), t[n.start][2]))],
                f'guard if ({cond}) on line {_line(ctx, n.start)} (known true)')


def r_known_guard(ctx, rng, everywhere=False):
    """D4 (extended): a statement put under a test that is known to be true where it stands
    (`if (g != 0) g2 = v;` just after g was given a non-zero constant, `if (x == K) ...` in the arm that
    tested it): armcc folds the test, but only after the value it reads has been counted. The fact
    comes from _known (as for known_const). Also removes such a guard."""
    t = ctx.toks
    kn = _known(ctx)
    olds = []
    for n in ctx.nodes:
        M = kn.get(n.start)
        if n.kind != 'if' or n.els is not None or not M or n.parent is None or n.parent.kind != 'block' or n.then.kind != 'stmt': continue
        ce = ctx.cond_expr(n)
        for eq in (True, False):
            f = _eq_fact(ctx, ce, eq) if ce is not None else None
            if f and f[0] in M and (M[f[0]] == f[1]) == eq: olds.append(n)
    s = _guard_sites(ctx)
    if olds and (not s or rng.random() < 0.3):
        n = rng.choice(olds)
        return Edit([(n.start, n.then.start, [])], f'guard known true removed @{_line(ctx, n.start)}')
    if not s: return None
    n, v, k = rng.choice(s)
    return _guard_edit(ctx, n, v, k, rng.random() < 0.4)


r_known_guard.enumerate = lambda ctx: [(3, _guard_edit(ctx, n, v, k), v) for n, v, k in _guard_sites(ctx)]


def r_switch_guard(ctx, rng, everywhere=False):
    """D8 (extended): `switch (x) { ... }` against `if (x >= LO && x <= HI) switch (x) { ... }` where LO and
    HI are its smallest and largest case and it has no default (so nothing runs outside the range
    either way), both ways. x is a non-volatile integer variable."""
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        sw = n
        guard = None
        if n.kind == 'if' and n.els is None and n.then.kind == 'switch': sw, guard = n.then, n
        elif n.kind != 'switch' or (n.parent is not None and n.parent.kind == 'if' and n.parent.els is None and n.parent.then is n): continue
        if sw.kind != 'switch' or sw.body.kind != 'block' or n.parent is None or n.parent.kind != 'block': continue
        x = _unparen(ctx.cond_expr(sw)) if ctx.cond_expr(sw) is not None else None
        if x is None or x.kind != 'atom' or x.op != 'id' or not _trackable(ctx, t[x.start][1]): continue
        vals, ok = [], True
        for c in sw.body.children:
            if c.kind != 'label': continue
            if t[c.start][1] != 'case': ok = False; break
            e = ctx.expr(c.start + 1, c.end - 1)
            v = permc.const_value(e, t) if e is not None else None
            if v is None or v < 0: ok = False; break
            vals.append(v)
        if not ok or len(vals) < 2: continue
        v = t[x.start][1]
        lo, hi = min(vals), max(vals)
        if guard is None: cands.append(('add', n, v, lo, hi))
        else:
            ce = _unparen(ctx.cond_expr(guard)) if ctx.cond_expr(guard) is not None else None
            want = {f'{v} >= {lo} && {v} <= {hi}', f'{v} <= {hi}' if lo == 0 else None}
            got = None if ce is None else ' '.join(str(permc.const_value(permc.E('atom', i, i + 1, 'num'), t)) if t[i][0] == 'num' else t[i][1]
                                                    for i in range(ce.start, ce.end))
            if got in want: cands.append(('drop', guard, v, lo, hi))
    if not cands: return None
    op, n, v, lo, hi = rng.choice(cands)
    if op == 'drop': return Edit([(n.start, n.then.start, [])], f'range guard off the switch at line {_line(ctx, n.start)}')
    cond = f'{v} <= {_lit(hi)}' if lo == 0 and _name_kind(ctx, v)[1] is False else f'{v} >= {_lit(lo)} && {v} <= {_lit(hi)}'
    return Edit([(n.start, n.start + 1, permc.new(f'if ({cond}) switch', t[n.start][2]))], f'range guard {cond} on the switch at line {_line(ctx, n.start)}')


def _is_bool(e):
    e = _unparen(e)
    return (e.kind == 'bin' and (e.op in NEG or e.op in _SEQ_OPS)) or (e.kind == 'unary' and e.op == '!')


def r_bool_store(ctx, rng, everywhere=False):
    """D18 (extended): `if (c) x = 1; else x = 0;` against `x = (c);` (c a comparison, && || or !, whose
    value is 0 or 1), both ways; with 0 and 1 the other way round the test is negated."""
    t = ctx.toks
    cands = []
    for n in ctx.nodes:
        if n.kind == 'if' and n.els is not None and ctx.cond_expr(n) is not None and _is_bool(ctx.cond_expr(n)):
            a, b = _single_assign(ctx, n.then), _single_assign(ctx, n.els)
            if a and b and a[0] == b[0] and {a[1].strip(), b[1].strip()} == {'0', '1'}: cands.append(('expr', n, a))
        e = ctx.stmt_expr(n) if n.kind == 'stmt' else None
        if e is not None and e.kind == 'assign' and e.op == '=' and _is_bool(e.kids[1]) and permc.is_lvalue_simple(e.kids[0], t) \
                and n.parent is not None and n.parent.kind == 'block' and not permc.has_side_effects(e.kids[1], t):
            cands.append(('if', n, e))
    if not cands: return None
    op, n, a = rng.choice(cands)
    if op == 'expr':
        ce = ctx.cond_expr(n)
        c = ctx.src(_unparen(ce).start, _unparen(ce).end) if a[1].strip() == '1' else _neg(ctx, ce)
        return Edit([(n.start, n.end, permc.new(f'{a[0]} = ({c});', t[n.start][2]))], f'flag {a[0]} stored as the test @{_line(ctx, n.start)}')
    L, c = ctx.src(a.kids[0].start, a.kids[0].end), _unparen(a.kids[1])
    return Edit([(n.start, n.end, permc.new(f'if ({ctx.src(c.start, c.end)}) {L} = 1; else {L} = 0;', t[n.start][2]))],
                f'flag {L} stored in each arm @{_line(ctx, n.start)}')


def r_accum_start(ctx, rng, everywhere=False):
    """D27 (extended): how an accumulation starts: `id = 0; id |= x << 8; id |= y;` against
    `id = x << 8; id |= y;` (also += and ^=), both ways. Integers of at most 32 bits; the first term has
    no side effects and does not read the target."""
    t = ctx.toks
    cands = []
    for blk in permc.blocks(ctx.root):
        kids = blk.children
        for k, n in enumerate(kids):
            e = ctx.stmt_expr(n) if n.kind == 'stmt' else None
            if e is None or e.kind != 'assign' or not permc.is_lvalue_simple(e.kids[0], t) or _ikind(ctx, e.kids[0]) is None: continue
            if e.op == '=' and permc.const_value(e.kids[1], t) == 0 and k + 1 < len(kids) and kids[k + 1].kind == 'stmt':
                f = ctx.stmt_expr(kids[k + 1])
                if f is not None and f.kind == 'assign' and f.op in ('|=', '+=', '^=') and _eq(ctx, f.kids[0], e.kids[0]) \
                        and _terms_ok(ctx, n, e.kids[0], [f.kids[1], f.kids[1]], 1):
                    cands.append(('drop', n, kids[k + 1], f))
            if e.op == '=' and permc.const_value(e.kids[1], t) is None and k + 1 < len(kids) and kids[k + 1].kind == 'stmt' \
                    and _terms_ok(ctx, n, e.kids[0], [e.kids[1], e.kids[1]], 1):
                f = ctx.stmt_expr(kids[k + 1])
                if f is not None and f.kind == 'assign' and f.op in ('|=', '+=', '^=') and _eq(ctx, f.kids[0], e.kids[0]):
                    cands.append(('add', n, f.op, e))
    if not cands: return None
    op, n, m, e = rng.choice(cands)
    if op == 'drop':
        L = ctx.src(e.kids[0].start, e.kids[0].end)
        return Edit([(n.start, m.end, permc.new(f'{L} = {ctx.src(e.kids[1].start, e.kids[1].end)};', t[n.start][2]))],
                    f'{L} starts from its first term @{_line(ctx, n.start)}')
    L = ctx.src(e.kids[0].start, e.kids[0].end)
    text = f'{L} = 0;\n{ctx.indent(n.start)}{L} {m} {ctx.src(e.kids[1].start, e.kids[1].end)};'
    return Edit([(n.start, n.end, permc.new(text, t[n.start][2]))], f'{L} starts from 0 @{_line(ctx, n.start)}')


def r_regroup(ctx, rng, everywhere=False):
    """D20 (extended): the grouping and term order of a sum (or a chain of | & ^ *) of three or more
    side-effect-free integer terms: `(a + b) + c` against `a + (b + c)`, or two terms exchanged. Equal
    modulo 2^32, whatever the grouping."""
    t = ctx.toks
    cands = []
    for n, root in _roots(ctx):
        for x in permc.ewalk(root):
            if x.kind != 'bin' or x.op not in ('+', '|', '&', '^', '*'): continue
            p_end = t[x.end][1] if x.end < len(t) else ''
            terms = [z for _, z in _op_chain(x, (x.op,))]
            if len(terms) < 3 or t[x.start - 1][1] == x.op or p_end == x.op: continue       # the whole chain only
            if any(_ikind(ctx, z) is None or permc.has_side_effects(z, t) or _fx(ctx, z.start, z.end).vol for z in terms): continue
            cands.append((x, terms))
    if not cands: return None
    x, terms = rng.choice(cands)
    S = [_opnd(ctx, z, x.op, True) for z in terms]
    if rng.random() < 0.5:
        i, j = sorted(rng.sample(range(len(S)), 2))
        S[i], S[j] = S[j], S[i]
        new = f' {x.op} '.join(S)
    else:
        k = rng.randrange(1, len(S) - 1)
        new = f' {x.op} '.join(S[:k]) + f' {x.op} (' + f' {x.op} '.join(S[k:]) + ')'
    return Edit([(x.start, x.end, permc.new(wrap(ctx, x, new), t[x.start][2]))], f'regroup {ctx.src(x.start, x.end)[:30]} -> {new[:40]} @{_line(ctx, x.start)}')


def _probe_form_edits(ctx, i, e, rng=None):
    """Dead forms at insertion point i that read expression e as a stored comparison does."""
    lab = ctx.fresh('pmt_L')
    out = [('while', Edit([ctx.insert_at(i, f'while ({e}) break;')], f'dead while ({e}) break; @{_line(ctx, i)}', meaning='probe')),
           ('goto', Edit([ctx.insert_at(i, f'if ({e}) goto {lab}; {lab}: ;')], f'dead if ({e}) goto next; @{_line(ctx, i)}', meaning='probe')),
           ('switch0', Edit([ctx.insert_at(i, 'switch (0) { default: break; }')], f'empty switch (0) @{_line(ctx, i)}', meaning='probe'))]
    return out


def r_probe_forms(ctx, rng, everywhere=False):
    """D1 (extended, dead code): other forms that act as a stored comparison does: `while (E) break;`,
    `if (E) goto L; L: ;`, an empty `switch (0) { default: break; }`, the comparison stored in one of the function's own locals that is dead at
    that point (`y = (E);`), an unread comparison ahead of an if's test (`if ((z = (E)), C)` with a new
    local z), and a call argument written `(0, arg)`."""
    t = ctx.toks
    r = rng.random()
    if r < 0.2:
        ifs = [n for n in ctx.nodes if n.kind == 'if' and ctx.cond_expr(n) is not None and ctx.cond_expr(n).kind != 'comma']
        if ifs:
            n = rng.choice(ifs)
            ops = _operands_near(ctx, n.start, rng)
            if ops:
                x = rng.choice(ops)
                z = ctx.fresh('pmt_z')
                lp, rp = n.cond
                return Edit([(ctx.lb, ctx.lb + 1, [t[ctx.lb]] + permc.new(f'unsigned int {z};', '\n    ')),
                             (lp + 1, rp, permc.new(f'({z} = ({x} == {_const(ctx, rng, at=n.start)})), {ctx.src(lp + 1, rp)}', t[lp + 1][2]))],
                            f'unread comparison of {x} ahead of the test at line {_line(ctx, n.start)}', meaning='probe')
    if r < 0.3:
        args = [a for n, root in _roots(ctx) for c in permc.ewalk(root) if c.kind == 'call' for a in c.kids[1:]
                if a.kind in ('atom', 'index', 'member') and not (a.kind == 'atom' and a.op != 'id')]
        if args:
            a = rng.choice(args)
            return Edit([(a.start, a.end, permc.new(f'(0, {ctx.src(a.start, a.end)})', t[a.start][2]))],
                        f'argument {ctx.src(a.start, a.end)[:24]} behind a comma @{_line(ctx, a.start)}', meaning='probe')
    pts = _probe_points(ctx)
    if not pts: return None
    i, blk, k = rng.choice(pts)
    ops = _operands_near(ctx, i, rng)
    if not ops: return None
    x = rng.choice(ops)
    e = f'{x} == {_const(ctx, rng, at=i)}'
    if r < 0.6 and k < len(blk.children):
        n = blk.children[k]
        region = (n.start, n.start, blk, k, k - 1, True)
        ys = [y for y in ctx.locals if _plain_local(ctx, y) and _name_kind(ctx, y) is not None and y != x and _encloses(ctx.locals[y].block, blk)
              and ctx.locals[y].node.end <= i and _dead_at(ctx, y, blk, k)]
        if ys:
            y = rng.choice(sorted(ys))
            return Edit([ctx.insert_at(i, f'{y} = ({e});')], f'dead {y} = ({e}); @{_line(ctx, i)}', meaning='probe')
    return rng.choice(_probe_form_edits(ctx, i, e))[1]


def _dead_at(ctx, y, blk, k):
    """Local y holds nothing needed at the boundary before child k of blk: the next sibling that mentions
    it plainly redefines it (and nothing jumps away before that), or it is never mentioned from there
    on and no loop or goto label can lead back to an earlier use."""
    kids = blk.children
    i = kids[k].start if k < len(kids) else blk.end - 1
    occ, _ = _occ(ctx, y)
    for q in range(k, len(kids)):
        if any(kids[q].start <= o < kids[q].end for o in occ): return _plain_def(ctx, kids[q], y)
        if kids[q].kind in ('label', 'pp') or _escapes(ctx, kids[q], ret=False): return False
    if any(o >= i for o in occ): return False
    return not any(ls <= i < le and any(ls <= o < le for o in occ) for ls, le in _loops(ctx)) and not any(p < i for p in _goto_labels(ctx))


def enum_probe_forms(ctx):
    out = []
    for i, blk, k in _probe_points(ctx)[::2]:
        for e in _tests_near(ctx, i, 1):
            for form, ed in _probe_form_edits(ctx, i, e): out.append((3.5, ed, form))
    return out


r_probe_forms.enumerate = enum_probe_forms


def r_extra_param(ctx, rng, everywhere=False):
    """A11 (extended): an unused parameter added at the end of FUNC's parameters (in its definition and in
    each declaration of it in the file), or its unused last parameter removed: the caller's argument
    registers and FUNC's own change. Only when the file has no call of FUNC and no header mentions it.
    Marked 'type' (the callers in other files pass what they passed)."""
    t = ctx.toks
    if ctx.func in getattr(ctx.types, 'header_ids', ()): return None
    ds = _fn_decls(ctx, {ctx.func}).get(ctx.func, [])
    if not ds or None in ds: return None
    n_all = sum(1 for i in range(len(t) - 1) if t[i][1] == ctx.func and t[i + 1][1] == '(')
    if n_all != len(ds): return None                           # a call of it in the file
    spans = []
    lps = [(i + 1, ctx.pair[i + 1]) for _, i, _ in ds]
    last = {tuple(x[1] for x in t[_split_top(ctx, lp + 1, rp, ',')[-1][0]:rp]) for lp, rp in lps}
    dlp, drp = next((i + 1, ctx.pair[i + 1]) for _, i, d in ds if d)
    parts = _split_top(ctx, dlp + 1, drp, ',')
    pname = t[drp - 1][1] if t[drp - 1][0] == 'id' else None
    if len(parts) >= 2 and pname and pname not in ctx.ids and len(last) == 1 and pname.startswith('pmt_') or \
            (len(parts) >= 2 and pname and pname not in ctx.ids and len(last) == 1 and rng.random() < 0.3):
        for lp, rp in lps:
            a = _split_top(ctx, lp + 1, rp, ',')[-1][0]
            spans.append((a - 1, rp, []))
        return Edit(spans, f'{ctx.func}: unused last parameter {pname} removed', meaning='type')
    ty = rng.choice(['unsigned char', 'int', 'unsigned short'])
    nm = ctx.fresh('pmt_unused')
    for lp, rp in lps:
        inner = [x[1] for x in t[lp + 1:rp]]
        if '...' in inner: return None
        text = f'{ty} {nm})' if inner in ([], ['void']) else f', {ty} {nm})'
        a = lp + 1 if inner in ([], ['void']) else rp
        spans.append((a, rp + 1, permc.new(text, '')))
    return Edit(spans, f'{ctx.func}: unused {ty} parameter added', meaning='type')



def r_wrap_form(ctx, rng, everywhere=False):
    """D23 (extended): the spelling of a wrap or of a constant that is cut to the target's width, as the
    whole value of a store: `L = x & 0xffff;` against `L = (unsigned short)x;` against `L = x % 65536;`
    (the last for an unsigned x only), the same for a byte; and `b = 1;` against `b = 0x101;` for a
    target of 8 or 16 bits (the stored bits are the same). Integers of at most 32 bits."""
    t = ctx.toks
    cands = []
    for n, root in _roots(ctx):
        if n.kind != 'stmt' or root.kind != 'assign' or root.op != '=' or _ikind(ctx, root.kids[0]) is None: continue
        kl = _ikind(ctx, root.kids[0])
        r = _unparen(root.kids[1])
        x = bits = None
        if r.kind == 'bin' and r.op == '&' and permc.const_value(r.kids[1], t) in (0xff, 0xffff): x, bits, form = r.kids[0], 8 if permc.const_value(r.kids[1], t) == 0xff else 16, 'mask'
        elif r.kind == 'bin' and r.op == '%' and permc.const_value(r.kids[1], t) in (256, 65536) and _ikind(ctx, r.kids[0]) is not None \
                and _ikind(ctx, r.kids[0])[1] is False: x, bits, form = r.kids[0], 8 if permc.const_value(r.kids[1], t) == 256 else 16, 'mod'
        elif r.kind == 'cast' and _tkind(ctx, ctx.src(r.op[0], r.op[1])) in ((8, False), (16, False)): x, bits, form = r.kids[0], _tkind(ctx, ctx.src(r.op[0], r.op[1]))[0], 'cast'
        if x is not None and _ikind(ctx, x) is not None:
            forms = [f for f in ('mask', 'cast') + (('mod',) if _ikind(ctx, x)[1] is False else ()) if f != form]
            cands.append((root.kids[1], x, bits, forms))
        kv = permc.const_value(r, t)
        if r.kind == 'atom' and r.op == 'num' and kv is not None and kl[0] < 32 and t[r.start][1][-1] not in 'uUlL':
            if 0 <= kv < (1 << kl[0]): cands.append((root.kids[1], None, f'{kv + (1 << kl[0]):#x}', None))
            elif (1 << kl[0]) <= kv < (2 << kl[0]): cands.append((root.kids[1], None, _lit(kv - (1 << kl[0])), None))
    if not cands: return None
    v, x, bits, forms = rng.choice(cands)
    if x is None: new = bits
    else:
        f = rng.choice(forms)
        xs = paren(ctx, _unparen(x))
        new = {'mask': f'{xs} & {"0xff" if bits == 8 else "0xffff"}', 'mod': f'{xs} % {256 if bits == 8 else 65536}',
               'cast': f'({"unsigned char" if bits == 8 else "unsigned short"}){xs}'}[f]
    return Edit([(v.start, v.end, permc.new(new, t[v.start][2]))], f'{ctx.src(v.start, v.end)[:30]} -> {new[:30]} @{_line(ctx, v.start)}')


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
    Rule('loop_form', 'D32', 4, r_loop_form),
    # movement over a distance, and locals joined or split (D33, D34, B8, B9 are new kinds)
    Rule('stmt_move', 'D6', 5, r_stmt_move),
    Rule('compound_swap', 'D6', 4, r_compound_swap),
    Rule('if_hoist', 'D33', 4, r_if_hoist),
    Rule('loop_test', 'D34', 4, r_loop_test),
    Rule('local_merge', 'B8', 5, r_local_merge),
    Rule('local_split', 'B8', 5, r_local_split),
    Rule('local_reuse', 'B9', 4, r_local_reuse),
    # catalogue kinds that had no rule
    Rule('embed_assign', 'D11', 3, r_embed_assign),
    Rule('abs_form', 'D15', 2, r_abs_form),
    Rule('expr_split', 'D27', 3, r_expr_split),
    Rule('return_local', 'D24', 3, r_return_local, True),
    Rule('ptr_base', 'D26', 2, r_ptr_base),
    Rule('byte_read', 'D21', 3, r_byte_read),
    Rule('const_type', 'D25', 2, r_const_type),
    Rule('arg_local', 'D28', 3, r_arg_local),
    Rule('setter', 'B7', 2, r_setter, True),
    Rule('memcpy_loop', 'D17', 2, r_memcpy_loop),
    Rule('global_type', 'A7', 3, r_global_type),
    Rule('callee_linkage', 'A9', 2, r_callee_linkage),
    # spellings
    Rule('sub_neg', 'generic', 1, r_sub_neg),
    Rule('factor', 'generic', 1, r_factor),
    Rule('arrow_deref', 'generic', 0.5, r_arrow_deref),
    Rule('index_deref', 'generic', 1, r_index_deref),
    Rule('pad_local', 'generic', 0.5, r_pad_local),
    # the levers of compiler.md that had no rule (permuter-coverage.md; D35 to D38 are new kinds)
    Rule('known_const', 'C2', 4, r_known_const),
    Rule('formula_copy', 'D35', 1, r_formula_copy),
    Rule('sink_assign', 'C1, D6', 4, r_sink_assign),
    Rule('step_fold', 'D12, D13', 3, r_step_fold),
    Rule('index_step', 'D12', 1.5, r_index_step),
    Rule('loop_step', 'D13', 2, r_loop_step),
    Rule('early_exit', 'D7', 3, r_early_exit),
    Rule('clamp_form', 'D18', 2, r_clamp_form),
    Rule('chain_tail', 'D33', 2, r_chain_tail),
    Rule('view_access', 'D5', 2, r_view_access),
    Rule('narrow_operand', 'D23', 2, r_narrow_operand),
    Rule('cast_neg', 'D23', 0.5, r_cast_neg),
    Rule('mask_form', 'D16', 2, r_mask_form),
    Rule('via_target', 'D27', 2, r_via_target),
    Rule('subexpr_local', 'B1 add', 3, r_subexpr_local),
    Rule('expr_helper', 'B6', 3, r_expr_helper),
    Rule('addr_helper', 'B6, B7', 2, r_addr_helper, True),
    Rule('outline_block', 'B6', 3, r_outline_block, True),
    Rule('cache_group', 'B1 add', 2, r_cache_group),
    Rule('scope_init', 'B3', 1, r_scope_init),
    Rule('union_split', 'D21', 0.5, r_union_split),
    Rule('repeat_test', 'D5', 0.5, r_repeat_test),
    Rule('index_split', 'D26', 0.5, r_index_split),
    Rule('cmp_diff', 'generic', 0.5, r_cmp_diff),
    Rule('packed_struct', 'A7', 2, r_packed_struct),
    Rule('struct_array', 'A8', 1, r_struct_array),
    Rule('field_type', 'A7', 1.5, r_field_type),
    Rule('signature_type', 'A11', 2, r_signature_type),
    Rule('probe_test', 'D36', 4, r_probe_test),
    Rule('probe_elem', 'D2', 3, r_probe_elem),
    Rule('probe_ptr', 'C4', 2, r_probe_ptr),
    Rule('array_operand', 'D37', 2, r_array_operand),
    Rule('nop_form', 'D38', 0.5, r_nop_form),
    Rule('func_pragma', 'D30', 0.3, r_func_pragma),
    Rule('file_opts', 'A3', 0.5, r_file_opts),
    # idioms of the older tools, and one edit at every look-alike site (B10 is a new kind)
    Rule('dead_zero', 'D4', 2, r_dead_zero),
    Rule('literal_local', 'C2', 3, r_literal_local),
    Rule('uniform', 'B10', 5, r_uniform),
    # levers of the project notes
    Rule('copy_source', 'B1', 3, r_copy_source),
    Rule('known_guard', 'D4', 1.5, r_known_guard),
    Rule('switch_guard', 'D8', 1.5, r_switch_guard),
    Rule('bool_store', 'D18', 2, r_bool_store),
    Rule('accum_start', 'D27', 1.5, r_accum_start),
    Rule('regroup', 'D20', 1.5, r_regroup),
    Rule('probe_forms', 'D1', 3, r_probe_forms),
    Rule('extra_param', 'A11', 1, r_extra_param),
    Rule('wrap_form', 'D23', 1, r_wrap_form),
]
BY_ID = {r.id: r for r in RULES}
r_probe_cmp.enumerate = enum_probe_cmp
r_probe_shift.enumerate = enum_probe_shift
r_probe_addr.enumerate = enum_probe_addr
r_probe_remove.enumerate = enum_probe_remove
MEANING = {'retype': 'type', 'volatile_read': 'same', 'const_type': 'type', 'global_type': 'type', 'callee_linkage': 'type',
           'pad_local': 'probe', 'field_type': 'type', 'signature_type': 'type', 'file_opts': 'type', 'repeat_test': 'type',
           'probe_test': 'probe', 'probe_elem': 'probe', 'probe_ptr': 'probe', 'array_operand': 'probe', 'nop_form': 'probe',
           'probe_forms': 'probe', 'extra_param': 'type'}


# ---------------------------------------------------------------- engine-side rules (not part of the catalogue blocks above)
# Edits outside the function body: the macros FUNC invokes (macro_body, macro_expand, macro_fold)
# and the order of the file's declarations it names (file_order). They live in permmacro.py, which
# belongs to the permuter's engine; this block only registers them. Keep it last.
import permmacro as _permmacro          # noqa: E402
_permmacro.register(globals())
