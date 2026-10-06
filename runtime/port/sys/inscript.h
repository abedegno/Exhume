/* inscript.h: an input script (--input-script), keys and mouse events at set times, for tests
   (inscript.c). */
#ifndef INSCRIPT_H
#define INSCRIPT_H
#include <stdint.h>
#include "plat.h"
/* Reads PATH; key and pointer are the hooks real input goes through. 0, or -1 after a message
   naming the file and line. */
int inscript_load(const char *path, void (*key)(uint8_t), void (*pointer)(const PlatPointer *));
/* Sends every event whose time has come (PlatHooks.tick). */
void inscript_tick(void);
#endif
