# The link stage, and why UW2's is a reference implementation

Linking the matched objects into the whole executable is the last and strongest check (skills/link-and-diff). For UW2 it is done by `examples/uw2/extract.py` and `examples/uw2/link.py`, kept as UW2Decomp wrote them with only their paths moved into exhume.toml. They are not generalised, because most of their lines are facts measured from UW2.EXE rather than rules, and half-generalising them would hide which is which. This page separates the two, so the next game can reuse the rules and measure its own facts.

The generic tools around them are general: `tools/build.py` builds the objects, `tools/dosrun.mjs` runs TASM, TLIB and TLINK in headless DOS, `tools/exediff.py` compares the result, `tools/rungame.mjs` runs it.

## Rules (true of TLINK 3.01, Turbo C++ 1.01 and the VROOMM overlay manager)

These are in profiles/borland-tc101/linker.md with their evidence. In short:

- TLINK writes relocations module by module, and takes a library's modules in library order, even ones it finds it needs on a later pass. So the relocation table records the original module order, and a library's relocation order is its module order.
- The overlay manager's segment table (`__SEGTABLE__`, located from the FBOV header after the load module) has one entry per segment, `{paragraph, end, flags, start}`, in link order. Overlay stubs are entries with flags 3; after a 0x20-byte header each stub entry is `INT 3Fh`, the function's offset in its overlay, and a zero.
- TLINK numbers an overlay's stub entries from the last public its object lists, and Turbo C lists publics by a hash of the name (profiles/borland-tc101/bssorder.py). So the stub order constrains the names in an overlay, and a public with no stub entry was `static`.
- TLINK places each segment where it first sees its name. Empty segments declared early in a small module reproduce the original segment order and alignment; empty entries in the segment table are segments the original link had.
- Each `far` variable in a Turbo C file is its own paragraph-aligned segment, so the order of far data segments shows which file defined each one.
- TLINK stores the output name (`__EXENAME__`) and the DOS date (`__EXEDATE__`) in the image: link under the original name, after setting the date.
- Alignment padding between separately assembled modules is zero bytes to the segment's alignment: that is how a segment that was several modules is split.
- A relocation in the EXE where the object has no fixup is a constant in the source where the original had a segment.

## The general shape of extract.py

1. For every source, place its `_DATA` and `_BSS` and resolve every extern, by running `verify.main()` on it (no sys.argv involved: `verify.main(argv, on_names=...)`).
2. Find the C library's modules in the EXE by searching for their code and data with fixups masked, to name their publics.
3. Collect every name's address: object publics, overlay stub entries, library publics, startup code.
4. Find what no source covers: code in the resident segments, far data segments, gaps in DGROUP between files' data. Write each as a data-only TASM module, with a label for every name defined inside it and a `dd NAME` or `dw seg NAME` for every MZ relocation inside it.
5. Check the sources against the EXE: names that resolve elsewhere, overlay functions with no stub entry, overlay publics out of stub order, relocations without fixups, data starting at an odd address. Each is a defect in a source, reported, never patched into the object.
6. Write the manifest: resident modules, overlays, the second library's modules, in the original order.

link.py then assembles the generated modules, builds the second library with TLIB, sets the date, and runs `TLINK /c /m /s` from a response file (the module list is longer than a DOS command line), then calls exediff.

## Facts that are UW2's (measure these again for another game)

In extract.py:

- The header size and segment table length it asserts (0x2C00, 0xAE entries), DGROUP as segment table entry 168, C0's `_TEXT` (with the C library appended) as entry 6.
- Overlay stubs as entries 91 to 167 (ovr091 to ovr167), far data as entries 50 to 78, alignment padding in entries 3, 4 and 23.
- The C library's data range (DS:1BF4 to DS:204E), the second library's (to DS:2220, where the overlay manager's `__OvrSize` is) and the C library's `_BSS` start (DS:865C), all measured from TLINK's map of this link.
- C0's `_DATA` at DS:4 and the offsets of its routines in UW2's C0 (`__exit` at 0x133 and so on).
- The module order lists `RES`, `LATE` and the overlay list, recovered from UW2's relocation table: seg000 to seg004 before C0's `_TEXT`, seg003, seg004 and seg045 in a second library after CM.LIB, seg021's first module resident and the rest in that library.
- The names of generated modules and the source files it treats specially (SEG046, the overlay manager, comes from OVERLAY.LIB itself; FARDATA.ASM is marked `/* fardata */`).

In link.py:

- C0.ASM's three changes (the exit-table patch after main returns, the null-pointer checksum starting at `DATASEG@`, a word-aligned `_FARDATA`), found by comparing UW2's startup code with Turbo C++'s.
- The output name `uwedit.exe` and the date 12 May 1993.
- The libraries `CM.LIB UWLIB.LIB OVERLAY.LIB`, with no floating point libraries (UW2 uses none).

## Adapting it to another game

1. Copy examples/uw2/extract.py and link.py into the new project's example directory.
2. Replace every fact above with the new EXE's, measured the same way: the segment table, the relocation order, a first TLINK map of a trial link (`/m /s`) for library data ranges, the startup code compared byte by byte with the stock C0.
3. Start with everything extracted (all code and data from the EXE) and confirm the link reproduces the EXE; then replace extracted pieces with matched objects one at a time. exediff after each change shows exactly what moved.
4. Keep every check that stops the link on a source defect. A link that patches objects to make them fit hides the defects it was meant to find.
