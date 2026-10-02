---
name: fingerprint-toolchain
description: Use at the start of a byte-matching decompilation of a DOS executable, before any matching, to identify the exact compiler, assembler, linker, memory model and switches that built it, and to prove the identification by compiling a function to the same bytes.
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

## Record

- Write what you proved, with the evidence, in the profile's compiler.md, assembler.md or linker.md (or start a new profile directory with a profile.toml: command lines, name prefix and limit, library-name patterns, log patterns).
- Write the project's facts (EXE path, DGROUP paragraph, overlay naming, listing) in exhume.toml.

## Traps

- A match with fixups masked proves code generation, not names or switches that only affect data. `-d` shows only in the data segment; verify.py proves it.
- One file needing a switch does not prove all do. UW2's seg045 was the one file compiled without `-Y`.
- If a backward jump that would fit in a byte is `E9`, or a same-segment call is `9A`, the code was not written as plain TASM source (raw bytes, compiled C, or MASM).
- A new toolchain in emu2: emu2 has no COMMAND.COM, so each profile command line must be one program with at most one `>` or `>>` at its end; it refuses anything else by name. A compiler that runs a second program itself (a driver calling its passes, a linker spawned by the compiler) needs that to work in emu2 too; if it does not, use `EXHUME_DOS=dosbox-x`. Before trusting a fast DOS with a new toolchain, build the proof functions in it and in js-dos and compare the objects.
- A toolchain that stamps the date or time into its output (TLINK's `__EXEDATE__`) needs a DOS that lets the date be set; see profiles/borland-tc101/linker.md.
