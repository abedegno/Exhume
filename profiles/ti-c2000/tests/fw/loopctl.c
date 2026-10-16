/* Synthetic test source for the ti-c2000 profile: a PI loop and a few counters. */
typedef struct { float kp, ki, i, out, lo, hi; } pi_t;

unsigned int mode;
unsigned int ticks = 3;
long total;
float level = 0.25f;
unsigned int hist[8];
pi_t loop = { 0.5f, 0.01f, 0.0f, 0.0f, -10.0f, 10.0f };
unsigned int flags;
float target;

float loop_step(pi_t *p, float e)
{
    float u;
    p->i += p->ki * e;
    u = p->kp * e + p->i;
    if (u > p->hi) u = p->hi;
    else if (u < p->lo) u = p->lo;
    p->out = u;
    return u;
}

void ctl_tick(void)
{
    ticks++;
    total += ticks;
    hist[ticks & 7] = mode;
    if (ticks >= 100) {
        ticks = 0;
        flags |= 1;
    }
    level = loop_step(&loop, target - level);
}

unsigned int ctl_mode(unsigned int cmd)
{
    switch (cmd) {
    case 0: mode = 4; break;
    case 1: mode = 7; break;
    case 2: mode = 1; flags = 0; break;
    case 3: mode = 9; break;
    case 4: mode = 2; break;
    case 5: mode = 3; break;
    case 6: mode = 11; break;
    case 7: mode = 5; break;
    case 8: mode = 6; break;
    case 9: mode = 8; break;
    case 10: mode = 12; break;
    case 11: mode = 13; break;
    default: mode = 0; break;
    }
    return mode;
}
