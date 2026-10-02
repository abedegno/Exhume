import re, os, sys
M=os.path.join(os.path.dirname(os.path.abspath(__file__)), 'map.tsv')
rows=[l.rstrip('\n').split('\t') for l in open(M)]
mp={}
for old,new,kind,ev in rows:
    os_,oe=old.split('.'); nd,nf=new.split('/'); ns,ne=nf.split('.')
    mp[os_]=(oe,nd,ns,ne)
alt='|'.join(sorted(mp,key=len,reverse=True))
rx=re.compile(r'(src/)?\b('+alt+r')(\.(?:C|ASM))?\b(?!\.\w)')
def sub(m):
    src,st,ext=m.groups()
    oe,nd,ns,ne=mp[st]
    if st=='PLAYER' and not ext: return m.group(0)
    if src: return f'src/{nd}/{ns}'+('.'+ne if ext else '')
    return ns+('.'+ne if ext else '')
tot=0
for f in sys.argv[1:]:
    t=open(f,encoding='latin1',newline='').read()
    n2,c=rx.subn(sub,t)
    if c: open(f,'w',encoding='latin1',newline='').write(n2); print(c,f); tot+=c
print(tot)
