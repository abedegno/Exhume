"""Collect the source edits that changed matched functions in past projects, sorted by kind of edit.

    python3 armfixdb.py mine REPO [REPO ...] --out DB.jsonl [--paths GLOB,...]
    python3 armfixdb.py stats DB.jsonl [--matches]
    python3 armfixdb.py show DB.jsonl KIND [N]
    python3 armfixdb.py label DB.jsonl OUT.jsonl --homes DIR,... [--includes DIR,...] [--jobs N] [--max-size 4]
    python3 armfixdb.py matrix OUT.jsonl
    python3 armfixdb.py suggest OUT.jsonl DIFF.txt [FUNC]

mine   walks every commit on every branch of each repository (each commit once), and for each C
       function whose tokens changed writes one record: repository, commit, message, file,
       function, the edit kinds found, the score change the message gives ("(48 to 46)") and
       whether the message says the function matches. Layout and comment changes are not edits
       (tokens are compared); a consistent renaming is recorded as kind 'rename' only.
stats  counts each edit kind, over all records or (--matches) only those whose message says the
       function matched or names a lower score.
show   prints the before and after of N records of one kind.
label  compiles the file of each small edit (at most --max-size statements) at the parent commit
       and at the commit, each in its own extracted copy of the project (the directory holding
       exhume.toml), with that project's options (the file's `/* opts: */` line, else c_opts)
       and compiler home (resolved in the current checkout, else the directory of the same name
       among --homes; every header directory of the project and of --includes, e.g. a vendor
       library kept outside the repository, is passed as -I), and records what the edit did to the function's instructions: 'none',
       'regs' (same instructions, other registers), 'order' (same instructions, other order),
       'const' (only constant loads and moves changed), 'insns' (other changes, same count) or
       'size' (the count changed); with the count of differing instructions (ndiff) and the
       count when registers are masked (nreg: ndiff - nreg instructions differ only in registers). Each worker uses
       its own Wine prefix under the output's directory, removed at the end.
suggest reads a scorer's instruction diff of one function (fnscore.py's FUNC output: lines
       `-  N insn` for the original and `+  N insn` for the build, grouped by instruction index),
       sorts what is left into register-only lines, constant loads and moves, order and other
       instructions, and ranks the edit kinds by how often each changed that kind of thing in
       the labelled records, with the armidioms.py mode that writes variants of that kind.
matrix prints, for each effect, the edit kinds that produced it and how often, from labelled
       records (rename-only and readability edits fall out as 'none').

The kinds are what a person changes to steer armcc (compiler.md has the evidence behind each):
probe_add, probe_remove (unread comparisons), decl_order, decl_type, local_added, local_removed,
dead_store, literal_local, temp_local, stmt_order, operand_order, cast, compound_assign,
index_pointer, control_form, condition, ternary, call_args, other.
"""
import collections, difflib, fnmatch, json, os, re, shutil, subprocess, sys, tempfile

TOK = re.compile(r'''
    (?P<ws>\s+) | (?P<com>//[^\n]*|/\*.*?\*/) | (?P<pp>^\s*\#[^\n]*(?:\\\n[^\n]*)*) |
    (?P<str>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*') |
    (?P<num>0[xX][0-9a-fA-F]+[uUlL]*|\d+\.\d*(?:[eE][+-]?\d+)?[fF]?|\d+[uUlLfF]*) |
    (?P<id>[A-Za-z_]\w*) |
    (?P<op><<=|>>=|->|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||[-+*/%&|^!~<>=]=?|[()\[\]{};,.?:])
''', re.X | re.S | re.M)
KEYWORDS = set('''if else for while do switch case default return break continue goto sizeof
    struct union enum typedef static extern const volatile register void char short int long
    float double signed unsigned'''.split())
TYPES = set('''void char short int long float double signed unsigned uchar ushort uint ulong u8 u16 u32
    s8 s16 s32 uint8_t uint16_t uint32_t int8_t int16_t int32_t bool BOOL'''.split())


def tokens(text):
    out = []
    for m in TOK.finditer(text):
        k = m.lastgroup
        if k in ('ws', 'com', 'pp'): continue
        out.append(m.group())
    return out


DEF = re.compile(r'^[A-Za-z_][\w \t\*]*?\b([A-Za-z_]\w*)\s*\(([^;{}()]*(?:\([^()]*\)[^;{}()]*)*)\)\s*\{', re.M)


def functions(src):
    """Map name -> token list of the body, for top-level definitions."""
    out = {}
    clean = re.sub(r'/\*.*?\*/', lambda m: re.sub(r'[^\n]', ' ', m.group()), src, flags=re.S)
    clean = re.sub(r'//[^\n]*', '', clean)
    for m in DEF.finditer(clean):
        name = m.group(1)
        if name in KEYWORDS: continue
        i = m.end(); d = 1
        while i < len(clean) and d:
            c = clean[i]
            if c == '"' or c == "'":
                j = i + 1
                while j < len(clean) and clean[j] != c:
                    j += 2 if clean[j] == '\\' else 1
                i = j
            elif c == '{': d += 1
            elif c == '}': d -= 1
            i += 1
        out[name] = tokens(clean[m.end():i - 1])
    return out


def statements(toks):
    """Split a body into statements: runs ending at ';', '{' or '}' (for-headers kept whole)."""
    out, cur, par = [], [], 0
    for t in toks:
        cur.append(t)
        if t == '(': par += 1
        elif t == ')': par -= 1
        elif par == 0 and t in (';', '{', '}'):
            out.append(tuple(cur)); cur = []
    if cur: out.append(tuple(cur))
    return out


def is_probe(s):
    return 'z' in s and ('unsigned' in s or s[:2] == ('z', '='))


def strip_probes(st):
    """Drop `{ unsigned int z; z = (...); }` blocks (the statements and their braces)."""
    out, i = [], 0
    while i < len(st):
        if st[i] == ('{',) and i + 3 < len(st) and is_probe(st[i + 1]) and st[i + 3] == ('}',):
            i += 4; continue
        if is_probe(st[i]) and ('unsigned' in st[i]):
            i += 1; continue
        out.append(st[i]); i += 1
    return out


def is_decl(s):
    if not s or s[-1] != ';': return False
    k = 0
    while k < len(s) and s[k] in ('static', 'const', 'volatile', 'register', 'struct', 'union', 'enum'):
        k += 1 + (s[k] in ('struct', 'union', 'enum'))
    if k >= len(s): return False
    if s[k] in TYPES or (k + 1 < len(s) and re.match(r'[A-Za-z_]', s[k + 1] or '') and s[k + 1] not in KEYWORDS
                         and re.match(r'[A-Z]|\w+_t$|\w*[A-Z]\w*$', s[k])):
        return s[k] not in KEYWORDS or s[k] in TYPES
    return False


def decl_names(s):
    """Names declared by a declaration statement and the type tokens before the first name."""
    names, typ = [], []
    i = 0
    while i < len(s) and (s[i] in TYPES or s[i] in ('static', 'const', 'volatile', 'register', 'struct', 'union', 'enum')
                          or (not names and i + 1 < len(s) and re.match(r'[A-Za-z_]', s[i + 1]))):
        typ.append(s[i]); i += 1
        if i < len(s) and re.match(r'[A-Za-z_]', s[i]) and (i + 1 == len(s) or s[i + 1] in (';', ',', '=', '[')):
            break
    depth = 0
    for j in range(i, len(s)):
        t = s[j]
        if t in '([{': depth += 1
        elif t in ')]}': depth -= 1
        elif depth == 0 and re.match(r'[A-Za-z_]\w*$', t) and (j == i or s[j - 1] in (',', '*')) and t not in KEYWORDS:
            names.append(t)
    return tuple(typ), names


def is_literal(t):
    return re.match(r'^(0[xX][0-9a-fA-F]+|\d+)[uUlL]*$', t) is not None


def assign_lhs(s):
    depth = 0
    for j, t in enumerate(s):
        if t in '([': depth += 1
        elif t in ')]': depth -= 1
        elif depth == 0 and t == '=': return s[:j], s[j + 1:-1]
    return None, None


def rename_map(a, b):
    if len(a) != len(b): return None
    m = {}
    for x, y in zip(a, b):
        if x == y: continue
        if not (re.match(r'[A-Za-z_]\w*$', x) and re.match(r'[A-Za-z_]\w*$', y)): return None
        if x in KEYWORDS or y in KEYWORDS: return None
        if m.setdefault(x, y) != y: return None
    return m


def edit_size(before, after):
    """Statements removed plus statements added, probes left out."""
    sb, sa = strip_probes(statements(before)), strip_probes(statements(after))
    sm = difflib.SequenceMatcher(None, sb, sa, autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != 'equal')


def kinds(before, after):
    k = set()
    sb, sa = statements(before), statements(after)
    pb = sum(1 for s in sb if is_probe(s) and 'unsigned' in s)
    pa = sum(1 for s in sa if is_probe(s) and 'unsigned' in s)
    if pa > pb: k.add('probe_add')
    if pa < pb: k.add('probe_remove')
    sb, sa = strip_probes(sb), strip_probes(sa)
    if sb == sa: return k
    fb = [t for s in sb for t in s]; fa = [t for s in sa for t in s]
    rm = rename_map(fb, fa)
    if rm is not None:
        k.add('rename'); return k
    db = [s for s in sb if is_decl(s)]; da = [s for s in sa if is_decl(s)]
    cb = [s for s in sb if not is_decl(s)]; ca = [s for s in sa if not is_decl(s)]
    tb = {n: decl_names(s)[0] for s in db for n in decl_names(s)[1]}
    ta = {n: decl_names(s)[0] for s in da for n in decl_names(s)[1]}
    nb = [n for s in db for n in decl_names(s)[1]]; na = [n for s in da for n in decl_names(s)[1]]
    if set(na) - set(nb): k.add('local_added')
    if set(nb) - set(na): k.add('local_removed')
    if any(n in tb and tb[n] != ta[n] for n in ta): k.add('decl_type')
    common = [n for n in nb if n in ta]
    if common != [n for n in na if n in tb] and sorted(common) == sorted(n for n in na if n in tb): k.add('decl_order')
    if db != da and collections.Counter(db) == collections.Counter(da): k.add('decl_order')
    if cb != ca and collections.Counter(cb) == collections.Counter(ca): k.add('stmt_order')
    new_locals = set(na) - set(nb)
    sm = difflib.SequenceMatcher(None, cb, ca, autojunk=False)
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal': continue
        old, new = cb[i1:i2], ca[j1:j2]
        for idx, s in enumerate(new):
            lhs, rhs = assign_lhs(s)
            if lhs is None: continue
            rest = ca[j1 + idx + 1:]
            # dead store: a new assignment overwritten before the target is read
            for r in rest:
                l2, r2 = assign_lhs(r)
                if l2 == lhs and not any(t == lhs[0] for t in r2):
                    k.add('dead_store'); break
                if lhs[0] in r: break
            if len(lhs) == 1 and lhs[0] in new_locals:
                k.add('literal_local' if len(rhs) == 1 and is_literal(rhs[0]) else 'temp_local')
        if len(old) == len(new):
            for o, n in zip(old, new):
                if o == n: continue
                if collections.Counter(o) == collections.Counter(n):
                    k.add('operand_order'); continue
                oo = [t for t in o if t not in TYPES and t not in '()']
                nn = [t for t in n if t not in TYPES and t not in '()']
                if oo == nn: k.add('cast'); continue
                so, sn = ' '.join(o), ' '.join(n)
                if re.search(r'[-+*/%&|^]=|\+\+|--', so) != re.search(r'[-+*/%&|^]=|\+\+|--', sn): k.add('compound_assign')
                if (('[' in o) != ('[' in n)) or (o.count('&') != n.count('&') and ('[' in o or '[' in n)): k.add('index_pointer')
                if o[0] in ('if', 'while', 'for') and o[0] == n[0]:
                    cmp = lambda s: [x for x in s if x in ('<', '<=', '>', '>=', '==', '!=', '!', '&&', '||')]
                    if cmp(o) != cmp(n) or collections.Counter(o) == collections.Counter(n): k.add('condition')
                if ('?' in o) != ('?' in n): k.add('ternary')
                if o[0] == n[0] and '(' in o and o[1:2] == ('(',) and o[0] not in KEYWORDS: k.add('call_args')
        kw = lambda ss: collections.Counter(t for s in ss for t in s if t in ('for', 'while', 'do', 'switch', 'case', 'else', 'goto', 'break', 'return'))
        if kw(old) != kw(new): k.add('control_form')
        if sum(s.count('?') for s in old) != sum(s.count('?') for s in new): k.add('ternary')
    if not k - {'probe_add', 'probe_remove'}: k.add('other')
    return k


SCORE = re.compile(r'\((\d+)\s+to\s+(\d+)\)')


def git(repo, *a):
    return subprocess.run(['git', '-C', repo] + list(a), capture_output=True, text=True, errors='replace').stdout


def mine(repos, out, globs):
    seen = set(); n = 0
    with open(out, 'w') as fh:
        for repo in repos:
            log = git(repo, 'log', '--all', '--no-merges', '--format=%H%x00%P%x00%s', '--', '*.c', '*.inc')
            for line in log.splitlines():
                h, parents, msg = line.split('\0', 2)
                if h in seen or not parents: continue
                seen.add(h)
                p = parents.split()[0]
                files = git(repo, 'diff', '--name-only', p, h, '--', '*.c', '*.inc').split()
                if globs: files = [f for f in files if any(fnmatch.fnmatch(f, g) for g in globs)]
                recs = []
                for f in files:
                    a = git(repo, 'show', f'{p}:{f}'); b = git(repo, 'show', f'{h}:{f}')
                    if not a or not b: continue
                    fa, fb = functions(a), functions(b)
                    for name in fa.keys() & fb.keys():
                        if fa[name] == fb[name]: continue
                        kk = kinds(fa[name], fb[name])
                        if not kk: continue
                        sc = SCORE.search(msg)
                        rec = dict(repo=repo, commit=h[:10], msg=msg, file=f, func=name, kinds=sorted(kk), size=edit_size(fa[name], fb[name]),
                                   nfuncs=0,
                                   score=[int(sc.group(1)), int(sc.group(2))] if sc else None,
                                   named=name in msg, matches=bool(re.search(r'\bmatch(es|ed)?\b|exact', msg, re.I)),
                                   before=' '.join(fa[name]), after=' '.join(fb[name]))
                        recs.append(rec)
                for rec in recs:
                    rec['nfuncs'] = len(recs)
                    fh.write(json.dumps(rec) + '\n'); n += 1
    print(f'{n} function edits from {len(seen)} commits -> {out}')


def load(db):
    return [json.loads(l) for l in open(db)]


def useful(r):
    """A small edit (at most 4 statements) in a commit that says it matched or names a lower
    score, and either names this function or changed at most 3 functions."""
    if r['kinds'] == ['rename'] or r.get('size', 99) > 4: return False
    if not (r['named'] or r.get('nfuncs', 99) <= 3): return False
    return r['matches'] or bool(r['score'] and r['score'][1] < r['score'][0])


def stats(db, only):
    rs = [r for r in load(db) if (useful(r) if only else True) and r['kinds'] != ['rename']]
    c = collections.Counter(k for r in rs for k in r['kinds'])
    print(f'{len(rs)} records')
    for k, v in c.most_common(): print(f'{v:6d}  {k}')


def show(db, kind, n):
    for r in [r for r in load(db) if kind in r['kinds'] and useful(r)][:n]:
        print(f"== {r['commit']} {r['func']} {r['kinds']}\n   {r['msg']}")
        sm = difflib.SequenceMatcher(None, r['before'].split(), r['after'].split(), autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op != 'equal':
                print('   -', ' '.join(r['before'].split()[max(0, i1 - 4):i2 + 2]))
                print('   +', ' '.join(r['after'].split()[max(0, j1 - 4):j2 + 2]))


# ---- label: compile before and after ----------------------------------------------------------
INSN = re.compile(r'^\s+0x([0-9a-fA-F]+):\s+(?:[0-9a-fA-F]{2,8}\s)+\s*\S*\s+([A-Z][A-Z0-9.]*)\s*(.*?)\s*$')
LABEL_LINE = re.compile(r'^    ([A-Za-z_$][\w$.]*)\s*$')
REG = re.compile(r'\b(r1[0-5]|r\d|sb|sl|fp|ip|lr|sp|pc)\b')


def listing(text):
    """{function: [(mnemonic, operands)]} from `fromelf -c`, data ($d) skipped."""
    out, cur, code = {}, None, True
    for line in text.splitlines():
        if line.startswith('** Section'):
            cur, code = None, True; continue
        m = LABEL_LINE.match(line)
        if m:
            n = m.group(1)
            if n in ('$t', '$t.0'): code = True
            elif n.startswith('$d'): code = False
            elif not n.startswith('$') and not n.startswith('|'):
                cur, code = n, True; out.setdefault(cur, [])
            continue
        m = INSN.match(line)
        if m and cur and code:
            ops = re.sub(r'\s*;.*$', '', m.group(3))
            ops = re.sub(r'\[pc,#-?\d+\]', '[pc]', ops)
            ops = re.sub(r'\b0x[0-9a-fA-F]+\b', 'A', ops) if m.group(2).startswith(('B', 'CB', 'BL')) else ops
            out[cur].append((m.group(2), ops))
    return out


def effect(a, b):
    if a == b: return 'none', 0
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    n = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != 'equal')
    if len(a) != len(b): return 'size', n
    if [REG.sub('R', ' '.join(x)) for x in a] == [REG.sub('R', ' '.join(x)) for x in b]: return 'regs', n
    if collections.Counter(a) == collections.Counter(b): return 'order', n
    const = lambda x: x[0] in ('MOVS', 'MOV', 'MOVW', 'MOVT', 'MOV.W', 'MVN', 'MVNS', 'LDR') and ('#' in x[1] or '[pc]' in x[1])
    diff = [x for op, i1, i2, j1, j2 in sm.get_opcodes() if op != 'equal' for x in a[i1:i2] + b[j1:j2]]
    if diff and all(const(x) or x[0] in ('MOV', 'MOVS') for x in diff): return 'const', n
    return 'insns', n


def project_dir(repo, rev, f):
    d = os.path.dirname(f)
    while True:
        if git(repo, 'cat-file', '-t', f'{rev}:{d + "/" if d else ""}exhume.toml').strip() == 'blob': return d
        if not d: return None
        d = os.path.dirname(d)


def extract(repo, rev, pdir, dest, f):
    """The project's headers, .inc files, exhume.toml and the source f, nothing else (some old
    commits hold a toolchain or build output inside the project: gigabytes)."""
    os.makedirs(dest, exist_ok=True)
    names = git(repo, 'ls-tree', '-r', '--name-only', rev, '--', pdir or '.').splitlines()
    keep = [n for n in names if n == f or n.endswith(('.h', '.inc', '/exhume.toml')) or n == 'exhume.toml']
    keep = [n for n in keep if not re.search(r'(^|/)(build|toolchain)/', n[len(pdir):])]
    for i in range(0, len(keep), 500):
        a = subprocess.run(['git', '-C', repo, 'archive', rev, '--'] + keep[i:i + 500], capture_output=True)
        subprocess.run(['tar', '-x', '-C', dest], input=a.stdout, capture_output=True)
    return os.path.join(dest, pdir) if pdir else dest


def resolve_home(repo, pdir, toml_text, homes):
    m = re.search(r'^home\s*=\s*"([^"]+)"', toml_text, re.M)
    if not m: return None
    h = os.path.normpath(os.path.join(repo, pdir or '', m.group(1)))
    if os.path.isfile(os.path.join(h, 'ARM', 'ARMCC', 'bin', 'armcc.exe')): return h
    for d in homes:
        c = os.path.join(d, os.path.basename(h))
        if os.path.isfile(os.path.join(c, 'ARM', 'ARMCC', 'bin', 'armcc.exe')): return c
    return None


_W = {}


def _init(q, root, includes):
    _W['prefix'] = q.get()
    _W['includes'] = includes
    os.environ['WINEPREFIX'] = _W['prefix']; os.environ['WINEDEBUG'] = '-all'
    os.environ['WINEDLLOVERRIDES'] = 'mscoree,mshtml='
    os.environ['EXHUME_ARMCC_RUNNER'] = 'wine'
    os.makedirs(_W['prefix'], exist_ok=True)
    quiet = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    subprocess.run(['wine', 'cmd', '/c', 'exit'], **quiet)
    _W['root'] = root


def _compile_rev(repo, rev, f, homes, work):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import armtc
    pdir = project_dir(repo, rev, f)
    if pdir is None: return None, 'no exhume.toml'
    root = extract(repo, rev, pdir, os.path.join(work, rev[:10]), f)
    toml = open(os.path.join(root, 'exhume.toml')).read()
    home = resolve_home(repo, pdir, toml, homes)
    if not home: return None, 'no compiler home'
    src = os.path.join(work, rev[:10], f)
    head = open(src, encoding='latin1').read(4000)
    o = re.search(r'/\*\s*opts:\s*([^*]+?)\s*\*/', head)
    c = re.search(r'^c_opts\s*=\s*"([^"]+)"', toml, re.M)
    opts = o.group(1) if o else (c.group(1) if c else '--cpu Cortex-M3 -O1')
    # project tools often add include paths of their own: give every header directory
    inc = sorted({dp for dp, _, fs in os.walk(root) if any(x.endswith('.h') for x in fs) and '/build' not in dp})
    inc += _W.get('includes', [])
    opts += ''.join(f' -I{d}' for d in inc)
    tc = armtc.Toolchain(home, root, runner='wine')
    obj = os.path.join(work, rev[:10] + '.o')
    ok, log = tc.compile(src, obj, opts)
    if not ok: return None, 'compile failed: ' + ' '.join(l for l in log.splitlines() if 'rror' in l)[:200]
    rc, out = tc.run('fromelf', ['-c', obj], os.path.dirname(obj))
    return listing(out), opts.split(' -I')[0]


def _label(job):
    repo, commit, f, recs, homes = job
    work = tempfile.mkdtemp(prefix='fixdb', dir=_W['root'])
    try:
        parent = git(repo, 'rev-parse', commit + '^').strip()
        full = git(repo, 'rev-parse', commit).strip()
        la, oa = _compile_rev(repo, parent, f, homes, work)
        lb, ob = _compile_rev(repo, full, f, homes, work)
        out = []
        for r in recs:
            if la is None or lb is None:
                r['effect'], r['ndiff'] = 'error', (oa if la is None else ob)
            elif r['func'] not in la or r['func'] not in lb:
                r['effect'], r['ndiff'] = 'error', 'function not in object'
            else:
                a, b = la[r['func']], lb[r['func']]
                r['effect'], r['ndiff'] = effect(a, b)
                m = lambda xs: [REG.sub('R', ' '.join(x)) for x in xs]
                sm = difflib.SequenceMatcher(None, m(a), m(b), autojunk=False)
                r['nreg'] = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != 'equal')
                r['ninsn'] = [len(a), len(b)]
                r['opts'] = ob
            out.append(r)
        return out
    finally:
        shutil.rmtree(work, ignore_errors=True)


def label(db, out, homes, jobs, max_size, extra=()):
    import multiprocessing as mp
    includes = sorted({dp for r in extra for dp, _, fs in os.walk(r) if any(x.endswith('.h') for x in fs)})
    rs = [r for r in load(db) if r['kinds'] != ['rename'] and r.get('size', 99) <= max_size]
    groups = collections.defaultdict(list)
    for r in rs: groups[(r['repo'], r['commit'], r['file'])].append(r)
    root = os.path.join(os.path.dirname(os.path.abspath(out)), 'fixdb_work')
    os.makedirs(root, exist_ok=True)
    q = mp.Manager().Queue()
    for i in range(jobs): q.put(os.path.join(root, f'wine{i}'))
    if os.path.exists(out):  # resume: keep what an earlier run labelled
        for r in load(out): groups.pop((r['repo'], r['commit'], r['file']), None)
    done = 0
    with open(out, 'a') as fh, mp.Pool(jobs, _init, (q, root, includes)) as pool:
        for res in pool.imap_unordered(_label, [(k[0], k[1], k[2], v, homes) for k, v in groups.items()]):
            for r in res: fh.write(json.dumps(r) + '\n')
            fh.flush()
            done += 1
            if done % 25 == 0: print(f'{done}/{len(groups)} files', flush=True)
            if shutil.disk_usage(root).free < 5 << 30:
                print('less than 5 GB free: stopped (run again to resume)'); pool.terminate(); break
    shutil.rmtree(root, ignore_errors=True)
    print(f'{len(rs)} records labelled -> {out}')


def matrix(path):
    rs = [json.loads(l) for l in open(path)]
    eff = collections.Counter(r['effect'] for r in rs)
    print('effects:', dict(eff))
    tab = collections.defaultdict(collections.Counter)
    for r in rs:
        if r['effect'] in ('error',): continue
        for k in r['kinds']: tab[k][r['effect']] += 1
    cols = ['none', 'regs', 'order', 'const', 'insns', 'size']
    print(f"{'kind':16s}" + ''.join(f'{c:>7s}' for c in cols) + '   active')
    for k, c in sorted(tab.items(), key=lambda kv: -sum(kv[1].values())):
        tot = sum(c.values())
        print(f'{k:16s}' + ''.join(f'{c[x]:7d}' for x in cols) + f'   {100 * (tot - c["none"]) // max(tot, 1):3d}%')


MODE = dict(literal_local='literals', dead_store='zero,dead', decl_type='types', decl_order='declorder',
            stmt_order='swap', operand_order='operands', compound_assign='compound', local_added='literals',
            temp_local='literals', probe_add='climb.py', probe_remove='climb.py')
CONST = re.compile(r'^(movs?|mov\.w|movw|movt|mvns?|ldr(\.w)?)\s+\w+,\s*(#|\[pc\])')


def symptom(text, func=None):
    """Counts of the remaining difference: regs, const, order, other (instruction lines)."""
    lines, on = [], func is None
    for l in text.splitlines():
        if func and re.match(r'^\S', l): on = l.split()[0] == func; continue
        m = re.match(r'^\s+([-+])\s+(\d+)\s+(.*)$', l)
        if on and m: lines.append((m.group(1), int(m.group(2)), m.group(3).strip()))
    hunks = collections.defaultdict(lambda: ([], []))
    for s, i, x in lines: hunks[i][0 if s == '-' else 1].append(x)
    c = collections.Counter()
    mask = lambda x: REG.sub('R', x)
    allm = collections.Counter(mask(x) for s, i, x in lines if s == '-')
    allp = collections.Counter(mask(x) for s, i, x in lines if s == '+')
    for i, (a, b) in hunks.items():
        if len(a) == len(b) and [mask(x) for x in a] == [mask(x) for x in b]:
            c['regs'] += len(a); continue
        for x in a + b:
            if CONST.match(x): c['const'] += 1
            elif allm[mask(x)] and allp[mask(x)]: c['order'] += 1
            else: c['other'] += 1
    return c


def suggest(path, diff, func):
    sym = symptom(open(diff).read(), func)
    tot = sum(sym.values()) or 1
    print('remaining difference:', ', '.join(f'{k} {v}' for k, v in sym.most_common()))
    rs = [json.loads(l) for l in open(path)]
    rs = [r for r in rs if r.get('effect') not in (None, 'error')]
    by = collections.defaultdict(list)
    for r in rs:
        for k in r['kinds']: by[k].append(r)
    rows = []
    for k, v in by.items():
        n = len(v)
        p = dict(regs=sum(1 for r in v if r['ndiff'] > r.get('nreg', r['ndiff'])) / n,
                 const=sum(1 for r in v if r['effect'] == 'const' or (r['effect'] in ('insns', 'size') and r.get('nreg', 0) > 0)) / n,
                 order=sum(1 for r in v if r['effect'] == 'order') / n,
                 other=sum(1 for r in v if r['effect'] in ('insns', 'size')) / n)
        score = sum(sym[x] / tot * p[x] for x in p)
        small = sum(1 for r in v if r['effect'] != 'none' and r['ndiff'] <= 6) / n
        rows.append((score, k, n, p, small))
    print(f"{'kind':16s} {'n':>4s} {'fit':>5s} {'regs':>5s} {'const':>5s} {'order':>5s} {'other':>5s} {'small':>5s}  mode")
    for score, k, n, p, small in sorted(rows, reverse=True):
        if k == 'rename': continue
        print(f"{k:16s} {n:4d} {score:5.2f} {p['regs']:5.2f} {p['const']:5.2f} {p['order']:5.2f} {p['other']:5.2f} {small:5.2f}  {MODE.get(k, '-')}")


def main(argv):
    if not argv or argv[0] not in ('mine', 'stats', 'show', 'label', 'matrix', 'suggest'):
        print(__doc__); return 2
    if argv[0] == 'mine':
        out = argv[argv.index('--out') + 1]
        globs = argv[argv.index('--paths') + 1].split(',') if '--paths' in argv else []
        repos = [a for i, a in enumerate(argv[1:], 1) if not a.startswith('--') and argv[i - 1] not in ('--out', '--paths')]
        mine(repos, out, globs)
    elif argv[0] == 'label':
        homes = argv[argv.index('--homes') + 1].split(',') if '--homes' in argv else []
        jobs = int(argv[argv.index('--jobs') + 1]) if '--jobs' in argv else 3
        ms = int(argv[argv.index('--max-size') + 1]) if '--max-size' in argv else 4
        extra = argv[argv.index('--includes') + 1].split(',') if '--includes' in argv else []
        label(argv[1], argv[2], [os.path.expanduser(h) for h in homes], jobs, ms, [os.path.expanduser(e) for e in extra])
    elif argv[0] == 'suggest':
        suggest(argv[1], argv[2], argv[3] if len(argv) > 3 else None)
    elif argv[0] == 'matrix':
        matrix(argv[1])
    elif argv[0] == 'stats':
        stats(argv[1], '--matches' in argv)
    else:
        show(argv[1], argv[2], int(argv[3]) if len(argv) > 3 else 5)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
