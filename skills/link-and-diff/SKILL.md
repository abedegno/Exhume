---
name: link-and-diff
description: Use when the matched objects of a DOS decompilation are to be linked into a whole executable with the original linker in headless DOS and compared with the original, region by region; and to prove the result runs and that a source change reaches the screen. Also for an armcc5 Cortex-M firmware, whose whole image is rebuilt in pieces under MDK-Lite's 32 KB limit and compared byte for byte.
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

## TLINK leaves memory uncleared

TLINK does not clear the memory it builds the image in unless told to. Padding and data no record covers come out as whatever was there, so the same link can give different bytes in different DOS set-ups or as the module list changes (UW1: 16 to 40 stale bytes in DGROUP under emu2 at 640 KB, none at 512 KB or under DOSBox-X; UW2's modding build met it in overlay padding). Link with `/i` ("initialize all segments"): the uncovered bytes are then 0, and the link is the same under every DOS (UW1's tools/link.py: `/c /m /s /i`). The EXE cannot show whether the original link used it.

## Reading exediff

- Relocations with the same set in a different order: a segment that was several modules is one source here, or modules are in the wrong order. Split by the linker's padding and the relocation order.
- A resident difference inside one object: a fixup the object lacks or a name resolving elsewhere; verify that file again.
- Only flags in the overlay segment table differ, or whole runs of segments sit in a different order: the original's segments were in other classes. TLINK orders segments in blocks by class and sets a segment's code flag only when its class ends in upper-case `CODE` (profiles/borland-tc101/linker.md, "Segment classes"). Try class splits before suspecting the linker version: `python3 tools/omfclass.py OBJ SEGNAME CLASS` rewrites a segment's class in an object; do it in every object that declares the segment, in a copy of the build directory (`EXHUME_BUILD`), with the order-fixing module's declarations included (UW2's XORDER takes its classes from the objects), relink, compare. When a pattern links the original, change the sources (a TASM `.model` segment cannot change class: profiles/borland-tc101/assembler.md) and record the spellings as inferred, since the EXE stores no class names. This is how UW2's last two bytes, the code flags of seg003 and seg004, were found.
- A few bytes still unexplained after that may go in `[gate] known_diffs` while you look, said plainly in the docs; the goal, which UW2 reaches, is no difference at all.

## A linked image (armcc5): the whole-image gate

A Cortex-M firmware matched with the armcc5 profile has no DOS link to rebuild: every match
already links at the real addresses. The proof that the whole image comes from the sources is
the gate, `python3 tools/gate.py check` (profiles/armcc5/tools/armgate.py). MDK-Lite will not link
more than 32 KB, so it rebuilds the image in pieces, each linked by armlink at its own address,
and compares every byte. profiles/armcc5/linker.md, "The whole-image gate", has the reasons. One
source file larger than 32 KB (possible without `--split_sections`) cannot be linked by armlink
under Lite at all: prove it with `profiles/armcc5/tools/objplace.py`, which places its object and
applies every relocation itself, after `objplace.py check` has shown it gives armlink's bytes on
the sources armlink can link (linker.md, "A firmware built without --split_sections").

1. Every map row needs an owner: the map's file column (merge.py fills it), else the one source
   that defines the name. A named row nobody defines is a library member and is linked from the
   toolchain's own library, so name library rows by their member names (armlibid.py, or by hand).
   A placeholder row with no source stops its piece.
2. Every data row needs an owner too: the data stage links each file's data in the order the
   image shows, and one unowned row shifts everything after it. A file's const, RW and zero-init
   rows each form one run; the gate says when the runs of two files disagree on their order (one
   owner is wrong).
3. Write the pieces file (`pieces.toml`, docs/config.md "[gate] for a linked image"). Cut the code
   region into runs of consecutive rows, each starting on a 4-byte boundary at a section start and
   small enough for one Lite link: 32 KB counts the piece's functions and every other section of
   its files. A run of about 15 KB of functions can already need splitting. The first piece is
   the startup piece (`startup = true`): the vector table, MicroLIB's entry code, the startup
   code, the library members and the C functions up to the first aligned row after them. The
   data stage takes the rest: const data, the region table, the compressed RW image.
4. Run `python3 tools/gate.py check`. Each piece reports MATCHES, or the functions that differ;
   the data stage reports the load image and every data symbol's address; then the rebuilt image
   is written in the original file's own format and compared with it file for file. `--piece N`
   links one piece alone, the quick loop while fixing one.
5. Functions not matched yet go in the pieces file's `known_diffs`: their piece passes as long as
   only they differ. A known diff that changes size shifts what follows; the gate reports those
   functions as affected rather than failed. The goal is no known diffs, IDENTICAL.

Reading what fails:

- A piece's functions in the wrong order: a placeholder name sorts in the wrong place. armlink
  lays a region out in ASCII order of `i.<name>`, and the gate renames each placeholder to the
  real name before it plus `_ADDR`; a note names the pair out of order. A real name that sorts
  out of address order is a wrong name.
- The startup piece's library members in the wrong order: they load in resolution passes, the
  members the project's objects call first, in archive order. The gate requests exactly what the
  objects reference; a difference here usually means a library row has the wrong name or the
  wrong toolchain's library (MicroLIB changed between MDK releases).
- `__initial_sp` or another name undefined: the original link took it from outside the sources
  (a scatter file, a linker option). Put it in a symdefs file and name it in `[gate] symdefs`.
- The data stage's bytes differ from some address on: the row there has no owner, or the wrong
  one, or a file's data options differ (a larger `--bss_threshold` keeps zero globals in `.data`).
- The whole gate takes minutes under Wine: give it workers (`[gate] jobs`, default one per core).
  Each has its own Wine prefix and a persistent wineserver; a firmware of about 500 functions
  went from half an hour to under a minute.
