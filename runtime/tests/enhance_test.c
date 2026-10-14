/* A host test of the enhancement registry (runtime/port/sys/enhance.c), not part of any build:
   cc -I runtime/tests -I runtime/port -I runtime/port/sys runtime/tests/enhance_test.c \
      runtime/port/sys/enhance.c runtime/tests/config_stub.c -o /tmp/enh && /tmp/enh */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include "enhance.h"
static const struct enhance_flag T[] = {
    {"skip-intro", "Starts at the main menu", ENH_TIMING, NULL, NULL},
    {"look", "A presentation flag", ENH_PRESENTATION, "UltimaHacks", NULL},
    {"subtitles", "UW2 only", ENH_PRESENTATION, NULL, "UW2"},
};
static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)
int main(void)
{
    uint32_t m = 0;
    char b[128];
    enhance_init(T, 3, "UW1");
    CHECK(enhance_index("look") == 1);
    CHECK(enhance_index("nope") == -1);
    CHECK(enhance_parse("skip-intro,look", &m, 1, 1) == 0 && m == 3);
    CHECK(strcmp(enhance_names(m, b, sizeof b), "skip-intro,look") == 0);
    CHECK(enhance_parse("look", &m, 0, 1) == 0 && m == 1);
    CHECK(enhance_parse("nope", &m, 1, 1) == -1 && m == 1);          /* strict: an error, mask kept */
    CHECK(enhance_parse("nope,look", &m, 1, 0) == 0 && m == 3);      /* lenient: warned, skipped */
    CHECK(enhance_parse("subtitles", &m, 1, 1) == -1);               /* the other game's */
    CHECK(enhance_kind_mask(ENH_PRESENTATION) == 2);                 /* look; subtitles is UW2's */
    CHECK(strcmp(enhance_names(0, b, sizeof b), "") == 0);
    m = 3;
    CHECK(enhance_save("home", m) == 0);
    m = 0;
    CHECK(enhance_load("home", &m) == 0 && m == 3);
    {   /* a recording's flags: format 5 names them; 6 (a test's, with cheats) may name none */
        static const char *names[] = { "look", "" };
        int v, carries;
        for (v = 5; v <= 6; v++) {
            const char *n = names[v - 5];
            unsigned char h[15] = { 'T', 'S', 'T', 'R', (unsigned char)v, 0, 0, 0, 0, 0, 0, 0, 9,
                                    (unsigned char)strlen(n), 0 };
            FILE *f = fopen("/tmp/enhance_test.rec", "wb");
            fwrite(h, 1, 15, f);
            fwrite(n, 1, strlen(n), f);
            fclose(f);
            m = 7;
            CHECK(enhance_from_recording("/tmp/enhance_test.rec", &m, &carries) == 0 && carries == 1
                  && m == (v == 5 ? 2u : 0u));
        }
        remove("/tmp/enhance_test.rec");
    }
    printf(fails ? "enhance_test: %d failed\n" : "enhance_test: all passed\n", fails);
    return fails != 0;
}
