/* Enhancements: changes the original game does not have, each off unless the player turns it
   on (enhance.c; Underworld Exhumed's docs/ENHANCEMENTS.md). The game declares its flags in a
   table and tests them with ENHANCED(i), only in port-only code, so the DOS build never sees
   them. The mask is set once before the game starts and never changes while it runs. */
#ifndef ENHANCE_H
#define ENHANCE_H
#include <stdint.h>
#include <stddef.h>
#include <stdio.h>

enum { ENH_PRESENTATION, ENH_TIMING, ENH_GAMEPLAY };

struct enhance_flag {
    const char *name;     /* "skip-intro": what --enhance and the settings file say */
    const char *about;    /* one line, for --enhance list (and a settings screen) */
    int kind;             /* ENH_PRESENTATION, ENH_TIMING or ENH_GAMEPLAY */
    const char *source;   /* where the idea or code came from, or NULL for this project's own */
    const char *only;     /* NULL; or the other game's name, for a flag listed here but not in it */
};

extern uint32_t enhance_on;
#define ENHANCED(i) ((enhance_on >> (i)) & 1u)

/* The game's table (at most 32 flags, bit i for entry i) and its name, before anything else. */
void enhance_init(const struct enhance_flag *table, int n, const char *game);
/* A flag's index, or -1. */
int enhance_index(const char *name);
/* MASK's names, comma-separated ("" for none), into out. */
const char *enhance_names(uint32_t mask, char *out, size_t n);
/* The flags of one kind (another game's left out). */
uint32_t enhance_kind_mask(int kind);
/* LIST's names turned on (on) or off in *mask; strict: an unknown or another game's name is
   an error (-1, *mask unchanged), else a warning and skipped. */
int enhance_parse(const char *list, uint32_t *mask, int on, int strict);
/* --enhance list. */
void enhance_list(FILE *f);
/* The settings file's enhance= (lenient), and writing it. */
int enhance_load(const char *home, uint32_t *mask);
int enhance_save(const char *home, uint32_t mask);
/* A recording's flags: *carries 1 for format 5; -1 when it names one this build lacks. */
int enhance_from_recording(const char *path, uint32_t *mask, int *carries);
/* The log line, when any flag is on. */
void enhance_log(void);
#endif
