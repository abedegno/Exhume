# Borland Turbo Assembler 2.0: the assembler

How Turbo Assembler 2.0 (TASM.EXE, MD5 `b68a63d6a94672910d4149fad4c18f00`) encodes source, so that hand-written assembly modules and C with inline assembly can be rebuilt byte for byte. Learned by byte-matching UW2.EXE's assembly: ten modules, 71,920 bytes (UW2: seg001 to seg004, seg017, seg020 to seg022, seg045, since rewritten as C, and seg046), then seg000, seg018 and SetPnt in seg019; plus seg013, which is C with inline assembly. The compiler is in `compiler.md`, the linker in `linker.md`.

## Identifying TASM 2.0

UW2's assembly modules were assembled with something that behaves exactly like **Turbo Assembler 2.0 in its default single-pass mode**: a routine from seg004 rebuilds byte for byte with it, including six padding nops TASM generates itself (`profiles/borland-tc101/setup-tasm.sh` unpacks it; `tools/build.py` assembles `.ASM` files with TASM, options such as `/ml` from the source's `/* opts: */` line). MASM 5.1 is also single-pass and has not been ruled out.

- **The padding tells you what the source said.** With `.386`, a forward conditional jump written without `short` is reserved as a 4-byte near jump and, when the target turns out close, becomes the 2-byte jump plus `nop nop`; a forward `jmp` likewise becomes `EB xx 90`. Forward jumps with no padding were written `short`. Backward jumps are never padded. So write plain `jcc label` where the original has the `nop`s (and drop them from the source), and `jcc short label` where it doesn't.
- Register-to-register forms are the `8B` (reg, r/m) encodings and the AX short forms (`05 imm16`) are used, as TASM does by default.
- **8086 mode (no `.186`):** TASM 2.0 writes `push imm` as `50 55 8B EC C7 46 02 iw 5D`, so that sequence proves the module was assembled without `.186` (UW2: seg022).

## Encodings

- In UW2, seg004 uses 386 instructions with 32-bit registers in 16-bit segments: `.386` with `segment use16`.
- **`.386` makes constants 32-bit:** `cmp cx,0FFFCh` gives the `81` form and `push 0FFFCh` gives `68`; write `-4` to get `83` / `6A`. Under `.186`, `0FFFCh` already gives `83`.
- **AX with an immediate** always takes the short form (`05`/`3D`/`25`), even when the constant fits in a byte.
- **TASM always shortens within the segment:** a backward `jmp near ptr` becomes `EB`, a forward one `EB 90`; `call` or `call far ptr` into the same segment becomes `push cs; call near` (plus a `nop` when forward); `jmp far ptr` into the same segment becomes `EB`. So `9A`/`EA` into the module's own segment, or a backward `E9` that would fit in a byte, means raw bytes or compiled C (UW2's seg046 `9A` self-calls are calls between separately assembled library modules; see `linker.md`).
- **A forward call to a far proc** without `far ptr` is an error ("Forward reference needs override").
- **Displacements:** a relocatable label always takes disp16 (so `8A A7 10 00` is a data label, not a number); a forward numeric equate gives disp8 plus `nop`.
- **`ret` in a `proc far`** assembles as `CB`; write `retn` for `C3`.
- `xlat cs:label` and `xlat byte ptr cs:[bx]` both give `2E D7`; writing `cs:label[bx]` gives a disp16 and the wrong instruction.
- **`NOSMART`** turns off TASM's shortening: `call far ptr` into the same segment stays a real `9A`, `lea reg,ds:[direct]` stays `8D` (not `mov reg,offset`), and sign-extended `83` forms become `81` (`and di,0Fh` gives `81 E7 0F 00`). A backward `jmp near ptr` is still shortened. Code that mixes `83` forms with `9A` self-calls and direct `lea` was probably assembled by MASM 5.1, which does all three with no switches; in TASM, toggling `nosmart`/`smart` around just those instructions reproduces it (UW2: SEG046.ASM, macros `FCALL` and `LEAD`).

## Padding and data

- **Padding:** `even` in a code segment pads with `90h`; `align 4` in a word-aligned segment errors ("Segment alignment not strict enough") and pads with `87 DB`. Zero bytes between routines are not TASM's: see `linker.md` for TLINK's padding between modules.
- **`db N dup (x)` is emitted as an LIDATA record, which `tools/omf.py` ignores**, so a nonzero `dup` compares as zeros and shows as a false mismatch. Write nonzero runs out in full; zero `dup`s are fine. `align 16` in a TASM 2.0 code segment fills with `87 DB`/`90`, so zero runs between UW2's seg004 routines were written as data, not `align`.
- **A module can define its own `.data?`** (`_BSS`); verify.py checks its base. UW2's seg045 did, and its nine globals fall in exactly the order `profiles/borland-tc101/bssorder.py` predicts, with Turbo C idioms throughout: it was compiled C kept as assembly, and is now `SEG045.C`.

## Names, segments and modules

- **Names:** TASM keeps long names whole, but match.py looks up 32 characters, so keep table names to 32 or fewer.
- **Hand-written assembly modules** can sit where a C file was expected: UW2's seg017 is `src/SEG017.ASM` (its frames end `mov sp,bp; pop bp` with no locals, which neither TCC nor TASM's ARG/LOCAL produce). TASM 2.0 with `.186` makes `enter`/`leave` for ARG/LOCAL procs. An `extrn name:far` inside `.code` is taken as same-segment and called with `push cs; call near`, so declare externs outside it.
- **An assembly routine can share a C file's segment**: UW2's seg019 starts with SetPnt, an assembly module called with far calls from the C (a same-file C function would get `push cs; call near`). Its target table starts at the first C function (org 0x41).
- **Modules that switch DS** (UW2: seg003 uses seg_370D; seg021 its own data segment; seg046 `_OVRGROUP_`) need `tools/asmgen.py --no-near-names`, or the draft names their data after unrelated DGROUP variables.

## C with inline assembly

- **C with inline assembly** (`#pragma inline`) goes through TASM, and shows it: a call to a later function in the same resident file becomes `push cs; call near; nop` (`0E E8 xx xx 90`), which TCC alone never produces. UW2's seg013 is mostly pseudo-registers (`_AH = ...; geninterrupt(0x67);`) with two short `asm` statements; prefer pseudo-registers wherever they reproduce the bytes (`_BX = 0` gives `xor bx,bx`, so a literal `mov bx,0` needs `asm`). TCC needs TASM.EXE beside it in C:\ for such files, which the config's `[toolchain] stage` provides.

## Drafting with asmgen

- **`tools/asmgen.py SEG --fix`** drafts a module from the EXE bytes, the IDA listing and symbols.tsv, then assembles, compares and rewrites until it matches, falling back to `db` (with the instruction as a comment) where TASM cannot produce the bytes. Resident segments only. Read every `db ...; instr` line: each one is either an encoding TASM never makes or a sign the source was not what the draft guessed.
