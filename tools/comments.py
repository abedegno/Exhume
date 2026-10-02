"""Comment a matched tree without touching its code, and prove it.

    python3 tools/comments.py [--config PATH] apply SRC SPEC.py [-n]
    python3 tools/comments.py [--config PATH] check A B
    python3 tools/comments.py [--config PATH] check --ref GITREF [SRC ...]
    python3 tools/comments.py reflow [--width 92] [--max 100] SRC ...
    python3 tools/comments.py audit SRC ...
    python3 tools/comments.py join SRC ...

Step 6 of skills/readability-pass adds file headers, routine comments and tagged notes to every
source. Comments cannot change the bytes, but an edit meant as a comment can: a stray `*/`, a
line pasted into code, a nested `/*` that ends a comment early. Every command here compares
the source's token stream with its comments removed (tools/cparse.py tokens(): strings and
character constants kept whole, whitespace ignored) before and after, and refuses to write
when they differ. A comment pass whose every file passes `check --ref` needs no build to
prove; the gate passes it anyway.

  apply    edit SRC as SPEC.py says, then write it only if the tokens are unchanged, no
           comment nests another and no non-ASCII character was added. SPEC.py defines any of:
             HEADER = '...'        the file's header comment (text only): it replaces the first
                                   comment after the /* opts: */ line (or the target line), or
                                   is inserted there if HEADER_INSERT = True
             HEADER_AFTER = [...]  more comment blocks after the header
             BEFORE = [(anchor, text)]   a comment above the one line that starts with anchor
                                   (with several, the one that begins a function definition)
             TRAIL = [(substring, text)] a comment at the end of the one line containing it
             RETAG = [(old, new)]  exact replacements, each of which must occur once: for
                                   rewording or tagging existing comments
           -n reports without writing. Assembly sources (.ASM) get `;` comments.
  check    A and B (two files), or each SRC (default: every source) against its text at a
           git revision: "same tokens" or where they first differ.
  reflow   rewrap the paragraphs of block comments that have a line over --max characters
           to --width, in place, refusing any change to the tokens.
  audit    per file, the function definitions with no comment directly above them.
  join     put a trailing comment that runs on over several lines back on its code line
           (the inverse of a hand wrap, before reflow or for a long-line policy).

Tags used in the UW2 run (docs/readability.md): `match:` explains a code shape kept for byte
matching (a dead store, a strange cast, an order), `name:` the evidence for a name (the
sibling build's symbol, an original source file, or "descriptive").
"""
import os, re, sys, subprocess, textwrap
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import cparse


def is_asm(path): return path.upper().endswith(('.ASM', '.INC'))


def toks(text, path): return cparse.tokens(text, asm=is_asm(path))


def block(text, indent='', asm=False, width=100):
    lines = text.strip('\n').split('\n')
    if asm: return '\n'.join(indent + ('; ' + l if l else ';') for l in lines)
    if len(lines) == 1 and len(indent) + len(lines[0]) + 6 <= width: return indent + '/* ' + lines[0] + ' */'
    out = [indent + '/* ' + lines[0]] + [indent + '   ' + l if l else '' for l in lines[1:]]
    out[-1] += ' */'
    return '\n'.join(out)


def first_difference(a, b):
    n = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return f'token {n}: {" ".join(a[max(0, n - 4):n + 6])!r} became {" ".join(b[max(0, n - 4):n + 6])!r}'


def apply(path, specp, dry):
    spec = {}
    exec(open(specp).read(), spec)
    asm = is_asm(path)
    orig = open(path, encoding='latin-1').read()
    src = orig
    before = toks(orig, path)
    for old, new in spec.get('RETAG', []):
        k = src.count(old)
        if k != 1: return f'RETAG matches {k} times: {old[:60]!r}'
        src = src.replace(old, new)
    lines = src.split('\n')
    if 'HEADER' in spec:
        k = next((i for i, l in enumerate(lines) if re.search(r'/\*\s*opts:', l)), None)
        if k is None: k = next((i for i, l in enumerate(lines) if re.search(r'/\*\s*target:', l)), -1)
        k += 1
        if not spec.get('HEADER_INSERT'):
            if asm:
                if not lines[k].lstrip().startswith(';'): return f'no header comment after line {k}: {lines[k]!r}'
                e = k
                while e + 1 < len(lines) and lines[e + 1].lstrip().startswith(';') and lines[e + 1].strip() != ';': e += 1
            else:
                if not lines[k].lstrip().startswith('/*'): return f'no header comment after line {k}: {lines[k]!r}'
                e = k
                while '*/' not in lines[e]: e += 1
            del lines[k:e + 1]
        extra = []
        for t in spec.get('HEADER_AFTER', []): extra += block(t, asm=asm).split('\n')
        lines[k:k] = block(spec['HEADER'], asm=asm).split('\n') + extra
    for anchor, com in spec.get('BEFORE', []):
        hits = [i for i, l in enumerate(lines) if l.startswith(anchor)]
        if len(hits) > 1:
            def isdef(i):
                for l in lines[i:i + 8]:
                    if l.strip().startswith('{'): return True
                    if l.rstrip().endswith(';'): return False
                return False
            hits = [i for i in hits if isdef(i)]
        if len(hits) != 1: return f'BEFORE matches {len(hits)} lines: {anchor!r}'
        i = hits[0]
        lines[i:i] = block(com, re.match(r'\s*', lines[i]).group(0), asm).split('\n')
    for sub, com in spec.get('TRAIL', []):
        hits = [i for i, l in enumerate(lines) if sub in l]
        if len(hits) != 1: return f'TRAIL matches {len(hits)} lines: {sub!r}'
        i = hits[0]
        code = re.sub(r"'[^'\n]*'|\"[^\"\n]*\"", '', lines[i])
        if (';' in code) if asm else ('/*' in code or '//' in code):
            return f'TRAIL line already has a comment: {lines[i]!r}'
        lines[i] = lines[i] + ('  ; ' + com if asm else '  /* ' + com + ' */')
    out = '\n'.join(lines)
    after = toks(out, path)
    if after != before: return 'TOKENS CHANGED: ' + first_difference(before, after)
    if not asm:
        for m in re.finditer(r'/\*(.*?)\*/', out, re.S):
            if '/*' in m.group(1): return f'nested comment: {m.group(0)[:80]!r}'
    if any(ord(ch) > 127 for ch in out) and not any(ord(ch) > 127 for ch in orig): return 'non-ASCII characters added'
    if not dry: open(path, 'w', encoding='latin-1', newline='').write(out)
    n = lambda k: len(spec.get(k, []))
    return f'ok {path}: header {"yes" if "HEADER" in spec else "no"}, BEFORE {n("BEFORE")}, TRAIL {n("TRAIL")}, RETAG {n("RETAG")}' \
        + (' (not written: -n)' if dry else '')


def check(a, ref, files, cfg):
    bad = 0
    if ref is None:
        x, y = a
        ta, tb = toks(open(x, encoding='latin-1').read(), x), toks(open(y, encoding='latin-1').read(), y)
        print('same tokens' if ta == tb else 'DIFFERENT ' + first_difference(ta, tb)); return 0 if ta == tb else 1
    if not files:
        import sources
        files = sources.all_sources(cfg) + sorted(p for p in __import__('glob').glob(os.path.join(cfg.include, '*')) if p.lower().endswith('.h'))
    top = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True,
                         cwd=os.path.dirname(os.path.abspath(files[0]))).stdout.strip()
    same = 0; new = 0
    for f in files:
        r = subprocess.run(['git', '-C', top, 'show', f'{ref}:{os.path.relpath(os.path.abspath(f), top)}'],
                           capture_output=True, text=True, encoding='latin-1')
        if r.returncode: new += 1; print(f'{f}: not at {ref}'); continue
        ta, tb = toks(r.stdout, f), toks(open(f, encoding='latin-1').read(), f)
        if ta == tb: same += 1
        else: bad += 1; print(f'{f}: CODE CHANGED since {ref}, ' + first_difference(ta, tb))
    print(f'{same} files with the same tokens as at {ref}, {bad} changed' + (f', {new} not there' if new else ''))
    return 1 if bad else 0


def reflow(files, width, maxlen):
    for p in files:
        src = open(p, encoding='latin-1').read(); before = toks(src, p)
        lines = src.split('\n'); changed = False; i = 0
        while i < len(lines):
            l = lines[i]
            if len(l) > maxlen and not is_asm(p):
                text = '\n'.join(lines[:i + 1])
                o = text.rfind('/*'); c = text.rfind('*/', 0, len(text) - len(l))
                if not (o != -1 and o > c): i += 1; continue
                lo = l.lstrip(); starts = lo.startswith('/*')
                ind = len(l) - len(lo) + (3 if starts else 0)
                k = i
                while k + 1 < len(lines):
                    nx = lines[k + 1]
                    if '*/' in lines[k] or not nx.strip() or nx.lstrip().startswith(('- ', '/*')) or len(nx) - len(nx.lstrip()) != ind: break
                    k += 1
                seg = lines[i:k + 1]; prefix = seg[0][:len(seg[0]) - len(seg[0].lstrip())]
                lead, rest = (prefix + '/* ', seg[0].lstrip()[3:]) if starts else (prefix, seg[0].lstrip())
                words = ' '.join([rest] + [x.strip() for x in seg[1:]])
                wrapped = textwrap.wrap(words, width=width, initial_indent=lead, subsequent_indent=' ' * ind,
                                        break_long_words=False, break_on_hyphens=False)
                lines[i:k + 1] = wrapped; changed = True; i += len(wrapped); continue
            i += 1
        out = '\n'.join(lines)
        if toks(out, p) != before: print('TOKENS CHANGED, not written:', p); continue
        if changed: open(p, 'w', encoding='latin-1', newline='').write(out); print('reflowed', p)


def audit(files):
    for p in files:
        L = open(p, encoding='latin-1').read().split('\n'); tot = 0; unc = []
        for i, l in enumerate(L):
            if re.match(r'^[a-z].*\b(far|near)?\s*\**\s*[A-Za-z_]\w*\(', l) and not l.rstrip().endswith(';') \
               and not l.startswith(('extern', 'typedef', 'static char', '#')):
                ok = False
                for m in L[i:i + 5]:
                    if m.strip().startswith('{'): ok = True; break
                    if m.rstrip().endswith(';'): break
                if not ok: continue
                tot += 1; j = i - 1
                while j >= 0 and not L[j].strip(): j -= 1
                if j < 0 or not L[j].rstrip().endswith('*/'): unc.append(re.search(r'([A-Za-z_]\w*)\(', l).group(1))
        print(f"{p}: {tot} functions, {len(unc)} with no comment above: {' '.join(unc[:40])}")


def join(files):
    for p in files:
        src = open(p, encoding='latin-1').read(); before = toks(src, p)
        lines = src.split('\n'); out = []; i = 0; ch = 0
        while i < len(lines):
            l = lines[i]; k = l.find('/*')
            if k > 0 and l[:k].strip() and '*/' not in l[k:]:
                acc = l; j = i + 1
                while j < len(lines):
                    acc += ' ' + lines[j].strip(); j += 1
                    if '*/' in lines[j - 1]: break
                out.append(acc); i = j; ch += 1; continue
            out.append(l); i += 1
        res = '\n'.join(out)
        if toks(res, p) != before: print('TOKENS CHANGED, not written:', p); continue
        if ch: open(p, 'w', encoding='latin-1', newline='').write(res); print(p, ch)


def main(argv):
    a = list(argv)
    cpath = None
    if '--config' in a: i = a.index('--config'); cpath = a[i + 1]; del a[i:i + 2]
    def take(flag, default=None):
        if flag in a: i = a.index(flag); v = a[i + 1]; del a[i:i + 2]; return v
        return default
    if not a: print(__doc__); return 2
    cmd = a.pop(0)
    if cmd == 'apply':
        dry = '-n' in a; rest = [x for x in a if x != '-n']
        if len(rest) != 2: print(__doc__); return 2
        r = apply(rest[0], rest[1], dry); print(r); return 0 if r.startswith('ok') else 1
    if cmd == 'check':
        ref = take('--ref')
        cfg = None
        if ref is not None:
            import config
            cfg = config.load(cpath)
        if ref is None and len(a) != 2: print(__doc__); return 2
        return check(a, ref, a if ref is not None else None, cfg)
    if cmd == 'reflow':
        w = int(take('--width', 92)); m = int(take('--max', 100)); reflow(a, w, m); return 0
    if cmd == 'audit': audit(a); return 0
    if cmd == 'join': join(a); return 0
    print(__doc__); return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
