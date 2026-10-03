# Case study: Ultima Underworld II

Ultima Underworld II: Labyrinth of Worlds (Looking Glass Technologies for Origin Systems, DOS, 1993) was the first program decompiled with this method, in the repository UW2Decomp. Exhume's tools were extracted from it. This page records what was done, the numbers, and what each stage found. Unless a number says otherwise it comes from UW2Decomp's commit history and README as of 2 October 2026; the byte-identical link and the port are from 3 October.

## The target

- `UW2.EXE`: 675,184 bytes. MZ header 0x2C00 bytes, load module 0x6EBA0 bytes, 2,791 relocations, then a Borland VROOMM overlay block (FBOV) of 0x335D0 bytes. The overlay segment table has 174 entries; 77 are overlay stubs (ovr091 to ovr167).
- Built with Borland Turbo C++ 1.01 (medium model, 186 instructions, switches varying per file), Turbo Assembler 2.0 and Turbo Link 3.01.
- A sibling: the Japanese FM Towns release, built from the same object list in the same link order with Watcom C (32-bit), kept 3,237 of Looking Glass's original names.
- An IDA listing of the DOS EXE from the UWReverseEngineering project.

## The result

- **All C matched:** 337,327 of 337,327 bytes, 99 source files (about 47,700 lines), every one verified (fixups, initialised data, `_BSS`, far data, names).
- **All assembly matched:** ten modules, 71,920 bytes, later split into their original modules (seg003, seg004 and seg021 are 14, 14 and 17 TASM modules), plus seg000, seg018 and SetPnt; 54 assembly sources in all (about 33,800 lines). seg045 turned out to be compiled C and is now `SEG045.C`. Every code segment outside the C runtime library rebuilds byte for byte.
- **Linked:** TLINK in headless DOS produces an EXE byte-identical to the original (`cmp` finds no difference). For two days it differed in two bytes, the overlay segment table's code flag for seg003 and seg004, until those were traced to segment class names (below). It runs to the title and menu, and a changed string in a C source shows on screen.
- **Names:** symbols.tsv holds 2,919 names: 2,291 original (from the FM Towns build), 12 runtime library, 616 provisional.
- **Not yet source:** about 83 KB of far data (the graphics and 3D modules' tables and the 3D object models) and eight small, unreferenced DGROUP gaps, all taken from the user's EXE at link time.

## Timeline

94 commits. The first function matches (ovr154, the whole of `PLAYER.C`, 6,967 bytes) landed at 23:53 on 30 September 2026. All C code matched by 14:41 on 1 October, all code by 16:58, the first running link at 17:56, and the two-byte link at 21:22: about 21.5 hours from first file to a link two bytes short. The byte-identical link (UW2Decomp commit 64c4665) came at 14:59 on 3 October.

## What each stage found

### Fingerprint

- Three spike functions (18, 202 and 381 bytes) matched with fixups masked, the largest after about six compiles, which fixed the compiler as Turbo C++ 1.01 and the model as medium. Each compile took about 30 seconds headless at the time.
- Switches vary per file. `-Y` (overlay code) proved necessary even in a resident file (seg029, which passes a same-file function's address); `-d` was proven by the data segment, which stores each repeated literal once; `-Z` was proven absent in some files and present in one. seg045 alone needed `-k-` without `-Y`.
- The assembly was identified as TASM 2.0 single-pass when a seg004 routine rebuilt from its disassembly came out byte for byte, including six padding `nop`s TASM generates itself. The padding then shows, jump by jump, where the source said `short`.

### Map

- locate.py placed and validated 109 of 122 segments on its first run, corrected two stale IDA proc offsets, and later read offsets IDA wrote mid-name.
- The DOS and FM Towns builds proved to share link order (every game anchor monotonic; only the C library and platform modules differ). Size-based alignment between anchors, confirmed by both call graphs, named 884 of the 1,886 non-library functions at first. Holding out known pairs, confirmed names were right 80 times in 82; both misses disagreed with a hand-made anchor.
- With matched files as anchors the map now carries 1,298 anchored names (Exhume's rerun of the pipeline gives the same files as UW2Decomp's tools on the same inputs).
- Listing failures: seg002 (a run/skip/dump decoder) was never listed as procedures; seg004 began with a 0x63-byte table IDA decoded as code; ending a table at the first far return overran the assembly modules, whose tables had to be rewritten from paragraph start to the padding before the next module.
- `map/filenames.tsv` first proposed original file names from the System Shock source release (same engine lineage): six strong candidates and eleven plausible. The readability pass (below) then renamed every file, recording each name's kind and evidence there.

### Match

- Most lessons are in profiles/borland-tc101/compiler.md: about 150 rules of the form "this byte pattern means this source shape", each found while matching a specific file.
- The `_BSS` layout rule, `(c0 + 256*c1 + 8*c[len-2] + 64*len) & 1023` on the name, was found from probe compiles on seg015 and predicted two other files' layouts. It made naming statics a calculation, and it also explained overlay stub order (Turbo C lists publics by the same key).
- One file was public code: ovr127 is Haruhiko Okumura's 1989 LZSS.C almost line for line. seg046 is Borland's own overlay manager from OVERLAY.LIB.
- The emulator occasionally returned empty, zero-filled or truncated objects, more often with several emulators at once. The runner checks each object is a complete OMF record chain with good checksums and retries.

### Verify

- verify.py found, among others: data that belonged to the file (static) where it had been declared extern; two functions given two names each; files whose data started at an odd address because the leading byte was missing (five files, one missing 11 bytes); one name used for two variables (OVR108's `sound_fpage`; `hitz` in SEG007 and SEG024); overlay functions that were static in the original (no stub entry); and map pairings on the wrong function, corrected by the FM Towns code.
- A gate failed silently once: piping `verify.py --update` through `tail` lost its exit status, and two files merged with seven symbol conflicts. merge now checks every exit status.

### Agents and costs

As recorded in the project notes:

- Claude Opus agents matched about 4 KB per 170k tokens on average. A Sonnet trial matched both its files but used about five times the tokens per byte: seg014 (579 bytes) 185k tokens; seg043 (1.6 KB) 434k tokens, 53 minutes, 218 tool calls.
- Seven Opus agents in parallel exhausted the session's usage limit around 01:30 on 1 October; all seven stopped mid-file and were resumed (not restarted). Three at a time was the working limit after that.
- Codex could not start the emulator in its sandbox, so a build queue outside it served match and verify requests, with a 30-build budget per file. gpt-6-sol at medium effort alone: about 1.3M input tokens for a 1 KB file. A gpt-6-luna draft finished by sol: 0.9M to 2.0M (average 1.46M over four files, luna counted at 1/20 of sol's price). sol at low effort: 1.8M to 2.95M. luna alone never finished a file.
- By our count of the commit messages, Codex alone is credited with 27 C files (twelve of them tiny overlays of 5 to 141 bytes), Codex drafts finished by Claude agents with 10 (including the largest file, ovr110, 20.6 KB, and seg006, 14.6 KB, and ovr166, 12 KB), and Claude agents with the rest and with all the assembly.
- Codex drafts hid wrong extern names behind masked fixups: ovr103's finisher corrected about 20.

### Link

- The module order came from the EXE: the relocation table (written module by module) and the overlay segment table.
- TLINK stores the output name and the DOS date in the image: the link must be called `uwedit.exe` and run on 12 May 1993. UW2's C0 is Turbo C++'s C0.ASM with three changes.
- The link exposed what matching could not: OVR112 called `strncmp` where UW2 calls `strnicmp`; SEG032 had a constant where UW2 has a segment relocation; nine overlay functions were static; provisional names that broke the overlay stub order (OVR124 suggested a wrong FM Towns assignment). Once the sources were corrected the link needed no patches.
- The relocation order was reproduced exactly only after seg003, seg004 and seg021 were split into their original modules, found from the relocation order and TLINK's zero padding.
- **The last two bytes were segment classes.** For two days the relink differed from UW2.EXE only in the overlay segment table's code flag for seg003 and seg004 (file 0x6676C and 0x66774: 1 where UW2 has 0). TLINK 3.01 set the flag for class `CODE`, and the class `Code` gave 0 but moved the segments, so it looked like a different linker. The hunt ruled that out first: TLINK 3.0, from Turbo Assembler 2.0's package, linked 19,856 bytes different until C0's `_DATA` was made word-aligned, and then gave the same two bytes as 3.01; TLINK 4.0, from Borland C++ 2.0, gave the same two bytes; making the seg003 modules' segments stack instead of public left them too, and private or common changed 466,893 and 490,055 bytes. MASM 5.1 was fetched because it upper-cases class names unless run with `/Ml`, which bears on how a period source could have spelled one. Reading TLINK 3.01's code then gave the rule (profiles/borland-tc101/linker.md, "Segment classes"): segments are laid out in blocks by class, in the order the classes are first met, and the flag is set when the class of a frame's first segment ends in upper-case `CODE`. So the original had three classes: seg000 to seg002 in one that ends in `CODE` but is not `CODE` (it comes first and keeps the flag), seg003 and seg004 in one that does not end in upper-case `CODE`, and everything else in `CODE`. The class experiments rewrote the class names in the objects and relinked: `XCODE` and `GRAPH`, `FAR_CODE` and `Code`, `LIBCODE` and `code`, and separate classes for the graphics and 3D modules each gave 0 differences, in TLINK 3.01, in 4.0 and in 3.0 with the word-aligned `_DATA`; the control, block 1 left as `CODE` with block 2 `GRAPH`, moved seg000 to seg002 behind seg004 and changed 228,676 bytes. The EXE stores no class names, so the pattern is proved and the spellings are chosen: UW2Decomp uses `ASMCODE` and `code`. Its sources changed only in the class (VALLOC.ASM and LPFDELTA.ASM lost `.model`, since TASM keeps a `.model` segment's class), its tools now find code segments by a class ending in `CODE`, and its modding build zeroes the overlay padding TLINK fills from an uncleared buffer, which held 4 stale bytes once seg003 and seg004 had a class of their own.

### Run and liveness

- The linked EXE reaches the title and main menu. Changing `"Str:"` to `"Lnk:"` in OVR101.C (character creation) changed exactly those three bytes in the EXE and showed on screen.

### Modding build

- On 2 October UW2Decomp added `link.py --mod` (commit 00b6b67). Four DGROUP addresses its sources wrote as numbers became names: SEG039.C indexed the C library's `_ctype` at `0x1bf7`, SEG021A loaded C0's `__psp` as `92h`, SEG021Q put a private stack at `211Ch`, and SEG004N handed OVR119's `Palettes` to the image decoders as `6742h`. Each new spelling compiles to the same bytes.
- The last was found by booting, not by the scan: with data grown anywhere before DS:6742 the 3D view drew every object sprite on a coloured box. Padding `_BSS` file by file narrowed it to OVR119's `_BSS`; the scan then learned to follow numbers into pointer registers and through calls, and widening that to `mov sp` found SEG021Q's stack.
- In extracted far data, the 3D model interpreter's opcode table (111 words at 4FAF:24F4) is written as `dw offset NAME` for the 110 entries that name a handler; the one entry that points at an unnamed data word stays a number.
- Proven with a longer string and new code in OVR101 (an overlay), new initialised data and code in SEG039 (resident: every later segment, the far data and DGROUP's paragraph move), and a 333-byte `_BSS` array in SEG006, each alone and together: the game reaches the 3D view and looks and walks as the original does.

### Readability pass

On 2 October UW2Decomp made the matched tree readable in seven commits (12516e4 to f9bf8b5), and the EXE stayed identical through every one of them: the gate passed after each.

- **Gate** (12516e4): `make check` over `tools/uw2.py`, incremental by source and header hash: every source matches and verifies, symbols.tsv rebuilds from scratch, the exact link equals UW2.EXE but the two known bytes, the unchanged modding build equals the exact link. About a minute from nothing, about ten seconds with nothing changed. A pre-push hook runs it; GitHub runs only the toolchain-free repocheck.
- **Shared headers** (0c4d92c): 16 subsystem headers in `src/include`. Prototypes in the sources fell from 2,523 to 718, extern declarations from 899 lines to 256, local struct definitions from 350 to 50. 36 structs were reconciled from up to 53 copies each (`struct Object` had 53), their fields and types fixed by the bytes, and 1,263 names are shared. 227 names whose declarations differ between files stay declared in those files, because the files compile differently with one shared declaration.
- **Named constants** (165ed97): 1,251 hex literals replaced by names, about 1,780 uses: all 448 item ids named from the game's own item names, the object classes from the FM Towns enum, object field masks, string blocks, fonts, palettes, tile types, skills, quest bytes, spell classes, error codes.
- **Struct fields and accessors** (9aba437): cast-pointer offset sites fell from 96 to 12 (the rest kept because the bytes need them); new structs for the level block, animations, bitmaps, the font header, SCD rows and armour; 114 shared `OBJ_*`/`SET_*` accessor macros replace the per-file copies, two with a second spelling where files compile them differently.
- **File names** (e44f1fc): all 154 sources renamed and grouped in 15 subsystem directories: 9 names original (from System Shock's source and Miles' AIL 2.14: VALLOC, INTERP, INPUT, DAMAGE, GAMESTRN, WRAPPER, GAMEWRAP, PLAYER, AIL), 34 inferred, 111 descriptive. Tools find sources by segment, so the link needed no change.
- **Comments** (3c9afb9, 198a7b3): every file has a header comment and routine notes, with `match:` and `name:` tags; 14 subsystem notes. The first pass was cut short by a session limit after commenting agents fanned out into nested agents (skills/orchestrate).
- **Findings** (f9bf8b5): docs/FINDINGS.md lists 17 likely bugs in the original, each re-checked against the source and the FM Towns build, with confidence, effect in the game and whether a port should reproduce it (two candidates dropped on re-reading), plus game rules recovered from the code, engine findings, dead code and open questions.

## The port

From 2 to 3 October UW2Decomp built a native port from the same sources (its docs/PORT.md has the design and every result): about 21 hours from Milestone 1 (17:37 on 2 October) to Milestone 6a (14:42 on 3 October), then CI. The DOS build never changed: the gate passed after every commit, and `make check-all` after each milestone. At the CI commit (6252fdf) the port's own C is about 68,300 lines: 56,500 translated from the assembly by `tools/asm2c.py` and 11,800 written by hand; the shared game C is the decompilation's own.

| Milestone | Commit | What it took | Exit test, as measured |
| --- | --- | --- | --- |
| 1. Measure | 67ab669, 2 Oct 17:37 | compat.h, the stand-in headers, `make port-check` | 92 of 99 sources compile; 18 errors and 233 warnings; 308 names a link still needs, 218 of them the port's work |
| 2. Compile and link | 277c5e0, 19:20 | eight gated steps: errors, 30 near pointers in integers, 87 calls without prototypes, explicit widths (1,210 specifiers), the layout check, the promotion audit (1,894 sites, 15 fixed), the null-pointer pass, the far pointer macros and stubs | 98 of 98 compile with 0 errors and 61 warnings; all 21 file records identical under Turbo C and clang (38 of 46 records, the 8 others hold pointers); a 788 KB arm64 binary with 160 stub functions and 80 stub variables, stopping at the first stub |
| 3. Boot to the title | 729bd40, 20:22 | the SDL3 platform layer, the paragraph map, far heap and EMS, Borland's library, seg021 in C, the parts of the graphics library the opening screens need, AIL with no driver | `init_world` runs to its end on the user's data; the Origin and Looking Glass screens differ from DOS in 0 of 64,000 pixels; 38 stub functions left |
| 4. Replay | f0660c9, 23:34 | the replay hooks in 19 files, the replay DOS build, `state_dump`, the rest of the graphics library, the cutscene player's planar writes, `STACK_JUNK` for one local | a 27 KB recording of 28 million hook calls replays twice in DOS identically at all 43 checkpoints; the port is identical at the first 37, up to the 3D renderer |
| The 3D renderer | ca5ac4d, 3 Oct 07:33 | 26 assembly modules translated by asm2c.py (70,114 lines added), the machine they run on, the render interface and sprite hook | `newgame` identical at all 43 checkpoints, `walk` at all 68; with the frame buffer in every dump every 20h ticks, 337 frame buffers (82 distinct 3D frames) identical to DOS's |
| 5. Sound and the game loop | c58e5b7 (checkpoint, 11:46), 50459d1, 13:14 | AIL in C, the drivers from AIL 2.14's source with UW2's own differences, Nuked OPL3, libmt32emu, the DAC, `SND_READ`, `SLAVE_TIMER`, recording format 3; DOS-compatible saves; one implementation per routine (about 12,000 duplicated lines removed) | eight sessions identical to DOS at every checkpoint, saves byte for byte; 0 differing driver reads of 1,795,103 (`sound`), 1,832,854 (`soundfm`) and 3,092,157 (`soundmt`); the drivers' writes identical to DM03.ADV's and DM05.ADV's in Unicorn |
| 6a. Fast tests | 2e4848f, 14:42 | golden references, unthrottled replays, DOSBox-X for the DOS side, parallel sessions, routine fuzzing, coverage, `make test` and the pre-push hook | goldens for all 8 sessions (550 checkpoints, 355 PNGs, 7 MB), identical from DOSBox-X and js-dos; the port replays all 8 in 14 s one at a time (58 s before), 3.5 s in parallel; DOS 165 s in DOSBox-X against 590 s in js-dos; 35 fuzz targets, 5,560 cases in 13 s, clean; coverage 45.4% of lines; `make test` 23 s, `make test-full` 12.5 minutes |
| CI | 6252fdf, 15:46 | the encrypted bundle, accuracy and nightly workflows, the port on three systems | `make test` passes on Ubuntu 24.04 on x86-64 and arm64; x86-64 found the one layout-dependent overflow (BAGS.C's `OpenTheBag`), fixed by keeping the two arrays in one |

What the replays found that no audit did: the value macro that evaluated its argument twice; a propagated return taken for a normal one in the translated polygon clipper; a patched shift count read as a constant; a far pointer's split that the graphics library keeps in its row records; two locals read before they are set (`held`, `head`); a stream and its handle disagreeing about the file position; DOSBox's Sound Blaster ending transfers early.

## Exhume's reproduction of the port (3 October 2026)

`examples/uw2/prove-port.sh --dos` runs Exhume's port tools and runtime against `git archive` snapshots of UW2Decomp at its CI commit (6252fdf), with the toolchain copied in, never the working checkout, on an Apple M4 Pro:

| Check | Result |
| --- | --- |
| Exhume's gate (emu2) | passes: 153 of 153 sources, symbols.tsv rebuilt, exact link and unchanged modding build byte-identical to UW2.EXE |
| `tools/portbuild.py` | `uw2port` byte-identical to the one UW2Decomp's own portbuild.py links in the same tree, all 173 objects equal |
| `tools/replay.py build` | the replay DOS build is the EXE the committed goldens were made with (the same SHA-256) |
| `tools/replay.py verify all` | all 8 sessions identical to UW2Decomp's committed goldens, at all 550 checkpoints, saved games included, 3.4 s in parallel |
| `tools/replay.py golden all --check` (DOSBox-X) | each session twice in DOS, the two runs identical and both identical to the committed golden, 57 s for the sixteen runs |
| runtime/include/portable.h and runtime/replay/replay.c with UW2's bindings in place of UW2Decomp's | the gate passes (no DOS byte changed); the port verifies all 8; the replay DOS build made from Exhume's replay.c reproduces every golden in DOSBox-X, twice per session |
| `tools/fuzzasm.py` with examples/uw2/port's targets | 35 routines, 5,560 cases, no difference, the same report as UW2Decomp's but for the timings; a target changed to compare registers the C does not set fails all 200 cases |
| `tools/asm2c.py` | `--check` clean; with the 27 generated files deleted, it writes all 27 again byte-identical to UW2Decomp's |
| runtime/port/x86/asmrt with asmgame.h (UW2Decomp's own divfault.c) | the port verifies all 8 sessions and fuzzes clean |
| portcheck, portstubs `--check`, widths `--diff`, intaudit, layoutcheck | the same output as UW2Decomp's tools in the same tree (layoutcheck's DOS and host probe outputs too) |
| the whole portability layer from runtime/port with UW2's bindings | gate passes; port verifies all 8; fuzzing clean; stubs current; portcheck 0 errors, 56 warnings, as before |
| runtime/port/sound and sys/pit.c too (UW2Decomp's tvfx.c through yamaha.h's `struct AilFmExt`) | all 8 verify; ailcheck.py: DM03.ADV 13,608 writes over 195 calls and 14,041 over 270, DM05.ADV 25,726 bytes over 280, all identical; the WAV of everything the cards play in `sound` (22,557,836 bytes) and `soundfm` is byte-identical to UW2Decomp's port's |
| the same with no FM extension (yamaha.c as YAMAHA.INC without `OSI_ALE`) | builds and runs; all 8 still verify (no game state depends on the synthesiser); ailcheck: DM03.ADV's TVFX writes missing (13,189 of 13,608 and 13,245 of 14,041), DM05.ADV identical |
| `tools/test.py fast` | the gate, the port, the quick fuzzing and the 8 sessions in 24 s |
| `tools/coverage.py` | 45.4% of lines, 1,335 of 2,274 functions, the fuzzing adding 652 lines and 7 functions: UW2Decomp's figures (its committed page predates the CI commit's few changed lines) |
| `tools/citemplates.py` with examples/uw2/ci.toml | UW2Decomp's four workflows and two actions at 6252fdf, byte for byte; actionlint finds nothing in them or in the defaults' |
| `tools/ci-assets.sh` | on a dummy bundle with a throwaway age key, the output and tree of UW2Decomp's own |

The steps that need nothing of Exhume's runtime (build, verify, fuzz, asm2c) also pass against UW2Decomp's later commits (467afb5, its release work), whose port Exhume's portbuild.py still links byte-identically; the runtime installs do not apply there, since that work extended the platform API (a folder dialog, the game directory search), and its CI has since been edited away from the templates.

## Exhume's reproduction (1 October 2026)

`examples/uw2/prove.sh --map` runs Exhume's tools against the UW2Decomp checkout, read-only, writing everything to Exhume's build directory:

| Check | Result |
| --- | --- |
| Build all 154 sources (3 DOS sessions at a time, up to 12 sources each, in js-dos; see "Choosing the DOS" below for emu2) | 154 built in 13 s; every object equal to UW2Decomp's (segments, data, publics, externs, fixups) |
| match.py, fresh builds of PLAYER.C (overlay), SEG012.C (resident), SEG022.ASM (assembly) | all WHOLE SEGMENT MATCHES; reports identical to UW2Decomp's match.py |
| verify.py on all 153 sources with a target | 153 verified; output identical to UW2Decomp's verify.py for all 153 |
| rebuild-symbols.py | 2,917 names; equal to UW2Decomp's symbols.tsv except two stale provisional names for variables OVR166.C now keeps static |
| examples/uw2/link.py | 3 s; same size; the same two bytes differ; byte-identical to UW2Decomp's linked EXE, map identical |
| Map pipeline into a copy | dos_procs, dos_code, segments, procs and both call graphs identical; anchors, functions and files identical to UW2Decomp's own tools run on the same inputs |
| rungame.mjs | the linked EXE reaches the main menu; the OVR101 string change shows in character creation |
| Exact link's snapshot for the modding build (2 October) | `LINK/base/layout.json` identical to UW2Decomp's |
| tools/addrscan.py (2 October) | report identical to UW2Decomp's addrscan.py on the same objects (3,907 numeric operands, 155 marked) |
| link.py --mod, no source changed (2 October) | 110 opcode table entries written as names; EXE byte-identical to the exact link |
| link.py --mod with OVR101 and SEG039 grown, in a copy of src/ (2 October) | 675,328 bytes; character creation shows "Strength:" and a new "S+D:" line; the game reaches the 3D view with objects drawn normally; rungame finds the marker the new resident code changes at start-up (`EXHUME-1`) in memory |

One compile took about 30 seconds during the project. On the same machine on 1 October, UW2Decomp's tcc.mjs took about 7 seconds for one file and Exhume's runner about 2.5 seconds; batching many sources into one DOS session is what makes a full rebuild take seconds rather than an hour. Since 2 October the toolchain runs in emu2 by default, which takes a full rebuild from about a minute to a few seconds (next section).

## Choosing the DOS (2 October 2026)

All of UW2 was matched with the toolchain in js-dos (DOSBox in WebAssembly, in headless Chrome). UW2Decomp then measured three native DOS emulators for the toolchain (its commit fbcbae9, docs/BUILDING.md, "Choosing the DOS"), and Exhume took the same runner (`tools/dosbackend.mjs`, docs/method.md, "Choosing the DOS"). js-dos is still what runs the game.

UW2Decomp's measurements, on an Apple M4 Pro with 14 cores:

| | emu2 | DOSBox-X | DOSBox Staging | js-dos |
| --- | --- | --- | --- | --- |
| `match.py` on one file (SKILLS.C) | 0.4 s | 1.6 s | 1.4 s | 6.3 s |
| the exact link (`link.py`) | 1.3 s | 2.6 s | 3.3 s | 3.1 s |
| all 153 sources, 3 sessions | 7 s | 13 s | 13 s | 64 s, 23 left for single builds |
| all 153 sources, 12 sessions | 3 s | 4 s | 6 s | (3 sessions) |
| `make check-all` | 7 s | 10 s | 12 s | 149 s |
| `make check`, nothing changed | 4 s | 7 s | 7 s | 10 s |
| `make` after a change to `portable.h` (93 sources), one at a time as before | 27 s | 140 s | | 585 s |
| the same, batched and in parallel | 4 s | 6 s | 8 s | 128 s |

Every one of the 153 sources was compiled in each and compared with js-dos's objects: they differ only in the time of day Turbo C records for each source and header (the Borland dependency records and their checksums), which differs between two js-dos builds too. The exact links (EXE and map) are identical in all four. An emu2 without the date patch gets three bytes of the link date wrong.

Exhume's own runs, on the same machine, against a `git archive` snapshot of UW2Decomp at fbcbae9 (the toolchain linked in, nothing written to the checkout):

| | emu2 | DOSBox-X | js-dos |
| --- | --- | --- | --- |
| `match.py` on SKILLS.C (ovr154), a fresh build | 0.5 s | 1.8 s | 3.0 s |
| the exact link (`examples/uw2/link.py`) | 1.7 s | 2.9 s | 3.5 s |
| `gate.py check --all` (153 sources compiled, 12 sessions; 3 in js-dos) | 8 s, compiles 3 s | 12 s | 74 s, compiles 34 s |
| `gate.py check`, nothing changed | 5 s | | 11 s |
| `link.py --mod` after a comment added to `portable.h` (93 sources), batched and in parallel | 4 s | | 60 s |
| the same compiles one source to a session, three at once (the old path) | 9 s | | |
| `prove.sh` step 1, `build.py --all` | 3 s | | 68 s |
| `prove.sh` whole, with the boot (which is always js-dos) | 1 min 45 s | | 3 min 4 s |

- The gate passed in all three, and `prove.sh` in emu2 and js-dos: every object equal to UW2Decomp's own (segments, data, publics, externs, fixups), matches and verifies identical to UW2Decomp's tools, the link byte-identical to UW2Decomp's, the modding build with size-changing edits booted to the 3D view with its marker found in memory.
- The 154 objects (153 sources and FARDATA) from emu2, DOSBox-X and js-dos are the same record for record once the Borland dependency comments (COMENT class E9) are left out; none is byte-identical as it stands, since each carries the time stamps of its own staging. The exact link and its map are byte-identical across all three and to UW2Decomp's, and so are the modded EXEs from emu2 and js-dos.
- An unpatched emu2 (the pinned commit without `tools/emu2-date.patch`) linked an EXE that differs in three bytes, the link date (2 October 2026 for 12 May 1993).
- js-dos dropped sessions part of the way through 12-source batches: `build.py --all` lost 41 of 154 sources on one run. The lone retry of each failed source, which the gate already had, now runs in `build.py` and the modding build too. That run also found that a session that died could leave a zero-length object that counted as built; `dosrun.mjs` no longer copies back a damaged output and `build.py` requires each source's own log.

## Exhume's reproduction of the byte-identical link (3 October 2026)

Run against a `git archive` snapshot of UW2Decomp at 64c4665 (the toolchain linked in, UW2Decomp's own objects copied in for the comparisons), never the working checkout, in emu2.

| Check | Result |
| --- | --- |
| `tools/gate.py check --all`, with no `known_diffs` in examples/uw2/exhume.toml | 153 of 153 sources compiled, matched and verified; symbols.tsv rebuilt from scratch, 2,917 names, equal to the committed file; exact link byte-identical to UW2.EXE; modding build byte-identical to it. 9 s from nothing, 5 s with nothing changed |
| `cmp` of the exact link and of the unchanged modding build with UW2.EXE | no difference in either; the exact link is also byte-identical to UW2Decomp's own |
| `link.py --mod`, no source changed | `clear_overlay_padding` zeroes 4 stale bytes, as UW2Decomp's does |
| `prove.sh`, whole, with the boot | 1 min 45 s; every object equal to UW2Decomp's, matches, verifies and addrscan identical to UW2Decomp's tools, exediff `IDENTICAL`, the snapshot equal to UW2Decomp's, the modded EXE (675,328 bytes) booted to the 3D view with `EXHUME-1` in memory |
| the recipe in reverse: `tools/omfclass.py` setting the 31 `ASMCODE` and `code` objects back to `CODE`, then the exact link | exactly the old two bytes differ, 0x6676C and 0x66774 |
| `gate.py` with the two old `known_diffs` set | the identical EXE fails ("expected the 2 known bytes to differ, 0 do"), as it should |

## Exhume's reproduction of the readability pass (2 October 2026)

Run against a snapshot of UW2Decomp at f9bf8b5 (`git archive`, with the toolchain linked in), never the working checkout; the earlier commits were checked from their own snapshots.

| Check | Result |
| --- | --- |
| `tools/gate.py check` on the snapshot | 153 of 153 sources compiled, matched and verified; symbols.tsv rebuilt from scratch, 2,917 names, equal to the committed file; exact link equal to UW2.EXE except 0x6676C and 0x66774; modding build byte-identical to it. 78 s from nothing, 11 s with nothing changed |
| the gate's negative cases | one changed constant in `game/SKILLS.C` fails `check --fast` (ovr154, 6,820 of 6,967 bytes); a comment added to `include/player.h` recompiles exactly its 52 includers, which pass |
| `tools/declinv.py` at 12516e4, 0c4d92c and f9bf8b5 | prototypes 2,523, 718, 558; extern lines 899, 256, 254; local struct definitions 350, 50, 36: the commit's figures. Struct tags defined in more than one place: 34, then 0 |
| `tools/declinv.py` at f9bf8b5: the conflicts left | 255 names declared differently in different files (250 with divergent forms, 39 with an old-style declaration somewhere), such as `advance`, defined with a `char` in `game/SKILLS.C` and declared with an `int` in `game/SKILLCHK.C`; 27 tie groups that mix header and local names, all passing the gate |
| `tools/headergen.py` with examples/uw2/readability/headers/plan.toml, from the run's base tree | 1,263 names into 16 headers, 227 left by the plan and 55 by rule; all 115 files it writes have the token streams of commit 0c4d92c |
| `tools/structrec.py` at 12516e4 | `struct Object` 53 copies, `struct Player` 41, `struct ComObj` 28, `struct Tile` 26; converting ComObj rewrites all 28 files, with the field names of the run's base tree (checked in four) |
| `tools/rawoffsets.py` | 96 sites at 0c4d92c, 12 at f9bf8b5 |
| `tools/comments.py apply` on `game/SKILLS.C` | refuses a spec whose edit changes `return 1` to `return 2`, and one whose comment text closes the comment early and leaves `value = 0;` behind; writes a comment-only spec (a routine comment, a trailing note, a reworded comment), after which the full gate passes |
| `tools/repocheck.py` | Exhume: all checks pass; the snapshot with its game data extensions banned: all pass, and a copy of UW2.EXE renamed `notes.txt` is caught by its MZ header |

