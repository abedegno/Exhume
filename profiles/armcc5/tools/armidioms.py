"""Write variants of one function that try plain vendor idioms, for a batch scorer to rank.

    python3 armidioms.py literals SRC FUNC OUTDIR [--types uchar,int,ushort]
    python3 armidioms.py zero SRC FUNC OUTDIR
    python3 armidioms.py MODE SRC FUNC OUTDIR      MODE: types, declorder, swap, operands, compound, dead

literals  for each small integer literal in a statement (right of '=' or a call argument, not an
          index or a shift count), one variant per local type and place: the literal is held in
          a new local assigned just before the statement, or at the top of the body. armcc keeps
          a narrow local that holds a constant as a value of its own, so the constant is set where
          the local is assigned and shared from there (compiler.md, "A constant held in a local").
zero      for each struct or union local whose word the function first loads from elsewhere
          (`x.word = expr;`), a variant with a dead `x.word = 0;` before it. The dead store makes
          armcc create the function's shared zero at that point.

types     each scalar local's declared type changed to each of uchar, ushort, int, uint (and the
          signed narrow types for a signed local).
declorder each pair of neighbouring declarations swapped, and each declaration moved to the top.
swap      each pair of neighbouring simple statements (assignments and calls, same block) swapped.
operands  each commutative binary operator (==, !=, +, *, &, |, ^) with its two operands swapped,
          and each relational comparison written the other way round (a < b as b > a).
compound  each `x = x OP e;` written `x OP= e;` and back, and `x++` as `x += 1`.
dead      before each assignment to a scalar local, a dead `x = 0;` (zero above for unions).

armfixdb.py names the edit kinds that changed code like a function's remaining difference; each
kind has a mode here (literal_local: literals, dead_store: zero and dead, decl_type: types,
decl_order: declorder, stmt_order: swap, operand_order: operands, compound_assign: compound).

Each variant is a whole copy of SRC named after what it changes. Score the directory with the
project's batch scorer (a parallel `armvariant.py` run, or a project's parvar script) and keep
the best; rename the new local before merging.
"""
import os, re, sys


def body_span(src, fn):
    m = re.search(r'^[A-Za-z_][\w \*]*\b' + re.escape(fn) + r'\s*\([^)]*\)\s*\{\n', src, re.M)
    if not m: raise SystemExit(f'{fn}: no definition found')
    k = m.end(); d = 1; i = k
    while d:
        c = src[i]; d += (c == '{') - (c == '}'); i += 1
    return k, i - 1


DECL = re.compile(r'^\s+(unsigned |signed |const |volatile )*[A-Za-z_]\w*[\s\*]+[A-Za-z_]\w*(\[[^\]]*\])?(\s*=[^;]*)?(,\s*\**[A-Za-z_]\w*(\s*=[^;]*)?)*;\s*$')
SKIP = re.compile(r'^\s*(case\b|default\b|#|/\*|\*)|\bfor\s*\(|\bwhile\s*\(|\bswitch\s*\(|unsigned int z;')
LIT = re.compile(r'(?<![\w.\[])(0x[0-9a-fA-F]+|\d+)(?![\w.\]])')


def decl_end(lines):
    end = 0
    for n, l in enumerate(lines):
        if not l.strip() or (DECL.match(l) and '(' not in l.split('=')[0]):
            end = n + 1
        else:
            break
    return end


def literals(src, fn, out, types):
    k, e = body_span(src, fn)
    lines = src[k:e].split('\n')
    top = decl_end(lines)
    count = 0
    for n in range(top, len(lines)):
        l = lines[n]
        if SKIP.search(l) or ';' not in l: continue
        for mm in LIT.finditer(l):
            s, f = mm.span()
            pre = l[:s].rstrip()
            if ('=' not in pre and '(' not in pre) or pre.endswith(('[', '<<', '>>')): continue
            val = int(mm.group(1), 0)
            if val > 0xFFFF: continue
            for ty in types:
                if val > 0xFF and ty in ('uchar', 'char', 'unsigned char'): continue
                for where in ('here', 'top'):
                    nl = lines[:]
                    nl[n] = l[:s] + 'idiom_k' + l[f:]
                    if where == 'here':
                        nl.insert(n, re.match(r'^\s*', l).group(0) + f'idiom_k = {mm.group(1)};')
                    else:
                        nl.insert(top, f'    idiom_k = {mm.group(1)};')
                    nl.insert(0, f'    {ty} idiom_k;')
                    name = f'lit{count:04d}_{ty.replace(" ", "_")}_{where}.c'
                    with open(os.path.join(out, name), 'w') as fh:
                        fh.write(src[:k] + '\n'.join(nl) + src[e:])
            count += 1
    return count


def zero(src, fn, out):
    k, e = body_span(src, fn)
    lines = src[k:e].split('\n')
    count = 0
    for n, l in enumerate(lines):
        m = re.match(r'^(\s*)([A-Za-z_]\w*)\.(\w+) = (?!0;)[^;]+;\s*$', l)
        if not m: continue
        nl = lines[:]
        nl.insert(n, f'{m.group(1)}{m.group(2)}.{m.group(3)} = 0;')
        with open(os.path.join(out, f'zero{count:03d}_{m.group(2)}.c'), 'w') as fh:
            fh.write(src[:k] + '\n'.join(nl) + src[e:])
        count += 1
    return count


SCALAR = ('unsigned char', 'unsigned short', 'unsigned int', 'signed char', 'short', 'char', 'int',
          'uchar', 'ushort', 'uint', 'u8', 'u16', 'u32', 's8', 's16', 's32', 'long', 'unsigned long')
DECL1 = re.compile(r'^(\s+)((?:unsigned |signed )?\w+)(\s+)([A-Za-z_]\w*)(\s*(?:=[^;,]*)?);\s*$')


def write(out, name, src, k, e, lines):
    with open(os.path.join(out, name), 'w') as fh:
        fh.write(src[:k] + '\n'.join(lines) + src[e:])


def scalar_locals(lines, top):
    out = {}
    for n in range(top):
        m = DECL1.match(lines[n])
        if m and m.group(2) in SCALAR: out[m.group(4)] = (n, m.group(2))
    return out


def types(src, fn, out):
    k, e = body_span(src, fn); lines = src[k:e].split('\n'); top = decl_end(lines); c = 0
    for v, (n, ty) in scalar_locals(lines, top).items():
        alts = ['uchar', 'ushort', 'int', 'uint']
        if ty in ('short', 'signed char', 's8', 's16', 'char'): alts += ['short', 'signed char']
        for a in alts:
            if a == ty: continue
            nl = lines[:]; m = DECL1.match(nl[n])
            nl[n] = f'{m.group(1)}{a}{m.group(3)}{m.group(4)}{m.group(5)};'
            write(out, f'type{c:03d}_{v}_{a.replace(" ", "_")}.c', src, k, e, nl); c += 1
    return c


def declorder(src, fn, out):
    k, e = body_span(src, fn); lines = src[k:e].split('\n'); top = decl_end(lines); c = 0
    ds = [n for n in range(top) if lines[n].strip()]
    for a, b in zip(ds, ds[1:]):
        nl = lines[:]; nl[a], nl[b] = nl[b], nl[a]
        write(out, f'decl{c:03d}_swap.c', src, k, e, nl); c += 1
    for n in ds[1:]:
        nl = lines[:]; x = nl.pop(n); nl.insert(ds[0], x)
        write(out, f'decl{c:03d}_top.c', src, k, e, nl); c += 1
    return c


SIMPLE = re.compile(r'^(\s+)[^\s{}].*;\s*$')
CTRL = re.compile(r'^\s*(if|else|for|while|do|switch|case|default|return|break|continue|goto)\b|unsigned int z;')


def swap(src, fn, out):
    k, e = body_span(src, fn); lines = src[k:e].split('\n'); top = decl_end(lines); c = 0
    for n in range(top, len(lines) - 1):
        a, b = lines[n], lines[n + 1]
        if not (SIMPLE.match(a) and SIMPLE.match(b)) or CTRL.match(a) or CTRL.match(b): continue
        if re.match(r'^\s*', a).group(0) != re.match(r'^\s*', b).group(0): continue
        nl = lines[:]; nl[n], nl[n + 1] = b, a
        write(out, f'swap{c:04d}.c', src, k, e, nl); c += 1
    return c


OPND = r'(?:[A-Za-z_]\w*(?:\[[^\]\[]*\]|\.\w+|->\w+)*|0x[0-9a-fA-F]+|\d+)'
COMM = re.compile(r'(?<![\w\]\)])(' + OPND + r')\s*(==|!=|\+|\*|&|\||\^)\s*(' + OPND + r')(?![\w\[(])')
REL = {'<': '>', '>': '<', '<=': '>=', '>=': '<='}
RELX = re.compile(r'(?<![\w\]\)])(' + OPND + r')\s*(<=|>=|<|>)\s*(' + OPND + r')(?![\w\[(])')


def operands(src, fn, out):
    k, e = body_span(src, fn); lines = src[k:e].split('\n'); top = decl_end(lines); c = 0
    for n in range(top, len(lines)):
        l = lines[n]
        if l.strip().startswith(('/*', '*', '#')) or 'unsigned int z;' in l: continue
        for rx, flip in ((COMM, lambda o: o), (RELX, lambda o: REL[o])):
            for m in rx.finditer(l):
                if m.group(2) in ('&', '*') and l[:m.start()].rstrip().endswith(('(', '=', ',')) and False: continue
                if m.group(2) in ('<', '>') and (l[m.end(2):m.end(2) + 1] in '<>' or l[m.start(2) - 1:m.start(2)] in '<>'): continue
                nl = lines[:]
                nl[n] = l[:m.start()] + f'{m.group(3)} {flip(m.group(2))} {m.group(1)}' + l[m.end():]
                write(out, f'opnd{c:04d}.c', src, k, e, nl); c += 1
    return c


def compound(src, fn, out):
    k, e = body_span(src, fn); lines = src[k:e].split('\n'); top = decl_end(lines); c = 0
    for n in range(top, len(lines)):
        l = lines[n]
        m = re.match(r'^(\s*)([\w\[\]\.>-]+) = \2 ([-+*/%&|^]|<<|>>) (.+);\s*$', l)
        if m: new = f'{m.group(1)}{m.group(2)} {m.group(3)}= {m.group(4)};'
        else:
            m = re.match(r'^(\s*)([\w\[\]\.>-]+) ([-+*/%&|^]|<<|>>)= (.+);\s*$', l)
            if m: new = f'{m.group(1)}{m.group(2)} = {m.group(2)} {m.group(3)} {m.group(4)};'
            else:
                m = re.match(r'^(\s*)([\w\[\]\.>-]+)(\+\+|--);\s*$', l)
                if not m: continue
                new = f'{m.group(1)}{m.group(2)} {m.group(3)[0]}= 1;'
        nl = lines[:]; nl[n] = new
        write(out, f'comp{c:04d}.c', src, k, e, nl); c += 1
    return c


def dead(src, fn, out):
    k, e = body_span(src, fn); lines = src[k:e].split('\n'); top = decl_end(lines); c = 0
    locs = scalar_locals(lines, top)
    for n in range(top, len(lines)):
        m = re.match(r'^(\s*)([A-Za-z_]\w*) = (?!0;)[^;]+;\s*$', lines[n])
        if not m or m.group(2) not in locs: continue
        nl = lines[:]; nl.insert(n, f'{m.group(1)}{m.group(2)} = 0;')
        write(out, f'dead{c:03d}_{m.group(2)}.c', src, k, e, nl); c += 1
    return c


MODES = dict(types=types, declorder=declorder, swap=swap, operands=operands, compound=compound, dead=dead)


def main(argv):
    if len(argv) < 4 or argv[0] not in ('literals', 'zero') + tuple(MODES):
        print(__doc__); return 2
    mode, path, fn, out = argv[:4]
    src = open(path).read()
    os.makedirs(out, exist_ok=True)
    if mode == 'literals':
        types = argv[argv.index('--types') + 1].split(',') if '--types' in argv else ['uchar', 'int']
        n = literals(src, fn, out, types)
    elif mode == 'zero':
        n = zero(src, fn, out)
    else:
        n = MODES[mode](src, fn, out)
    print(f'{n} sites, {len(os.listdir(out))} variants in {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
