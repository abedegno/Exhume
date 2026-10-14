/* RW data big enough for armlink to compress: zero runs, repeats and plain bytes. */
unsigned char g_tbl[256] = {
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 0, 1, 2, 3, 4, 5,
    0, 7, 1, 8, 2, 9, 3, 10, 4, 11, 5, 12, 6, 0, 7, 1, 8, 2, 9, 3, 10, 4, 11, 5, 12, 6, 0, 7, 1, 8, 2, 9 };
unsigned short g_cfg[40] = { 1000, 1001, 1002, 1003, 1004, 1005, 1006, 1007, 1008, 1009,
                             1010, 1011, 1012, 1013, 1014, 1015, 1016, 1017, 1018, 1019 };
unsigned char g_mixed[48] = { 0x55, 0, 0, 0, 0xAA, 0x55, 0, 0, 0, 0xAA, 0x55, 0, 0, 0, 0xAA, 0x31,
                              0x41, 0x59, 0x26, 0x53, 0x58, 0x97, 0x93, 0x23, 0x84, 0x62, 0x64, 0x33 };

unsigned char g_buf[64];           /* over armcc's 8-byte --bss_threshold: zero-init RAM */

unsigned char *tbl(void) { return g_tbl; }
unsigned short *cfg(void) { return g_cfg; }
unsigned char *mixed(void) { return g_mixed; }
unsigned char *buf(void) { return g_buf; }

/* a string and a table reached with adr: armcc can keep these beside the code that uses them */
const char *version(void) { return "TestFW build 7"; }
unsigned short lookup(unsigned int i)
{
    static const unsigned short steps[8] = { 3, 5, 8, 13, 21, 34, 55, 89 };
    return steps[i & 7];
}

/* a switch with cases too far apart for tbb: armcc reaches them through b trampolines */
unsigned short g_acc[16];
void big_switch(unsigned int k, unsigned short v)
{
    switch (k) {
    case 0: g_acc[0] += (unsigned short)(v * 3 + 0); g_acc[1] += (unsigned short)(v * 4 + 1); g_acc[2] += (unsigned short)(v * 5 + 2); g_acc[3] += (unsigned short)(v * 6 + 3); g_acc[4] += (unsigned short)(v * 7 + 4); g_acc[5] += (unsigned short)(v * 8 + 5); break;
    case 1: g_acc[3] += (unsigned short)(v * 4 + 7); g_acc[4] += (unsigned short)(v * 5 + 8); g_acc[5] += (unsigned short)(v * 6 + 9); g_acc[6] += (unsigned short)(v * 7 + 10); g_acc[7] += (unsigned short)(v * 8 + 11); g_acc[8] += (unsigned short)(v * 9 + 12); break;
    case 2: g_acc[6] += (unsigned short)(v * 5 + 14); g_acc[7] += (unsigned short)(v * 6 + 15); g_acc[8] += (unsigned short)(v * 7 + 16); g_acc[9] += (unsigned short)(v * 8 + 17); g_acc[10] += (unsigned short)(v * 9 + 18); g_acc[11] += (unsigned short)(v * 10 + 19); break;
    case 3: g_acc[9] += (unsigned short)(v * 6 + 21); g_acc[10] += (unsigned short)(v * 7 + 22); g_acc[11] += (unsigned short)(v * 8 + 23); g_acc[12] += (unsigned short)(v * 9 + 24); g_acc[13] += (unsigned short)(v * 10 + 25); g_acc[14] += (unsigned short)(v * 11 + 26); break;
    case 4: g_acc[12] += (unsigned short)(v * 7 + 28); g_acc[13] += (unsigned short)(v * 8 + 29); g_acc[14] += (unsigned short)(v * 9 + 30); g_acc[15] += (unsigned short)(v * 10 + 31); g_acc[0] += (unsigned short)(v * 11 + 32); g_acc[1] += (unsigned short)(v * 12 + 33); break;
    case 5: g_acc[15] += (unsigned short)(v * 8 + 35); g_acc[0] += (unsigned short)(v * 9 + 36); g_acc[1] += (unsigned short)(v * 10 + 37); g_acc[2] += (unsigned short)(v * 11 + 38); g_acc[3] += (unsigned short)(v * 12 + 39); g_acc[4] += (unsigned short)(v * 13 + 40); break;
    case 6: g_acc[2] += (unsigned short)(v * 9 + 42); g_acc[3] += (unsigned short)(v * 10 + 43); g_acc[4] += (unsigned short)(v * 11 + 44); g_acc[5] += (unsigned short)(v * 12 + 45); g_acc[6] += (unsigned short)(v * 13 + 46); g_acc[7] += (unsigned short)(v * 14 + 47); break;
    case 7: g_acc[5] += (unsigned short)(v * 10 + 49); g_acc[6] += (unsigned short)(v * 11 + 50); g_acc[7] += (unsigned short)(v * 12 + 51); g_acc[8] += (unsigned short)(v * 13 + 52); g_acc[9] += (unsigned short)(v * 14 + 53); g_acc[10] += (unsigned short)(v * 15 + 54); break;
    case 8: g_acc[8] += (unsigned short)(v * 11 + 56); g_acc[9] += (unsigned short)(v * 12 + 57); g_acc[10] += (unsigned short)(v * 13 + 58); g_acc[11] += (unsigned short)(v * 14 + 59); g_acc[12] += (unsigned short)(v * 15 + 60); g_acc[13] += (unsigned short)(v * 16 + 61); break;
    case 9: g_acc[11] += (unsigned short)(v * 12 + 63); g_acc[12] += (unsigned short)(v * 13 + 64); g_acc[13] += (unsigned short)(v * 14 + 65); g_acc[14] += (unsigned short)(v * 15 + 66); g_acc[15] += (unsigned short)(v * 16 + 67); g_acc[0] += (unsigned short)(v * 17 + 68); break;
    case 10: g_acc[14] += (unsigned short)(v * 13 + 70); g_acc[15] += (unsigned short)(v * 14 + 71); g_acc[0] += (unsigned short)(v * 15 + 72); g_acc[1] += (unsigned short)(v * 16 + 73); g_acc[2] += (unsigned short)(v * 17 + 74); g_acc[3] += (unsigned short)(v * 18 + 75); break;
    case 11: g_acc[1] += (unsigned short)(v * 14 + 77); g_acc[2] += (unsigned short)(v * 15 + 78); g_acc[3] += (unsigned short)(v * 16 + 79); g_acc[4] += (unsigned short)(v * 17 + 80); g_acc[5] += (unsigned short)(v * 18 + 81); g_acc[6] += (unsigned short)(v * 19 + 82); break;
    case 12: g_acc[4] += (unsigned short)(v * 15 + 84); g_acc[5] += (unsigned short)(v * 16 + 85); g_acc[6] += (unsigned short)(v * 17 + 86); g_acc[7] += (unsigned short)(v * 18 + 87); g_acc[8] += (unsigned short)(v * 19 + 88); g_acc[9] += (unsigned short)(v * 20 + 89); break;
    case 13: g_acc[7] += (unsigned short)(v * 16 + 91); g_acc[8] += (unsigned short)(v * 17 + 92); g_acc[9] += (unsigned short)(v * 18 + 93); g_acc[10] += (unsigned short)(v * 19 + 94); g_acc[11] += (unsigned short)(v * 20 + 95); g_acc[12] += (unsigned short)(v * 21 + 96); break;
    default: g_acc[0] = 0; break;
    }
}
