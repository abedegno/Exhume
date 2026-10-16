/* Synthetic memory layout for the ti-c2000 profile's integration tests. */
MEMORY
{
PAGE 0:
   FLASH    : origin = 0x080000, length = 0x002000
   RAMF     : origin = 0x00B000, length = 0x000400
   RESET    : origin = 0x3FFFC0, length = 0x000002
PAGE 1:
   STACK    : origin = 0x00A000, length = 0x000400
   RAMD     : origin = 0x00A400, length = 0x000800
}
SECTIONS
{
   .text    : > FLASH, PAGE = 0
   .cinit   : > FLASH, PAGE = 0
   .econst  : > FLASH, PAGE = 0
   .switch  : > FLASH, PAGE = 0
   ramfuncs : LOAD = FLASH, RUN = RAMF, LOAD_START(_ram_code_load),
              LOAD_SIZE(_ram_code_size), RUN_START(_ram_code_run), PAGE = 0
   .ebss    : > RAMD, PAGE = 1
   .stack   : > STACK, PAGE = 1
   .reset   : > RESET, PAGE = 0, TYPE = DSECT
}
