/* A frame state machine: globals, a switch, a string, a divide by 10. */
/* dropped: g_proto_name */
extern unsigned short crc16(const unsigned char *p, unsigned int n);

typedef struct { unsigned char step; unsigned short t1, t2, t3; } Timers;
Timers g_timers;
unsigned char g_frame[8] = { 0x01, 0x03, 0x00, 0x00, 0x00, 0x0A, 0, 0 };
const char g_proto_name[] = "Test CAN Protocol";
unsigned short g_soc_tenths = 955;

void timers_tick(void)
{
    if (g_timers.t1 < 0xFFFF) g_timers.t1++;
    if (g_timers.t2 < 0xFFFF) g_timers.t2++;
    if (g_timers.t3 < 0xFFFF) g_timers.t3++;
}

unsigned char soc_percent(void)
{
    return (unsigned char)(g_soc_tenths / 10);
}

int next_frame(unsigned char *out)
{
    unsigned short crc;
    switch (g_timers.step) {
    case 0: out[0] = 0x11; break;
    case 1: out[0] = soc_percent(); break;
    case 2: out[0] = g_frame[1]; break;
    case 3: out[0] = (unsigned char)g_timers.t1; break;
    case 4: out[0] = (unsigned char)(g_timers.t2 >> 8); break;
    case 5: out[0] = g_proto_name[0]; break;
    case 6: crc = crc16(g_frame, 6); out[0] = (unsigned char)crc; out[1] = (unsigned char)(crc >> 8); break;
    default: g_timers.step = 0; return 0;
    }
    g_timers.step++;
    return 1;
}
