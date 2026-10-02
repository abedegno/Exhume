"""Callers of the object-list functions pass union Link pointers, as SEG029 defines them."""
import re, glob, os, sys
FN = 'Obj_PtrTMem Obj_Add Obj_AddEnd Obj_Rem Obj_Punt Obj_InList Obj_FreeLinkChain Obj_FreeChain Obj_Find check_weight'.split()
pat = re.compile(r'\b(%s)\s*\(' % '|'.join(FN))
def args_span(s, i):
    d = 1; j = i
    while d:
        if s[j] == '(': d += 1
        elif s[j] == ')': d -= 1
        j += 1
    return i, j - 1
def fix_arg(a):
    a2 = re.sub(r'^(\s*&\s*.*?(?:->|\.)\s*(?:qn|ol))\s*\.\s*word\s*$', r'\1.link', a)
    if a2 != a: return a2
    a2 = re.sub(r'^(\s*&\s*.*?(?:->|\.)\s*objects)\s*\.\s*word\s*$', r'\1', a)
    if a2 != a: return a2
    a2 = re.sub(r'^(\s*&\s*Inventory\s*\[[^\]]*\])\s*\.\s*word\s*$', r'\1', a)
    return a2
n = 0
for p in sorted(glob.glob('base/*.C')):
    if p.endswith('SEG029.C'): continue
    s = open(p, encoding='latin1').read(); out = []; pos = 0
    for m in pat.finditer(s):
        # skip declarations
        line = s[s.rfind('\n', 0, m.start()) + 1:m.start()]
        if re.match(r'\s*(extern\s+)?[a-z].*\bfar\s*\*?\s*(far\s+)?$', line) and '=' not in line and 'return' not in line: continue
        i, j = args_span(s, m.end())
        if i < pos: continue
        args = s[i:j]
        parts = []; d = 0; cur = ''
        for ch in args:
            if ch in '([': d += 1
            elif ch in ')]': d -= 1
            if ch == ',' and d == 0: parts.append(cur); cur = ''
            else: cur += ch
        parts.append(cur)
        k = 0
        new = [fix_arg(x) if idx == k else x for idx, x in enumerate(parts)]
        if new != parts:
            out.append(s[pos:i]); out.append(','.join(new)); pos = j; n += 1
    out.append(s[pos:])
    open(p, 'w', encoding='latin1').write(''.join(out))
print(n, 'arguments rewritten')
