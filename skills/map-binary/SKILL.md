---
name: map-binary
description: Use after the toolchain is fingerprinted, to lay out a DOS executable for matching - find every code segment and procedure, split it into original source files, recover original function names from a symbol-bearing sibling build (another platform's release), and write the target tables agents match against.
---

# Map the binary

Paths are relative to the Exhume checkout; outputs go to the project's map directory (exhume.toml `map`). Each tool's docstring says what it reads and writes.

## Steps

1. Configure `[binary] listing` (an IDA listing of the EXE) and its paragraph bias (`listing_para_bias`: what IDA adds to load paragraphs in segment names), and `[sibling]` if another build with symbols exists (image, symbol TSV, bitness, end of code).
2. Run, in order: `tools/doslist.py` (every proc from the listing), `tools/locate.py` (each segment's file offset, checked by disassembling up to five procs per segment against the listing; stale proc offsets are corrected and reported), `tools/callgraphs.py` (both call graphs), `tools/anchors.py` (known pairs, and order inversions), `tools/align.py` (names by alignment), `tools/files.py` (per-file summary).
3. Check `align.py --holdout KIND` before trusting the names: hold out one kind of anchor and see how many come back. In UW2, confirmed names were right 80 times in 82.
4. Make a target table per segment: `python3 tools/targets.py SEG > targets/SEG.tsv`. One code segment is one source file for compilers that give each file its own segment (Turbo C does).
5. Read every table before handing it out. Fix it by hand where the listing is wrong.

## What the sibling gives you

- If the sibling was linked from the same object list in the same order, its functions appear in the same order, so a size-based alignment between anchors names most functions, and the call graphs confirm each pairing. anchors.py's order inversions tell you whether that assumption holds; outside the C library and platform modules there should be none.
- It is a witness for meaning and names, never for bytes. When it disagrees with the target binary about code, the target wins.
- Two names at one sibling address mean its linker folded identical functions; which DOS copy carries which name cannot be proven. Say so in a comment.

## Known listing failures (all seen in UW2)

- Proc offsets in IDA names can be stale; locate.py corrects them by searching a few bytes either side.
- Data decoded as code and code left as data: seg004 began with a 0x63-byte table IDA decoded as `or` instructions. A whole module (seg002) was never listed as procs.
- targets.py ends the last function at the first far return. For assembly modules this overran into the next module; their tables must run from the module's first byte to the zero padding before the next one. Turbo C files can also start a few bytes past the paragraph (`org`).
- `files.py` sizes come from the listing and overstate assembly modules; the target tables have the true extents.

## After matching

Matched files are better anchors than anything else: anchors.py takes their tables (matched.txt), the far functions in symbols.tsv, and `callpairs.py` output (`tools/callpairs.py src/FILE.C > map/pairs_SEG.tsv`), so re-run anchors, align and files as files are merged, and rebuild the tables of unmatched files from the improved map.
