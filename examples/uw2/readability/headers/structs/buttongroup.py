KIND = 'struct'; TAG = 'buttongroup'; HEADER = 'ui.h'
TEXT = r'''
/* A button group of the options panel: an init function, the art row for its pictures
   (or -1), and for each of its seven buttons a handler, the handler's argument and a
   group to go to next. */
struct buttongroup {
    void (far *init)(void);             /* 0x00 */
    int images;                         /* 0x04 */
    void (far *fn[7])(int);             /* 0x06 */
    int arg[7];                         /* 0x22 */
    struct buttongroup *sub[7];         /* 0x30 */
};
'''
OVERRIDES = {}
