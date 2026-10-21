# Brief: match one unit of an armcc5 image

You write C that Arm Compiler 5 (the build the project's config names: 5.06u7 unless the
fingerprint found another) turns back into one unit of the original image: a run of consecutive
functions given to you by address. The orchestrator merges your file; you never
merge, commit, or edit shared files.

## Rules

- **Do not spawn sub-agents.** Work alone.
- **No git commands.**
- Edit only your own source file (named in your assignment). Scratch files go in
  `build/scratch/<unit>/`. Never write to a fixed path in /tmp.
- Don't edit `map/*.tsv`, `matched.txt`, or other people's sources.
- **Budget:** about 20 builds per stubborn function, and the total in your assignment. At the
  budget, stop and report your best version.

## Tools (run from the project root)

```sh
PY=~/Exhume/.venv/bin/python; T=~/Exhume/profiles/armcc5/tools
$PY ~/Exhume/tools/match.py src/AREA/FILE.c              # build, place, link, compare: ~6 s
$PY ~/Exhume/tools/match.py src/AREA/FILE.c --dis NAME   # side-by-side Thumb-2 diff
$PY $T/armvariant.py score src/AREA/FILE.c NAME --diff   # compile only: NAME's score, 0 = same code
$PY $T/armvariant.py pool src/AREA/FILE.c NAME           # NAME's literal loads and pool, in order
$PY $T/fnscore.py src/AREA/FILE.c [NAME] [--pool]        # compile only: every function's score, = is the same code
```

A score is not a match (it masks call targets and pool words); match.py is the proof.

- `build/decomp/<ADDR>.c` holds Ghidra's decompiled C for every function: a draft, often wrong in
  types and structure, never the answer.
- `map/functions.tsv` and `map/data.tsv` hold every address with its size, name and kind (`rw`,
  `zi`, `ro`, `code`, `lib`).
- `~/Exhume/profiles/armcc5/compiler.md` and `linker.md` hold what matching has taught. Read them
  first.

## Writing the file

- **Functions:** define your unit's functions, and nothing else. Name each one with
  `/* at: name 0xADDR */` lines, or keep the placeholder `sub_XXXXXXXX`. armlink laid the code out
  **sorted by function name (ASCII)**, so a real name must sort between its neighbours'. When
  unsure, keep the placeholder.
- **Other functions:** call them by their map name, with a prototype you work out from the call
  sites. Use `sub_XXXXXXXX` unless the map names them.
- **A callee from the same file:** if a caller keeps r1-r3 (or ip) live across a `bl`, or pushes
  an odd number of registers, the callee was defined in the **same source file** (before or after
  it). armcc knew its register use, so a prototype won't reproduce it. Define the callee in your
  file, with its own `at:` entry, even if it's outside your unit, and say so in your report: one
  source file can own functions far apart in the image, because the layout is sorted by name,
  not by file. A caller that saves values in r4-r8 across a call to a function in your unit
  shows the reverse: they were in different files (`__weak` on the callee reproduces that while
  both are in one file, until the files are regrouped).
- **Names and adjacency:** two adjacent functions are linked in one region, where armlink orders
  them by name. A pair whose names don't sort in address order won't match. When unsure, use the
  placeholder.
- **Globals:**
  - **Define** the globals the original file defined, with their initial values from the RW image
    (`map/data.tsv` says `rw` or `zi`). The sign is that armcc addresses a file's own globals
    relative to each other: `ldr r6,=g_A; add.w r7,r6,#8` means g_B was defined in the same file,
    straight after g_A. `extern` declarations can't reproduce that: each gets its own literal.
  - Declare the rest `extern`, with the map's name (`g_XXXXXXXX` for RAM, `c_XXXXXXXX` for flash
    data) and a real type. Structs are fine: one extern struct often explains many offsets from
    the same base. The map's data sizes are guesses (the gap to the next address), so trust the
    code's offsets.
- **Strings and small tables** a function reaches with `adr` belong to that function: write them
  as literals in it.
- `static` helpers are fine when a function is only called from inside your unit.
- **No assembly stand-ins.** Every function is C. No `__asm` functions, no GNU `asm(...)`, no
  `__asm { }` forcing ordinary instructions, no data placed in code sections, no `register ...
  __asm("rN")` variables. An inline `__asm { MSR ... }` (or MRS, CPSID, WFI and the like) for one
  special-register instruction with no C equivalent is fine. If the map's rows look wrong (a
  function split into several rows, a trampoline or literal pool with its own row), report the
  addresses and match the rest: the map gets fixed, the source doesn't work around it.

## How armcc 5 at -O1 -Otime behaves (short version; compiler.md has the evidence)

- Short conditionals compile to compare and branch, never IT blocks.
- Division by a constant becomes multiply and shift.
- Each function has its own literal pool after it.
- `uint8_t`/`uint16_t` arithmetic shows up as `uxtb`/`uxth`, so get the types right.
- A switch with a bound becomes `cmp; bhs default; tbb`. Distant cases go through `b`
  trampolines after the return.
- A function that starts with `nop.w` before its push is two functions: a 4-byte tail call to the
  next one (`void A(void) { B(); }`, which armlink turned into `nop.w`) and B. Report the row.
- `/* opts: ... */` holds the file's own options when they differ from the defaults
  (`--no_data_reorder` when the file's RAM data is not sorted by size, `--bss_threshold=64` when
  zero-valued objects larger than 8 bytes sit in initialised RAM).

## When a function won't match

1. Run `--dis NAME`. The first differing instruction usually names the cause:
   - **register allocation:** reorder statements or locals
   - **a missing `uxth`:** the type
   - **a different branch shape:** if/else order, or a loop form
   - **the same loads in another order, or another pool order:** the order the source created the
     constants in (`armvariant.py pool`; compiler.md, "Literal pools and scheduling")
2. Try the smallest change that explains the difference. Record each attempt's result (bytes
   differing) in your scratch notes.
3. Get the structure first (`armvariant.py score --regs` is 0 when only registers differ). A
   register swap that no spelling of the nearby statements moves usually comes from something
   else in the function (compiler.md, "Inline helpers, getters and register allocation" and the
   sections after it):
   - a `static __inline` helper or one-line getter the original had (armcc allocates inlined
     code after the caller's own), often where the image repeats a run of instructions
     (`armrepeats.py --func NAME`);
   - a block-local temporary for a copied field, or a local declared another integer type;
   - the declaration order of the locals, even within one declaration;
   - the clamp's or the condition's spelling, far from the swap;
   - a dead use that survives to register allocation (an unread pointer comparison).
   These usually take two or more changes together. Past your budget, report the function with
   its `--regs` score: it goes to a search (`armoutline.py`, `armbrute.py`, `armpermute.py`).

## Report (your final message)

- Each function with its state: `MATCH`, or bytes differing plus the `--dis` summary.
- The match.py result line for the whole file.
- Builds used.
- Anything you learned about the compiler that isn't in compiler.md, with the evidence: the C
  and the bytes.
- Names you chose, and why they fit the ASCII order.
