/* fuzzhost-uw2.c: UW2's side of tools/fuzzhost.c ([fuzz] host_glue): the memory a fuzzing
   case may set and the routines may change, and UW2's C entries as call kinds. Included by
   fuzzhost.c, which UW2Decomp's port links in place of src/port/sys/main.c.

   The regions: the far data block the port loads from UW2.EXE (seg_370D, the graphics
   library's data, to 6061Ch), seg021's data (FD71), the code block of seg003 and seg004 (the
   translated code reads patched immediates there), and two scratch segments, E000 for data and
   F000 for the stack. The kinds:

     cfst          cFstSinCos(BX): AX, BX = the two results (sys/imath.c)
     csqrt         cSqRt(CX:BX): DI = the result
     csincos       cSinCos(BX): AX, BX
     catan2        cAtan2(AX, BX): CX
     uncmp         seg004_uncmp(BX format, AX image, BP size offset, DS:SI palette, DH row):
                   AX = the result (3d/expand.c, cFrmtoRaw's decoder table) */
#define FAR_FIRST 0x37050u
#define FAR_END 0x6061Cu
#define CODE_FIRST 0x00850u
#define CODE_SIZE (0x065C0u - CODE_FIRST + 0x10010u)
#define FD71_SEG 0x60B9u
#define SCRATCH_SEG 0xE000u
#define STACK_SEG 0xF000u
#define LOAD (PORT_LOAD_SEG * 16u)

extern unsigned char port_far_block[];
extern unsigned char port_code_block[];
void cFstSinCos(int angle, int16_t *a, int16_t *b);
int cSqRt(int32_t v);
void cSinCos(int angle, int16_t *x, int16_t *y);
int cAtan2(int x, int y);
uint16_t seg004_uncmp(uint16_t bx, uint16_t ax, uint16_t bp, const uint8_t *pal, uint8_t dh);

static unsigned char scratch[0x10010], stack[0x10010];
struct fuzz_region fuzz_regions[] = {
    { "far data", port_far_block, FAR_FIRST + LOAD, FAR_END - FAR_FIRST, 0 },
    { "seg021 data", dseg062_62a6, FD71_SEG * 16u + LOAD, DSEG062_62A6_SIZE, 0 },
    { "seg003-seg004 code", port_code_block, CODE_FIRST + LOAD, CODE_SIZE, 0 },
    { "scratch E000", scratch, SCRATCH_SEG * 16u, 0x10000, 0 },
    { "stack F000", stack, STACK_SEG * 16u, 0x10000, 0 },
};
int fuzz_nregions = (int)(sizeof fuzz_regions / sizeof fuzz_regions[0]);

int fuzz_init(const char *exe)
{
    if (port_load_exe(exe)) return 1;
    pm_add("fuzz scratch", scratch, 0x10000, SCRATCH_SEG);
    pm_add("fuzz stack", stack, 0x10000, STACK_SEG);
    return 0;
}

int fuzz_call(const char *kind, unsigned seg, unsigned off, uint32_t *c)
{
    (void)seg; (void)off; (void)c;
    if (!strcmp(kind, "cfst")) {
        int16_t a, b;
        cFstSinCos(BX, &a, &b);
        AX = (uint16_t)a; BX = (uint16_t)b;
    } else if (!strcmp(kind, "csqrt")) DI = (uint16_t)cSqRt((int32_t)((uint32_t)CX << 16 | BX));
    else if (!strcmp(kind, "csincos")) {
        int16_t a, b;
        cSinCos(BX, &a, &b);
        AX = (uint16_t)a; BX = (uint16_t)b;
    } else if (!strcmp(kind, "catan2")) CX = (uint16_t)cAtan2((int16_t)AX, (int16_t)BX);
    else if (!strcmp(kind, "uncmp")) AX = seg004_uncmp(BX, AX, BP, pDS + SI, DH);
    else return 0;
    return 1;
}
