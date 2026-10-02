# Case study: Ultima Underworld II

Ultima Underworld II: Labyrinth of Worlds (Looking Glass Technologies for Origin Systems, DOS, 1993) was the first program decompiled with this method, in the repository UW2Decomp. Exhume's tools were extracted from it. This page records what was done, the numbers, and what each stage found. Unless a number says otherwise it comes from UW2Decomp's commit history and README as of 2 October 2026.

## The target

- `UW2.EXE`: 675,184 bytes. MZ header 0x2C00 bytes, load module 0x6EBA0 bytes, 2,791 relocations, then a Borland VROOMM overlay block (FBOV) of 0x335D0 bytes. The overlay segment table has 174 entries; 77 are overlay stubs (ovr091 to ovr167).
- Built with Borland Turbo C++ 1.01 (medium model, 186 instructions, switches varying per file), Turbo Assembler 2.0 and Turbo Link 3.01.
- A sibling: the Japanese FM Towns release, built from the same object list in the same link order with Watcom C (32-bit), kept 3,237 of Looking Glass's original names.
- An IDA listing of the DOS EXE from the UWReverseEngineering project.

## The result

- **All C matched:** 337,327 of 337,327 bytes, 99 source files (about 47,700 lines), every one verified (fixups, initialised data, `_BSS`, far data, names).
- **All assembly matched:** ten modules, 71,920 bytes, later split into their original modules (seg003, seg004 and seg021 are 14, 14 and 17 TASM modules), plus seg000, seg018 and SetPnt; 54 assembly sources in all (about 33,800 lines). seg045 turned out to be compiled C and is now `SEG045.C`. Every code segment outside the C runtime library rebuilds byte for byte.
- **Linked:** TLINK in headless DOS produces an EXE of the same size, identical to the original byte for byte, relocation table order included, except two bytes: the overlay segment table's code flag for seg003 and seg004 (1 where UW2 has 0). It runs to the title and menu, and a changed string in a C source shows on screen.
- **Names:** symbols.tsv holds 2,919 names: 2,291 original (from the FM Towns build), 12 runtime library, 616 provisional.
- **Not yet source:** about 83 KB of far data (the graphics and 3D modules' tables and the 3D object models) and eight small, unreferenced DGROUP gaps, all taken from the user's EXE at link time.

## Timeline

94 commits. The first function matches (ovr154, the whole of `PLAYER.C`, 6,967 bytes) landed at 23:53 on 30 September 2026. All C code matched by 14:41 on 1 October, all code by 16:58, the first running link at 17:56, and the two-byte link at 21:22: about 21.5 hours from first file to finished link.

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
- The last two bytes are unexplained: TLINK 3.01 sets the segment table's code flag only for a class spelled exactly `CODE`, and the class `Code` gives 0 but moves the segments.

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

## Exhume's reproduction (1 October 2026)

`examples/uw2/prove.sh --map` runs Exhume's tools against the UW2Decomp checkout, read-only, writing everything to Exhume's build directory:

| Check | Result |
| --- | --- |
| Build all 154 sources (3 DOS sessions at a time, up to 12 sources each) | 154 built in 13 s; every object equal to UW2Decomp's (segments, data, publics, externs, fixups) |
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

One compile took about 30 seconds during the project. On the same machine on 1 October, UW2Decomp's tcc.mjs took about 7 seconds for one file and Exhume's runner about 2.5 seconds; batching many sources into one DOS session is what makes a full rebuild take seconds rather than an hour.

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

