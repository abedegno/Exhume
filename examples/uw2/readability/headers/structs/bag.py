KIND = 'struct'; TAG = 'Bag'; HEADER = 'inv.h'
TEXT = r'''
/* An open bag in the inventory panel, 12 bytes: bags opened inside bags form a chain
   (OpenBag is the innermost). */
struct Bag {
    struct Bag far *next;               /* 0x00, the bag opened inside this one */
    struct Bag far *prev;               /* 0x04, the bag this one was opened from */
    union Link obj;                     /* 0x08, the bag object */
    int weight;                         /* 0x0A */
};
'''
OVERRIDES = {}
