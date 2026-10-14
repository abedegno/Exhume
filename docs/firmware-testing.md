# Testing a matched firmware beyond the bytes

The matcher and the exact link prove that the source compiles, with the original's compiler, to
the original's bytes. They say nothing about a build that is meant to differ: a readable rewrite
of a function, a fix, a modded build, or the same C built by another compiler. This page is the
armcc5 profile's answer for Cortex-M firmware, the counterpart of docs/port.md's replays and
fuzzing for DOS programs. Three tools, in profiles/armcc5/tools:

| Question | Tool |
|---|---|
| Does each function of another build do what the original's does? | emu/difftest.py |
| Does the whole firmware start, run its main loop and answer on its interfaces? | emu/sysemu.py |
| Does the source hold up under a second compiler? | openbuild/openbuild.py, then both |

They need Python with `unicorn` and `capstone` (Exhume's .venv has both), and for the open
build clang and the `arm-none-eabi` binutils. Configuration is `[difftest]`, `[sysemu]` and
`[openbuild]` in exhume.toml (docs/config.md). skills/firmware-verify is the working order.

## Differential tests

`emu/difftest.py` runs every function of two builds side by side in Unicorn (Cortex-M, Thumb) on
the same random inputs and compares what each does. The reference (A) is the original image; the
candidate (B) is any build of the source. Symbols are matched by name, so B may be laid out
anywhere: its own ELF gives its addresses (`--b-syms`).

Each run, from a seed:

- **RAM.** The same random bytes in both builds, given symbol by symbol: each data symbol's bytes
  at that symbol's address in each build. Half the time a symbol gets small values (0 to 3, 0xFF,
  a low nibble), which reach the code's thresholds and states far more often than random words.
- **Arguments.** r0 to r3, typed from the C definition: integers as wide as their type, with the
  sign extension AAPCS callers do, a third of the time an edge value (0, 1, 0x7F, 0x80, 0xFFFF,
  0x80000000); pointers into a scratch region at the same address in both builds.
- **Peripherals.** Every read returns a value drawn from the seed, the register's address and how
  many times it has been read, so a polling loop sees its bit set sooner or later in both.
- **Compared afterwards.** r0 as wide as the C return type, every RAM symbol's bytes, the scratch
  region, and the sequence of peripheral writes (register, width, value). With `--mode unit` the
  calls to other functions are stubbed and the calls (callee and r0 to r3) compared too.

Two builds laid out differently hold different addresses by design. A 32-bit value that points
into code or data counts as the same when it points to the same symbol and offset in each build
(a DMA address, a pointer kept in a struct), or into read-only data with the same bytes (a string
literal has no symbol). Initialised data whose initial bytes differ between the builds (tables of
function pointers) start from each build's own values with `--init-rw`, read from the ELF's
writable sections or from an armlink image's scatter-load table, and count only where a function
changes them.

A run cut short stops at a different point in each build, since they take different numbers of
instructions to the same place. Only a fault at the same address in both counts as the same; a
run that hits the instruction budget is reported as cut short and not compared. `main` and other
loops that never return always are, which is fine. A fault in one build only is a difference.

Coverage is each function's share of instructions some run reached in A. Literal pools decode as
instructions, so a function rarely shows 100%. Low coverage means the inputs do not reach the
code: more runs, or a test that sets up the state the function needs.

Deep mode, the default, runs callees too, so a callee inlined in one build and not the other
does not matter. Unit mode is the finer check for a function rewritten on its own, and it does
flag inlining: a callee missing from B's calls.

The tool checks itself: `--mutate FUNC=OFF:BYTE` patches one byte of B. A changed instruction
should show within a few runs; if it does not, the function's inputs are not reaching it.

    T=profiles/armcc5/tools
    python3 $T/emu/difftest.py --b build/exact.hex        # the exact link: all the same
    python3 $T/emu/difftest.py --b build/open/app.elf --b-syms build/open/app.elf --init-rw
    python3 $T/emu/difftest.py --runs 1000 --seed 7 crc16 next_frame
    python3 $T/emu/difftest.py --mutate crc16=2:0x00 crc16

Limits. A function runs alone from a random state, not from a state the firmware can reach.
A difference that needs a long sequence of calls (a filter's history, the tenth step of a state
machine) shows only if the random state happens to hold it; the system emulator covers that.
And a random state can reach paths the firmware never takes, so a difference is a lead, not yet
a bug: triage it (below).

## The system emulator

`emu/sysemu.py` runs the whole image from reset: SP and PC from the vector table, the startup
code (the C library's scatter-load included), `main` and its loop. Time advances in emulated
milliseconds of `instr_per_ms` instructions; between milliseconds each peripheral model ticks and
interrupts are delivered.

An exception is delivered by calling its handler from the vector table as a function, on the
interrupted stack, with the interrupted registers saved and restored around it. A Cortex-M
handler is an ordinary AAPCS function, so this is what the hardware does, less the stacked frame
and EXC_RETURN. SysTick, PendSV and every IRQ a model asserts or the NVIC holds pending are
delivered when enabled and PRIMASK is clear, lowest exception number first, without nesting or
priorities. A handler written in assembly that inspects its stacked frame or returns through
EXC_RETURN needs the hardware's way, which this does not model.

The models (`emu/stm32f1.py`, STM32F1-class and register-compatible parts) are as far as a
firmware needs them to start and run, not a datasheet's worth:

- RCC: each oscillator and the PLL ready as soon as switched on, the clock switch status
  following the switch. Start-up code's wait loops end at once.
- SysTick, NVIC enables and pending bits, VTOR, AIRCR's reset request (in the core, sysemu.py).
- USART/UART: bytes received at the baud rate, one in DR at a time, an overrun counted when DR is
  not read in time; bytes sent kept with the time they would leave the line, the TXE interrupt
  paced at the baud rate.
- GPIO: inputs a test drives, the output latch (ODR, BSRR, BRR) with a history of changes.
- Flash: the unlock sequence, page and mass erase, programming with PG (a halfword that is not
  erased sets PGERR; a write without PG is an event).
- General-purpose timers' update interrupt, ADC1 with the DMA filling a scan-mode buffer, the
  independent watchdog (expiry is a reset), and the device information block (flash size,
  unique ID, IDCODE).

A model is a small class: a base address and size, `read(off, size)` returning the value the CPU
sees (or None for plain memory), `write(off, size, value)`, `tick()` each millisecond and
`irqs()` for the lines it asserts. Anything device-specific is the project's own: a plugin file
named in `[sysemu] plugins`, with `attach(machine, options)` adding its models and stubs.

A device the firmware drives at a level too fine to model well, such as a bus bit-banged on GPIO
pins, is better reached through the firmware's own bus functions: `Machine.stub(name, fn)`
replaces a function with Python that returns to its caller. `emu/example_device.py` is the
pattern, a made-up register-file device on a bit-banged I2C bus: stub start, stop, write-byte and
read-byte, and the device sees whole transfers.

Power-on RAM. `fill_ram(seed)` (`[sysemu] ram_fill`) puts random bytes in RAM before reset. Real
RAM holds whatever it holds at power-on; the startup code clears only the regions the linker gave
it. Code that relies on RAM nobody cleared, or on an initialisation call the firmware makes only
on some paths, works in an emulator that starts from zeros and fails on the device. Test every
change with a few seeds.

Driving it from a test:

    m = sysemu.Machine(image, syms, flash=(0x08000000, 0x10000), ram=(0x20000000, 0x5000))
    stm32f1.add_all(m); m.fill_ram(seed); m.boot(); m.run_ms(500)
    m.periph['USART1'].rx(request); m.run_until(lambda m: len(m.periph['USART1'].tx) >= n, 200)

The strongest test of a protocol the firmware speaks is a second implementation of it: a model
in Python, written from the specification and not from the firmware, that says what the reply to
each request should be for the firmware's state at that moment (read from its RAM). Send the
requests a real peer sends, at its real byte rate, and compare every reply byte for byte, and its
timing. Random power-on RAM and a few minutes of firmware time per scenario find what unit tests
of the handlers do not.

Limits. No cycle timing: `instr_per_ms` only decides how much main-loop work fits in a
millisecond. At most one SysTick or timer update a millisecond. An interrupt arrives only at a
millisecond's edge, never inside a critical section that lasts less. A fault stops the run
rather than entering HardFault.

## The open-toolchain build

`openbuild/openbuild.py` builds the same C with clang (Thumb, Cortex-M3 by default) and GNU ld.
It is not byte for byte, which is the matcher's job; it is a second build to hold against the
original with the differential tests and to boot in the system emulator. It finds what only one
compiler makes work, and it is where a port to a newer chip or toolchain starts. Nothing it needs
goes in the matched tree: it builds a rewritten copy under `[openbuild] out`.

What it takes for armcc's C to mean the same under clang:

- **compat.h**, force-included: armcc's keywords (`__weak`, `__irq`, `__forceinline`,
  `__align`, `__declspec`) and intrinsics (`__nop`, `__wfi`, `__disable_irq` returning the old
  mask). A project whose CMSIS headers define the intrinsics for GCC-like compilers passes
  `-DEXHUME_NO_INTRINSICS`.
- **`__packed` before `struct` or `union`** is rewritten after the keyword. Clang ignores an
  attribute in that place, silently with warnings off, so the struct is unpacked and every field
  after the first unaligned one moves. On a field (`__packed unsigned short t;`) compat.h's macro
  is enough. A `__packed` pointer has no clang spelling; Cortex-M3 and later load and store
  unaligned halfwords and words anyway (not LDRD, LDM or STM, which fault).
- **`-fshort-enums`.** armcc gives an enum the smallest integer type that holds its values,
  clang an int. Every struct with an enum field changes layout without it, library init structs
  included.
- **Embedded assembler** (`__asm { ... }`) has no clang form; each use becomes GCC-style inline
  assembly through an `[openbuild] rewrite` pair. They are rare (a `MSR` before a reset).
- **The library.** runtime.c and runtime.S give what armcc's C reaches without naming it: memset,
  memcpy, the run-time ABI's `__aeabi_mem*`, 64-bit division and shifts. Anything else the
  firmware calls (a small `sprintf`, float helpers) the project adds with `[openbuild] extra` or
  links from compiler-rt with `libs`. Write it from the C standard's contract, not from the
  original's code.
- **The original's RAM layout** (`ramlayout.py`). Decompiled source reaches data through a
  neighbour's address more often than one would think: a wide view of one variable that covers
  the next, a table read past its end, `&a + n` for `b`. Each is right only where the original
  compiler and linker put the data. The generated linker script puts every RAM symbol the map
  knows at its original address (with `-fdata-sections` each variable is a section of its own,
  matched by name). Initialised and zero-initialised data share one output section loaded from
  flash, so a variable one compiler made `.data` and the other `.bss` still lands in place.
  armcc keeps small zero-initialised variables among a file's initialised ones; an ordinary
  open link moves them to `.bss`, and their neighbours change.
- **Over-alignment.** Clang may align a global more than the original did (a variable whose
  address is taken as a wider type, a packed struct). Where the original address is not a
  multiple of that, ramlayout.py lowers the section's alignment in the object with
  `objcopy --set-section-alignment`. GNU ld stops with "cannot move location counter backwards"
  when a variable has grown into the next: a layout difference to find before testing anything.
- **Addresses computed from a layout.** Source that reaches the i-th of several separately
  defined objects as `base + i * size` holds only where the original linker put them one after
  another. In flash this is not fixed by the RAM layout; name each object instead, and prove the
  change with the matcher as any change to the matched tree.
- **No startup by default.** For difftest the build needs no vector table; to boot it in the
  system emulator, add a startup file with `[openbuild] extra` that copies `_ram_load` to
  `_ram_start`..`_ram_end` and calls main (the test firmware's `boot_gnu.S`).

    python3 $T/openbuild/openbuild.py
    python3 $T/emu/difftest.py --b build/open/app.elf --b-syms build/open/app.elf --init-rw

Expect most functions to behave the same at once and a tail of differences, nearly all
explained by the items above. What remains is the interesting part.

## Triage: firmware bug or build bug

A difference is a lead. Reproduce it with its seed (`--runs 1 --seed N FUNC`), read the first
mismatch, then sort it:

**A build bug** (the open build differs where the original's C meant one thing):

- A field at a different offset in a struct symbol's bytes: packing lost, an enum's size, an
  over-aligned member. Compare `offsetof` in both builds before reading any code.
- A read that lands on another variable: the RAM layout (is the open build using it, did
  ramlayout.py report a name it could not place?), or an address computed from a layout.
- A library call that answers differently: the open build's stand-in, against the C standard.
- In unit mode only, a callee missing or added: inlining. Use deep mode to judge behaviour.

Fix a build bug in the open build (compat.h, a rewrite, a flag, the layout). Fix the source only
where it relied on a layout no compiler promises, and then prove the change against the original
bytes as any change to the matched tree.

**A firmware bug** (the original's own C is wrong, and each build is wrong its own way):

- An uninitialised local on some path: each build returns its own leftover register or stack
  slot. Typically a helper that writes its result only when its input is valid.
- A loop or index past the end of a table: each build reads whatever it placed after the table.
- Reliance on RAM nobody cleared, or on an initialisation done only on some paths: shows with
  random power-on RAM in the system emulator, not in difftest.

Record each in the project's findings with the input that shows it and whether it matters in
practice (a byte that is always valid BCD, a current no installation reaches). Keep the bug in
the matched source; a fix is the modded build's decision, made on purpose.

**The emulator, not the firmware**: a run cut short in one build by a wild pointer built from
random RAM, a peripheral value the hardware never returns, a path only random state reaches.
Rerun with more seeds; if only impossible states show it, say so and move on.

Rules: a difference is explained before it is accepted, never waved through as noise; the same
function with the same seed must give the same answer every time (the tools are deterministic);
and every claim of "behaves the same" comes with its run count and coverage.

## Tests

profiles/armcc5/tests/test_emu_core.py (Unicorn only: the models, the core and the comparisons
on hand-encoded Thumb), test_emu_openbuild.py (clang and binutils: the open build boots, two
optimisation levels agree, and each build bug above is caught when its remedy is turned off) and
test_emu_armcc.py (the armcc toolchain: the original-style build boots, agrees with itself, and
the open build in its RAM layout agrees with it). They use the test firmware in
profiles/armcc5/testfw: emu/system.c (clocks, SysTick, a timer, a UART that answers lines,
flash, GPIO, a bit-banged I2C device) and emu/quirks.c (one example of each compiler- or
layout-dependent pattern), with crc.c and the rest of testfw/src.
