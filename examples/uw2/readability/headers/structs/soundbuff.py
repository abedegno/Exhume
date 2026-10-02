KIND = 'struct'; TAG = 'SoundBuff'; HEADER = 'sound.h'
TEXT = r'''
/* AIL's sound buffer, 12 bytes. */
struct SoundBuff {
    unsigned pack_type;
    unsigned sample_rate;
    char far *data;                     /* 0x04 */
    unsigned long len;                  /* 0x08 */
};
'''
OVERRIDES = {}
