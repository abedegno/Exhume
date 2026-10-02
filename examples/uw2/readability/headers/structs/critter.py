# struct Critter was the player's view of a creature record (Creature[63], through playerdat):
# lay its fields out against Creature's and rename the tag.
KIND = 'struct'; TAG = 'Critter'; HEADER = 'critter.h'; EMIT = False; RETAG = 'Creature'
TEXT = r'''
/* A creature type's properties, 48 bytes: the critters table of DATA\OBJECTS.DAT, 64 of
   them, loaded into Creature[]. The player's own entry is Creature[63], reached through
   playerdat. The names are provisional, taken from the sources that use the fields. */
struct Critter {
    unsigned char armour[4];            /* 0x00, by hit location; ovr157 reads armour[0]
                                           as the creature's level */
    unsigned char avghit;               /* 0x04, average hit points; for the player,
                                           maximum vitality */
    unsigned char attr[3];              /* 0x05: strength, dexterity, intelligence */
    unsigned char death:3;              /* 0x08 */
    unsigned char blood:2;
    unsigned char corpse:3;
    unsigned char race;                 /* 0x09 */
    unsigned char passive:1;            /* 0x0A */
    unsigned char bA_1:1;
    unsigned char remains:3;
    unsigned char bA_5:1;
    unsigned char swims:1;
    unsigned char flier:1;
    unsigned char speed;                /* 0x0B */
    unsigned char run;                  /* 0x0C */
    unsigned level:4;                   /* 0x0D; bartering calls it wit */
    unsigned shrewd:4;
    unsigned haggle:4;                  /* 0x0E */
    unsigned patience:4;
    unsigned char b0F;                  /* 0x0F */
    unsigned char sound:4;              /* 0x10 */
    unsigned char armour_kind:2;
    unsigned char weapon_kind:2;
    signed char equip;                  /* 0x11 */
    signed char defence;                /* 0x12 */
    struct {
        signed char chance;
        unsigned char damage;           /* attacks[0].damage is also the damage done to doors */
        unsigned char prob;
    } attacks[3];                       /* 0x13 */
    unsigned char b1C_0:4;              /* 0x1C */
    unsigned char range:4;
    unsigned char noise:4;              /* 0x1D */
    unsigned char visibility:4;
    unsigned char hearing:4;            /* 0x1E */
    unsigned char sight:4;
    unsigned char lazy:4;               /* 0x1F */
    unsigned char alert:4;
    struct {
        unsigned char present:1;
        unsigned char item:7;
    } arms[2];                          /* 0x20, arms[0].item is the missile it fires */
    struct {
        unsigned prob:4;
        unsigned item:12;
    } other[2];                         /* 0x22 */
    unsigned treasure_prob:4;           /* 0x26 */
    unsigned treasure_rate:4;
    unsigned food_prob:4;               /* 0x27 */
    unsigned food_item:4;
    int exp;                            /* 0x28 */
    unsigned char spells[3];            /* 0x2A */
    unsigned char b2D_0:1;              /* 0x2D */
    unsigned char caster:7;
    unsigned char locks;                /* 0x2E */
    unsigned char b2F;
};
'''
OVERRIDES = {'OVR110.C': {'str': 'attr[0]'}}
