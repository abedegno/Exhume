# Reading a bare C28x image in Ghidra

Matching needs no decompiler: the map comes from dis2000 and flow analysis, and the gate compares
words. Ghidra is for the step before that, working out what an image with no symbols does, and
for a second opinion on the map's function starts. Exhume ships nothing for it. The processor
module and its scripts are Nishanth Samala's
[ghidra-tms320c28x](https://github.com/outlandnish/ghidra-tms320c28x) (Apache-2.0); its
`docs/C28X_IMAGE_SETUP.md` is the reference for each step. This page holds only what one image
(a 64K-word F2806x application, Ghidra 12.1.4) taught me about using it.

## Setting up

- Build the extension with Gradle (`gradle -PGHIDRA_INSTALL_DIR=...`) and install the zip from
  `dist/`. Copying the folder into Ghidra's Extensions directory loads the processor, but
  `EmulateStartup` needs the module's emulator class, which only the built zip has.
- Ghidra refuses an extension whose `extension.properties` version differs from its own.
- Copy the scripts you run into a folder of their own and pass that as `-scriptPath`. Ghidra
  compiles a script folder as a unit, so one script that does not compile against your Ghidra
  stops every script in the folder loading. (`DumpAppliedProtos.java` did that to me on 12.1.4.)
- A vendor image may be byte-swapped relative to what the module expects. The check: after the
  swap, `LCR` targets land inside the image.
- Import with `-loader BinaryLoader -loader-baseAddr` set to the image's **word** address. The
  address space has 2-byte words, so a script builds an address with `getAddress(word * 2)`.

## The order that worked

1. A setup script for the part (the module has `SetupF2812` and `SetupF28377D`; I wrote one for
   the F2806x): RAM as initialised read/write/execute blocks, peripheral frames as volatile, and
   flash outside the image, OTP and boot ROM as **uninitialised** blocks, or `SeedFunctions`
   takes them for code.
2. `SeedFunctions`: functions from call targets and prologues, and `_c_int00`.
3. `EmulateStartup apply`, with `-Dc28x.emu.ramBlocks=` naming the RAM blocks: runs the image's
   own startup and keeps what it writes, so the ramfuncs and the `.cinit` values are in RAM
   without my working out how the image copies them. It should stop when startup hands over
   to `main`. `MaterializeSections` and `MaterializeCopyTable` do the same for the two copy
   mechanisms they know.
4. Full auto-analysis.
5. `MarkJumpTables`, `MarkDataTables`, `FinalizeRamfuncs`, `MergeSplitFunctions`, then
   `ReachabilityReport`.

Upstream has added steps since (`MarkComponentRegistry`, `SweepResidualMarks`); follow its
document for the current list.

## What to take from it, and what not

- Function starts and switch tables are a check on `map/functions.tsv`, not a source for it. A
  start Ghidra has and the map lacks, or the reverse, is worth a look in dis2000's listing.
  `SeedFunctions` also splits some functions at a register push in the middle; that is what
  `MergeSplitFunctions` repairs.
- The decompiler's C is a guide to meaning, never to the source's shape. compiler.md has the
  misreadings I met (float constants loaded with `MOVIZ` alone).
- The module's emulator is good enough to run single functions on chosen inputs, which settles
  what a routine computes before any C is written. It had three instruction bugs when I started
  (`SUBF32`, `ADDB`, the flags of `ADD loc16`), all fixed upstream since; if emulation and the
  disassembly disagree, suspect the module and check TI's instruction reference.
