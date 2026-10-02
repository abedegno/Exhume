# retype.py FILE VAR[,VAR...] : retype 'unsigned far *VAR' link pointers to 'union Link far *'
import re,sys,os
f=os.path.expanduser('~/UW2Decomp/src/'+sys.argv[1]); vs=sys.argv[2].split(',')
t=open(f,encoding='latin-1').read(); o=t
V='|'.join(map(re.escape,vs))
def wordfix(base):
    return base+'.link' if re.search(r'(->|\.)(qn|ol)$',base) else base
# declarations (locals and parameters), also 'unsigned far *a, *b' not handled
t=re.sub(r'\bunsigned far \*\s*(%s)\b'%V, r'union Link far *\1', t)
# assignments X = &E.word
def asg(m):
    return f'{m.group(1)} = &{wordfix(m.group(2))}'
t=re.sub(r'\b(%s) = &([^;,\n]*?)\.word\b'%V, asg, t)
# head bits casts
t=re.sub(r'\(\(struct ObjHeadBits far \*\)(%s)\)->index'%V, r'\1->f.index', t)
t=re.sub(r'\(\(struct ObjHeadBits far \*\)(%s)\)->flags'%V, r'\1->f.low', t)
open(f,'w',encoding='latin-1').write(t)
print('changed' if t!=o else 'nochange')
