KIND = 'struct'; TAG = 'MissileDat'; HEADER = 'x'; EMIT = False; RETAG = 'MissileInfo'
TEXT = r'''
/* A missile weapon, 3 bytes: the ranged weapons table of DATA\OBJECTS.DAT, in Missile[]. */
struct MissileDat {
    unsigned char damage;               /* 0x00 */
    unsigned char type;                 /* 0x01, the missile type */
    signed char ammo;                   /* 0x02, the ammunition it fires */
};
'''
OVERRIDES = {'OVR163.C': {'ammo': 'ammo'}}
