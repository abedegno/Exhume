# Brief: match one unit of an armcc5 image

You write C that Arm Compiler 5.06u7 turns back into one unit of the original image: a run of
consecutive functions given to you by address. The orchestrator merges your file; you never
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
~/Exhume/.venv/bin/python ~/Exhume/tools/match.py src/AREA/FILE.c             # build, place, link, compare: ~6 s
~/Exhume/.venv/bin/python ~/Exhume/tools/match.py src/AREA/FILE.c --dis NAME   # side-by-side Thumb-2 diff (needs the venv: capstone)
```

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
- **Globals:** declare every global you use `extern`, with the map's name (`g_XXXXXXXX` for RAM,
  `c_XXXXXXXX` for flash data) and a real type. Don't define data in this phase. Structs are
  fine: one extern struct often explains many offsets from the same base.
- **Strings and small tables** a function reaches with `adr` belong to that function: write them
  as literals in it.
- `static` helpers are fine when a function is only called from inside your unit.

## How armcc 5.06u7 at -O1 -Otime behaves (short version; compiler.md has the evidence)

- Short conditionals compile to compare and branch, never IT blocks.
- Division by a constant becomes multiply and shift.
- Each function has its own literal pool after it.
- `uint8_t`/`uint16_t` arithmetic shows up as `uxtb`/`uxth`, so get the types right.
- A switch with a bound becomes `cmp; bhs default; tbb`. Distant cases go through `b`
  trampolines after the return.

## When a function won't match

1. Run `--dis NAME`. The first differing instruction usually names the cause:
   - **register allocation:** reorder statements or locals
   - **a missing `uxth`:** the type
   - **a different branch shape:** if/else order, or a loop form
2. Try the smallest change that explains the difference. Record each attempt's result (bytes
   differing) in your scratch notes.

## Report (your final message)

- Each function with its state: `MATCH`, or bytes differing plus the `--dis` summary.
- The match.py result line for the whole file.
- Builds used.
- Anything you learned about the compiler that isn't in compiler.md, with the evidence: the C
  and the bytes.
- Names you chose, and why they fit the ASCII order.
