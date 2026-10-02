import os,re,json,sys
R=os.path.expanduser(sys.argv[1] if len(sys.argv) > 1 else '~/ShockMac')
defs={}   # name -> [file]
files={}
fdef=re.compile(r'^(?![ \t]*(?:if|while|for|switch|return|else|do)\b)[A-Za-z_][\w \t\*]*?\b([A-Za-z_]\w*)[ \t]*\(([^;{}()]|\([^()]*\))*\)[ \t]*(?:\n[ \t]*[^;{}\n]*;)*[ \t\n]*\{',re.M)
for dp,dn,fn in os.walk(R):
    if '/.finf' in dp or '/.rsrc' in dp: continue
    for f in fn:
        if not re.search(r'\.(c|asm|s|h)$',f,re.I): continue
        p=os.path.join(dp,f); t=open(p,encoding='latin1').read()
        t=re.sub(r'/\*.*?\*/',' ',t,flags=re.S); t=re.sub(r'//[^\n]*','',t)
        rel=os.path.relpath(p,R)
        ds=set(m.group(1) for m in fdef.finditer(t)) if not f.lower().endswith(('.h','.s','.asm')) else set()
        if f.lower().endswith(('.asm','.s')):
            ds=set(re.findall(r'(?im)^\s*(?:public|global|export|\.global)\s+_?(\w+)',t))|set(re.findall(r'(?im)^_?(\w+)\s+proc\b',t))
        files[rel]=dict(defs=sorted(ds),ids=sorted(set(re.findall(r'\b[A-Za-z_]\w{3,}\b',t))))
        for d in ds: defs.setdefault(d.lower(),[]).append(rel)
json.dump(dict(defs=defs,files=files),open('ssindex.json','w'))
print(len(files),len(defs))
