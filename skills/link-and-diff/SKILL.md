---
name: link-and-diff
description: Use when the matched objects of a DOS decompilation are to be linked into a whole executable with the original linker in headless DOS and compared with the original, region by region; and to prove the result runs and that a source change reaches the screen.
---

# Link and diff

Paths are relative to the Exhume checkout. The link is the strongest check there is: it sees what byte matching with masked fixups cannot (wrong callees, missing relocations, wrong statics, public order), and a byte-identical EXE that runs is the end of the method.

## What a link needs

- Every object, in the original module order. The relocation table records it: the linker writes relocations module by module, and a library's modules in library order. An overlay segment table (Borland FBOV) lists every segment in link order too.
- Whatever no source produces yet, taken from the user's own EXE at build time and never committed: data-only assembler modules for far data, gaps in DGROUP between files, code with no source. Each relocation inside them becomes a `dd NAME` / `dw seg NAME` fixup.
- The linker's environment: TLINK stores the output file name (`__EXENAME__`) and the DOS date (`__EXEDATE__`) in the image, so run it under the original name and set the date first (UW2: `uwedit.exe`, 12 May 1993). The DOS must keep a date set through INT 21h AH=2Bh for the programs after the one that set it: DOSBox-X and js-dos do, emu2 only with `tools/emu2-date.patch` (built in by `tools/setup-emu2.sh`); otherwise the date bytes differ and exediff points at the overlay data. The startup module (C0.ASM) may differ from the stock one; find the differences by comparing bytes.
- Segments placed in order of first sight, within blocks by class: declare empty segments early in tiny modules, with the class of the objects that fill them, to reproduce the original order and alignment.

## Steps

1. Build everything: `python3 tools/build.py --all`.
2. Link, in the DOS `tools/dosbackend.mjs` picks (emu2 if built: about 2 seconds for UW2; js-dos about 4). With emu2 a link batch may hold only programs with an optional redirection (no `copy`, no `cd`); set the date with a small program, as UW2's SETDATE.COM does. For UW2 this is the reference implementation: `python3 examples/uw2/link.py` (it runs `examples/uw2/extract.py` first). It stops with the source to correct when an object disagrees with the EXE (an unknown name, a public UW2 had static, an overlay's publics out of stub order, a missing relocation). For another game, copy and adapt it; docs/link.md says which parts are general.
3. Compare: `python3 tools/exediff.py build/LINK/out/GAME.EXE`. Give only the linked EXE; the original comes from exhume.toml. It reports header fields, relocations (set, then order), the resident image segment by segment and the overlay area overlay by overlay.
4. Run it (always in js-dos, whatever DOS built it): `node tools/rungame.mjs --data GAMEDIR --as GAME.EXE build/LINK/out/GAME.EXE shot- w:12000 s:title k:Escape w:3000 s:menu`, then read the screenshots.
5. Prove the build is live: change one string in a copy of a source, build it into a scratch directory (`EXHUME_BUILD=/tmp/x python3 tools/build.py copy/FILE.C`), link it in place of the matched object (`link.py --no-extract --obj STEM=/tmp/x/STEM/STEM.OBJ --out /tmp/x/out`), and see the change on screen. exediff should show exactly the changed bytes. This works only for a change of the same length.
6. When every object verified, the exact link leaves a snapshot in `<build>/LINK/base` for the modding build (`link.py --mod`), which links changes of any size. That is skills/modding-build.

## What the exact link cannot see

A DGROUP address written as a number (`mov si,6742h` for `offset DGROUP:_Palettes`) assembles to the same bytes as the name, so it matches and links exactly. Only a link that moves data exposes it. Run `tools/addrscan.py` and the layout audit in skills/modding-build before any change that alters sizes.

## Reading exediff

- Relocations with the same set in a different order: a segment that was several modules is one source here, or modules are in the wrong order. Split by the linker's padding and the relocation order.
- A resident difference inside one object: a fixup the object lacks or a name resolving elsewhere; verify that file again.
- Only flags in the overlay segment table differ, or whole runs of segments sit in a different order: the original's segments were in other classes. TLINK orders segments in blocks by class and sets a segment's code flag only when its class ends in upper-case `CODE` (profiles/borland-tc101/linker.md, "Segment classes"). Try class splits before suspecting the linker version: `python3 tools/omfclass.py OBJ SEGNAME CLASS` rewrites a segment's class in an object; do it in every object that declares the segment, in a copy of the build directory (`EXHUME_BUILD`), with the order-fixing module's declarations included (UW2's XORDER takes its classes from the objects), relink, compare. When a pattern links the original, change the sources (a TASM `.model` segment cannot change class: profiles/borland-tc101/assembler.md) and record the spellings as inferred, since the EXE stores no class names. This is how UW2's last two bytes, the code flags of seg003 and seg004, were found.
- A few bytes still unexplained after that may go in `[gate] known_diffs` while you look, said plainly in the docs; the goal, which UW2 reaches, is no difference at all.
