KIND = 'union'; TAG = 'Link'; HEADER = 'uw2.h'
TEXT = r'''
/* A link word: an object's index in the top ten bits (0 for none, 1..0xFF a mobile object,
   0x100..0x3FF a static one) and six other bits below it. Object lists are chained through
   them: a tile's list head, each object's next link and its contents link. */
union Link {
    unsigned word;
    struct { unsigned low:6, index:10; } f;
};
'''
OVERRIDES = {}
