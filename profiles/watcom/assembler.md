# Assemblers are independent producers

The native runner defaults to Watcom WASM. Pass `producer='tasm'` or
`producer='masm'` per module for Turbo Assembler or Microsoft Macro Assembler.
A C compiler selection does not permanently select an assembler.

WASM uses the Watcom BINB/BINW layout, WATCOM/INCLUDE/PATH environment, and
`wasm SOURCE.ASM /fo=OUT.OBJ {options}`. TASM.EXE and MASM.EXE are expected in
the selected assembler home. TASM takes `SOURCE.ASM,OUT.OBJ`; MASM takes
`SOURCE.ASM,OUT.OBJ,NUL,NUL;` to suppress interactive output prompts. The
optional `source_name` is the actual staged DOS basename and preserves native
OMF module naming. Each run requires successful status and a fresh object.

The keyword-only `include_dirs` argument stages explicitly supplied `.h` and
`.inc` header trees into private INC0, INC1, etc. directories. WASM receives
`/i=INC0`, TASM `/iINC0`, and MASM `/IINC0`; installed-tool smoke tests verify
these forms with an original include-defined constant used in generated code.
Include roots and entries must not be symlinks. The source filename and module
identity stay independent of the include staging.

Native smoke tests assemble original small USE32 sources with public symbols
and external calls using GA WASM, TASM 3.1 and MASM 5.10. This demonstrates
those command paths; it does not establish full dialect compatibility or
that one assembler reproduces every source contribution. A source using IDEAL
syntax is not automatically accepted by WASM or MASM. See `../tasm31` for the
independent TASM component and pinned media setup. MASM installation is
project supplied. NASM and GNU/objconv are separate pipelines, not implicit
fallbacks, and are not implemented by this runner.
