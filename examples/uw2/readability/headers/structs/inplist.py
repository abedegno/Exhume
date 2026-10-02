KIND = 'struct'; TAG = 'Inplist'; HEADER = 'ui.h'
TEXT = r'''
/* The mouse and keyboard state handed to an input handler, 10 bytes (seg010's dispatcher
   fills it in; reached through the near pointer inplist). */
struct Inplist {
    int x, y;                           /* relative to the region that took the click */
    int mouse;                          /* 0x04: 1 for a mouse event, 0 for a key */
    int cmd;                            /* 0x06: the input code, the buttons for a mouse event */
    int mode;                           /* 0x08: mask of the screen modes */
};
'''
OVERRIDES = {'OVR123.C': {'buttons': 'cmd'}, 'OVR158.C': {'buttons': 'cmd'}}
