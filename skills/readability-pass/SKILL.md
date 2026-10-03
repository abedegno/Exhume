---
name: readability-pass
description: Use when a byte-matched, exactly linked DOS decompilation is to be made readable without changing a byte - a build gate, shared headers, named constants, struct fields and accessors, original file names and subsystem directories, comments and findings - each step proved by the gate; and when running agents to do it.
---

# Readability pass

A matched tree is correct but hard to read: every file declares what it uses for itself, numbers stand where names should, structs are reached through casts and offsets, files are named after IDA segments. This pass fixes that in six steps, in order, and every byte of the build stays identical through all of them; the gate passing after each step is the proof. docs/readability.md has the reasons and the UW2 numbers. Paths are relative to the Exhume checkout; tools take `--config` or `EXHUME_CONFIG`.

Start only when every source matches and verifies, the exact link is byte-identical to the original, and the modding build with nothing changed equals the exact link (skills/modding-build). Keep each step in its own commit, never mixed with a change meant to alter the program.

## 1. Build driver and gate

Make one command prove the whole tree, before anything else changes.

- Fill in `[gate]` in exhume.toml (docs/config.md): the link commands, where they write the EXE, boot steps. Leave out `known_diffs`: the exact link must be byte-identical (a project that still has a few unexplained bytes may list them there for now, as offset, original byte, linked byte). Copy `tools/templates/Makefile` to the project root.
- `python3 tools/gate.py check --all` must pass on the untouched tree: every source compiled, WHOLE SEGMENT MATCHES and a clean verify for each, symbols.tsv rebuilt from scratch equal to the committed one, the exact link byte-identical to the original, the unchanged modding build equal to the exact link. Then prove it can fail: change one constant in one source, see `check --fast` fail, revert.
- `gate.py check` recompiles only sources whose hash (text plus included headers), options, object or toolchain changed since they last passed. `check --fast` stops after match and verify of the changed sources: the edit loop, never the proof.
- Install the pre-push hook (`sh tools/install-hooks.sh PROJECT`) and the CI workflow (`tools/templates/repocheck.yml`): hosted CI cannot have the toolchain or the program, so it runs only `tools/repocheck.py` (script syntax, Markdown links, no executables, objects, libraries, disk images or game data, by name or by header). The hook is the real gate. It checks the working tree, so commit or stash first.

Traps: the exact link's own exit status is the comparison's (nonzero while any byte differs, known or not), so judge the EXE, never the exit code; delete the old EXE first so a failed link is never judged by a stale one. symbols.tsv from scratch depends on order (a file can need a name another file merges first, such as a far address it refers to by its offset alone): merge overlays first then by target, and retry the failures until a round adds nothing. A fixed sort by path breaks when files move between directories.

## 2. Shared headers

One declaration per name and one definition per struct, in a header per subsystem (`[project] include`, default `src/include`).

1. Measure: `python3 tools/declinv.py` counts prototypes, extern lines and local struct definitions, and lists the conflicts (divergent forms, old-style, static, duplicate definitions, tag kinds), the struct tags defined in several places with their sizes, and order ties.
2. Structs first: for each tag, `tools/structrec.py report TAG` shows every copy's used fields by bit position; write the canonical definition as a spec (`structrec.py spec`, then edit), then `structrec.py convert SPEC --dir BASE --write` on a copy of the tree taken before the step (BASE). The bytes decide each field: offset, width, unit (a char or an int bitfield) and signedness. Files whose fields match nothing, or several, are reported and left alone: hand-fix them in BASE or give `OVERRIDES`.
3. Headers: write a plan (examples/uw2/readability/headers/plan.toml is UW2's) and run `tools/headergen.py PLAN --base BASE --write --loop 12`. It declares every name that can be shared in its owner's header, deletes the sources' own copies and adds the includes; with `allow_divergent` the loop excludes the names of each failing file until `check --fast` passes. The loop overshoots: then put names back by per-file bisection (examples/uw2/readability/headers/scripts/refine2.py), judged by `check --fast` and by `tools/buildwarn.py --diff` (save the warnings before the step).
4. Full gate, then `declinv.py` again for the numbers.

Traps, all found by the gate on UW2:
- A header is the first sight of every name in it, for every file that includes it, the defining file too. Turbo C lists a file's publics, and lays out its `_BSS`, by a hash of the name, ties in order of first sight, so declaring one name of a tie group in a header and not its partner reorders them (a verify `_BSS` problem, or a link stop on overlay stub order). Tie partners go in together or stay out together; headergen and declinv check this with the profile's bssorder.py.
- An unused extern or prototype leaves no trace in the object (only comment records naming the included files), so a header may declare far more than a file uses.
- Some names must stay declared in their files: a `char` parameter where a caller pushes an `int` (UW2's `advance`), an old-style declaration (`f()`: callers push arguments unconverted), another return type, another view of the same data. Declare such a name in each file its own way and in no header.
- A far variable's declaration stays with its definition: an uninitialised `far` definition after an `extern` declaration of the same name comes out as near DGROUP data.
- Struct sizes are evidence: a definition by value lays out `_BSS` with the struct's size (UW2's `struct MotionCalc` is 23 bytes although six files padded it to 24), and struct copies and pointer strides show sizes too (8-byte copies of `struct Object` became `struct StaticObj`).
- `unsigned` against `int` fields can differ: `p->f |= x` is `or [bx+N],ax` on an unsigned field and load, or, store on an int one. A comparison or `>>` shows signedness. Keep the type the code needs and cast at the one use that differs.
- Fields at the same offset compile alike whatever their names: a scalar and the element of an array field, a bitfield and the same bits in another partition of its word. Only bits, unit and type matter.
- Turbo C 1.01 has no anonymous unions in C, so a word read whole and as bitfields is a named union and every access names the view.
- `struct X;` declares a tag; a tag declared and never defined draws a warning only. Two headers that need each other's structs complete get them from a third header each includes at its end.
- A header must not take the name of a file the toolchain stages (tools/build.py refuses).

## 3. Named constants

`#define`s and enums in the headers, beside the declarations they go with, each group saying where its names and values come from: the format documents, the program's own data (UW2 named all 448 item ids from the game's item name strings: examples/uw2/consts/), the sibling build's enums. A literal stays a literal where nothing shows what it means.

Traps: keep a constant's spelling, because the type follows it: hex values from 0x8000 are `unsigned` and decimal ones `long`, so a name never replaces a decimal `32768` with `0x8000`. Enum constants compile like the `int` literal everywhere (case labels, comparisons with `char`, bitfield values, indices, arguments), so only values below 0x8000 suit an enum. Constant expressions fold (`ERR_LOWMEM | 2` is the single value), but rewriting an inverse mask as `~NAME` or splitting a literal combined with variables can change the code: `(a & ~0x3F) + (b & ~0x3F)` tested for zero drops an `or ax,ax` that the `0xFFC0` spelling keeps. Strings: with `-d` a file's literal pool shares duplicates and tails, so a `#define` of the same literal changes nothing, but a named array is data in definition order outside the pool.

## 4. Struct fields and accessors

Fields instead of casts and offsets; one set of accessor macros for packed fields; callers retyped to the shared types.

- `python3 tools/rawoffsets.py --ref BEFORE` counts the cast-pointer offset sites (UW2: 96 to 12).
- `tools/accessors.py inventory` lists every function-like macro by body; `unify HEADER` replaces each file's copies of a header's macros; `inline HEADER` replaces open-coded reads that are token for token a getter's body.
- Moving prototypes that take shared types into a header draws "Suspicious pointer conversion" at callers that pass another pointer type (UW2: 234); retype the callers (pointer types of the same address cost nothing) until `buildwarn.py --diff` shows none.

Traps: a field compiles like the cast it replaces only when the address is the same and reached the same way. Not when the scaling differs (`((unsigned *)p)[i * 3 + 2]` scales after the add, `p[i].f` folds the offset into the displacement), and not when the base pointer differs (code that keeps a pointer at a later field and reads before it with a negative displacement keeps that pointer). Bitfields pack as one stream of bits with byte granularity, each in the 16-bit window starting at the byte holding the next free bit (the profile's compiler.md, "Bitfields"). Setter macros fold constants (`x & 0xF00F | ((0) & 0xFF) << 4` is `x &= 0xF00F`), but `((v) & 7)` of an already masked `x & 7` merges into one `and` unless the macro casts first, which keeps both, and a shift by 0 always costs `shr ax,0`: files that compile a macro differently need a second spelling of it, said in a comment.

## 5. File names and directories

Original names where there is evidence, the subsystem's directory for each file, and a record of every name's kind and evidence (UW2: `map/filenames.tsv`, columns segment, old file, new file, kind, evidence).

- Evidence, strongest first: a related code base with the same functions doing the same job (UW2: System Shock's source release, from the same studio and engine; a library's own module names), giving **original**; the sibling build's name prefixes or a related file's job, giving **inferred**; otherwise **descriptive**. examples/uw2/rename/ has the index and similarity search.
- First make every tool find sources by segment, never by file name (tools/sources.py: `by_segment`, `family` for a split segment's modules) and the link order by segment; then `git mv`. Stems stay unique across directories: objects and link modules are named by them.
- Rewrite paths in documents and comments (examples/uw2/rename/refs.py), then run the full gate and `tools/repocheck.py` for broken links.

Traps: Turbo C names a file's code segment `FILE_TEXT` and each far variable's segment `FILE<n>_FAR`, and TASM does the same for a module written with `.model` and `.code`, so a rename renames segments. The EXE keeps no segment names and TLINK places segments in the order it first meets the names, so this is harmless except where two modules share a segment: an assembly module sharing a C file's segment must declare that file's segment name, and a link that declares segments early to fix their order must take the names from the objects. Far data segments are ordered by first definition in link order, which shows the owner and does not depend on names.

## 6. Comments, subsystem notes and findings

- Every file gets a header comment: its job in the program, entry points, data owned, neighbours, and where its name comes from. Routines get a comment where the code is not obvious. Tag a note `match:` when it explains a code shape kept for byte matching (a dead store, an odd cast, an order) and `name:` when it gives the evidence for a name.
- Agents write comment edits as specs for `tools/comments.py apply`, which writes only when the token stream is unchanged, no comment nests another and no non-ASCII was added. Before committing, `tools/comments.py check --ref HEAD` over the tree must report no file with changed code; then the full gate.
- One note per subsystem (architecture, data, the rules of the program found in the code, open questions), then a findings page across them: likely bugs, each labelled by confidence (confirmed by a second witness such as the sibling build or the data reaching the faulty path; likely; possible), with its effect and whether a port should reproduce it; recovered rules; engine findings; dead code; open questions. Re-read every bug candidate in the source before listing it, and drop the ones that do not hold (UW2 dropped two of 19).

Traps: a comment's text can close the comment early (`*/` in the text) and leave code behind, which `apply` catches. What the code does is read from the code; intent and effect in the program are inferences and must say so.

## Orchestration

- The orchestrator runs steps 1 and 2 itself, or with one agent: header generation is one job over the whole tree.
- Steps 3, 4 and 6 split by subsystem: one agent per subsystem, each owning that subsystem's sources and its header. Shared headers are the collision point: never give two agents the same header, and serialise edits to the umbrella header.
- About three agents at once. Never nested agents: tell every agent not to spawn its own, and restate it when resuming one. On UW2 two commenting agents each fanned out to about five sub-agents, twelve ran at once and the session limit stopped them all (the first comment commit is a partial pass for that reason). Resume an interrupted agent rather than restarting it.
- Agents use `gate.py check --fast` on their own changes; the DOS sessions of several full gates at once slow each other. The orchestrator runs the full gate before every commit and checks comment agents' work with `comments.py check --ref`.
- Agents never commit, never edit tools, symbols.tsv or another agent's files, and report: files changed, what was named and from what evidence, anything that had to stay as it was and why, the gate's result.

## Report

Per step, the numbers that show it happened, measured by the tools: prototypes, extern lines and local struct definitions (declinv.py), literals named and their uses, raw offset sites (rawoffsets.py), files renamed by kind, notes written, findings by confidence; and the gate's result for the final tree.
