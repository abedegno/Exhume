# Toolchain profiles

A profile is what is true of one compiler, assembler and linker rather than of one game. Each lives in its own directory:

- `profile.toml`: what the tools read. Build command lines (`{opts}`, `{file}`), default switches, the code segment class, the public name prefix and length limit, build-log patterns, library-name patterns for symbols.tsv, the linker's call rewrite, the far return byte, fingerprint strings and the DGROUP anchor, expected MD5s of the executables.
- `compiler.md`, `assembler.md`, `linker.md`: what matching taught about each tool, with the evidence.
- Profile-specific tools and setup scripts (for Borland: `bssorder.py`, `setup-tc.sh`, `setup-tasm.sh`).

A project names its profile in exhume.toml (`[toolchain] profile`).

## borland-tc101

Turbo C++ 1.01, Turbo Assembler 2.0, Turbo Link 3.01 and the VROOMM overlay manager. Learned on Ultima Underworld II. Complete for the medium model with overlays; other memory models are untested.

## armcc5

Arm Compiler 5 (armcc 5.06, armasm, armlink, fromelf) from Keil MDK, for Cortex-M firmware, run as
MDK-Lite (no licence; a 32 KB link limit, which the whole-image gate works around by linking in
pieces) under Wine, in Docker or on the host. Learned on a Cortex-M3 firmware of about 500
functions: every function matched in C, the image rebuilt byte-identical from its pieces, its const
data, region table and compressed RW data; then a second, built at `-O0` without
`--split_sections` with the standard C library, drafted from the image by `tools/o0c.py` and
rebuilt byte for byte, one of its files (over the Lite link limit) proved by object placement
(`tools/objplace.py`). The tools read Intel HEX update files and ELF objects;
`compiler.md` and `linker.md` hold what matching taught, `match-brief.md` is the brief for matching
agents. Beyond the bytes: differential tests, a Cortex-M system emulator and an open-toolchain
build (docs/firmware-testing.md, skills/firmware-verify).

Next for this family: other armcc 5 updates (each update changes code generation: pin the exact
build first), Arm Compiler 6 (armclang, LLVM-based: a different profile), and GCC for Arm
(arm-none-eabi-gcc; the profile's open-toolchain build uses clang with GNU ld for testing).

## ti-c2000

TI's C2000 code generation tools (cl2000, lnk2000, hex2000, with ofd2000, dis2000 and ar2000) for
the C28x with the FPU, COFF ABI. The compiler is TI's Windows build under Wine (every version
from TI's public download server), and the object tools come from a native macOS or Linux build
(18.12 and later). Learned on a TMS320F2806x firmware: every word of the image rebuilt from
source, with no blobs, every function in C except a few that were assembly in the original. The
tools (`profiles/ti-c2000/tools/`) read the project's config: the toolchain runner, Wine job
prefixes and object reader (`c28tc.py`), the map (`c28map.py`), disassembly through a patched
placeholder object (`c28disasm.py`), per-function matching by linking each source at its real
addresses (`c28match.py`), the whole-image gate (`c28gate.py`), the `.ebss` order solver
(`c28ebss.py`), the runtime-library fingerprint (`c28libfp.py`) and a batch search for one
function's variants (`c28batch.py`). `compiler.md` and `linker.md` hold what matching taught,
`match-brief.md` is the brief for matching agents. Its tests run without the toolchain; the
integration tests build a synthetic firmware with TI's compiler when one is named in the
environment (`profiles/ti-c2000/tests/synth.py`).

Next for this family: EABI (from 18.12; other section names and an ELF object reader), the CLA
compiler, and the C2000 parts without the FPU (`rts2800_ml.lib`).

## Profiles that would come naturally next (not written)

- **Borland C++ 2.0 / 3.x and Turbo C 2.0.** The nearest neighbours: same object format, same linker family and overlay manager in 3.x, much of compiler.md likely to carry over but every rule to be re-proven (the `_BSS` hash and public order in particular).
- **Microsoft C 5.1 to 7.0 and MASM 5.1/6.0 with LINK.** The other large DOS family. Different runtime strings, `_TEXT` naming and frame habits, its own overlay scheme (MOVE in 7.0 or the older static overlays), no FBOV. MASM 5.1 is also single-pass, and some of UW2's overlay manager code looks MASM-assembled, so assembler.md's padding rules are the place to start telling them apart.
- **Watcom C/C++ 9.x to 11 with WLINK, often with a DOS/4GW extender.** 32-bit flat code and register calling (UW2's FM Towns sibling is Watcom code). It needs an LE/LX reader beside the MZ one, and a different model of fixups.
- **DJGPP (GCC for DOS, COFF in a stub EXE).** Matching GCC means pinning the exact GCC and binutils versions and flags; register allocation is far less predictable than Turbo C's.

Each needs its own fingerprint strings, build commands, log patterns, object-format support where it is not OMF, and its compiler.md written from matched code, not from documentation.
