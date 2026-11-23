"""Edits outside the function body, for armpermute2.py: the function-like macros the function
uses, and the order of the file's declarations it refers to.

The rules of permrules.py edit the tokens of FUNC's body. A macro FUNC invokes at fifty sites is
one preprocessor token outside it, so its body could only be changed by hand; and armcc's choice
of data anchor and literal order can follow the order in which the file declares its globals.
permrules.py registers these rules at its end (`permmacro.register`); they take a Ctx and a
random generator and return an Edit like any other rule.

- macros_used(ctx): the view of the function-like macros defined in the file that FUNC's body
  invokes: (token index of the #define, name, parameters, body text, the text before the body).
- macro_body: one rewrite of such a macro's body (so of every invocation at once):
  `do { } while (0)` against a plain block; two statements against one comma expression;
  `x[i++] = v;` against `x[i] = v; i++;` (these three keep the meaning); two statements in the
  other order; a block-local temporary for a parameter against its direct use; a parameter
  with or without its parentheses (these three are marked 'type': what they do depends on the
  arguments at every invocation in the file, which is not checked).
- macro_expand: one invocation in FUNC written out as the preprocessor would (the arguments'
  tokens in place of the parameters), so that this site can then be edited alone; macro_fold is
  the inverse: a run of tokens in FUNC that is exactly a macro's expansion becomes the
  invocation. Both keep the meaning. A body with # or ## is left alone.
- file_order: two file-scope declarations of globals FUNC refers to change places. Two extern
  declarations: 'same' (no storage, no layout). A definition among them: 'type', because the
  order of definitions is the order of the objects in their section. The permuter's scorer
  (permscore) takes a data section's address from the map row of one symbol it defines and the
  others by their offsets in the variant's own section, so a reordered section gives other
  addresses than the image's and shows as differing literals: such an edit cannot score well
  by accident, but its object layout still has to be checked by match.py (the placement of
  every section), which the scorer does not do."""
import re
import permc
from permc import Tok

_DEFINE = re.compile(r'^(#[ \t]*define[ \t]+(\w+)\(([^)]*)\))(.*)$', re.S)
_KEYS = ('if', 'for', 'while', 'do', 'switch', 'return', 'goto', 'break', 'continue', 'case', 'default', 'else')


def macros_used(ctx, invoked=True):
    """[(token index, name, [parameters], body text, head text)] of the function-like macros
    defined in the file that FUNC's body invokes (with invoked false: of all of them)."""
    m = getattr(ctx, '_pm_macros' + str(invoked), None)
    if m is None:
        m = []
        for i, t in enumerate(ctx.toks):
            if t[0] != 'pp': continue
            mm = _DEFINE.match(re.sub(r'\\\r?\n', ' ', t[1]))
            if not mm: continue
            name = mm.group(2)
            if invoked and not any(ctx.toks[j + 1][1] == '(' for j in ctx.ids.get(name, [])): continue
            params = [p.strip() for p in mm.group(3).split(',') if p.strip()]
            if '...' in params or '#' in mm.group(4): continue
            m.append((i, name, params, mm.group(4).strip(), mm.group(1)))
        setattr(ctx, '_pm_macros' + str(invoked), m)
    return m


def _line(ctx, i):
    return 1 + sum(t[2].count('\n') for t in ctx.toks[ctx.lb:i + 1])


def _words(text):
    return [t[1] for t in permc.tokenize(text)[0]]


def _pretty(words):
    """Words as C text with the usual spacing (`table[1] = (v); counter++;`)."""
    s = ' '.join(words)
    if any(x[:1] in '"\'' for x in words): return s
    s = re.sub(r'\s+([;,)\]])', r'\1', s)
    s = re.sub(r'([(\[])\s+', r'\1', s)
    s = re.sub(r'(\w) \[', r'\1[', s)
    s = re.sub(r'\b(?!(?:while|if|for|switch|return|sizeof|do|else|case)\b)(\w+) \(', r'\1(', s)
    s = re.sub(r'([\w\])]) (\+\+|--)(?=[;,)\] ]|$)', r'\1\2', s)
    return s


def _shape(w):
    """('do' | 'block' | None, the words inside)."""
    if len(w) >= 7 and w[:2] == ['do', '{'] and w[-5:] == ['}', 'while', '(', '0', ')']: return 'do', w[2:-5]
    if len(w) >= 2 and w[0] == '{' and w[-1] == '}':
        d = 0
        for k, x in enumerate(w):
            d += (x == '{') - (x == '}')
            if d == 0 and k < len(w) - 1: return None, w
        return 'block', w[1:-1]
    return None, w


def _stmts(inner):
    """The statements of a brace-free run of words (each without its ;), or None."""
    if '{' in inner or '}' in inner: return None
    out, cur, d = [], [], 0
    for x in inner:
        d += (x in '([') - (x in ')]') if len(x) == 1 else 0
        if x == ';' and d == 0:
            out.append(cur); cur = []
        else: cur.append(x)
    return out if not cur and out else None


def _top_commas(st):
    d, out = 0, []
    for k, x in enumerate(st):
        if len(x) == 1: d += (x in '([') - (x in ')]')
        if x == ',' and d == 0: out.append(k)
    return out


def _is_expr(ctx, st):
    return bool(st) and st[0] not in _KEYS and not ctx.types.is_type_word(st[0])


def _wrap(shape, stmts):
    inner = ' '.join(_pretty(s) + ';' for s in stmts)
    return 'do { ' + inner + ' } while (0)' if shape == 'do' else '{ ' + inner + ' }'


def _body_rewrites(ctx, params, body, rng):
    """[(new body text, what, meaning)] of the rewrites of one macro body."""
    w = _words(body)
    shape, inner = _shape(w)
    out = []
    if shape == 'do': out.append(('{ ' + _pretty(inner) + ' }', 'plain block for do-while(0)', 'same'))
    if shape == 'block': out.append(('do { ' + _pretty(inner) + ' } while (0)', 'do-while(0) for the block', 'same'))
    st = _stmts(inner) if shape else None
    if st:
        for k in range(len(st) - 1):
            a, b = st[k], st[k + 1]
            if _is_expr(ctx, a) and _is_expr(ctx, b):
                out.append((_wrap(shape, st[:k] + [a + [','] + b] + st[k + 2:]), f'statements {k + 1} and {k + 2} as one comma expression', 'same'))
                out.append((_wrap(shape, st[:k] + [b, a] + st[k + 2:]), f'statements {k + 1} and {k + 2} in the other order', 'type'))
            # x[i] = v; i++;  ->  x[i++] = v;
            if (len(a) >= 6 and a[1] == '[' and a[3] == ']' and a[4] == '=' and re.match(r'[A-Za-z_]\w*$', a[2]) and a[2] not in a[5:]
                    and a[0] != a[2] and b in ([a[2], '++'], ['++', a[2]], [a[2], '+=', '1'])):
                out.append((_wrap(shape, st[:k] + [a[:3] + ['++'] + a[3:]] + st[k + 2:]), f'{a[2]}++ inside the index of statement {k + 1}', 'same'))
        for k, a in enumerate(st):
            cs = _top_commas(a)
            if cs and _is_expr(ctx, a):
                out.append((_wrap(shape, st[:k] + [a[:cs[0]], a[cs[0] + 1:]] + st[k + 1:]), f'comma expression {k + 1} as two statements', 'same'))
            # x[i++] = v;  ->  x[i] = v; i++;
            if (len(a) >= 7 and a[1] == '[' and a[3] == '++' and a[4] == ']' and a[5] == '=' and re.match(r'[A-Za-z_]\w*$', a[2])
                    and a[2] not in a[6:] and a[0] != a[2]):
                out.append((_wrap(shape, st[:k] + [a[:3] + a[4:], [a[2], '++']] + st[k + 1:]), f'{a[2]}++ after statement {k + 1}', 'same'))
        names = set(w)
        for p in params:
            uses = [k for k, x in enumerate(inner) if x == p]
            written = any(inner[k + 1:k + 2] and (inner[k + 1] in permc.ASSIGN or inner[k + 1] in ('++', '--')) or (k and inner[k - 1] in ('++', '--', '&'))
                          for k in uses)
            if uses and not written:               # (a parameter the body writes must stay the argument itself)
                nm = 'pmt_m0'
                while nm in names or nm in ctx.ids: nm = nm[:-1] + str(int(nm[-1]) + 1)
                ty = rng.choice(['int', 'unsigned int', 'unsigned char', 'unsigned short'])
                rest = [nm if x == p else x for x in inner]
                # parentheses the parameter no longer needs go: (p) -> name
                txt = _pretty(' '.join(rest).replace(f'( {nm} )', nm).split(' '))
                out.append((('do { ' if shape == 'do' else '{ ') + f'{ty} {nm} = ({p}); ' + txt + (' } while (0)' if shape == 'do' else ' }'),
                            f'{ty} temporary for parameter {p}', 'type'))
        # the inverse: a first statement `T name = (p);` whose name the rest uses
        f = st[0]
        if len(f) >= 4 and '=' in f and ctx.types.is_type_word(f[0]):
            e = f.index('=')
            val = f[e + 1:]
            if val[:1] == ['('] and val[-1:] == [')']: val = val[1:-1]
            if e >= 2 and len(val) == 1 and val[0] in params and re.match(r'[A-Za-z_]\w*$', f[e - 1]) and f[e - 1] not in params:
                nm = f[e - 1]
                rest = [s2 for s2 in st[1:]]
                if any(nm in s2 for s2 in rest) and not any(len(s2) > 1 and s2[0] == nm and s2[1] in permc.ASSIGN for s2 in rest):
                    sub = [[x2 for x in s2 for x2 in (['(', val[0], ')'] if x == nm else [x])] for s2 in rest]
                    out.append((_wrap(shape, sub), f'parameter {val[0]} used directly, not through {nm}', 'type'))
    for p in params:
        for k, x in enumerate(w):
            if x != p: continue
            if 0 < k < len(w) - 1 and w[k - 1] == '(' and w[k + 1] == ')':
                if k >= 2 and (re.match(r'[A-Za-z_]\w*$', w[k - 2]) and w[k - 2] not in _KEYS): continue       # a call's argument list
                out.append((_pretty(w[:k - 1] + [p] + w[k + 2:]), f'parameter {p} without its parentheses (use {k})', 'type'))
            else:
                out.append((_pretty(w[:k] + ['(', p, ')'] + w[k + 1:]), f'parameter {p} in parentheses (use {k})', 'type'))
    return out


def r_macro_body(ctx, rng, everywhere=False):
    """One rewrite of the body of a function-like macro FUNC invokes."""
    R = _rules()
    ms = macros_used(ctx)
    if not ms: return None
    i, name, params, body, head = rng.choice(ms)
    outs = _body_rewrites(ctx, params, body, rng)
    if not outs: return None
    new, what, meaning = rng.choice(outs)
    if _words(new) == _words(body): return None
    return R.Edit([(i, i + 1, [Tok('pp', head + ' ' + new, ctx.toks[i][2])])], f'macro {name}: {what}', meaning=meaning)


def _invocations(ctx, name):
    return [j for j in ctx.ids.get(name, []) if ctx.toks[j + 1][1] == '(']


def _args(ctx, j):
    """([[token index]] of the arguments of the call at token j, index of its closing parenthesis)."""
    rp = ctx.pair[j + 1]
    args, cur, d = [], [], 0
    for k in range(j + 2, rp):
        x = ctx.toks[k][1]
        if x in ('(', '[', '{'): d += 1
        elif x in (')', ']', '}'): d -= 1
        if x == ',' and d == 0:
            args.append(cur); cur = []
        else: cur.append(k)
    if cur or args: args.append(cur)
    return args, rp


def r_macro_expand(ctx, rng, everywhere=False):
    """One invocation of a macro written out in place, as the preprocessor expands it."""
    R = _rules()
    sites = [(j, m) for m in macros_used(ctx) for j in _invocations(ctx, m[1])]
    if not sites: return None
    j, (i, name, params, body, head) = rng.choice(sites)
    args, rp = _args(ctx, j)
    if len(args) != len(params) or any(not a for a in args): return None
    sub = {p: [ctx.toks[k] for k in a] for p, a in zip(params, args)}
    new = []
    for t in permc.tokenize(body)[0]:
        if t[0] == 'id' and t[1] in sub:
            a = sub[t[1]]
            new += [Tok(a[0][0], a[0][1], t[2])] + a[1:]
        else: new.append(t)
    if not new: return None
    new[0] = Tok(new[0][0], new[0][1], ctx.toks[j][2])
    return R.Edit([(j, rp + 1, new)], f'expand {name} @{_line(ctx, j)}', meaning='same')


def _match(pat, words, i, end, bind):
    """Where the pattern (('lit', word) and ('par', name) items) ends when matched at words[i],
    with the parameters' captures in bind, or None. A capture is a balanced run with no
    top-level comma or semicolon."""
    if not pat: return i
    kind, v = pat[0]
    if kind == 'lit':
        return _match(pat[1:], words, i + 1, end, bind) if i < end and words[i] == v else None
    if v in bind:
        n = len(bind[v])
        return _match(pat[1:], words, i + n, end, bind) if words[i:i + n] == bind[v] else None
    d = 0
    for k in range(i, min(end, i + 60)):
        x = words[k]
        if x in ('(', '['): d += 1
        elif x in (')', ']'):
            d -= 1
            if d < 0: break
        elif x in (';', '{', '}') or (x == ',' and d == 0): break
        if d == 0:
            bind[v] = words[i:k + 1]
            j = _match(pat[1:], words, k + 1, end, bind)
            if j is not None: return j
            del bind[v]
    return None


def r_macro_fold(ctx, rng, everywhere=False):
    """A run of tokens in FUNC that is exactly a macro's expansion becomes its invocation (at
    one place, or with everywhere at every place of that macro)."""
    R = _rules()
    words = getattr(ctx, '_pm_words', None)
    if words is None: words = ctx._pm_words = [t[1] for t in ctx.toks]
    found = []
    for i, name, params, body, head in macros_used(ctx, invoked=False):
        if i > ctx.start: continue
        pat = [('par', t[1]) if t[0] == 'id' and t[1] in params else ('lit', t[1]) for t in permc.tokenize(body)[0]]
        if len(pat) < 3 or pat[0][0] != 'lit' or not all(any(p == ('par', q) for p in pat) for q in params): continue
        k = ctx.lb + 1
        while k < ctx.rb:
            if words[k] == pat[0][1]:
                bind = {}
                e = _match(pat, words, k, ctx.rb, bind)
                if e is not None:
                    found.append((k, e, name, [bind[p] for p in params])); k = e; continue
            k += 1
    if not found: return None
    one = rng.choice(found)
    chosen = [f for f in found if f[2] == one[2]] if everywhere else [one]
    spans = []
    for a, b, name, args in chosen:
        text = name + '(' + ', '.join(' '.join(x) for x in args) + ')'
        spans.append((a, b, permc.new(text, ctx.toks[a][2])))
    return R.Edit(spans, f'fold {len(chosen)} expansion{"s" if len(chosen) > 1 else ""} of {one[2]} @{_line(ctx, one[0])}', meaning='same')


def file_decls(ctx):
    """[(first token, token after the ;, names, is an extern declaration)] of the file-scope
    declarations before FUNC that declare a global FUNC's body names (no functions, no typedefs)."""
    d = getattr(ctx, '_pm_decls', None)
    if d is None:
        d = []
        t = ctx.toks
        depth, start = 0, 0
        for i in range(ctx.start):
            x = t[i]
            if x[0] == 'pp':
                if depth == 0: start = i + 1
                continue
            if x[1] in ('{', '(', '['): depth += 1
            elif x[1] in ('}', ')', ']'):
                depth -= 1
                if depth == 0 and x[1] == '}' and t[i + 1][1] != ';' and not any(y[1] == '=' for y in t[start:i]): start = i + 1
            elif x[1] == ';' and depth == 0:
                ws = [y[1] for y in t[start:i]]
                if ws and ws[0] != 'typedef' and '(' not in ws:
                    names = [n for n in ws if n in ctx.types.globals and n in ctx.ids]
                    if names: d.append((start, i + 1, names, 'extern' in ws))
                start = i + 1
        ctx._pm_decls = d
    return d


def r_file_order(ctx, rng, everywhere=False):
    """Two file-scope declarations of globals FUNC names change places (neighbours in the list
    of such declarations two times in three, else any two)."""
    R = _rules()
    ds = file_decls(ctx)
    if len(ds) < 2: return None
    if rng.random() < 0.67:
        k = rng.randrange(len(ds) - 1); a, b = ds[k], ds[k + 1]
    else:
        a, b = sorted(rng.sample(ds, 2))
    t = ctx.toks
    ta, tb = list(t[a[0]:a[1]]), list(t[b[0]:b[1]])
    if [x[1] for x in ta] == [x[1] for x in tb]: return None
    na = [Tok(tb[0][0], tb[0][1], ta[0][2])] + tb[1:]           # each keeps the place's own leading comments
    nb = [Tok(ta[0][0], ta[0][1], tb[0][2])] + ta[1:]
    both_extern = a[3] and b[3]
    return R.Edit([(a[0], a[1], na), (b[0], b[1], nb)], f'file order: {a[2][0]} and {b[2][0]} change places'
                  + ('' if both_extern else ' (a definition: object layout)'), meaning='same' if both_extern else 'type')


def _rules():
    import permrules
    return permrules


def register(ns):
    """Add these rules to permrules' tables (called at the end of permrules.py with its globals)."""
    Rule = ns['Rule']
    for rid, kinds, weight, fn, every in (('macro_body', 'D28', 3, r_macro_body, False), ('macro_expand', 'D28', 2, r_macro_expand, False),
                                          ('macro_fold', 'D28', 1, r_macro_fold, True), ('file_order', 'A8', 2, r_file_order, False)):
        if rid not in ns['BY_ID']:
            r = Rule(rid, kinds, weight, fn, every)
            ns['RULES'].append(r); ns['BY_ID'][rid] = r
