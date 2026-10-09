/* port.h: what the port's own C shares (Exhume's docs/port.md). Not a game header and not the
   platform API (platform/plat.h): the paragraph map, what is loaded from the user's EXE, the
   emulated hardware (the VGA, the EMS pages, the PIT, the keyboard controller, the mouse
   driver), finding the game and the settings file, and the port's diagnostics. Port C that
   also includes game headers includes compat.h first.

   The project's portgame.h comes first, from the project's own port directory (the include
   path puts it ahead of the runtime's): it names the program (PORT_NAME, the prefix of its
   messages), may move the load segment and the far heap (PORT_LOAD_SEG, PORT_HEAP_FIRST,
   PORT_HEAP_END), gives DGROUP's paragraph (PORT_DGROUP_PARA) and where the C library's _ctype
   table sits in it (PORT_CTYPE_AT), the termination chain exit runs (PORT_EXIT_CHAIN()),
   whether the port has the black box (PORT_BLACKBOX, with BLACKBOX_KEEP and BLACKBOX_SKIP:
   sys/blackbox.c), what marks the game's directory (PORT_GAME_EXE and the rest:
   sys/gamedir.c), and declares the program's own far blocks. compat.h includes it too, so the
   game's C sees it: it declares only the port's names. UW2's is UW2Decomp's src/port/portgame.h. */
#ifndef EXHUME_PORT_H
#define EXHUME_PORT_H

#include <stddef.h>
#include <stdint.h>
#include "portgame.h"

#ifndef PORT_NAME
#define PORT_NAME "port"
#endif

/* A little-endian word store that evaluates its value once: the port's SETW macros, written
   as two byte stores, evaluated the value twice, so `SETW(o, W(o) + n)` saw its own new low
   byte the second time and got the high byte wrong whenever the low byte carried. */
static inline void port_setw(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)v;
    p[1] = (uint8_t)(v >> 8);
}

/* Diagnostics. port_log prints when tracing (the port's -v), and
   port_fatal stops the program with a message. port_halt is where a stub or an unported path
   ends up: the game's thread parks, the window stays, and the message says why. */
extern int port_trace;
void port_log(const char *fmt, ...) __attribute__((format(printf, 1, 2)));
void port_fatal(const char *fmt, ...) __attribute__((format(printf, 1, 2), noreturn));
void port_halt(const char *why) __attribute__((noreturn));
/* Called after each grPageFlip from C (a full screen the game has just shown): writes the
   screenshots --shot-at-flip asks for (sys/main.c). */
void port_on_flip(void);
/* The black box (sys/blackbox.c): a player's session recorded to the home directory's
   recordings/, and its streams written out at a fault or at the game's exit. portgame.h
   defines PORT_BLACKBOX when the port links it. */
int port_blackbox_start(const char *home);
void port_blackbox_close(int crashed);

/* The game's file layer (sys/borland.c): port_files_changed counts the game's changes to its
   files (a write, a written file's close after its buffer is out, a removal, a rename, a new
   folder), so that the web build knows when to copy the home directory into the browser's
   storage; port_flush_writes writes out what the web build's write-behind buffers still hold
   (nothing elsewhere), for the end of a run that never reaches exit. */
unsigned port_files_changed(void);
void port_flush_writes(void);

/* The paragraph map (mem/parmap.c, docs/port.md "Far pointers and segments"): host memory
   given DOS paragraph numbers, so that MK_FP, FP_SEG, FP_OFF and segment arithmetic work.
   pm_add gives a block the paragraphs from seg; the block's byte 0 is seg:0000. */
void pm_add(const char *name, void *base, size_t size, unsigned seg);
void *port_mk_fp(unsigned seg, unsigned off);
unsigned port_fp_seg(const volatile void *p);
unsigned port_fp_off(const volatile void *p);
/* A far pointer's segment and offset as the DOS code made it, when it was derived from one of
   the last few MK_FP results (a picture's pixels after its header); else FP_SEG and FP_OFF. */
void port_fp_split_recent(const void *p, unsigned *seg, unsigned *off);
void pm_remove(void *base);
void *pm_segbase(unsigned seg);
/* The program's load segment: far data blocks are at their EXE paragraph plus this. */
#ifndef PORT_LOAD_SEG
#define PORT_LOAD_SEG 0x0800u
#endif
/* The emulated conventional memory heap that farmalloc hands out (mem/parmap.c). */
#ifndef PORT_HEAP_FIRST
#define PORT_HEAP_FIRST 0x7000u
#endif
#ifndef PORT_HEAP_END
#define PORT_HEAP_END   0xA000u
#endif
#ifndef PORT_DGROUP_PARA
#define PORT_DGROUP_PARA 0u
#endif

/* What the port reads from the user's own EXE at start-up, never shipped: the far data no
   source defines yet, and DGROUP's initialised image (the project's: UW2's mem/fardata.c). */
int port_load_exe(const char *path);      /* 0, or -1 with a message */
/* Whether path is the build of the game the port was made from (the project's, beside
   port_load_exe): 0 if it is; else -1 if it cannot be read, -2 for another size, -3 for
   another build. No message. */
int port_check_exe(const char *path);
/* The EXE's DGROUP image (its initialised data, DS:0 on), for the C library's tables and the
   null-pointer copies. */
extern unsigned char port_dgroup_image[0x10000];
/* The null pointers (docs/port.md, "Null pointers"): the project writes what DOS reads at DS:0
   and in the interrupt vector table into the port's copies, which start as DGROUP's image and
   zeros (mem/parmap.c). */
void port_game_nulls(unsigned char *ds0, unsigned char *ivt);

/* The EMS 4.0 driver's memory (mem/emm.c): a store of 16 KB logical pages for one handle and
   a 64 KB page frame of four slots at segment seg, a region of the paragraph map. Mapping a
   page into a slot copies the page that was there back to the store and the new one in, so
   the frame holds what DOS's would; a page mapped into two slots stays one page. emm_open
   allocates the smaller of free_pages (what the emulated driver reports free) and max_pages,
   failing below min_pages: the number allocated, or 0. emm_map: 1 if done, 0 if refused;
   logical FFFFh unmaps the slot. The project's C keeps the game's own entry points (UW2:
   UW2Decomp's mem/ems.c, for EMS.C's). */
int emm_open(unsigned min_pages, unsigned max_pages, unsigned free_pages, unsigned seg);
void emm_close(void);
int emm_map(unsigned physical, unsigned logical);
/* More handles beside the one emm_open makes: emm_alloc gives a handle of n pages (1 to 15;
   0 when none is left), emm_map_handle maps its page logical into a slot as emm_map does,
   emm_free frees it (unmapping any of its pages still in the frame). */
int emm_alloc(unsigned n);
int emm_map_handle(unsigned physical, int h, unsigned logical);
void emm_free(int h);
/* The frame's host memory: 64 KB mapped twice in a row and a guard page (mem/frame.c); with
   PORT_FRAME_SINGLE, 64 KB once (WebAssembly cannot map memory twice; -DPORT_FRAME_SINGLE
   builds it on the desktop, with the 64 KB after it inaccessible). port_ems_has: whether p is
   in the frame's 64 KB (mem/emm.c; the far pointer arithmetic is portable.h's EMS_WRAP and
   EMS_ADD). */
#if defined(__EMSCRIPTEN__) && !defined(PORT_FRAME_SINGLE)
#define PORT_FRAME_SINGLE 1
#endif
unsigned char *port_frame_alloc(void);
int port_ems_has(const void *p);
/* port_ems_runs_past: whether n bytes from p, a pointer into the frame, run past its 64 KB (by
   the linear address, not the segment's offset); 0 for a pointer outside the frame. */
int port_ems_runs_past(const void *p, unsigned long n);

/* Finding the game, and the settings file (sys/gamedir.c, sys/gogreg.c). port_find_game
   looks for the game's directory by itself; port_game_in looks in a folder the user chose;
   both return 0 with the directory in out. port_game_refused says what was found and refused
   by port_check_exe, for the message when nothing was found ("" if nothing). The settings
   file is PORT_CONFIG_FILE in the home directory, lines of key=value: port_config_get gives
   0 and the value, port_config_set writes it. port_gog_registry (Windows) calls fn for each
   game GOG's installers recorded in the registry, its product id, title and folder, until fn
   returns non-zero, and returns that, or 0. */
int port_find_game(const char *home, char *out, size_t outsz);
int port_game_dir_ok(const char *dir);   /* 0 when DIR itself holds the game's build (sys/gamedir.c) */
int port_game_in(const char *dir, const char *home, char *out, size_t outsz);
const char *port_game_refused(void);
int port_config_get(const char *home, const char *key, char *out, size_t outsz);
int port_config_set(const char *home, const char *key, const char *value);
int port_gog_registry(int (*fn)(const char *id, const char *name, const char *path, void *ctx), void *ctx);

/* The emulated VGA (gfx/vga.c). */
void vga_outb(unsigned port, uint8_t v);
uint8_t vga_inb(unsigned port);
void vga_outw(unsigned port, uint16_t v);
void vga_write(uint16_t off, uint8_t v);       /* a CPU write to A000:off */
uint8_t vga_read(uint16_t off);                /* a CPU read of A000:off (loads the latches) */
void vga_set_mode(int mode);                   /* int 10h, AH = 0 */
void vga_scanout(uint8_t *pixels, int *w, int *h, uint8_t rgb6[768]);
void vga_scanout_now(uint8_t *pixels, int *w, int *h, uint8_t rgb6[768]);
void vga_get_dac(uint8_t rgb6[768]);
const uint8_t *vga_plane(int p);               /* for the state dump */
void vga_window_init(void);                    /* A000:0000 in the paragraph map */
int vga_in_window(const volatile void *p);     /* p points into it */
void port_vga_store(volatile void *p, unsigned char v);
uint8_t vga_reg_crtc(int i);

/* Ports other than the VGA's (sys/borland.c dispatches outportb and inportb). */
void port_outb(unsigned port, uint8_t v);
uint8_t port_inb(unsigned port);

/* The PIT (sys/pit.c): the port's timer thread gives AIL's timers (sound/ail.c) the time
   that passes, and runs the BIOS tick at 18.2 Hz and the keyboard's typematic repeat. */
void pit_start(void);
uint32_t pit_bios_ticks(void);                 /* 18.2 Hz ticks since start, as 0040:006C */

/* The keyboard controller (sys/kbdint.c): a byte from the platform, as from port 60h. */
void kbd_byte(uint8_t scancode);
void kbd_tick_ms(uint32_t ms);                 /* the typematic repeat, from the PIT thread */

/* The mouse driver (sys/mousedrv.c): int 33h's state, fed by pointer events. */
struct PlatPointer;
void mouse_event(const struct PlatPointer *ev);
/* An enhancement's mouse-look (the game's mousedrv.c): its motion passed through, scaled by a
   percentage, with the pointer captured */
void port_idle(void);                   /* sys/pit.c: rest while the game waits on the clock */
void port_pause(int on);                /* sys/pit.c: the settings screen open (1) or closed (0): the game's clock stands still */
void port_pause_wait(void);             /* sys/pit.c: the game's thread, at a clock read: wait while paused */
int port_game_parked(void);             /* sys/pit.c: 1 while the game's thread waits in port_pause_wait */
uint32_t pit_paused_ms(void);           /* sys/pit.c: milliseconds spent paused so far (main thread) */
void audio_set_volume(int percent);     /* sound/audio.c: 0..100, applied to every sample played after the call */
void mouse_look_mode(int on);
void mouse_look_speed(int pct);
int mouse_int33(uint16_t *ax, uint16_t *bx, uint16_t *cx, uint16_t *dx);

/* The divide trap (sys/int0trap.c): the handler the game installed for int 0, called on a
   host SIGFPE (x86-64; arm64's divide does not trap). */
void int0_install(void);

#endif
