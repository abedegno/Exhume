# Toolchain profiles

A profile is what is true of one compiler, assembler and linker rather than of one game. Each lives in its own directory:

- `profile.toml`: what the tools read. Build command lines (`{opts}`, `{file}`), default switches, the code segment class, the public name prefix and length limit, build-log patterns, library-name patterns for symbols.tsv, the linker's call rewrite, the far return byte, fingerprint strings and the DGROUP anchor, expected MD5s of the executables.
- `compiler.md`, `assembler.md`, `linker.md`: what matching taught about each tool, with the evidence.
- Profile-specific tools and setup scripts (for Borland: `bssorder.py`, `setup-tc.sh`, `setup-tasm.sh`).

A project names its profile in exhume.toml (`[toolchain] profile`).

## borland-tc101

Turbo C++ 1.01, Turbo Assembler 2.0, Turbo Link 3.01 and the VROOMM overlay manager. Learned on Ultima Underworld II. Complete for the medium model with overlays; other memory models are untested.

## Profiles that would come naturally next (not written)

- **Borland C++ 2.0 / 3.x and Turbo C 2.0.** The nearest neighbours: same object format, same linker family and overlay manager in 3.x, much of compiler.md likely to carry over but every rule to be re-proven (the `_BSS` hash and public order in particular).
- **Microsoft C 5.1 to 7.0 and MASM 5.1/6.0 with LINK.** The other large DOS family. Different runtime strings, `_TEXT` naming and frame habits, its own overlay scheme (MOVE in 7.0 or the older static overlays), no FBOV. MASM 5.1 is also single-pass, and some of UW2's overlay manager code looks MASM-assembled, so assembler.md's padding rules are the place to start telling them apart.
- **Watcom C/C++ 9.x to 11 with WLINK, often with a DOS/4GW extender.** 32-bit flat code and register calling (UW2's FM Towns sibling is Watcom code). It needs an LE/LX reader beside the MZ one, and a different model of fixups.
- **DJGPP (GCC for DOS, COFF in a stub EXE).** Matching GCC means pinning the exact GCC and binutils versions and flags; register allocation is far less predictable than Turbo C's.

Each needs its own fingerprint strings, build commands, log patterns, object-format support where it is not OMF, and its compiler.md written from matched code, not from documentation.
