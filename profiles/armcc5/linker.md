# armlink 5.06u7: what matching taught

- A symdefs file (`#<SYMDEFS>#` header, then `0xADDR T|D name` lines) is an input file: list it
  with the objects. `--symdefs=FILE` is the option that writes one.
- An execution region `ER 0xADDR FIXED { obj(i.name) }` puts one function at its address;
  `fromelf --bin` then writes one file per region, named after it.
- MDK-Lite objects are in Keil's "ORC" format: the file starts `\xf7ORC` where an ELF object has
  `\x7fELF`, and fromelf will not write one out as ELF under the Lite licence. `fromelf --text -s`
  lists their sections and symbols, `-v` adds each section's file offset and `-r` the
  relocations. The section contents themselves are stored plainly at those offsets, which is what
  object placement (below) reads.
- `fromelf --i32` writes a directory, one file per load region, when there is more than one.
- armlink drops a section nothing references (default unused-section elimination): a `const
  char name[]` used only as `name[0]` was folded to a constant by armcc and its `.constdata` never
  reached the image. So a source's data section may have no address at all; the matcher links it
  out of the way (UNPLACED) and notes it, rather than failing.
- Sections are 4-byte aligned: a 0x22-byte function is followed by a 2-byte `PAD` in the map, and
  `fromelf --bin` pads its region to 0x24. The section's own size (`fromelf -s`) is what to
  compare.
- In `--map` output the entry section's row carries a `*` before its name.
- armlink orders the sections of an execution region by attribute (RO code, RO data, RW, ZI),
  then by section name in ASCII order (A-Z < `_` < a-z), then by input order, whatever order the
  scatter file lists them in. armcc names a function's section `i.<name>`, so a region of
  functions comes out in name order: the test firmware's `.ANY (+RO)` gives i.big_switch, i.buf,
  i.cfg, i.crc16, ..., i.version, and a Cortex-M3 firmware of about 500 functions had its whole
  code region in ASCII order, so a function's address bounds its real name between its
  neighbours'. Same-named sections (`.constdata`, `.data`, `.bss`, library `.text`) keep input
  order. Placeholders need link names that sort between their real neighbours (the gate's
  link_names).
- armlink's default `--branchnop` turns a branch to the next instruction into `nop.w`, so a 4-byte
  tail-call function placed right before its callee shows as a `nop.w`: split such a map row 4
  bytes in.
- armcc gives all of one file's const data a single `.constdata` section, so armlink can only drop
  dead data whose whole section is unreferenced: a dead table beside a live one is in the image.
  A source marks data the original never had with `/* dropped: NAME */`; any other data the map
  doesn't know blocks a whole match.
- Load and execution regions must start on a 4-byte boundary (L6244E), but a section of bytes and
  halfwords can be 2-aligned (a `.data` at 0x2000001A). The matcher puts such a section in a
  region at the aligned address below, after 1-3 pad bytes `+FIRST`. The pad must be the
  section's own kind: armlink starts an execution region's RW part after its RO part on a 4-byte
  boundary, so an RO pad before `.data` pushed it 2 bytes on.
- `--datacompressor off` keeps RW output plain when a match link has a load region whose RW
  execution address differs from its load address.
- armcc may keep a function's string literal inside its own section, after the code, reached by
  `adr` (`version()` returning "TestFW build 7": a 0x14-byte `i.version`); larger const tables go to
  `.constdata`. The map treats `adr` and `add rX, pc` targets as data up to the next known byte.
- A linked image has no unreferenced armcc functions (armlink removes them), so the map seeds
  Ghidra with what references code: vector handlers, region-table handlers, `bl` targets from a
  linear sweep, and Thumb code addresses in any aligned word of the image or its RW data
  (function-pointer tables). Seeding every point after a branch split real functions (about
  twice as many "functions" as there are); seeding by reference keeps them whole.
- Ghidra does not recover `tbb`/`tbh` tables in a raw binary; the map follows each case target
  itself.
- armcc reaches switch cases beyond a `tbb` table's 510 bytes through runs of 16-bit `b`
  trampolines placed after the function's return; the trampolines and their targets belong to that
  function. The switch's bound is a `cmp` a few instructions before the `tbb` (often with a `bhs`
  default between): with `bhs`/`bcs` to the default the table has that many entries, with `bhi`
  one more. These rules were needed for a real firmware's map to cover every byte.

## The whole-image gate and the modded build (armgate.py, armdata.py, armmod.py)

What rebuilding a whole image in pieces taught, on the test firmware and on a Cortex-M3 firmware
of about 500 functions that the gate now rebuilds byte for byte.

- MDK-Lite refuses an image over 32 KB (L6050U), and the limit counts every RO and RW section in
  the link: the piece's own functions, its files' other sections placed FIXED, dumped copies and
  library members. A 15 KB run of functions failed at 35 KB until it was split in two. Zero-init
  does not count: a link with a 40 KB blob of bytes failed, one with 60 KB of zero-init linked.
- A piece can start only on a 4-byte boundary at a section start (L6244E): a run that would start
  at a 2-aligned function starts at the next aligned row instead, and the function before it
  joins the previous piece.
- Linking a piece naturally (its function sections in one region, armlink choosing order and
  padding) and everything else FIXED at map addresses, with the rest of the image's symbols from
  symdefs, reproduces the image piece by piece. The order the scatter file lists the sections in
  does not matter; the names do (above).
- A name in a symdefs file is never pulled from a library. Leaving the library rows' names out of
  symdefs, and listing a stub object that references them (kept with `--keep`), makes armlink link
  the real members. Members load in resolution passes: the first loads, in archive order, what
  the objects reference; later passes load what those members need. Their `.text` sections then
  keep that load order. So the stub requests only what the project's objects reference (their
  `Ref` symbols), and the members those need follow as in the original. A stub that requested
  every member put two 64-bit shift helpers before the memory and string functions, against the
  image.
- Every armcc object references `Lib$$Request$$armlib`. Requested from a stub, it made armlink
  stop with an internal fault: never request linker-made `$$` names.
- An entry point a member's object keeps local (a printf variant's internal helpers) is
  undefined to a stub (L6218E); the gate drops it from the stub and links again.
- armlink adds the RW decompressor only when it compresses RW data, and the region handlers only
  for the region kinds the link has. The startup piece links the image's own RW bytes (and a
  zero-init array for each zero region) so armlink makes the same choices: MicroLIB's
  zero-run-length decompressor (`__dczerorl2.o`, `__decompress`/`__decompress1`) and the
  zero-init handler, as the original link did.
- A library member the image has elsewhere (the region handlers or a printf variant, sorted
  among the functions by their `i.` names) must be linked out of the startup piece by object
  name: an object-name selector (`handlers.o (+RO)`) beats a library wildcard (`*.l (+RO)`). The
  gate finds them from a first link's `--symbols` listing.
- Section-name selectors beat attribute selectors: a stub's const table, listed `stub.o (+RO)` in
  one region, still went to a region holding `*(.constdata)`. The data stage's stub is code only.
- armlink removes unused sections only from a link with an entry point. A `--no_startup` link
  with no `--entry` kept a string the original's link had dropped; with `--entry` on a stub
  function it went, as it should.
- MicroLIB's scatter-load code and handlers must be in a root region (L6202E: cannot be assigned
  to a non-root region). A section fixed at an address that is not 4-aligned therefore goes in a
  root region at the aligned address below, after a pad `+FIRST`, in flash; in RAM the pad's
  region keeps its load address out of the way, as the matcher does.
- The region table's handler addresses come from wherever the handlers are linked. The data stage
  links once with the library members free, reads which member each table entry names, and links
  again with those sections FIXED at the addresses the image's own table gives, so the table comes
  out as the original's. `__scatterload`'s two literal words (the table's base and limit) are
  taken from that link too.
- The compressed RW stream may end a byte or more before its load region, which ends on a 4-byte
  boundary: the image's data ends there, padded with zero.
- A function or global that a partial file defines although another file owns it is renamed in
  that file (`--preinclude` of `#define name name__dup_FILE`) and dumped past the image. The dump
  must stay within `bl` range of the piece: a dump at a distant address made armlink add veneers
  to the piece.
- A known diff that grows its function pushes the piece into the next FIXED region (L6221E:
  load regions overlap); the gate moves those regions aside and reports the functions the shift
  affects rather than failing them.
- Each Keil tool call under Wine boots a wineserver unless one is running in the prefix (about 6
  s under Rosetta), and a prefix's server serialises its clients. The gate runs its compiles and
  links in worker processes, each with its own prefix and a persistent `wineserver -p`: the whole
  gate for the 500-function firmware went from 29 minutes to 45 seconds. A new prefix stops at
  Wine's offer to install Mono and Gecko unless `WINEDLLOVERRIDES=mscoree,mshtml=` is set.
- The first program run under a new wineserver starts Wine's services (services.exe, winedevice
  and others), which inherit that program's output pipes and live as long as the server. When
  that first program is a Keil tool run with its output captured, the output never reaches end of
  file and the call never returns. Run `wine cmd /c exit`, with no pipes, in each new prefix (or
  after starting each server) before any tool, and give every stage a timeout so a hang fails
  instead of waiting forever.
- `fromelf --i32` writes 16-byte data records, an extended linear address record at the start
  and at each 64 KB boundary, a start linear address record (the entry point) and CRLF line ends;
  writing the rebuilt bytes the same way (ehex.write) gives a file identical to the shipped one.

The data stage:

- armcc gives an object one `.constdata`, one `.data` and one `.bss` for the data its functions
  use, and each global no function in the file uses a section of its own. armlink lays each kind
  out in object link order, so each kind's map rows split into one run per file, and one object
  order serves all three kinds. Merging the three address orders gives that order; a conflict
  means a row has the wrong owner.
- Linking every data-owning file whole keeps the code its data points at (a table of handlers)
  and that code's callees: over 32 KB. The data stage compiles each file with its function bodies
  taken out, except a function that declares a static (the static is its data), plus one function
  that stores the address of every global the file owns through a pointer parameter. armcc drops
  all but the last store to a local, even through a volatile-qualified pointer, and with them the
  references; stores through a parameter stay.
- A modded build's RW image may compress to another length; the data stage compares the
  original's range and hands back everything it linked.

The modded build:

- The new code links as one more piece in free flash, its RAM in an UNINIT region above
  everything the image uses (its data and the stack). Nothing initialises that RAM at reset, zero
  or not: the original's region table does not know it. armcc puts zero globals of up to 8 bytes
  in `.data` (`--bss_threshold`), so even a "zero-init" counter in the mod starts as whatever the
  RAM held. The mod sets its state up on first use or in a reset hook.
- The mod's own symbols go to every link of the gate as extra symdefs, so edited sources can call
  it or point at it, and everything else stays at its address.
- Changes inside the original image are kept to the same size: a vector or a pointer redirected,
  a table's address in literal pools, an immediate. A word-by-word check against the original
  allows exactly those (armmod.check_allowed) and counts them.

## A firmware built without --split_sections, with the standard C library

What a second Cortex-M3 firmware of about 500 functions taught, built at `-O0` without
`--split_sections` and linked with the standard C library (not MicroLIB). Its whole image is
rebuilt byte for byte.

- Without `--split_sections` a source file is one `.text`, and one file can pass 32 KB: a 45 KB
  file of 15 functions could not be linked by MDK-Lite at all. `tools/objplace.py` proves such a
  file by doing armlink's job for its one object: each section placed where the matcher places it,
  every relocation applied with the addend in place (REL: `R_ARM_ABS32`, `R_ARM_REL32`,
  `R_ARM_THM_CALL`, `R_ARM_THM_JUMP24`, `R_ARM_THM_JUMP19`, MOVW/MOVT, `R_ARM_NONE`), symbols
  resolved from the object and the same symdefs the armlink route gets, a weak definition giving
  way to a symdefs one, every placed byte compared, nothing masked. An unresolved symbol, an
  unknown relocation, a branch to data or out of range, or a relocation in the wrong mapping state
  (`$t` for branches, `$d` for literal words) fails. Before it was trusted it was run beside
  armlink over every source armlink could link (115 sources, 2,476 relocations): the same bytes
  each time. armcc emitted only `R_ARM_THM_CALL` and `R_ARM_ABS32` in that firmware.
- Alignment padding between two files: where 1 to 3 zero bytes lie between two sections and end on
  the second's alignment, they are armlink's padding, made by linking the two in one region (the
  first `+FIRST`). It only applies when the second section is 4-aligned in its object, which for a
  `.text` means it holds a literal pool (compiler.md).
- The region table follows the last RO section. When that section ends off a 4-byte boundary
  (a byte table ending at 4n+2), armlink pads before `Region$$Table`, which holds words. A link
  that makes the table cannot start a region at the unaligned end (L6244E), so it starts the
  table's region at the 4-byte boundary below with a byte-aligned area standing in for the end of
  the section (an assembler area of 1 to 3 bytes, byte-aligned); armlink then places the
  table on the next boundary and fills the gap itself. With stand-ins of 1, 2, 3 and 4 bytes it put
  the table at the same address with a PAD of 3, 2, 1 and 0 bytes, and the table and the
  compressed RW data came out the same. Check the PAD row in the listing and that the padding
  bytes are zero and the image's.
- The standard library's start-up is `__main`, `__scatterload` (`__scatterload_rt2`),
  `__decompress1`, the zero-init handler and the `.ARM.Collect$$` pieces of `__rt_entry`,
  `__rt_lib_init`, `__rt_lib_shutdown` and `__rt_exit`, all linked from the toolchain's own `c_w.l`
  (and `fz_ws.l` for float helpers) by requesting the entry points the objects reference. armcc
  defines `__ARM_use_no_argv` in a file with `int main(void)`; a library link without it adds the
  argv set-up. `__user_libspace` must sit at the original's address, so the zero-init region is
  laid out around the library's own `.bss`.
- A startup file's default handlers are weak, and the application's real handlers override them
  at their own addresses. The startup source names those addresses on its `; at:` lines; the
  matcher (armmatch.place) then places its section by its strong symbols and takes each weak name
  an at: line puts elsewhere as overridden, so it comes from symdefs at the real handler's
  address. Names the startup file only references (`__main`, the heap set-up helpers)
  need `; at:` lines too when it is matched without the library.
