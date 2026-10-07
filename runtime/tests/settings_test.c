/* A host test of the settings screen's model, drawing and input (runtime/port/ui/settings.c), not
   part of any build:
   cc -I runtime/tests -I runtime/port -I runtime/port/sys -I runtime/port/ui runtime/tests/settings_test.c \
      runtime/port/ui/settings.c runtime/port/ui/font.c runtime/port/sys/enhance.c runtime/tests/config_stub.c \
      -o /tmp/set && /tmp/set */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include "settings.h"
#include "enhance.h"
int port_config_get(const char *home, const char *key, char *out, size_t n);
int port_config_set(const char *home, const char *key, const char *value);
static int applied = -1;
static void apply_vol(int v) { applied = v; }
static const char *const cards[] = { "None", "Sound Blaster", "MT-32", NULL };
static const char *const cards_stored[] = { "0", "3", "6", NULL };
static const struct setting T[] = {
    { SET_TAB_SOUND, "Music", SET_CYCLE, "music-test", cards, cards_stored, 0, 0, 0, 1, 1, NULL, NULL, NULL, NULL },
    { SET_TAB_SOUND, "Volume", SET_SLIDER, "volume", NULL, NULL, 0, 100, 10, 100, 0, apply_vol, NULL, NULL, NULL },
};
static const struct enhance_flag F[] = { { "skip-intro", "Starts at the main menu", ENH_TIMING, NULL, NULL } };
static int fails;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); fails++; } else printf("ok   %s\n", m); } while (0)
int main(void)
{
    static uint32_t px[SET_W * SET_H];
    char v[64];
    int i, lit = 0;
    enhance_init(F, 1, "UW1");
    port_config_set("h", "volume", "abc");                       /* a bad value: the default */
    settings_init("h", T, 2, "Test");
    CHECK(settings_value(1) == 100, "a bad volume reads as the default");
    settings_show(1);
    CHECK(settings_open(), "shown");
    CHECK(settings_draw(px), "drawn while shown");
    for (i = 0; i < SET_W * SET_H; i++) lit += px[i] != px[0];
    CHECK(lit > 1000, "the layer has text on it");
    settings_key(SET_KEY_ENTER);                                 /* row 0: Music, cycle on */
    CHECK(port_config_get("h", "music-test", v, sizeof v) == 0 && !strcmp(v, "6"), "Enter cycles Music to MT-32, saved");
    settings_key(SET_KEY_DOWN);
    settings_key(SET_KEY_LEFT);                                  /* Volume 100 -> 90 */
    CHECK(applied == 90, "a live slider applies at once");
    CHECK(port_config_get("h", "volume", v, sizeof v) == 0 && !strcmp(v, "90"), "and is saved");
    settings_key(SET_KEY_TAB); settings_key(SET_KEY_TAB); settings_key(SET_KEY_TAB);   /* to Enhancements */
    settings_key(SET_KEY_ENTER);
    CHECK(port_config_get("h", "enhance", v, sizeof v) == 0 && strstr(v, "skip-intro"), "an enhancement is saved");
    CHECK(enhance_on == 0, "but not turned on in this run");
    settings_key(SET_KEY_ESC);
    CHECK(!settings_open(), "Esc closes");
    CHECK(!settings_draw(px), "nothing drawn when closed");
    printf("%d failed\n", fails);
    return fails != 0;
}
