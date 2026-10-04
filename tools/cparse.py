"""A small parser for the top level of period C sources, and Turbo C's struct layout.

Not a C compiler front end: enough to inventory a decompilation's declarations, move them
into headers, and lay out structs, on code that compiles with the original compiler. Used by
declinv.py, headergen.py, structrec.py, accessors.py and comments.py.

    strip_comments(text)     comments replaced by spaces (newlines kept), strings kept, so
                             offsets into the result are offsets into the text
    chunks(text)             the top-level pieces: (start, end, text), a preprocessor line,
                             a declaration up to its `;` or a function definition to its `}`
    analyse(path)            each top-level piece classified (below)
    tokens(text)             the token stream with comments removed, for proving an edit
                             changed nothing but comments (C and TASM)
    layout(body, ctx, kind)  field offsets of a struct or union body, by Turbo C's rules

analyse() entries: {kind, start, end, line, endline, text, ...} where kind is
  pp_define / pp_include / pp_<directive>   (name, norm)
  typedef                                   (name, norm)
  struct_def / union_def / enum_def         (name, norm, declarators, static)
  func_def                                  (name, norm: the prototype, params unnamed; static)
  proto                                     (name, norm, extern, static, oldstyle, wrapper)
  extern / var_def / static_var             (names: [{name, decl, init}])

setup(cfg) teaches the parser a project's own types and macros, from the headers in
[project] include and the optional [cparse] section of exhume.toml:
  typedefs       a typedef's size and signedness for layout (int16 is int, uint16 unsigned),
                 from the first definition whose type is known (the #ifdef __TURBOC__ branch
                 of portable.h; the host's int16_t is not known). A typedef the headers define
                 one way only (InputFn) also expands when declarations are compared (expand());
                 one defined per compiler does not, since it names another type on the host.
                 [cparse] typedefs = {name = "type"} adds or overrides.
  empty macros   an object-like macro some branch defines empty (HOST_LAYOUT_BEGIN): the
                 compiler sees nothing, so it is not part of the next declaration.
                 [cparse] empty_macros = [...] adds names.
  param macros   a one-parameter macro whose body is () or the parameter (OLDSTYLE): in
                 `int far f M((char c));` it wraps the parameter list. The entry is a proto
                 of f, oldstyle when the macro gives (), with wrapper = M and the parameters
                 kept in norm. [cparse] param_macros = [...] adds names.
Without setup() none of these are known, as before.
"""
import re

KW = set('''auto break case char const continue default do double else enum extern float for goto if int
long register return short signed sizeof static struct switch typedef union unsigned void volatile while
far near huge cdecl pascal interrupt _seg _cs _ds _es _ss asm'''.split())


TYPEDEFS = {}        # typedef name -> the type it names, as the compiler sees it
SAME_TYPEDEFS = {}   # typedef name -> its type, for typedefs the headers define one way only
EMPTY_MACROS = set()
PARAM_MACROS = {}    # name -> True when the compiler sees () (an old-style declaration)
_setup_for = None


def strip_comments(t):
    """Replace comments by spaces (keeping newlines), keep strings."""
    out = []; i = 0; n = len(t)
    while i < n:
        c = t[i]
        if c == '/' and i + 1 < n and t[i + 1] == '*':
            j = t.find('*/', i + 2); j = n if j < 0 else j + 2
            out.append(re.sub(r'[^\n]', ' ', t[i:j])); i = j
        elif c == '/' and i + 1 < n and t[i + 1] == '/':
            j = t.find('\n', i); j = n if j < 0 else j
            out.append(' ' * (j - i)); i = j
        elif c in '"\'':
            j = i + 1
            while j < n and t[j] != c:
                if t[j] == '\\': j += 1
                j += 1
            out.append(t[i:j + 1]); i = j + 1
        else:
            out.append(c); i += 1
    return ''.join(out)


def tokens(src, asm=False):
    """The whitespace-separated token stream of a source with its comments removed (strings
    and character constants kept whole). Two versions with equal streams differ only in
    comments and layout: the compiler sees the same program. asm=True reads `;` comments and
    TASM's `comment` blocks are not handled (none in the sources this was written for)."""
    out = []; i = 0; n = len(src)
    while i < n:
        c = src[i]
        if not asm and src.startswith('/*', i):
            j = src.find('*/', i + 2)
            if j < 0: raise ValueError('unterminated comment')
            out.append(' '); i = j + 2
        elif not asm and src.startswith('//', i):
            j = src.find('\n', i); i = n if j < 0 else j
        elif asm and c == ';':
            j = src.find('\n', i); i = n if j < 0 else j
        elif c in '"\'':
            j = i + 1
            while j < n and src[j] != c and not (asm and src[j] == '\n'):
                j += 2 if (src[j] == '\\' and not asm) else 1
            out.append(src[i:j + 1]); i = j + 1
        else:
            out.append(c); i += 1
    return ''.join(out).split()


def chunks(t):
    """Top-level chunks: (start, end, text). Preprocessor lines are their own chunks.
    t must have its comments stripped (strip_comments)."""
    res = []; i = 0; n = len(t); start = None; depth = 0
    while i < n:
        c = t[i]
        if start is None:
            if c.isspace(): i += 1; continue
            if c == '#':
                j = i
                while True:
                    k = t.find('\n', j); k = n if k < 0 else k
                    if t[k - 1:k] == '\\': j = k + 1; continue
                    break
                res.append((i, k, t[i:k])); i = k; continue
            start = i
        if c in '"\'':
            j = i + 1
            while j < n and t[j] != c:
                if t[j] == '\\': j += 1
                j += 1
            i = j + 1; continue
        if c == '{': depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                head = t[start:i]
                hb = head[:head.find('{')]
                if hb.strip().endswith(')') and '=' not in hb and not re.match(r'\s*(typedef|extern)\b', hb) \
                   and not re.match(r'\s*(static\s+)?(struct|union|enum)\s*\w*\s*$', hb):
                    res.append((start, i + 1, t[start:i + 1])); start = None; i += 1; continue
        elif c == ';' and depth == 0:
            res.append((start, i + 1, t[start:i + 1])); start = None
        i += 1
    return res


def struct_body(t):
    """The text between a definition's first `{` and the brace that closes it (t without
    comments): an initializer after the body is not part of it."""
    i = t.find('{'); d = 0
    for k in range(i, len(t)):
        if t[k] == '{': d += 1
        elif t[k] == '}':
            d -= 1
            if d == 0: return t[i + 1:k]
    return t[i + 1:t.rfind('}')]


def lineof(t, pos): return t.count('\n', 0, pos) + 1


def norm(s): return re.sub(r'\s+', ' ', s).strip()


def split_top(s, sep=','):
    out = []; d = 0; cur = ''
    for ch in s:
        if ch in '([{': d += 1
        elif ch in ')]}': d -= 1
        if ch == sep and d == 0: out.append(cur); cur = ''
        else: cur += ch
    out.append(cur); return out


TOK = re.compile(r'[A-Za-z_]\w*|\d\w*|\.\.\.|\S')


def param_type(p, typedefs):
    """Strip the parameter name from one parameter declaration."""
    p = norm(p)
    if p in ('void', '...', ''): return p
    toks = TOK.findall(p)
    m = re.match(r'(.*?\(\s*(?:far|near)?\s*\*\s*(?:far|near)?\s*)([A-Za-z_]\w*)?(\s*\)\s*)(\(.*\))(.*)$', p)     # function pointer
    if m and (m.group(2) is None or m.group(2) not in KW):
        inner = m.group(4)[1:-1]
        ps = ', '.join(param_type(q, typedefs) for q in split_top(inner)) if inner.strip() else ''
        return norm(m.group(1) + m.group(3) + '(' + ps + ')' + m.group(5))
    m = re.match(r'(.*?)([A-Za-z_]\w*)(\s*(?:\[[^\]]*\]\s*)+)$', p)                 # array
    if m and m.group(2) not in KW and m.group(2) not in typedefs and m.group(1).strip():
        return norm(m.group(1) + m.group(3))
    last = toks[-1]
    if re.match(r'[A-Za-z_]\w*$', last) and last not in KW and last not in typedefs and len(toks) > 1:
        if toks[-2] not in ('struct', 'union', 'enum'):
            return norm(p[:p.rfind(last)])
    return p


def proto_norm(decl, name, typedefs):
    """A function declaration normalised: parameters without names."""
    m = re.search(r'\b' + re.escape(name) + r'\s*\(', decl)
    j = decl.find('(', m.start())
    d = 0
    for k in range(j, len(decl)):
        if decl[k] == '(': d += 1
        elif decl[k] == ')':
            d -= 1
            if d == 0: break
    ps = [param_type(p, typedefs) for p in split_top(decl[j + 1:k])]
    return norm(decl[:j]) + '(' + ', '.join(ps) + ')' + norm(decl[k + 1:])


def _dname(dcl):
    dcl0 = dcl.split('=')[0]
    m = re.search(r'\(\s*(?:far|near)?\s*\*\s*(?:far|near)?\s*([A-Za-z_]\w*)\s*\)', dcl0)
    if m: return m.group(1)
    ids = [x for x in re.findall(r'[A-Za-z_]\w*', re.sub(r'\[[^\]]*\]', '', dcl0)) if x not in KW]
    return ids[-1] if ids else '?'


def blank_empty_macros(t):
    """t (comments stripped) with every empty macro name outside preprocessor lines replaced
    by spaces: offsets are kept, and the chunk after it starts at its real first token."""
    if not EMPTY_MACROS: return t
    pat = re.compile(r'\b(' + '|'.join(sorted(map(re.escape, EMPTY_MACROS))) + r')\b')
    out = []; directive = False
    for line in t.split('\n'):
        if directive or line.lstrip().startswith('#'):
            directive = line.rstrip().endswith('\\'); out.append(line); continue
        out.append(pat.sub(lambda m: ' ' * len(m.group(0)), line))
    return '\n'.join(out)


def unwrap_params(body):
    """(body, macro, oldstyle) with `NAME M((params))` read as `NAME(params)`, M a param macro;
    (body, None, False) when there is none."""
    if not PARAM_MACROS: return body, None, False
    m = re.search(r'\b([A-Za-z_]\w*)\s+(' + '|'.join(map(re.escape, PARAM_MACROS)) + r')\s*\(\s*\(', body)
    if not m: return body, None, False
    i = body.find('(', m.start(2)); d = 0
    for k in range(i, len(body)):
        if body[k] == '(': d += 1
        elif body[k] == ')':
            d -= 1
            if d == 0: break
    else: return body, None, False
    inner = body[i + 1:k].strip()
    return body[:m.end(1)] + inner + body[k + 1:], m.group(2), PARAM_MACROS[m.group(2)]


def analyse_text(raw):
    t = blank_empty_macros(strip_comments(raw))
    out = []; typedefs = set(TYPEDEFS) | set(SAME_TYPEDEFS)
    for s, e, c in chunks(t):
        ent = dict(start=s, end=e, line=lineof(t, s), endline=lineof(t, e - 1), text=raw[s:e])
        cn = norm(c)
        if c.startswith('#'):
            m = re.match(r'#\s*(\w+)\s*(.*)', cn)
            ent['kind'] = 'pp_' + (m.group(1) if m else '')
            if m and m.group(1) == 'define':
                mm = re.match(r'([A-Za-z_]\w*)(\([^)]*\))?\s*(.*)', m.group(2))
                ent['name'] = mm.group(1); ent['norm'] = norm(m.group(2))
            else: ent['norm'] = norm(m.group(2)) if m else cn
            out.append(ent); continue
        if re.match(r'typedef\b', cn):
            ent['kind'] = 'typedef'
            m = re.search(r'\(\s*(?:far|near)?\s*\*\s*([A-Za-z_]\w*)\s*\)', cn)
            nm = m.group(1) if m else (re.findall(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\])*\s*;$', cn) or ['?'])[0]
            ent['name'] = nm; ent['norm'] = cn; typedefs.add(nm)
            out.append(ent); continue
        m = re.match(r'(static\s+)?(struct|union|enum)\s+([A-Za-z_]\w*)?\s*\{', cn)
        if m:
            body_end = d = 0          # the brace closing the body (an initializer may follow)
            for k in range(cn.find('{'), len(cn)):
                if cn[k] == '{': d += 1
                elif cn[k] == '}':
                    d -= 1
                    if d == 0: body_end = k; break
            ent['kind'] = m.group(2) + '_def'; ent['name'] = m.group(3) or ''
            ent['norm'] = cn[:body_end + 1]
            ent['declarators'] = cn[body_end + 1:].strip().rstrip(';').strip()
            ent['static'] = bool(m.group(1))
            out.append(ent); continue
        if cn.endswith('}'):
            hb = cn[:cn.find('{')]
            m = re.search(r'([A-Za-z_]\w*)\s*\([^()]*(?:\([^()]*\)[^()]*)*\)\s*$', hb)
            ent['kind'] = 'func_def'; ent['name'] = m.group(1) if m else '?'
            ent['static'] = bool(re.match(r'static\b', hb))
            try: ent['norm'] = proto_norm(hb.strip(), ent['name'], typedefs)
            except Exception: ent['norm'] = norm(hb)
            out.append(ent); continue
        ext = bool(re.match(r'extern\b', cn)); st = bool(re.match(r'static\b', cn))
        body = re.sub(r'^(extern|static)\s+', '', cn).rstrip(';').strip()
        body, wrapper, wrapped_old = unwrap_params(body)
        d = 0; proto_name = None
        toks = list(re.finditer(r'[A-Za-z_]\w*|[()=\[\]{},]', body))
        for idx, tk in enumerate(toks):
            v = tk.group(0)
            if v == '(': d += 1; continue
            if v == ')': d -= 1; continue
            if v == '=' and d == 0: break
            if d == 0 and re.match(r'[A-Za-z_]', v) and idx + 1 < len(toks) and toks[idx + 1].group(0) == '(' and v not in KW:
                proto_name = v; break
        if proto_name and len(split_top(body)) == 1:
            ent['kind'] = 'proto'; ent['name'] = proto_name; ent['extern'] = ext; ent['static'] = st
            ent['norm'] = proto_norm(body, proto_name, typedefs)
            ent['oldstyle'] = bool(re.search(re.escape(proto_name) + r'\s*\(\s*\)', body))
            ent['wrapper'] = wrapper
            if wrapper:
                # the declaration as written: NAME M((params)), the compiler's view old-style
                # when M gives ()
                ent['oldstyle'] = ent['oldstyle'] or wrapped_old
                k = ent['norm'].find(proto_name + '(') + len(proto_name)
                ent['norm'] = ent['norm'][:k] + ' %s((' % wrapper + ent['norm'][k + 1:]
                q = ent['norm'].rfind(')'); ent['norm'] = ent['norm'][:q + 1] + ')' + ent['norm'][q + 1:]
            out.append(ent); continue
        parts = split_top(body)
        first = parts[0]
        n0 = _dname(first); f0 = first.split('=')[0]
        base = f0[:f0.rfind(n0)]
        cut = min([k for k in (base.find('*'), base.find('(')) if k >= 0] or [len(base)])
        base_type = re.sub(r'(\s*\b(far|near|huge)\b)+\s*$', '', base[:cut]).strip()
        names = []
        for k, p in enumerate(parts):
            dcl = p.split('=')[0].strip()
            init = '=' in p and not p.strip().startswith('(')
            names.append(dict(name=_dname(p), decl=norm(dcl if k == 0 else base_type + ' ' + dcl), init=init))
        ent['kind'] = 'extern' if ext else ('static_var' if st else 'var_def')
        ent['names'] = names
        out.append(ent)
    return out


def analyse(path):
    return analyse_text(open(path, encoding='latin1').read())


# ---- a project's typedefs and macros (setup) -------------------------------------------------
BASE_SIZES = None


def typedef_parts(cn):
    """(name, type) of a typedef's text (comments stripped, one line): the type with the name
    taken out, parameter names dropped (`void (far*)(NEARPTR)`); None if not understood."""
    body = norm(cn)
    if not body.startswith('typedef'): return None
    body = body[len('typedef'):].rstrip(';').strip()
    m = re.search(r'\(\s*(?:far|near|huge)?\s*\*\s*(?:far|near|huge)?\s*([A-Za-z_]\w*)\s*\)', body)
    if m:
        name = m.group(1)
        typ = param_type(body, {name})
        typ = body[:m.start(1)] + body[m.end(1):] if typ == body else typ
        return name, norm(typ)
    m = re.match(r'(.*?)([A-Za-z_]\w*)\s*((?:\[[^\]]*\]\s*)*)$', body)
    if not m or not m.group(1).strip(): return None
    return m.group(2), norm(m.group(1) + m.group(3))


def type_size(typ):
    """The size of a typedef's type, or None: a basic type, an earlier typedef, a pointer whose
    distance is written (far or huge 4, near 2)."""
    t = norm(typ)
    if '*' in t:
        p = t[t.find('('):] if '(' in t else t
        if re.search(r'\b(far|huge)\s*\*', p): return 4
        if re.search(r'\bnear\s*\*', p): return 2
        return None
    if '[' in t: return None
    return SIZES.get(re.sub(r'\b(const|volatile)\s+', '', t))


def resolve(typ):
    """typ with each simple typedef name replaced by its basic type (int16 -> int), for
    layout's char test and signedness."""
    for _ in range(3):
        n = re.sub(r'\b([A-Za-z_]\w*)\b', lambda m: TYPEDEFS.get(m.group(1), m.group(1))
                   if m.group(1) in TYPEDEFS and '(' not in TYPEDEFS[m.group(1)] else m.group(1), typ)
        if n == typ: break
        typ = n
    return typ


def expand(s):
    """A normalised declaration with each typedef the headers define one way only written out
    (InputFn -> void (far*)(NEARPTR)): two forms that differ only so name the same type on
    every build. Typedefs defined per compiler (int16) stay."""
    if not SAME_TYPEDEFS: return s
    pat = re.compile(r'\b(' + '|'.join(sorted(map(re.escape, SAME_TYPEDEFS), key=len, reverse=True)) + r')\b')
    for _ in range(3):
        n = pat.sub(lambda m: SAME_TYPEDEFS[m.group(1)], s)
        if n == s: break
        s = n
    return s


def setup(cfg, headers=None):
    """Learn the project's typedefs, empty macros and param macros (see the top of this file)
    from the headers in [project] include (or the paths in headers) and exhume.toml's
    [cparse]. Safe to call more than once."""
    global BASE_SIZES, _setup_for
    import glob, os
    key = (getattr(cfg, 'file', None), tuple(headers) if headers else None)
    if _setup_for == key: return
    _setup_for = key
    if BASE_SIZES is None: BASE_SIZES = dict(SIZES)
    SIZES.clear(); SIZES.update(BASE_SIZES)
    TYPEDEFS.clear(); SAME_TYPEDEFS.clear(); EMPTY_MACROS.clear(); PARAM_MACROS.clear()
    if headers is None:
        inc = getattr(cfg, 'include', None)
        headers = sorted(glob.glob(os.path.join(inc, '*.[Hh]'))) if inc and os.path.isdir(inc) else []
    forms = {}; order = []; defs = {}
    for h in headers:
        t = strip_comments(open(h, encoding='latin1').read())
        for s, e, c in chunks(t):
            cn = norm(c)
            if cn.startswith('#'):
                m = re.match(r'#\s*define\s+([A-Za-z_]\w*)(\(\s*([A-Za-z_]\w*)?\s*\))?\s*(.*)$', cn)
                if m: defs.setdefault(m.group(1), []).append((m.group(2) is not None, m.group(3), m.group(4).strip()))
                continue
            if not cn.startswith('typedef'): continue
            tp = typedef_parts(cn)
            if not tp: continue
            if tp[0] not in forms: order.append(tp[0])
            forms.setdefault(tp[0], [])
            if tp[1] not in forms[tp[0]]: forms[tp[0]].append(tp[1])
    for name, ds in defs.items():
        if name in KW: continue
        if any(not fl and not body for fl, _, body in ds): EMPTY_MACROS.add(name)
        if all(fl and p for fl, p, _ in ds) and all(body in ('()', p) for fl, p, body in ds):
            PARAM_MACROS[name] = any(body == '()' for _, _, body in ds)
    for name in order:
        fs = forms[name]
        for typ in fs:
            if type_size(typ) is not None or re.match(r'(struct|union|enum)\b', typ):
                TYPEDEFS[name] = typ
                if type_size(typ) is not None: SIZES[name] = type_size(typ)
                break
        if len(fs) == 1: SAME_TYPEDEFS[name] = fs[0]
    c = (getattr(cfg, 'raw', None) or {}).get('cparse', {})
    for name, typ in c.get('typedefs', {}).items():
        TYPEDEFS[name] = norm(typ)
        if type_size(typ) is not None: SIZES[name] = type_size(typ)
    EMPTY_MACROS.update(c.get('empty_macros', []))
    for name in c.get('param_macros', []): PARAM_MACROS.setdefault(name, True)


# ---- struct layout -------------------------------------------------------------------------
# Turbo C++ 1.01 without -a: members are byte-aligned; a bitfield goes in the 16-bit window
# (8-bit for char bitfields) starting at the byte that holds the next free bit, and moves to
# the next byte when it would not fit; a member after a bitfield run starts at the next whole
# byte. Profiles for other compilers would replace these rules.
SIZES = {'char': 1, 'signed char': 1, 'unsigned char': 1, 'int': 2, 'unsigned': 2, 'unsigned int': 2,
         'short': 2, 'unsigned short': 2, 'signed': 2, 'signed int': 2, 'long': 4, 'unsigned long': 4,
         'signed long': 4, 'long int': 4, 'float': 4, 'double': 8, 'void': 1}
MACROS = {}


def split_members(body):
    out = []; d = 0; cur = ''
    for ch in body:
        if ch == '{': d += 1
        elif ch == '}': d -= 1
        if ch == ';' and d == 0:
            if cur.strip(): out.append(cur.strip())
            cur = ''
        else: cur += ch
    if cur.strip(): out.append(cur.strip())
    return out


def evalc(expr):
    expr = expr.strip()
    for _ in range(5):
        expr = re.sub(r'\b([A-Za-z_]\w*)\b', lambda m: '(' + MACROS[m.group(1)] + ')' if m.group(1) in MACROS else m.group(1), expr)
    if not expr: return None
    e = re.sub(r'\b0x([0-9A-Fa-f]+)\b', lambda m: str(int(m.group(1), 16)), expr)
    e = re.sub(r'(\d+)[uUlL]+\b', r'\1', e)
    e = re.sub(r'\bsizeof\s*\(\s*(?:struct\s+)?(\w[\w ]*?)\s*\)', lambda m: str(SIZES.get(m.group(1).strip(), 0)), e)
    return int(eval(e, {}))


class Ctx:
    def __init__(self, tags=None):
        self.tags = tags or {}      # 'struct X' -> (size, fields)


def member_layout(decl, ctx):
    """-> [(name, type, size, bits or None, nested fields or None)]"""
    decl = norm(decl)
    nested = None
    m = re.match(r'(struct|union)\s*(\w*)\s*\{(.*)\}\s*(.*)$', decl, re.S)
    if m:
        sub = layout(m.group(3), ctx, m.group(1))
        base = ('%s %s' % (m.group(1), m.group(2))).strip() if m.group(2) else m.group(1) + ' {...}'
        if m.group(2): ctx.tags['%s %s' % (m.group(1), m.group(2))] = (struct_size(sub), sub)
        bsize = struct_size(sub); nested = sub; rest = m.group(4)
    else:
        mm = re.match(r'((?:(?:unsigned|signed|char|int|short|long|float|double|void|const|volatile)\b\s*)+|(?:struct|union|enum)\s+\w+|\w+)\s*(.*)$', decl)
        base = mm.group(1).strip(); rest = mm.group(2)
        if base.startswith(('struct', 'union')):
            t = ctx.tags.get(base); bsize = t[0] if t else None; nested = t[1] if t else None
        elif base.startswith('enum'): bsize = 2
        else: bsize = SIZES.get(re.sub(r'\s+', ' ', base), None)
    res = []
    for d in split_top(rest):
        d = d.strip()
        if not d: res.append((None, base, bsize, None, nested)); continue
        bits = None
        if ':' in d and '?' not in d:
            d, b = d.rsplit(':', 1); bits = evalc(b); d = d.strip()
        ptr = re.match(r'^((?:far|near|huge)?\s*\*\s*(?:far|near|huge)?\s*)+', d)
        fp = re.match(r'\(\s*(far|near)?\s*\*\s*(\w+)\s*((?:\[[^\]]*\])*)\s*\)\s*\(.*\)\s*$', d)
        if fp:
            size = 4 if fp.group(1) != 'near' else 2
            for a in re.findall(r'\[([^\]]*)\]', fp.group(3)): size *= evalc(a)
            res.append((fp.group(2), base + ' (*)()', size, None, None)); continue
        if ptr and '*' in ptr.group(0):
            p = ptr.group(0)
            size = 4 if re.search(r'far\s*\*|huge\s*\*', p) else 2
            d2 = d[len(p):].strip(); typ = base + ' ' + re.sub(r'\s+', ' ', p.strip()); nest = None
        else:
            d2 = d; size = bsize; typ = base; nest = nested
        dm = re.match(r'(\w+)\s*((?:\[[^\]]*\])*)$', d2)
        if not dm: raise ValueError('cannot parse declarator %r in %r' % (d, decl))
        dims = [evalc(a) for a in re.findall(r'\[([^\]]*)\]', dm.group(2))]
        if dims:
            n = 1
            for x in dims: n *= x
            typ = typ + ''.join('[%d]' % x for x in dims)
            size = None if size is None else size * n
        res.append((dm.group(1), typ, size, bits, nest))
    return res


def struct_size(fields):
    return max((f['end'] for f in fields), default=0)


def layout(body, ctx=None, kind='struct'):
    """[{name, type, off, bits, bitoff, size, end, unit, nested}] for a struct or union body."""
    ctx = ctx or Ctx()
    fields = []; bit = 0
    for decl in split_members(body):
        for name, typ, size, bits, nested in member_layout(decl, ctx):
            if kind == 'union': bit = 0
            if bits is not None:
                w = 8 if re.search(r'\bchar\b', resolve(typ)) else 16
                if bits == 0: bit = (bit + 7) // 8 * 8; continue
                byte = bit // 8
                if (bit - byte * 8) + bits > w: byte += 1; bit = byte * 8
                fields.append(dict(name=name, type=typ, off=bit // 8, bitoff=bit % 8, bits=bits,
                                   size=None, end=(bit + bits + 7) // 8, unit=w))
                bit += bits
            else:
                off = (bit + 7) // 8
                if size is None: raise ValueError('unknown size for %s %s' % (typ, name))
                fields.append(dict(name=name, type=typ, off=off, bits=None, size=size, end=off + size, nested=nested))
                bit = (off + size) * 8
    return fields


def layout_text(src, ctx):
    """Lay out every top-level struct and union defined in src into ctx.tags."""
    src = strip_comments(src)
    for m in re.finditer(r'(struct|union)\s+(\w+)\s*\{', src):
        if src[:m.start()].count('{') - src[:m.start()].count('}') != 0: continue   # nested
        i = m.end() - 1; d = 0
        for j in range(i, len(src)):
            if src[j] == '{': d += 1
            elif src[j] == '}':
                d -= 1
                if d == 0: break
        fl = layout(src[i + 1:j], ctx, m.group(1))
        ctx.tags['%s %s' % (m.group(1), m.group(2))] = (struct_size(fl), fl)


def leaves(fl, base=0, prefix=''):
    """Every field down to the scalars: (path, absolute bit, bits, unit, type, size, is_container)."""
    for x in fl:
        nm = prefix + (x['name'] or '?')
        if x['bits']:
            yield (nm, (base + x['off']) * 8 + x['bitoff'], x['bits'], 'bf%d' % x['unit'], x['type'], None, False)
        else:
            nested = x.get('nested')
            if nested and '[' not in x['type']:
                yield (nm, (base + x['off']) * 8, x['size'] * 8, 'struct', x['type'], x['size'], True)
                yield from leaves(nested, base + x['off'], nm + '.')
            else:
                yield (nm, (base + x['off']) * 8, x['size'] * 8, 'scalar', re.sub(r'\s+', ' ', x['type']), x['size'], False)
