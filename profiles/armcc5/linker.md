# armlink 5.06u7: what matching taught

- A symdefs file (`#<SYMDEFS>#` header, then `0xADDR T|D name` lines) is an input file: list it
  with the objects. `--symdefs=FILE` is the option that writes one.
- An execution region `ER 0xADDR FIXED { obj(i.name) }` puts one function at its address;
  `fromelf --bin` then writes one file per region, named after it.
- MDK-Lite objects are in Keil's "ORC" format. `fromelf --text -s` lists their sections and
  symbols; nothing else may read them.
- `fromelf --i32` writes a directory, one file per load region, when there is more than one.
- armlink drops a section nothing references (default unused-section elimination): a `const
  char name[]` used only as `name[0]` was folded to a constant by armcc and its `.constdata` never
  reached the image. So a source's data section may have no address at all; the matcher links it
  out of the way (UNPLACED) and notes it, rather than failing.
- Sections are 4-byte aligned: a 0x22-byte function is followed by a 2-byte `PAD` in the map, and
  `fromelf --bin` pads its region to 0x24. The section's own size (`fromelf -s`) is what to
  compare.
- In `--map` output the entry section's row carries a `*` before its name.
- Without `--keep`, `*(+RO)` sorts the input sections (by name here: i.crc16, i.main,
  i.next_frame, ...), not in object order. The gate (Plan 3) must learn the original's order.
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
