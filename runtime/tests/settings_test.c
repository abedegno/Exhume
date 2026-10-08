/* A host test of the settings screen's model, drawing and input (runtime/port/ui/settings.c), not
   part of any build:
   cc -std=c99 -Wall -Wextra -I runtime/tests -I runtime/port -I runtime/port/sys -I runtime/port/ui runtime/tests/settings_test.c \
      runtime/port/ui/settings.c runtime/port/ui/font.c runtime/port/sys/enhance.c runtime/tests/config_stub.c \
      -o /tmp/set && /tmp/set */
#include <stdio.h>
#include <string.h>
#include <stdint.h>
#include "settings.h"
#include "enhance.h"
int port_config_get(const char *home, const char *key, char *out, size_t n);
int port_config_set(const char *home, const char *key, const char *value);
static int applied = -1, applies;
static void apply_vol(int v) { applied = v; applies++; }
static const char *const cards[] = { "None", "Sound Blaster", "MT-32", NULL };
static const char *const cards_stored[] = { "0", "3", "6", NULL };
static const struct setting T[] = {
    { SET_TAB_SOUND, "Music", SET_CYCLE, "music-test", cards, cards_stored, 0, 0, 0, 1, 1, NULL, NULL, NULL, NULL, NULL, NULL, NULL },
    { SET_TAB_SOUND, "Volume", SET_SLIDER, "volume", NULL, NULL, 0, 100, 10, 100, 0, apply_vol, NULL, NULL, NULL, NULL, NULL, NULL },
};
static const struct enhance_flag F[] = {
    { "skip-intro", "Starts at the main menu", ENH_TIMING, NULL, NULL },
    { "free-heading", "Sliding along a wall no longer turns your view", ENH_GAMEPLAY, "UltimaHacks", NULL },
};
/* the games' Window scale row (settab.c): a cycle, 1x to 8x, the file's text the number */
static const char *const scale_names[] = { "1x", "2x", "3x", "4x", "5x", "6x", "7x", "8x", NULL };
static const char *const scale_stored[] = { "1", "2", "3", "4", "5", "6", "7", "8", NULL };
static const struct setting S[] = {
    { SET_TAB_DISPLAY, "Window scale", SET_CYCLE, "scale", scale_names, scale_stored, 0, 0, 0, 2, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL },
};
/* settings.c's layout: the rows start at y 72, 20 high; a slider's track starts at x 248, 160 wide */
#define ROW_Y 72
#define ROW_H 20
#define VALUE_X 248
#define LABEL_X 24
static int fails;
#define CHECK(c, m) do { if (!(c)) { printf("FAIL %s\n", m); fails++; } else printf("ok   %s\n", m); } while (0)
static int get_bad(void) { return -1; }
static int get_big(void) { return 150; }
static int get_two(void) { return 2; }
static const struct setting G[] = {
    { SET_TAB_SOUND, "Card", SET_CYCLE, NULL, cards, NULL, 0, 0, 0, 1, 0, NULL, get_bad, NULL, NULL, NULL, NULL, NULL },
    { SET_TAB_SOUND, "Level", SET_SLIDER, NULL, NULL, NULL, 0, 100, 10, 50, 0, NULL, get_big, NULL, NULL, NULL, NULL, NULL },
    { SET_TAB_SOUND, "Flag", SET_BOOL, NULL, NULL, NULL, 0, 0, 0, 1, 0, NULL, get_two, NULL, NULL, NULL, NULL, NULL },
};
/* a row on each of the first three tabs, for walking the tab bar */
static const struct setting K[] = {
    { SET_TAB_SOUND, "A", SET_BOOL, NULL, NULL, NULL, 0, 0, 0, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL },
    { SET_TAB_CONTROLS, "B", SET_BOOL, NULL, NULL, NULL, 0, 0, 0, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL },
    { SET_TAB_DISPLAY, "C", SET_BOOL, NULL, NULL, NULL, 0, 0, 0, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL },
};
/* a folder row under a bool, both on Sound: the folder row unselected (row 1) */
static const struct setting P[] = {
    { SET_TAB_SOUND, "A", SET_BOOL, NULL, NULL, NULL, 0, 0, 0, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL },
    { SET_TAB_SOUND, "Folder", SET_FOLDER, "folder-test", NULL, NULL, 0, 0, 0, 0, 0, NULL, NULL, NULL, NULL, NULL, NULL, NULL },
};
static int three(void) { return 3; }
static const struct setting L[] = {
    { SET_TAB_DISPLAY, "Window scale", SET_CYCLE, "scale", scale_names, scale_stored, 0, 0, 0, 2, 0, NULL, NULL, NULL, NULL, NULL, NULL, three },
};
int main(void)
{
    static uint32_t px[SET_W * SET_H];
    char v[64];
    int i, lit = 0;
    enhance_init(F, 2, "UW1");
    port_config_set("h", "volume", "abc");                       /* a bad value: the default */
    settings_init("h", T, 2, "Test");
    CHECK(settings_value(1) == 100, "a bad volume reads as the default");
    settings_set_value(1, 40);
    CHECK(settings_value(1) == 40, "settings_set_value sets the current value");
    settings_set_value(1, 100);
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
    /* the enhancement rows follow the table's two: a gameplay one has its mark and its credit */
    CHECK(settings_note(3) && !strcmp(settings_note(3), "changes play"), "a gameplay enhancement's row is marked \"changes play\"");
    CHECK(settings_note(2) && !strcmp(settings_note(2), "Restart to apply"), "a changed restart row says \"Restart to apply\"");
    settings_key(SET_KEY_ENTER);                                 /* back to the value the run started with */
    CHECK(settings_note(2) == NULL, "a restart row changed back to its starting value has no note");
    settings_key(SET_KEY_ENTER);
    CHECK(settings_note(2) && !strcmp(settings_note(2), "Restart to apply"), "and changed again, it has it once more");
    CHECK(settings_help(3) && strstr(settings_help(3), "(from UltimaHacks)"), "an enhancement's help line gives its credit");
    CHECK(settings_help(2) && !strstr(settings_help(2), "(from"), "and one of the project's own has none");
    settings_key(SET_KEY_TAB); settings_key(SET_KEY_TAB);       /* back to Sound: Volume is row 1 */
    applies = 0;
    settings_pointer(VALUE_X + 80, ROW_Y + ROW_H + 5, 1);       /* press on the track: 50 */
    settings_pointer(VALUE_X + 81, ROW_Y + ROW_H + 5, 1);       /* dragged, still 50 */
    settings_pointer(VALUE_X + 82, ROW_Y + ROW_H + 5, 1);
    CHECK(settings_value(1) == 50 && applies == 1, "a slider dragged across one value commits once");
    settings_pointer(VALUE_X + 96, ROW_Y + ROW_H + 5, 1);       /* 60 */
    settings_pointer(VALUE_X + 96, ROW_Y + ROW_H + 5, 0);
    CHECK(settings_value(1) == 60 && applies == 2, "and again when the value changes");
    settings_key(SET_KEY_ESC);
    CHECK(!settings_open(), "Esc closes");
    CHECK(!settings_draw(px), "nothing drawn when closed");
    settings_init("h", G, 3, "Test");                            /* get() results are validated like file text */
    CHECK(settings_value(0) == 1, "a cycle get() of -1 reads as the default");
    CHECK(settings_value(1) == 100, "a slider get() above hi reads as hi");
    CHECK(settings_value(2) == 1, "a bool get() of 2 reads as the default");
    settings_show(1);
    CHECK(settings_draw(px), "and draws");
    /* bad file values read as the screen's validated ones (main.c starts the run with these) */
    port_config_set("h", "scale", "0");
    settings_init("h", S, 1, "Test");
    CHECK(settings_value(0) == 2, "scale=0 reads as the default, 3x");
    port_config_set("h", "scale", "99");
    settings_init("h", S, 1, "Test");
    CHECK(settings_value(0) == 2, "scale=99 reads as the default, 3x");
    port_config_set("h", "scale", "8");
    settings_init("h", S, 1, "Test");
    CHECK(settings_value(0) == 7, "scale=8 reads as 8x");
    port_config_set("h", "volume", "abc");
    settings_init("h", T, 2, "Test");
    CHECK(settings_value(1) == 100, "volume=abc reads as 100");
    /* Left and Right on the tab bar walk the tabs and stay on the bar: Right, Right from Sound is
       Display, with no row changed on the way (a Mac player found the second Right changing
       Controls' first row); Down then enters Display's rows */
    settings_init("h", K, 3, "Test");
    settings_show(1);
    settings_key(SET_KEY_UP);                                    /* the first row to the tab bar */
    settings_key(SET_KEY_RIGHT);
    settings_key(SET_KEY_RIGHT);
    CHECK(settings_value(0) == 0 && settings_value(1) == 0 && settings_value(2) == 0, "Right, Right on the tab bar changes no row");
    settings_key(SET_KEY_DOWN);
    settings_key(SET_KEY_ENTER);
    CHECK(settings_value(2) == 1 && settings_value(1) == 0, "and lands on Display: Down, Enter changes its row");
    settings_key(SET_KEY_UP);
    settings_key(SET_KEY_LEFT);
    CHECK(settings_value(1) == 0, "Left on the tab bar goes back a tab without changing a row");
    settings_key(SET_KEY_TAB);                                   /* Tab: the next tab's first row, as before */
    settings_key(SET_KEY_ENTER);
    CHECK(settings_value(2) == 0, "Tab goes to the next tab's first row");
    /* a folder row says it opens a picker: a folder icon before its value (an unselected row: drawn
       in the text colour 0xE8D9B5, as ABGR), and a help line when selected */
    port_config_set("h", "folder-test", "");
    settings_init("h", P, 2, "Test");
    settings_show(1);
    settings_draw(px);
    CHECK(px[(ROW_Y + ROW_H + 10) * SET_W + VALUE_X + 7] == 0xFFB5D9E8u, "a folder row has a folder icon where its value starts");
    settings_key(SET_KEY_DOWN);
    CHECK(settings_help(1) && strstr(settings_help(1), "choose a folder"), "a folder row's help says Enter or a click chooses a folder");
    /* a cycle's < arrow steps it back and its > (or elsewhere on the row) forward (a Mac player found
       both arrows stepping Window scale up) */
    port_config_set("h", "scale", "3");
    settings_init("h", S, 1, "Test");                            /* Window scale, 3x */
    settings_show(1);
    settings_key(SET_KEY_TAB); settings_key(SET_KEY_TAB);       /* its tab, Display */
    settings_pointer(VALUE_X + 3, ROW_Y + 8, 1); settings_pointer(VALUE_X + 3, ROW_Y + 8, 0);
    CHECK(settings_value(0) == 1, "a click on < steps a cycle back (3x to 2x)");
    settings_pointer(VALUE_X + 5 * 8 + 3, ROW_Y + 8, 1); settings_pointer(VALUE_X + 5 * 8 + 3, ROW_Y + 8, 0);
    CHECK(settings_value(0) == 2, "a click on > steps it forward (2x to 3x)");
    settings_pointer(LABEL_X + 8, ROW_Y + 8, 1); settings_pointer(LABEL_X + 8, ROW_Y + 8, 0);
    CHECK(settings_value(0) == 3, "a click on the label steps it forward, as before");
    /* a cycle with a limit (Window scale on a small display): a saved value past it shows as the last
       offered and steps from there, wrapping within the offered ones; the file keeps it until a change */
    port_config_set("h", "scale", "6");
    settings_init("h", L, 1, "Test");
    settings_show(1);
    settings_key(SET_KEY_TAB); settings_key(SET_KEY_TAB);       /* Display */
    settings_draw(px);
    CHECK(port_config_get("h", "scale", v, sizeof v) == 0 && !strcmp(v, "6"), "a limited cycle leaves a saved value past the limit in the file");
    settings_key(SET_KEY_RIGHT);                                 /* 6x shows as 3x: Right wraps to 1x */
    CHECK(port_config_get("h", "scale", v, sizeof v) == 0 && !strcmp(v, "1"), "Right from past the limit wraps to the first (3x is the last offered)");
    settings_key(SET_KEY_LEFT);
    CHECK(port_config_get("h", "scale", v, sizeof v) == 0 && !strcmp(v, "3"), "Left from the first goes to the last offered, 3x, not 8x");
    printf("%d failed\n", fails);
    return fails != 0;
}
