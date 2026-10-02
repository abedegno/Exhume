KIND = 'struct'; TAG = 'Handler'; HEADER = 'motion.h'
TEXT = r'''
/* A mover's handler, 12 bytes, reached through the near pointer TP while seg031 moves a
   physics record: which collision bits to ignore, which to pass to special, and which
   stop it climbing onto objects. The player's is PT, the critters' CT1..CT4. */
struct Handler {
    unsigned ignore;                    /* 0x00 */
    unsigned mask;                      /* 0x02 */
    unsigned noclimb;                   /* 0x04 */
    unsigned w6;                        /* 0x06 */
    unsigned char (far *special)();     /* 0x08, called with the collision state's address
                                           when state & mask */
};
'''
