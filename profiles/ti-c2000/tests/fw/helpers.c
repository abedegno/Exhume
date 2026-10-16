/* Synthetic test source for the ti-c2000 profile: helpers, a ramfunc and an interrupt. */
#pragma CODE_SECTION(fast_sum, "ramfuncs")

extern float level;
extern unsigned int ticks;
extern void ctl_tick(void);

const unsigned int table[4] = { 0x1021, 0x2042, 0x4084, 0x8108 };
unsigned int isr_count;
float ratio;

unsigned int checksum(const unsigned int *p, unsigned int n)
{
    unsigned int s = 0;
    while (n--) s ^= *p++ + table[s & 3];
    return s;
}

long fast_sum(const int *p, unsigned int n)
{
    long s = 0;
    unsigned int k;
    for (k = 0; k < n; k++) s += p[k];
    return s;
}

float scale(float a, float b)
{
    if (b == 0.0f) return 0.0f;
    return a / b;
}

interrupt void timer_isr(void)
{
    isr_count++;
    ctl_tick();
    ratio = scale(level, (float)ticks);
}
