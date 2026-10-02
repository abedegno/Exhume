# Borland Turbo Link 3.01: the linker

What Turbo Link 3.01 (TLINK.EXE from Turbo C++ 1.01), TLIB and Borland's VROOMM overlay manager do to the objects, and what the linked EXE records about the original link. Learned by relinking UW2.EXE from its matched sources: `examples/uw2/link.py` produces an EXE of the same size, identical to the original byte for byte, relocation table order included, except two bytes. The compiler is in `compiler.md`, the assembler in `assembler.md`.

## The link checks what byte matching cannot

- **`match.py` masks fixups**, so a call to the wrong routine (UW2: OVR112 called `strncmp` for `strnicmp`) or a constant where the original has a relocation (UW2: SEG032's `0x5DFD`) still matches; the link exposes them. Run it after any change to names or declarations.
- **A name for the middle of another file's array** (UW2: `PlayerCreature` = `Creature[63]`) must be written as base plus offset once the array has source; the link stops on it otherwise.
- **Objects link exactly as compiled.** In UW2's reference link an overlay whose publics come out in the wrong order, an unknown name, a public the original had `static`, or a missing relocation stops the link with the source to correct; nothing is patched into copies of the objects.

## Calls and overlays

- TLINK rewrites a far call into the same overlay as `nop; push cs; call near` (`90 0E E8`). match.py treats the `9A` with fixup and the rewritten form as equal (`overlay_call_rewrite` in `profile.toml`). In resident code the far call stays a far call to the segment's own paragraph.
- **The overlay manager's segment table** (VROOMM, `__SEGTABLE__`): after the load image comes an `FBOV` block whose header gives the file offset and entry count of a table TLINK writes for every segment, in link order. Each entry is 8 bytes, `{paragraph, end, flags, start}`; overlay stubs have flags 3. In UW2 there are 0xAE entries at file 0x66750, and entries 91 to 167 are the stubs of ovr091 to ovr167.
- **Overlay code names other segments by their byte offset in that table** (8-byte entries), while resident code uses real paragraphs, so the same function can appear under two addresses unless overlay segment values are converted through the table. verify.py does that.
- **An overlay's stub** starts with a 0x20-byte header; each entry after it is `INT 3Fh` (`CD 3F`), the function's offset in its overlay, and a zero byte.
- **TLINK numbers stub entries from the last public listed** in the object. Turbo C lists publics in descending order of the `profiles/borland-tc101/bssorder.py` key (see `compiler.md`), so **public order is a constraint on names**: the EXE's stub order constrains an overlay's function names, and a confirmed sibling name that breaks it (UW2: OVR124) suggests a wrong assignment.
- **A function the original had `static` has no overlay stub entry**: TLINK gives every public of an overlaid segment an entry, so a public with none was static, and nothing else may refer to it.
- **A function's address stored as data in an overlay points at its entry in the overlay's stub table** (UW2: `do_gem` is stub +25h), not at its code.
- **Stub order matters only for reproducing the EXE.** When an overlay's code changes, TLINK numbers its stub entries from the changed object's public order, and every call to them is a fixup, so a modding build can skip the stub-order check (docs/link.md, "The modding build").
- Empty overlays (UW2: ovr098, ovr100, ...) have stubs with no entries and code size 0, so the original had modules with an empty code segment in the overlay list.
- **The overlay manager is `OVERLAY.LIB`** (in `XLIB.ZIP` on Disk03 of Turbo C++ 1.01). UW2's seg046 is its `_OVRTEXT_` code, five modules (OVRMAN, OVRSWAP, OVRDATA, OVRUSER, OVRDETEC, in link order), each equal to the library object outside its fixups. Its DS is `_OVRGROUP_`, not DGROUP, so its variables are equates at their link-time offsets and DGROUP names must not be used for them. Its `9A` self-calls are calls between separately assembled library modules.

## Order and placement

- **TLINK places segments in the order it first sees their names.** To put a segment early, declare it empty in a module linked first (UW2: XORDER declares seg000 to seg004 ahead of C0's `_TEXT`).
- **TLINK writes relocations module by module**, so the relocation table records the original module order, and the overlay segment table records the segment order. UW2's link order came from these two.
- **TLINK takes library modules in library order**, even ones it finds it needs on a later pass, so a library's relocation order is its module order. A module is pulled only by a named reference: write `offset name` (not the number) where another module uses its entry points.
- **Zero bytes after a `ret`/`jmp` are TLINK's alignment padding between separately assembled modules** (UW2: word-aligned in seg003, paragraph-aligned in seg004 and seg021), which is how those segments were split into their original modules (14, 14 and 17).
- **Segment names do not reach the EXE, but TLINK joins segments by name** and places them in the order it first meets the names. Renaming source files (which renames `FILE_TEXT` and `FILE<n>_FAR`) changes nothing in the EXE as long as modules that share a segment agree on its name, and a link that declares segments early to fix their order takes the names from the objects rather than spelling them (UW2: examples/uw2/extract.py's XORDER and XSEG020).
- **Far data order shows the owner.** Turbo C gives each `far` variable its own segment (`compiler.md`), and TLINK orders segments by first definition, so the order of the far segments shows which file defined each variable; examples/uw2/extract.py places them by link order.
- Segment alignment can be read off the table: a segment that starts on a fresh paragraph after a gap was `para`, one that starts exactly where the previous ended `byte`, one that skips one byte to an even address `word`. Empty entries are real: the original link had them.

## What TLINK stores in the EXE

- **The output name** goes into `__EXENAME__`. UW2's is `uwedit.exe` in lower case, so the link must name its output that.
- **The DOS date of the link** goes into `__EXEDATE__`. UW2's is 12 May 1993; the reference link sets the DOS date first with a tiny SETDATE program (`mov ah,2Bh; int 21h`, linked as a .COM in the same batch). So the DOS the link runs in must honour INT 21h AH=2Bh and keep the date for TLINK, which runs after SETDATE has exited. DOSBox-X, DOSBox Staging and js-dos do. emu2 runs each program as its own process and refuses to set the date; `tools/emu2-date.patch` (built by `tools/setup-emu2.sh`) keeps a date set this way in a file the later programs of the run read. With an unpatched emu2 the link takes today's date and three bytes of UW2's link differ.
- **The code flag in the overlay segment table** is set only for a segment class spelled exactly `CODE`. UW2 has 0 for seg003 and seg004 where the relink gives 1; a class `Code` gives 0 but moves the segments, so the original's combination is unexplained (possibly a different TLINK 3.0x). These are the two bytes that still differ (file 0x6676C and 0x66774).

## Command line

- UW2's link, through a response file (the module list is longer than a DOS command line): `TLINK /c /m /s XORDER C0UW2 <resident objects> /o <overlay objects> /o-, uwedit.exe, uwedit.map, CM.LIB UWLIB.LIB OVERLAY.LIB`.
- `/c` is case-sensitive linking (TCC -Y passes `/c/x`); `/o` overlays the modules that follow and `/o-` ends that; `/m /s` write the map.
- UW2 uses no floating point, so EMU.LIB and MATHM.LIB (which TCC would add) contribute nothing. The C library's modules come before the second library's and the overlay manager's in UW2.EXE, hence the library order.
- TLIB builds a project library from objects in a given order (UW2: UWLIB.LIB from the seg003, seg004 and seg021 modules and seg045, in the order their relocations show).

## Startup code (C0)

- The startup module is Turbo C++'s own `C0.ASM`, assembled as `BUILD-C0.BAT` does for the model (medium: `TASM /D__MEDIUM__ /MX`).
- UW2's C0 differs from the shipped `C0.ASM` in three ways, which `examples/uw2/link.py` applies to a copy: the self-modifying patch of the exit-table scan comes after `main` returns, not before `main` is called; the null-pointer checksum starts at `DATASEG@` (DS:4 in UW2, where `_DATA` begins) through `mov ax,offset DATASEG@; mov si,ax`, instead of at 0; and its `_FARDATA` is word-aligned (segment table entry 49 is an empty segment right after `_OVRTEXT_` ends at an odd address).
- C0's `_DATA` starts with 4 zero bytes and then the copyright string `Turbo C++ - Copyright 1990 Borland Intl.`, which `tools/fingerprint.py` uses to suggest DGROUP's paragraph.
- **DGROUP must stay under 64 KB.** In the medium model C0 puts the stack (`_stklen`) and the near heap (`_heaplen`) in DGROUP after the static data, so the static data can grow only by what is left. UW2: static data ends at DS:8C84, `_stklen` is 0x1000 and `_heaplen` 0xC00, which leaves about 22 KB.
- **The startup variables** `_heaplen`, `_stklen` and `_ovrbuffer` are initialised in `main`'s file (UW2: ovr112) and read by C0 and the overlay manager.
