/* UW2's line for runtime/port/compat.h: UWEDIT.C's main runs as uw2_main on the game's thread
   (src/port/sys/main.c calls it). UW2Decomp's src/port/compat.h is this, then compat.h. */
#define PORT_GAME_MAIN uw2_main
