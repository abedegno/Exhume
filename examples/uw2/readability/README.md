# UW2's readability pass: plans, specs and the scripts that did it

UW2Decomp's readability pass (commits 12516e4 to f9bf8b5, 2 October 2026) turned the matched tree into source a person can read while keeping every byte: a gate, shared headers, named constants, struct fields, file names, comments. docs/readability.md is the method and skills/readability-pass the working instructions. The general tools are in `tools/`; this directory keeps what was specific to UW2, as a reference for the next project and as the inputs that reproduce UW2's results.

None of this contains game data. The struct specs and the plan are UW2Decomp's own source text (they are in its headers too).

## headers/: step 2, shared headers (commit 0c4d92c)

- `plan.toml`: UW2's plan for `tools/headergen.py`: 15 subsystem headers and the umbrella `uw2.h`, which sources go to which header (by the file names of that time, OVR112, SEG012 ...; SEG003, SEG004 and SEG021 match the modules of those split assembly segments by target), section titles, and the 227 names left in their files. The exclude list is what the loop and the bisection below arrived at.
- `structs/`: the struct and union specs for `tools/structrec.py` and `tools/headergen.py` (`active.json` lists the 42 applied, in order), each a canonical definition reconciled from the sources' copies, with its header, forced field mappings per file and the files left alone. At 12516e4 `struct Object` had 53 copies, `struct Player` 41, `struct ComObj` 28, `struct Tile` 26 (`tools/structrec.py report TAG`).
- `scripts/`: the run's own scripts, kept as reference only. They have UW2Decomp's flat file names and paths of the time written in, and read a `base/` directory.
  - The run kept a `base/` copy of the sources from before the step. `structrec.py convert` (then `sconv.py`) rewrote it struct by struct; `fix_player.py`, `fix_static.py`, `fix_api.py` and `fix_comments.py` are hand edits the field mapping could not express (four files indexed `quests[2]` where the shared struct has `quests[12]`; 8-byte copies of `struct Object` becoming `struct StaticObj`; callers of the object-list functions passing `union Link` pointers, undone where a file's own prototype takes `unsigned far *` by `unfix_api.py`; comments that described a dropped struct but also the file).
  - `loop.py` is the first version of `headergen.py --loop`: generate with `allow_divergent`, run the gate, exclude every divergent name of every failing file, repeat. It overshoots: one bad name in a file excludes all of that file's suspects.
  - `refine2.py` and `accept1.py` win the overshoot back: they put the excluded names back by per-file bisection (a trial with half of each failing file's suspects, rounds until nothing is undecided), judged by the fast gate and by new compiler warnings. That took the exclude list from 311 to 227.
  - The run also used `fastcheck.py` (now `tools/gate.py check --fast`), `warns.sh` (now `tools/buildwarn.py`) and `wave.sh` (generate, gate, compare warnings: now `headergen.py --write`, `gate.py check --fast` and `buildwarn.py --diff`).

Reproducing it: with UW2Decomp's sources at 12516e4 and the run's `base/` (12516e4 after the struct conversions and hand fixes above), `tools/headergen.py examples/uw2/readability/headers/plan.toml --base BASE --write` moves 1,263 names into 16 headers and leaves 227 + 55 in their files, and the 115 files it writes have the same token streams as commit 0c4d92c's (tools/comments.py check): only comments differ, from per-file rewording the run did by hand.

## fields/: step 4, struct fields and shared accessors (commit 9aba437)

UW2-specific helpers, reference only.

- `objoff.py FILE VAR...`: replace cast-offset reads through `struct Object` pointers with fields, from a table of UW2's byte and word offsets (`*(unsigned far *)((unsigned char far *)obj + 0x16)` becomes `obj->home`).
- `retype.py FILE VAR,...`: retype `unsigned far *` link variables as `union Link far *`, with their `&x.word` assignments and bitfield casts.
- `objlist.py`: move the object-list functions' prototypes from SEG029 into `object.h` and delete the per-file copies; `wordargs.py` then passes `&x.link` (or `&tile->objects`) where callers passed `&x.word`. Moving the prototypes first drew 234 "Suspicious pointer conversion" warnings; the retyping cleared them all, with no byte changed.
- `decls.py "NAME ..."`: every declaration and the definition of each name, across sources and headers (now `tools/declinv.py NAME`).

The general parts are `tools/rawoffsets.py` (96 sites before, 12 after) and `tools/accessors.py` (114 shared `OBJ_*`/`SET_*` macros replaced the per-file copies; two have a second spelling because files compile them differently, which `accessors.py unify` reports as "the same body as").

## comments/: step 6 (commits 3c9afb9 and 198a7b3)

`combat-spec.py` is one of the 25 specs the comment agents wrote for `tools/comments.py apply` (then `apply.py`): the header of `combat/COMBAT.C`, a routine note per function and `RETAG` lines that tag existing comments `match:` or `name:`. Every file of the pass went through `apply`, which refuses an edit whose token stream differs.

## Elsewhere in examples/uw2

- `consts/`: step 3, the UW2-specific name sources (README there).
- `rename/`: step 5, finding original file names (README there).
