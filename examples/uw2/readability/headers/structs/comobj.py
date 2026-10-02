KIND = 'struct'; TAG = 'ComObj'; HEADER = 'object.h'
TEXT = r'''
/* One object type's common properties: DATA\COMOBJ.DAT, 11 bytes an entry, loaded into
   ComObjData (UW-Formats documents the file). Names not in the format document are
   provisional, taken from the sources that use the fields. */
struct ComObj {
    unsigned height:8;                  /* 0x00 */
    unsigned radius:3;                  /* 0x01 */
    unsigned animated:1;
    unsigned mass:12;                   /* in tenths of a stone */
    unsigned b3_0:1;                    /* 0x03 */
    unsigned solid:1;
    unsigned c3_2:1;
    unsigned no_hit:1;
    unsigned b3_4:1;
    unsigned pickup:1;
    unsigned stack:2;
    unsigned value;                     /* 0x04, monetary value */
    unsigned touch:1;                   /* 0x06 */
    unsigned usable:1;
    unsigned qualclass:2;               /* quality class: this times 6 plus the quality
                                           indexes string block 5 */
    unsigned light:1;
    unsigned bounce:4;
    unsigned fate:4;                    /* 0x07, bits 1-4 */
    unsigned pickable:1;
    unsigned b7_6:1;
    unsigned can_own:1;                 /* the object can have an owner */
    unsigned char resist;               /* 0x08 */
    unsigned char render:2;             /* 0x09 */
    unsigned char tenacity:4;
    unsigned char b9_6:2;
    unsigned qualtype:4;                /* 0x0A, quality type: a group of 6 strings in block 4 */
    unsigned lookable:1;                /* a printable "look at" description */
    unsigned bA_5:3;
};
'''

OVERRIDES = {'OVR163.C': {'value': 'value'}, 'OVR110.C': {'value': 'value'}}
