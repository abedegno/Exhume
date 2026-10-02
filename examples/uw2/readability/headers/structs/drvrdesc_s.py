KIND = 'struct'; TAG = 'DrvrDesc'; HEADER = 'sound.h'
TEXT = r'''
/* AIL's description of a driver. */
struct DrvrDesc {
    unsigned min_api;
    unsigned drvr_type;                 /* 0x02: 2 digital, 3 XMIDI */
    char data_suffix[4];                /* 0x04 */
    char far *dev_names;                /* 0x08 */
    int io, irq, dma, drq;              /* 0x0C */
};
'''
OVERRIDES = {}
