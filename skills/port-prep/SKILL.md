---
name: port-prep
description: Use when a byte-matching decompilation is complete or nearly so and someone wants to port, modernise or modify the program - lists what the matched tree still lacks, what to keep as the reference, and the checks that keep a port honest against the original.
---

# Prepare a matched decompilation for porting

A matched decompilation is a reference, not yet a portable program. UW2 has not been ported; this list comes from what matching and linking it left open. Paths are relative to the Exhume checkout.

## First: the modding build

Before any port or modification, get the modding build working (skills/modding-build): the exact link's snapshot, `link.py --mod` giving the exact EXE with nothing changed, a layout audit with `tools/addrscan.py`, and a proof that edits of different sizes in an overlay and in a resident file boot into the program's main scene. Until then every change has to keep the original's sizes. After it, a change of any size can be tried and run against the original in minutes.

## Keep the matching tree as the reference

- Never change the matched sources in place for a port. Branch or copy. The matched tree, its link and `exediff` are the regression test for any later clean-up that is meant to keep the bytes.
- Clean-ups that keep the bytes (names, comments, types that compile the same, shared headers) can go into the matched tree, each one proved by build, match, verify and link.

## What the matched tree still lacks (check each)

- **Data owned by no source.** Anything the link still extracts from the user's EXE (examples/uw2/extract.py: UW2 still takes about 83 KB of far data, the graphics and 3D modules' tables and models, and eight small unattributed DGROUP gaps) must become source or a loaded asset. The modding build keeps extracted pieces in their places next to their neighbours and writes relocated words and typed code offsets in them as names, but their internal layout is fixed: change such data, and the assembly that addresses it by number, only at the same length until it has source.
- **Addresses written as numbers.** The layout audit (skills/modding-build) lists what is left; each remaining entry is a place a port must replace with a name or a structure.
- **Provisional names.** symbols.tsv marks each name original, library or provisional. Name what you can from the sibling build before a port freezes them.
- **Prototypes.** Original files often declared what they called for themselves, inconsistently (UW2: seg038 calls `advance` with an int, ovr154 defines it with a char). A port needs one header per module with the true types.
- **Platform code.** List every file that touches the hardware or DOS: interrupts (`geninterrupt`, pseudo-registers), port I/O (`outportb`), EMS, the timer, video memory, the sound driver interface (UW2: Miles AIL 2.0 in seg022), the mouse and keyboard handlers, and all assembly modules. These are the port's platform layer.
- **Memory model assumptions.** 16-bit `int`, far pointers built with `MK_FP` and compared by offset only, segment arithmetic (`FP_SEG(x) + 1`), far pointer subtraction done as long division, huge arrays, overlay stubs (a function address stored as data points at its overlay stub, not its code).
- **Layout assumptions.** Struct fields placed with padding arrays to exact offsets, the compiler's bitfield placement (Borland places each field in the 16-bit window starting at the byte holding the next free bit), save files and data files read by `fread` into structs. Write each record layout down with its byte offsets before changing the compiler.
- **Odd behaviour the bytes force.** A three-deep chained bitfield assignment stores an uninitialised DX into the middle field; impossible comparisons are kept (UW2's `mendable` compares against 0x90 and 0x94, in both builds). Note each one where matching revealed it, and decide per case whether the port keeps it.

## Checks that keep a port honest

- Differential testing against the original running in DOS: drive both with the same inputs and compare saves, memory (dos-mcp `read_memory`, `search_memory`) and screenshots (`tools/rungame.mjs`).
- Keep the save-file and data-file formats byte-compatible with the original unless there is a reason not to, and test round trips both ways.
- Port one subsystem at a time behind the platform layer, and keep the DOS build linking and matching until the last DOS-only file goes.
