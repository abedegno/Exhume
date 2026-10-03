/* fuzzhost.c: the port's side of tools/fuzzasm.py, the differential fuzzing of single routines
   (docs/port.md, "Fuzzing single routines"). Not part of the port: fuzzasm.py compiles it with
   the port's flags and links it with the port's objects in place of the port's main ([fuzz]
   main), so the routines it calls are the port's own C and translations.

   The project's side is one C file, [fuzz] host_glue, included below (FUZZ_GLUE; UW2's is
   examples/uw2/port/fuzzhost-uw2.c). It defines:

     struct fuzz_region fuzz_regions[]; int fuzz_nregions;
                    the memory a case may set and the routines may change, each a block of
                    host memory at a linear DOS address (the far data, the code segments the
                    translated code reads as data, two scratch segments)
     int fuzz_init(const char *exe);
                    loads what the port loads from the user's EXE and maps the scratch segments
                    into the paragraph map; 0 when ready
     int fuzz_call(const char *kind, unsigned seg, unsigned off, uint32_t *c);
                    runs a call kind of its own (a C entry such as UW2's cSqRt, taking and
                    leaving its values in the emulated registers); 1 when it knew the kind

   It then serves cases on its standard input, one at a time. Before each case every region is
   put back as it was after fuzz_init. A case is text lines:

     R eax ebx ecx edx esi edi ebp esp ds es ss fs gs flags   the registers (hex)
     M linear hexbytes                                       memory to set first (any number)
     C kind seg off retcs retip                              the call

   and the answer is the registers in the same order after the call, `S status message` (0 the
   routine returned, 1 the port stopped: port_halt, as at a divide the port does not model), the
   memory that changed (`D linear hexbytes`, runs of changed bytes) and `E`. The kinds `near`
   and `far` call seg:off (an EXE paragraph and offset, as ASM_JMP takes them) through the
   translated code's machine (runtime/port/x86/asmrt.h): the translation there, or the
   hand-written C its glue sends the call to. */
#include <setjmp.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "port.h"
#include "x86/asmrt.h"

struct fuzz_region { const char *name; unsigned char *p; uint32_t lin, size; unsigned char *saved; };

extern int asm_level;

/* what the port's main gives the rest of the port */
int port_trace;
void port_log(const char *fmt, ...) { (void)fmt; }
void port_fatal(const char *fmt, ...)
{
    va_list ap;
    va_start(ap, fmt);
    vfprintf(stderr, fmt, ap);
    va_end(ap);
    fputc('\n', stderr);
    exit(1);
}
static jmp_buf halted;
static char halt_why[256];
void port_halt(const char *why)
{
    snprintf(halt_why, sizeof halt_why, "%s", why);
    longjmp(halted, 1);
}
void port_on_flip(void) {}

#ifndef FUZZ_GLUE
#error "compile with -DFUZZ_GLUE='\"path\"': the project's regions and call kinds ([fuzz] host_glue)"
#endif
#include FUZZ_GLUE

static unsigned char *at(uint32_t lin)
{
    int i;
    for (i = 0; i < fuzz_nregions; i++)
        if (lin >= fuzz_regions[i].lin && lin < fuzz_regions[i].lin + fuzz_regions[i].size)
            return fuzz_regions[i].p + (lin - fuzz_regions[i].lin);
    return NULL;
}

static int hexval(int c) { return c <= '9' ? c - '0' : (c | 32) - 'a' + 10; }

int main(int argc, char **argv)
{
    static char line[1 << 21];
    uint32_t r[14];
    int i;
    struct fuzz_region *R = fuzz_regions;
    if (argc < 2) { fprintf(stderr, "usage: fuzzhost EXE\n"); return 2; }
    if (fuzz_init(argv[1])) return 1;
    for (i = 0; i < fuzz_nregions; i++) {
        R[i].saved = malloc(R[i].size);
        memcpy(R[i].saved, R[i].p, R[i].size);
    }
    printf("READY\n");
    fflush(stdout);
    while (fgets(line, sizeof line, stdin)) {
        if (line[0] == 'R') {
            for (i = 0; i < fuzz_nregions; i++) memcpy(R[i].p, R[i].saved, R[i].size);
            sscanf(line + 1, "%x %x %x %x %x %x %x %x %x %x %x %x %x %x", &r[0], &r[1], &r[2], &r[3], &r[4],
                   &r[5], &r[6], &r[7], &r[8], &r[9], &r[10], &r[11], &r[12], &r[13]);
        } else if (line[0] == 'M') {
            uint32_t lin = (uint32_t)strtoul(line + 2, NULL, 16);
            char *h = strchr(line + 2, ' ');
            for (h = h ? h + 1 : line + strlen(line); h[0] && h[1] && h[0] != '\n'; h += 2, lin++) {
                unsigned char *p = at(lin);
                if (p) *p = (unsigned char)(hexval(h[0]) << 4 | hexval(h[1]));
            }
        } else if (line[0] == 'C') {
            char kind[16];
            unsigned seg, off, rcs, rip;
            int status = 0;
            uint32_t c = 0;
            sscanf(line + 2, "%15s %x %x %x %x", kind, &seg, &off, &rcs, &rip);
            EAX = r[0]; EBX = r[1]; ECX = r[2]; EDX = r[3]; ESI = r[4]; EDI = r[5]; EBP = r[6]; ESP = r[7];
            SET_DS(r[8]); SET_ES(r[9]); SET_SS(r[10]); SET_FS(r[11]); SET_GS(r[12]);
            asm_set_flags((uint16_t)r[13]);
            asm_level = 0;
            halt_why[0] = 0;
            if (setjmp(halted)) status = 1;
            else if (!strcmp(kind, "near")) c = asm_call(ASM_JMP(seg, off), (uint16_t)rip);
            else if (!strcmp(kind, "far")) c = asm_callf(ASM_JMP(seg, off), (uint16_t)rcs, (uint16_t)rip);
            else if (!fuzz_call(kind, seg, off, &c)) {
                status = 1; snprintf(halt_why, sizeof halt_why, "unknown kind %s", kind);
            }
            if (!status && c) { status = 1; snprintf(halt_why, sizeof halt_why, "returned past the call (%08X)", c); }
            printf("R %x %x %x %x %x %x %x %x %x %x %x %x %x %x\n", EAX, EBX, ECX, EDX, ESI, EDI, EBP, ESP,
                   asm_ds, asm_es, asm_ss, asm_fs, asm_gs, asm_flags());
            printf("S %d %s\n", status, halt_why);
            for (i = 0; i < fuzz_nregions; i++) {
                uint32_t k = 0;
                while (k < R[i].size) {
                    uint32_t e;
                    if (R[i].p[k] == R[i].saved[k]) { k++; continue; }
                    for (e = k; e < R[i].size && R[i].p[e] != R[i].saved[e]; e++) ;
                    printf("D %x ", R[i].lin + k);
                    for (; k < e; k++) printf("%02x", R[i].p[k]);
                    putchar('\n');
                }
            }
            printf("E\n");
            fflush(stdout);
        }
    }
    return 0;
}
