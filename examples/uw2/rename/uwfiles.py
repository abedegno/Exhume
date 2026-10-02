import os,re,json,glob
U=os.path.expanduser('~/UW2Decomp')
syms={}
for l in open(U+'/symbols.tsv'):
    if l.startswith('#') or not l.strip(): continue
    f=l.rstrip('\n').split('\t'); syms[f[0]]=f[2] if len(f)>2 else ''
ss=json.load(open('ssindex.json'))
out={}
for src in sorted(glob.glob(U+'/src/*.C')+glob.glob(U+'/src/*.ASM')):
    t=open(src,encoding='latin1').read()
    m=re.search(r'/\*\s*target:\s*(\w+)\s*\*/',t)
    if not m: continue
    tg=m.group(1); rows=[l.split('\t') for l in open(f'{U}/targets/{tg}.tsv') if not l.startswith('#') and l.strip()]
    names=[r[0] for r in rows]
    fm=[n for n in names if syms.get(n)=='FM Towns' or syms.get('_'+n)=='FM Towns']
    hits={}
    for n in fm:
        for k in (n.lower(), n.lower().rstrip('_')):
            if k in ss['defs']:
                for f in ss['defs'][k]: hits.setdefault(f,[]).append(n)
    # strings
    strs=re.findall(r'"((?:[^"\\]|\\.)*)"',t) if src.endswith('.C') else re.findall(r"'([^']{4,})'",t)
    out[os.path.basename(src)]=dict(target=tg,funcs=names,fm=fm,ss=hits,strings=strs[:40])
json.dump(out,open('uwfiles.json','w'),indent=1)
for k,v in out.items():
    print(f"{k:12} {v['target']:18} fns={len(v['funcs'])} fm={len(v['fm'])} ss=" + '; '.join(f'{f.split("/")[-1]}:{",".join(n)}' for f,n in sorted(v['ss'].items(),key=lambda x:-len(x[1]))[:4]))
