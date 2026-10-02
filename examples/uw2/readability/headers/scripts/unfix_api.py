"""Undo fix_api.py where a file's own prototype of the function takes unsigned far *."""
import re, glob, os
exec(open('fix_api.py').read().split('n = 0')[0])
def unfix_arg(a):
    a2 = re.sub(r'^(\s*&\s*.*?(?:->|\.)\s*(?:qn|ol))\s*\.\s*link\s*$', r'\1.word', a)
    if a2 != a: return a2
    a2 = re.sub(r'^(\s*&\s*.*?(?:->|\.)\s*objects)\s*$', r'\1.word', a)
    if a2 != a: return a2
    a2 = re.sub(r'^(\s*&\s*Inventory\s*\[[^\]]*\])\s*$', r'\1.word', a)
    return a2
n = 0
for p in sorted(glob.glob('base/*.C')):
    if p.endswith('SEG029.C'): continue
    s = open(p, encoding='latin1').read()
    uns = set()
    for fn in FN:
        m = re.search(r'^[^\n(]*\b%s\s*\(\s*unsigned\s+far\s*\*' % fn, s, re.M)
        if m: uns.add(fn)
    if not uns: continue
    out = []; pos = 0
    for _ in range(3):
        out = []; pos = 0; changed = False
        for m in pat.finditer(s):
            if m.group(1) not in uns: continue
            line = s[s.rfind('\n', 0, m.start()) + 1:m.start()]
            if re.match(r'\s*(extern\s+)?[a-z].*\bfar\s*\*?\s*(far\s+)?$', line) and '=' not in line and 'return' not in line: continue
            i, j = args_span(s, m.end())
            if i < pos: continue
            parts = []; d = 0; cur = ''
            for ch in s[i:j]:
                if ch in '([': d += 1
                elif ch in ')]': d -= 1
                if ch == ',' and d == 0: parts.append(cur); cur = ''
                else: cur += ch
            parts.append(cur)
            new = [unfix_arg(x) if idx == 0 else x for idx, x in enumerate(parts)]
            if new != parts:
                out.append(s[pos:i]); out.append(','.join(new)); pos = j; n += 1; changed = True
        out.append(s[pos:]); s = ''.join(out)
        if not changed: break
    open(p, 'w', encoding='latin1').write(s)
print(n, 'arguments restored')
