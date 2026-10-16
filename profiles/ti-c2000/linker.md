# lnk2000 and hex2000: what matching taught

The evidence is the same as compiler.md's: a whole F2806x firmware rebuilt word for word by the
gate in a private project, and the synthetic firmware in tests/fw, whose integration tests
(tests/test_integration.py) link it through the gate with both linkers.

## One link for the whole image

- lnk2000 has no size limit, so the gate links the whole image in one run (armcc5's MDK-Lite has
  to link in 32 KB pieces). The link takes seconds; the compiles take most of the gate's time.
- The native lnk2000 of 18.12 and the 15.12 and 16.9 lnk2000 under Wine gave the same image, on
  the firmware and on the synthetic one. The native one needs no Wine start, so match.py always
  uses it and the gate uses it by default (`[c2000.gate] linker = "cgt"` picks the compiler's
  own). Both read the COFF objects of every compiler version I tried, and 16.9.6's linker took
  an object that 18.12's cl2000 assembled.
- Each function's subsection (`--gen_func_subsections=on`) is placed at its address by name,
  one output section each:

      SECTIONS
      {
        loopctl_text__loop_step : { "loopctl.obj"(.text:_loop_step) } load = 0x080193, PAGE = 0
        ...
      }

- Every subsection is `.clink` (conditionally linked). A link with no entry point removes all of
  them unless the linker is given `--disable_clink`. `--unused_section_elimination` is not
  accepted for COFF output.
- A section at a fixed address outside every MEMORY range is put in a made-up DEFAULT range, and
  a second such section then fails with "DEFAULT memory range overlaps". The gate's MEMORY covers
  the whole 22-bit space in ranges, with a parking range on each page outside the device for
  what is not compared (`[c2000] park`, `park_data`). match.py's links have no MEMORY at all,
  which the linker accepts.
- In an output section such as `.text` the linker allocates the input sections largest first
  (the synthetic link's map lists them by falling size). So the order of functions in an image
  says little about the files: the firmware's library members sat between the application's
  functions. The gate does not depend on the order, since it places every section by address.

## Ramfuncs

- A function under `#pragma CODE_SECTION(f, "ramfuncs")` is loaded in flash and runs from RAM
  after the start-up code copies it. It is compiled for its run address, so its calls and jumps
  to other ramfuncs go to RAM addresses. The map gives the load address in `addr` and the run
  address in the notes (`run=0x00B000`); match.py links the section at its run address and
  compares its words with the image at the load address; the gate links it with both:

      ram_text : { "helpers.obj"(ramfuncs:_fast_sum) } load = 0x080280, run = 0x00B000, PAGE = 0

- The original link's `LOAD_START()`, `LOAD_SIZE()` and `RUN_START()` symbols are not made again
  by the gate's link, which has no `ramfuncs` output section. They come from the map like any
  other symbol: the code holds them as constants (`MOVL XAR4,#sym` or `MOV AL,#sym;
  MOV AH,#sym>>16`), and the map gives them rows.
- **The wrong-name trap.** A name that spells an address resolves to that address, so a
  ramfunc's placeholder (`sub_` and its load address) resolves to the load address. Calls want
  the run address. While the placeholder is the map row's name, the row resolves it to the run
  address. Once the row is renamed, a source that still uses the old name links its calls to
  flash, and the words differ. Rename every use with the row.

## .cinit

- A COFF `.cinit` table is a run of records: a count (negative: a 32-bit address follows;
  positive: a 16-bit one), the address, then the data words. The firmware's records all used
  the 32-bit form. The table ends with a 0 word.
- With `--rom_model` the linker sets `cinit` to the start of the output section named `.cinit`,
  where the library's start-up code finds the table. After the last record it adds two words of its
  own: in the synthetic link's map, a 2-word `--HOLE-- [fill = 0]` at the end of `.cinit`. The gate
  counts them as words the linker made (`[c2000.gate] cinit_tail`, default 2).
- The records come file by file, in the order the original link met the files with initialised
  data, and each file's records in its `.ebss` order. That is not the order of the same files'
  code. The gate builds `.cinit` from the objects in `[c2000.gate] cinit_order`, with library
  members as `rts:member.obj`, and checks that the linked records are the image's, in order.
  The record order is how I found which file owned which variables: each file's records form one
  run.
- A library member's records are linked as `lib<member>(.cinit)`, which takes its subsections
  too (`.cinit:__lock` and `.cinit:__unlock` of _lock.obj) in the linker's own order. Listing the
  subsections one by one put them the wrong way round.
- A member that the image has only data of (no code) is linked for its record, with its `.ebss`
  where the image's code refers to its symbol.

## Linker-made symbols

- `--rom_model` with no copy table and no `.pinit` sets `binit` and `pinit` to -1, which the
  start-up code loads as `MOVL XAR7,#0x3FFFFF`, and `___TI_pprof_out_hndl` to -1. `__stack` is
  the start of `.stack`: the gate places `.stack` at `[c2000.gate] stack`, and the image's
  `MOV SP,#...` must come out the same.
- The gate never defines these from the map. They come from the link, so the compare checks
  them.

## The runtime library

- A member is linked only for a symbol that something needs. The gate asks for one global of
  each member it places with `-u`, and places each of the member's sections with
  `rts2800_fpu32.lib<boot28.obj>(.text)` inside the braces. The library is named once with `-l`
  and its directory with `-i`, after the objects; no `-l` inside the braces.
- Libraries are searched once, where they stand on the command line. With `-i DIR -l LIB` before
  the objects, the synthetic link resolved what `--rom_model` asks for at the start but left the
  float division helper, which only an object needed, unresolved. Put the libraries after the
  objects, or give `--reread_libs`.
- **Mapped symbols must not shadow library members.** A symbol defined from the map (`sym =
  0x...;` in the command file) is never taken from a library. The first gate defined every symbol
  the sources used from the map, the library routines' too, and the linker then left those members
  out: their words came out as fill, and the library's boot member was dropped, because the source
  that calls it found the name among the definitions. The gate now drops every symbol a placed
  member defines from its definitions.
- The members' data sections (a member's `.ebss`, or `.ebss:__lock` of _lock.obj) go where the
  image's relocated words in the members' code put them (`c28tc.derive_bases`).
- A section of fewer than 4 words fits too many places by its words alone (one-word returns are
  everywhere). The fingerprint places it from a call: where a placed member calls it and the
  words at the target fit. In the synthetic image that places two of the start-up code's callees.

## hex2000

- For a C28x image, `-romwidth 16` and `-order MS` (most significant byte of each word first)
  write the big-endian words a dump of the flash holds. The ROMS origin and length are then byte
  addresses: twice the word address. For words 0x080000 to 0x081FFF:

      ROMS { FLASH: origin = 0x100000, length = 0x4000, romwidth = 16, files = { app.bin } }

- With `-image -fill 0xFFFF` the holes come out as erased flash, and the file covers the whole
  range.
- hex2000 reads file names in a ROMS block only as plain names: a full path with `/` is a syntax
  error. Run it in the output's directory.

## Reading objects

- ofd2000's XML (`-x --obj_display=none,sections,relocs,symbols,header`) nests `<section>`
  inside symbols too: read only the direct children of `<ti_coff>`. Raw data offsets can be odd.
- Relocations name either a symbol or a section (`.ebss` for a file's own variables). The bits
  each type writes:

  | Type | Writes |
  |---|---|
  | R_REL22 | 6 bits in the opcode and the whole next word (LCR, LB, MOVL XARn,#22-bit) |
  | R_PARTMS16 | the data page word of `MOVW DP,#16` |
  | R_PARTLS6 | the low 6 bits of the opcode (a direct-page offset) |
  | R_ANKPTR, R_RELLONG | two words (pointers, `.cinit` addresses) |
  | R_RELWORD, R_ABS16_OC | one word |

  Masking whole words around a data-page access hides too much: with whole words masked, the
  fingerprint could not place one member.
- Static functions and variables are `C_LABEL` symbols (in 15.12, 16.9 and 18.12 objects alike);
  `C_STAT` names sections.
- dis2000 reads only objects, so `tools/c28disasm.py` links a placeholder: one section of
  `.space 16*N` (`.space` counts bits) at the address, linked once and cached. It overwrites the
  section's raw data in the `.out` with the words and runs `dis2000 -i` (data as text). One
  dis2000 run per disassembly, and nothing assembled.
- dis2000 prints a parallel instruction's second half (`|| MOV32 ...`) on a line of its own;
  the tools append it to the first half, so diffs show it.
- Constants loaded with `MOVL XARn,#imm` that land in a code range are not always code addresses
  (small counts in code that runs at a low RAM address), and byte tables can hold values that
  look like addresses. A map should take code addresses from constants only where the code is.
