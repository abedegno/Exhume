KIND = 'struct'; TAG = 'Font'; HEADER = 'x'; EMIT = False; RETAG = 'FontInfo'
TEXT = r'''
/* The current font (cur_font); the sources use only its line height. */
struct Font {
    char pad0[6];
    int height;                         /* 0x06, the line height */
};
'''
OVERRIDES = {}
