"""A small C reader for armpermute2.py: tokens, statement structure and expressions of one
function, with no C parser library (fast enough to run on every variant).

Tokens are (kind, text, ws): ws is the whitespace and comments before the token, so rendering
is ''.join(ws + text) and a file renders back byte for byte. Kinds: id, num, str, chr, op, pp
(a whole preprocessor line).

Structure (parse_body): a tree of statements over token indexes. Node kinds:
  block   [lb, rb]            children
  stmt    [start, end)        an expression statement, declaration, return, break, goto ...
  if      cond (lp, rp), then, els (node or None), else_at (index of 'else' or None)
  while / for / do / switch   cond (lp, rp), body
  label   [start, end)        'case K:', 'default:', 'name:'
  empty   ';'
Every node has start and end (end exclusive) and parent.

Expressions (parse_expr): nodes (kind, start, end, parts...) over token indexes, built by
precedence climbing: atom, paren, call, index, member, postfix, unary, cast, sizeof, binary,
cond, assign, comma.

Types: the integer and other type names a file knows (C keywords plus every typedef in it and
in the headers it includes), declared types of globals, struct fields and locals."""
import os, re

KEYWORDS = {'auto', 'break', 'case', 'char', 'const', 'continue', 'default', 'do', 'double', 'else', 'enum',
            'extern', 'float', 'for', 'goto', 'if', 'inline', 'int', 'long', 'register', 'return', 'short',
            'signed', 'sizeof', 'static', 'struct', 'switch', 'typedef', 'union', 'unsigned', 'void',
            'volatile', 'while', '__inline', '__forceinline', '__packed', '__irq', '__weak', '__asm',
            '__value_in_regs', '__align', '__restrict', '__int64'}
BASE_TYPES = {'char', 'short', 'int', 'long', 'unsigned', 'signed', 'float', 'double', 'void', '__int64'}
QUALIFIERS = {'const', 'volatile', '__packed', 'register', 'static', 'extern', '__inline', '__forceinline',
              'inline', '__align', '__restrict'}
INT_TYPES = ['unsigned char', 'signed char', 'unsigned short', 'short', 'unsigned int', 'int', 'char']

_TOK = re.compile(r'''
  (?P<ws>(?:\s+|/\*.*?\*/|//[^\n]*|\\\r?\n)+)
 |(?P<pp>\#(?:[^\n\\]|\\\r?\n|\\.)*)
 |(?P<id>[A-Za-z_]\w*)
 |(?P<num>(?:0[xX][0-9a-fA-F]+|\d+\.?\d*(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)[uUlLfF]*)
 |(?P<str>"(?:\\.|[^"\\\n])*")
 |(?P<chr>'(?:\\.|[^'\\\n])+')
 |(?P<op>>>=|<<=|\.\.\.|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^]=|[-+*/%&|^~!<>=?:;,.()\[\]{}])
''', re.S | re.X)


class Tok(tuple):
    __slots__ = ()

    def __new__(cls, kind, text, ws=' '):
        return tuple.__new__(cls, (kind, text, ws))

    kind = property(lambda s: s[0])
    text = property(lambda s: s[1])
    ws = property(lambda s: s[2])


def tokenize(text):
    """[Tok] of text, plus the trailing whitespace."""
    out, pos, ws = [], 0, ''
    n = len(text)
    while pos < n:
        m = _TOK.match(text, pos)
        if not m: raise ValueError(f'cannot tokenize at {text[pos:pos + 40]!r}')
        k = m.lastgroup
        if k == 'ws':
            ws += m.group()
        else:
            if k == 'pp' and ws and not ws.endswith('\n') and out:
                pass
            out.append(Tok(k, m.group(), ws)); ws = ''
        pos = m.end()
    return out, ws


def render(toks, tail=''):
    return ''.join(t[2] + t[1] for t in toks) + tail


def new(text, ws=' '):
    """Tokens of a C fragment, the first with whitespace ws."""
    toks, _ = tokenize(text)
    if toks: toks[0] = Tok(toks[0][0], toks[0][1], ws)
    return toks


def texts(toks, a, b):
    return ' '.join(t[1] for t in toks[a:b])


def match_brackets(toks):
    """{index of an opening bracket: index of its closer} and the reverse."""
    pair, stack = {}, []
    for i, t in enumerate(toks):
        if t[0] != 'op': continue
        c = t[1]
        if c in '([{': stack.append(i)
        elif c in ')]}':
            if not stack: raise ValueError(f'unbalanced {c} at token {i}')
            j = stack.pop(); pair[j] = i; pair[i] = j
    if stack: raise ValueError('unbalanced brackets')
    return pair


# ---------------------------------------------------------------- types known to a file

_TYPEDEF = re.compile(r'\btypedef\b([^;{]*(?:\{(?:[^{}]|\{[^{}]*\})*\})?[^;]*?)\b(\w+)\s*(?:\[[^\]]*\])?\s*;', re.S)


def strip_comments(text):
    return re.sub(r'/\*.*?\*/|//[^\n]*', ' ', text, flags=re.S)


def includes(path, text, include_dirs=()):
    out = []
    for inc in re.findall(r'^\s*#\s*include\s+"([^"]+)"', text, re.M):
        for d in (os.path.dirname(path),) + tuple(include_dirs):
            q = os.path.normpath(os.path.join(d, inc))
            if os.path.isfile(q): out.append(q); break
    return out


class TypeInfo:
    """Type names, typedefs ({name: C text}), struct fields ({field: type text}), globals
    ({name: (type text, array dims text or None, is extern)})."""

    def __init__(self):
        self.names = set(BASE_TYPES)
        self.typedefs = {}
        self.fields = {}
        self.struct_fields = {}       # typedef name -> [(field, type, dims)]
        self.globals = {}
        self.functions = set()

    def is_type_word(self, w):
        return w in self.names or w in ('struct', 'union', 'enum') or w in QUALIFIERS

    def scan(self, text):
        t = strip_comments(text)
        t = re.sub(r'^\s*#[^\n]*', ' ', t, flags=re.M)
        for m in _TYPEDEF.finditer(t):
            body, name = m.group(1), m.group(2)
            self.names.add(name)
            self.typedefs[name] = ' '.join(body.split())
            br = body.find('{')
            if br >= 0:
                fl = []
                inner = body[br + 1:body.rfind('}')]
                for decl in inner.split(';'):
                    d = ' '.join(decl.split())
                    # one type, then one or more declarators (`uchar a, b[4], *c;`)
                    mt = re.match(r'^((?:(?:const|volatile|unsigned|signed|struct|union|enum)\s+)*[A-Za-z_]\w*(?:\s+(?:int|char|short|long)\b)*)\s*(.*)$', d)
                    if not mt or not mt.group(2): continue
                    for part in mt.group(2).split(','):
                        mm = re.match(r'^\s*([*\s]*)(\w+)\s*((?:\[[^\]]*\])*)\s*(:\s*\d+)?\s*$', part)
                        if not mm: continue
                        ty = (mt.group(1) + ' ' + mm.group(1).strip()).strip()
                        if not mm.group(4): fl.append((mm.group(2), ty, mm.group(3) or None))     # not a bit-field
                        self.fields.setdefault(mm.group(2), ty if not mm.group(3) else ty + '[]')
                self.struct_fields[name] = fl

    def scan_globals(self, toks):
        """Globals declared at file scope in toks (top-level declarations ending in ;)."""
        depth = 0; start = 0
        for i, t in enumerate(toks):
            if t[0] == 'pp': start = i + 1; continue
            if t[1] in ('{', '(', '['): depth += 1
            elif t[1] in ('}', ')', ']'):
                depth -= 1
                if depth == 0 and t[1] == '}': start = i + 1
            elif t[1] == ';' and depth == 0:
                self._global_decl(toks, start, i); start = i + 1

    def _global_decl(self, toks, a, b):
        words = [t[1] for t in toks[a:b]]
        if not words or words[0] == 'typedef': return
        k = 0; ty = []
        extern = False
        while k < len(words) and (self.is_type_word(words[k]) or words[k] in ('struct', 'union', 'enum')):
            if words[k] == 'extern': extern = True
            elif words[k] not in ('static', 'extern'): ty.append(words[k])
            if words[k] in ('struct', 'union', 'enum') and k + 1 < len(words): k += 1; ty.append(words[k])
            k += 1
        if not ty: return
        # declarators split by top-level commas
        rest = words[k:]
        depth = 0; cur = []; decls = []
        for w in rest:
            if w in '([{': depth += 1
            if w in ')]}': depth -= 1
            if w == ',' and depth == 0: decls.append(cur); cur = []
            else: cur.append(w)
        decls.append(cur)
        for d in decls:
            if '=' in d: d = d[:d.index('=')]
            if '(' in d:
                if d and re.match(r'\w+$', d[0]): self.functions.add(d[0])
                continue
            stars = ''.join(x for x in d if x == '*')
            names = [x for x in d if re.match(r'[A-Za-z_]\w*$', x) and x not in QUALIFIERS]
            if not names: continue
            dims = None
            if '[' in d: dims = ' '.join(d[d.index('['):])
            self.globals[names[0]] = (' '.join(ty) + (' ' + stars if stars else ''), dims, extern)


def file_types(path, text, include_dirs=()):
    """TypeInfo of a source and the headers it includes (quoted includes, recursively)."""
    ti = TypeInfo()
    seen, todo = set(), [(os.path.abspath(path), text)]
    while todo:
        p, t = todo.pop()
        if p in seen: continue
        seen.add(p)
        ti.scan(t)
        for q in includes(p, t, include_dirs):
            if q not in seen:
                with open(q, encoding='latin1') as f: todo.append((q, f.read()))
    return ti


# ---------------------------------------------------------------- the function

def find_function(toks, name, pair=None):
    """(start, lb, rb) of FUNC's definition: start is the first token of its declaration
    (after the previous top-level ; or } or preprocessor line), lb/rb its body's braces."""
    pair = pair or match_brackets(toks)
    depth = 0
    for i, t in enumerate(toks):
        if t[1] == '{': depth += 1
        elif t[1] == '}': depth -= 1
        elif depth == 0 and t[0] == 'id' and t[1] == name and i + 1 < len(toks) and toks[i + 1][1] == '(':
            j = pair[i + 1] + 1
            # K&R or plain definition: the next { at top level after the parameter list
            k = j
            while k < len(toks) and toks[k][1] not in ('{', ';'): k += 1
            if k < len(toks) and toks[k][1] == '{':
                s = i
                while s > 0 and toks[s - 1][0] != 'pp' and toks[s - 1][1] not in (';', '}'): s -= 1
                return s, k, pair[k]
    raise LookupError(f'no definition of {name}')


class Node:
    __slots__ = ('kind', 'start', 'end', 'children', 'cond', 'then', 'els', 'else_at', 'body', 'parent', 'extra')

    def __init__(self, kind, start, end, **kw):
        self.kind, self.start, self.end = kind, start, end
        self.children = kw.get('children', [])
        self.cond = kw.get('cond'); self.then = kw.get('then'); self.els = kw.get('els')
        self.else_at = kw.get('else_at'); self.body = kw.get('body'); self.parent = None
        self.extra = kw.get('extra')

    def __repr__(self):
        return f'<{self.kind} {self.start}:{self.end}>'

    def subs(self):
        if self.kind == 'block': return list(self.children)
        out = []
        for x in (self.then, self.els, self.body):
            if x is not None: out.append(x)
        return out


def parse_body(toks, lb, pair=None):
    """The block node of the braces at lb."""
    pair = pair or match_brackets(toks)
    root = _block(toks, lb, pair)
    _link(root, None)
    return root


def _link(n, parent):
    n.parent = parent
    for c in n.subs(): _link(c, n)


def _block(toks, lb, pair):
    rb = pair[lb]
    kids = []
    i = lb + 1
    while i < rb:
        n = _stmt(toks, i, pair)
        kids.append(n); i = n.end
    return Node('block', lb, rb + 1, children=kids)


def _stmt(toks, i, pair):
    t = toks[i][1]
    if toks[i][0] == 'pp': return Node('pp', i, i + 1)
    if t == '{': return _block(toks, i, pair)
    if t == ';': return Node('empty', i, i + 1)
    if toks[i][0] == 'id':
        if t == 'if':
            lp = i + 1; rp = pair[lp]
            then = _stmt(toks, rp + 1, pair)
            els = else_at = None; end = then.end
            if end < len(toks) and toks[end][1] == 'else':
                else_at = end; els = _stmt(toks, end + 1, pair); end = els.end
            return Node('if', i, end, cond=(lp, rp), then=then, els=els, else_at=else_at)
        if t in ('while', 'switch'):
            lp = i + 1; rp = pair[lp]
            body = _stmt(toks, rp + 1, pair)
            return Node(t, i, body.end, cond=(lp, rp), body=body)
        if t == 'for':
            lp = i + 1; rp = pair[lp]
            body = _stmt(toks, rp + 1, pair)
            return Node('for', i, body.end, cond=(lp, rp), body=body)
        if t == 'do':
            body = _stmt(toks, i + 1, pair)
            w = body.end                                    # while ( ... ) ;
            lp = w + 1; rp = pair[lp]
            return Node('do', i, rp + 2, cond=(lp, rp), body=body)
        if t in ('case', 'default'):
            j = i
            depth = 0
            while not (toks[j][1] == ':' and depth == 0):
                if toks[j][1] in '([': depth += 1
                if toks[j][1] in ')]': depth -= 1
                if toks[j][1] == '?': depth += 1
                j += 1
            return Node('label', i, j + 1)
        if i + 1 < len(toks) and toks[i + 1][1] == ':' and t not in KEYWORDS:
            return Node('label', i, i + 2, extra='goto')
    # an expression statement or declaration: to the ; at this depth
    j = i
    while toks[j][1] != ';':
        if toks[j][1] in ('(', '[', '{'): j = pair[j]
        j += 1
    return Node('stmt', i, j + 1)


def is_decl(toks, n, types):
    """Whether statement node n is a declaration: it starts with a type word, or with two
    identifiers (a type the reader has not seen: `T x;`, `T *x;`)."""
    if n.kind != 'stmt': return False
    w = toks[n.start][1]
    if w in ('return', 'break', 'continue', 'goto'): return False
    if types.is_type_word(w): return True
    if toks[n.start][0] == 'id' and n.start + 1 < n.end:
        k = n.start + 1
        while toks[k][1] == '*': k += 1
        return toks[k][0] == 'id' and w not in KEYWORDS
    return False


def walk(n):
    yield n
    for c in n.subs(): yield from walk(c)


def blocks(root):
    return [n for n in walk(root) if n.kind == 'block']


# ---------------------------------------------------------------- expressions

PREC = {'*': 13, '/': 13, '%': 13, '+': 12, '-': 12, '<<': 11, '>>': 11, '<': 10, '>': 10, '<=': 10, '>=': 10,
        '==': 9, '!=': 9, '&': 8, '^': 7, '|': 6, '&&': 5, '||': 4}
ASSIGN = {'=', '+=', '-=', '*=', '/=', '%=', '&=', '|=', '^=', '<<=', '>>='}


class E:
    __slots__ = ('kind', 'start', 'end', 'op', 'kids')

    def __init__(self, kind, start, end, op=None, kids=()):
        self.kind, self.start, self.end, self.op, self.kids = kind, start, end, op, list(kids)

    def __repr__(self):
        return f'E({self.kind},{self.op},{self.start}:{self.end})'


class ExprParser:
    def __init__(self, toks, pair, types):
        self.t, self.pair, self.types = toks, pair, types

    def is_type_start(self, i, end):
        """Whether toks[i:] starts a type name (for a cast or sizeof)."""
        if i >= end: return False
        w = self.t[i][1]
        return self.t[i][0] == 'id' and (w in self.types.names or w in ('struct', 'union', 'enum', 'const', 'volatile', '__packed'))

    def looks_like_cast(self, lp):
        """(X) or (X *) of identifiers the reader does not know as types, followed by
        something that can only start an operand: a cast to a type from a header not read."""
        rp = self.pair[lp]
        inner = self.t[lp + 1:rp]
        if not inner or inner[0][0] != 'id' or inner[0][1] in KEYWORDS: return False
        if not all(x[0] == 'id' or x[1] == '*' for x in inner): return False
        if len([x for x in inner if x[0] == 'id']) > 1 and inner[-1][1] != '*':
            return False
        if rp + 1 >= self.end: return False
        nk, nw, _ = self.t[rp + 1]
        if nk in ('id', 'num', 'chr', 'str') or nw in ('(', '~', '!'): return True
        return inner[-1][1] == '*' and nw in ('&', '*')

    def parse(self, a, b):
        self.i, self.end = a, b
        e = self.comma()
        if self.i != b: raise ValueError(f'trailing tokens at {self.i}: {texts(self.t, self.i, b)[:60]}')
        return e

    def peek(self):
        return self.t[self.i][1] if self.i < self.end else None

    def comma(self):
        a = self.i
        e = self.assign()
        while self.peek() == ',':
            self.i += 1
            r = self.assign()
            e = E('comma', a, self.i, ',', (e, r))
        return e

    def assign(self):
        a = self.i
        l = self.cond()
        op = self.peek()
        if op in ASSIGN:
            self.i += 1
            r = self.assign()
            return E('assign', a, self.i, op, (l, r))
        return l

    def cond(self):
        a = self.i
        c = self.binary(4)
        if self.peek() == '?':
            self.i += 1
            x = self.comma()
            if self.peek() != ':': raise ValueError('expected :')
            self.i += 1
            y = self.cond()
            return E('cond', a, self.i, '?', (c, x, y))
        return c

    def binary(self, prec):
        a = self.i
        l = self.unary()
        while True:
            op = self.peek()
            p = PREC.get(op)
            if p is None or p < prec: return l
            self.i += 1
            r = self.binary(p + 1)
            l = E('bin', a, self.i, op, (l, r))

    def unary(self):
        a = self.i
        op = self.peek()
        if op in ('-', '+', '!', '~', '*', '&', '++', '--'):
            self.i += 1
            x = self.unary()
            return E('unary', a, self.i, op, (x,))
        if op == 'sizeof':
            self.i += 1
            if self.peek() == '(' and self.is_type_start(self.i + 1, self.end):
                self.i = self.pair[self.i] + 1
                return E('sizeof', a, self.i, 'type')
            x = self.unary()
            return E('sizeof', a, self.i, 'expr', (x,))
        if op == '(' and (self.is_type_start(self.i + 1, self.end) or self.looks_like_cast(self.i)):
            rp = self.pair[self.i]
            self.i = rp + 1
            x = self.unary()
            return E('cast', a, self.i, (a + 1, rp), (x,))
        return self.postfix()

    def postfix(self):
        a = self.i
        e = self.primary()
        while True:
            op = self.peek()
            if op == '(':
                rp = self.pair[self.i]
                args = []
                j = self.i + 1
                if j < rp:
                    sub = ExprParser(self.t, self.pair, self.types)
                    args = _split_args(sub, j, rp)
                self.i = rp + 1
                e = E('call', a, self.i, None, [e] + args)
            elif op == '[':
                rb = self.pair[self.i]
                sub = ExprParser(self.t, self.pair, self.types)
                idx = sub.parse(self.i + 1, rb)
                self.i = rb + 1
                e = E('index', a, self.i, None, (e, idx))
            elif op in ('.', '->'):
                self.i += 2
                e = E('member', a, self.i, op, (e,))
            elif op in ('++', '--'):
                self.i += 1
                e = E('postfix', a, self.i, op, (e,))
            else:
                return e

    def primary(self):
        a = self.i
        if a >= self.end: raise ValueError('unexpected end of expression')
        k, w, _ = self.t[a]
        if w == '(':
            rp = self.pair[a]
            sub = ExprParser(self.t, self.pair, self.types)
            inner = sub.parse(a + 1, rp)
            self.i = rp + 1
            return E('paren', a, self.i, None, (inner,))
        if k in ('id', 'num', 'chr'):
            self.i += 1
            return E('atom', a, self.i, k)
        if k == 'str':
            self.i += 1
            while self.i < self.end and self.t[self.i][0] == 'str': self.i += 1
            return E('atom', a, self.i, 'str')
        raise ValueError(f'unexpected {w!r} at {a}')


def _split_args(p, a, b):
    out = []
    depth = 0; s = a
    for j in range(a, b):
        w = p.t[j][1]
        if w in '([{': depth += 1
        elif w in ')]}': depth -= 1
        elif w == ',' and depth == 0:
            out.append(p.parse(s, j)); s = j + 1
    out.append(p.parse(s, b))
    return out


def ewalk(e):
    yield e
    for k in e.kids: yield from ewalk(k)


def has_side_effects(e, toks):
    for x in ewalk(e):
        if x.kind in ('assign', 'call') or x.kind in ('postfix',) or (x.kind == 'unary' and x.op in ('++', '--')):
            return True
    return False


def idents(e, toks):
    return {toks[x.start][1] for x in ewalk(e) if x.kind == 'atom' and x.op == 'id'}


def is_lvalue_simple(e, toks):
    """An identifier, a member chain or an index with constant/identifier index: something a
    store can name twice without side effects."""
    if e.kind == 'atom' and e.op == 'id': return True
    if e.kind == 'member': return is_lvalue_simple(e.kids[0], toks)
    if e.kind == 'index':
        return is_lvalue_simple(e.kids[0], toks) and e.kids[1].kind == 'atom'
    if e.kind == 'paren': return is_lvalue_simple(e.kids[0], toks)
    return False


def const_value(e, toks):
    """The integer value of a constant expression of literals, or None."""
    if e.kind == 'atom' and e.op == 'num':
        s = toks[e.start][1].rstrip('uUlL')
        try:
            return int(s, 0) if not re.search(r'[.eEfF]', s) or s.lower().startswith('0x') else None
        except ValueError:
            try: return int(s, 8)
            except ValueError: return None
    if e.kind == 'atom' and e.op == 'chr':
        s = toks[e.start][1][1:-1]
        if len(s) == 1: return ord(s)
        return None
    if e.kind == 'paren': return const_value(e.kids[0], toks)
    if e.kind == 'unary' and e.op == '-':
        v = const_value(e.kids[0], toks)
        return -v if v is not None else None
    return None
