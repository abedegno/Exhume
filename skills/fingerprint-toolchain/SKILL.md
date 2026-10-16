---
name: fingerprint-toolchain
description: Use at the start of a byte-matching decompilation of a DOS executable (or, with the armcc5 profile, an Arm Cortex-M firmware image; with the ti-c2000 profile, a TI C2000 DSP image), before any matching, to identify the exact compiler, assembler, linker, memory model and switches that built it, and to prove the identification by compiling a function to the same bytes.
---

# Fingerprint the toolchain

Paths are relative to the Exhume checkout. Nothing else in the method works until one function compiles to the original's bytes, so this step ends with that proof, not with a guess.

## Steps

1. Run `python3 tools/fingerprint.py GAME.EXE`. Note the MZ facts, whether there is an overlay block (FBOV is Borland VROOMM), the counts of standard frames (`55 8B EC`), `enter`, far calls, INT 3Fh thunks and far returns, and which profile's runtime strings it finds.
2. Read the runtime strings for a version, not just a vendor. "Turbo C++ - Copyright 1990 Borland Intl." is Turbo C++ 1.0x, not Borland C++ 2.0 or 3.x. Get that exact version: switches, code generation and the runtime library differ between point releases.
3. Find the memory model from the code: far calls (`9A`) between segments with globals addressed near (`mov bx,[828Ah]`) mean medium, as in UW2; data reached through `les bx,[...]` is an explicit far pointer there. UW2 also showed DS equal to SS: a stack array is indexed with no `ss:` override.
4. Set DGROUP: fingerprint.py suggests a paragraph from C0's copyright string. Confirm it with a string the code references by DS offset (UW2: "trap" at DS:1AEB is at file 0x6A57B, so DS:0 is file 0x68A90, paragraph 0x65E9). Put it in exhume.toml as `dgroup_para`.
5. Get the toolchain from the user's own disk images and unpack it with the profile's setup scripts (`profiles/borland-tc101/setup-tc.sh`, `setup-tasm.sh`). They check MD5s. Never add compiler binaries or game files to any repository. Then choose the DOS it runs in (docs/method.md, "Choosing the DOS"): build emu2 with `sh tools/setup-emu2.sh` (a compile then takes about half a second rather than several in js-dos, which matters over the hundreds of compiles fingerprinting takes), and check with `node tools/dosbackend.mjs`.
6. Pick one small, self-contained function (no calls, or calls only to other files). Write C for it, put `/* target: SEG */` and `/* opts: ... */` lines at the top, make a target table row for it, and run `python3 tools/match.py src/FILE.C --dis NAME`. Iterate on the source and the switches until it says MATCH. Then do two more functions from different segments.
7. Find the switches by the differences they make, one at a time: `-G` (`add sp,N` cleanup, no `enter`), `-O`, `-Z` (register reuse across stores), `-Y` (overlay code: a same-file function address is a relocated segment, not `mov ax,cs`), `-d` (duplicate strings stored once in the data segment), `-k-` (no frame in argument-less functions). Expect them to vary per file; each file names its own in its opts line.
8. For assembly, assemble a routine rebuilt from its disassembly. Padding is the tell: TASM 2.0 single-pass writes an unshortened forward jump as the short jump plus `nop`s. If the padding comes out the same, you have the assembler.

## Arm Compiler 5 (the armcc5 profile)

The same proof, other tells; profiles/armcc5/compiler.md has the evidence.

- Switches from the code: a short conditional as compare and branch is `-O1`, as an IT block
  `-O2` or `-O3`; a 16-bit divide by a constant as multiply and shift needs `-Otime`; a literal
  pool after every function means `--split_sections`. MicroLIB shows in its compact `__main` and
  `__scatterload`.
- The release, not just the version: armcc 5's updates compile nearly all code alike, so one
  matching function proves little. Install every MDK release you can get (each is one toolchain
  home), match every merged file under each with `profiles/armcc5/tools/armbuilds.py`, and read
  the files that only some releases match. On one firmware every file matched under 5.05u1 to
  5.06u2, and a single library function, 2 bytes longer from 5.06u3 on, settled the range. Repeat
  this when the tree is nearly done: the last stubborn functions are the best test, and a
  function nothing matches may be the compiler's, not the source's.
- The libraries are evidence too: MicroLIB's members come from the MDK release (link the image's
  `_printf_core` against each release's `mc_w.l`), and a vendor peripheral library's functions
  can match one release of its source and not the next (compile every release).
- `armcc --vsn`, and the `.comment` section of any object, name the build ("ARM Compiler 5.06
  update 2 (build 183)"); several MDK releases ship the same build.

## TI C2000 (the ti-c2000 profile)

The same proof; profiles/ti-c2000/compiler.md, "Fingerprinting the version", has the evidence.

- The runtime library first: `profiles/ti-c2000/tools/c28libfp.py --libs 'GLOB/lib/rts2800_fpu32.lib'`
  places each release's members in the image with relocated words masked and prints how many
  each places. The library can be older than the compiler, so it bounds the build, not the
  compiler.
- Then the compiler: install every release you can get from TI's public server into one Wine
  prefix, and run `match.py SRC --all-cgt` on matched files. Defect fixes and changed code shapes
  pin versions (an interrupt epilogue's `SETC INTM|DBGM` before `POP RB` from 15.12.2; a struct
  member past 255 words through `MOVL XAR0,#k` from 15.12.7 and 16.9.5; the -Ooff frame layout
  from 16.9.0). Neighbouring releases break different functions; the right one breaks none.
- A file that "needs" another compiler is evidence about the whole tree: try the whole tree under
  that compiler before giving one file a `/* cgt: */` line. On one firmware every file matched
  under the release a few files seemed to need, once functions bent to fit the first guess were
  written plainly again.
- `-O1`, not -O2: one function matched only at -O1. `--gen_func_subsections=on` changes no code
  and the tools always add it.

## Record

- Write what you proved, with the evidence, in the profile's compiler.md, assembler.md or linker.md (or start a new profile directory with a profile.toml: command lines, name prefix and limit, library-name patterns, log patterns).
- Write the project's facts (EXE path, DGROUP paragraph, overlay naming, listing) in exhume.toml.

## Traps

- A match with fixups masked proves code generation, not names or switches that only affect data. `-d` shows only in the data segment; verify.py proves it.
- One file needing a switch does not prove all do. UW2's seg045 was the one file compiled without `-Y`.
- If a backward jump that would fit in a byte is `E9`, or a same-segment call is `9A`, the code was not written as plain TASM source (raw bytes, compiled C, or MASM).
- A new toolchain in emu2: emu2 has no COMMAND.COM, so each profile command line must be one program with at most one `>` or `>>` at its end; it refuses anything else by name. A compiler that runs a second program itself (a driver calling its passes, a linker spawned by the compiler) needs that to work in emu2 too; if it does not, use `EXHUME_DOS=dosbox-x`. Before trusting a fast DOS with a new toolchain, build the proof functions in it and in js-dos and compare the objects.
- A toolchain that stamps the date or time into its output (TLINK's `__EXEDATE__`) needs a DOS that lets the date be set; see profiles/borland-tc101/linker.md.
