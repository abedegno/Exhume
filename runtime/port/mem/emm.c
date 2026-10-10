/* emm.c: replaces the EMS 4.0 driver's memory, as a game that keeps one main handle (and maybe
   a few small ones, emm_alloc) uses it (from
   UW2Decomp's src/port/mem/ems.c, whose EMS.C entry points stay UW2's and call this; Exhume's
   docs/port.md, "Far pointers and the paragraph map"). A store of 16 KB logical pages for the
   one handle, and a 64 KB page frame of four 16 KB slots that is a region of the paragraph
   map at the segment the project gives (UW2: E000h). Mapping a page into a slot copies the
   page that was there back to the store and copies the new one in, so the frame holds exactly
   what DOS's would, and the game's own record of what is mapped stays true. On the desktop the
   frame is mapped twice in a row (frame.c), so a pointer run past its end wraps to its start as
   a far pointer's offset does; with PORT_FRAME_SINGLE (the web build, where WebAssembly cannot
   map memory twice, or -DPORT_FRAME_SINGLE on the desktop) it is mapped once, and the wrap is
   port_ems_add's and port_ems_wrap's (below), where the game's C asks for it. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include "port.h"

static unsigned pages;                  /* the main handle's, store pages 0 .. pages-1 */
static unsigned total;                  /* store pages, the extra handles' after the main one's */
static int opened;
static unsigned char *store;
static unsigned char *frame;
static int slot[4] = { -1, -1, -1, -1 };        /* the logical page in each frame slot */

/* Slot s's page back to the store. In DOS a page mapped into two slots is one page seen
   twice; here each slot has a copy, so when another slot holds the same page, the copy that
   differs from the store is the one written since, and it goes to the store and to the other
   slot. (UW2: after a cutscene the textures are reloaded through slots 0 to 2, and the
   renderer then maps the same pages into slot 3; copied from the store alone, slot 3 got the
   cutscene's pictures, and the floor turned red after Lord British's talk in rc8.) */
static void write_back(int s)
{
    unsigned char *page = store + (size_t)slot[s] * 0x4000, *mine = frame + s * 0x4000;
    int q, shared = 0;
    for (q = 0; q < 4; q++) shared |= q != s && slot[q] == slot[s];
    if (!shared) { memcpy(page, mine, 0x4000); return; }
    if (!memcmp(page, mine, 0x4000)) return;
    memcpy(page, mine, 0x4000);
    for (q = 0; q < 4; q++)
        if (q != s && slot[q] == slot[s]) memcpy(frame + q * 0x4000, mine, 0x4000);
}

/* Maps store page `page` (FFFFh: none) into a slot. */
static int map_page(unsigned physical, unsigned logical)
{
    int q, shared = 0;
    /* the slot's own page again, in no other slot: the frame already holds it as DOS's would, and
       copying it out and back changes nothing (UW1's cutscene reader maps its page before every
       read, some eighty million times in the intro: as copies, minutes on some machines) */
    for (q = 0; q < 4; q++) shared |= q != (int)physical && slot[q] == (int)logical;
    if (slot[physical] == (int)logical && logical != 0xFFFF && !shared) return 1;
    if (slot[physical] >= 0) write_back((int)physical);
    if (logical == 0xFFFF) { slot[physical] = -1; return 1; }
    for (q = 0; q < 4; q++)
        if (q != (int)physical && slot[q] == (int)logical) write_back(q);
    memcpy(frame + physical * 0x4000, store + (size_t)logical * 0x4000, 0x4000);
    slot[physical] = (int)logical;
    return 1;
}

int emm_map(unsigned physical, unsigned logical)
{
    if (!opened || physical > 3) return 0;
    if (logical != 0xFFFF && logical >= pages) return 0;
    return map_page(physical, logical);
}

/* Handles of their own beside the main one (UW1's panel flips take three one-page handles):
   each a run of pages appended to the store, never given back (a freed handle's pages stay
   allocated and unused, which only a game that allocates without end would notice). */
#define EMM_HANDLES 16
static struct { unsigned base, n; int used; } xh[EMM_HANDLES];

int emm_alloc(unsigned n)
{
    int h;
    unsigned char *grown;
    if (!opened) return 0;
    for (h = 1; h < EMM_HANDLES && xh[h].used; h++)
        ;
    if (h == EMM_HANDLES) return 0;
    grown = realloc(store, ((size_t)total + n) * 0x4000);
    if (!grown) return 0;
    store = grown;
    memset(store + (size_t)total * 0x4000, 0, (size_t)n * 0x4000);
    xh[h].base = total;
    xh[h].n = n;
    xh[h].used = 1;
    total += n;
    return h;
}

int emm_map_handle(unsigned physical, int h, unsigned logical)
{
    if (!opened || physical > 3 || h <= 0 || h >= EMM_HANDLES || !xh[h].used) return 0;
    if (logical == 0xFFFF) return map_page(physical, 0xFFFF);
    if (logical >= xh[h].n) return 0;
    return map_page(physical, xh[h].base + logical);
}

void emm_free(int h)
{
    int q;
    if (h <= 0 || h >= EMM_HANDLES || !xh[h].used) return;
    for (q = 0; q < 4; q++)
        if (slot[q] >= (int)xh[h].base && slot[q] < (int)(xh[h].base + xh[h].n)) {
            write_back(q);
            slot[q] = -1;
        }
    xh[h].used = 0;
}

/* The frame's far pointer arithmetic (portable.h, EMS_WRAP and EMS_ADD). In DOS a far
   pointer's offset wraps at 64 KB, so a pointer into the frame moved past E000:FFFF comes back
   to E000:0000. The frame mapped twice (frame.c) wraps a pointer run up to 64 KB past the end by
   itself; with the frame mapped once (PORT_FRAME_SINGLE, the web build) nothing does, so the
   game's C says where it relies on it. */

int port_ems_has(const void *p)
{
    uintptr_t a = (uintptr_t)p, f = (uintptr_t)frame;
    return frame && a >= f && a < f + 0x10000;
}

int port_ems_runs_past(const void *p, unsigned long n)
{
    return port_ems_has(p) && (uintptr_t)p - (uintptr_t)frame + n > 0x10000;
}

/* EMS_ADD(p, n): p + n with the offset wrapping at 64 KB, for p in the frame; else p + n. */
void *port_ems_add(const void *p, long n)
{
    if (!port_ems_has(p)) return (unsigned char *)p + n;
    return frame + (((uintptr_t)p - (uintptr_t)frame + (uintptr_t)n) & 0xFFFF);
}

/* EMS_WRAP(p): p, a pointer into the frame or one the game has moved forward from such a pointer
   by any distance, as the start of what the game reads with the offset wrapping past FFFFh to
   the frame's start. The frame mapped twice: the pointer at p's offset in the frame. Mapped once:
   the frame from p's offset to its end and then from its start, twice over (an index from p can
   pass FFFFh too), copied to a buffer of the port's, which holds what the game reads through p
   until the next call or the next change to the frame. Only for reading. A pointer below the
   frame, or at or past the end of its two mappings (frame + 20000h: no longer the frame's, as
   the web build's heap goes on after its one), is returned as it is. */
const void *port_ems_wrap(const void *p)
{
    uintptr_t a = (uintptr_t)p, f = (uintptr_t)frame;
    size_t off;
    if (!frame || a < f || a - f >= 0x20000) return p;
    off = (size_t)(a - f) & 0xFFFF;
#ifdef PORT_FRAME_SINGLE
    if (off) {
        static unsigned char line[0x20000];
        memcpy(line, frame + off, 0x10000 - off);
        memcpy(line + 0x10000 - off, frame, off);
        memcpy(line + 0x10000, line, 0x10000);
        return line;
    }
#endif
    return frame + off;
}

int emm_open(unsigned min_pages, unsigned max_pages, unsigned free_pages, unsigned seg)
{
    opened = 0;
    if (free_pages < min_pages) return 0;
    pages = free_pages < max_pages ? free_pages : max_pages;
    total = pages;
    store = calloc(pages, 0x4000);
    if (!store) return 0;
    opened = 1;
    if (!frame && !(frame = port_frame_alloc())) port_fatal("ems: cannot map the page frame");
    pm_remove(frame);
    pm_add("EMS page frame", frame, 0x10000, seg);
    port_log("ems: %u pages\n", pages);
    return (int)pages;
}

void emm_close(void)
{
    if (opened) {
        free(store);
        store = NULL;
        opened = 0;
        memset(xh, 0, sizeof xh);
        slot[0] = slot[1] = slot[2] = slot[3] = -1;
    }
}
