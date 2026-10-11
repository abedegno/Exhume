/* Entry point: calls across files. */
extern void timers_tick(void);
extern int next_frame(unsigned char *out);

unsigned char g_out[2];
unsigned int g_sent;

int main(void)
{
    for (;;) {
        timers_tick();
        if (next_frame(g_out)) g_sent++;
    }
}
