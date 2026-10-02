KIND = 'struct'; TAG = 'Tile'; HEADER = 'map.h'
TEXT = r'''
/* A square of the level map, 4 bytes; the map (mapdata) is 64 by 64 of them. UW-Formats
   (4.2, the tilemap) documents the fields. */
struct Tile {
    unsigned type:4;                    /* 0x00: solid, open, the diagonals and slopes */
    unsigned height:4;
    unsigned light:2;                   /* 0x01 */
    unsigned floor:4;                   /* the floor texture */
    unsigned door:2;
    union Link objects;                 /* 0x02, the head of the tile's object list; its
                                           low six bits are the wall texture */
};
'''
OVERRIDES = {}
