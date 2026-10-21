---
name: match-file
description: Use when decompiling one C source file of an old DOS program (or of an armcc5 or TI C2000 firmware image) into C that recompiles byte for byte to the original - the per-file loop of writing C, compiling in headless DOS, reading instruction diffs and restructuring until every function matches and verify passes. Also the brief to give an agent that matches a file.
---

# Match one C file

You are rebuilding one original source file (one code segment) as C that the original compiler turns into the same bytes. Paths are relative to the Exhume checkout; run tools from the project root so `src/FILE.C` resolves.

## Before writing

- Read the profile's compiler.md (for Borland, `profiles/borland-tc101/compiler.md`). It lists switches and what the compiler's output reveals about the source; most of the answers are there.
- Read the target table `targets/SEG.tsv`: base, size, org, one row per function (name, listing name, offset, size).
- Pick the closest matched file as a worked example (same kind: overlay or resident) and reuse its struct and global declarations where your code touches the same data.
- symbols.tsv maps names to addresses. Use its name for any address already there. Use the sibling build's names (`tools/sibling.py NAME`) for callees and globals where they exist. If evidence says a name in symbols.tsv is wrong, report it; do not diverge.

## Loop

1. Start the file with `/* target: SEG */` and `/* opts: ... */` (for UW2: `-mm -1 -G -O -Y -d`). Change switches only when the bytes demand it, and say why.
2. `python3 tools/match.py src/FILE.C` compiles in DOS (about half a second in emu2, a few seconds in js-dos) and prints each function as MATCH, bytes differing, or size against the table's. Add `--dis NAME` for an instruction diff of one function; `--no-build` re-compares the last build.
3. Work function by function, smallest differences first. A length difference shows as the first instruction that differs; fix that one and rebuild. Batch edits: every build costs time and, in a queue, budget.
4. When the instructions agree but registers or reloads differ, restructure (statement order, nesting against early return or continue, declaration order for SI/DI, `x += y` against `x = x + y`, block-scoped locals). The compiler.md lists which source shapes give which bytes.
5. Stop at `WHOLE SEGMENT MATCHES`. Then `python3 tools/verify.py src/FILE.C` (without `--update`) must end `-- fixups and data verified`.

## When the search stalls

A function or unit that stays a few instructions off after many variants is usually telling you
the context is wrong, not that the right statement order is still out there. On two firmwares
and a DSP image the long tails were broken by context; local sweeps of thousands of variants of
one function recovered nothing once the instructions agreed but registers or schedule did not.
Stop varying the function and audit, in this order:

1. **File boundaries.** Is the unit one original source file, with the right first and last
   function? The compiler's knowledge of callees in the same file (registers kept live across
   the call, frame and push size, inlining) changes callers; a missing or extra function changes
   neighbours you think are unrelated.
2. **What the file owns.** Which globals it defines and which are extern; a value defined in the
   file, read through an extern, or reached as a member of a struct compiles differently (load
   reuse, literal pool, base register). The same memory can need two names in one file.
3. **Types and grouping.** Sizes and signedness of every object the unit touches; a group of
   variables as one struct or as separate scalars; arrays against pointer arithmetic.
4. **Linkage and declarations.** Static against global functions and data, prototypes (missing,
   old-style, or with other parameter types), `volatile`, inline helpers and macros the original
   had.
5. **The compiler and options for this file.** Another build of the same compiler, or another
   option set, before assuming the source is wrong; one file in an image can have been built
   differently (profiles' compiler.md say how to scan for it).

Before searching again, go back to the plain source: take out the dead uses, temporaries and
helpers that earlier rounds added to make up for a difference. They were fitted to the old
context, and once the context is right they usually make things worse (a draft tuned under one
compiler build scored worse under the right build than the plain source did). Then search again,
with small hypothesis-driven variant sets. Record each audit's finding in the
unit's notes even when it changes nothing: the next agent must not repeat it. An exact function
inside an unfinished unit is diagnostic only; never credit it.

## Rules

- No guessing: every constant, offset and name comes from the original's bytes or the sibling build. MATCH plus verify is the proof.
- Plausible, readable period C, but the bytes win.
- Calls to functions later in the same file: put a prototype or stub definition above the caller in a work file; only the merged file, with the real callee at its true offset, matches every displacement byte.
- Initialised data and string literals must be defined in the file in the original order (data in definition order, then the literal pool in order of first use).
- For statics in `_BSS` with no original name, choose names whose layout key puts them where the original has them (`python3 profiles/borland-tc101/bssorder.py NAMES...`), and say in a comment the name was chosen for layout.
- Uninitialised `static` variables declared inside functions come first in the file's `_BSS`, in the order the functions define them and with no word alignment (a byte then an `int` puts the `int` at an odd address); the file-scope names follow, laid out by their keys. A BSS that is off by a few bytes at its start usually means a function-level static is missing, or is declared at file scope.
- A function the original had `static` has no overlay stub entry; verify reports such publics. In an overlay, verify also checks the names against the stub table: the compiler lists publics in ascending order of a hash of the name (profiles/borland-tc101/bssorder.py), and TLINK numbers the stub entries in that order, so a wrongly named function shows as a `stub order` problem even when every byte matches. Choose a name whose key falls between its neighbours' (`python3 profiles/borland-tc101/bssorder.py NAMES...`). Two names with equal keys are listed in reverse order of first sight, the first seen last, so it takes the earlier stub entry; verify checks the object's own order against the stub too, and when it reports a tie the wrong way round, name the one the stub wants first earlier in the file (a prototype at the top does it).
- Only edit your own source file. No git commands. Do not edit tools, target tables, other sources, symbols.tsv or matched.txt. Keep scratch files in a subfolder named after your file; other agents share the scratch area.
- If a function will not match after about 20 builds, keep the best version and move on; report the remaining difference.

## armcc5 (Cortex-M images)

The loop is the same; the brief to give an agent is profiles/armcc5/match-brief.md, and the
evidence is in profiles/armcc5/compiler.md. What differs:

- A source names its functions' addresses with `/* at: name 0xADDR */` lines (or keeps the map's
  `sub_ADDR` placeholders) and its options with `/* opts: ... */`; there is no target table.
- match.py links the object at the real addresses with every other symbol defined from the map,
  and compares finished bytes: a wrong extern or callee shows as a byte difference, so there is no
  verify step. WHOLE FILE MATCHES, then `merge.py`.
- Between builds, `profiles/armcc5/tools/armvariant.py score SRC NAME --diff` compiles only (a
  second or two) and scores one function against the image; `--regs` tells structure from
  register allocation, and `pool` shows the literal pool's order. `fnscore.py SRC` scores every
  function of the file the same way (also without `--split_sections`) and reports any function
  whose size or entry moved, which a change to one function can do to its neighbours.
- For a register swap that resists edits, read compiler.md, "How the register allocator
  chooses": the allocator's keys (references, moves, degree, the order values are made) say
  which change can reach a swap. Moving a whole block into a `static __forceinline` helper that
  returns its result, and an unread comparison of addresses or a stored comparison that keeps a
  value live, are the levers that worked when respelling statements did not.
- A function that stays a register swap after the budget goes to a search rather than more hand
  edits: `armoutline.py` (inline helpers, or one statement at every point), `armbrute.py` over
  that template or a hand-written one, `armpermute.py`, then `armreduce.py` on the hit and
  match.py on the result. compiler.md, "Searching for the source", has what the searches found.
- An image built at `-O0` without `--split_sections` (one `.text` per file, compiler.md "armcc -O0,
  and files built without --split_sections") starts from `profiles/armcc5/tools/o0c.py` drafts
  rather than Ghidra's: `o0c.py gen` drafts every function from the image alone, `o0c.py score`
  compiles each and scores it, and `o0c.py file NAME --write F.c` joins a file's drafts into one
  source for match.py. A function proves only in its whole file there: what armcc knows of a
  same-file callee changes the caller's registers.
- A file over MDK-Lite's 32 KB link limit cannot be linked by match.py at all (L6050U), nor can a
  file whose object has two sections of one name, such as two `.data` sections (L6223E):
  `objplace.py match F.c` proves either by placing the object and applying every relocation
  itself.
- Every function is C: no `__asm` functions (all of a file's go into one `.emb_text` section),
  no forced instructions, no register variables; inline `__asm` only for one special-register
  instruction (MSR, MRS, CPSID, WFI).

## ti-c2000 (C2000 images)

The brief to give an agent is profiles/ti-c2000/match-brief.md, and the evidence is in
profiles/ti-c2000/compiler.md. What differs:

- Functions take their addresses from the map by name; there is no address line. A ramfunc
  needs `#pragma CODE_SECTION(f, "ramfuncs")` and is linked at its run address, compared at its
  load address.
- match.py links the object with the native lnk2000 at the real addresses, every other symbol
  from the map, and compares finished words: relocations are filled in, not masked, so there is
  no verify step. It prints EXACT, or the first difference side by side; `--all-cgt` runs every
  installed compiler.
- A file's own variables go between `/* ebss-begin */` and `/* ebss-end */`, and
  `profiles/ti-c2000/tools/c28ebss.py` puts them where the image has them. cl2000's `.ebss` order
  depends on every declaration and identifier in the file, so rerun it after any such change.
- Keep the functions in the original file's order: cl2000 uses what it learnt from a callee
  defined above the caller.
- A function that stays off after the budget goes to `c28batch.py --climb` over a choice-point
  spec; check a hit with match.py under the real name.

## Why verify matters here

match.py masks every byte a fixup writes, so a call to the wrong routine, a wrong global, or a constant where the original had a relocation still says MATCH. In UW2 a draft called `strncmp` where the original called `strnicmp`, and about 20 extern names in another file were wrong behind a MATCH. Only verify and the link catch these.

## Final report (when you are an agent)

Compact: per function MATCH or the remaining difference (one line if all match); verify's result; the switches and the evidence for them; names recovered and why, and any function renamed; disagreements with symbols.tsv or other files' declarations; compiler behaviour not already in compiler.md (specific and tested only); any problem with the target table; the number of builds used. Do not paste the file.
