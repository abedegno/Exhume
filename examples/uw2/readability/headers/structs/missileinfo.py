KIND = 'struct'; TAG = 'MissileInfo'; HEADER = 'combat.h'
TEXT = r'''
/* A missile weapon, 3 bytes: the ranged weapons table of DATA\OBJECTS.DAT, in Missile[]. */
struct MissileInfo {
    unsigned char damage;               /* 0x00 */
    unsigned char type;                 /* 0x01, the missile type */
    signed char ammo;                   /* 0x02, the ammunition it fires */
};
'''
OVERRIDES = {'SEG027.C': {'a': 'damage'}}
