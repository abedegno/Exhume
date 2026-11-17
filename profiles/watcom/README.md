# Watcom C32 / OMF32 / LE

This profile builds DOS-host Watcom C and assembly in DOSBox-X and verifies complete declared OMF32 module contributions against an LE executable. It supports a measured subset, including typed relocations, private symbol scope and explicit segment placements. **LE is supported; LX is not.** Unsupported records and relocation forms fail explicitly.

The tools derive from the [Duke recovery](../../docs/case-study-duke3d.md), with project-specific source, coordinates and supplied components kept outside Exhume. No compiler binaries, proprietary headers/libraries, SDK sources, game files or rebuilt executables are distributed here. [Tool identities](toolchains.json) cover measured Watcom 10.0 GA, 10.0a, 10.0b and 10.5; they do not establish compatibility with every Watcom/Open Watcom release.

## Project configuration

Copy [exhume.toml](examples/exhume.toml) and [modules.json](examples/modules.json) into your project, then supply its sources, reference image, tool homes and link driver. The example's coordinates and symbols are illustrative, not a buildable recovery or measured target. Replace every placement with independently justified coordinates.

`[watcom] modules` selects the JSON manifest. Each module key is a project-relative source path. `placements` maps **one-based OMF segment numbers** to `[one-based LE object number, object-relative byte offset]`; cover exactly every nonempty segment, including constants, initialized data and BSS. `symbols` independently binds external names to the same coordinate pairs. Module publics are checked against any supplied bindings; private names stay module-local. Optional `group_frames` maps one-based OMF group indices to `[LE object, 0]` for supported non-FLAT frame cases.

Module `options` are individual switches; otherwise config/profile defaults apply. `include` adds project-relative header directories. Optional `home` selects that module's tool installation, `module_name` sets C `/nm=`, and `source_name` sets the staged DOS assembly basename. `assembler` selects `wasm`, `tasm31` or `masm51` (the latter two also accept `tasm`/`masm`). C compilation and assembler selection are independent: [TASM 3.1](../tasm31/assembler.md) is an assembler component, not a replacement C/linker profile. Include staging is limited to declared `.h`/`.inc` trees and rejects symlinks.

The runner uses DOSBox-X from PATH or `EXHUME_DOSBOX_X`; generic `[toolchain] dos` selection does not switch this runner. Tool homes remain user-supplied. Receipts pin declared source/header/config/profile bytes; the wrapper does not freeze the full compiler installation, default compiler headers, emulator or link-driver dependencies. Projects must pin those identities separately. See [compiler](compiler.md), [assembler](assembler.md), [linker](linker.md), and the [configuration reference](../../docs/config.md#watcom).

## Public commands

Run these from the Exhume checkout, with your project config:

```sh
python3 tools/build.py --config /path/to/project/exhume.toml --all
python3 tools/match.py --config /path/to/project/exhume.toml src/example.c
python3 tools/verify.py --config /path/to/project/exhume.toml --all
python3 tools/merge.py --config /path/to/project/exhume.toml src/example.c
python3 tools/gate.py --config /path/to/project/exhume.toml check
```

`match` and `verify` freshly build and verify each **complete declared module**. They do not use Borland target-table rows or offer per-function CLI selection/disassembly. `--all` selects all manifest modules; `--no-build` rechecks existing objects, and `--object PATH` reads one explicitly supplied object for match/verify. These read-only checks do not constitute fresh source acceptance. `merge` requires fresh builds and writes `build/watcom/accepted.json`; it does not update Borland `symbols.tsv`/`matched.txt`. Reports may be saved with `--report PATH`; these are historical snapshots, not a latest-attempt status file. Canonical acceptance/gate/module-proof receipts are invalidated before a fresh attempt.

For focused function work, the callable [matching.match_function](tools/matching.py) API accepts a parsed module/public symbol, parsed LE image, target object/offset/size, independent symbol bindings and optional segment/group placements. It verifies supported fixups without masking callee identities; it is not the complete-module CLI or a whole-file proof.

`gate check` (also `gate exact`) freshly proves every declared module, then runs `link.command` as an argument list in the project root, without a shell. The example's `tools/link_project.py` is **your own driver**, not a bundled linker command. It must consume the fresh objects and declare native FILE/library order, tool identities, supplied components and binding steps. Object paths are resolved by [wproject.object_path](tools/wproject.py) into `build/watcom/<STEM>-<source-path-hash>/SOURCE.OBJ`. `link.output` must be a separate project-contained file inside the configured build directory. The gate removes stale output, retains `build/watcom/link.log`, compares the whole output file literally with the reference, and publishes `gate.json` only on success.

A module proof establishes its complete contribution, not complete source coverage. Even a successful exact gate establishes full-file identity separately from whether the project-owned link command compiled every owned span or supplied some original components. Full source coverage, library/provider selection and supplied-component attribution remain project obligations. Raw outputs are frozen and checked through parse/staging/report publication; no normalization, comparison masks or post-link patches produce exact-file success.

## Tests

```sh
python3 -m unittest discover -s profiles/watcom/tests -t .
```

Synthetic format, semantics, snapshot and dispatch tests need no proprietary tools. Native smoke tests skip when unconfigured; they use `EXHUME_WATCOM_HOME` (or `WATCOM_HOME`), `EXHUME_WATCOM_105_HOME`, `EXHUME_TASM_HOME`, `EXHUME_MASM_HOME` and DOSBox-X. Their original small fixtures exercise measured compiler/assembler paths; the Duke game's complete reproduction remains external project evidence.
