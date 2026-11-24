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
- Before blaming a source, compile a small control under every installed release: a matched
  sibling, or a few lines with the same idiom. When a tool runs under another release than the
  gate's (a trace, a search), check first that the function's listing is the same under both.
  One allocator trace ran under MDK 5.21 for a firmware built with 5.20; the traced function's
  listing was the same under both, so the trace stood for the original compiler.
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

Two more release tells, from an `-O0` firmware (each a few instructions, but every file built by
the wrong release fails on them):

- An assignment whose value is tested at once (`if ((r = f()) == 0)`, `while (--n)`) keeps the
  value with `movs r4,r0` under armcc 5.05 (MDK 5.11 to 5.15) and with `subs r4,r0,#0` under 5.06
  (MDK 5.16 and later). Checked on a two-line probe under 5.05u1, 5.05u2, 5.06, 5.06u2 and 5.06u3.
  A `subs rd,rn,#0` anywhere in an image rules out 5.05.
- A switch's range check: MDK 5.11 to 5.20 (up to 5.06u2) reuse the flags of a compare just
  before it; 5.21 (5.06u3) and later emit a second `cmp`. This is likely the same change as the
  constant knowledge above (5.06u3 drops what it knew past compares whose flags serve two
  branches). Two files of one image matched only under 5.21 or later.
- The C library dates the link, not the compile: the standard library's `exit` is 12 bytes in
  MDK 5.11 to 5.15 (no push, ending in a `bl` to `__rt_exit`) and 18 bytes from 5.16 (it pushes
  `{r4,lr}`, pops and branches). The compile and the library can come from different releases;
  pin each separately.
- A library member found by bytes inside the application's code (a short function whose bytes,
  with call targets masked, look like `abort` or `__alloca`) is a coincidence, not evidence of the
  release: the real members sit together where armlink put the library.

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
- `static __inline` is only a hint. armcc at `-O1` inlined the small helpers above, but in
  another firmware it kept two larger helpers (a loop with a float expression, and a float
  conversion) as separate functions in every build. `static __forceinline` inlined them every
  time. A helper that was not inlined shows as a `bl` and wrong sizes and entries, so check the
  listing before reading its registers.
- Moving a whole block into a helper that returns its result is the strongest form of this. A
  function with two sampling loops had the second loop's extrema coloured ahead of the first
  loop's sum and a divisor, and no spelling of the statements moved them. The first loop and the
  computation that used its sum, moved into `static __forceinline float read_first(int *sum)`
  and called as `first = read_first(&sum1);`, gave both loops the image's registers (64
  differing instructions to 34). The same split of the second loop changed nothing, and so did
  helpers for the expressions alone: the block whose values must be coloured later is the one to
  move. A second small helper (`to_volts(adc)`, used in the four arms of the other result) then
  made a reference value a later value of the caller and gave the image's spill: exact. In
  another function the first step of a running maximum, as a `static __forceinline` helper that
  returned it, put the result, zero and the first input in the image's registers (117 to 74).
- An address returned by an inline helper is a value of its own where a pointer local is not.
  A register write handler stored one case through `p = &flags[i].word.value; *p = val;`: right
  size, but the pointer coalesced into a long-lived r1 web (another case's row pointer, 13
  moves), so the array base took r2 where the image had r1. `*flag_word(i) = val;` with
  `static __inline ushort *flag_word(uint i) { return &flags[i].word.value; }` gave the image's
  separate `add.w r2, r1, r3, lsl #1; strh r0, [r2]` and its registers: exact (with an existing
  probe moved one statement up, which removed a `uxth`).
- Inside such a helper, the shape of the return still counts: one returned conditional
  expression made one `mov` at the join, where the image had one in each arm; four `return`
  statements, one per arm, gave each arm its own move.
- A getter adds a reference where the replay asks for one. A settings restore stored two
  thresholds as plain globals and two through the same region's base address; in natural order
  the base and the constant -130 swapped r0 and r1, and the replay said the base needed one
  more weighted reference (65 to 66 or 97). `protection_view()->discharge_overcurrent = -130;`
  with `static __inline Thresholds *protection_view(void) { return (Thresholds *)base; }`
  gave it: exact. When the replay names a node that needs a reference and the source has no
  natural extra use, a getter for that address is the vendor-shaped way to add one. The same
  works for a value: a capacity integrator spilled the wrong one of two hoisted products,
  `rated * 10` and `rated * 8` (both refs 99, tied on node number); the replay wanted the x8
  product at exactly 100. Routing one of its two uses through
  `static __forceinline uint rated_x8(void) { return rated * 8; }` gave the image's spill:
  exact. Both uses through the helper, or only the other one, did not.
- A copy-paste formula armcc folds late still leaves its pieces behind. Every case of a reply
  builder set `frames = (length - 1) / 8 + 1;`, but the error case had `frames = 1;` in our
  draft. With the formula (`length` just set to 5), armcc hoisted the 4 and the 0 it makes to
  the entry block and only then folded the result to 1: the image's `movs r3, #4` and a zero
  copy at entry, two bytes and a pool island in place. A constant the image creates at entry
  with no use outside one case can be the remains of such a folded expression.
- armcc drops a redundant final test (`else if (c >= 300) r = 0xff;` with `r` already 0xff on
  that path) unless something between the assignment and the test hides the value from it; a
  loop anywhere before the chain does. A classifier whose image keeps the test and has 0xff in
  r0 from entry still resists every spelling tried (initialisers, types, no-op statements,
  barriers, helpers), while its sibling with loops matches with the initialiser.

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
- Comparisons of a global's address with 0 are folded before allocation: about 1,100 builds with
  one at every statement of a function changed nothing. Comparisons of two different globals'
  addresses, or of a global's address with an element's address, survive to allocation and then
  vanish. In a loop whose count address was used once before the loop, and so was coloured
  after every value used inside it, one such comparison in the loop body made the address a
  loop value and gave the loop the image's registers:

      for (; i < g_count; i = i + 1 & 0xff) {
          g_out[i] = (short)((uint)g_in[i] / 50);
          { uint check; check = (uint)((const void *)&g_count != (const void *)&g_out[i + 1]); }
      }

- In another firmware an unread comparison of a global byte with a local, assigned to a
  block-scoped local (`{ uchar t; t = (g_peak_in == ret); }`), also survived and held both
  values through allocation, with `==` or `!=` and `uchar` or `uint` alike, while
  `(void)(g_peak_in != ret)`, an empty `if`, an unread plain read of the global and a copy of the
  local did not. So "scalar comparisons are deleted" holds for a bare statement, not always for a
  comparison stored in a local. Try both.
- In short, what has survived to allocation: unread comparisons of pointers or addresses, unread
  pointer locals, an unread comparison stored in a local, and unread uses of a parameter. What
  is deleted before it: `(void)E`, `if (E) {}`, plain reads, plain copies, comparisons of a
  global's address with 0, and dead or duplicate stores. Only the first kind can move a register.
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
- A zero argument the image passes from a register copied at an earlier store (`mov r3, r4`
  before a float store, then `str r3, [sp]` for a call's fifth argument) can be a variable the
  source had just cleared, not a literal. `log(2, 0xe, 0, 0, 0)` built fresh zeros for the call;
  `log(2, 0xe, 0, g_count, g_count)` after `g_count = 0;` gave the image's copy and registers
  through the float store, the two clears and the call.
- The order of a call's argument loads follows how the first argument is read. `f(idx, 100, 10)`
  with `extern uchar idx` built `movs r2,#10; movs r1,#100; ldrb r0,[idx]`; the image loaded
  `idx` first. `*(volatile uchar *)&idx` and `(uchar)*(ushort *)&idx` (a wider view, as another
  file of the same image declared it) both gave the image's order; declaring `idx` volatile for
  the whole file broke another function that had the plain order.
- A value read at the top and used in three branches is read by the image in the branches: with
  `prev = s.mask;` as the second statement, armcc loaded it before the hoisted loads of the
  branches, with `ldrh.w` through the struct's base register. Assigning `prev = s.mask;` just
  before each of its uses (where nothing had changed `s.mask` yet) let armcc hoist one load after
  the others, through the copied low base (`ldrh r6,[r0,#4]`, 2 bytes shorter): the function's
  size and entry block then matched.
- armcc shares one register for a constant that several stores use, and makes it where the
  constant first appears, so a dead store moves it. In a CAN handler the image set
  `movs r3, #0` at entry and used r3 for six zero stores in one branch; the build made the zero at
  the top of that branch. `id.word = 0;` just before `id.word = msg->ExtId;` (dead: overwritten
  at once) gave the image's code.
- A constant held in a narrow local is a value of its own: armcc folds an `int` local that only
  holds a constant, but not a `uchar` one used in several places. The image of a sender set
  `movs r2, #7` before an inlined `memcpy(data, src, 7)` and kept it for the call after it;
  `uchar length; length = 7;` before the copy, with `send(id, data, length)`, matched. In a task
  loop, `enable = 1; flag = enable;` for one of six stores of 1 took the score from 94 to 70.
  `tools/armidioms.py literals` writes these variants for every literal of a function, and
  `tools/armidioms.py zero` the dead stores.
- A field read once and used twice wants a local: `u16 lim = g.f14;` for two reads gave the
  image's registers (the field in r1, temporaries in r2).
- A copy merges with its source when the source dies at the copy. In a running maximum
  (`ret = g_a; if (ret < g_b) ret = g_b; ...`) armcc merged `ret` with the first input's
  register, where the image copied it out (`mov r4, sl`) and kept the input in its own. A later
  unread use of the input (the stored comparison above, in a later branch that also read `ret`)
  kept it live past the copy, and the function matched. A `mov` the image has and the build
  lacks usually means the source of that copy was still live after it in the original.
- Reading fields in a condition decides a helper's registers: cached locals in any order gave
  `ldrb.w ip; ldrh.w r3; add.w r4,ip,r3` (0x6A bytes); `if ((p->w & 0xff) + p->count <= n && ...)`
  gave the image's `ldrb.w r3; ldrh.w r4; add.w ip,r3,r4` (0x66). Sibling functions already
  matched are the template for the style.

## How the register allocator chooses

This is armcc 5.06's register allocator at `-O1` (update 3), as I came to know it while matching
the largest function of a firmware. A replay written from these rules gave the compiler's own
choice at every step for that function (about 9,000 values, two passes, 41 spills), so the
rules can be used to reason about a swap. They also explain why so many swaps resist local
edits. No option prints any of this (`--stats_file` gives only counts), but the release build
holds a trace of it that can be turned on from outside the compiler ("Tracing the register
allocator", below). On another firmware, built with `-Otime`, the trace confirmed these rules
with one difference in the colour order.

- It colours a graph in the Chaitin and Briggs way. Each value is a node; two nodes clash when
  they are live at the same time; a copy between two values is a move. There are 14 registers.
- Each node is in one of four work lists, by its degree against those 14: extra low (colourable
  even when its move clashes, below, are counted), low (colourable when only its real clashes
  are counted), high (may be spilled) and high non-spill (may not be). The real registers are
  never removed.
- Simplification removes one node at a time from the first list that is not empty, in the order
  extra low, low, high, high non-spill. From the extra-low and low lists it takes the node with
  the fewest references (weighted, so a reference inside a loop counts more), then the fewest
  moves, then the smallest degree counting move clashes, then the smallest degree, then the
  highest node number. The high list takes the fewest references, the fewest moves, then the
  largest degree. The high non-spill list takes the smallest degree first.
- The lists are indexed heaps whose keys are read when they are compared. A key that changes (a
  degree falls, a move or a move clash is added) does not re-sort the heap; only a change of
  list does. So the order of removal depends on the heap's history as well as on the keys, and
  no ranking of a function's values made up front predicts it.
- Colouring runs in the reverse order of removal: the node removed last is coloured first. Among
  nodes in the same list, the value with the most references is coloured first; on a tie of
  every other key, the one with the lower node number.
- Node numbers follow the order in which the compiler makes its intermediate code, which is
  mostly source order. The values of inlined code are made after the caller's own, so they have
  higher numbers and, on a tie, are coloured after the caller's values (above, "Inline helpers").
  Parameters seem to be numbered after the body's values.
- The colour: first a vote among the node's copy partners that already have a colour. Each
  partner whose register is still free votes for it, twice for a direct copy; the most votes
  win, and a tie goes to the lower register. With no vote, the node takes the first free
  register in the order r0-r7, ip, r8-r11, lr, from a preferred set when it can: the free
  registers among r0-r3 and ip, plus the registers this pass has already handed out this way.
  The preferred set is used only when the node has fewer uncoloured neighbours than the set has
  members. In practice it matters when r0-r3 are all taken: the value then goes to a callee-saved
  register already in use, and to ip before a callee-saved register that is not.
- With `-Otime` the order with no vote is r0-r3, ip, r4-r11, lr, and the preferred set plays no
  part. ip then comes before every callee-saved register, even one already in use; without
  `-Otime` a callee-saved register already handed out comes before ip. A replay with the rule for
  builds without `-Otime` missed 41 to 46 of 211 memory accesses of one `-Otime` function. With
  this rule, 869 traced compiles of that function (5.06u3) replayed with every removal, every
  heap and every colour the same.
- The preferred set above came from replaying code, not a trace. On a later firmware (5.06u3,
  `-O1` without `-Otime`), the traces fit only the plain first-free order r0-r7, ip, r8-r11, lr:
  every one of 4,868 colour steps in one file, and every function I traced in five others
  (`armreplay.py --plain`). Try both on a new firmware and keep the one its traces agree with.
- Move clashes: when a node is removed, each copy partner still in the graph takes on the node's
  clashes as move clashes (only while the node's clashes times its moves are under 1,000). When a
  node is coloured, the registers of its move-clash neighbours are struck out first, unless that
  leaves none. This is how a value avoids a register that is free where it lives: its copy
  partner clashes with that register somewhere else.
- Coalescing happens only when a node is removed, and only when the node has exactly one move,
  its partner is still low or extra low, and every clash of the node is already a clash of the
  partner. That last test is stricter than George's. The merged node takes its partner's colour.
  Every other copy is left to the vote.
- There are two passes. The first colours and spills. The second starts again from the whole
  graph, removes the values the first pass spilled first, and gives the final registers. A
  function that spills nothing is done in one pass.
- The `register` keyword changes nothing: the reference bonus it would give is behind an
  internal option that is off. `register` on each local of a large function, and on all of them,
  gave the same bytes.

What follows for matching:

- Two short temporaries with one definition and one use each, and no copies (an address and a
  value for one store, say), are decided by degree and then by node number: by which of them the
  compiler made first. Respelling the statement rarely changes that. Changing what is live
  around them can, as can making one of them in an inlined helper.
- A value used inside a loop has a large weighted count and is coloured before the values used
  only outside it. When the image gives a value used before a loop one of the loop's low
  registers, the original used it inside the loop (above, "Dead code that steers allocation").
- A copy is merged only if its source dies there (the subset test). Two values in two registers
  joined by a `mov` in the image mean both were live at the copy.
- In a large function an edit anywhere moves registers everywhere: each change of references,
  moves, degree or numbering changes the removal order of many nodes and the votes after them.
  There, a swap that no local edit moves is usually decided by the function's context, not by
  the statement (skills/match-file, "When the search stalls").
- A tie can be decided by the heap's history. In one firmware a function had four identical
  blocks, each a `sprintf` and an 8-byte copy loop. The vendor's next release added a few
  statements at the end of the function; after that, three of the loops compiled as before and
  one had two registers swapped. In each loop the loaded byte and the address `buf + i` tie on
  the first keys (257 weighted references, no moves). Whichever is removed first is coloured
  last and gets ip; the other gets r3. In my sources the address left first, as its smaller
  degree says. In the image's source the byte left first although its key was larger at that
  moment: positions left in the heap by earlier removals elsewhere in the function decided it.
  No edit to that loop could move it, and every edit elsewhere that fixed it broke another case.
  About 100,000 source variants (helpers, getters, dead uses, block temporaries, declaration
  orders, every MDK from 5.14 to 5.36, per-file options) stopped at three instructions off. A
  trace of the allocator showed the cause (below).
- Declaration order only renumbers. Changing the order of a function's locals gives the same
  graph with the variables' homes renumbered: the homes are numbered one after another in
  declaration order, and nothing else changes. I checked it against a real trace of another
  order: all 807 coloured nodes came out as predicted. So every order can be scored in a replay
  without compiling; the 720 orders of six locals take about two seconds
  (`tools/armreplay.py sweep`). For the function above I traced 864 structural forms and
  replayed all 720 orders of each. One form had 30 orders that scored 0 in the replay, and two
  of them, built for real, matched. The source that matched has no dead code; only its
  declaration order carries a `match:` comment.

## Tracing the register allocator

armcc 5's back end keeps a trace of its register allocator in the release build. It prints when
the back end's debug level is 5 or more, and no option sets that level. I turned it on by
starting armcc under winedbg and, before the compile began, writing to the running process's
memory so that the level the back end reads is 5. That is a write to one process, in my own Wine
prefix: nothing on disk is read, copied or changed, and the next compile is the stock compiler
again. The project's owner approved it; whether you may do it is a question for your compiler's
licence. The addresses involved belong to one armcc.exe build, and Exhume ships none:
`tools/armtrace.py run` reads the debugger commands from a local, git-ignored
`armtrace.local.toml` (profiles/armcc5/armtrace.local.example.toml).

The trace goes where armcc's diagnostics go (`--errors=FILE`). For one graph colouring it holds:

- the first clash graph, as a Graphviz dump: clashes drawn solid, copies dotted, width 5 for a
  direct copy;
- each node's starting status (precoloured, extra low, low, high, high non-spill);
- for each removal, the four work lists in heap order, with an entry that sorts after one of its
  children marked `x`; then the node with its weighted references, its clash, move-clash and
  move sets, the status changes, the move clashes it adds, and the coalescing test;
- each colouring, with the free registers before and after the move clashes, the votes and the
  colour; the spills; a second pass when there are spills; and the final graph.

A function of 1,338 nodes gave about 57,000 lines. Trace one function at a time, in a source that
holds only it and the declarations it needs: a whole file gives a colouring for every function.
`armtrace.py parse` lists what a trace holds.

`tools/armreplay.py check` replays the traced graph with the rules above and compares every
removal, every heap, every colour and every spill with the trace. On 869 traced compiles of one
`-Otime` function the replay agreed at every step. None of those compiles spilled, so the
replay's second pass and its rule for builds without `-Otime` are written from the earlier model
(above) and have not yet been checked against a trace. Once the replay agrees, it can stand for
the compiler on that graph:

- `armreplay.py tie TRACE A B` follows two nodes (two values the image has the other way round)
  through the removals and says why the first one left first: its keys, a tie broken by node
  number, or a stale heap position. A stale position means no edit to their own statements will
  move them.
- `armreplay.py sweep` scores every declaration order in the replay, against the trace's own
  colours with two nodes swapped or against a list of wanted registers. It needs each variable's
  home nodes (below).
- Every other change of the source needs a real compile and its own trace.

When to trace: when a function is down to a few swapped registers, the instructions agree
(`armvariant.py score --regs` is 0), and edits move the swap somewhere else each time. A replay
checked against the trace scores thousands of candidates a second and shows which tie to break;
brute force over the source does not.

What the trace does not give, and what it must not be taken for:

- It starts at the allocator. It does not say what graph a changed source will make. A replay of
  a captured graph that agrees with the compiler is an observed replay, not a prediction from
  source: in one project a model of the front end could predict the graph for 204 of 10,000
  generated sources, and the rest needed real compiles.
- A passing allocation found by changing the model is not a source. In one firmware, swapping
  two nodes' numbers in the replay fixed the two registers wanted and moved exactly two removals,
  and a complete allocation with every register right existed in the model. That allocation
  departed from the real heap's next choice thousands of times, and no C was found that made
  armcc produce it. Use a change to the model to learn what must change, then look for C that
  changes it, and build every candidate.
- Two sources can give a node the same static clashes and moves and still give it different
  registers: simplification adds move and move-clash relations as it goes, and select sees
  different neighbours already coloured. Compare the traces, not the starting graphs.
- A difference in one event is not a cause until it is tested: a coalescing that one source had
  and another lacked, suppressed in the model, changed no register.
- The variables' home nodes, and which node is which operand of which instruction, come from the
  compiler's intermediate code. I captured it the same way, at breakpoints in the running
  compiler; that needs the layouts of the compiler's own structures, which are build-specific
  too and are not in Exhume. Without them, identify nodes from the trace: their references and
  degree, and the registers the trace gives them against the listing's.

### The trace with the Linux compiler

ARM Compiler 5 for Linux is the same release built for 32-bit x86 Linux, and its trace is the
same. I traced one 64 KB unit (17 colourings, the largest 5,606 nodes) with the Windows build
under Wine and with the Linux build of the same update (5.06u3, build 300): the two traces had
the same 873,304 lines, and `armtrace.parse` gave equal results. The only differences were the
file name in the warnings and the way the two C libraries print a float in the graph dump's
colour attribute (`4.29497e+009` against `4.29497e+09`), which the parser does not read. The
objects were the same.

How I found the place to write, in words. The trace's format strings are in the program's read-
only data; the code that passes each one to the print routine is a few instructions after a call
through an object's table of methods, whose result is compared with a small number before the
print. All the allocator's tests call the same method. Under a debugger, stopped at one of those
calls, the method turned out to make sure of a singleton object and then to jump, as its last
instruction, to a two-instruction function that returns 0: the release build's debug level. The
Windows build has the same method with the getter inlined (its last two instructions return 0),
and that is where the Windows commands write. That tiny getter is shared by dozens of other
callers in the Linux build, so I do not write there: I replace the method's final jump, which only
the allocator's tests reach. Build 750 (5.06u6) has the same method at another address.

How it is switched on (armtrace.py `--runner linux`): an LD_PRELOAD library
(`tools/armtrace_shim/armtrace_shim.c`, built with `cc -m32` on first use; it needs no 32-bit C
library) runs before the compiler's `main`. It checks that the five bytes at the local file's
`hook` address are the ones recorded, makes that page writable, writes a jump to its own getter,
and puts the page back. Nothing on disk changes. Its getter answers a level per call site (the
return address), so the trace can hold only some of its parts, and it can count the calls from
the site that starts a colouring and answer 0 except in the K-th, so only one function is traced.
`armtrace.py survey` lists the call sites and what each prints, for the local file. With gdb in
place of the library (`--method gdb`, writing `push imm8; pop eax; ret` there at the first
instruction) the trace is the same and each compile costs about 80 ms more.

Checks, on the 64 KB unit with both builds: with the library loaded and the level 0, the object
is byte for byte the normal compile's; with the trace on at any level, in any form, the object is
also byte for byte the normal compile's (the command line is in the object, so compare with the
same `--errors` name). Costs on a 32-core x86 host (normal compile 0.18-0.20 s):

| trace | seconds | size |
|---|---|---|
| everything, level 5 | 5.5-6.1 | 74 MB |
| level 5, one colouring (the 5,606-node function) | 4.7 | 59 MB |
| everything, level 1 | 0.33-0.36 | 9.8 MB |
| light profile, all colourings | 0.33-0.35 | 9.0 MB |
| light profile, one colouring | 0.25-0.29 | 5.9 MB |

Most of the full trace is the work lists printed before every removal (41 of the 74 MB) and the
clash sets before and after it. The light profile (`--profile light`, `armtrace.parse_light`)
keeps the start of each colouring, the passes and spills, every removal with its weighted
references and its move neighbours, every colouring with the registers its coloured neighbours
take away and from whom (clash or move clash), its colour, and the coalescings. Reading it takes
0.08-0.1 s for the large function. It gives every final colour of the full trace (2,792 of 2,792),
but it has no graph dump and no heaps, so armreplay cannot check or replay it; it is for
measuring, per variant, why a value got the register it got.

### Reference counts as targets

- The graph dump leaves out moves to precoloured registers, and graphs with spills replay wrongly
  without them. `armreplay.py premoves` recovers the ones there at the start (every move the
  removals print, less those a move-clash event contradicts), and `--premoves FILE` uses them in
  the coalescing test. With them, functions with 6 and 15 spills replayed exactly. Take a node's
  reference count from the first pass that lists it; later passes print it changed.
- Some of those moves the node only gained by coalescing: a partner coalesced into it had the
  move to the register, so the removal prints it. Those take part in the coalescing test but do
  not count as moves in the heap keys; the ones the node holds from the start count in every
  heap's keys. A 14,966-node dispatch function (35 spills) disagreed from its very first heap
  until the two kinds were told apart: two tied nodes with refs 67 sat in each other's heap
  slots because one had moves to r0 and r1 from the start. Counted in the keys without telling
  the kinds apart, the same moves broke three smaller functions that agree either way once they
  are told apart (86 functions checked). `premoves` marks the first kind; ARMREPLAY_PMKEYS=none
  restores the earlier rule.
- `armreplay.py refs TRACE NODE=REFS` replays with a count changed and lists the nodes whose
  register changes. When the image swaps two values, look for the smallest change that gives
  exactly the image's registers and nothing else: it says which value needs more or fewer uses
  in the source, and how many. In one task loop the constant 1 (229) needed 195 to 224; in a
  receive handler five hoisted constants needed (62, 62, 62, 61, 61) for (61, 61, 61, 61, 61).
- A hoisted constant's count grows by about 28 for each copy of it the function uses (61 with
  one, 89 with two, 229 with seven); a plain local's by 32 for each reference, 3 for the
  definition. Nesting, `&&` against nested `if`s, and the spelling of a range check left counts
  unchanged at `-O1`; unread comparisons of a parameter with a constant added nothing.

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
- A pool dumped in the middle of a function moves the blocks around it. The image of one reset
  check had a 40-word pool behind `b` at +0xC8, and a `cbz` whose target was the second of two
  branches before that pool. The build, with the arms of `if (master != 0) {...} else {...}` the
  other way round, put the same code in another order and differed in 106 instructions. Writing
  `if (master == 0) {...} else {...}` gave the image's order: score 10, then exact. When a
  function's blocks are in another order around a mid-function pool, swap the arms of the big
  if/else first.

## Control flow

- Nesting decides which arm goes out of line: `if (v >= K) { if (a > w) w++; else { w = 0;
  f = 1; } } else w = 0;` put `else w = 0` out of line behind `blt`; early returns or an
  `if (v < K) ... else if ...` chain kept it inline behind `bge`.
- A flat chain and nested ifs with the same layout swap value registers:
  `if (p->n == 1) { if (v == 0x2200) {...} else p->err = 3; } else p->err = 2;` against
  `if (p->n != 1) p->err = 2; else if (v == 0x2200) {...} else p->err = 3;`.
- A sparse switch over 32-bit constants compiles to a binary search; at each leaf pair the
  second compare is `bne default` and may fall through into a case body. Out-of-line bodies are
  emitted in source order, and a leaf falls through only to a case that comes earlier in the
  source than its partner. On one firmware's CAN receive handler (ten cases), reordering the
  cases to give the image's out-of-line order and its three inline bodies took the masked score
  from 577 to 88, and moved the literal pool that a following function shares, which fixed that
  function's layout too.
- A pointer to a record in an array of structs takes its form from the spelling.
  `p = tbl; p += i;` compiled to one `mla rD, i, size, base`. `p = &tbl[i];` compiled to a `mul`
  by the record size first and an `add` of the base several instructions later, as one image had
  it (a 0x97-byte record).
- A store that three paths end with was emitted once when the source wrote it once, after an
  `if (a) ... else if (b) {...}` chain. Written in each arm (`if (a) {...; s = 0;} else {...; s = 0;}`),
  armcc kept two copies, where the image had one.
- A sparse switch over pointer values (`switch ((u32)p)` with eight addresses, also five)
  compiles to a binary search with a literal pool, never a compare chain.
- A function that starts with `nop.w` (`f3af 8000`) before its prologue is two functions: a
  4-byte one whose whole body is a tail call to the next (`void A(void) { B(); }` is `b.w B`),
  which armlink's default `--branchnop` turned into `nop.w`, and B straight after it. Split the
  map row 4 bytes in. Inline `__asm { NOP.W }` narrows to `bf00` and no intrinsic puts a NOP
  before a push, so nothing else makes those bytes.
- `__attribute__((naked))` is refused (#1207-D).

## Types and packing

- A byte the image reads twice in a row, testing the same thing, is volatile in that unit, and the
  source tested it twice. One status function tested `flags[0]` and then `flags[0] || flags[4]` on
  the same path. With the array declared plainly, armcc folded the second read whatever the
  spelling; declared `volatile` in that unit, `flags[0] != 0 || flags[0] != 0 || flags[4] != 0`
  matched. A unit's extern declaration can differ from the defining file's (here the defining
  file had `volatile`, the user had not).
- An if-else chain whose last arm stores the value the variable already holds keeps its test in
  the image only when the variable has no known value on that path. Initialised at its declaration
  or at the top of the function, armcc removed the last test however the constant was spelled
  (`0xff`, `-1`, a cast, a `static const`). Declared without a value and set in the other branch
  instead, the test stayed and the size matched.
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

## armcc -O0, and files built without --split_sections

Learned on a second Cortex-M3 firmware of about 500 functions, built at `-O0` without
`--split_sections` throughout (vendor driver files and application alike), and matched in C with
`tools/o0c.py` drafts. Most rules were checked by compiling a probe; the rest are from matched
functions.

How to tell such a build:

- `-O0` copies every parameter out of r0-r3 into r4 and up in the prologue of a function that
  calls out (`mov r4,r0; mov r5,r1`), used or not; a leaf copies one only when it needs the
  register (`mov r3,r0` so that r0 can hold the result: a leaf that moves r0 away returns a
  value). `-O1` drops the copies. `-O0` also makes no tail calls (`g(); }` stays `bl g`), and
  shares one epilogue: later returns branch back to the first `bx lr` or `pop` in layout. Scoring
  every function under -O0 to -O3, with and without -Otime, gave -O0 three times as many exact
  functions as any other level and no address range where another level won.
- Without `--split_sections` a file is one `.text`: its functions in source order with one literal
  pool after the last, except that armcc emits a callee defined later in the file ahead of its
  caller (a probe with `report` calling `pick` and `put` defined after it gave pick, put, report),
  so in the image each callee lies before its first caller. A function can load a literal that
  lies after later functions, so a function can only be proved by compiling its whole file, and
  armlink cannot drop unused functions (every function of a vendor driver file is in the image).

Where the files are:

- armcc gives a `.text` 4-byte alignment only when it holds a literal pool, else 2. So 1 to 3 zero
  bytes between two runs of functions are armlink's padding before a file whose `.text` holds a
  pool, and a function at an address of the form 4n+2 right after another file's code either
  starts a pool-less file or continues the file before it. In one image six one-function "files"
  were one file: only the last function loaded a literal, so only the whole file was 4-aligned,
  which explained a 2-byte gap of zeros before it.
- armcc places an intermediate literal pool where the end of the file is out of reach of a load:
  between functions, or inside a long function with a `b` over it. A pool inside a file does not
  end it. A file boundary can only lie where no load crosses it: no function before it loads a
  literal or `adr` block after it, none after it loads one before it, and on a 4-byte boundary.
  `o0c.py files` proposes files by that rule.
- The loads only say where a file may end. What armcc knows of a callee (below) shows boundaries
  the loads cannot: a caller that keeps a value in r1-r3 across a call, or pushes an odd number of
  registers, shares its file with the callee.

What armcc knows of callees at -O0:

- A callee in the same file lets armcc keep values in r1-r3 across the call and drop the
  stack-alignment padding from the caller's `push`: a caller pushes an odd number of registers only
  when every callee it calls is in the file. armcc emits such callees before their caller whatever
  the source order (a probe with the callees defined after the caller gave the same object, the
  callees first), so in the image they lie earlier. In one image two handlers pushed nine
  registers without padding, which told that three functions far before them were in the same
  file.
- Only a global callee counts. With the callee `static`, the caller is compiled as if the callee
  were in another file: a loop calling two static helpers saved its counter in r4 and pushed
  `{r4-r6, lr}`, where with the helpers global it kept the counter in r2 and pushed `{r4, lr}`
  (probe); a caller of a static leaf pushed `{r4, lr}` where a caller of the same leaf as a global
  function pushed only `lr`; and a parser that called two static helpers had a 0x1C frame where
  with the helpers global it had 0x18. Whether a helper was `static` shows in its callers.

Operand order and stores:

- armcc evaluates an operand reached through a cast pointer (`*(u16 *)(base + 4)`,
  `((S *)p)->f`) before a named variable, whatever the source order. The same holds for call
  arguments and compares. Declare a global as the scalar or struct it is (`extern u16 g_count;`)
  and it is loaded in source order; a draft that reaches globals through casts of a byte array
  gets the order wrong. Reading one byte through a cast and the other by name also decides which
  register an `and` gets in `(0xffff & (hi << 8)) + lo`.
- Two symbols for one address make two literal pool entries, and every later load moves. Keep one
  name per address across a file (the same name in every function, or one declared typed and the
  others reaching it through its address).
- Value first, then address: a store to a member array or to a member of a nested struct at a
  nonzero offset (`s.buf[1] = 4`, `g.sub.b = 0`), or to a struct array element at a constant
  index (`tbl[5].c = 0`), evaluates the value into r1 and the address into r0; a plain member
  store does the opposite. A cast macro (`((struct N *)g_x)->sub.b`) keeps the pattern, so it
  tells the struct layout. A 1-D member array gives a `mov r0,r1` before its second store.
- Volatile bitfields: `ldrb r0,[x]; movs r0,#7; strb` is a store to a volatile 8-bit bitfield;
  `movw r1,#k; ldrh; bfi r0,r1,#0,#16; strh` the same for 16 bits.
- A `short` member store keeps an `sxth` when the member is declared `short` in a struct, not when
  the store goes through `*(short *)p`.
- `u16` promotes to `int`: `v >> 8` on an `unsigned short` local is `asrs`, even right after
  loading a plainly positive constant (`mov r4,#0x5a0; asrs r0,r4,#8`, probe); `(unsigned)v >> 8`
  gives `lsrs`. An `asrs` on a value loaded with `ldrh` or `uxth` means a `u16` promoted, not a
  signed type.
- `256 * p[1] | p[0]` loads `p[1]` first; `p[1] << 8 | p[0]` and the other orders load `p[0]`
  first. `buf[2 + i]` computes the index before loading the base, `buf[i + 2]` after.
- A byte array in a struct indexed by a constant, reached through a pointer plus an offset that
  is not tiny (`(p + 0x40)[7]`), is `movs rX,#off; ldrb rY,[rX,rBase]`; `p[0x47]` folds the
  offset into the load.
- In a run of byte-pair stores armcc adds `mov r0,r1` before the second store when it knows the
  value fits in a byte (a `ldrb`, `& 1`, a `ubfx`, a constant). `+ 0`, `& 0xff`, casts and `<< 0`
  keep that knowledge; `+ 0x100 - 0x100` folds away in the code but loses it.
- Constant reuse: `if (x == 8) g = 8;` stores the compared register; `if (state == 1) return 1;`
  returns it. A constant can be rebuilt from a known register (`0xFFFFFFFF` as `subs r0,r4,#2`
  with r4 = 1).
- A `const` table indexed at run time must stay a symbol: written as a constant address, armcc
  folds part of the address into the load and the size changes.
- `abs()` is inlined (`cmp; blt; mov; b; rsbs`). Float code needs real `float` types:
  `v * 10.0f` is `bl __aeabi_fmul; mov r7,r0; bl __aeabi_f2uiz`, the `mov` being armcc's spill of
  the float temporary.
- Packed structs and pointer locals: a pointer local into an array of `__packed` structs
  (`p = &arr[n]`) can give a write-back store (`strb r0,[r4,#-1]!`); `p = arr; p += 1;` gives
  `ldr; adds r4,#size`, where `&arr[1]` folds the offset into the literal.
- A width mask that changes no value can still change registers. `(cfg.mask & 0xFF) & (1 << n)`
  on an 8-bit field emits nothing for the `& 0xFF`, but it was the only change that fixed the last
  11-byte register swap of one parser. An audit of file boundaries, callees, ownership, packing,
  signedness and linkage, five MDK releases, and batches of accessor, mask-type, declaration,
  copy and promotion forms had not.

What only leaves a `nop`:

- `-O0` emits a `nop` for a branch to the next instruction and for an assignment that stores the
  value the variable already holds: `x = x`, `x = 0` when x holds 0 (`for (i = 0; ...)` right after
  `i = 0`), a self-initialised local (`u16 t = t;`, a `nop` in the prologue).
- The same `nop` comes from `&& 1` in a condition, `(void)0;` as an if body, an empty
  `else if (x == 3) { }` at the end of a chain, a block-scope declaration with an initialiser at
  the end of a block, configuration tests that are constant (`if (0 && ...)`), and a label in front
  of a `goto`.
- Switch tails: a last case ending in an `if` gives one `nop` for the if's end and one for the
  switch's end (breaks jump to the second). `default: break;` after the last case adds one more;
  `case 0: case 5:` gives case 0 its own. A switch with one `case 0` compiles to `cbnz` like an
  `if` and still leaves the two `nop`s.
- `__nop()` is not the same: the intrinsic makes armcc forget register contents, so a register
  reused after the `nop` turns into a fresh literal load.

Control flow at -O0 follows the source's layout: `while`/`for` is `b test; body; test: bcc body`,
`do ... while` starts with a `nop`, `while (1)` is `b end; body; end: b body`, `if (x) break;` is
`b!x skip; b exit`, a conditional branch out of range goes to a `b` island placed in reach,
conditional branches are threaded through branches on the same flags, identical statement tails
are merged across paths (`ldr r1,=g; strb r0,[r1]` ending two arms), a function that never
returns has no `push`, and a switch is its dispatch (a bounds check and `tbb`/`tbh` or a word
table, or a binary tree of compares for sparse cases) followed by the case bodies in order.
Locals each get a register of their own for the whole function, by use count, ties by
declaration order; temporaries are allocated over the whole function, so one statement's registers
can hang on a statement far away (a random permuter over a hand source found such pairs).

Large functions at -O0:

- A large function keeps every local in a stack slot and pushes only `lr`, or `{r0, lr}` with a
  parameter. Functions of 1,103 and 856 instructions did this, while half of the first, 616
  instructions, pushed r4-r11 and kept its locals in registers. I don't know the exact size where
  the mode changes.
- In this mode r0-r3 hold only temporaries, and their choice is global. In an 18.5 KB switch,
  removing only the last two or three case bodies changed the registers of the function's first
  statements to chance. So you can't match one part of such a function in a small test
  function, because the registers there depend on the whole body.
- An empty `case X: break;` gets its own `b` at its source position, and the switch table points
  at that `b`. Its place between the neighbouring case bodies shows where the source had it.
  Putting four empty cases together at the end added four `b` instructions and turned one `beq`
  into `beq.w`, a 2-byte difference that was the first clue in that function.
- A block-scope local gets its own stack slot. It is not overlaid on a function-scope slot.

Changes that keep every instruction but move registers at -O0. I found each one in an exact
match:

- **A multiplication by a power of two instead of a shift**, e.g., `i * 2` for `i << 1`. The
  code is the same shift, but the allocator sees the multiply. In the 18.5 KB switch, the whole
  register difference came from three things together:
  - five such spellings in one case (four `i * 2` and one `* 256`);
  - the parameter declared `unsigned char` rather than `int`;
  - the empty cases in their right places.

  Every partial set failed. The vendor mixed the two spellings, and 48 other `x = x << 8`
  statements in the same function had to stay shifts. A neighbouring function had
  `((p[5] << 8) + p[6]) * 2`.
- **Nesting.** `if (a) { if (b || c) S }` and `if (a && (b || c)) S` give the same code. But
  in stack mode the spilled float temporaries get different slots, and in register mode they
  get different callee-saved registers.
- **An assignment inside an expression.** `soc = (sum = avg) / n;` and a separate `sum = avg;`
  give the same code, but `sum` has one reference fewer. Register locals are ranked by use count,
  with uses in a loop counted more, so two locals swapped registers.
- **Locals.** Removing a local that only held a loop bound flipped a value and index pair in a
  store elsewhere in the function. Passing an expression straight to a parameter, rather than
  through a temporary local, swapped two registers.

A register change at -O0 also changes a few instructions around it, so these levers can look
like code changes:

- a constant that armcc reuses from a register or loads again (`movs r1,#0x30`);
- a literal base that is loaded again;
- two adjacent stack loads that pair into one `ldrd` or stay two `ldr`;
- the literal pools and branch islands that move with these.

Count a difference as allocation only when every differing run is one of these. Three search
runs on the 18.5 KB switch required the register-masked instructions to be equal before they
scored a change, so they threw away the multiplications above every time.

Not inputs at -O0. Each of these was compiled and left the function byte-identical:

- line breaks (the whole function on one line, or every line tripled);
- file contents before the function, the path, the environment and the option order;
- constants (constant-only edits between eleven versions never moved a register);
- an enum against its underlying integer type;
- empty statements, braces, `else if` against `else { if }`, `!x` against `x == 0`, De Morgan
  forms, and compound against plain assignments;
- the host heap under Wine (the allocator's trace stayed byte-identical when the compiler's own
  pointers changed order).

Smaller -O0 facts from the same matches:

- A `static __inline` getter with no parameter is inlined but leaves a `nop` before its body. An
  inlined helper with a parameter leaves none.
- A macro body in `do { } while (0)` adds a `nop`, and `else (void)0;` adds a `b` and a `nop`.
  Plain braces add nothing.
- A local with an initialiser is kept even when it is never read.
- `if (a - b == 0) x = 0;` stores the zero it compared.
- A `short` member shifted right by 8 and stored to a byte is `ldrh` then `ubfx #8,#8`.
- An `unsigned` value converted to `float` calls `__aeabi_ui2f`, and a signed one calls
  `__aeabi_i2f`. Nothing else in the code differs, so only the call target shows the type.

Const data:

- Global `const` arrays go to the object's one `.constdata` in definition order, each at its own
  natural alignment with no other padding; the section's alignment is the largest in it (a `u16`
  table followed by byte tables gives a 2-aligned section). armlink puts every object's RO data
  after all the code, in link order, so the const region is the files' `.constdata` one after
  another in the order of their code.
- A function using such an array loads its address from the literal pool whether the array is
  defined in the same file or not: defining the data in the file that uses it changes no code.
- A string literal used in code goes into the function's literal pool, taken with `adr`, with or
  without `--split_sections`. A string that only a data initialiser points at
  (`const char *const p[] = {"a"}`) goes to `.conststring`, each string 4-aligned; a
  `const char s[]` goes to `.constdata`.

## One function in assembler among C neighbours

When every function of a file but one is C and the last is still assembler, the file can still be
built from source, with its native placement and literal pools, by composing the compiler's own
assembler output with the hand-written function. It keeps a whole image built from source while
that function waits for C. It is not a C match, and the gate should count the function as
assembler.

The easy ways fail when the functions share pools or one starts on a halfword:

- an embedded `__asm` function goes into the file's one `.emb_text` section, out of place;
- the C before and after it in separate objects, placed by the scatter file, lost the native
  sizes, alignment and pools;
- the function alone as an armasm file could be placed, but the C after it needed a start on a
  halfword that a separate object cannot give.

What worked, in one firmware, for a unit of nine C functions around one 11,746-byte assembler
function:

1. Compile the C with the file's options, and again with `-S`. Reassemble the `-S` output with
   armasm (`--cpu Cortex-M3 --apcs=/interwork`, as its assertions require), and require that every
   allocated section has the same bytes, attributes, size and alignment as the direct object,
   every public symbol the same offset, and every relocation the same symbolic expression. That
   proves the assembler text is the compiler's code.
2. Keep the compiler's instructions and literals. A small layout file gives what the C cannot:
   each function's native address, where its pools go and the padding. It holds no opcodes and
   no literal values.
3. Put everything in one Thumb AREA: the C functions, the hand-written function at its native
   place, the pools merged where the image shares them (a pool word both use must get the same
   value from both sources), and the image's branch over a pool in place of the compiler's.
   Assemble once, with assertions on every entry, every pool start and the total size.
4. Never read the target image while composing. Make the build cache's key include the
   assembler input, the layout file and the composer, and prove each reaches the object: a
   changed immediate in the assembler and a changed constant in the C must each fail the gate.

The composed object matched all 16,930 bytes and 660 relocations at native placement. A real C
replacement is then staged as an ordinary C file, without the composition, and proved on its own.

## Searching for the source

Most functions match by hand in a few builds. The last few in a firmware are register swaps and
load orders that no single edit moves; these took searches over combinations, not more hand edits.

- Decode a call that differs before searching for a code shape: in one image the shutdown routine
  called `f_close` where the older release, and the draft carried over from it, called `f_sync`.
  Two bytes of a `bl` differed, the instructions were the same, and only the decoded target
  showed it. The map's symbol table wins over a source's `at:` line, so an address fixed on the
  `at:` line alone changes nothing.
- Start a probe search from the plain source. A search started from a source that already held
  an earlier search's probes stopped at 2 differing instructions; from the plain source the
  same probes found the exact source in one round.
- The tools: `armvariant.py` scores one source against the image without linking (a second or
  two under Wine; a batch of 40 to 150 variants in one armcc call costs little more);
  `fnscore.py` scores every function of a file at once, with or without `--split_sections`, and
  flags a function whose size or entry moved, so a search can reject a variant that fixes one
  function by breaking its neighbour;
  `armpermute.py` mutates a function at random and keeps what scores no worse;
  `armoutline.py` writes templates of a function's statement runs as inline helpers, or of one
  statement at every point of its body; `armbrute.py` runs a template's combinations exhaustively,
  at random or by coordinate descent, or tabulates what armcc makes of each spelling;
  `armreduce.py` cuts a result down to the edits that matter. Rerun match.py on any hit.
- The score masks registers when asked (`--regs`): first get the structure (the same instructions
  in the same order), then search the allocation. Structural differences are source shape;
  register differences are helpers, temporaries, declaration order, types and dead uses (above).
- Search from the plain source. Strip the dead uses, temporaries, carriers and helpers that
  earlier rounds added to make up for something else first: each was fitted to a context that
  may have been wrong, and once the context is right they usually make things worse. A draft
  tuned under one compiler build scored worse under the right build than the plain source did,
  and when a wrongly placed local was removed, the two forms added to make up for it swapped
  registers until they were taken out as well.
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
  30 s without) and one shared server becomes the bottleneck. Warm each new prefix before its
  first tool, with `wine cmd /c exit` and no pipes (linker.md): a search's first compile in a
  fresh prefix otherwise never returns.
- armcc's front end keeps EDG's debug options: `--db=-dump_il` prints a function's intermediate
  language with source lines and columns, `--db=-dump_stmts` the statement tree,
  `--db=-dump_lifetimes` object lifetimes. The back end's register-allocation trace is in the
  release build but off, and no option turns it on; an observer can turn it on in the running
  process ("Tracing the register allocator"). Whether the scheduler's and the literal pool's
  debug output can be reached the same way is untested.
- A greedy climb over dead probes is cheap and finds register fixes that hand edits miss. Take
  five or six probe statements of the kinds that survive to allocation (above: an unread
  comparison of two locals, or of a local with a constant, stored in a block local; an unread
  comparison of two globals' addresses), insert each after every statement of the function, score
  all of them unmasked in parallel, keep the best, and repeat from it until nothing improves. In
  one firmware this matched a handler in one round (`{ uint z; z = (uint)(highest == lowest); }`
  at the top of its sampling loop) and a 0x280-byte function in two rounds (two comparisons of a
  local, `amps == limit` and `amps == 0`), from 43 differing instructions. Start the climb from the
  plain source: started from an earlier probe that had brought the second function to 18, it
  never got below 18. Count a score of 0 as a result: a scorer that prints `=` for an exact
  function must not be parsed as a failure.
- Run `tools/armrepeats.py --func NAME` before any search. A matched function that repeats the
  same instructions shows how the vendor wrote that code, and its source is the first form to
  try. In one firmware two sibling senders showed that the vendor passed some values through a
  temporary `t`: in one release that compiled the same as the plain spelling, in the next it did
  not, and only the sources with `t` rebuilt both.
- When registers stall, trace the allocator before brute force ("Tracing the register
  allocator"). On one function about 100,000 searched sources never reached 0; the trace showed
  the tie, and a replay over declaration orders found the source.
- Screen candidates before compiling them. A random permuter writes C that is not the same
  program: two `n++` in one expression, a temporary read before it is set. Check each text with
  clang for the target (`clang --target=arm-none-eabi -mcpu=cortex-m3 -std=c99 -fsyntax-only
  -Wunsequenced -Wuninitialized -Wsometimes-uninitialized`, with armcc's keywords defined away
  for clang only, `-D__packed=` and the like). In one run 35 of 242 compiled texts had unsequenced
  changes, and the lowest score of the run was one of them; in another, 342 of 637 had an
  uninitialised temporary. A low score from such C is not progress. Check too that a candidate
  does the reads, writes and calls the original does, in its order: a search can move a read
  into another branch, or add one through a new alias.
- Count everything: attempts, sources that compiled, sources that failed, distinct texts. Keep
  the failures with their errors. Distinct texts are not distinct outputs: one pilot compiled 826
  sources into 785 distinct listings.
- A model score ranks what to build; only a build proves anything. A candidate a model cannot
  score is still worth compiling, and a model's zero is a hint until match.py says WHOLE FILE
  MATCHES.
- Decide before a search what a result must satisfy besides its score, and audit each round's best
  few and every result with the right sizes against it: every function of the unit the same size
  and entry, the neighbours' bytes and pools unchanged, the same spills by identity (not only by
  count), the same divisions and calls. Report the measures apart: right sizes, the byte score and
  progress against a fixed control are different things. A result that only moves the old
  failure later in the function has not passed it.
- Simon Lindholm's decomp-permuter can drive armcc. Use its mutation engine (its Candidate and
  Randomizer classes) with your own compile and score hooks; its objdump scorer is not for this
  target. Its C parser does not know armcc's keywords, so take `__packed` and the like out of the
  parser's view only and compile the original text, and splice the mutated function back into
  its whole file so that the neighbours and headers stay. Turn on the passes for temporaries,
  declaration order, split and chained assignments, operand order, compound assignments,
  conditions and blocks; leave type changes and new helpers off unless you mean them. Its printer
  adds blank lines after the function, so compare the rest of the file without that whitespace.
  One pass, `perm_temp_for_expr`, took 1.5 of 1.6 seconds of a slow mutation, and it can put its
  temporary outside the region you marked (into another switch arm, or where it no longer comes
  before its use). Screen its output as above. On one large function a run of 256 builds and a
  pilot of 846 found no match; armpermute.py is the simpler built-in alternative.

What an 11.7 KB function taught about searching. The function was a protocol dispatcher at
`-O1`, and its registers resisted about 100 search runs over two and a half weeks. It matched in
the end with no edit at all, because the vendor had built the same source earlier at `-O0`:

- **Look for other builds of the same source first.** Older firmware versions may have the
  function built at another optimisation level. An `-O0` build maps statements to instructions
  almost one for one, so its source is much easier to recover. Here the `-O0` source, compiled
  unchanged at `-O1`, was byte-exact against the current image.
- **Don't compare builds by their instructions.** The `-O0` and `-O1` copies of the same function
  had a masked similarity of about 0.2, so a scan of the versions reported "no near sibling".
  Compare builds by compiling one candidate source under each build's options.
- **Use every version as a check.** Eleven old images held the function in six versions. The
  vendor's edits between versions were few and easy to read, so one source could be scored on
  all six at once. A real source feature has to hold in every version, and a change that helps
  one version and hurts another was fitted to noise.
- **Match the neighbours first.** Before searching the hard function, match the other functions
  of its file, and a few large functions elsewhere that use the same allocation mode. When they
  are all exact with natural C, the compiler and options are right, and the difference is in the
  hard function's own text. The neighbours also show the vendor's spellings.
- **Fix the structure first.** A length difference of a few bytes is evidence about the source,
  not a side effect. In that function, two bytes led to four misplaced empty cases. With them
  in place, the register levers could be scored.
- **Calibrate partial scores before trusting them.** When allocation is global, measure how far a
  small code-neutral change moves the score, and how often your source agrees with the image at
  each site by chance. In the function's 18.5 KB `-O0` build the image's choice at each of 332 stores behaved
  like a random bit, and our sources agreed at chance. Exact-case counts moved by about four
  either way as noise, so only an exact hit was evidence. A search that keeps the
  best-scoring variant just follows that noise.
- **Know what an exact model of the allocator can and can't do.** I wrote replicas of the
  allocator at `-O1` and at `-O0` that gave the compiler's own choice at every step. They let one
  run score 14.5 million combinations of source changes in hours. But they could only test the
  changes in their catalogue, and the answer, a multiplication in place of a shift, was never in
  it. Before you build a model, list the spellings of each kind of expression (shift or multiply,
  nesting, parameter types) and test each family with real compiles.
- **Make byte checks strict.** A checker that learns which symbol each address is must allow one
  address per symbol and no address shared by two symbols. One checker let `__aeabi_ui2f` take
  both its own address and `__aeabi_i2f`'s, and so passed a function with the wrong signedness.

## The fast permuter (armpermute2.py)

`armpermute2.py` is a second, faster permuter, written after the hand sweeps and `armpermute.py`
stopped finding the last functions of a firmware. It searches the same way the Nintendo
decompilation projects do: many islands of random source edits, real compiles, keep what scores
no worse. What makes it worth running for hours is the cost per variant and the rules.

Measured on a 32-core x86 host with the Linux build of the same compiler (ARM Compiler 5.06u3
for Linux, run with `ARM_TOOL_VARIANT=mdk_lite` and `ARM_PRODUCT_PATH` set to an MDK mappings
folder), one 12 KB source unit:

- process start 7 ms, whole unit 250 ms, the unit cut after the function 180 ms, one function
  alone 140 ms; 100 copies of a function in one file cost 157 ms each, so batching gains nothing
  natively (under Wine it did, because Wine's start-up was the cost). Code generation is the
  cost: one compiler process per variant per core, work files in /dev/shm.
- armcc reads the source in 4 KB pieces while it parses, but it parses the whole file before it
  generates any code (the last source read comes about 45 ms into a 250 ms compile). So a
  compile paused at the function's first line and forked per variant (the Borland permuter's
  snapshot) would save only the parse of what comes before, not the code generation; it was not
  built. What does save time is ending the file right after the function ('prefix' cut), since
  the code generation of the functions after it is skipped; the start-up check decides.
- fromelf costs 12 ms. The permuter does not run it: `orcobj.py` reads the object in-process.
  MDK-Lite's objects are ELF with the section headers and the global symbols shuffled (the file
  starts `\xf7ORC`); each record's byte permutation comes from a SHA-1 of a key built from the
  section header offset or the symbol count (the module docstring has the details). On 257
  objects it gives the same sections, symbols and relocations as `fromelf --text -v -r -s`,
  except for assembler objects' unnamed section symbol.
- Scoring (`permscore.py`) is in the worker: the function's bytes are placed at its map address
  with every relocation applied (calls and literals that name a mapped function go to its map
  address), and compared with the image. 0 means equal bytes. Otherwise the count is
  instruction-aligned like fnscore's, with branch targets as indexes, but bl targets and literal
  values are compared, not masked, and pc-relative displacements are dropped. On one tree's 967
  functions it agreed with fnscore wherever fnscore was right; where they differed, fnscore had
  masked a literal or call that really differed, or miscounted a one-instruction loop. The
  first version took a renamed row's placeholder alias in place of the source's own at: pin for
  the same name; the link prefers the pin, and so must a scorer. Cost: 0.5 ms when the bytes are
  equal, 1-13 ms otherwise (2,800 instructions), results cached by object hash.
- Mutation is a tokenizer, a statement tree and a small expression parser (`permc.py`), no C
  parser library: 0.1-0.5 ms per rule, 4-5 ms per variant including the second parse of a
  two-edit move, the rendering and the duplicate check (in the parent at first; in the workers
  since the third round, below).
- Throughput: 60 variants a second with 16 workers on the 12 KB unit (3.7 per worker), 130 a
  second on a smaller unit. Between a third and a half of the compiled variants give an object
  already seen: most single edits do not change the code.

The rules (`permrules.py`) are the kinds of edit that produced matches, taken from the history of
several trees rather than generic defaults, each usable both ways: dead uses in the forms that
survive to allocation (a comparison stored in a block local, a shift of a constant compared with
0, two addresses compared) and their removal or move; a local caching a global, member or
element and its inverse (reading it at each use, at one site or at every site read once); value
getters as `static __forceinline`; record pointer to direct access; statement order; arm swap,
nesting and test order; initialiser at declaration; a constant through a narrow local; redundant
stores (repeated, dead before another, or the value a test or case label just established);
if-chain to switch; goto chain against `||`; chained stores; `x[n++]`; `++n` in the test; range
tests; bit tests; select forms; shift against multiply; a cast to the target's own type; case-
label edits; scope and declaration order (weighted low: they decided small functions only);
sized extern arrays from the map (sizeof read from one probe compile); unprototyped definitions;
plus a few generic decomp-permuter passes. Weights follow how often each kind decided a match.

Lessons from proving it on solved cases:

- Keep some islands strict (they accept only improvements) and send restarts back to the
  original source every other time. Islands that accept equal scores collect neutral edits, and
  a one-edit answer from the original was tried more than once on top of such edits without
  scoring 0.
- Neighbours must be judged against the image, not against the base build: when the function's
  size changes, every later function's raw bytes change too (armcc resolves calls within a
  section without relocations).
- The search often finds a different exact source than the one a person found: a cached
  local in place of a dead comparison, a chained store in place of a dead shift. Reduce each hit
  (it does this itself, with armreduce's hunk reduction) and check it with match.py; prefer the
  form that also fits the vendor's style elsewhere.

Regression on solved cases (one firmware, functions matched by hand earlier; the source before
each match recovered from history; budget 10-15 minutes on 15 workers; "exact" means equal bytes
at the map address by the scorer, the same comparison objplace makes; every recorded answer was
itself checked to score exact under the scorer first, and two did not, because a data address
pinned later in the history differed):

| function kind | start | result | variants | seconds | what the search used | the recorded answer |
|---|---|---|---|---|---|---|
| CAN sender | 2 | exact | 66 | 1 | chained store | dead shift probe |
| byte receiver A | 8 | exact | 200 | 3 | stored comparison of two locals | dead shift probe |
| byte receiver B | 8 | exact | 531 | 9 | getter, `++` out of a test, dead shift probe | dead shift probe |
| current measurement | 7 | exact | 480 | 16 | two getters, stored comparison, block-local cache | stored comparison |
| update check (size +6) | 36 | exact | 809 | 10 | comma probe removed, dead shift, dead store, int temporary | comma probe removed, dead shift |
| receive task (size -4) | 3 | exact | 663 | 5 | cached selector, arm swap, dead shift | redundant store of the state |
| CAN receiver | 20 | exact | 131 | 2 | if chain to switch | if chain to switch |
| charge limit | 11 | exact | 35 | 1 | block-local cache | cache both operands |
| discharge limit (size +2) | 21 | exact | 338 | 11 | dead shift probe | stored comparison |
| protocol task | 29 | exact | 403 | 7 | range tests, arm swap, getter | arm swap, range tests |
| CAN receiver 2 | 8 | 1 (= the answer's floor) | 59,000 | 900 | stored comparison | local inlined at four sites (also 1) |
| register write | 2 | 2 (answer: 1) | 100,000 | 900 | none | int temporary used for the store and the call |
| holding registers | 2 | 2 | 91,000 | 900 | none | sized arrays, locals removed across blocks, else-if |
| summary update | 4 | 2 | 55,000 | 900 | stored comparison | sized array, goto chains to `||`, locals removed |

Ten of the fourteen found an exact source, most in seconds; three of the first runs failed until
the search was changed (strict islands, restarts to the original, redundant stores in nested
blocks, probes at block tops and with nearby constants), and then found them in 5-10 seconds. The
misses are the multi-edit structural rewrites (sizes plus restructuring plus locals removed across
blocks). The search found a different source from the recorded one in seven of ten cases, and
often a dead-code one: the permuter will fit probes where a person found a natural form, so its
results are evidence of where the tie is, not the vendor's source. `armpermute2.py` keeps a
Pareto front of score against the number of dead-code constructs added (best_dead+N.c).

After the regression, three more changes, measured on the misses and on the real target: a
bandit over the logged outcomes (a rule's weight is scaled by how often its variants change the
code at all) and a tabu list of (rule, site) moves that twice left the code unchanged; inlining
across stores to other named objects (two named objects cannot alias; only a store through a
pointer, or to an object the value reads, blocks it) plus an "every local at once" move; and a
dead-code penalty (`--dead-penalty N`: each dead construct added costs N instructions in the
ordering). The misses stayed misses. On a 5.6 KB function stuck at 60 by hand, 40 minutes of search
reached 47 at the right size, but only with seven dead constructs added (stored comparisons, an
address comparison, an unused local, a pointer store): a fit, not the source; by then 82% of
submissions were duplicate texts. With the dead-code penalty at 3 the search reached 49 with
no dead code added (inline getters for two call arguments, two reads through locals): the
penalty turns the search from fitting probes towards the natural forms. For runs of many hours
the search escalates by itself: when more than 60% of 3,000 generated variants are duplicate
texts it goes one level deeper (moves of up to six edits), takes a new seed and a new random mix
of rule weights, and sends half the islands back to the original; every new best is kept with
its edit list in OUT/best/, time-stamped.

Cut files did not help on this unit: the file cut after the function, and the file with the
other bodies stubbed (callees kept), both gave other code for the function (sizes differ), so
armcc's code for a function depends on more of the file than its callees; the start-up check
rejects them and the whole unit is compiled.

Second round: structural moves, moves as patches, simplify-first islands. The three misses
were rewrites of several edits that only pay off together, so the rules gained general moves of
those kinds and compound moves that apply a whole normalising pass in one step:

- common value temporary: `L = E; f(L);` (or E computed again) against `{ T t = E; L = t;
  f(t); }`, with the type chosen (int, unsigned int, L's type), the store through a cast pointer
  optionally written plainly; and its inverse;
- a group of locals inlined at every use (a run of locals assigned one after another, every
  such run, every local read once), with the declarations left unused removed, a multi-name
  declaration losing just those names; inlining now sees through function-like macros of the
  file whose bodies call nothing;
- remove every dead construct in one move (probes, dead stores to locals and parameters,
  repeated stores, unused locals, casts to the operand's own type);
- `else { if }` against `else if`;
- a pointer local set to a struct object or field and moved by arithmetic (`p = (T *)&G[0];
  p += K; x = p[1];`) replaced at each read by the field at that offset, from struct layouts
  read by one probe compile (`__INTADDR__` offsets and sizeof of each field of the structs the
  function's globals have); the inverse puts one object's fields behind a pointer local;
- a goto chain to the else arm against `&&` (the then-arm form already went to `||`); consecutive
  ifs with one idempotent body against `||`; `X = J; if (c) X = K;` against an if/else;
- one helper for every occurrence of a pattern: every `A[K]` read (or only those that are call
  arguments) through `get(k)`, every `A[K] = E;` through `set(k, v)`, and the inverse that
  writes out any one-statement forceinline helper at its calls;
- normalise: a random selection (or all) of the passes above in one move, and size-and: every
  sizeable extern sized together with one structural move;
- unsized extern arrays placed by at: lines take a size from the gap to the next known address.

Each applied rule step is kept as a patch (its span edits with six tokens of context each), so an
island's state is its list of moves. That gives crossover (another island's moves replayed on an
island where their context is found) and a reduction of every new best over its moves before it
is written: each move is left out in turn in parallel, then all that could go together, else one
by one. Moves stay whole, so a compound move is never cut into a partial edit (line-hunk
reduction can do that). A run resumed from an older state gets moves from the token hunks of
each island's difference from the original. A share of the islands (at least two) starts from
normalised forms of the base with different inlining choices, and returns there on restarts.

The dead-code penalty now counts more kinds, weighted: stored comparisons, unused locals and
pointer stores 1; casts to the same type and rule-made helpers or locals used for one read 0.5;
dead stores to locals and parameters and repeated stores 0.25 (a redundant store has been the
vendor's real code, so it only breaks ties; a redundant store that survives reduction of a best
is listed in the best's notes as a candidate real feature). The report line shows the best score
with no penalised construct added. The redundant-store rule no longer puts a dead store before a
store whose value has a call (the call could read it), nor a store after a test past a store
through a pointer.

Regression after this round (same cases, 10 workers): the three misses were found, two exact in
about a second (the simplify-first islands: sized arrays, pointer arithmetic to fields, goto
chains to tests, locals inlined) and the third at its recorded answer's floor after 90 seconds
(the common temporary). Of four earlier hits rerun, three were found again in 1-46 seconds; the
fourth (a five-edit combination of probes, a dead store and a temporary) was missed in two
15-minute tries (1 and 41); the first version missed it on its first run and found it on its
second, by luck of the seed. The parent's cost per variant rose from 6 to 21 ms with the
compound moves, and the rule set is a fifth larger, so such combinations are rarer per variant. The first test of the new moves also showed two traps: block locals of the
same name (every probe declares its own `z`) must not be taken for one local, and a variant's
move list must be the one it was generated from, not its island's current one.

Third round: generation in the workers, focused site choice, parsimony. A 9-hour run of
655,000 variants on a 5.6 KB function ran at 21 variants a second on 13 workers that could have
compiled 50 (the one parent made every variant, 21 ms each), and its best needed 111 moves after
reduction: drift, not structure. The one real fix found since, by hand, was a small local
difference in the instruction diff (two statements moved into a `for` header).

- The parent no longer parses or edits anything in the loop. A job is the name of a base text
  (the parent writes each island base once to a folder in the RAM directory, named by its hash),
  a seed, the island's key and what the island would still accept. The worker parses a base once
  (kept by hash), applies the rules under the seed, compiles, scores, and sends back the score,
  size, trail and the text's hash; the text and its moves come back only when the island, the
  best or the Pareto front may keep it. The set of texts already made is a table of 64-bit
  hashes in shared memory (made before the pool forks), so a worker drops a duplicate before it
  compiles it; the tabu moves and the rules' bandit weights are shared the same way. Reduction
  over moves sends move lists, not texts. Measured on one Mac under Wine with 4 workers, same
  function, same options (compile 0.76-0.80 s there, so the workers were the limit both times):
  the parent's main thread went from 5.9 ms to 0.11 ms per variant and the parent process's CPU
  from 6.9 ms to 0.4-0.5 ms per variant; throughput 4.1-4.6 a second before, 4.5-4.6 after;
  generation now costs 5-7 ms per variant of worker time, beside the compile. The parent's limit is now
  some thousands of variants a second, so the worker count sets the rate.
- Wine: the compiler's messages go to a file. With a pipe, a background process of the prefix
  (services.exe) could inherit it and hold it open after armcc ended, and the first compile then
  waited for the 120-second limit. The parent's own compiles share one prefix, stopped at the end.
- Focus. MDK-Lite's armcc refuses `-S`, `--asm` and `--interleave` ("Option not available with
  your license"), so the source lines come from debug information instead: the same text
  compiled once more with `-g`, and the `.debug_line` programs read in-process
  (`permscore.line_rows`). Checked on a real 64 KB unit: the code section and its relocations
  are byte for byte the same with `-g` as without, and the line of every one of 4,281 listing
  rows agrees with `fromelf --text -c --interleave=line_numbers` on the same object (fromelf is
  not run by the tool). The tool checks again at each use: the lines are taken only when the
  function places to the same bytes from the `-g` object. Each differing instruction of the
  variant's function (for an instruction the image has and the variant lacks, its neighbours)
  gives a hot line. This is done for the start, and in the worker for each variant that improves
  its island or the best (about one compile in a hundred); a neutral variant an island keeps
  inherits the base's hot lines, carried to their new numbers by a line diff. An edit with a
  span within `--focus-near` lines (3) of a hot line is always kept; any other is kept with
  probability `--focus-cold` (0.25). Exempt: compound moves, edits with no span in the
  function's body (externs, helper definitions) and edits that only change locals' declarations
  (type, order, split); a span that lies wholly in declarations is not judged, so inlining a
  local is judged by its uses. `--no-focus` turns it off. On the function above (score 52, 14
  hot lines of 374) a quarter of the edits the rules proposed were at hot lines, 7% exempt and
  two thirds elsewhere, of which a quarter were kept.
- Parsimony. Each move in a variant's list costs `--move-penalty` (0.15) differing instructions
  in the acceptance key, as the dead-code penalty does per construct: of two sources with the
  same code the shorter list wins, a neutral edit is no longer equal to its base (a strict island
  refuses it; a drifting island takes it only by its temperature), and reduction drops every
  move that does not pay for itself. An island that has given no new best for `--rebase` minutes
  (10) starts again from the best as reduced. Because the best is now the best for its length,
  the lowest count seen whatever its length is kept apart (`lowest.c`).
- Regression after this round, on the Mac (4 workers, Wine): the redundant-store case
  (start 3, wrong size) exact after 60 variants in 27 seconds, reduced to the one recorded
  edit.

Fourth round: a sweep first, so that an answer of one or two edits does not hang on the seed.
A regression of 15 solved cases over three seeds on the 32-core host (6 workers, 300 s) found ten
cases in seconds whatever the seed, but two whose answer was one probe or two edits were found
on one seed in 2 seconds and missed on the others after thousands of compiles. What the misses
showed:

- A start of the wrong size is left at once. Any right-size result ranks above a wrong-size one,
  so the first right-size variant became the best (23 or 46 differing instructions against the
  start's 21 at two bytes too many) and every island went to it. The answer was one probe from
  the original, and after the first seconds the original was reached only by restarts: two or
  three in 300 seconds, and for the even islands those went to the normalised form (which has
  the original's own probe removed), not to the original.
- A random draw of a probe picks a point, an operand and a constant: about one draw in 3,000
  gave any one of the exact forms.
- The object hash that told "the code did not change" was of the object file, which holds the
  path it was compiled at, so it differed from worker to worker: the bandit's no-change counts,
  the tabu list and the duplicate-object count only worked when the same worker happened to
  compile both. The hash is now of the function's placed bytes and the changed neighbours.
- At a wrong size the instruction diff is of shifted code, and the hot lines from it mean
  little. A base of the wrong size now has no hot lines.
- `typedef unsigned int uint, undefined4;` registered only the last name, so in a file whose
  `uint` came from such a line no global of that type was known to any rule.

The sweep (on by default; `--no-sweep`): before the islands start, the workers list every single
edit of the original, a rule per job, each edit once. The probe rules enumerate (`fn.enumerate`
in permrules): a stored comparison at each insertion point that repeats each test made near it
(`model == 'C'` again in the arm that tested it: the form most recorded probes had) and
compares the two nearest operands with 0 (tier 1), then the other operands and the nearest
constants (tiers 2 to 4); a shift of the nearest constant by the nearest local at each point
(tier 1), of each constant the function uses more than once at every eighth point (tier 1.5) and
at every point (tier 2); address comparisons of the nearest globals (tiers 3, 4); each existing
probe removed. Every other rule is drawn with fixed seeds until 60 draws in a row give nothing
new, its first 15 x weight edits in the first pass and the rest after the probes' second tier.
The singles are compiled in that order (within a tier a fixed shuffle, so a time limit thins
every part of the function alike); after tier 1.5 the probe families (a shift's constant, a
comparison's operand) of which a member improved the score go first. Then pairs: the 40 best
singles that changed the code, one per distinct code, two at a time, and each removal (a probe,
the dead constructs, a normalised form) with the best 300, because a removal alone often scores
no better than the start; then a beam: the 10 best distinct pairs each with one of the best 60
singles as a third edit, the best of those with a fourth, up to six, while a level betters the
one before. A pair is replayed from the two singles' patches; a patch whose context was changed
by the other edit is found with less context (four, then three tokens a side).
With `--time` the singles get a quarter of it, the pairs 15% and the beam 15%. The results are
printed per rule and written to OUT/sweep.tsv. The best becomes the best, the even islands
start from the best distinct results, the odd ones stay on the original, and 15% of all later
jobs (`--origin`) are made from the original with two edits at once. Dead-code and move
penalties apply to sweep results as to any other; `--only` and `--skip` choose its rules.

The end-of-run line now names both orders: `best 23 after 301 s, 1 moves; start 21 size
0x2e4/0x2e2; lowest count seen 22 with 63 moves`, and says when a right-size best has a higher
count than a wrong-size start.

Regression after this round, on the 32-core host with the native compiler (15 solved cases, the
source before each match; seeds 7, 11 and 23; 6 workers, 300 s, five cases at a time). The sweep
does not use the seed, so the three seeds gave the same result and, within a second or two, the
same time in every case:

| case | start | result on each seed | found by | singles listed, compiled |
|---|---|---|---|---|
| CAN receiver | 20 | exact, 4 s | single: if chain to switch | 7,966, 41 |
| CAN receiver 2 | 8 | 1 (the recorded answer's floor) | single | 8,731, 2,521 |
| charge limit | 11 | exact, 7 s | single: repeated store | 7,683, 37 |
| update check (size +6) | 36 | exact, 77 s | pair: a shift of the shared constant 0x4f, the comma probe removed (the first pair tried) | 9,251, 3,174 |
| current measurement | 7 | exact, 36 s | single: dead shift probe | 3,107, 509 |
| discharge limit (size +2) | 21 | exact, 40 s | single: a nearby test repeated | 5,163, 594 |
| byte receivers A and B | 8 | exact, 5 s | single: store inside the test | 4,972 and 4,769, 96 |
| summary update | 4 | exact, 6 s | single: normalise | 6,107, 24 |
| holding registers | 2 | exact, 8 s | single: normalise | 8,216, 26 |
| register write | 2 | 1 (the recorded answer's floor) | single: common temporary | 11,226, 4,973 |
| Modbus task (size -4) | 3 | exact, 3 s | single: dead store | 4,301, 130 |
| CAN sender | 2 | exact, 7 s | single: local inlined | 7,838, 26 |
| protocol task | 29 | exact, 98 s | third edit of the beam: arm swap and two range tests | 2,263, 2,255 |
| 5.6 KB responder | 60 | 46 with 3 moves | third edit of the beam: loop header, common temporary, getter | 9,885, 1,756 |

Before the sweep the same matrix (with and without focus) had the update check missed in all six
runs (26 to 56) and the discharge limit exact on one seed and at 2 or worse on the other two;
the responder was at 45 to 47 by the islands alone with focus and at 38, 49 and 52 without, so
one run of six had found a better source (38 with 6 moves) than the sweep's 46. The cost is time on the cases an island found by luck in a second or two:
the one-edit answers now take 3 to 40 seconds, because the sweep goes through the rules in
turn. The islands after the sweep added nothing in these 300-second runs. With 30 workers and
20 minutes on the responder the whole sweep ran (9,839 singles in 83 s, 2,487 pairs in 20 s, the
beam in 8 s: 46 with 3 moves) and the islands then reached 40 with 5 moves within three minutes
and nothing better after; 110 compiles a second (3.7 per worker, the workers' limit at 205 ms a
compile, 26 ms generation and 36 ms scoring), the parent 0.13 ms per variant. An island still on
the original after a sweep makes two edits at once: its single edits are all in the set of
texts made, and counted as duplicates they had set off the escalation.

Fifth round: a key that can be trusted, other starts, edits outside the function.

- The plain count of differing instructions could be bought. A two-hour run took a 5.6 KB
  function from 53 to 30 with honest edits, but on the way its best was a 38 that retyped a
  temporary to `signed char`: an `sxtb` the image does not have (and other behaviour), while
  about fifteen register ties elsewhere fell into place. Probes had done the same earlier. The
  scorer now also counts the structural difference: the same aligned diff over instructions
  with their registers masked (capstone decodes each distinct encoding once per worker:
  mnemonic without its width suffix and without the flag suffix a narrow encoding forces, the
  operand kinds, immediates, memory offsets, shifts; a literal load keeps its value, a call
  its target; `b` and `b.w` are one kind, as are the two widths of a conditional branch; a
  two-operand ALU form is its three-operand one; sp and pc stay themselves; push and pop lose
  their register lists; a register-to-register `mov` is left out as a tie the allocator did not
  make). An instruction the image does not have, lacks, or has in another place is structure;
  literal pools and pc-relative displacements are in neither count, so what only moved because
  the size changed upstream is not structure. The acceptance key is now exact; right size;
  the structural count; then the rest of the plain count (register ties) with the dead-code and
  move penalties. Scores print as `30 (structure 4, registers 26)`, best.txt lists the structural
  differences, and `--flat-score` gives the old key. Under the new key the bests of that run
  read: the start 53 (7 + 46); the honest 38 before the retype (4 + 34); the retyped 38 (5 +
  33), which is now worse than the one it replaced; a 43 on the way had been 13 + 30; the final
  30 is 4 + 26. On the same function's older source the old key's 46 and 40 are 6 + 40 and 5 +
  35, and the new key's best after 300 seconds is 52 = 1 + 51 against a start of 10 + 50: one
  structural difference left, which best.txt names (`ldrh` in the image where the source's
  code has `ldr`). Scoring costs more (two aligned diffs: 28 ms a variant on that function
  before, 43 to 50 ms now, on a loaded host, beside a 200 ms compile).
  A 20-minute run with the new key from that run's 53 source (6 workers, dead-code penalty 3)
  ended at 47 = 1 + 46 with the same one structural difference left; 329 variants that retyped
  something to `signed char` were compiled, 16 were kept by an island for a while, none became
  a best.
- Other starts. `--seed-source FILE` (repeatable) gives another complete text of the same
  source file: the function as a sibling release has it, an older commit, a hand variant. It is
  compiled and scored at the start and kept when its count is within `--seed-factor` (2) times
  the best start's plus 5; a kept one gets an equal part of the sweep's time and its own islands
  (the islands go to the starts in turn, every second island of a start strict), and every
  result that descends from it has a first move `seed` naming the file. First use: a function
  whose committed source scored 2 with a comma probe, and a probe-free variant with a helper
  that scored 4. With the variant as a seed source the sweep found an exact source in 51 seconds
  on 6 workers: the variant plus one redundant store, a single edit that years of hand and
  random search from the probe source had not reached (to be confirmed by match.py).
- Edits outside the function body (`permmacro.py`, registered at the end of permrules):
  `macro_body` rewrites the body of a function-like macro the function invokes, so every
  invocation at once (do-while(0) against a plain block, two statements against a comma
  expression, `x[i++] = v;` against `x[i] = v; i++;`: these keep the meaning; statement order,
  a block-local temporary for a parameter against its direct use, a parameter's parentheses:
  marked 'type', since what they do depends on the arguments at every invocation in the file);
  `macro_expand` writes one invocation out as the preprocessor would, so that site can be
  edited alone, and `macro_fold` turns a run of tokens that is exactly an expansion back into
  the invocation; `file_order` swaps two file-scope declarations of globals the function names
  ('same' for two extern declarations; 'type' when a definition is among them, because the
  order of definitions is the order of the objects in their section: the scorer takes a data
  section's address from one symbol's map row and the others by their offsets in the variant's
  own section, so a reordered section shows as wrong literals, but the object layout itself is
  only checked by match.py).
- The end of a run lists the time and compile count of each new best ("is it still
  descending?") and writes OUT/yield.json (per rule: compiles, how many changed the code,
  improved an island, entered a best) and OUT/weights.json (the run's weights scaled by each
  rule's rate of improvements against the average, a quarter to four times), which a later run
  loads with `--weights`.

Regression with the new key (the same 15 cases, seeds 7, 11 and 23, 6 workers, 300 s, one case
at a time on a host busy with other runs; the three seeds agree but for the last row, the
sweep not using the seed).
No case that was exact is lost and the times are within a second or two, three on the largest
unit, of the fourth round's:

| case | start (structure + registers) | result | fourth round |
|---|---|---|---|
| CAN receiver | 20 (0 + 20) | exact, 4-5 s | exact, 4 s |
| CAN receiver 2 | 8 (1 + 7) | 1 (1 + 0) | 1 |
| charge limit | 11 (0 + 11) | exact, 7 s | exact, 7 s |
| update check (size +6) | 36 (20 + 16) | exact, 78 s | exact, 77 s |
| current measurement | 7 (0 + 7) | exact, 37-38 s | exact, 36 s |
| discharge limit (size +2) | 21 (17 + 4) | exact, 42-43 s | exact, 40 s |
| byte receivers A and B | 8 (0 + 8) | exact, 5-6 s | exact, 5 s |
| summary update | 4 (4 + 0) | exact, 6 s | exact, 6 s |
| holding registers | 2 (2 + 0) | exact, 9 s | exact, 8 s |
| register write | 2 (2 + 0) | 1 (1 + 0) | 1 |
| Modbus task (size -4) | 3 (2 + 1) | exact, 3 s | exact, 3 s |
| CAN sender | 2 (2 + 0) | exact, 7 s | exact, 7 s |
| protocol task | 29 (29 + 0) | exact, 100 s | exact, 98 s |
| 5.6 KB responder | 60 (10 + 50) | 52, 51, 51 (structure 1), 3 moves | 46 (6 + 40), 3 moves |

The responder is the one case where the two keys choose differently: of the start's ten
structural differences the old key's 46 keeps six, the new key's 51 or 52 one, at a cost of ten
or eleven register ties.

Sixth round: the way out of a probe-matched source. A function whose committed source scored 2
with a comma probe was three steps from exact: the probe removed (2 to 88), three stores through
a `set(i, v)` helper (88 to 4), one clear before a set (4 to 0). From the committed source the
sweep found nothing in 9,533 singles and 2,421 pairs: the first step makes everything worse, the
second is a helper at some of its sites only, and the third pays only after both.

- Bases without dead code. Each dead construct of the given source is removed (each probe, the
  comma probe of a condition, each dead or repeated store, alone; each kind together; all
  together), and every removal that compiles and changes the function's code is a start with
  its own sweep of singles, pairs and beam, whatever it scores (`--debt-bases`, 16; together
  they get `--debt-share` of `--time`, 0.3). Such a sweep is not there to put probes back: its
  first stage is the first pass of every rule, then the site subsets, and the probe tiers last.
- Site subsets. A rule that sends several sites through one helper or local (index_helper,
  setter, getter, addr_helper, expr_helper, local_cache, cache_group, arg_local, uniform) was
  tried at one site or at all. The sweep now lists each such edit at every subset of its sites
  too (all subsets up to 8 sites; of more, every run of neighbouring sites and a fixed sample of
  120), found from the edit itself: its spans that replace code in the function's body are the
  sites, what it inserts and what it writes outside the body stays. The subsets of the edits
  whose whole form scored best go first.
- Again from the best. When a sweep's best has half its start's count or less, the sweep is
  made again from that best (`--resweep`, one level, half the time).
- In the structural count an instruction that is only displaced (two neighbours in the other
  order, or one moved up to 16 places with the rest in order) is one difference, not a missing
  one and an extra one: the flat count had made the better source of that function (two
  transpositions, 4) look worse than the probe-matched one (2).
- A folder of starts: `--seed-dir DIR`, by default `<tree>/variants/FUNC`, each `*.c` in it a
  seed source; and every run leaves OUT/variants/, the best text of each distinct residue (set
  of structural differences left), as candidates for that folder.

Regression with these defaults (the same 15 cases and conditions as the fifth round's; the rule
set had meanwhile grown from 84 to 131 rules, and with the site subsets a function lists 17,000
to 18,000 single edits where it listed 9,000 to 10,000). No exact case is lost; the times are a
few seconds longer:

| case | start (structure + registers) | result | fifth round |
|---|---|---|---|
| CAN receiver | 20 (0 + 20) | exact, 7-8 s | exact, 4-5 s |
| CAN receiver 2 | 8 (1 + 7) | 1 (1 + 0) | 1 (1 + 0) |
| charge limit | 11 (0 + 11) | exact, 7-8 s | exact, 7 s |
| update check (size +6) | 36 (18 + 18) | exact, 83-84 s | exact, 78 s |
| current measurement | 7 (0 + 7) | exact, 42-43 s | exact, 37-38 s |
| discharge limit (size +2) | 21 (9 + 12) | exact, 49 s | exact, 42-43 s |
| byte receivers A and B | 8 (0 + 8) | exact, 6 s | exact, 5-6 s |
| summary update | 4 (2 + 2) | exact, 6 s | exact, 6 s |
| holding registers | 2 (1 + 1) | exact, 9-11 s | exact, 9 s |
| register write | 2 (2 + 0) | 1 (1 + 0) | 1 (1 + 0) |
| Modbus task (size -4) | 3 (2 + 1) | exact, 4 s | exact, 3 s |
| CAN sender | 2 (1 + 1) | exact, 7 s | exact, 7 s |
| protocol task | 29 (26 + 3) | exact, 102 s | exact, 100 s |
| 5.6 KB responder | 60 (7 + 53) | 52 on each seed (structure 1), 3 moves | 52, 51, 51 (structure 1) |

(Some starts read differently from the fifth round's because a displaced instruction now counts
once.) And the function this round was built for, from its committed probe source with the
compiler of its own tree, 6 workers, `--time 2400`: exact after 676 seconds, three moves. The original's sweep found nothing (18,297 singles in
458 s, 2,428 pairs in 60 s, none better than the start). The first base, the comma probe
removed (start 88 = 30 + 58 at the wrong size), found in 108 s of singles a `set` helper for the
buffer at 11 of its 28 stores: 4 = 2 + 2, its site subsets tried first because the whole helper
had improved the base. The sweep made again from that 4 found a clear before a set, a single
edit, after 26 s: exact. (Another exact source than the one found by hand, which has the
helper at three stores and the clear at another byte; several exist.)

Seventh round: one candidate against two builds. The same function exists in two releases
built with different compiler versions, from what is very probably one source. A candidate that
fits one image by luck will rarely fit the other, so `--also CONFIG:SRC[:FUNC]` gives a run a
second target: another tree's exhume.toml, its text of the source file and the function's name
there. Each target has its own compiler (a `permuter.env` in the tree's root with
EXHUME_ARMCC_LINUX and EXHUME_ARMCC_MAPPINGS, then `--also-env KEY=VALUE` after its `--also`,
over the environment), its own image and scorer, in every worker.

- The two texts need not be equal. A candidate is a move list; each move is replayed on the
  other tree's text as a patch, where its context is found. A candidate with a move that has no
  place there is scored on the primary alone: the other text stays as the candidate's base has
  it, with that base's result, and the score is marked `[target 2: edit not carried]`. A whole
  text (a seed source, a resumed island) is carried hunk by hunk, and the mark says how many
  hunks had a place.
- The key is exact on both; the right size on both; the structural counts summed; the register
  counts summed (`--also-weight` scales the second); then the penalties. Scores print as
  `29+29 (structure 0+0, registers 29+29)`; best.c and best.also1.c are the two texts.
- The second compile is made only when the primary result is within `--also-margin` (6)
  differing instructions of its base's, and a sixth of that in structure, at a size no worse.
- The start line is the check of the set-up: the empty edit must give each tree its known
  score.

Checked on a 5.6 KB function that two releases share (compilers 5.06 update 3 and update 6):

- A function exact in both trees, broken in copies of both by the same edit (two statements in
  the other order: 6+6, structure 4+4): the two-target run restored exact on both after 4
  seconds, one move, and wrote both texts.
- The two trees' own best sources of the function had drifted apart: each tree's search had
  left its own edits in (seven hunks between them, one of which is the releases' real
  difference, a constant). From such a pair an edit near a drifted hunk does not carry, and a
  whole text carries only in part. So the run was made from one source under both: the first
  tree's text of the function put into the second tree's file, with the constant. Then every
  edit of the function carries (4% of all variants did not: edits at file scope, where the
  files differ).
- What it showed is that for this function the two compilers answer alike. The first tree's
  earlier descent (33, 32, 30, 29, 26 differing instructions) scores 33, 32, 30, 29 and 26 under
  the second compiler too: those steps were not fits to one image, and the second tree gained
  three instructions from the first tree's source. Of 3,174 swept results at the right size
  under both, 3,170 had the same counts and the same structure on the two builds; the four that
  differed were pairs with a probe. From the sources as the trees had them, of 83 single edits
  that changed a count, 76 moved both the same way, 7 moved one only, none moved them opposite
  ways. A second build by a near compiler version therefore adds little discrimination here:
  it confirms, it does not separate.
- Cost: a quarter of the variants were compiled for both (25%), 72% were too far on the primary
  to be worth it. A 30-minute run on 6 workers from 26+26 (29,167 variants: 7,889 singles and
  2,301 pairs of the original, a base without dead code, the islands) found nothing better.
- The 15 solved cases without `--also` are unchanged.

Not done: hot lines from the second target (the focus uses the primary's); a cut file with a
second target.
