---
name: match-file
description: Use when decompiling one C source file of an old DOS program into C that recompiles byte for byte to the original - the per-file loop of writing C, compiling in headless DOS, reading instruction diffs and restructuring until every function matches and verify passes. Also the brief to give an agent that matches a file.
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

## Rules

- No guessing: every constant, offset and name comes from the original's bytes or the sibling build. MATCH plus verify is the proof.
- Plausible, readable period C, but the bytes win.
- Calls to functions later in the same file: put a prototype or stub definition above the caller in a work file; only the merged file, with the real callee at its true offset, matches every displacement byte.
- Initialised data and string literals must be defined in the file in the original order (data in definition order, then the literal pool in order of first use).
- For statics in `_BSS` with no original name, choose names whose layout key puts them where the original has them (`python3 profiles/borland-tc101/bssorder.py NAMES...`), and say in a comment the name was chosen for layout.
- Uninitialised `static` variables declared inside functions come first in the file's `_BSS`, in the order the functions define them and with no word alignment (a byte then an `int` puts the `int` at an odd address); the file-scope names follow, laid out by their keys. A BSS that is off by a few bytes at its start usually means a function-level static is missing, or is declared at file scope.
- A function the original had `static` has no overlay stub entry; verify reports such publics. In an overlay, verify also checks the names against the stub table: the compiler lists publics in ascending order of a hash of the name (profiles/borland-tc101/bssorder.py), and TLINK numbers the stub entries in that order, so a wrongly named function shows as a `stub order` problem even when every byte matches. Choose a name whose key falls between its neighbours' (`python3 profiles/borland-tc101/bssorder.py NAMES...`).
- Only edit your own source file. No git commands. Do not edit tools, target tables, other sources, symbols.tsv or matched.txt. Keep scratch files in a subfolder named after your file; other agents share the scratch area.
- If a function will not match after about 20 builds, keep the best version and move on; report the remaining difference.

## Why verify matters here

match.py masks every byte a fixup writes, so a call to the wrong routine, a wrong global, or a constant where the original had a relocation still says MATCH. In UW2 a draft called `strncmp` where the original called `strnicmp`, and about 20 extern names in another file were wrong behind a MATCH. Only verify and the link catch these.

## Final report (when you are an agent)

Compact: per function MATCH or the remaining difference (one line if all match); verify's result; the switches and the evidence for them; names recovered and why, and any function renamed; disagreements with symbols.tsv or other files' declarations; compiler behaviour not already in compiler.md (specific and tested only); any problem with the target table; the number of builds used. Do not paste the file.
