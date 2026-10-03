/* UW2's bindings for runtime/include/portable.h: the original tokens of the hooks UW2 uses.
   UW2Decomp's src/include/portable.h is runtime/include/portable.h with these definitions in
   it (examples/uw2/port/README.md has how the two were proved to give the same bytes and the
   same replays). Under Turbo C each hook expands to these tokens, so the DOS bytes are the
   original ones; the names are declared by UW2's shared headers (sys.h, gfx.h, 3d.h), which
   the hooks' users include. */
#define ORIG_GAME_TIME()   (*Time)              /* the 1/256 s game clock seg021 keeps */
#define ORIG_KEY()         key()                /* KEYQUEUE.ASM: the next key event */
#define ORIG_MOUSE()       mouse()              /* MOUSEDRV.ASM: the motion into *MouseDx, *MouseDy */
#define ORIG_MBUTTONS()    mbuttons()
#define ORIG_JOY_READ()    seg021_22FD_7CD()    /* JOYPORT.ASM: joy_position */
#define ORIG_JOY_BUTTONS() seg021_22FD_809()    /* joy_buttons */
#define SLAVE_TIMER_HZ     16                   /* SOUND.C's effects timer, which AIL ran */
#define RENDER_TAG_CONTEXT dbptr                /* DRAWOBJ.C: where the sprite opcode goes */
