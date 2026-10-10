# The link stage, and why UW2's is a reference implementation

Linking the matched objects into the whole executable is the last and strongest check (skills/link-and-diff). For UW2 it is done by `examples/uw2/extract.py` and `examples/uw2/link.py`, kept as UW2Decomp wrote them with only their paths moved into exhume.toml. They are not generalised, because most of their lines are facts measured from UW2.EXE rather than rules, and half-generalising them would hide which is which. This page separates the two, so the next game can reuse the rules and measure its own facts.

The generic tools around them are general: `tools/build.py` builds the objects, `tools/dosrun.mjs` runs TASM, TLIB and TLINK in headless DOS (the one `tools/dosbackend.mjs` picks, docs/method.md, "Choosing the DOS"), `tools/exediff.py` compares the result, `tools/rungame.mjs` runs it, `tools/modding.py` holds the parts of the modding build that are not UW2's, and `tools/addrscan.py` lists addresses written as numbers.

## Rules (true of TLINK 3.01, Turbo C++ 1.01 and the VROOMM overlay manager)

These are in profiles/borland-tc101/linker.md with their evidence. In short:

- TLINK writes relocations module by module, and takes a library's modules in library order, even ones it finds it needs on a later pass. So the relocation table records the original module order, and a library's relocation order is its module order.
- The overlay manager's segment table (`__SEGTABLE__`, located from the FBOV header after the load module) has one entry per segment, `{paragraph, end, flags, start}`, in link order. Overlay stubs are entries with flags 3; after a 0x20-byte header each stub entry is `INT 3Fh`, the function's offset in its overlay, and a zero.
- TLINK numbers an overlay's stub entries from the last public its object lists, and Turbo C lists publics by a hash of the name (profiles/borland-tc101/bssorder.py). So the stub order constrains the names in an overlay, and a public with no stub entry was `static`.
- TLINK lays segments out in blocks by class, the classes in the order it first meets them, and within a block places each segment where it first sees its name. Empty segments declared early in a small module, with the class of the objects that fill them, reproduce the original segment order and alignment; empty entries in the segment table are segments the original link had.
- TLINK sets a segment's code flag in the overlay segment table only when its class ends in upper-case `CODE`. Class names are not in the EXE, but the flags and the block order show which segments shared a class (UW2 had three classes of code; profiles/borland-tc101/linker.md, "Segment classes"). So tools find a code segment by a class ending in `CODE` (`Config.is_code_class`), and when a relink differs only in those flags or in the order of whole runs of segments, `tools/omfclass.py` tries other classes in the objects without reassembling.
- Each `far` variable in a Turbo C file is its own paragraph-aligned segment, so the order of far data segments shows which file defined each one.
- TLINK stores the output name (`__EXENAME__`) and the DOS date (`__EXEDATE__`) in the image: link under the original name, after setting the date. The DOS must honour INT 21h AH=2Bh for the programs that run after the one that sets it: emu2 does only with `tools/emu2-date.patch`.
- Alignment padding between separately assembled modules is zero bytes to the segment's alignment: that is how a segment that was several modules is split.
- A relocation in the EXE where the object has no fixup is a constant in the source where the original had a segment.

## The general shape of extract.py

1. For every source, place its `_DATA` and `_BSS` and resolve every extern, by running `verify.main()` on it (no sys.argv involved: `verify.main(argv, on_names=...)`).
2. Find the C library's modules in the EXE by searching for their code and data with fixups masked, to name their publics.
3. Collect every name's address: object publics, overlay stub entries, library publics, startup code.
4. Find what no source covers: code in the resident segments, far data segments, gaps in DGROUP between files' data. Write each as a data-only TASM module, with a label for every name defined inside it and a `dd NAME` or `dw seg NAME` for every MZ relocation inside it.
5. Check the sources against the EXE: names that resolve elsewhere, overlay functions with no stub entry, overlay publics out of stub order, relocations without fixups, data starting at an odd address. Each is a defect in a source, reported, never patched into the object.
6. Write the manifest: resident modules, overlays, the second library's modules, in the original order.
7. When every object verified, keep the snapshot the modding build needs (`modding.write_snapshot`): a copy of each object, the SHA-1 of its source, and where its data was placed.

link.py then assembles the generated modules, builds the second library with TLIB, sets the date, and runs `TLINK /c /m /s` from a response file (the module list is longer than a DOS command line), then calls exediff.

## The modding build

The exact link places each object's data where verify.py finds it, by comparing the object with the EXE's bytes. A changed source no longer matches those bytes, so that cannot place it. The modding build (`link.py --mod`) works from the snapshot instead:

1. Run the exact link once while every source matches. extract.py writes `<build>/LINK/base`: a copy of each matched object, the SHA-1 of the source it was built from, and the places verify found for its `_DATA`, `_BSS`, far segments and externs (`layout.json`). It writes nothing there if any object fails to verify.
2. Edit sources, then `link.py --mod`. Each source whose text differs from the snapshot's is compiled with its own `/* opts: */` into `<build>/MODLINK/src/STEM`, keyed by the source's hash, so the matched objects in `<build>` stay as they were and the exact link still works after a revert. extract.py `--mod` lays the image out from the snapshot's objects and places, and writes the near code offsets the IDA listing types in the extracted data as `dw offset NAME`. The changed objects are linked in place of the matched ones.
3. Two checks of the exact link are skipped. An overlay's publics may come out in a different order, since TLINK numbers its stub entries itself and every call to them is a fixup. And exediff is not run, because the EXE is meant to differ. With no source changed, the modding build must give the exact link's EXE; examples/uw2/prove.sh and the gate check that.
4. One thing is evened out first. TLINK fills the paragraph padding after each overlay's code and fixups from a buffer it does not clear, so those bytes depend on what it linked before; UW2's modding link leaves 4 bytes of old code there that the exact link (and UW2.EXE) has as 0. Nothing reads them, and `modding.clear_overlay_padding` sets them to 0 in the modding EXE, never in the exact one.

This moves everything the linker places: calls, globals, strings and pointer initialisers are fixups in compiled C, and each relocated word in the extracted modules is a `dd NAME` or `dw seg NAME`. It does not move a number. An address written as a plain number, in assembly, in C or in extracted data, stays where the original had it, and the build can run and then fail far from the cause. Finding those numbers is the layout audit below.

What stays fixed until it has source or names: the internal layout of extracted far data and of assembly modules that address their own data by number. Those may change only at the same length. A new source file needs a place in the link order (extract.py's lists) first; the modding build stops on a source the snapshot does not have.

## Layout audits

A literal address and a symbolic one assemble to the same bytes: `mov si,6742h` and `mov si,offset DGROUP:_Palettes` differ only in the fixup the second carries, and in C `((signed char near *)0x1bf7)[c]` and `(_ctype + 1)[c]` both give `[bx+1BF7h]`. Match masks fixups and the exact link moves nothing, so neither can tell them apart. Only a link that moves things can. Audit the layout before trusting a modding build:

1. Scan. `python3 tools/addrscan.py` reads the matched objects and lists every operand with no fixup that could be an address, with the segment it goes through. In compiled C, DS and SS are DGROUP, so only direct operands count. In assembly it follows the segment registers through the code: from publics that C calls (DS = SS = DGROUP), from the state at each call in other assembly modules (iterated until it settles), from dispatchers configured in `[layout] dispatch`, through moves, push and pop, code-segment variables, and `mov sreg,[x]` where the EXE has a relocation at x. Code nothing reaches gets what the segment's computed jumps hold. It also flags numbers moved into pointer registers (BX, SI, DI, BP) or handed to a call in them while DS or ES may be DGROUP, and a stack placed at a number (`mov sp,N`, or `mov reg,N` before `mov sp,reg`) when SS is or becomes DGROUP. Lines marked `*` may go through DGROUP or an unknown segment; read each one.
2. Fix what is an address. Write it as a name (`offset DGROUP:_name` in assembly, the variable in C), then match, verify and link exactly again: the bytes must not change. Record what was checked and found not to be an address (BIOS addresses, coordinates, divisors, tables decoded as code).
3. Boot with DGROUP shifted. Make a change that moves data, for example a new initialised array early in DGROUP, and boot the modding build to a scene that uses much of the program. A number the scan missed shows as wrong behaviour. UW2's object sprites were drawn on coloured boxes because the image decoders read their palette at a fixed DS:6742.
4. Bisect by padding `_BSS`. Add a `_BSS` array (`unsigned char pad[334];`) to one file at a time, link `--mod`, boot to the same point, and compare the screenshot with the exact build's (`tools/pngdiff.py A.png B.png X0 Y0 X1 Y1`, over a rectangle that does not animate). Padding in a file whose `_BSS` comes before the variable the number points at moves that variable and breaks the scene; padding in a file after it does not. So the boundary between the files that break it and the files that do not is the file holding the variable (UW2: OVR119, where `Palettes` is). Bisect over the files in link order rather than trying each. Then find who uses its address as a number (UW2: SEG004N's `mov si,6742h`, found this way, and then by widening the scan to numbers in pointer registers).
5. Prove a change reaches the program: `tools/rungame.mjs` takes an `m:TEXT` step that searches the guest's memory, so new code can change a marker string at run time and the run can check it (exit status 1 when it is not found).

Numbers into the program's own far data segments, used while DS or ES is on that segment, stay right while the segment's internal layout does. List them by module (`addrscan.py --all`) so it is known which modules must keep their length.

## Facts that are UW2's (measure these again for another game)

In extract.py:

- The header size and segment table length it asserts (0x2C00, 0xAE entries), DGROUP as segment table entry 168, C0's `_TEXT` (with the C library appended) as entry 6.
- Overlay stubs as entries 91 to 167 (ovr091 to ovr167), far data as entries 50 to 78, alignment padding in entries 3, 4 and 23.
- The C library's data range (DS:1BF4 to DS:204E), the second library's (to DS:2220, where the overlay manager's `__OvrSize` is) and the C library's `_BSS` start (DS:865C), all measured from TLINK's map of this link.
- C0's `_DATA` at DS:4 and the offsets of its routines in UW2's C0 (`__exit` at 0x133 and so on).
- The module order lists `RES`, `LATE` and the overlay list, recovered from UW2's relocation table: seg000 to seg004 before C0's `_TEXT`, seg003, seg004 and seg045 in a second library after CM.LIB, seg021's first module resident and the rest in that library.
- The names of generated modules and the source files it treats specially (SEG046, the overlay manager, comes from OVERLAY.LIB itself; FARDATA.ASM is marked `/* fardata */`).

- For `--mod`: the IDA listing's `dw offset` lines and their table runs (seg052_519C's model opcode table at 4FAF:24F4, 110 named handlers and one unnamed entry), and SEG046 left out of the changed sources.

In exhume.toml, for addrscan: far data as segment table entries 50 to 78, seg046 linked from OVERLAY.LIB, and seg003's dispatcher, which takes a routine's offset in BP and calls it with DS = ES = SS = seg_370D. Underworld Exhumed's uw2/docs/LAYOUT.md is the audit of UW2: the four DGROUP addresses its sources wrote as numbers and every scan line read by hand.

In link.py:

- C0.ASM's three changes (the exit-table patch after main returns, the null-pointer checksum starting at `DATASEG@`, a word-aligned `_FARDATA`), found by comparing UW2's startup code with Turbo C++'s.
- The output name `uwedit.exe` and the date 12 May 1993.
- The libraries `CM.LIB UWLIB.LIB OVERLAY.LIB`, with no floating point libraries (UW2 uses none).

## Adapting it to another game

1. Copy examples/uw2/extract.py and link.py into the new project's example directory.
2. Replace every fact above with the new EXE's, measured the same way: the segment table, the relocation order, a first TLINK map of a trial link (`/m /s`) for library data ranges, the startup code compared byte by byte with the stock C0.
3. Start with everything extracted (all code and data from the EXE) and confirm the link reproduces the EXE; then replace extracted pieces with matched objects one at a time. exediff after each change shows exactly what moved.
4. Keep every check that stops the link on a source defect. A link that patches objects to make them fit hides the defects it was meant to find.
5. Once the link is exact, add `--mod` the way examples/uw2 does (the calls into tools/modding.py), set `[layout]` for addrscan, and audit the layout before relying on it.
