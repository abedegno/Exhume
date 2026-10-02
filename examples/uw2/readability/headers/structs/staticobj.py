KIND = 'struct'; TAG = 'StaticObj'; HEADER = 'object.h'; EMIT = False
TEXT = r'''
/* A static object: the first 8 bytes of struct Object, all a static object has. */
struct StaticObj {
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
PREFER = ['qn.f.quality', 'qn.f.next', 'ol.f.owner', 'ol.f.link', 'qn.word', 'ol.word']
OVERRIDES = {}
