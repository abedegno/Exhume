/* The settings screen (settings.c): a model of tabs and rows, its drawing into a 640x400 RGBA
   layer, and its keyboard and pointer input. Each game gives it a table of rows; the Enhancements
   tab is built from enhance.h's flags. The SDL backend shows the layer and feeds it input.
   Pixels are 0xAABBGGRR words, that is the bytes R, G, B, A in memory on a little-endian host
   (SDL_PIXELFORMAT_RGBA32). */
#ifndef SETTINGS_H
#define SETTINGS_H
#include <stdint.h>

enum { SET_TAB_SOUND, SET_TAB_CONTROLS, SET_TAB_DISPLAY, SET_TAB_ENHANCE, SET_TAB_GAME, SET_TABS };
enum { SET_CYCLE, SET_BOOL, SET_SLIDER, SET_FOLDER };

struct setting {
    int tab;
    const char *label;
    int kind;
    const char *key;                /* the settings file's key; NULL when get/put stand in for it */
    const char *const *names;       /* SET_CYCLE: the values shown, NULL-ended */
    const char *const *stored;      /* SET_CYCLE: the file's text for each (NULL: the index) */
    int lo, hi, step, def;          /* SET_SLIDER range and step; every kind's default */
    int restart;                    /* 1: applies at the next start */
    void (*apply)(int value);       /* live options: called with the new value */
    int (*get)(void);               /* optional: the value from elsewhere (UW.CFG) */
    void (*put)(int value);         /* optional: the value to elsewhere */
    int (*check)(const char *path); /* SET_FOLDER: 0 to accept a folder, else refuse */
    const char *refuse;             /* SET_FOLDER: what the screen says of a refused folder (NULL: a general line) */
};

enum { SET_KEY_UP, SET_KEY_DOWN, SET_KEY_LEFT, SET_KEY_RIGHT, SET_KEY_TAB, SET_KEY_ENTER,
       SET_KEY_ESC, SET_KEY_F11 };

/* The table (at most 64 rows with the enhancement rows added after it), loaded from HOME's
   settings file. TABLE must outlive the screen. */
void settings_init(const char *home, const struct setting *table, int n, const char *title);
int  settings_open(void);           /* 1 while shown */
void settings_show(int on);
void settings_key(int key);         /* SET_KEY_* */
void settings_pointer(int x, int y, int down);   /* in the layer's 640x400 pixels */
int  settings_draw(uint32_t *rgba); /* 640*400 pixels; 0 when not shown */
int  settings_value(int row);
void settings_set_value(int row, int value);   /* the row's current value only: nothing is written or applied */       /* the row's current value (index, 0/1, slider value) */
void settings_folder_chosen(const char *path);   /* the async folder picker's answer */
extern int (*settings_pick_folder)(void);        /* set by the backend: start the picker */
#define SET_W 640
#define SET_H 400
#endif
