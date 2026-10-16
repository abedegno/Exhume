/* Synthetic test source for the ti-c2000 profile: start-up and the main loop. */
extern unsigned int ram_code_load, ram_code_size, ram_code_run;
extern unsigned int checksum(const unsigned int *p, unsigned int n);
extern long fast_sum(const int *p, unsigned int n);
extern unsigned int ctl_mode(unsigned int cmd);

int samples[4] = { 1, -2, 3, -4 };

void copy_words(unsigned int *dst, const unsigned int *src, unsigned long n)
{
    while (n--) *dst++ = *src++;
}

long acc;
unsigned int sum;

void main(void)
{
    copy_words(&ram_code_run, &ram_code_load, (unsigned long)&ram_code_size);
    ctl_mode(2);
    for (;;) {
        acc = fast_sum(samples, 4);
        sum = checksum((const unsigned int *)samples, 4);
    }
}
