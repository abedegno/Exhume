# objoff.py FILE VAR... : replace cast-offset accesses into struct Object pointers with fields
import re,sys,os
f=os.path.expanduser('~/UW2Decomp/src/'+sys.argv[1]); vs=sys.argv[2:]
BYTE={0:None,8:'hp',9:'heading',0xa:'b0A',0x11:'b11',0x12:'last_hit',0x13:'b13',0x14:'b14',0x15:'b15',0x18:'b18',0x19:'b19',0x1a:'whoami'}
WORD={0:'id',2:'pos',4:'qn.word',6:'ol.word',0xb:'goal_word',0xd:'attitude_word',0xf:'b0F',0x16:'home'}
t=open(f,encoding='latin-1').read(); n=0
for v in vs:
    V=re.escape(v)
    def w(m):
        global n; off=int(m.group(1),0)
        if off not in WORD: return m.group(0)
        n+=1; return f'{v}->{WORD[off]}'
    t=re.sub(r'\*\(unsigned far \*\)\(\(unsigned char far \*\)%s\s*\+\s*(0x[0-9a-fA-F]+|\d+)\)'%V,w,t)
    def b(m):
        global n; off=int(m.group(1),0)
        if not BYTE.get(off): return m.group(0)
        n+=1; return f'{v}->{BYTE[off]}'
    t=re.sub(r'\(\(unsigned char far \*\)%s\)\[(0x[0-9a-fA-F]+|\d+)\]'%V,b,t)
open(f,'w',encoding='latin-1').write(t); print(n)
