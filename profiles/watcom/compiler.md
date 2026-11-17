# Watcom C32: measured support

This profile runs historical DOS-host `wcc386.exe` under DOSBox-X. It searches
BINB (10.0 family) and BINW (10.5) layouts. Tool identities for measured 10.0 GA,
10.0a, 10.0b and 10.5 installations are in `toolchains.json`; no compiler,
headers, runtime libraries or SDK source are bundled. Only these measured
releases are represented; other Watcom and Open Watcom releases need separate
verification.

The original recovery's final complete-source manifest selects compiler
revisions per module, including GA, 10.0a, 10.0b and 10.5. Its scope explicitly
reports `whole_target_match=false` and `source_credit_added=0`. Earlier matching
notes alone do not describe the final source candidate. Neither matching
module bytes nor a complete-source link uniquely identifies the historical
compiler, original identifiers, libraries or flags. Keep those claims separate
from tested build compatibility.

## ABI and code generation

An original synthetic `int twice(int x) { return x*2; }` compiled with
`/5r /s /otexan` produces `01 C0 C3` with both tested GA and 10.5 compilers.
Register-argument function publics have a trailing underscore (`twice_`);
external C data has a leading underscore. Default int/pointer widths are
32 bits. The compiler emits OMF32 records, including SEGDEF32, LEDATA32,
PUBDEF32 and FIXUPP32. Absolute data references and relative function calls
must retain their symbolic targets when compared.

Flags belong to each module. `/4r` versus `/5r` can preserve function bodies
while changing alignment. Loop optimization and alias assumptions change
stack allocation and indexed addressing. Interrupt handlers differ between
measured compiler revisions. Private global names can affect BSS ordering;
declaration order alone need not determine it. Literal padding may contain
compiler-buffer contents. Do not promote an observed flag set, source name,
private identifier or padding pattern into a universal profile rule.

## Runner and tests

`compile_source(home, source, output, options, timeout=30, *, include_dirs=None,
module_name=None)` compiles SOURCE.C inside a private DOS stage. The optional
`module_name` supplies `/nm=` without changing SOURCE.C or its `__FILE__` value.
Only explicitly supplied header directories are copied, limited to `.h` and
`.inc` files and their directories. The toolchain's H directory provides
compiler headers. Include roots and entries inside them must not be symlinks.
Project source trees are never copied wholesale.

Options must be individual switches using the runner's restricted character
set; shell metacharacters, response files and output-name overrides are
rejected. Native module IDs and assembler filenames must be DOS-safe. Options
are trusted build configuration, not a sandbox for hostile compiler input.
Both tool mounts are writable DOSBox drives; source compilation is not a
security boundary for untrusted source or tool executables.

Successful batch status plus a newly produced nonempty OUT.OBJ is required.
Diagnostics go to a sibling `.log`; failure, missing output and timeout leave
an existing object intact. DOSBox-X is found on PATH or via EXHUME_DOSBOX_X.
The default timeout is 30 seconds. No NASM producer is included, so NASMENV
handling is outside this runner's support.

Synthetic tests require no proprietary tools. Optional native tests use
EXHUME_WATCOM_HOME (WATCOM_HOME is also accepted), EXHUME_WATCOM_105_HOME,
EXHUME_TASM_HOME and EXHUME_MASM_HOME. They skip cleanly when unconfigured.
The smoke checks exercise GA and 10.5 compilation, project headers, native
module IDs, invalid C, missing output, real timeout, WASM, TASM 3.1 and MASM
5.10 assembly. Version-specific target matching remains project evidence.
