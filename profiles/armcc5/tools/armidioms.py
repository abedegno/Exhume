"""Write variants of one function that try plain vendor idioms, for a batch scorer to rank.

    python3 armidioms.py literals SRC FUNC OUTDIR [--types uchar,int,ushort]
    python3 armidioms.py zero SRC FUNC OUTDIR

literals  for each small integer literal in a statement (right of '=' or a call argument, not an
          index or a shift count), one variant per local type and place: the literal is held in
          a new local assigned just before the statement, or at the top of the body. armcc keeps
          a narrow local that holds a constant as a value of its own, so the constant is set where
          the local is assigned and shared from there (compiler.md, "A constant held in a local").
zero      for each struct or union local whose word the function first loads from elsewhere
          (`x.word = expr;`), a variant with a dead `x.word = 0;` before it. The dead store makes
          armcc create the function's shared zero at that point.

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


def main(argv):
    if len(argv) < 4 or argv[0] not in ('literals', 'zero'):
        print(__doc__); return 2
    mode, path, fn, out = argv[:4]
    src = open(path).read()
    os.makedirs(out, exist_ok=True)
    if mode == 'literals':
        types = argv[argv.index('--types') + 1].split(',') if '--types' in argv else ['uchar', 'int']
        n = literals(src, fn, out, types)
    else:
        n = zero(src, fn, out)
    print(f'{n} sites, {len(os.listdir(out))} variants in {out}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
