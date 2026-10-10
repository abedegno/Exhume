/* pace.c: replaces nothing; the port's frame pacing, first written for UW2's port (its
   src/port/3d/render.c) and shared since UW1 needed it too (Underworld Exhumed's issue 6).

   The Underworlds move the player by the time since the last frame (PLAYMOVE.C's check_physics,
   on the 256 Hz clock *Time) and write the position back in whole 1/256 tiles each frame,
   dropping the fraction (MOTION.C's back_to_space): down for a move in a positive direction, up
   for a negative one. A PC of 1992 took 10 to 15 ticks to draw a frame, and the loss was small;
   the host draws one in under a tick, and a slow move in a positive direction went nowhere at
   all: walking backwards with x at 0.7 of a unit a tick (UW2's rc8: "struggling to walk back
   whilst in the corridors"), and in UW1 sidestepping (235) and walking backwards (188) towards
   +x or +y, so that a sidestep stalled or drifted along one axis, by the way the player faced.
   DOSBox at high cycles does the same. So a 3D frame waits until PORT_FRAME_TICKS ticks have
   passed since the last, 32 frames a second at most. Not under replay, whose clock is the
   recording's: the goldens are as DOS made them. */
#include <stdint.h>
#include "port.h"
#include "plat.h"

extern int16_t rp_request;              /* runtime/replay/replay.c: 2 when replaying */

void port_pace_frame(const volatile uint32_t *clock)
{
    static uint32_t last;
    static int started;
    int ms;
    if (rp_request == 2) return;
    /* at most 40 ms, should the clock ever stand still (the settings screen pauses it) */
    if (started)
        for (ms = 0; *clock - last < PORT_FRAME_TICKS && ms < 40; ms++)
            plat_sleep_ns(1000000);
    started = 1;
    last = *clock;
}
