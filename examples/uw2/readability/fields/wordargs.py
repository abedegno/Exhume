import re,glob,os
SRC=os.path.expanduser('~/UW2Decomp/src')
import sys
fns={n:[0] for n in "Obj_PtrTMem Obj_FreeChain Obj_FreeLinkChain Obj_Add Obj_AddEnd Obj_Rem Obj_Punt Obj_Find Obj_InList check_weight Obj_Fate BagWeight FreePlayerInv InvSaveNexts delete_trap".split()}
fns['InvRestoreNexts']=[1]
rx=re.compile(r'\b(%s)\s*\('%'|'.join(fns))
def split_args(t,i):
    # t[i] is just after '('; return list of (start,end) and index after ')'
    depth=0; args=[]; st=i
    while True:
        c=t[i]
        if c in '([': depth+=1
        elif c in ')]':
            if depth==0: args.append((st,i)); return args,i+1
            depth-=1
        elif c==',' and depth==0: args.append((st,i)); st=i+1
        i+=1
def fix(t):
    out=[]; changed=0
    for m in list(rx.finditer(t))[::-1]:
        args,_=split_args(t,m.end())
        if len(args)<=fns[m.group(1)][0]: continue
        a0,a1=args[fns[m.group(1)][0]]
        arg=t[a0:a1]
        mm=re.match(r'^(\s*)&(.*)\.word(\s*)$',arg,re.S)
        if not mm: continue
        base=mm.group(2)
        if re.search(r'(->|\.)(qn|ol)$',base): new=f'{mm.group(1)}&{base}.link{mm.group(3)}'
        else: new=f'{mm.group(1)}&{base}{mm.group(3)}'
        t=t[:a0]+new+t[a1:]; changed+=1
    return t,changed
for f in sorted(glob.glob(SRC+'/*.C')):
    t=open(f,encoding='latin-1').read()
    n,k=fix(t)
    if k: open(f,'w',encoding='latin-1').write(n); print(os.path.basename(f),k)
