/* pit.c: replaces the PC's programmable interval timer as an AIL 2 game uses it (first written
   for UW2Decomp's port). AIL.ASM reprogrammed channel 0 and ran the timers registered with
   AIL_register_timer from it (UW2: the game clock at 256 Hz, SOUND.C's cllbck_tst, among them),
   and the BIOS kept its 18.2 Hz tick. Here a thread of the port's own measures the host's
   high-resolution counter and gives AIL (sound/ail.c, which keeps API_timer's DDA) the time
   that has passed, so that AIL runs as many PIT ticks as the PIT would have, its timers at
   their own rates, a late wake-up running the missed ticks at once, as queued interrupts did.
   Under replay AIL takes its ticks from the replayed clock instead. The BIOS tick (for clock())
   and the keyboard's typematic repeat are driven from here too. */
#include <stdatomic.h>
#include "port.h"
#include "plat.h"
#include "sound/audio.h"

static _Atomic uint32_t bios_ticks;
static uint64_t hz, start;
static _Atomic int paused;                  /* the settings screen is open */
static uint64_t pause_t0, pause_total;      /* the main thread's: when it opened, and the time spent open */

uint32_t pit_bios_ticks(void)
{
    return atomic_load(&bios_ticks);
}

static int pit_thread(void *arg)
{
    uint64_t now, last = plat_counter(), prev = last, bios_next;
    (void)arg;
    /* 18.2065 Hz: 1193182 / 65536 */
    bios_next = start + hz * 65536 / 1193182;
    for (;;) {
        plat_sleep_ns(500000);
        now = plat_counter();
        if (atomic_load(&paused)) {
            /* the game's clock stands still: no AIL ticks, no BIOS ticks, no key repeat, and the
               BIOS tick's schedule moves on by the time spent paused */
            bios_next += now - prev;
            prev = now;
            last = now;
            continue;
        }
        ail_pit_advance((now - prev) * 1000000000u / hz);
        prev = now;
        while (now >= bios_next) {
            atomic_fetch_add(&bios_ticks, 1);
            bios_next += hz * 65536 / 1193182;
        }
        kbd_tick_ms((uint32_t)((now - last) * 1000 / hz));
        last = now - (now - last) % (hz / 1000 ? hz / 1000 : 1);
    }
    return 0;
}

void pit_start(void)
{
    hz = plat_counter_hz();
    start = plat_counter();
    if (plat_thread_start("pit", pit_thread, 0)) port_fatal("cannot start the timer thread");
}

/* The game is waiting on the clock (runtime/replay/replay.c's rp_time saw it read the same value
   many times over): rest half a millisecond, an eighth of the game clock's 3.9 ms tick. */
void port_idle(void)
{
    plat_sleep_ns(500000);
}

/* The settings screen opened or closed (main thread). While paused the PIT thread gives the
   game no time, and the game's thread waits at its next clock read (port_pause_wait). */
void port_pause(int on)
{
    uint64_t now = plat_counter();
    if (on && !atomic_load(&paused)) {
        pause_t0 = now;
        atomic_store(&paused, 1);
    } else if (!on && atomic_load(&paused)) {
        pause_total += now - pause_t0;
        atomic_store(&paused, 0);
    }
}

static _Atomic int parked;                   /* the game's thread is waiting in port_pause_wait */

void port_pause_wait(void)
{
    if (!atomic_load(&paused)) return;
    atomic_store(&parked, 1);
    while (atomic_load(&paused)) plat_sleep_ns(10000000);
    atomic_store(&parked, 0);
}

/* 1 while the game's thread waits at a clock read for the pause to end: it is between two of its
   own steps, not in the middle of a file call, so another thread may write out its files (the web
   build's end of a run, platform/sdl3/plat_sdl3.c). */
int port_game_parked(void)
{
    return atomic_load(&parked);
}

/* Milliseconds spent paused so far (main thread): an input script's clock is the BIOS clock plus
   this, so that its events go on being sent while the clock stands still. */
uint32_t pit_paused_ms(void)
{
    uint64_t t = pause_total + (atomic_load(&paused) ? plat_counter() - pause_t0 : 0);
    return (uint32_t)(t * 1000 / (hz ? hz : 1));
}
