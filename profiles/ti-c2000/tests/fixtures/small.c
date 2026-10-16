/* The source of ofd_small.xml and ofd_small.words (ofd2000 of this file compiled with the profile's options). */
extern void notify(unsigned int code);
unsigned int count = 5;
float gain;
const unsigned int steps[3] = { 1, 2, 4 };
static unsigned int last;
void bump(unsigned int k)
{
    count += steps[k & 1];
    last = count;
    if (count > 100) notify(last);
}
