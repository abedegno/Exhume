import json,re,math,os
U=os.path.expanduser('~/UW2Decomp')
ss=json.load(open('ssindex.json')); uw=json.load(open('uwfiles.json'))
df={}
for f,v in ss['files'].items():
    for i in set(x.lower() for x in v['ids']): df[i]=df.get(i,0)+1
N=len(ss['files'])
ssids={f:set(x.lower() for x in v['ids']) for f,v in ss['files'].items()}
ssdefs={f:set(x.lower() for x in v['defs']) for f,v in ss['files'].items()}
for k,v in uw.items():
    t=open(f"{U}/src/{k}",encoding='latin1').read()
    t=re.sub(r'/\*.*?\*/',' ',t,flags=re.S) if k.endswith('.C') else re.sub(r';[^\n]*','',t)
    ids=set(x.lower() for x in re.findall(r'\b[A-Za-z_]\w{3,}\b',t))
    defs=set(x.lower() for x in v['funcs'])
    sc=[]
    for f in ssids:
        if f.lower().endswith('.h'): continue
        common=ids&ssids[f]
        s=sum(math.log(N/df[i]) for i in common if df.get(i,N)<=8)
        d=defs&ssdefs[f]
        sc.append((s+5*len(d),f,sorted(i for i in common if df.get(i,N)<=4)[:8],sorted(d)))
    sc.sort(reverse=True)
    print(k, ' | '.join(f"{f.split('/')[-1]}({s:.0f}:{','.join(c)}{' DEF:'+','.join(d) if d else ''})" for s,f,c,d in sc[:3]))
