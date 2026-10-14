"""Group matched sources that define the same function or global: parts of one original file.

    python3 armgroups.py [--config PATH] [--md]

Matching by units of consecutive addresses copies some definitions into several files: a callee
a caller's registers show was defined earlier in the caller's file (compiler.md, "What a file's
contents decide") goes into the caller's unit as well as its own, and so does a global a
function reaches from another global's address. Each such shared definition is evidence from
the bytes that the files were one source file. This prints the groups (union-find over shared
definitions, sources under [project] exclude left out) with the names that join them; --md as
Markdown table rows. Before the whole-image gate every function and global needs one owner, so
each group becomes one file: keep one copy of each shared definition, give the file one
/* opts: */ line that suits every part, and match the joined file again (every function's bytes
depend on what else its file defines)."""
import collections, glob, os, re, sys

CONTROL = {'if', 'for', 'while', 'switch', 'return', 'sizeof'}


def strip_comments(s):
    return re.sub(r'/\*.*?\*/|//[^\n]*', ' ', s, flags=re.S)


def defs(text):
    """(functions defined with a body, globals defined at file scope) in C text."""
    c = strip_comments(text)
    funcs = set()
    for m in re.finditer(r'^[A-Za-z_][\w \*]*?\b(\w+)\s*\([^;{)]*\)\s*\{', c, re.M):
        if m.group(1) not in CONTROL and not re.match(r'\s*static\b', m.group(0)): funcs.add(m.group(1))
    data, depth, stmt, body = set(), 0, '', False
    for ch in c:
        if ch == '{':
            if depth == 0: body = bool(re.search(r'\)\s*(?:\w+\s*)*$', stmt))     # a function's body
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0 and body: stmt = ''; body = False; continue
        if depth == 0 and ch == ';':
            st = ' '.join(stmt.split())
            head = re.sub(r'=[^,]*', '', st)          # initialisers dropped (braces never got here)
            if not re.match(r'(extern|typedef|static|#)', st) and '(' not in head \
                    and not re.fullmatch(r'(struct|union|enum)\s+\w+', head.strip()):
                for part in head.split(','):
                    m = re.search(r'\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*$', part.strip())
                    if m: data.add(m.group(1))
            stmt = ''
        elif depth == 0 and ch != '}':
            stmt += ch
        elif depth == 0 and ch == '}':
            stmt += ' '
        if depth == 0 and ch == '\n' and stmt.lstrip().startswith('#'): stmt = ''
    return funcs, data


def groups(texts):
    """[(files, shared names)] for every group of two or more files; texts: {file: C text}."""
    owners = collections.defaultdict(set)
    for f, t in texts.items():
        fn, dt = defs(t)
        for n in fn | dt: owners[n].add(f)
    parent = {f: f for f in texts}

    def find(x):
        while parent[x] != x: parent[x] = parent[parent[x]]; x = parent[x]
        return x
    links = collections.defaultdict(set)
    for n, fs in owners.items():
        fs = sorted(fs)
        for a in fs[1:]: parent[find(a)] = find(fs[0])
        if len(fs) > 1:
            for a in fs: links[a].add(n)
    out = collections.defaultdict(list)
    for f in sorted(texts): out[find(f)].append(f)
    multi = sorted((g for g in out.values() if len(g) > 1), key=lambda g: (-len(g), g))
    return [(g, sorted(set().union(*(links[f] for f in g)))) for g in multi]


def main(argv):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'tools'))
    import config
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    exclude = [os.path.realpath(x) for x in (getattr(cfg, 'exclude', None) or [])]
    texts = {}
    for f in sorted(glob.glob(os.path.join(cfg.src, '**', '*.c'), recursive=True)):
        if any(os.path.realpath(f).startswith(x + os.sep) for x in exclude): continue
        with open(f, encoding='latin1') as fh: texts[os.path.relpath(f, cfg.root)] = fh.read()
    gs = groups(texts)
    for i, (g, shared) in enumerate(gs, 1):
        if '--md' in a:
            print(f'| {i} | ' + '<br>'.join(f'`{x}`' for x in g) + ' | ' + ', '.join(f'`{x}`' for x in shared) + ' |')
        else:
            print(f'group {i}: ' + ' '.join(g)); print('   shared: ' + ' '.join(shared))
    print(f'{len(gs)} groups of two or more files; {len(texts) - sum(len(g) for g, _ in gs)} files stand alone',
          file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
