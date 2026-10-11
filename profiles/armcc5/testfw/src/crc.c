/* Modbus CRC-16 with a table: a const table, a loop, a static helper. */
static const unsigned short crc_hi[16] = {
    0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
    0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400 };

static unsigned short step(unsigned short crc, unsigned char nib)
{
    return (unsigned short)(crc_hi[(crc ^ nib) & 0x0F] ^ (crc >> 4));
}

unsigned short crc16(const unsigned char *p, unsigned int n)
{
    unsigned short crc = 0xFFFF;
    while (n--) {
        crc = step(crc, *p);
        crc = step(crc, (unsigned char)(*p++ >> 4));
    }
    return crc;
}
