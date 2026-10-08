/* inscript.c: an input script for tests (--input-script FILE). One event a line, at a time in
   milliseconds of the PIT's BIOS clock (pit_bios_ticks, 18.2 Hz) from the start:
       T key NAME        press and release
       T down NAME       press          T up NAME     release
       T move X Y        the pointer to X, Y of the game's 320 by 200 screen
       T click X Y left|right
       T mdown X Y left|right    press a button there    T mup X Y left|right   release it
       T look DX DY      relative motion, as a captured mouse gives (mouse-look), DX right and
                         DY down in host pixels, -1000..1000
       T wclick X Y      a left click at X, Y of the window, in its own coordinates (0..10000),
                         put on the backend's event queue (plat_window_click), so that it is
                         mapped as a player's click is
       T wdown X Y       the press of a wclick alone, its release lost (as a macOS fullscreen
                         switch can lose one)
   '#' starts a comment. Keys go in as PC set-1 scan codes through the hook real keys use, and
   the pointer through the one the mouse uses, so the game, the recorder and the black box see
   a player's input. The time is the BIOS clock's plus the time the settings screen has been open,
   which stops that clock. */
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <ctype.h>
#include "inscript.h"
#include "portgame.h"
#ifndef PORT_NAME
#define PORT_NAME "port"
#endif
uint32_t pit_bios_ticks(void);
uint32_t pit_paused_ms(void);

enum { EV_KEY, EV_DOWN, EV_UP, EV_MOVE, EV_CLICK, EV_LOOK, EV_MDOWN, EV_MUP, EV_WCLICK, EV_WDOWN };
struct ev { uint32_t ms; int kind, code, x, y, button; };
static struct ev *evs;
static int nev, next;
static void (*key_fn)(uint8_t);
static void (*ptr_fn)(const PlatPointer *);

/* names to set-1 make codes; 0x100 marks an E0-prefixed key */
static const struct { const char *name; uint16_t code; } keys[] = {
    {"esc", 0x01}, {"1", 0x02}, {"2", 0x03}, {"3", 0x04}, {"4", 0x05}, {"5", 0x06}, {"6", 0x07},
    {"7", 0x08}, {"8", 0x09}, {"9", 0x0A}, {"0", 0x0B}, {"backspace", 0x0E}, {"tab", 0x0F},
    {"q", 0x10}, {"w", 0x11}, {"e", 0x12}, {"r", 0x13}, {"t", 0x14}, {"y", 0x15}, {"u", 0x16},
    {"i", 0x17}, {"o", 0x18}, {"p", 0x19}, {"enter", 0x1C}, {"ctrl", 0x1D}, {"a", 0x1E},
    {"s", 0x1F}, {"d", 0x20}, {"f", 0x21}, {"g", 0x22}, {"h", 0x23}, {"j", 0x24}, {"k", 0x25},
    {"l", 0x26}, {"shift", 0x2A}, {"z", 0x2C}, {"x", 0x2D}, {"c", 0x2E}, {"v", 0x2F},
    {"b", 0x30}, {"n", 0x31}, {"m", 0x32}, {"alt", 0x38}, {"space", 0x39}, {"f1", 0x3B},
    {"f2", 0x3C}, {"f3", 0x3D}, {"f4", 0x3E}, {"f5", 0x3F}, {"f6", 0x40}, {"f7", 0x41},
    {"grave", 0x29}, {"`", 0x29}, {".", 0x34}, {";", 0x27}, {"f8", 0x42}, {"f9", 0x43}, {"f10", 0x44}, {"f11", 0x57}, {"f12", 0x58},
    {"up", 0x148}, {"left", 0x14B}, {"right", 0x14D}, {"down", 0x150}, {"home", 0x147},
    {"end", 0x14F}, {"pgup", 0x149}, {"pgdn", 0x151}, {"insert", 0x152}, {"delete", 0x153},
};

static int key_code(const char *name)
{
    size_t i;
    for (i = 0; i < sizeof keys / sizeof keys[0]; i++)
        if (!strcmp(keys[i].name, name)) return keys[i].code;
    return -1;
}

static int bad(FILE *f, const char *path, int line, const char *what)
{
    if (f) fclose(f);
    fprintf(stderr, PORT_NAME ": input script %s:%d: %s\n", path, line, what);
    free(evs);
    evs = NULL;
    nev = 0;
    return -1;
}

int inscript_load(const char *path, void (*key)(uint8_t), void (*pointer)(const PlatPointer *))
{
    char buf[256], word[32], arg[32], btn[16];
    int line = 0, cap = 0;
    FILE *f = fopen(path, "r");
    if (!f) {
        fprintf(stderr, PORT_NAME ": input script %s: cannot open it\n", path);
        return -1;
    }
    key_fn = key;
    ptr_fn = pointer;
    while (fgets(buf, sizeof buf, f)) {
        struct ev e;
        char *h = strchr(buf, '#');
        unsigned long ms;
        int n;
        line++;
        if (h) *h = 0;
        memset(&e, 0, sizeof e);
        arg[0] = 0;
        n = sscanf(buf, "%lu %31s %31s", &ms, word, arg);
        if (n <= 0) continue;                       /* blank, or a comment */
        if (n < 2) return bad(f, path, line, "a time and an event");
        e.ms = (uint32_t)ms;
        for (h = arg; *h; h++) *h = (char)tolower((unsigned char)*h);
        if (!strcmp(word, "key") || !strcmp(word, "down") || !strcmp(word, "up")) {
            e.kind = word[0] == 'k' ? EV_KEY : word[0] == 'd' ? EV_DOWN : EV_UP;
            if (n < 3 || (e.code = key_code(arg)) < 0) return bad(f, path, line, "no such key");
        } else if (!strcmp(word, "move") || !strcmp(word, "click") || !strcmp(word, "mdown") || !strcmp(word, "mup")) {
            e.kind = !strcmp(word, "move") ? EV_MOVE : !strcmp(word, "click") ? EV_CLICK : !strcmp(word, "mdown") ? EV_MDOWN : EV_MUP;
            btn[0] = 0;
            if (sscanf(buf, "%lu %31s %d %d %15s", &ms, word, &e.x, &e.y, btn) < 4
                || e.x < 0 || e.x > 319 || e.y < 0 || e.y > 199
                || (e.kind != EV_MOVE && strcmp(btn, "left") && strcmp(btn, "right")))
                return bad(f, path, line, "a position 0..319 0..199 (and for a click, left or right)");
            e.button = !strcmp(btn, "right") ? PLAT_BUTTON_RIGHT : PLAT_BUTTON_LEFT;
        } else if (!strcmp(word, "wclick") || !strcmp(word, "wdown")) {
            e.kind = !strcmp(word, "wclick") ? EV_WCLICK : EV_WDOWN;
            if (sscanf(buf, "%lu %31s %d %d", &ms, word, &e.x, &e.y) < 4
                || e.x < 0 || e.x > 10000 || e.y < 0 || e.y > 10000)
                return bad(f, path, line, "a window position 0..10000 0..10000");
        } else if (!strcmp(word, "look")) {
            e.kind = EV_LOOK;
            if (sscanf(buf, "%lu %31s %d %d", &ms, word, &e.x, &e.y) < 4
                || e.x < -1000 || e.x > 1000 || e.y < -1000 || e.y > 1000)
                return bad(f, path, line, "a motion -1000..1000 -1000..1000");
        } else
            return bad(f, path, line, "an event: key, down, up, move, click, mdown, mup, look, wclick or wdown");
        if (nev && e.ms < evs[nev - 1].ms) return bad(f, path, line, "times must not go back");
        if (nev == cap) {
            struct ev *m = realloc(evs, (size_t)(cap = cap ? cap * 2 : 32) * sizeof *evs);
            if (!m) return bad(f, path, line, "out of memory");
            evs = m;
        }
        evs[nev++] = e;
    }
    fclose(f);
    return 0;
}

static void send_key(int code, int down)
{
    if (code & 0x100) key_fn(0xE0);
    key_fn((uint8_t)((code & 0x7F) | (down ? 0 : 0x80)));
}

static void send_ptr(int type, const struct ev *e, unsigned held)
{
    PlatPointer p;
    memset(&p, 0, sizeof p);
    p.type = type;
    p.x = (float)e->x;
    p.y = (float)e->y;
    p.absolute = 1;
    p.button = type == PLAT_POINTER_MOVE ? 0 : (unsigned)e->button;
    p.buttons = held;
    ptr_fn(&p);
}

static void send_look(const struct ev *e)
{
    PlatPointer p;
    memset(&p, 0, sizeof p);
    p.type = PLAT_POINTER_MOVE;
    p.dx = (float)e->x;
    p.dy = (float)e->y;
    ptr_fn(&p);                     /* absolute 0: only the motion means anything */
}

void inscript_tick(void)
{
    /* 18.2065 ticks a second: 182 ticks are 9996 ms */
    /* plus the time the settings screen has stood open, when the BIOS clock stood still */
    uint32_t now = (uint32_t)((uint64_t)pit_bios_ticks() * 10000000u / 182065u) + pit_paused_ms();
    while (next < nev && evs[next].ms <= now) {
        const struct ev *e = &evs[next++];
        switch (e->kind) {
        case EV_KEY: send_key(e->code, 1); send_key(e->code, 0); break;
        case EV_DOWN: send_key(e->code, 1); break;
        case EV_UP: send_key(e->code, 0); break;
        case EV_MOVE: send_ptr(PLAT_POINTER_MOVE, e, 0); break;
        case EV_LOOK: send_look(e); break;
        case EV_WCLICK: plat_window_click((float)e->x, (float)e->y, 1); break;
        case EV_WDOWN: plat_window_click((float)e->x, (float)e->y, 0); break;
        case EV_MDOWN: send_ptr(PLAT_POINTER_MOVE, e, 0); send_ptr(PLAT_POINTER_DOWN, e, (unsigned)e->button); break;
        case EV_MUP: send_ptr(PLAT_POINTER_UP, e, 0); break;
        case EV_CLICK:
            send_ptr(PLAT_POINTER_MOVE, e, 0);
            send_ptr(PLAT_POINTER_DOWN, e, (unsigned)e->button);
            send_ptr(PLAT_POINTER_UP, e, 0);
            break;
        }
    }
}
