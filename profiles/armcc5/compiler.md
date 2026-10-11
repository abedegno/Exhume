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

## Learned while matching a real firmware

- A byte load (`ldrb [r1,#0x61]; tst #2`) testing a bit of a halfword field comes from a 1-bit
  bitfield (`unsigned short b9:1` in a `__packed` struct); an ordinary mask (`u16 & 0x200`) keeps
  the halfword load (`ldrh [r1,#0x60]; tst.w #0x200`).
- An OR of several bitfields compiles to separate byte tests; one halfword test
  (`ldrh; bic #0xf9; bics #0xfb00; beq`) is `*(unsigned short *)&f & 0x406`. A mask that isn't a
  Thumb-2 immediate becomes two `bic`s.
- Zero assignments keep source order, and later zeros copy the first one's register:
  `j = 0; ... for (; j < N; j++)` gives `movs r2,#0` first, where `for (j = 0; ...)` put it last.
- A struct used inside a `__packed` struct must itself be `__packed` (else error #1032);
  halfword fields at odd addresses still compile to plain `ldrh`/`ldrsh` on Cortex-M3.
- A long function can carry a literal pool in its middle, with a `b` over it.
- Compare constants stay as written: `x < 10` is `cmp #10; bhs`, `x <= 9` is `cmp #9; bhi`, so the
  source's `<` or `<=` reads straight off the bytes.
- `uint16 / 250` at -Otime is `(x+1)*0x4189 >> 22` (`movw #0x4189; mla r1,r1,r4,r4; ubfx #22,#8`);
  writing `(x+1)/250` gives `adds; umull; ubfx #4` instead.
- Globals read directly in each condition compile differently from cached locals: direct reads
  hoist one set of loads with a fresh literal and keep a `tst/bne` per term; locals fuse terms
  into `and; and; orrs`.
- In a switch over a global struct, fields used by several cases are loaded once before the
  `tbb`; a field used by one case is loaded inside it.
- `g.buf[g.state++] = c` in a case where the state is a known constant reloads the struct base
  (`ldr r1,=g` again); `g.state = N+1; g.buf[N] = c;` does not.
- Two reads of one byte, one through a struct field and one through a byte-pointer cast, stay two
  loads; the same lvalue twice merges into one.
