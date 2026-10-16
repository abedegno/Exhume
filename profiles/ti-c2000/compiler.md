# TI C2000 CGT (cl2000): what matching taught

The evidence is of two kinds. Most rules come from a firmware for a TMS320F2806x with the FPU
that I matched whole in a private project: several hundred functions, every word of the image
rebuilt from source by the gate, with no blobs, and every function in C except a few files that
were assembly in the original. Its names and addresses stay out of this page, so each rule says
what I tried and what came out, in made-up names. The rest are synthetic probes anyone can
rerun: the firmware in tests/fw, built by tests/synth.py, and the short files quoted below,
compiled with TI's public compilers.

Words are 16 bits, and addresses count words. `@x` is a direct-page access (`MOVW DP,#page`
then a 6-bit offset); `*+XARn[k]` goes through an auxiliary register.

## Flags

- `-v28 -ml -mt --float_support=fpu32 -O1`, COFF ABI. One function matched only at -O1, not at
  -Ooff, -O0, -O2 or -O3. Others matched at -O1 to -O3, and `-ms` changed none of them.
- `--gen_func_subsections=on` (short form `-mo`) puts each function in a section of its own, so
  the tools can link each at its address. The original build did not use it: the image held
  functions that nothing calls or points to, which a link of per-function sections would have
  dropped. I saw no change in code from it.
- Per-file options did not explain any hard function. For six hard functions I tried
  `--opt_for_speed=0..5`, `-mf`, `--fp_mode=strict/relaxed`, `-g`, `--symdebug:none/dwarf/skeletal`,
  `--optimize_with_debug`, `-O2` with and without `-mf`, `-ms`, `-mn`, `-mi`, `-ma`, `-oi0`,
  `--auto_inline=0`, `--call_assumptions=0..3`, `--no_rpt`, `--no_fast_branch`,
  `--disable_dp_load_opt`, `--fp_reassoc`, `--sat_reassoc`, `--float_operations_allowed=all`,
  `--plain_char=signed`, `--silicon_errata_fpu1_workaround` and `--gen_func_subsections=off`.
  None removed a difference; most changed nothing and the rest made it worse. Every one of those
  functions later matched in plain C at the default flags. A file's own options go on a
  `/* opts: */` line (they replace the defaults) or a `/* cflags: */` line (added to them).

Section names with `--gen_func_subsections=on`:

| What | Section |
|---|---|
| a function | `.text:_name` |
| a function with `#pragma CODE_SECTION(f, "ramfuncs")` | `ramfuncs:_f` |
| a function's switch tables | `.switch:_name` (one section per function, all its tables) |
| a const object | `.econst:_name` |
| global and static variables | `.ebss` (one per file, no subsections) |
| initialised data records | `.cinit` (one per file; library members also use `.cinit:__name`) |

Every subsection is marked `.clink` (conditionally linked); linker.md has what that means for a
link.

## Fingerprinting the version

The version matters: neighbouring releases of cl2000 compile most code alike and differ in a few
shapes, and the firmware needed one exact release line. These are the tells I used, in the order
that worked.

- **The runtime library first.** `tools/c28libfp.py` searches the image for every member of a
  `rts2800_fpu32.lib`, section by section, with the bits relocations write masked. `--libs GLOB`
  runs it for every installed library and prints how many sections and words each places. On the
  synthetic image, linked with 16.9.6's library, 16.9.0 to 16.9.8 place all 9 sections
  (366 words); 15.12.x, 16.3 and 16.6 place 8 (330), 16.9.9 and later 8 (336), 15.9.0 only 4.
- **The library can be older than the compiler.** In the firmware the members were word for word
  those of an earlier release line than the one whose compiler built the code. The project
  linked a library it had kept while the compiler moved on. So the library's range bounds the
  build date, not the compiler. A maths member that changed in one point release closed the
  range from above.
- **Defect fixes.** An interrupt handler that calls a function ends, from 15.12.2 on, with
  `SETC INTM|DBGM` before `POP RB` (TI's defect SDSCM00039432). 15.9.0, 15.12.0, 15.12.1 and
  16.3.0 omit it and the handler is a word shorter. The synthetic `timer_isr` in
  tests/fw/helpers.c shows it: `match.py helpers --all-cgt` fails it with exactly those four.
- **Code shapes that changed.** A struct member more than 255 words into a struct, reached
  through a pointer, is `MOVL ACC,XARn; ADD ACC,#k << s; MOVL XAR4,ACC` up to 15.12.6 and in
  16.9.0 to 16.9.4, and `MOVL XAR0,#k` with `*+XARn[AR0]` in 15.12.7 and from 16.9.5. This probe
  shows it:

      typedef struct { float a; float line[300]; float b; float c; unsigned n, w; } blk_t;
      float big_step(blk_t *p, float x)
      {
          float y;
          p->line[p->w] = x;
          if (++p->w >= 300) p->w = 0;
          y = p->b * x + p->c;
          p->c = y;
          p->n++;
          return y + p->a;
      }

  In the synthetic firmware, 16.9.9, 16.9.10, 16.9.11 and 18.1.8 start a 32-bit sum with
  `MOVB ACC,#0; MOVL XAR7,ACC` where 16.9.8 and earlier write `MOVB XAR7,#0`. 16.6.0 and
  earlier set up a call's arguments in the other order (`MOVL XAR4,XAR1` before `MOVB AL,#4`).
  A word-copy loop with a 32-bit count (`copy_words` in tests/fw/appmain.c) keeps the count in
  ACC and copies through P from 16.3 on (`MOV PL,*XAR7++; MOV *XAR4++,P; TEST ACC`); every 15.x
  compiles it another way. So the synthetic sources alone place their compiler in 16.9.0 to
  16.9.8 or 17.x.
- **Neighbouring versions break different functions.** With the whole firmware in place, each
  release near the right one failed its own handful: one broke three functions, the next two
  would not build a TI support file unchanged, another broke about fifteen. The right release is
  the one that fails none, and only the whole tree shows that.
- **One file "needing" another compiler is evidence about the whole tree.** I first matched the
  tree with a 15.12 compiler. A few files then matched only with 16.x, and for a while I gave
  each its own `/* cgt: */` line, on the theory that the project had rebuilt only some files
  after an upgrade. The true answer was simpler: 16.9.6 built every file, once the seven
  functions I had bent to fit 15.12 were written plainly again. A per-file compiler line is a
  tool for finding this out. As the final state of a tree it needs evidence that no single
  compiler builds everything.
- Run `match.py SRC --all-cgt` (or the whole gate under each candidate) on every matched file
  whenever a function resists: a function nothing matches may be the compiler's, not the source's.

## -O1: what the code says about the source

### What the file knows

- **Earlier callees.** Within one file cl2000 uses what it learnt from a function defined above
  the caller. Parameters the callee never reads are not loaded. The data page the callee leaves
  set is reused after the call. A callee that never returns is laid out last, with no branch
  back. A callee that does not touch XAR6 lets the caller keep a value there. This works only
  for callees above the caller, so a scratch file must keep the functions in the original's
  order. I had a caller that reloaded the data page after a call; it matched only once the
  callee was defined below it. Two functions matched only as files of their own, because the
  file around them changed what they knew.
- **A small callee defined later in the file is inlined at -O1.** The image kept such a call, so
  the two were in different files.
- **File globals against externs.** Globals defined in the same file share one `MOVW DP` while
  they are in one 64-word page. An `extern` global gets its own `MOVW DP` at every access,
  because the compiler cannot know its page. Members of one struct share one too. So a run of
  accesses without a DP reload means the variables were defined in the function's file, or are
  one struct.
- **Stores to a file's own globals keep source order.** Stores to struct members and to externs
  are moved: a store of 0 sinks, a `MOVB @x,#imm` store rises. When a run of stores in the image
  keeps source order under one data page, the variables are the file's own. Several functions
  that a page struct left 7 words off matched at once with the variables defined in the file.
- **Same-file globals are constant-propagated.** A global the file defines and has just stored is
  folded into a later expression. Code that stores a constant and then divides the global at run
  time (a call to the library's float division) reaches memory defined elsewhere.

### Scheduling

- **A local copied from a struct member moves past calls.** `d = s.f;` is moved down to its use,
  past a call and past stores to other objects. It stays where it is when a branch, a store into
  the same struct, or a store through a pointer comes between. Copies of plain scalars and of
  array elements are not moved. A function whose image loaded four values before a division
  call matched only when those values were read in one nested `__fmin` expression written before
  the division's statement.
- **Two names for one object.** The compiler treats two names for one address as two objects
  (an `extern float x` and a struct member at the same address). After a store through one name
  it reads the value back through the other. Where the image stores a value and then reloads it
  for a compare, the original used two names. One ramp matched only with the add through a plain
  extern and the compare through the struct.
- **A plain pointer beside a volatile array.** A function that builds a frame in a `volatile`
  array was 34 instructions off. The image formed `base + k` in XAR7 for two stores, where I got
  `MOVB XAR0,#k; MOV *+XAR4[AR0]`. Hundreds of forms of those stores changed nothing. Writing the
  first stores of each of those blocks through a local pointer that is not volatile
  (`buf = frame; buf[n++] = ...`), with the rest kept as `frame[n++]`, matched. Plain stores can
  be scheduled across each other, so the next pair's index constants load early, and XAR0 and
  XAR1 are both busy. When a store pattern looks like register pressure that no local change
  explains, look for stores the original wrote through a pointer that is not volatile.
- **Volatile reads and stores are barriers.** A volatile read keeps the loads before it in place.
  A volatile read at an assignment (`x = *(volatile float *)&s.f`) turns a register copy into
  the image's reload. Stores written through `*(volatile int *)&g` stay after reads that would
  otherwise sink below them. A non-volatile extern store can move ahead of a volatile one. A
  buffer that DMA fills must be `volatile`: then an LSR moved behind two MOVXI, the last 2 words
  of a function. A word read once per bit (`MOV AL,@x; LSR AL,1; OR AL,@x`) is volatile. A
  non-volatile word is read once.
- **Where one object ends.** The scheduler treats stores to members of one object as possibly
  ordered and stores to different objects as free. Where a page struct crosses into the next
  page, the split matters: functions matched only with the next page's words as members of the
  same struct.
- **Init functions**, a long run of constant stores to several objects, matched when I wrote the
  stores to each object as a group in the image's order for that object, the groups one after
  another. The scheduler interleaves the groups by itself (one went from 88 differing words to 0
  in one step).

### Operands and registers

- **The order of operands in multiplies is set by the source.** For `a OP b` cl2000 writes
  `OPF32 Rd, Rb, Rc` with Rb holding b and Rc holding a, for MPYF32, ADDF32, MAXF32 and MINF32.
  SUBF32 keeps the order (Rb - Rc). So the image's register order gives the source order of each
  operator separately, nested expressions too. `(int)(a * b)` on integers loads b first, and so
  does `a + b` of two 16-bit struct members. With one operand in memory the rule changes: an
  address-taken scalar `k` gives `MPYF32 Rk,Rk,RD` for `D * k`, where an array element, a struct
  member or a register copy gives `MPYF32 Rk,RD,Rk`.
- **Float if/else layout and CMPF32.** `if (a < b) A; else B;` compiles to `CMPF32 a,b; B A,LT`,
  with B in the fall-through and A after it. The CMPF32 operand order follows the source. Writing
  every test of one function from the image's branch condition fixed most of its float tests at
  once. A climb that inverted one test at a time did not find them, because inverting one test
  changes the cross-jumping of the shared tails.
- **`fmin` against an explicit if.** `x = x + c; if (x > m) x = m;` gives MINF32 and keeps both
  stores, and stops the compiler sharing loads across the statements. `x = __fmin(x + c, m)`
  drops the first store. For a maximum, `if (x < g) x = g;` still compiles to MAXF32, but the
  other arm's `x = g` becomes a reload and equal blocks are cross-jumped. `__fmax` gives a
  register copy (`MOV32 R0H,R1H`) instead. A clamp written `m = y; if (m < K) m = K;` and
  `if (y < K) m = K; else m = y;` give the same instructions with other registers. Try both
  wherever a MAXF32 or MINF32 sits near a register difference.
- **One statement per line.** `b = 0; a = 0;` on one line came out of the optimizer as
  `b = a = 0` (`--optimizer_interlist` shows it), and the code generator then copied the
  register (`MOVB AH,#0; MOVZ AR6,AH`). On two lines the stores stayed separate
  (`MOVB XAR6,#0; MOVB AH,#0`), as the image had them.
- **`x = y = 0` merges.** A chained assignment shares one register for the value. The image's
  separate stores of 0 come from separate statements, in the image's order: with the other flag
  first the allocator swapped AH and AR6 and the code grew by 6 words.
- **Reusing a local for the return value renumbers the FPU registers.** Under 15.12, one function
  matched only when its last lines were `if (c) t = p; else t = q; return t;`, with `t` a local
  used earlier for another value. Two direct returns renumbered the FPU registers of the whole
  function. Reusing a local changes its live range, and every other variable's colour follows.
  The same holds for a parameter reused as the running index of a loop: the register follows all
  of a variable's uses.
- **The initialiser order sets the register numbering.** Locals that start at 0 can share a
  register. One function's result kept its own register only when `r = 0.0f;` came before the
  other zero assignments, with the declarations written apart from the initialisers
  (`float a, b, r; r = 0.0f; a = 0.0f; b = 0.0f;`). In another, the order of two zero
  initialisers fixed the register numbers of the whole function, while all 24 orders of the
  declarations themselves made no difference.
- **Register colouring is global.** One expression can renumber the FPU registers of a whole
  function (dividing before multiplying, in one case). A draft with many register differences
  can be one expression away, so a search should score whole-function variants and not stop at
  the first local fix.
- **Separate locals for separate lifetimes.** In one function a value saved before an update and
  passed to a call had to be a local of its own: reusing the local that later took the call's result
  moved two values after the call into other registers. In another function, a temporary of its own
  (`e = b * k; ... e = c * e; g = e;`) put a product in the image's register; one expression reused
  another operand's register. Splitting one variable in two, or giving a value its own local, moved
  several register cases that no statement order did.
- **Forms tuned for the wrong compiler.** While the tree was on 15.12, seven functions took weeks
  of contortions to come near: volatile casts, reused locals, odd orders. Under the right
  compiler their plain drafts were EXACT, and the contortions had to be undone, because they
  were wrong for 16.9.6. When a function needs an unnatural form, try the neighbouring compilers
  before tuning the source further.

### Smaller shapes

- A store of 0 to a non-volatile global is `MOVB AL,#0; MOV @x,AL`; to a volatile one it is
  `MOV @x,#0`. A non-volatile store can still be `MOV @x,#0` when AL is busy, so this alone does
  not prove volatile.
- An `asm(" ...")` statement is followed by `SPM #0` before the next call, so EALLOW, EDIS, EINT
  and ERTM macros show up as `SPM #0` after them.
- Interrupt functions save `ASP; PUSH RB; PUSH AR1H:AR0H; MOVL *SP++,XT ...; MOV32 *SP++,STF`,
  then `SETFLG RNDF32=1,RNDF64=1; SPM #0; CLRC OVM|PAGE0; CLRC AMODE`, and the reverse at the end
  (tests/fw/helpers.c's `timer_isr`).
- Switch tables: `MOV AH,@AL; ADDB AH,#-first; CMPB AH,#count-1; B default,HI; ...;
  MOVL XAR7,#table; ... MOVL XAR7,*XAR7; LB *XAR7`, 32-bit entries in `.switch:_f`. A 9-case
  switch at -O1 compiled to compares, a 12-case one in tests/fw/loopctl.c and a 30-case one to
  tables. Case blocks come in source order. A block shared by several labels stays where it is
  written.
- An unused local still costs `ADDB SP,#2`, and so does a block-local temporary the compiler
  keeps in a register. An empty frame in the image means the source had such a local.
- `ZERO R0` before a return means the returned value is a known 0 on that path: the source
  returns a local the path set to 0.
- `CMP AL,#-8192; SB LO` is `x < 0xE000U` on an unsigned x: the constant is shown signed.
- `INC @x; MOV AL,@x; ADDB AL,#-1; CMPB AL,#20` is `if (x++ > 20)`; `if (++x > 20)` has no ADDB.
- A uint16 converted to float from a register (a local, or a value just tested) is
  `MOVU ACC,AL; MOV32 R0H,ACC; NOP x4; UI32TOF32`; from memory it is `UI16TOF32 R0H,@x`.
- `(a << 8) | (b & 0xFF)` in one expression becomes `MOVB AL.MSB,AH`. An image that shifts, adds
  to the index and ORs (`MOV ACC,*<<8; ADDB XAR0,#1; ... OR AL,AH`) was
  `t = buf[i++] << 8; t |= buf[i] & 0xFF;`.
- Bit fields and masks compile alike, except that a bit-field test decides which arm of an `if`
  falls through. A one-bit field assigned on a plain word is cleared and set in memory
  (`AND @x,#~bit; ... OR @x,AL`); on a volatile word it goes through a register.
- `x |= 1 << n` under an `if` can be if-converted into `x |= (cond) << n`; `x = x | (1 << n)` is
  not.
- `memset(&s, 0, sizeof s)` with a constant size is `RPT #n-1 || MOV *XAR4++,#0`.
- Integer arguments go in AL, AH, then AR4 (the third 16-bit argument); pointers in XAR4, XAR5.
- Arrays get the low stack slots and scalars the high ones, whatever the declaration order.
- A pointer to a global is folded back into direct accesses. A base kept in XAR2 across a loop
  in the image came from an absolute address cast (`rx = (unsigned *)0xA640`), which the
  compiler cannot relate to the array. Casting through an integer is folded too.
- Reading a peripheral register through a word array view
  (`((volatile unsigned *)&Regs)[k] & 0xFF`) kept the base register the image used, where the
  named member reloaded the data page.
- A branch target one word early (on a shared `MOV @x,#0` rather than past it) means the source
  had an `else x = 0;` that the compiler merged with an equal store.
- Ghidra misreads some float constants. `MOVIZ` loads a register's upper half and zeroes the
  lower half, and a constant whose lower half is 0 needs no `MOVXI`. Ghidra keeps the lower half
  the register held before: `MOVIZ R1H,#0x41C8` after 0.1 (0x3DCCCCCD) was in R1H is 25.0, and
  Ghidra shows 25.1. Take the constants from the disassembly.

## .ebss layout

- **It isn't source order.** cl2000 lays out a file's uninitialised and initialised globals in
  `.ebss` by class: 16-bit scalars first, then 32-bit scalars (long, float), then aggregates
  (structs and arrays). A one-word struct or array sorts with the 16-bit scalars. An aggregate
  under 64 words does not cross a page, and one over 64 words starts on a page. Inside each class
  the order is a permutation of the definition order. In tests/fw/loopctl.c the three 32-bit
  scalars come out in reverse order. In a probe file of n `unsigned int z0 ... z(n-1)` and one
  function that increments each, the variables in address order are, by definition index,
  1 0 2 (n = 3), 2 3 0 1 (4), 3 4 1 2 0 (5), 4 5 3 1 2 0 (6) and 5 4 7 6 1 0 3 2 (8), under
  15.12.3 and 16.9.6 alike.
- **It is a function of the whole file.** The permutation follows the sequence of types and the
  identifier spellings of everything the file declares: functions, externs and headers.
  Initialisers and the variables' own names did not matter. Adding a function or a declaration
  moved variables. Renaming a callee (`sub_...` to its real name, same address, same prototype)
  moved several of the file's variables, and 43 words differed. A header change with no change
  to any code moved variables too.
- **The fix is a solver.** `tools/c28ebss.py` compiles the file, reads where each variable
  landed, and permutes the definitions between `/* ebss-begin */` and `/* ebss-end */` until each
  lands at its address. Because the shuffle depends on the slots, not on which name is in them,
  one round usually does it; when the spellings move it, swaps finish the job. Rerun it after any
  rename, any new declaration or header, and any function added to or taken out of the file.
- **Define the variables in the file.** It is worth the solver: a file's own variables share a
  data page and keep their store order, which page structs and externs do not (above). The
  `.cinit` records then come out in the image's order by themselves, since a file's records
  follow its `.ebss` order.
- `unsigned int buf[100] = {0};` makes a `.cinit` record of one word (only the initialised
  element).

## -Ooff frame layout

At -Ooff the frame layout changed between 16.3 and 16.9.0. 15.12.x and 16.3.0 store the 16-bit
arguments first and put a 32-bit local below them. 16.9.0 and later give the 32-bit values (a
pointer argument, a long local) the top slots and the 16-bit values the slots after them. 16.9
also reloads a pointer argument from the frame into XAR7 for each store through it, where 15.12
stores through XAR4, which still holds it. A probe:

    unsigned int fold(const unsigned int *p, unsigned int n, unsigned int seed)
    {
        unsigned long acc;
        unsigned int k;
        acc = seed;
        for (k = 0; k < n; k++) acc += p[k];
        return (unsigned int)(acc ^ (acc >> 16));
    }
    void clamp_word(unsigned int *x, unsigned int lo, unsigned int hi)
    {
        if (*x < lo) *x = lo;
        if (*x > hi) *x = hi;
    }

15.12.7 puts `n` and `seed` at `*-SP[3]` and `*-SP[4]` and `acc` at `*-SP[6]`; 16.9.0 puts
`acc` at `*-SP[4]` and the two at `*-SP[5]` and `*-SP[6]`. In the firmware, a file of -Ooff
helpers had four functions that no source form matched under 15.12; they were EXACT under 16.9
at -Ooff unchanged. At -Ooff a comparison loads its right operand first: `a >= b` compiles to
`MOVL ACC,b; CMPL ACC,a`.

## What can't be C

- A function that writes registers before it allocates its frame (`ADDB SP,#4` after an `EDIS`),
  or that has no epilogue after its last jump (a boot loader's `LB` to the application). No TI C
  compiler places a prologue after body code, or drops the epilogue of a function whose last
  statements are asm. Write it as assembly, with a header comment giving that evidence.
- Boot-ROM style code: `RPT || MOV` memory clears, `POP ST1`, `POP ST0`, `LB` to an absolute
  address.
- TI's own assembly support files: the branch at the code start address, the microsecond delay
  loop, the CSM password words. They assemble from TI's text with the same cl2000. Every other
  function in the firmware was C, TI's default ISRs included (`asm(" ESTOP0"); for(;;);` ends
  each).

## Searching for the source

- `tools/c28batch.py` compiles many renamed copies of one function in one cl2000 run (40 per
  file by default) and links each at the function's address with the native linker. With jobs
  in parallel that scored about 10 variants a second on the firmware, so a few thousand random
  combinations of choice points took minutes; `--climb` does coordinate descent and `--pairs`
  two-point moves. 6000 random variants of statement orders stopped one function at 36 differing
  instructions; adding the two return forms as one more choice point found the match in the next
  climb.
- Batch scores are a guide. The copies move the file's `.ebss`, so data addresses are normalised
  away, and then two blocks that swap two variables score as equal (`--strict --own LO:HI` keeps
  the addresses outside the file's own range). The same text can come out with other registers
  as a renamed copy than under its own name. In one exhaustive run a variant that match.py found
  EXACT was not ranked best. Check the best few under the real name with match.py.
- Read the image twice before searching. Two "near misses" were misread semantics (a statement
  outside the inner if; a return after the if), not compiler choices.
- When only the order of loads differs, try each access path the headers offer for the same
  memory (struct member, array element, volatile or not, a plain pointer) before more expression
  shapes. More of the hard functions were settled by which object an access names than by the
  statements.

## Running the tools under Wine

- Each Wine prefix has one wineserver, which serialises its clients. The tools give each job a
  prefix of its own, an APFS clone (`cp -Rc`) of the master prefix held with an flock, so
  compiles run side by side. The cloned prefix costs no disk until it changes.
- A server is started with `wineserver -p60` and no pipes, then a first `wine cmd /c exit`, also
  with no pipes. Otherwise Wine's services keep the first tool's output pipe open and the tool
  seems to hang. The servers exit 60 s after their last client; the gate stops them at the end.
- A Wine tool hung once (lnk2000). The tools stop the prefix's server after a timeout
  (`wineserver -k`), which ends the hung process, and try once more.
- A compile under a warm server takes about a second; ofd2000, dis2000, lnk2000 and hex2000 run
  natively from an 18.12 or later CGT.
