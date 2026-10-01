---
name: verify-and-merge
description: Use when a source file reports WHOLE SEGMENT MATCHES and is to be accepted into a byte-matching decompilation - check everything match.py masks (fixups, data, BSS, far data, names) and merge the file through the gates that keep symbols.tsv and matched.txt consistent. Also use after any rename.
---

# Verify and merge

Paths are relative to the Exhume checkout. Only one merge at a time: symbols.tsv and matched.txt are shared.

## Verify

`python3 tools/verify.py src/FILE.C` reads the last build and checks what match.py masked:

- every extern resolves to one address everywhere it is used, and no two externs share one (two names for one function);
- the file's own publics land where the EXE has them, including overlay stub entries (a public with no stub entry was static in the original);
- references into the file's own code land where the object says (calls to later functions, jump tables, function addresses stored as data);
- `_DATA` equals the EXE's bytes at one agreed base; pointer tables inside it are checked and masked; `_BSS` references agree on one base;
- the file's own far data segments are placed, sized against the overlay segment table and compared;
- every name agrees with symbols.tsv (one name per address, one address per name).

It ends `-- fixups and data verified` or lists each PROBLEM. Fix the source, never the tool or symbols.tsv, to make a problem go away; if verify is wrong, say so with evidence.

## Merge

`python3 tools/merge.py src/FILE.C` is the only way a file becomes matched. It builds fresh, requires WHOLE SEGMENT MATCHES, requires a clean verify, runs `verify.py --update` (which refuses to write symbols.tsv on any problem), adds the target to matched.txt and refreshes the map's files.tsv. It stops at the first failure and changes nothing.

Then commit the source, symbols.tsv and matched.txt together, with the file's evidence in the message (switches, names recovered, what verify found).

## Renames

After renaming anything that other files use, rebuild every object (`python3 tools/build.py --all`), check every file still matches and verifies, and rebuild symbols.tsv from scratch: `python3 tools/rebuild-symbols.py`. Never patch symbols.tsv by hand. Patching leaves stale names behind: Exhume's rebuild of UW2's symbols.tsv dropped two names for variables OVR166.C had since made static.

## Failure modes this catches (all from UW2)

- A wrong callee behind a MATCH: OVR112 called `strncmp` where UW2 calls `strnicmp`; a Codex draft of ovr103 had about 20 wrong extern names.
- A constant where the original had a relocation (SEG032's `0x5DFD` was a segment). verify sees fixups the object has; the link sees relocations the object lacks.
- One name for two variables (OVR108's `sound_fpage` and the DS:34AA one; `hitz` in SEG007 and SEG024).
- A file's data starting at an odd address: it is missing the byte before (five files).
- Stale objects: a failed compile used to leave the previous object in place. build.py deletes the object before building, and merge.py always builds fresh.
- A gate that hid its own failure: piping `verify.py --update` through `tail` lost its exit status, and two files merged with seven symbol conflicts. merge.py checks every exit status.
- `verify.py FILE --update`: the flag goes after the file.
