KIND = 'struct'; TAG = 'Spell'; HEADER = 'combat.h'
TEXT = r'''
/* One spell's runes, 4 bytes. */
struct Spell {
    unsigned char cls;                  /* class in bits 3-7 */
    int runes;                          /* the three runes, 5 bits each */
    unsigned char sub;
};
'''
OVERRIDES = {}
