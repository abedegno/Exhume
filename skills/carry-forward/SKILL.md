---
name: carry-forward
description: Use when a byte-matched decompilation is to be carried to the vendor's next (or earlier) release of the same program, or to a sibling build of the same source such as another board or product size - comparing the images with addresses allowed for, carrying the map over, rebuilding what did not change, matching what did, keeping one source for builds that differ only in constants, and proving every image with its own gate. Written from armcc5 Cortex-M firmwares and a TI C2000 image; the method is the same for any profile.
---

# Carry a matched tree to another release or board

A matched tree is most of the next release's source already. Usually most functions are the same
code at other addresses, and only a few changed. The work is to tell those apart, carry the map,
and then match only what changed. Paths are relative to the Exhume checkout.

Measured on two projects:

- A protocol-only update of a 598-function firmware: 573 functions the same apart from addresses
  (moved by 0 to 0xFC bytes), 25 changed. The const data was the same bytes, 0xFC later.
- A larger update of the same firmware (8 KB more code, four new protocols): 531 of 598 the same.
- Two boards built from one source: every function in the same order, all but one the same size;
  seven functions and three const tables differed, and every difference was a number.
- A device's two processors (an ARM and a C2000), carried two releases on: most library, RTOS
  and driver units moved over after their symbols were rebound, and the changed units were
  matched with their callers.

## 1. Lay out the releases

- One folder per release (`v1/`, `v2/`), each with its own `exhume.toml`, `src/`, `map/`,
  pieces file, tools and docs. Keep the firmware images and the toolchains once, at the root
  (`firmware/`, `toolchain/`), and name them from each folder as `../firmware/` and
  `../toolchain/`. A folder's `build/` is not tracked.
- If each release was made on a branch, merge it into its folder with `git read-tree --prefix`,
  so the folder keeps its branch's history.
- Read the image's layout (where the code ends and the const data starts, the region table, the
  RW size, the stack, where the library's start-up members are) from the image and the map in
  the tools, never as constants. The same tools then rebuild any release from its own image and
  map.
- Two boards of one release are one folder: one source, a config and a map per board (below).

## 2. Compare the images, with addresses allowed for

Walk the old map in address order and find each function in the new image. Compare the bytes
with every address masked or relocated: `bl` and `b.w` targets that are the same function, and
literal words that point at the same function or data, count as equal. Then compare the const
data and the initialised RAM the same way. What is left is real change: list it per function and
per const row, and list every RAM address that moved.

- A raw scan for the old functions' bytes (no masking, rows of 16 bytes or more) gives
  candidates, not ports. Short leaves and default handlers match in many places; a matching byte
  string does not prove a boundary or that its calls and data still mean the same; relocated
  calls change bytes in code that did not change. Same-offset differences between two images do
  not count changed source either.
- Know your map's units. A C2000 map's sizes are decimal word counts; one survey read them as
  hexadecimal and its counts were wrong.
- A function can change only because a structure it uses grew. In one release a record
  grew from 46 to 144 bytes, and every unit that walked those records changed its strides and
  offsets.
- Write a "what changed" page per release, by what the code does, once the changed functions
  are matched: the matched source is the evidence for it.

When several releases came out, target the one you need directly and use the one between them
only to tell which changes came when. Matching every intermediate release first repeats the
placement and proof work for little gain.

## 3. Carry the map

- Functions keep their names and files at their new addresses. Data that moved keeps its
  placeholder name with the old address (`g_200033C0` in the old map stays `g_200033C0` though it
  now lives at 0x200039E0); `map/data.tsv` gives each item's real address in this release. Name
  data that is new in this release with a prefix of its own and its new address (`g15_20000084`).
  Record both in the names file.
- `/* at: */` lines in the sources hold one release's addresses. Where they differ per release
  or board, let the tools take the address from the map for any name the map holds.
- Check the runtime for new needs: a release can call a library helper the old one did not
  (`__aeabi_ui2f`), and its region table can move the zero-init region (in one release the gate's
  own zero-init section needed 4-byte alignment, where 8 left a four-byte hole).

## 4. Rebuild the unchanged, then match the changed

- Gate the unchanged files first. Most pass once their symbols are rebound.
- Match the changed functions with their callers and callees in their files: file boundaries,
  same-file callees and the file's own data still decide the code (the profile's compiler.md).
  A vendor can move a function into another file between releases; in one, a function moved into
  the file that defined its counter, and only a separate variable for that counter gave its code.
- New functions follow the same match loop (skills/match-file). Helper agents can each take a new
  protocol or subsystem with a private copy of the map (skills/orchestrate).

## 5. One source for both

The strongest evidence that a source is the vendor's is that it rebuilds both releases with only
the changed lines different. Score every candidate of a function that exists in both against
both images (two configs, two maps).

- A form that fits one release only is compensation. In one firmware the old release's source
  held a block temporary that did nothing in C and only fixed registers. Searched again with the
  new release in view, it turned out to stand in for a declaration order: 168 orders without it
  rebuilt the old release exactly, and the forms tuned for one release scored badly on the other.
- A change at the end of a function can move registers at its start: allocation is
  function-wide. When the new release changes registers in code it did not change, the vendor's
  source sat near a tie that the new code tipped. Sibling functions that rebuild both releases
  show which forms the vendor used; on armcc, an allocator trace shows the tie
  (profiles/armcc5/compiler.md, "Tracing the register allocator").

## 6. Boards: one source, constants per board

Two images of one release for two boards were the same program with different numbers (rated
current, a shunt divisor, calibration windows, default capacities). One source builds both:

- A board header defines each constant per board under `#if BOARD == ...`; the board's config
  passes `-DBOARD=` to every compile, and each board has its own config and map
  (`configs/board-B.toml`, `boards/B/`).
- Any spelling that gives the same number compiles to the same bytes, so the image cannot tell
  how the vendor wrote a constant.
- A constant can still change code: on one board a divisor equalled another constant used later
  in the function, armcc kept both in one register, and the multiply had to be its own statement
  to load it where that board's image does. Check every function against both boards.
- Placeholder names keep one board's addresses; each board's map holds its real ones.

## 7. Prove each image

- Each image passes its own gate: IDENTICAL, no known diffs, zero original-byte blobs, coverage
  with no gaps or overlaps.
- Run a negative control: change something every release has (the version number the firmware
  reports) and see the gate reject it; restore it before the final run.
- Docs and reference pages written for the old release describe the old one. Mark them so, and
  check their addresses and formulas against the new source before using them.

## C2000 notes

- Sizes in a C2000 map are words, in decimal.
- Variables that moved change `.ebss` order: rerun `profiles/ti-c2000/tools/c28ebss.py` per file
  after carrying the map.
- One memory can need a volatile view in one file and an ordinary view in another (a task that
  polls a request word, and the initialiser and the page writer that set it). Keep each file's
  view; the image shows which each used.
- A boot sector the vendor did not change is a useful anchor: in one image it was the same bytes
  in three releases.
