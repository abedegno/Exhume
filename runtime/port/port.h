/* port.h: what the port's own C shares (Exhume's docs/port.md). Not a game header and not the
   platform API (platform/plat.h): the paragraph map, what is loaded from the user's EXE, the
   emulated hardware (the VGA, the PIT, the keyboard controller, the mouse driver), and the
   port's diagnostics. Port C that also includes game headers includes compat.h first.

   The project's portgame.h, beside this file, comes first: it names the program (PORT_NAME,
   the prefix of its messages), may move the load segment and the far heap (PORT_LOAD_SEG,
   PORT_HEAP_FIRST, PORT_HEAP_END), gives DGROUP's paragraph (PORT_DGROUP_PARA) and where the
   C library's _ctype table sits in it (PORT_CTYPE_AT), the termination chain exit runs
   (PORT_EXIT_CHAIN()), whether the port has the black box (PORT_BLACKBOX, with BLACKBOX_KEEP
   and BLACKBOX_SKIP: sys/blackbox.c), and declares the program's own far blocks. UW2's is
   examples/uw2/port/portgame.h. */
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
/* The EXE's DGROUP image (its initialised data, DS:0 on), for the C library's tables and the
   null-pointer copies. */
extern unsigned char port_dgroup_image[0x10000];
/* The null pointers (docs/port.md, "Null pointers"): the project writes what DOS reads at DS:0
   and in the interrupt vector table into the port's copies, which start as DGROUP's image and
   zeros (mem/parmap.c). */
void port_game_nulls(unsigned char *ds0, unsigned char *ivt);

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
int mouse_int33(uint16_t *ax, uint16_t *bx, uint16_t *cx, uint16_t *dx);

/* The divide trap (sys/int0trap.c): the handler the game installed for int 0, called on a
   host SIGFPE (x86-64; arm64's divide does not trap). */
void int0_install(void);

#endif
