KIND = 'struct'; TAG = 'PhysTp'; HEADER = 'motion.h'; EMIT = False; RETAG = 'Handler'
TEXT = r'''
/* How seg031 moves a physics record through terrain, 12 bytes: the player's (PT) and the
   critters' (CT1..CT4), reached through the near pointer TP. */
struct PhysTp {
    unsigned ignore;                    /* 0x00 */
    unsigned mask;                      /* 0x02 */
    unsigned noclimb;                   /* 0x04 */
    unsigned w6;                        /* 0x06 */
    unsigned char (far *special)();     /* 0x08, called with the collision state's address
                                           when state & mask */
};
'''

OVERRIDES = {'SEG006.C': {'flags': 'ignore', 'w2': 'mask', 'w4': 'noclimb', 'handler': 'special'}, 'SEG007.C': {'flags': 'ignore', 'w2': 'mask', 'w4': 'noclimb', 'handler': 'special'}}
