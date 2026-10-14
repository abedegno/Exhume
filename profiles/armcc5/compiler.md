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
  before or after the caller (it knows the callee's register use); with only a prototype it
  saves them in r4-r10 instead. So a caller that keeps r1-r3 live across a call shares its source
  file with the callee. `__weak` definitions switch this off.
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
  unused extra register (r8, or ip). When the callee is defined in the same file (before or after
  the caller) and needs no aligned stack, the padding goes: an odd-count `push` in a non-leaf means
  a same-file callee.
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
- `(u16field + 5) / 10` with the value in r1 and 0xCCCCCCCD in r0 needs
  `u32 k; k = g.f; v = (k + 5) / 10;`; other spellings swap the registers or load order.
- A byte built from 1-bit bitfields after `dst.all = 0;` forwards the zero (`mov r2,rZero; lsrs;
  bfi; bfi; strb`, no load). A union used inside a `__packed` struct must itself be `__packed`.
- The zero-assignment order rule applies to mixed widths too: `i = 0; mask = 0; for (; ...)`.
- A caller's masking follows the prototype it saw, not the callee's definition: a file that
  declared `u16 f(u8)` keeps `and.w ..., lsl #4` masks even where `f` is defined returning `u8`.
- A clamp's form can move register allocation and literal loads far from the clamp; when a
  mismatch is a swap or a reordered literal load, try the clamp forms across the whole function.
- `g.buf[g.state++] = c;` reloads the struct base only when the known state is 1 or more; at state
  0 it doesn't (and a literal-index store there reuses another register for the 1).
- `if (c) s.f = 1; else s.f = 2;` on a bitfield hoists the shared `ldrb; bic` above the branch;
  the ternary gives `movs; ldrb; bfi; strb`.
- Several fields loaded before any store (`ldrh` x6, then `subw; strh` pairs) need locals for each;
  direct reads interleave loads and stores.
- `u16 / 25` at -Otime is `(x+1) * 0xA3D7 >> 20` (`movw #0xa3d7; mla; lsrs #20`).
- An extra u16 local for one value can fix register allocation hundreds of bytes away.
- A local `char s[8] = "...";` declared inside a switch case copies its `adr` constant there;
  declared at function scope the copy moves to the prologue.
- `a[0] = a[1] = 0;` stores a[1] first.
- In one expression pair on a 5-bit value, `/10` used `movs #0xcd; muls; lsrs #11` while `%10`
  still used `umull` with 0xCCCCCCCD.
- Sometimes the original did use pointer locals (`s = &g_A; o = &g_B;` set early): the bases are
  loaded before an unrelated test and fields are reloaded at each use. Direct reads keep fields in
  registers instead. Read globals directly first, but try pointer locals when the original reloads.
- A signed product stored to a `u16` field gets `and.w ..., #0xffff`; to a `short` field it doesn't.
- Compare operand order tells a local from a global: with a global field, `j < g.len` and
  `g.len > j` both give `cmp rLen, rJ`; with a u8 local the operands follow the source.
- `n = g.len = f();` gives `uxtb; strb` with one register; `n = f(); g.len = n;` allocates
  differently.
- `w = v * 102; w = w / 100;` multiplies before loading the divide literal; one expression loads
  the literal first.

## Which compiler build

armcc 5's point releases compile nearly all code alike, so one matched function says little about
the build. A whole firmware's matched files, run under every MDK release from 4.60 (armcc 5.02)
to 5.36 (5.06u7) with `tools/armbuilds.py`, settled it:

- 5.05u1 to 5.06u2 matched every file. 5.05 and earlier broke about ten files (different code,
  or a link that failed), and 5.03 and 5.02 shortened a large switch-driven function by 0x30
  bytes. 5.06u3 and every later update built one library function 2 bytes longer.
- The one function that told them apart rebuilt a constant from a register known to hold a
  smaller value (`lsls r1,r2,#5` for 0x100 with r2 = 8, after a switch whose cases share their
  compares). 5.06u2 and earlier keep that knowledge past compares whose flags serve two branches;
  5.06u3 onwards drop it and load the constant (`mov.w r1,#0x100`, 2 bytes longer). No option
  changed it (-O0 to -O3, -Otime or not, -g, --c99, --no_inline and others).
- So: build every merged file under every release you can get, and look for the files that only
  some releases match. A function nothing matches after long work is worth trying under the
  neighbouring releases before more hours on its source; here it was the compiler.
- Several MDK releases carry the same armcc build (5.21 and 5.21a; 5.22 and 5.23; 5.24 and 5.24a;
  5.17, 5.18 and 5.18a; 5.13 and 5.14). `armcc --vsn` and the object's `.comment` ("Component: ARM
  Compiler 5.06 update 2 (build 183)") name the build.
- MDK 4's compilers take their licence from a `TOOLS.INI` at the toolchain root, not from
  `ARM_TOOL_VARIANT`; a minimal one gives the Lite licence.
- MicroLIB members are not compiled with the project: `_printf_core` from MDK 5.14, 5.20 and 5.25
  was the image's, 5.36's differed throughout. Its release is evidence for the MDK release too.
- The image's code also chose the library: a peripheral library's functions compiled from two
  vendor releases were identical except a few (a UART set-up, a clock query), which matched only
  the older release. Compile every release of a vendor library, not just the latest.

## What a file's contents decide

Which functions and globals share a source file changes the code, so it is evidence of the
original files, and a source file can't be split or joined freely afterwards.

- A callee defined in the same file, before or after its caller, lets armcc keep r1-r3 live across
  the call and drop the stack-alignment push (above). armcc generates a file's callees first: in
  `void init(void) { other(); cfg(); }` with `cfg` defined later, `cfg` is the object's first
  section. A test with the callee before and after its caller gave the same bytes.
- A caller masks a callee's result by the prototype it saw, not the callee's definition. With
  `u8 changed; changed |= check();` and `check` defined in the file returning `u32`, armcc emits
  `orrs r4,r0; uxtb`; with only a `u8` prototype in view, a bare `orrs r4,r0`. A missing `uxtb`
  shows the callee was in another file, compiled against a narrower prototype.
- A file's own globals are reached from one another's addresses (`add.w ip,r5,#8` for the global
  defined 8 bytes after another); externs each get a literal. So a function that uses two globals
  its file defines must stay with that data (data is laid out per file).
- A second pool entry for the same address means two variables: with one struct over a block of
  RAM, armcc reused the struct's base register for a flag byte at its start; with the flag and
  the halfwords after it as separate globals defined in the file, it loaded the same literal again
  (`ldr.w ip,[pc,#..]`) for the flag, as the image did. armcc shares the pool word through
  the file's data anchor but doesn't treat two variables as one address.
- Options are per file: a file's `/* opts: */` line holds what differs from the defaults
  (`--no_data_reorder`, `--bss_threshold=N`). Files joined into one need options that suit both.
- `--split_sections` does not split embedded assembler: every `__asm` function of a file goes into
  one `.emb_text` section. One more reason for C everywhere.

## Inline helpers, getters and register allocation

armcc allocates an inlined function's code after its caller's own. A `static __inline` helper the
original source had moves registers anywhere in the function without changing one instruction,
and no edit to the caller's own statements reproduces that. When a register swap resists every
spelling of the statements around it, the source probably called a helper there.

- A loop calling two functions six times, then a store to a peripheral register: written in
  place, armcc put the register's address in r1 and the value in r0, the image the other way
  round. The loop as `static __inline void repeat6(void)` gave the image's registers; the loop's
  own bytes were the same either way. Moving only the store into a helper did nothing.
- A sign clamp (`if (!(v > 0 && m.b1)) { if (v < 0 && m.b0) v = -v; else v = 0; }`) as
  `static __inline int clamp(int v) {...; return v;}`, together with one local widened to `u32`,
  fixed a two-register swap; neither change alone did. Found by a search.
- Four of ten single-byte flag reads written through one-line getters
  (`static __inline u8 get_f21(void) { return g_state.f21; }`) swapped ip and r7 as the image had
  them. No subset of the four worked, and each partial set scored worse than no getters at all:
  one change at a time would never have found it. 32 of 59,049 combinations matched.
- Two copies of a 10-byte text into a buffer through one `copy_text(dst)` helper decided which of
  two hoisted constants got r4. The natural helper; both copies must use it.
- A getter for a struct pointer (`view()->flags |= 0x80`) fixed three registers but left a
  temporary in r1 where the image had r0: the getter's result is a value of its own, allocated
  before armcc forwards the base into the address. A getter that fixes what you look at can still
  leave this trace.
- `tools/armrepeats.py` finds instruction runs the image repeats at several places: inlined
  helpers and macros of the original. A function holding such a run probably called the helper.

## Dead code that steers allocation

Some code armcc deletes still takes part in register allocation; some is deleted before it.

- A test is deleted only when both outcomes reach the same code: `if (c) continue;` where the body
  falls through to the increment anyway, `if (c) break;` just before a case's own `break`, `else if
  (c) return;` at the very end of a void function. Inside a loop, or before later statements, the
  same test is kept and costs a compare and branch.
- A deleted test on locals still moves registers (in one loop any dead use of i, j or n moved a
  hoisted constant from r6 to ip); a deleted test on globals alone changed nothing.
- A dead arm can keep a value live: in a chain of `if ((d -= K) == 0)` tests on `d = (u32)p -
  base`, adding `else if (d == (u32)p) return;` (deleted, no code) kept `p` in r0, so the running
  difference stayed in r1 and the last test became `subs.w r1,r1,#K` as in the image, not `cmp`.
- An unread comparison of pointers survives until register allocation, counts as a use of what it
  reads, and is deleted after it: `{ u32 z; z = (u32)(&tbl[i + 1] != 0); }` at the end of a loop
  body moved a hoisted array base from lr to r7, with no code. `if (E) {}`, `(void)(E);`, scalar
  comparisons and plain pointer assignments are deleted before allocation and change nothing;
  only relational or equality comparisons of pointers survived (70 of 6912 statement forms tried).
- Unread pointer locals hold registers through allocation the same way:

      { T *q0 = (T *)&g_a; T *q1 = &tbl[1]; T *q2 = (T *)&g_b;
        v = tbl[0].f24; v &= ~0x41; w = tbl[0].f26;
        z = (u32)(q0 != tbl); z = (u32)(q1 != tbl); z = (u32)(q2 != tbl); }
      v &= ~0xe000; w &= 4;

  armcc gives short values the first free register in its order (ip, then r6, then fp); the three
  pointers, live from before the load to the comparisons, take ip and r6, so the half-masked `v`
  went to fp as in the image. They and the comparisons are gone after allocation. Found by a
  placement search: 615 variants, 3 matches, all with three pointers opened before the load.
- Dead, duplicate and redundant stores are deleted before allocation and change nothing.

## Hoisted values and their registers

- armcc hoists loads and constants that sibling regions share into the entry block. Which register
  a hoisted value gets follows how it is used: references inside a loop count far more than those
  outside, and a value whose range crosses a call ranks among the call-crossing values (r4 up).
- A hoisted address used in five loops was coloured after every other value (lr) until it had
  one real use in one loop's latch block (the block with `++i` and the test); then it was fourth
  (r7), as in the image. One use anywhere else, of 150 positions, did not do it; two uses in two
  of a few straight-line blocks did. The unread comparison above is such a use with no code.
- In a small copy, which of two hoisted values was coloured first flipped only with nine stores
  inside the loop, never with sixteen outside it.
- The order of hoisted values in the entry block is not their source order; it moved with the
  block structure of either region, and once with a dead store armcc later deleted.
- A temporary far away decides a loop's registers: `{ u32 x = g.f18; out.buf[4] = x; }` for
  `out.buf[4] = g.f18;` in one case of a switch, together with a declaration order
  (`u8 i; u8 n; u8 j; char s[8]; u32 v; u32 w; u16 t;`), fixed a swap in another case. Another
  function's swap 0x50 bytes before the change was fixed by `u32 max` for `u16 max` and an `int`
  temporary for a copied field. Neither change alone worked in either.
- A field read once and used twice wants a local: `u16 lim = g.f14;` for two reads gave the
  image's registers (the field in r1, temporaries in r2).
- Reading fields in a condition decides a helper's registers: cached locals in any order gave
  `ldrb.w ip; ldrh.w r3; add.w r4,ip,r3` (0x6A bytes); `if ((p->w & 0xff) + p->count <= n && ...)`
  gave the image's `ldrb.w r3; ldrh.w r4; add.w ip,r3,r4` (0x66). Sibling functions already
  matched are the template for the style.

## Literal pools and scheduling

- The pool is in the order the source created the constants, not the instruction order. For
  `buf[idx] = val`, armcc creates the value, then the index, then the array, whatever the
  spelling (`*(buf + idx)`, `idx[buf]`, `&buf[idx]`, casts, a pointer taken first, volatile,
  2-D arrays, extern or static: over 10,000 variants, tabulated as `tools/armbrute.py --table
  --pool` does). The scheduler then moves loads, so the order shows in the pool when the index
  chain is longest and in the loads when the chains tie.
- A side effect in the array operand creates the array first: `(p = buf)[k = idx] = val;` and
  `(c = 0, buf)[idx] = val;` (the dead assignments compile to nothing) give pool value, array,
  index, with the index still loaded first. A store through a pointer assigned in the expression
  hides the array from alias analysis, so armcc reloads what is read after it: use the index
  local `k` afterwards; the comma form keeps the store visibly to `buf`. Three functions of one
  image needed this.
- A pointer local to a global moved a mid-function pool: with `Info *p = &g_info;` and `p->f` at
  every use the instructions matched but the pool landed 16 bytes early (behind a padding
  halfword); `g_info.f` directly put it where the image had it. When a pool lands a block early or
  late over matching code, try the other spelling of the base.
- Loads from a `__packed` struct are scheduled ahead of a hoisted constant: the same entry block
  with the struct declared plainly put `movs r4,#0` before two `ldr`s; declared `__packed` (the
  global was at an odd address), after them, as the image had. The loads were the same
  instructions; armcc rates a possibly unaligned load as slower and starts it earlier.
- A mask written as one expression is never split: `v & 0x1fbe` became two `bic`s with nothing
  between them in every test. The image had a load between its two BICs, so the source applied
  the mask in two steps (`v &= ~0x41; w = ...; v &= ~0xe000;`).
- armcc rebuilds a constant from a register it knows (`lsls r1,r2,#5` for 0x100 with r2 = 8;
  `lsls r1,r1,#14` for 0x10000 after `cmp r1,#4; beq`) only on a straight run into the use; a
  compare whose flags serve two branches on the way ends the knowledge (in 5.06u3 and later). A
  switch gave `mov.w` every time. With a pointer local, the knowledge reaches an MMIO address:
  `volatile u32 *reg = &PERIPH->CLR; *reg = 0x200;` stored `asrs r1,r0,#21`, 0x200 rebuilt from
  the register's address (any 0x400xxxxx shifted right 21 bits).
- `tools/armvariant.py pool SRC FUNC` prints a function's loads in code order and its pool.

## Control flow

- Nesting decides which arm goes out of line: `if (v >= K) { if (a > w) w++; else { w = 0;
  f = 1; } } else w = 0;` put `else w = 0` out of line behind `blt`; early returns or an
  `if (v < K) ... else if ...` chain kept it inline behind `bge`.
- A flat chain and nested ifs with the same layout swap value registers:
  `if (p->n == 1) { if (v == 0x2200) {...} else p->err = 3; } else p->err = 2;` against
  `if (p->n != 1) p->err = 2; else if (v == 0x2200) {...} else p->err = 3;`.
- A sparse switch over pointer values (`switch ((u32)p)` with eight addresses, also five)
  compiles to a binary search with a literal pool, never a compare chain.
- A function that starts with `nop.w` (`f3af 8000`) before its prologue is two functions: a
  4-byte one whose whole body is a tail call to the next (`void A(void) { B(); }` is `b.w B`),
  which armlink's default `--branchnop` turned into `nop.w`, and B straight after it. Split the
  map row 4 bytes in. Inline `__asm { NOP.W }` narrows to `bf00` and no intrinsic puts a NOP
  before a push, so nothing else makes those bytes.
- `__attribute__((naked))` is refused (#1207-D).

## Types and packing

- Type-based aliasing decides reloads both ways: one function's code through casts of a file's
  `u16` arrays differed by 514 bytes from the same code through the struct types another file
  used for the same globals. When joining files, keep each function's view of the data.
- Accesses through the packed fields of a global at an odd address compile longer than the same
  accesses through an aligned struct cast from the block's aligned base
  (`(*(Block *)g_blk).f`); the image showed which the original did.
- A `__packed` struct of `u16` bitfields still updates one byte when the fields fit in it
  (`bic r2,r3,#1; strb r2,[r0]`).
- An interpolation slope declared `u16` drops its own narrowing but swaps the slope and the delta
  between r4 and r5; declared `int`, the delta took r4 as the image had.
- Two separately defined `u16[50]` arrays are reached from one base (`add.w r4,ip,#100`); one
  `u16[2][50]` with a pointer gave other base registers and an extra index reload. Map rows for
  such data split at the second array.

## Data and options

- Zero-valued RW objects of 10 to 55 bytes mean the file had a larger `--bss_threshold` than the
  default 8. No single value fitted a whole firmware (20-byte zero structs were in .bss there), so
  it is per file: 64 in three files.
- At -O1 with --split_sections, each global no function in the file references gets a section of
  its own. A data-only file built at `-O0` keeps one section per kind, in declaration order.
- A section holding a pointer is 4-aligned, so it can't share another file's halfword-aligned
  `.data` run.

## Searching for the source

Most functions match by hand in a few builds. The last few in a firmware are register swaps and
load orders that no single edit moves; these took searches over combinations, not more hand edits.

- The tools: `armvariant.py` scores one source against the image without linking (a second or
  two under Wine; a batch of 40 to 150 variants in one armcc call costs little more);
  `armpermute.py` mutates a function at random and keeps what scores no worse;
  `armoutline.py` writes templates of a function's statement runs as inline helpers, or of one
  statement at every point of its body; `armbrute.py` runs a template's combinations exhaustively,
  at random or by coordinate descent, or tabulates what armcc makes of each spelling;
  `armreduce.py` cuts a result down to the edits that matter. Rerun match.py on any hit.
- The score masks registers when asked (`--regs`): first get the structure (the same instructions
  in the same order), then search the allocation. Structural differences are source shape;
  register differences are helpers, temporaries, declaration order, types and dead uses (above).
- Search the combinations, not one change at a time: the matches above needed two to four
  changes together, and for one function every partial set scored worse than none.
- armcc compiles its command line's files in order and stops at the first with an error, writing
  nothing for the files after it. A batch must restart after each failing file, or every variant
  after a bad one goes unscored silently: searches made before this was fixed covered less than
  their counts said. Count the variants that did not compile.
- Keep the template's variants compilable: a type choice the file has no typedef for failed 4810
  of 8320 variants of one template, and helpers not given a struct local 555 of 593 of another.
- Under Wine, start a persistent server in each prefix (`wineserver -p`) and give each parallel
  job its own prefix; otherwise every call boots Wine (390 variants: 3 s with the servers up,
  30 s without) and one shared server becomes the bottleneck.
- armcc's front end keeps EDG's debug options: `--db=-dump_il` prints a function's intermediate
  language with source lines and columns, `--db=-dump_stmts` the statement tree,
  `--db=-dump_lifetimes` object lifetimes. The back end's debug output (register allocation,
  scheduling, literal pools) is compiled out of the release build; no option reaches it.
