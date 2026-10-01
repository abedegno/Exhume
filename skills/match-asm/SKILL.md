---
name: match-asm
description: Use when rebuilding a hand-written assembly module of an old DOS program as assembler source that assembles byte for byte to the original (TASM 2.0 for the Borland profile), starting from tools/asmgen.py's generated draft and turning it into source a period programmer would have written.
---

# Match one assembly module

Paths are relative to the Exhume checkout. Read the profile's assembler.md first (`profiles/borland-tc101/assembler.md`), especially the single-pass padding rules.

## Is it assembly?

- Few standard frames (`push bp; mov bp,sp`), 386 instructions in a 16-bit program, `retf` without frames, port I/O and string instructions in bulk, or a jump padded with `nop`s the compiler never emits: assembly. `tools/files.py` classifies segments by frame share.
- Compiled C kept as assembly is a trap: UW2's seg045 had Turbo C idioms throughout and a `_BSS` laid out exactly by the compiler's name key. Rewrite such a module in C.
- A known library is a gift: UW2's seg046 was Borland's VROOMM overlay manager, equal to OVERLAY.LIB's modules outside their fixups. Compare against the toolchain's own libraries before writing anything.

## Steps

1. Fix the target table first. It must run from the module's first byte to the padding before the next module (zero bytes after a `ret`/`jmp` are the linker's alignment between separately assembled modules). Listing proc boundaries inside it may be wrong; you may re-split rows in your own table. Keep names to 32 characters or fewer.
2. `python3 tools/asmgen.py SEG --fix > src/SEGxxx.ASM`. It decodes the bytes with the listing's help, writes TASM syntax, assembles in DOS, compares item by item and rewrites what differs until it matches, falling back to `db` with the instruction as a comment. Resident segments only. Use `--no-near-names` for modules that point DS somewhere other than DGROUP; `--keep-db` to leave undecoded runs as data.
3. `python3 tools/match.py src/SEGxxx.ASM` must say WHOLE SEGMENT MATCHES, and `python3 tools/verify.py src/SEGxxx.ASM` (without `--update`) must verify.
4. Now make it source. Replace each `db` fallback with a real instruction wherever the assembler can produce the bytes (`short`, `.186`/`.386`, negative constants for sign-extended immediates, `retn`, `nosmart` around the few instructions that need it). Keep a `db` only where the assembler cannot make the encoding, with the instruction and the reason in a comment. Every remaining `db ...; instr` line is either an encoding the assembler never makes or a sign the source was not what the draft guessed.
5. Name labels from the sibling build where the code clearly corresponds (`tools/sibling.py NAME`), with a short comment saying why. Turn obvious data into tables, strings and `dw seg X`.
6. Rebuild and re-verify after each batch of changes.

## Module boundaries

A segment can be several original modules. The linker's padding between modules (to a word or a paragraph) and the order of the relocations in the EXE (the linker writes them module by module, a library's modules in library order) give the split. UW2's seg003, seg004 and seg021 were 14, 14 and 17 modules; the link proved it by reproducing the relocation table's order exactly.

## Rules

As match-file: only your own source and your own target table; no git; no guessing; report `db` fallbacks left and why, CPU directives and options with evidence, names recovered, table rows changed, and assembler behaviour not already in assembler.md (tested only).
