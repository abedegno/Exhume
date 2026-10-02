KIND = 'struct'; TAG = 'Button'; HEADER = 'ui.h'
TEXT = r'''
/* One menu button, 16 bytes: the picture when not selected and when selected, and the
   button's bottom left corner and size. */
struct Button {
    unsigned char far *img[2];          /* 0x00 */
    int x;                              /* 0x08 */
    int y;                              /* 0x0A, the bottom row */
    int w;                              /* 0x0C */
    int h;                              /* 0x0E */
};
'''
OVERRIDES = {}
