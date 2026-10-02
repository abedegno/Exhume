KIND = 'struct'; TAG = 'MotionParams'; HEADER = 'motion.h'
TEXT = r'''
/* The stepping state: a Bresenham walk along the major axis of the velocity. FM Towns's
   _MP, one initialised struct at DS:3FA (FM Towns reads DS:416 and DS:417 as _MP+0x20 and
   _MP+0x21, so they are fields, not separate globals). */
struct MotionParams {
    int *vel;                           /* 0x00, CP->vel */
    int *pos;                           /* 0x02, Ppd's x, y and z */
    int frac[3];                        /* 0x04, the fraction of pos, 0x2000 to a unit */
    int step[3];                        /* 0x0A */
    int major;                          /* 0x10 */
    int minor;                          /* 0x12 */
    int steps;                          /* 0x14 */
    int rem;                            /* 0x16 */
    int dt;                             /* 0x18 */
    int done;                           /* 0x1A */
    signed char hit;                    /* 0x1C, DS:416 */
    int item;                           /* 0x1D, DS:417 */
    int targz;                          /* 0x1F */
    int f21;                            /* 0x21 */
    int f23;                            /* 0x23 */
    int zspeed;                         /* 0x25 */
    unsigned headings[8];               /* 0x27 */
};
'''
OVERRIDES = {}
