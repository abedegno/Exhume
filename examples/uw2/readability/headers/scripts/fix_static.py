# Uses of an 8-byte object record (a static object's whole record) where a file's own
# struct Object was 8 bytes: the struct copies, strides and locals keep their size as
# struct StaticObj.
import sys
def ed(f, pairs):
    p='base/'+f; s=open(p,encoding='latin1').read()
    for a,b,n in pairs:
        c=s.count(a); assert c==n, (f,a,c); s=s.replace(a,b)
    open(p,'w',encoding='latin1').write(s)
ed('OVR122.C', [
 ('static struct Object far *saveObjs;','static struct StaticObj far *saveObjs;',1),
 ('saveObjs = (struct Object far *)(invSlots + 0x1C);','saveObjs = (struct StaticObj far *)(invSlots + 0x1C);',2),
 ('    return saveObjs + saveNum;','    return (struct Object far *)(saveObjs + saveNum);',1),
 ('    return saveObjs + n;','    return (struct Object far *)(saveObjs + n);',1),
 ('        *copy = *obj;','        *(struct StaticObj far *)copy = *(struct StaticObj far *)obj;',1),
 ('    struct Object far *cursor;','    struct StaticObj far *cursor;',2),
 ('cursor = (struct Object far *)(p + 1);','cursor = (struct StaticObj far *)(p + 1);',2),
 ('        *cursor = *CursorObjPtr;','        *cursor = *(struct StaticObj far *)CursorObjPtr;',1),
 ('        *CursorObjPtr = *cursor;','        *(struct StaticObj far *)CursorObjPtr = *cursor;',1),
])
ed('OVR123.C', [('    struct Object obj;\n','    struct StaticObj obj;\n',1), ('LookAt(&obj, 0);','LookAt((struct Object far *)&obj, 0);',1)])
for f, n in (('OVR124.C', 2), ('OVR103.C', 1)):
    ed(f, [('*copy = *obj;','*(struct StaticObj far *)copy = *(struct StaticObj far *)obj;',n)])
ed('OVR125.C', [('*split = *obj;','*(struct StaticObj far *)split = *(struct StaticObj far *)obj;',1)])
ed('OVR122.C', [('        *obj = *saved;','        *(struct StaticObj far *)obj = *(struct StaticObj far *)saved;',1)])
