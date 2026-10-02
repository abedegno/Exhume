---
name: modding-build
description: Use when the sources of a byte-matched, exactly linked DOS decompilation are to be changed by any size (a fix, a mod, the first step of a port) and still linked into a running executable; and to audit the layout for addresses written as numbers, which matching and the exact link cannot see.
---

# Modding build

Paths are relative to the Exhume checkout. The exact link proves the sources; the modding build lets them change. It needs an exact link that works first (skills/link-and-diff). docs/link.md, "The modding build" and "Layout audits", has the reasons behind each step.

## Steps

1. Exact link once, with every source matching and verifying: `python3 examples/uw2/link.py` (or the project's own link). It leaves a snapshot in `<build>/LINK/base`: each matched object, its source's SHA-1 and where its data sits. If any object fails to verify, no snapshot is written; fix that first.
2. Check the modding build against it with nothing changed: `python3 examples/uw2/link.py --mod`, then `cmp <build>/MODLINK/out/GAME.EXE <build>/LINK/out/GAME.EXE`. They must be identical. A difference means the snapshot or the `dw offset` names in extracted data are wrong.
3. Audit the layout before trusting a changed build (below).
4. Edit and link: `python3 examples/uw2/link.py --mod`. Only sources whose text differs from the snapshot's are compiled, into `<build>/MODLINK/src`; the matched objects stay as they were, so reverting a source brings back the exact link. To build from a copy of the sources instead, point `[project] src` at it (UW2's config reads `EXHUME_SRC`).
5. Boot and look: `node tools/rungame.mjs --data GAMEDIR --as GAME.EXE <build>/MODLINK/out/GAME.EXE shot- ... s:NAME ... m:MARKER`. Read every screenshot. Go as far as the program's main scene (UW2: through character creation into the 3D view), not just the title: layout faults show where data is used, not where it is loaded. `m:TEXT` searches the guest's memory; make new code change a marker string at run time and search for the changed text, so the run proves the new code ran. rungame exits 1 when a search finds nothing.

## Layout audit

A literal address and a symbolic one assemble to the same bytes, so a number that should have been a name matches and links exactly, then breaks when anything before it moves.

1. `python3 tools/addrscan.py` lists every operand with no fixup that could be an address; `*` marks those that may go through DGROUP or an unknown segment. Set `[layout]` in exhume.toml first (far data entries, library objects, dispatchers; docs/config.md). `--entries` and `--jumps` show the segment register states it inferred, which is how to check a surprising line.
2. Read each `*` line against the source and the IDA listing. Write real addresses as names (`offset DGROUP:_name`, the variable in C), then match, verify and exact-link again: the bytes must not change. Note every line that is not an address and why (a coordinate, a divisor, a BIOS address, a table IDA decoded as code).
3. Boot with DGROUP shifted: add a new initialised array early in DGROUP's data and boot to the main scene. Compare with the exact build's screenshot at the same point (`python3 tools/pngdiff.py A.png B.png X0 Y0 X1 Y1`, over a rectangle that does not animate).
4. If it differs, bisect: add a `_BSS` array to one file at a time, in link order, link `--mod`, boot, compare. Padding before the variable breaks the scene and padding after it does not, so the boundary is the file holding the variable. Then find who uses its address as a number, and widen the scan if it missed it.
5. List what still has to keep its length: extracted data with internal offsets, assembly modules that address their own far data by number (`addrscan.py --all`). Those change only at the same length until they have names.

## Rules

- Edit a copy or a branch, never the matched tree, unless the change keeps the bytes and passes match, verify and the exact link.
- A new source file needs a place in the link order (the link's module lists) before `--mod` will take it.
- Keep DGROUP's static data, stack and near heap under 64 KB (profiles/borland-tc101/linker.md).
- Report a modding build as proven only after it reaches the main scene and the marker search succeeds; a title screen is not enough.
