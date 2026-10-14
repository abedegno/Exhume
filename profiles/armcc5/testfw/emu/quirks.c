/* Code whose behaviour depends on the compiler or the layout, for the differential tests. Each
   is a pattern a decompiled firmware really has; each comment says what a second compiler
   shows. */

/* armcc's __packed before the struct keyword. clang ignores an attribute there, so without the
   open build's rewrite the struct is unpacked: a at 2, b at 4, seven bytes become eight. */
typedef __packed struct { unsigned char tag; unsigned short a; unsigned int b; } Rec;
Rec g_rec;

void rec_set(unsigned short a, unsigned int b)
{
    g_rec.tag = 0x5A; g_rec.a = a; g_rec.b = b;
}

/* armcc gives an enum the smallest type that holds it (one byte here); clang an int, unless
   -fshort-enums: then level moves from 1 to 4. */
typedef enum { MODE_OFF, MODE_ON, MODE_AUTO } Mode;
typedef struct { Mode mode; unsigned char level; } Setting;
Setting g_setting;

void setting_set(unsigned int level)
{
    g_setting.mode = MODE_AUTO; g_setting.level = (unsigned char)level;
}

/* a 32-bit view of a 16-bit variable reads its neighbour too: whatever the original placed
   after it (armcc puts a file's small zero-initialised data after its initialised data, in
   .data, so here g_setting). An ordinary open build puts something else there; one in the
   original's RAM layout gets it right. */
unsigned short g_count = 0x2222;

unsigned int count_and_next(void) { return *(unsigned int *)&g_count; }

/* a loop that can run past its table: above the last limit it reads whatever the build placed
   after the table, so two builds may disagree there (a firmware bug, not a build bug) */
static const unsigned short limits[4] = { 10, 50, 100, 200 };
static const unsigned char rates[4] = { 1, 2, 4, 8 };

unsigned char rate_for(unsigned short amps)
{
    unsigned int i = 0;
    while (amps > limits[i]) i++;
    return rates[i];
}

/* a variable left unset on one path: each build returns its own leftover register or stack
   (a firmware bug: harmless only while the input is valid BCD) */
unsigned char bcd_next(unsigned char x)
{
    unsigned char v;
    if ((x & 0x0F) < 10 && (x >> 4) < 10) v = (unsigned char)((x >> 4) * 10 + (x & 0x0F));
    return (unsigned char)(v + 1);
}
