# The method

Exhume rebuilds an old DOS program from source that the original tools compile, assemble and link to the same bytes. This is the method end to end, as it was used on Ultima Underworld II (docs/case-study-uw2.md has the numbers). Each stage has a skill under skills/ with the working detail; this page is the order and the reasons.

```
fingerprint -> map -> match -> verify -> link -> run -> prove liveness
```

Every stage ends in a check that a machine can repeat. Nothing is accepted on judgement alone: a function matches or it does not, a file verifies or it does not, the linked EXE equals the original or exediff says where it differs.

## 0. Set up a project

A project is a directory with sources, target tables, a map and symbols.tsv, described by an `exhume.toml` (docs/config.md). The repository must never contain the game or the compiler: the user supplies both, the profile's setup scripts unpack the toolchain from the user's own disk images and check its MD5, and anything the link takes from the game is generated under the build directory at build time.

Requirements: Python 3.11 or later with `iced-x86`, Node 20 or later with `npm install` (which fetches dos-mcp, a headless js-dos), and for the Borland profile `mtools` and `7z` to unpack disk images.

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

- `tools/match.py src/FILE.C` compiles in headless DOS through `tools/build.py` and `tools/dosrun.mjs`, compares each function with fixup bytes masked, and with `--dis` shows an instruction diff.
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

Boot the linked EXE among the game's files in headless DOS and look at it: `tools/rungame.mjs` presses keys and takes screenshots. A byte-identical EXE should run; this catches an environment problem (the wrong data directory, a missing file) rather than a code one.

## 7. Prove liveness

Change one visible thing in one source (a string), build it into a scratch directory, link it in place of the matched object, run it, and see the change on screen. exediff should report exactly the changed bytes. This proves the build really comes from the sources: that no stale object, extracted module or copied file stands in for them.

## What "done" means

- Every code segment outside the C runtime library has source that matches whole and verifies.
- The linked EXE equals the original, or every remaining difference is listed and explained (UW2: two flag bytes in the overlay segment table, with a hypothesis).
- The game runs from the linked EXE, and a source change reaches the screen.
- Data no source owns yet is listed (UW2: about 83 KB of far data from the graphics and 3D modules, and eight small DGROUP gaps). Until it has source, only changes that keep data sizes are safe.
