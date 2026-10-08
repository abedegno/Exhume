/* A host test of the EMS emulation (runtime/port/mem/emm.c), not part of any build:
   cc -std=c99 -O2 -Wall -Wextra -I runtime/tests -I runtime/port runtime/tests/emm_test.c runtime/port/mem/emm.c -o /tmp/emm && /tmp/emm
   What a page holds survives mapping it again, into its own slot or another, and a page in two
   slots stays one page; and mapping a slot's own page again costs nothing (UW1's cutscene reader
   does it some eighty million times in the intro, each once a 32 KB copy). */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include "port.h"

/* emm.c's needs from the rest of the port */
static unsigned char frame_mem[0x20000];
unsigned char *port_frame_alloc(void) { return frame_mem; }
void pm_add(const char *name, void *p, size_t n, unsigned seg) { (void)name; (void)p; (void)n; (void)seg; }
void pm_remove(void *p) { (void)p; }
void port_log(const char *fmt, ...) { (void)fmt; }
void port_fatal(const char *fmt, ...) { fprintf(stderr, "fatal: %s\n", fmt); exit(1); }

static int fails;
#define CHECK(c) do { if (!(c)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
#define SLOT(s) (frame_mem + (s) * 0x4000)

int main(void)
{
    int i;
    clock_t t0;
    double secs;
    CHECK(emm_open(8, 8, 8, 0xE000) == 8);

    /* a page written in its slot, mapped again into the same slot: still there, and kept when
       the slot moves on to another page and comes back */
    CHECK(emm_map(0, 3));
    memset(SLOT(0), 0xA5, 0x4000);
    CHECK(emm_map(0, 3));
    CHECK(SLOT(0)[0] == 0xA5 && SLOT(0)[0x3FFF] == 0xA5);
    SLOT(0)[100] = 0x11;
    CHECK(emm_map(0, 3));
    CHECK(emm_map(0, 4));
    CHECK(SLOT(0)[100] == 0);
    CHECK(emm_map(0, 3));
    CHECK(SLOT(0)[100] == 0x11 && SLOT(0)[0] == 0xA5);

    /* one page in two slots: a write through one, then that slot mapped again, shows in the other */
    CHECK(emm_map(1, 3));
    SLOT(1)[7] = 0x77;
    CHECK(emm_map(1, 3));
    CHECK(SLOT(0)[7] == 0x77);
    CHECK(emm_map(0, 5));
    CHECK(emm_map(1, 5));
    CHECK(emm_map(2, 3));
    CHECK(SLOT(2)[7] == 0x77 && SLOT(2)[100] == 0x11);

    /* the cost: a million maps of a slot's own page; as copies they move 32 GB (a third of a second
       on a fast machine, minutes on some), as no-ops next to nothing */
    t0 = clock();
    for (i = 0; i < 1000000; i++) emm_map(3, 6);
    secs = (double)(clock() - t0) / CLOCKS_PER_SEC;
    CHECK(secs < 0.05);         /* a no-op loop takes about a millisecond */
    printf("a million maps of the same page: %.3f s\n", secs);

    emm_close();
    if (fails) { printf("emm_test: %d failed\n", fails); return 1; }
    printf("emm_test: all passed\n");
    return 0;
}
