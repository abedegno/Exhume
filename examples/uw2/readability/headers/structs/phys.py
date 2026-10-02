KIND = 'struct'; TAG = 'Phys'; HEADER = 'motion.h'
TEXT = r'''
/* A physics record, 40 bytes: the player's (PN) and the four scratch records CN1..CN4 are
   seg031's, which steps them through the tile grid through the near pointer CP. */
struct Phys {
    int x, y, z;                        /* 0x00, x and y in 1/256 tiles, z in 1/8 */
    int vel[3];                         /* 0x06 */
    int acc[3];                         /* 0x0C, acc[2] is gravity */
    int time;                           /* 0x12 */
    int speed;                          /* 0x14 */
    unsigned char bounce;               /* 0x16 */
    unsigned char flags;                /* 0x17 */
    int mass;                           /* 0x18 */
    unsigned char light;                /* 0x1A */
    unsigned char hp;                   /* 0x1B */
    unsigned char resist;               /* 0x1C */
    unsigned char b1D;                  /* 0x1D */
    int heading;                        /* 0x1E */
    int index;                          /* 0x20 */
    unsigned char radius;               /* 0x22 */
    unsigned char height;               /* 0x23 */
    unsigned char b24;                  /* 0x24 */
    unsigned char terrain;              /* 0x25, one bit per terrain type */
    unsigned impact;                    /* 0x26 */
};
'''
