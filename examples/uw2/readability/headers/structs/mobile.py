KIND = 'struct'; TAG = 'Mobile'; HEADER = 'object.h'; EMIT = False; RETAG = 'Object'
TEXT = r'''
/* An object: 27 bytes for a mobile one (critters, missiles and anything else that moves,
   in critdata); a static object (objdata) is only the first 8 bytes, struct StaticObj.
   UW-Formats (4.2, the master object list) documents the fields; names it lacks are
   provisional, taken from the sources that use them. */
struct Mobile {
    unsigned id;                        /* 0x00: item 0-8 (major class 6-8, minor 4-5,
                                           index 0-3), flags 9-12, tenacious 13,
                                           is_quant 15 */
    unsigned pos;                       /* 0x02: z 0-6, heading 7-9, fine y 10-12,
                                           fine x 13-15 */
    union {
        unsigned word;
        struct { unsigned quality:6, next:10; } f;
        union Link link;
    } qn;                               /* 0x04: the quality and the next object in the
                                           list */
    union {
        unsigned word;
        struct { unsigned owner:6, link:10; } f;
        union Link link;
    } ol;                               /* 0x06: the owner, and the contents (or, if
                                           is_quant, the quantity) */
    unsigned char hp;                   /* 0x08, mobile objects only from here on */
    unsigned char heading;              /* 0x09 */
    unsigned char b0A;                  /* 0x0A */
    unsigned goal_word;                 /* 0x0B: goal 0-3, target 4-11; a missile's
                                           fine x */
    unsigned attitude_word;             /* 0x0D: level 0-3, talked to 13, attitude
                                           14-15; a missile's fine y */
    unsigned b0F;                       /* 0x0F; a missile's fine z */
    unsigned char b11;                  /* 0x11 */
    unsigned char last_hit;             /* 0x12 */
    unsigned char b13;                  /* 0x13 */
    unsigned char b14;                  /* 0x14 */
    unsigned char b15;                  /* 0x15 */
    unsigned home;                      /* 0x16: y 4-9, x 10-15 */
    unsigned char b18;                  /* 0x18, fine heading in bits 0-4 */
    unsigned char b19;                  /* 0x19 */
    unsigned char whoami;               /* 0x1A, the conversation to run */
};

'''
PREFER = ['qn.f.quality', 'qn.f.next', 'ol.f.owner', 'ol.f.link', 'qn.word', 'ol.word']
OVERRIDES = {}
