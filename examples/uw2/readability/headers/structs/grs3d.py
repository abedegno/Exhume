KIND = 'struct'; TAG = 'Grs3d'; HEADER = 'view3d.h'
TEXT = r'''
/* The art page each 3D object's pictures are in (grs_3dinf), 2 bytes. */
struct Grs3d {
    unsigned char page;
    char b1;
};
'''
OVERRIDES = {}
