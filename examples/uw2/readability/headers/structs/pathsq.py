KIND = 'struct'; TAG = 'PathSq'; HEADER = 'critter.h'
TEXT = r'''
/* One square of a critter's path, 4 bytes. */
struct PathSq { unsigned char x, y, unused, flag; };
'''
OVERRIDES = {}
