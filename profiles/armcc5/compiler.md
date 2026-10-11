# Arm Compiler 5.06u7 (armcc): what matching taught

Evidence is a matched function in a real firmware or a test in tests/.

## Flags

- `-O1` turns a conditional increment into a compare and branch (`cmp; bcs; adds; strh`);
  `-O2` and `-O3` use an IT block (`cmp; it cc; addcc; strhcc`) instead. That makes the
  optimisation level visible in any short conditional.
- `-Otime` divides a 16-bit value by a constant with a multiply and shift (by 10:
  `movw #0xCCCD; muls; lsrs #19` or `ubfx`); without it the compiler emits `udiv`.
- `--split_sections` puts each function in its own section `i.<name>`, with its own literal
  pool straight after it. Without it, the pools of neighbouring functions merge.

## Data

- An uninitialised global (`unsigned char g;`) goes into `.data` with the initialised ones,
  not `.bss`.
- Within `.data` the order isn't definition order: a 1-byte global defined after a 6-byte
  table came first (offset 0, table at 2).
- Uninitialised globals of at most 8 bytes go into `.data`; larger ones into `.bss` (armcc's
  default `--bss_threshold=8`). A 64-byte buffer made the test firmware's zero-init region.

## Libraries

- MicroLIB (`--library_type=microlib`, Keil's "Use MicroLIB") has its own compact startup:
  `__main` (`ldr.w sp,[pc]; bl __scatterload; ldr r0,=main; bx r0`), `__scatterload` walking the
  region table with `ldr r4/ldr r5`, and `__decompress`, `__scatterload_zeroinit` and
  `__scatterload_copy` as handlers. The standard library links `__scatterload_rt2` and
  `__dczerorl2` instead. Both compress RW data in the same format (tools/lz77.py). MicroLIB needs
  `__initial_sp`, which a startup file normally defines.
