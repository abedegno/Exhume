KIND = 'struct'; TAG = 'Scroll'; HEADER = 'ui.h'
TEXT = r'''
/* A message scroll's state. Field names beyond the ones the code clearly uses (coordinates,
   cursor position, the font colour) are our own; FM Towns has no per-field names, only
   the struct's three instances (_main_scroll, _npc_scroll, _menu_scroll). Byte-packed,
   21 (0x15) bytes, matching the spacing between those three instances in the EXE. */
struct Scroll {
    int x0;                             /* 0x00 */
    int y0;                             /* 0x02 */
    int top;                            /* 0x04 */
    int bottom;                         /* 0x06 */
    int cur_x;                          /* 0x08 */
    int cur_y;                          /* 0x0A */
    int left;                           /* 0x0C */
    int last_y;                         /* 0x0E */
    unsigned char more_pending;         /* 0x10 */
    int start_line;                     /* 0x11 */
    int font_color;                     /* 0x13 */
};
'''
OVERRIDES = {}
