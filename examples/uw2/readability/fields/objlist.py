import re,glob,os,sys
SRC=os.path.expanduser('~/UW2Decomp/src')
names="Obj_PtrTMem Obj_IntTMem Obj_FreeChain Obj_FreeLinkChain Obj_Check Obj_Alloc Obj_Add Obj_AddEnd Obj_Rem Obj_Punt Obj_Find IsMobElem Obj_InList HasOrIsObj Obj_FindInMap check_weight ObjCrunch".split()
s29=open(SRC+'/SEG029.C').read()
protos={}
for n in names:
    m=re.search(r'^([a-z][^;\n]*\b%s\s*\([^;\n]*\))\s*\n\{'%n,s29,re.M); protos[n]=m.group(1)+';'
hdr=open(SRC+'/include/object.h').read()
anchor='struct Object far * far Obj_FindInMapSquare(int major, int minor, int index, int x, int y);\n'
hdr=hdr.replace(anchor, anchor+''.join(protos[n]+'\n' for n in names),1)
open(SRC+'/include/object.h','w').write(hdr)
decl=re.compile(r'^[ \t]*(?:extern\s+)?(?:void|char|unsigned|int|struct|signed|long)\b[^;{}()=]*\b(%s)\s*\([^;{}]*?\)\s*;[ \t]*\n'%'|'.join(names),re.M)
for f in sorted(glob.glob(SRC+'/*.C')):
    t=open(f,encoding='latin-1').read()
    new,k=decl.subn('',t)
    if k:
        open(f,'w',encoding='latin-1').write(new); print(os.path.basename(f),k)
