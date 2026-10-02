KIND = 'struct'; TAG = 'PathRec'; HEADER = 'critter.h'
TEXT = r'''
/* A critter's path, 28 bytes: two bits of direction and one bit of slope for each of up
   to 64 steps, the steps taken (flag.bits.count) and the path's length (index). */
struct PathRec {
    unsigned char x, y;
    union {
        unsigned char raw;
        struct { unsigned char count:7, slope:1; } bits;
    } flag;
    unsigned char index, directions[16], slopes[8];
};
'''
OVERRIDES = {}
