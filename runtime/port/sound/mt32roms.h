/* Finding the user's MT-32 or CM-32L ROM images (mt32roms.c). */
#ifndef MT32ROMS_H
#define MT32ROMS_H
#include <stddef.h>

/* Whether this build can play the MT-32 at all (libmt32emu linked). */
int mt32roms_available(void);

/* The files a folder searched holds at most that are read (a ROM folder holds a few dozen). */
#define MT32ROMS_MAX_FILES 256

/* A control and PCM pair: each image is one whole file (its second path "") or two halves that
   libmt32emu joins; their libmt32emu identifiers as whole images; the folder they are in. */
struct mt32roms_set {
    char ctrl[2][1300], pcm[2][1300];
    char ctrl_id[32], pcm_id[32];
    char dir[1024];
};

/* 1 when PATH (a folder, or a file whose folder is meant) holds a usable control and PCM pair,
   recognised by content under any names, whole or in halves; the pair into out when it is not
   NULL. A path to nothing holds none. */
int mt32roms_pick(const char *path, struct mt32roms_set *out);

/* Adds SET's two images to a libmt32emu context (an mt32emu_context), joining halves. Returns
   how many were added: 2 when both were. */
int mt32roms_add(void *context, const struct mt32roms_set *set);

/* The ROMs' folder, by the sources in order: given (--mt32-roms), env (the port's variable),
   remembered (its setting), then a search of the port's home, the game's folder, the program's
   folder and the folders other emulators keep MT-32 ROMs in. Returns the source ("given",
   "environment", "remembered", "found") with the folder in dir_out, or NULL when none holds a
   pair. Logs one line, "mt32: ROMs SOURCE DIR (CONTROL, PCM)" or "mt32: no ROMs". */
const char *mt32roms_locate(const char *given, const char *env, const char *remembered, const char *home,
                            const char *data, char *dir_out, size_t n);

#endif
