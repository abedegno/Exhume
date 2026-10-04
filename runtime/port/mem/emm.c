/* emm.c: replaces the EMS 4.0 driver's memory, as a game that keeps one handle uses it (from
   UW2Decomp's src/port/mem/ems.c, whose EMS.C entry points stay UW2's and call this; Exhume's
   docs/port.md, "Far pointers and the paragraph map"). A store of 16 KB logical pages for the
   one handle, and a 64 KB page frame of four 16 KB slots that is a region of the paragraph
   map at the segment the project gives (UW2: E000h). Mapping a page into a slot copies the
   page that was there back to the store and copies the new one in, so the frame holds exactly
   what DOS's would, and the game's own record of what is mapped stays true. The frame is
   mapped twice in a row (frame.c), so a pointer run past its end wraps to its start as a far
   pointer's offset does. */
#include <stdlib.h>
#include <string.h>
#include "port.h"

static unsigned pages;
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

int emm_map(unsigned physical, unsigned logical)
{
    int q;
    if (!opened || physical > 3) return 0;
    if (logical != 0xFFFF && logical >= pages) return 0;
    if (slot[physical] >= 0) write_back((int)physical);
    if (logical == 0xFFFF) { slot[physical] = -1; return 1; }
    for (q = 0; q < 4; q++)
        if (q != (int)physical && slot[q] == (int)logical) write_back(q);
    memcpy(frame + physical * 0x4000, store + (size_t)logical * 0x4000, 0x4000);
    slot[physical] = (int)logical;
    return 1;
}

int emm_open(unsigned min_pages, unsigned max_pages, unsigned free_pages, unsigned seg)
{
    opened = 0;
    if (free_pages < min_pages) return 0;
    pages = free_pages < max_pages ? free_pages : max_pages;
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
        slot[0] = slot[1] = slot[2] = slot[3] = -1;
    }
}
