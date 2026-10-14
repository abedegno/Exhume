/* cheat.h: test-only cheats, an input script's `cheat` lines (cheat.c). A test sets up a game
   state that scripted play cannot reach cheaply (where the player is, what they carry, their
   statistics), saves it, and a real test starts from that saved game. The game supplies its
   commands in a table and the test of whether a cheat may run now; cheat.c parses, gates,
   records and replays them, and runs each at the game's safe point (port_cheat_point). */
#ifndef CHEAT_H
#define CHEAT_H
#include <stdint.h>
#include <stddef.h>

/* A command: NAME's arguments as words. check: only whether they are well formed (the script
   is read before the game has loaded anything); run: carry it out through the game's own
   routines. NULL when done (or, checking, when good), else what is wrong. */
struct cheat_cmd {
    const char *name;                   /* "goto" */
    const char *usage;                  /* "LEVEL X Y", for the messages */
    const char *(*fn)(int argc, char *argv[], int run);
};

struct cheat_game {
    const struct cheat_cmd *cmds;
    int ncmds;
    /* whether a cheat may run now: in the game, on its 3D view, with nothing pending (the
       safe point is reached in every pass of the main loop, the menus' too) */
    int (*ready)(void);
};

/* The game's table, before the input script is read (main). */
void cheat_init(const struct cheat_game *game);
/* Whether cheats are allowed: EXHUME_TEST_CHEATS=1 in the environment, and never in the web
   build (a player's page has no environment to set). */
int cheat_allowed(void);
/* inscript.c: TEXT (the line after "cheat") checked and added to the script's cheats; NULL,
   or what is wrong. */
const char *cheat_add(const char *text);
/* inscript.c, on the platform's thread: the next scripted cheat's time has come. */
void cheat_release(void);
/* The script's cheats (0 for none): replay.c writes recording format 6 when there are any. */
int cheat_scripted(void);
/* main, for --replay PATH: a format 6 recording's cheats, to be run again at the same safe
   points; 0, or -1 after a message (cheats not allowed, or the file is bad). */
int cheat_from_recording(const char *path);
/* The game's safe point, on its own thread, once a pass of its main loop (MAINLOOP.C, port
   only). Nothing at all unless the script has cheats or the replay's recording does. */
void port_cheat_point(void);
/* A number: decimal, or hex with 0x; lo..hi. 0, or -1. */
int cheat_num(const char *s, long lo, long hi, long *v);

/* replay.c's side (the port's): a cheat run at safe point POLL, recorded when a recording is
   being made (a chunk of stream 10). */
void rp_cheat_put(uint32_t poll, const char *text);
#endif
