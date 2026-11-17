# Turbo Assembler 3.1

This is an assembler component. A project may select it for one module while
using Watcom C and other assemblers for other modules. It does not select a C
compiler, linker, memory model or DOS extender.

The default `/ml` preserves symbol case. Flat-mode and dialect switches belong
to the module: measured sources used combinations of `/t /p /mx /w2`, and
`/q /t /ml /d__FLAT__ /j.386p`. They are not universal defaults. Source names
can change native OMF module identity; pass a DOS-safe `source_name` to the
runner when this matters. The runner's default name is SOURCE.ASM. Explicit
`include_dirs` are staged as header-only INCn trees and passed using `/iINCn`;
symlinked include roots or entries are rejected.

`profiles/watcom/tools/runner.py` supports `producer='tasm'` independently of
its default WASM producer. It stages original source in a private directory,
checks assembler status and fresh nonempty output, and preserves any previous
object on failure. Synthetic source integration checks cover a USE32 public
function with an external call, and invalid assembly.

## Local installation

Obtain the preserved Borland C++ 3.1 archive independently. No binaries or
vendor source are distributed here. The pinned source archive is linked from
https://winworldpc.com/download/f7396558-9ba2-11e9-ab10-fa163e9022f0 .

Run:

```
python3 profiles/tasm31/setup.py --archive /path/to/borland-cpp-3.1.7z --destination /path/to/tasm-3.1 --manifest /path/to/tasm-3.1.json
```

The setup requires 7z. It checks the original archive SHA-256, extracts Disk07
and Disk09 images, checks the two split package parts, removes their five-byte
headers and extracts only TASM.EXE and TASMX.EXE. It verifies both executable
hashes before publishing a new installation directory. It never executes the
installer or overwrites an existing destination. The manifest records media
provenance and tool identities. The runner uses TASM.EXE; TASMX.EXE is preserved
as a verified alternative but is not selected implicitly.

Set `EXHUME_TASM_HOME` to that directory to run the optional native tests.
