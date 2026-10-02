KIND = 'struct'; TAG = 'ObjStatic'; HEADER = 'object.h'; EMIT = False; RETAG = 'StaticObj'
TEXT = r'''
/* A static object: the first 8 bytes of struct Object, all a static object has. */
struct ObjStatic {
    unsigned id;
    unsigned pos;
    union {
        unsigned word;
        struct { unsigned quality:6, next:10; } f;
        union Link link;
    } qn;
    union {
        unsigned word;
        struct { unsigned owner:6, link:10; } f;
        union Link link;
    } ol;
};
'''
OVERRIDES = {}
