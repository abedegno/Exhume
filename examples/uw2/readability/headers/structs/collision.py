KIND = 'struct'; TAG = 'Collision'; HEADER = 'map.h'
TEXT = r'''
/* A collision record, 6 bytes: what seg028's object checks found in a mover's way, in
   oCollisions. */
struct Collision {
    unsigned char top;                  /* 0x00 */
    unsigned char bottom;               /* 0x01 */
    union Link link;                    /* 0x02: the object, with flags in the low six bits */
    int offset;                         /* 0x04, the tile's offset in the map */
};
'''
OVERRIDES = {'SEG030.C': {'link': 'link'}, 'SEG031.C': {'link': 'link', 'z': 'top', 'top': 'bottom'}}
