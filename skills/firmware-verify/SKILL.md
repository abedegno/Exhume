---
name: firmware-verify
description: Use when a byte-matched Cortex-M firmware (the armcc5 profile) is to be built or changed in a way that no longer matches the original bytes - a readable rewrite, a fix, a modded build, the same C under clang - and the change must be shown to behave as the original does; with differential tests of every function, a whole-system emulator from reset, the open-toolchain build in the original's RAM layout, and the triage of each difference into firmware bug or build bug.
---

# Verify a firmware beyond the bytes

Paths are relative to the Exhume checkout. docs/firmware-testing.md is the method and the
reasons; docs/config.md has `[difftest]`, `[sysemu]` and `[openbuild]`. The tools are in
profiles/armcc5/tools/emu and profiles/armcc5/tools/openbuild; run them with Exhume's .venv
Python (unicorn and capstone).

## Before you start

- The tree matches: every source passes the matcher and the exact link equals the original. The
  matched tree stays the reference; nothing here goes into it except changes proved byte for byte.
- The map names the functions and data (map/functions.tsv, map/data.tsv), and the sources are
  where `[project] src` says: the differential tests type arguments and returns from the C
  definitions, and the open build places RAM from the map.
- Install clang and the `arm-none-eabi` binutils for the open build.

## Steps

1. **Prove the tester on the exact build.** Run difftest with the exact link as the candidate:
   every function must be the same. Then `--mutate FUNC=OFF:BYTE` on a few functions you care
   about: each must be caught. A function that stays "same" under a mutation is not reached by
   its inputs; look at its coverage before trusting it.
2. **Build with the open toolchain.** Fill in `[openbuild]` (at least `flash` and `ram`; `extra`
   for anything of the library the firmware calls besides mem*, str* and 64-bit division) and run
   openbuild.py. Fix compile errors in the open build's settings (compat.h, a `rewrite` pair, a
   define), never by editing the matched source to suit clang. Read every note ramlayout.py
   prints: a name in two objects is not placed, and "cannot move location counter backwards"
   means a variable grew.
3. **Compare every function.** `difftest.py --b build/open/app.elf --b-syms build/open/app.elf
   --init-rw` with the default 200 runs. Write down the count that differs and the mean coverage.
4. **Triage each difference** (docs/firmware-testing.md, "Triage"). Reproduce it with its seed,
   read the first mismatch, decide: build bug, firmware bug, or the emulator's random state. Fix
   build bugs in the open build and run step 3 again. Record firmware bugs in the project's
   findings with the input that shows them. Stop when every remaining difference has a written
   reason.
5. **Boot the whole firmware.** Fill in `[sysemu]`; write a plugin for each device the firmware
   talks to (example_device.py is the pattern; stub the firmware's own bus functions rather than
   modelling bit-banged pins). Boot the original image to its main loop, then the changed build.
   Use `ram_fill` or `fill_ram(seed)` with several seeds.
6. **Test what the firmware says, end to end.** For each interface that matters, write a second
   implementation in Python from the specification (not from the firmware), drive the emulated
   firmware with the requests a real peer sends at its real byte rate, and compare each reply
   byte for byte with the model's answer for the firmware's state at that moment, and its timing.
   Run the same test on the original image and on every changed build.
7. **Keep it running.** Every change to the source: the matcher or the exact link first, then
   difftest on the changed functions (`difftest.py FUNC ...`), then the end-to-end tests. Report
   each result with its run count, seeds and coverage.

## Traps

- **Packing that silently disappears.** armcc's `__packed` before `struct` is ignored by clang;
  the open build rewrites it. A struct whose fields still move points to another spelling: a
  macro that expands to `__packed`, a typedef of a typedef. Check offsetof in both builds.
- **Enum sizes.** Keep `short_enums` on. An init struct from a vendor library changes layout
  without it, and every function that fills one differs.
- **Neighbours.** Code that reads one variable through another's address is right only in the
  original's RAM layout. Keep `ram_layout` on; when difftest shows a read landing on a different
  symbol, check ramlayout.py placed both.
- **Layout arithmetic in flash.** `base + i * size` over separately defined objects holds only
  where the original linker put them together; the RAM layout does not cover flash. Name each
  object, and prove that change with the matcher.
- **Random state is not reachable state.** A difference that needs an impossible input (a
  peripheral value the hardware never returns, RAM no path writes) is the tester's, not the
  firmware's. Say so in the triage, with the input.
- **One build faulting is a difference.** A wild pointer from random RAM faults in both builds at
  the same address when the code is the same; a fault in one build only is a real lead.
- **Inlining.** Unit mode flags a callee clang inlined. Judge behaviour in deep mode.
- **Zeroed RAM hides bugs.** An emulator that starts from zeros passes code that relies on RAM
  the startup never clears, or on an initialisation made only on some paths. Always test changed
  builds with random power-on RAM.
- **Interrupt handlers in assembly.** The emulator calls a handler as a function; one that reads
  its stacked frame or returns through EXC_RETURN by hand needs care (or a stub).

## Rules

- A difference is explained before it is accepted. "Probably noise" is not an explanation.
- The original's bugs stay in the matched source. A fix is a change to the modded build, made on
  purpose, with its own test.
- The model in an end-to-end test is written from the specification. A model copied from the
  firmware proves only that the firmware agrees with itself.
- No firmware image, toolchain binary or device data goes into Exhume; tests use the synthetic
  firmware in profiles/armcc5/testfw.
