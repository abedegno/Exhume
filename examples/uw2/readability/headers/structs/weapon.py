KIND = 'struct'; TAG = 'Weapon'; HEADER = 'combat.h'
TEXT = r'''
/* A melee weapon, 8 bytes: the melee weapons table of DATA\OBJECTS.DAT. */
struct Weapon {
    unsigned char damage[3];            /* by swing kind: slash, bash, stab */
    unsigned char min_charge;           /* 0x03 */
    unsigned char speed;                /* 0x04 */
    unsigned char max_charge;           /* 0x05 */
    unsigned char skill;                /* 0x06, the skill it uses */
    unsigned char durability;           /* 0x07 */
};
'''
OVERRIDES = {}
