KIND = 'struct'; TAG = 'CutsState'; HEADER = 'gfx.h'
TEXT = r'''
/* The state of a cutscene, on show_anm's stack, 0x5C bytes. */
struct CutsState {
    char name[0x13];                    /* CUTS\csXXX.nXX */
    int x, y, w, h;                     /* 0x13, the window; 320 by 200 for full screen */
    unsigned char windowed;             /* 0x1B */
    unsigned char far *palette;         /* 0x1C */
    char far *text_lines[6];            /* 0x20 */
    unsigned char color38;              /* 0x38 */
    int flag39;                         /* 0x39, subtitle lines to draw */
    int frame3B, frame3D;               /* 0x3B */
    unsigned frame3F;                   /* 0x3F, pause length */
    unsigned repeat41;                  /* 0x41, loops left */
    int repeat43;                       /* 0x43 */
    int fade45, fade47, fade49;         /* 0x45, speech playing, fade in, fade out */
    int file4B, file4D;                 /* 0x4B */
    int vscr4F;                         /* 0x4F */
    int panx51, pany53, dir55, step57, remaining59; /* 0x51 */
    union {
        unsigned char value;
        struct { unsigned b0:1, b1:1, b2:1, b3:1, b4:1, b5:1, b6:1, b7:1; } bit;
    } flags;                            /* 0x5B */
};
'''
OVERRIDES = {}
