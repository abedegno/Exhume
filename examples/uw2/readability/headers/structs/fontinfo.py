KIND = 'struct'; TAG = 'FontInfo'; HEADER = 'gfx.h'
TEXT = r'''
/* The current font (cur_font); the sources use only its line height. */
struct FontInfo {
    char pad0[6];
    int height;                         /* 0x06, the line height */
};
'''
OVERRIDES = {}
