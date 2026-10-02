KIND = 'struct'; TAG = 'MotionCalc'; HEADER = 'map.h'
TEXT = r'''
/* The motion calculation record, 23 bytes (seg031's Ppd is one, and the next variable
   follows at +0x17), reached through curP: what seg028's collision checks find under and
   around a mover's footprint. */
struct MotionCalc {
    int x, y, z;                        /* 0x00, in 1/8 tiles */
    unsigned heading;                   /* 0x06, the heading in bits 13-15 */
    unsigned char radius;               /* 0x08 */
    unsigned char height;               /* 0x09 */
    int index;                          /* 0x0A */
    int hits0, hits1;                   /* 0x0C */
    unsigned char floor;                /* 0x10, the height under the centre */
    unsigned char top;                  /* 0x11, the highest under the footprint */
    unsigned char slope;                /* 0x12 */
    unsigned char open;                 /* 0x13 */
    unsigned char found;                /* 0x14, collisions found */
    unsigned char count;                /* 0x15, those in the way */
    signed char first;                  /* 0x16, the first of them in oCollisions */
};
'''
OVERRIDES = {'SEG028.C': {'w6': 'heading', 'hits0': 'hits0', 'hits1': 'hits1'},
             'SEG031.C': {'heading': 'heading'}}
