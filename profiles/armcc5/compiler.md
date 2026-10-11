# Arm Compiler 5.06u7 (armcc): what matching taught

Evidence is a matched function in a real firmware or a test in tests/.

## Flags

- `-O1` turns a conditional increment into a compare and branch (`cmp; bcs; adds; strh`);
  `-O2` and `-O3` use an IT block (`cmp; it cc; addcc; strhcc`) instead. That makes the
  optimisation level visible in any short conditional.
- `-Otime` divides a 16-bit value by a constant with a multiply and shift (by 10:
  `movw #0xCCCD; muls; lsrs #19` or `ubfx`); without it the compiler emits `udiv`.
- `--split_sections` puts each function in its own section `i.<name>`, with its own literal
  pool straight after it. Without it, the pools of neighbouring functions merge.

## Data

- An uninitialised global (`unsigned char g;`) goes into `.data` with the initialised ones,
  not `.bss`.
- Within `.data` the order isn't definition order: a 1-byte global defined after a 6-byte
  table came first (offset 0, table at 2).
- Uninitialised globals of at most 8 bytes go into `.data`; larger ones into `.bss` (armcc's
  default `--bss_threshold=8`). A 64-byte buffer made the test firmware's zero-init region.

## Libraries

- MicroLIB (`--library_type=microlib`, Keil's "Use MicroLIB") has its own compact startup:
  `__main` (`ldr.w sp,[pc]; bl __scatterload; ldr r0,=main; bx r0`), `__scatterload` walking the
  region table with `ldr r4/ldr r5`, and `__decompress`, `__scatterload_zeroinit` and
  `__scatterload_copy` as handlers. The standard library links `__scatterload_rt2` and
  `__dczerorl2` instead. Both compress RW data in the same format (tools/lz77.py). MicroLIB needs
  `__initial_sp`, which a startup file normally defines.

## Learned while matching a real firmware

- A byte load (`ldrb [r1,#0x61]; tst #2`) testing a bit of a halfword field comes from a 1-bit
  bitfield (`unsigned short b9:1` in a `__packed` struct); an ordinary mask (`u16 & 0x200`) keeps
  the halfword load (`ldrh [r1,#0x60]; tst.w #0x200`).
- An OR of several bitfields compiles to separate byte tests; one halfword test
  (`ldrh; bic #0xf9; bics #0xfb00; beq`) is `*(unsigned short *)&f & 0x406`. A mask that isn't a
  Thumb-2 immediate becomes two `bic`s.
- Zero assignments keep source order, and later zeros copy the first one's register:
  `j = 0; ... for (; j < N; j++)` gives `movs r2,#0` first, where `for (j = 0; ...)` put it last.
- A struct used inside a `__packed` struct must itself be `__packed` (else error #1032);
  halfword fields at odd addresses still compile to plain `ldrh`/`ldrsh` on Cortex-M3.
- A long function can carry a literal pool in its middle, with a `b` over it.
- Compare constants stay as written: `x < 10` is `cmp #10; bhs`, `x <= 9` is `cmp #9; bhi`, so the
  source's `<` or `<=` reads straight off the bytes.
- `uint16 / 250` at -Otime is `(x+1)*0x4189 >> 22` (`movw #0x4189; mla r1,r1,r4,r4; ubfx #22,#8`);
  writing `(x+1)/250` gives `adds; umull; ubfx #4` instead.
- Globals read directly in each condition compile differently from cached locals: direct reads
  hoist one set of loads with a fresh literal and keep a `tst/bne` per term; locals fuse terms
  into `and; and; orrs`.
- In a switch over a global struct, fields used by several cases are loaded once before the
  `tbb`; a field used by one case is loaded inside it.
- `g.buf[g.state++] = c` in a case where the state is a known constant reloads the struct base
  (`ldr r1,=g` again); `g.state = N+1; g.buf[N] = c;` does not.
- Two reads of one byte, one through a struct field and one through a byte-pointer cast, stay two
  loads; the same lvalue twice merges into one.
- A file's own initialised globals are addressed relative to each other (`ldr r6,=g_A; add.w
  r7,r6,#8` for g_B defined just after g_A); `extern` declarations each get their own literal. So
  an `add` from another global's base means both were defined in the original file.
- Bitfields on `u8` compile to `ldrb/bic/orr/strb` on the byte, on `u16` to `ldrh/strh` on the
  halfword. A constant into a 2-bit field is `bic #3; add #n`; storing 3 is `orr #3`.
- `if (x == 0) A; else B;` puts A out of line (after later code, through `b` trampolines);
  `if (x) B; else A;` keeps the else inline.
- Locals cached at the top of a large function push bases into high registers (wide encodings);
  reading fields at each use lets armcc load once at entry with low registers.
- A clamp into a `u32` then narrowed (`if (f > K) c = K; else c = f; t = c;`, `u16 t`) is
  `cmp; bls; mov; uxth`; clamping straight into a `u16` adds a `b` and a `uxth` on one path.
- `(u16)(x << 8) | b` stored to a u16 keeps `and.w r2, sl(=0xffff), r2, lsl #8`; without the cast
  the mask goes.
- `(u16field + 5) / 10` with signed int arithmetic still uses `umull` by 0xCCCCCCCD; the signed and
  `+ 5u` forms schedule the literal load differently.
- How a clamp is written decides register allocation even when the instructions are the same:
  `v = f > K ? K : f;`, `if (f > K) c = K; else c = f;` and `if (v > K) v = K;` swap r0/r1 around
  it. When a mismatch is only a register swap near a clamp, try all three.
- armcc keeps r1-r3 and ip live across a `bl` to a function defined anywhere in the same file,
  before or after the caller (it knows the callee's register use); with only a prototype it saves them in r4-r10 instead. So a
  caller that keeps r1-r3 live across a call shares its source file with the callee. `__weak`
  definitions switch this off.
- `#pragma O0` inside an `-O1` file gives code neither command-line level gives
  (`sub sp,#4; movs; str; b test; ...; add sp,#4; bx lr` for a volatile counter loop); functions
  under it still pass on their register use.
- Dead `else if` arms armcc deletes can still leave a shared subexpression hoisted where they
  were; the hoist shows in the bytes.
- `for (i = 8; i != 0; i--)` on a u8 is `subs; uxtb; cmp; bne`; `do {} while (--i)` is
  `subs; ands #0xff; bne`; on a u16 the loops give `uxth` or `lsls/lsrs #16`.
- A local return value (`r = ...; return r;`) and direct returns allocate registers differently.
- `a = (a & ~0xf) | 0x10;` and `a &= ~0xf; a |= 0x10;` allocate differently; `& 0xf0` is `and`,
  `&= ~0xf` is `bic`.
- armcc can rematerialise a constant from a known register (`lsls r5,r1,#5` for 0x100 from 8).
- `if (!(x & 2)) r = 1; else r = 0;` puts `r = 1` after the return; `if (x & 2) r = 0; else r = 1;`
  keeps it inline.
- armcc's default `--data_reorder` sorts a file's `.data` by size, smallest first (equal sizes keep
  definition order). Where the original's RAM order isn't sorted by size, the file was built with
  `--no_data_reorder` (an `/* opts: */` line).
- Type-based aliasing decides reloads: declared as a struct, a count stays live in a register
  across unrelated u16 stores; declared as a `u16[]`, every u16 store may alias it and it is
  reloaded. When the original reloads a global after an unrelated store, try a plain array type.
- A separate small array global gets its own base register; a scalar can be reached from the
  section anchor (`subs r2,r5,#4; ... [r2,#0xc]`).
- An address-taken `u16` local is stored with a 32-bit `str` and read with `ldrh`; stack slots go
  in reverse declaration order (the first address-taken local gets the highest offset).
- `buf[n++]` after `n = 0` (constant-propagated) still changes code: a copied base, a hoisted
  constant in a callee-saved register; literal indices are shorter.
- `*p++` gives a post-indexed `ldrb r,[r1],#1`; `x = *p; p++;` gives `ldrb` then `adds`.
- MicroLIB's library members (printf8.o's `_printf_core` and friends) in a real image can come from
  a different MDK release than the compiler: same algorithm, different register allocation. They
  can't be matched from C; the gate needs that release's `mc_w.l`.
- The reverse also shows: a caller that saves its bases in r4-r8 across a call to a function in
  the same unit came from a different file; `__weak` on the callee reproduces that while both are
  in one file.
- `g.f = c ? A : 0;` gives one store after the join; `if (c) g.f = A; else g.f = 0;` stores in each
  branch.
- `(s16)-u32field` gives `rsbs; sxth`; `-(s16)u32field` gives `sxth; rsbs`.
- A checksum summed in a `u16` adds a `uxth` in the loop; in a `u32` it doesn't.
- The declaration order of locals decides register allocation, even within one declarator list
  (`u8 i, v, n;` matched where `u8 n, i, v;` was 368 bytes off).
- `if (c) v = 2; else v = 0; p->buf[n] = v;` gives one store after the join; storing in each branch
  keeps the constants in callee-saved registers.
- A field of an ordinary struct is loaded once across byte stores to other memory; a field of a
  `__packed` struct is reloaded. Which structs were packed shows in the reloads.
- The literal pool follows the order in which the source first refers to each literal, not the
  instruction order: it shows which global the source touched first.
- `i = g.f >> 4; i++;` gives `lsrs; adds; uxtb`; `i = (g.f >> 4) + 1;` hoists the 1 into a
  callee-saved register.
- A range check `(u32)(c - '0') > 9` gives `sub; cmp #9; bls`; `c >= '0' && c <= '9'` gives
  `cmp #10; blo`.
- Splitting a sign-extended sum into an `s16` local (`k = f + 2731; v = (k + 5) / 10;`) loads the
  divide's literal after the add; one expression loads it first.
- A known-zero index can come out as a register holding zero (`ldrb r0,[r2,r1]`), where a literal
  `buf[0]` gives an immediate offset.
- A function returning `u8` versus `u16` changes how callers mask the result.
- A non-leaf function keeps the stack 8-byte aligned at calls to extern functions by pushing an
  unused extra register (r8, or ip). When the callee is defined earlier in the same file and needs
  no aligned stack, the padding goes: an odd-count `push` in a non-leaf means a same-file callee.
- A range test `c >= 'A' && c <= 'F'` fuses into `sub; cmp #5; bhi`; nested ifs keep
  `cmp #0x41; blo; cmp #0x46; bhi`.
- `if (b == 0) {...} else if (w == 0) clear;` puts the clear out of line (`cbz` and `b`); the
  inverted form puts it inline with `cbnz`.
- `u8 *dst = p->buf; dst += 3;` keeps two adds; `&p->buf[3]` folds into one.
- A union local `{ u16 w; u8 b[2]; }` to split a word goes through the stack (`str; ldrb [sp,#1]`).
- `dst[n] = x; n++;` updates n in place; `dst[n++] = x;` takes a new register for n+1.
- `arr[a - 1]` with a u8 `a` gives `subs` without `uxtb`; `a--` first adds the `uxtb`.
- Branch order decides layout: the arm tested first (`if (p->err != 0)`) goes inline.
- A builder with constant indices may still have used a running index (`n = 14; ...; txlen =
  n - 14;`): the folded constant is loaded early. A literal at the end loads late.
- A value both clamped and split into bytes needs one `u32` local, or the multiply is redone.
