# The method

Exhume rebuilds an old DOS program from source that the original tools compile, assemble and link to the same bytes. This is the method end to end, as it was used on Ultima Underworld II (docs/case-study-uw2.md has the numbers). Each stage has a skill under skills/ with the working detail; this page is the order and the reasons.

```
fingerprint -> map -> match -> verify -> link -> run -> prove liveness -> modding build -> readability pass
```

Every stage ends in a check that a machine can repeat. Nothing is accepted on judgement alone: a function matches or it does not, a file verifies or it does not, the linked EXE equals the original or exediff says where it differs.

## 0. Set up a project

A project is a directory with sources, target tables, a map and symbols.tsv, described by an `exhume.toml` (docs/config.md). The repository must never contain the game or the compiler: the user supplies both, the profile's setup scripts unpack the toolchain from the user's own disk images and check its MD5, and anything the link takes from the game is generated under the build directory at build time.

Requirements: Python 3.11 or later with `iced-x86`, Node 20 or later with `npm install` (which fetches dos-mcp, a headless js-dos), and for the Borland profile `mtools` and `7z` to unpack disk images. Optional, for speed: git, make and a C compiler to build emu2 (`sh tools/setup-emu2.sh`), or DOSBox-X.

### Choosing the DOS

Every compile, assembly, library and link goes through `tools/dosrun.mjs`, which stages a directory as `C:\` and runs a batch file in the DOS that `tools/dosbackend.mjs` picks. `EXHUME_DOS`, or `[toolchain] dos` in exhume.toml, chooses it:

- `emu2`: [emu2](https://github.com/dmsc/emu2), a small emulator for DOS command-line programs. `sh tools/setup-emu2.sh` clones it into `tools/emu2` at a pinned commit and applies `tools/emu2-date.patch`. Each batch line runs as its own emu2 process with the staged directory as `C:`. There is no COMMAND.COM, so a line must be `echo` or a program in `C:\` with an optional `>` or `>>` at the end; anything else (a DOS internal command such as `copy`, a pipe, input redirection, a nested batch file) stops the run with an error naming the line. The profiles' build lines (`TCC ... > STEM.LOG`, `TASM ...`) and a link batch (`TASM`, `TLIB X @LIB.RSP`, `TLINK @LINK.RSP`, a `.COM` the batch has just linked) all fit.
- `dosbox-x`: DOSBox-X, headless (SDL's dummy drivers), on a copy of the staged directory mounted as `C:`. It runs any batch.
- `staging`: DOSBox Staging. Its macOS build has no headless video, so every run opens a window for a second or two; it is never picked automatically.
- `jsdos`: js-dos, DOSBox compiled to WebAssembly, in headless Chrome through dos-mcp. It needs nothing beyond `npm install`, and it is slow and occasionally drops a session part of the way through a batch.
- `auto`, or unset: emu2 if it is built, else DOSBox-X if it is installed, else js-dos. `node tools/dosbackend.mjs` prints the one in use.

`EXHUME_EMU2`, `EXHUME_DOSBOX_X` and `EXHUME_DOSBOX_STAGING` name a binary. The program itself (`tools/rungame.mjs`, `gate.py boot`) always runs in js-dos, which gives it a screen, keys and memory reads.

How many DOS sessions run at once follows the backend (`tools/dosbatch.py`): one per core up to 12 in a native DOS, three in js-dos, where each session is a headless Chrome and more of them slow each other and damage more outputs. `EXHUME_DOS_SESSIONS` or `[toolchain] sessions` overrides it. The gate, `build.py --all` and the modding build put several sources in each session and build each source that failed in a batch once more alone.

A linker that records the date of the link (Borland TLINK's `__EXEDATE__`) needs a DOS that honours INT 21h AH=2Bh, set date, for the programs that run after the one that sets it. DOSBox and js-dos do. emu2 does only with `tools/emu2-date.patch`, which keeps the date in a file for the rest of the run; without it the link date comes out as today's, and UW2's link differs in three bytes.

The backends build the same bytes. On UW2, every object built in emu2, DOSBox-X and js-dos is the same once the Borland dependency records are set aside (the source and header time stamps Turbo C records, which differ between two js-dos runs too), and the exact link, its map and the modding build are byte-identical in all of them. docs/case-study-uw2.md has the timings.

## 1. Fingerprint the toolchain

Find the exact compiler, assembler, linker, memory model and switches, and prove them by compiling one function to the original's bytes (skills/fingerprint-toolchain).

- `tools/fingerprint.py GAME.EXE` reports the executable's format, overlay scheme, telling byte patterns and which profile's runtime strings it contains, and suggests the DGROUP paragraph.
- The proof is a match: a small function written in C that `tools/match.py` reports as MATCH. Then switches, one at a time, by what each one changes.
- Assemblers are fingerprinted by their padding: a single-pass assembler's unshortened forward jumps leave `nop`s that the compiler never emits.

## 2. Map the binary

Lay out every segment and procedure, decide which segment is which source file, and recover original names (skills/map-binary).

- An IDA listing gives procedures; `tools/locate.py` checks each segment's place in the file by disassembling it against the listing, and corrects stale offsets.
- A sibling build that kept its symbols (UW2: the FM Towns release) names functions: if it was linked from the same object list, its functions are in the same order, so `tools/align.py` aligns the two builds between known pairs and confirms each pairing against both call graphs.
- `tools/targets.py` writes one target table per segment: where the file's code is and what each function is called. Read every table before using it; listings mis-split procedures and tables can overrun into the next module.

## 3. Match

Write the source for one file until every function compiles to the original's bytes (skills/match-file for C, skills/match-asm for assembly).

- `tools/match.py src/FILE.C` compiles in headless DOS through `tools/build.py` and `tools/dosrun.mjs` (about half a second in emu2, several seconds in js-dos), compares each function with fixup bytes masked, and with `--dis` shows an instruction diff.
- The profile's compiler.md lists what the output reveals about the source: declaration order from the frame, types from widening, statement shapes from register reuse and tail merging, file-scope names from the `_BSS` layout.
- Assembly starts from `tools/asmgen.py --fix`, a generated draft that already assembles to the right bytes, and is then turned into source a person would have written.
- Agents do most of this work. skills/orchestrate covers models, how many at once, budgets, and the build queue for sandboxed agents.

## 4. Verify and merge

Check everything match masks, then accept the file through gates (skills/verify-and-merge).

- `tools/verify.py` checks every fixup (each extern resolves to one address, no address has two names), the file's own publics (including overlay stub entries), its initialised data byte for byte, its `_BSS` base and its far data segments, and every name against symbols.tsv.
- `tools/merge.py` builds fresh, requires a whole-segment match and a clean verify, merges the names into symbols.tsv and records the file in matched.txt. It changes nothing on any failure.
- After any rename, `tools/rebuild-symbols.py` rebuilds symbols.tsv from scratch.

## 5. Link

Link the objects with the original linker in headless DOS and compare the result with the original (skills/link-and-diff, docs/link.md).

- The module order comes from the EXE: the relocation table is written module by module, and an overlay segment table lists every segment in order.
- What no source holds yet is taken from the user's own EXE at build time, as data-only assembler modules, so the link can be complete before the decompilation is.
- `tools/exediff.py` compares header, relocations (set and order), the resident image segment by segment and each overlay. The link catches what masked matching cannot: wrong callees, constants that were relocations, functions that were static, overlay public order.

The link stage is the least general part of Exhume. For UW2 it is a reference implementation in examples/uw2/, because most of it encodes facts measured from UW2.EXE.

## 6. Run

Boot the linked EXE among the game's files in headless js-dos and look at it: `tools/rungame.mjs` presses keys, takes screenshots and searches memory. A byte-identical EXE should run; this catches an environment problem (the wrong data directory, a missing file) rather than a code one.

## 7. Prove liveness

Change one visible thing in one source (a string), build it into a scratch directory, link it in place of the matched object, run it, and see the change on screen. exediff should report exactly the changed bytes. This proves the build really comes from the sources: that no stale object, extracted module or copied file stands in for them.

## 8. Modding build

Make the sources changeable by any size, which is what a fix, a mod or a port needs first (skills/modding-build, docs/link.md).

- The exact link keeps a snapshot of the matched objects, their sources' hashes and where their data sits. `link.py --mod` compiles only the sources that changed since, into a separate directory, and lays the image out from the snapshot. With nothing changed it must give the exact link's EXE.
- A literal address and a symbolic one assemble to the same bytes, so match and the exact link cannot tell them apart; only a link that moves things can. Audit the layout: scan with `tools/addrscan.py`, write each address it finds as a name, then boot with DGROUP shifted and bisect by padding `_BSS` in different files to find the ones the scan missed (docs/link.md, "Layout audits").
- Prove it the way liveness was proved, with edits that change sizes: a longer string and new code in an overlay, new data and code in a resident file. Boot each into the program's main scene, and check a marker that new code changes at run time with rungame.mjs's memory search (`m:TEXT`).

## 9. Readability pass

Make the tree readable without changing a byte (skills/readability-pass, docs/readability.md), in six steps, each committed on its own and proved by the gate.

- First the gate: `tools/gate.py check` proves the whole tree in one command (every source matches and verifies, symbols.tsv rebuilds from scratch, the exact link is byte-identical to the original, the unchanged modding build equals the exact link), recompiling only what changed, including every source that includes a changed header. A pre-push hook runs it; hosted CI runs `tools/repocheck.py`, which needs neither the toolchain nor the program.
- Then shared headers (`tools/declinv.py`, `tools/structrec.py`, `tools/headergen.py`), named constants, struct fields and accessors (`tools/rawoffsets.py`, `tools/accessors.py`), original file names and subsystem directories (tools find sources by segment, `tools/sources.py`), and comments, subsystem notes and a findings page (`tools/comments.py`, which proves an edit changed only comments).

## What "done" means

- Every code segment outside the C runtime library has source that matches whole and verifies.
- The linked EXE equals the original, or every remaining difference is listed and explained (UW2: two flag bytes in the overlay segment table, with a hypothesis).
- The game runs from the linked EXE, and a source change reaches the screen.
- Data no source owns yet is listed (UW2: about 83 KB of far data from the graphics and 3D modules, and eight small DGROUP gaps).
- The modding build links changed sources of any size and runs, and the layout audit lists what still has to keep its length (UW2: the extracted far data and the assembly modules that address it by number).
- The tree reads as source: shared headers, names for constants and fields, file names with their evidence, every file commented, the findings written down; and one command, run before every push, proves it still builds the original.
