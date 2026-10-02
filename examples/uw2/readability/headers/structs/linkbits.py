KIND = 'union'; TAG = 'LinkBits'; HEADER = 'uw2.h'; EMIT = False; RETAG = 'Link'
TEXT = r'''
union LinkBits {
    unsigned word;
    struct { unsigned low:6, index:10; } f;
};
'''
OVERRIDES = {}
