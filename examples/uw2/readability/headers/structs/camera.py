KIND = 'struct'; TAG = 'Camera'; HEADER = 'view3d.h'
TEXT = r'''
/* The 3D view's camera, a copy of the player's position (cPlayer). */
struct Camera {
    char pad0[0x0A];
    int x;                              /* 0x0A, in 1/256 tiles */
    char pad0C[2];
    int z;                              /* 0x0E */
    char pad10[2];
    int y;                              /* 0x12 */
    char pad14[0x28 - 0x14];
    int pitch;                          /* 0x28 */
    int bank;                           /* 0x2A */
    int heading;                        /* 0x2C */
};
'''
OVERRIDES = {'SEG032.C': {'heading': 'heading'}, 'SEG033.C': {'heading': 'heading'}}
