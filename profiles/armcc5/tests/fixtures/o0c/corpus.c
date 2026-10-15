/* o0c's test corpus: small functions of the shapes armcc -O0 makes, compiled as one file (no
   --split_sections), so the functions sit in source order and share literal pools. */
typedef unsigned char u8;
typedef unsigned short u16;

u8 g_mode;
u16 g_count;
int g_last;
int g_table[8];

void put(int c)
{
    g_last = c;
}

int add3(int a, int b, int c)
{
    return a + b + c;
}

void clear_table(void)
{
    int i;
    for (i = 0; i < 8; i++) g_table[i] = 0;
}

int pick(int x)
{
    switch (x) {
    case 0: return 10;
    case 1: return 20;
    case 2: return 35;
    case 3: return 47;
    case 4: return 51;
    default: return -1;
    }
}

void tick(void)
{
    if (g_mode == 2) g_count++;
    else g_count = 0;
    if (g_count > 300) g_mode = 0;
}

int sum_to(int n)
{
    int s = 0;
    while (n > 0) {
        s += n;
        n--;
    }
    return s;
}

int clamp(int v, int lo, int hi)
{
    if (v < lo) return lo;
    if (v > hi) return hi;
    return v;
}

void report(int n)
{
    int i;
    for (i = 0; i < n; i++) put(pick(i) + g_table[i & 7]);
}
