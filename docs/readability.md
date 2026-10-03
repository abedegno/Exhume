# The readability pass

A byte-matched tree proves the source is the program, but it is not yet source a person would want to read. Each file declares for itself what it uses, often differently from the next file; numbers stand where names belong; structs are reached through casts and offsets; files are named after IDA segments; and nothing says what any of it does. The readability pass fixes that after the modding build, and every byte of the build stays the same through it. This page is the method and its reasons; skills/readability-pass has the working steps and the traps, and docs/case-study-uw2.md the numbers from UW2.

```
gate -> shared headers -> named constants -> struct fields -> file names -> comments and findings
```

## The rule: the bytes do not change

Every change in the pass is meant to keep the program identical, so the proof is the same for all of them: every source still matches and verifies, symbols.tsv rebuilds from scratch to the same names at the same addresses, the exact link is still byte-identical to the original, and the modding build with nothing changed still equals the exact link. One command checks all of that, and nothing is committed until it passes. A change that alters the program belongs to the modding build, never to this pass.

This is why the gate comes first. Each later step touches most of the tree (the header step changed 92 of UW2's 99 C files), and only a check of the whole tree after every change makes that safe.

## The gate

`tools/gate.py` is the build driver and the gate; a project wraps it in a Makefile (`tools/templates/Makefile`: `make`, `make exact`, `make check`, `make check-all`, `make boot`) with its facts in exhume.toml's `[gate]` (docs/config.md).

- `check` compiles the sources that changed since they last passed, several to a DOS session and several sessions at once (one per core up to 12, or three in js-dos), then requires WHOLE SEGMENT MATCHES and a clean verify for every source, rebuilds symbols.tsv from scratch and compares it, and requires the exact link to be byte-identical to the original (or to differ in exactly `[gate] known_diffs`, when a project lists any) and the unchanged modding build to equal the exact link. On UW2 it takes about 8 seconds from nothing and 5 when nothing changed in emu2, and about 74 and 11 in js-dos.
- A source counts as changed when its hash changes, and the hash covers the shared headers it includes (`tools/srcdeps.py`), its `/* opts: */`, its object and the toolchain. Editing a header recompiles exactly the files that include it.
- `check --fast` compiles, matches and verifies only the changed sources, without the symbols rebuild or the links: the loop while editing, not the proof.
- symbols.tsv is rebuilt in an order that does not depend on file names: overlays first, then by target, retrying any source that fails until a round adds nothing, since a file can need a name another file merges first. A fixed order by path stops working the moment files move into directories.

Hosted CI cannot run the gate, because the compiler and the program must never be on a public server. So the gate runs locally from a git pre-push hook (`tools/install-hooks.sh`), and CI runs `tools/repocheck.py`, which needs neither: every script parses, every relative Markdown link resolves, and nothing committed is an executable, object, library, disk image or game data file, by name or by its first bytes (`tools/templates/repocheck.yml`).

## The steps

The order matters. Headers come before constants because the constants live in the headers beside the declarations they belong to. Struct fields come after headers because a field is only worth naming once there is one shared struct to name it in. Renaming files comes after the content changes so that the many edits of the earlier steps do not collide with moves. Comments come last, because they describe the final names.

1. **Gate.** Above.
2. **Shared headers.** One declaration per name and one definition per struct, in a header per subsystem. `tools/declinv.py` measures the starting point and lists what cannot be shared. `tools/structrec.py` reconciles each struct's copies into one definition, field by field, from the bits each copy's code uses. `tools/headergen.py` writes the headers from a plan, deletes the sources' own declarations and adds the includes, and with `--loop` leaves out the names that make a file compile differently until the fast gate passes. Some names must stay declared in their files: where the original files disagreed with each other (a `char` parameter one caller pushes as an `int`), the bytes depend on the disagreement.
3. **Named constants.** `#define`s and enums, only where evidence says what a number means: format documents, the program's own data (UW2 named its 448 item ids from the game's item names), the sibling build's enums. The type of a constant follows its spelling, so the spelling is kept.
4. **Struct fields and accessors.** Fields in place of cast-pointer offsets (`tools/rawoffsets.py` counts them), one set of accessor macros for packed fields (`tools/accessors.py`), and callers retyped to the shared types. A few casts stay, where the original computed the address in a way a field cannot.
5. **File names and directories.** An original name where a related code base shows one (a file defining the same functions for the same job), an inferred name where the sibling's names or a related file's job suggest one, a descriptive name otherwise, each recorded with its kind and evidence; and a directory per subsystem. Before moving anything, every tool must find sources by segment rather than by file name (`tools/sources.py`), so the link order needs no change.
6. **Comments and findings.** A header comment on every file, comments on routines whose code is not obvious, notes tagged `match:` (a shape kept for byte matching) and `name:` (the evidence for a name), a note per subsystem, and a findings page: likely bugs in the original with their confidence and effect, the rules of the program as the code implements them, dead code, open questions. `tools/comments.py apply` writes a comment edit only when the file's token stream is unchanged, and `tools/comments.py check --ref` proves a whole pass changed nothing but comments.

## What the pass found out about the compiler

Each step was an experiment on the compiler, and the gate recorded the result. The facts are in the profile (profiles/borland-tc101/compiler.md, "Readability changes: what keeps the bytes"); the ones that shaped the method:

- An unused declaration leaves no trace in the object, so headers can declare far more than a file uses.
- A header is the first sight of every name in it, and Turbo C breaks ties in public order and `_BSS` layout by first sight, so a name tied with one the headers leave out must stay out too.
- A shared prototype can change how a caller pushes its arguments, so a name the original files declared differently stays declared in each.
- A `#define` keeps the bytes only with the literal's spelling (hex from 0x8000 is `unsigned`, decimal is `long`); an enum is an `int` literal.
- A field compiles like the cast it replaces only when the address is reached the same way.
- File names become segment names, which never reach the EXE but which the linker joins modules by.

## Running it with agents

Steps 1 and 2 are each one job over the whole tree. Steps 3, 4 and 6 split by subsystem, one agent per subsystem owning its sources and its header, about three at once and never nested (skills/orchestrate). Agents run the fast gate on their own changes; the orchestrator runs the full gate before every commit and checks comment work with `comments.py check --ref`.

## Tools

| Tool | Step | What it does |
| --- | --- | --- |
| `tools/gate.py` | 1 | the build driver and gate: `check [--all] [--fast]`, `exact`, `game`, `symbols`, `boot` |
| `tools/sources.py`, `tools/srcdeps.py` | 1, 5 | sources in subdirectories, by stem or segment; a source's hash with its headers |
| `tools/repocheck.py`, `tools/install-hooks.sh`, `tools/templates/` | 1 | toolchain-free CI, the pre-push hook, a Makefile and a workflow |
| `tools/declinv.py` | 2 | declaration inventory, conflicts, multiply defined structs, order ties |
| `tools/structrec.py` | 2 | struct reconciliation: report, spec, convert |
| `tools/headergen.py` | 2 | headers from a plan, sources stripped, includes added, the exclude loop |
| `tools/buildwarn.py` | 2, 4 | compiler warnings saved and compared |
| `tools/rawoffsets.py` | 4 | cast-pointer offset sites, against a git revision |
| `tools/accessors.py` | 4 | macro inventory, unify per-file macros, inline open-coded reads |
| `tools/comments.py` | 6 | apply comment edits that keep the tokens; check, reflow, audit, join |
| `tools/cparse.py` | all | the C parser, token streams, Turbo C struct layout |

UW2's plan, struct specs and the run's own scripts are in examples/uw2/readability/, examples/uw2/consts/ and examples/uw2/rename/.
