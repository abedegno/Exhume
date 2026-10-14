/* A host test of the cheats (runtime/port/sys/cheat.c), not part of any build:
   cc -I runtime/tests -I runtime/port/sys runtime/tests/cheat_test.c runtime/port/sys/cheat.c \
      -o /tmp/cheat && /tmp/cheat */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include "cheat.h"

static int fails, ran, ready = 1;
static char last[128];
static uint32_t put_at[8];
static char put_text[8][128];
static int nput;
#define CHECK(c) do { if (!(c)) { printf("FAIL %s:%d %s\n", __FILE__, __LINE__, #c); fails++; } } while (0)

/* replay.c's side: what a recording would get */
void rp_cheat_put(uint32_t poll, const char *text)
{
    put_at[nput] = poll;
    snprintf(put_text[nput++], sizeof put_text[0], "%s", text);
}

static const char *c_set(int argc, char *argv[], int run)
{
    long v;
    if (argc != 2 || cheat_num(argv[1], 0, 99, &v)) return "a field and a value 0 to 99";
    if (run) {
        ran++;
        snprintf(last, sizeof last, "%s=%ld", argv[0], v);
    }
    return NULL;
}

static int is_ready(void) { return ready; }

static const struct cheat_cmd cmds[] = { { "set", "FIELD VALUE", c_set } };
static const struct cheat_game game = { cmds, 1, is_ready };

static int replay(void)
{
    cheat_init(&game);
    unsetenv("EXHUME_TEST_CHEATS");
    CHECK(cheat_from_recording("/tmp/cheat_test.rec") == -1);   /* a replay needs the variable too */
    setenv("EXHUME_TEST_CHEATS", "1", 1);
    CHECK(cheat_from_recording("/tmp/cheat_test.rec") == 0);
    CHECK(cheat_scripted() == 0);                               /* a replay's cheats are not recorded again */
    ready = 0;                                                  /* a replay runs them where they ran */
    port_cheat_point();
    CHECK(ran == 0);
    port_cheat_point();                                         /* safe point 2 */
    CHECK(ran == 1 && !strcmp(last, "str=9"));
    port_cheat_point();
    CHECK(ran == 2 && !strcmp(last, "int=4") && nput == 0);
    return fails != 0;
}

static const char *argv0;

int main(int argc, char *argv[])
{
    long v;
    int i;
    FILE *f;
    char cmd[512];
    argv0 = argv[0];
    if (argc > 1) return replay();
    CHECK(cheat_num("12", 0, 20, &v) == 0 && v == 12);
    CHECK(cheat_num("0xA0", 0, 0x1FF, &v) == 0 && v == 0xA0);
    CHECK(cheat_num("0x", 0, 0x1FF, &v) == -1);
    CHECK(cheat_num("12x", 0, 20, &v) == -1);
    CHECK(cheat_num("21", 0, 20, &v) == -1);
    CHECK(cheat_num("-3", -5, 5, &v) == 0 && v == -3);
    CHECK(cheat_num("0x-3", -5, 5, &v) == -1);
    cheat_init(&game);
    unsetenv("EXHUME_TEST_CHEATS");
    CHECK(!cheat_allowed());
    CHECK(cheat_add("set str 10") != NULL);                     /* refused without the variable */
    CHECK(cheat_scripted() == 0);
    setenv("EXHUME_TEST_CHEATS", "1", 1);
    CHECK(cheat_allowed());
    CHECK(cheat_add("fly 3") != NULL && strstr(cheat_add("fly 3"), "set"));   /* lists the commands */
    CHECK(cheat_add("set str") != NULL);                        /* the game's check */
    CHECK(cheat_add("set str 10\n") == NULL);
    CHECK(cheat_add("  set   dex   7  ") == NULL);
    CHECK(cheat_scripted() == 2);
    port_cheat_point();                                         /* nothing due yet */
    CHECK(ran == 0);
    cheat_release();
    ready = 0;
    port_cheat_point();                                         /* due, but not ready */
    CHECK(ran == 0);
    ready = 1;
    port_cheat_point();                                         /* safe point 3 */
    CHECK(ran == 1 && !strcmp(last, "str=10") && nput == 1 && put_at[0] == 3 && !strcmp(put_text[0], "set str 10"));
    cheat_release();
    port_cheat_point();
    CHECK(ran == 2 && !strcmp(last, "dex=7") && put_at[1] == 4 && !strcmp(put_text[1], "set dex 7"));
    /* a format 6 recording's cheats, read back and run at their safe points, in a process of
       its own (cheat.c holds one session's cheats) */
    f = fopen("/tmp/cheat_test.rec", "wb");
    fwrite("TSTR\6\0\0\0\0\0\0\0", 1, 12, f);
    fwrite("\x09\0\0", 1, 3, f);                              /* the enhancements: none */
    fwrite("\x01\x02\0\xAA\xBB", 1, 5, f);                    /* a TIME chunk, skipped */
    for (i = 0; i < 2; i++) {
        const char *t = i ? "set int 4" : "set str 9";
        unsigned char h[7] = { 10, (unsigned char)(4 + strlen(t)), 0, (unsigned char)(2 + i), 0, 0, 0 };
        fwrite(h, 1, 7, f);
        fwrite(t, 1, strlen(t), f);
    }
    fclose(f);
    snprintf(cmd, sizeof cmd, "%s replay", argv0);
    CHECK(system(cmd) == 0);
    printf("%s\n", fails ? "cheat_test: FAILED" : "cheat_test: all pass");
    remove("/tmp/cheat_test.rec");
    return fails != 0;
}
