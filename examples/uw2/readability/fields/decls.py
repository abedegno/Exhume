import re,glob,os,collections,sys
SRC=os.path.expanduser('~/UW2Decomp/src')
names=sys.argv[1].split()
for n in names:
    d=collections.defaultdict(list)
    for f in sorted(glob.glob(SRC+'/*.C')+glob.glob(SRC+'/include/*.h')):
        txt=open(f,encoding='latin-1').read()
        for m in re.finditer(r'^[ \t]*(?:extern\s+)?[A-Za-z][^;{}()=]*\b%s\s*\(([^;{}]*?)\)\s*;'%n,txt,re.M):
            d[re.sub(r'\s+',' ',m.group(0).strip())].append(os.path.basename(f))
        if re.search(r'^[a-z][^;\n]*\b%s\s*\([^;]*\)\s*\n\{'%n,txt,re.M): d['DEF'].append(os.path.basename(f))
    print('==',n)
    for k,v in d.items(): print('   ',k,v)
