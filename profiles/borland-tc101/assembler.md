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
- **`.model` and `.code` name the code segment after the file**, `FILE_TEXT`, as Turbo C does for a C file, so renaming such a module renames its segment (UW2: `gfx/MODEX.ASM` gives `MODEX_TEXT`). A module that names its segments with `SEGMENT` directives keeps them whatever the file is called (UW2: `SEG003_TEXT`, shared by seg003's 14 modules). A module sharing a segment with another file must use that file's segment name (UW2: `3d/SETPNT.ASM` declares `GRIDDB_TEXT`, the code segment of the C file it shares seg019 with); see linker.md.
- **A code segment's class matters to the link, though the EXE does not store it.** TLINK orders segments in blocks by class and sets the overlay segment table's code flag only for a class ending in upper-case `CODE` (linker.md, "Segment classes"). Keep a module's code segment in the class of the segment it joins. UW2 had three: seg000 to seg002 (`SPRITE.ASM`, `VALLOC.ASM`, `LPFDELTA.ASM`) are `ASMCODE` in UW2Decomp, the `SEG003_TEXT` and `SEG004_TEXT` declarations of the 28 graphics and 3D modules `code`, everything else `CODE` (seg021's `SEG021_TEXT` included); the pattern is proved, the spellings chosen. TASM with `/ml` keeps a class as written; MASM 5.1 upper-cases class names unless run with `/Ml`.
- **A `.model` segment cannot take another class.** TASM keeps the class `.model` gives its segments, so a `segment` directive cannot move `.code`'s `FILE_TEXT` into another class. Declare the segment by hand under the name `.model` gave it (`VALLOC_TEXT segment word public 'ASMCODE'`, with `assume cs:VALLOC_TEXT, ds:DGROUP`) and add what `.model medium` added, an empty `_DATA segment word public 'DATA'` and `DGROUP group _DATA`; the object then differs from the `.model` one only in the class name (UW2: VALLOC.ASM and LPFDELTA.ASM).
- **Modules that switch DS** (UW2: seg003 uses seg_370D; seg021 its own data segment; seg046 `_OVRGROUP_`) need `tools/asmgen.py --no-near-names`, or the draft names their data after unrelated DGROUP variables.

## Addresses written as numbers

- **A literal address and a symbolic one assemble to the same bytes.** `mov si,6742h` and `mov si,offset DGROUP:_Palettes` give `BE 42 67`; the second also carries a fixup. Match masks fixups and the exact link moves nothing, so both match and both link to the original. Only a link that moves data shows the difference: UW2's SEG004N had the number, and its object sprites were drawn with the wrong palette as soon as DGROUP data before DS:6742 grew. Write every operand that is an address as a name (`offset DGROUP:name`, `offset name`, `seg name`), and check with `tools/addrscan.py` (docs/link.md, "Layout audits").
- The same holds for a stack placed at a number: UW2's SEG021Q set `mov ax,211Ch; mov sp,ax` before `mov ax,DGROUP; mov ss,ax`, the top of a private stack at the start of another module's `_DATA`, now `mov ax,offset DGROUP:_joy_position`.
- **In a FAR_DATA segment, `dw offset NAME` for a far extern gives NAME's offset in its own segment**, not in the data segment. That is how a table of near code offsets kept in far data (UW2: the 3D model interpreter's opcode table, jumped through with `jmp word ptr [bx+24F4h]`) can be written by name, so it follows the code when the code moves.

## C with inline assembly

- **C with inline assembly** (`#pragma inline`) goes through TASM, and shows it: a call to a later function in the same resident file becomes `push cs; call near; nop` (`0E E8 xx xx 90`), which TCC alone never produces. UW2's seg013 is mostly pseudo-registers (`_AH = ...; geninterrupt(0x67);`) with two short `asm` statements; prefer pseudo-registers wherever they reproduce the bytes (`_BX = 0` gives `xor bx,bx`, so a literal `mov bx,0` needs `asm`). TCC needs TASM.EXE beside it in C:\ for such files, which the config's `[toolchain] stage` provides.

## Drafting with asmgen

- **`tools/asmgen.py SEG --fix`** drafts a module from the EXE bytes, the IDA listing and symbols.tsv, then assembles, compares and rewrites until it matches, falling back to `db` (with the instruction as a comment) where TASM cannot produce the bytes. Resident segments only. Read every `db ...; instr` line: each one is either an encoding TASM never makes or a sign the source was not what the draft guessed.
