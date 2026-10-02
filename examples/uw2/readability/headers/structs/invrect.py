KIND = 'struct'; TAG = 'InvRect'; HEADER = 'inv.h'
TEXT = r'''
/* One inventory slot on screen, 14 bytes: the rectangle the mouse hits (y counts up from
   the bottom of the screen, so top >= bottom) and where its picture goes. */
struct InvRect {
    int left, top, right, bottom;       /* 0x00 */
    int x, y;                           /* 0x08 */
    unsigned char w, h;                 /* 0x0C */
};
'''
OVERRIDES = {}
