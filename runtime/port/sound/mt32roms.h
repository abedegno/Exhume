/* Finding the user's MT-32 or CM-32L ROM images (mt32roms.c). */
#ifndef MT32ROMS_H
#define MT32ROMS_H
#include <stddef.h>

/* Whether this build can play the MT-32 at all (libmt32emu linked). */
int mt32roms_available(void);

/* 1 when PATH (a folder, or a file whose folder is meant) holds a usable control and PCM pair,
   recognised by content under any names: their full paths into ctrl and pcm, their libmt32emu
   identifiers into ctrl_id and pcm_id (each n bytes), and the folder into dir when it is not NULL. */
int mt32roms_pick(const char *path, char *ctrl, char *pcm, char *ctrl_id, char *pcm_id, size_t n, char *dir);

/* The ROMs' folder, by the sources in order: given (--mt32-roms), env (the port's variable),
   remembered (its setting), then a search of the port's home, the game's folder, the program's
   folder and the folders other emulators keep MT-32 ROMs in. Returns the source ("given",
   "environment", "remembered", "found") with the folder in dir_out, or NULL when none holds a
   pair. Logs one line, "mt32: ROMs SOURCE DIR (CONTROL, PCM)" or "mt32: no ROMs". */
const char *mt32roms_locate(const char *given, const char *env, const char *remembered, const char *home,
                            const char *data, char *dir_out, size_t n);

#endif
