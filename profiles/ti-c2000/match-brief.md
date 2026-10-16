# Brief: match one unit of a C2000 image

You write C that TI's C2000 compiler (cl2000, the version the project's config names) turns back
into one unit of the original image: a run of functions given to you by address, usually one
original source file. The orchestrator moves your file into src/ and runs the gate; you never
do, and you never edit shared files.

## Rules

- **Do not spawn sub-agents.** Work alone.
- **No git commands.**
- Edit only your own source file (named in your assignment). Scratch files go in
  `build/scratch/<unit>/`. Never write to a fixed path in /tmp.
- Don't edit `map/*.tsv` or other people's sources. Names you want changed go in your report.
- **Budget:** about 20 builds per stubborn function, and the total in your assignment. At the
  budget, stop and report your best version.

## Tools (run from the project root)

```sh
PY=~/Exhume/.venv/bin/python; T=~/Exhume/profiles/ti-c2000/tools
$PY ~/Exhume/tools/match.py src/AREA/FILE.c          # compile, place, link, compare: a second or two
$PY ~/Exhume/tools/match.py src/AREA/FILE.c NAME     # only NAME, with its side-by-side diff
$PY ~/Exhume/tools/match.py src/AREA/FILE.c --all-cgt  # every installed compiler
$PY $T/c28disasm.py NAME                             # the image's words for a map row
$PY $T/c28ebss.py src/AREA/FILE.c                    # put the file's variables where the image has them
$PY $T/c28batch.py src/AREA/FILE.c NAME SPEC.json --climb   # search variants of one function
```

- match.py prints EXACT for each function, switch table and const object, or the first
  difference with both disassemblies side by side. Relocations are not masked: every call
  target, data page and address is filled in by the linker and must be the image's word. A name
  the map does not know is reported UNRESOLVED with the address the image holds.
- `map/functions.tsv` and `map/data.tsv` hold every address with its size in words, its name and
  kind (`c`, `ramfunc` with `run=0x...`, `rts`, `asm`).
- `~/Exhume/profiles/ti-c2000/compiler.md` and `linker.md` hold what matching has taught. Read
  them first; most answers are there.

## Writing the file

- **Functions:** define your unit's functions under their map names (`sub_ADDR` until someone
  names them). Each function's address comes from the map; there is no address line in the
  source. A ramfunc needs `#pragma CODE_SECTION(f, "ramfuncs")`.
- **Order:** keep the functions in the original file's order, which is usually address order.
  cl2000 uses what it learnt of a callee defined above the caller (unused parameters, the data
  page it leaves). A function that reloads the data page after a call to a same-file callee had
  that callee below it.
- **Other functions and data:** use the map's names, with prototypes and types you work out from
  the code. A name that spells its address (`sub_080400`, `var_A404`) resolves even before the
  map has it.
- **The file's own variables:** define the variables the original file defined, between
  `/* ebss-begin */` and `/* ebss-end */`, one declaration per line, with initial values from the
  image's `.cinit` records. The signs: accesses that share one `MOVW DP` across several
  variables, stores kept in source order, `.cinit` records that come together. Then run
  c28ebss.py. cl2000 does not keep source order in `.ebss`, and the order changes with every
  declaration and identifier in the file, so run it again after any change to the file's
  declarations, includes or function names. match.py shows misplaced variables as relocation
  differences.
- **Everything else** is `extern`. Where the image shares one data page across another file's
  variables, declare an `extern` struct over that memory; where it reloads the page between two
  neighbouring addresses, they are separate objects.
- **Per-file lines:** `/* opts: ... */` replaces the default options, `/* cflags: ... */` adds to
  them, `/* cgt: VERSION */` picks another installed compiler. Use them only with evidence, and
  say what it is.
- **No assembly stand-ins.** Every function is C. `asm(" ESTOP0")`, `asm(" EALLOW")` and the like
  for one instruction with no C form are fine. A function that writes registers before its
  frame exists, or has no epilogue after its last jump, is assembly (compiler.md, "What can't be
  C"): report it rather than forcing it.

## How cl2000 at -O1 behaves (short version; compiler.md has the evidence)

- Interrupt functions save and restore the whole context. A switch of 12 dense cases became a
  table in `.switch:_f`, one of 9 cases a chain of compares.
- For `a OP b` in floating point the register order follows the source: `MPYF32 Rd, Rb, Rc` with
  Rb holding b. Write the operands in the image's order.
- Float tests: `if (a < b) A; else B;` puts B in the fall-through and A after it, and CMPF32's
  operands follow the source. Write each test from the image's branch condition.
- Stores to the file's own globals keep source order; stores to struct members and externs move.
- Volatile reads and stores are barriers; a value read again after its store, or read once per
  use, is volatile or has a second name.
- `x = y = 0` and two zero stores on one line merge into one register; separate statements on
  separate lines keep separate stores.

## When a function won't match

1. Read the side-by-side diff. The first differing instruction usually names the cause:
   - **a register swap:** the order of initialisers, a reused local, a separate local, the
     operand order of a product;
   - **a missing or extra reload:** volatile, a second name for the same object, a struct member
     against a plain global, a store through a pointer;
   - **loads or stores in another order:** which object each access names (own global, struct
     member, extern), and the order the source creates them;
   - **a different branch shape:** the sense of the test, a missing `else`, a `goto`.
2. Try the smallest change that explains the difference. Record each attempt and its result in
   your scratch notes.
3. Run `--all-cgt` once: a function that only another compiler matches is evidence about the
   compiler, not the source. Report it; don't add a `/* cgt: */` line on your own.
4. Past the budget, write a choice-point spec and run c28batch.py `--climb` (`--pairs` for
   changes that only work together). Check any hit with match.py under the real name.

## Report (your final message)

- Each function with its state: EXACT, or the words differing and the first difference.
- The match.py result line for the whole file, and c28ebss.py's.
- Builds used.
- Anything you learned about the compiler that isn't in compiler.md, with the evidence: the C
  and the words.
- Names you would give, with the evidence.
