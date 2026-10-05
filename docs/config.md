# exhume.toml

Every Exhume tool reads one project file. It is found from `--config PATH`, then the `EXHUME_CONFIG` environment variable, then the first `exhume.toml` in the current directory or a parent. `python3 tools/config.py` prints how it resolved. The full example is `examples/uw2/exhume.toml`.

Paths are relative to `[project] root` (and root to the config file) unless absolute. `~` and `${VAR}` / `${VAR:-default}` are expanded in every string, so one file can serve a checkout in place and a run that writes elsewhere.

## [project]

- `name`: a label.
- `root`: the decompilation repository.
- `src`, `targets`, `symbols`, `matched`, `map`: sources, target tables, the symbol map, the list of matched segments, the map directory. Sources may sit in subdirectories of `src` (one per subsystem, say); every tool finds them through tools/sources.py, by stem (the file name without extension, upper case, unique across the tree) or by DOS segment.
- `exclude`: directories under `src` (relative to it) that hold no sources for the DOS build, such as a host port's own code; never searched for sources (UW2: `port`).
- `include`: the shared headers (default `src/include`), never searched for sources. tools/build.py stages them in C:\ beside each source, and tools/srcdeps.py hashes a source together with the headers it includes, so the gate and the modding build recompile every includer of a changed header. The runtime's `include/` (portable.h; `[port] runtime`) is staged and hashed the same way, after these, so a project header of the same name wins.
- `build`: where objects (`build/STEM/STEM.OBJ`, with `BUILD.LOG`), the link and the queue go. It may be outside root; Exhume's own proof sets it to `~/Exhume/build/uw2` so the UW2Decomp checkout is never written.
- `queue`: the build queue directory, relative to build.

## [binary]

- `exe`: the original executable, the user's own copy.
- `dgroup_para`: DGROUP's load paragraph (DS:0 is at header + paragraph * 16). `tools/fingerprint.py` suggests it; confirm it with a string the code references.
- `overlay_segment`: a regular expression matching overlay target names, whose first group is the overlay's index in the overlay segment table (UW2: `ovr(\d{3})`).
- `listing`, `listing_para_bias`, `listing_stub_prefix`: the IDA listing, what IDA adds to load paragraphs in segment names, and the prefix of its overlay stub segments.
- `listing_segments`: load paragraphs for listing segments whose names carry none (UW1's listing names segments `seg004`, `seg051`). Code segments come from `<map>/segments.tsv` without it; list the data segments the modding build must read `dw offset` tables in (UW1: `{ seg051 = 0x4723 }`).

## [toolchain]

- `profile`: a directory under profiles/.
- `home`: the compiler's directory (headers, libraries, startup source), read by the link.
- `stage`: what is copied into C:\ for every DOS run: a directory's contents, or a single file.
- `c_opts`, `asm_opts`: defaults for sources with no `/* opts: */` line (otherwise the profile's).
- `dos`: the DOS the toolchain runs in: `emu2`, `dosbox-x`, `staging`, `jsdos` or `auto` (the default: emu2 if built, else DOSBox-X, else js-dos). The environment's `EXHUME_DOS` wins over it. docs/method.md, "Choosing the DOS".
- `sessions`: DOS sessions at once for the gate, `build.py`, the modding build and `buildd.py` (default: one per core up to 12 in a native DOS, 3 in js-dos). The environment's `EXHUME_DOS_SESSIONS` wins over it.

## [names]

- `original_symbols`: a symbol table whose names count as original (the sibling's). symbols.tsv labels such names with `original_label`, runtime helpers `library`, and the rest `provisional`.

## [sibling]

A second build with symbols. `image` (a flat code image), `symbols` (TSV: name, hex address, size), `bits` (16 or 32), `code_end` (symbols above it are data), `extra_anchors` (optional TSV of hand-proven DOS name to sibling name pairs, header line first).

## [map]

- `library_segments`: segments holding startup code and the C library, left out of the alignment.

## [layout]

For `tools/addrscan.py`, which lists addresses written as numbers (docs/link.md, "Layout audits"). All optional.

- `far_data_entries`: `[first, last]` overlay segment table entries that hold far data; their paragraphs are reported as `FDnn`. Without it, every entry with flags 0 other than DGROUP's counts.
- `library_objects`: stems, or target segments, whose objects are not scanned because the link takes that code from a library (UW2: `seg046`, the overlay manager).
- `dispatch`: code entered through a dispatcher. Each entry is `{ register = "bp", ds = "FD51", es = "FD51", ss = "FD51" }`: a label whose offset is loaded into that register (`mov bp,offset X`) is entered with those segment registers.

DGROUP is found by `[binary] dgroup_para`. The modding build also reads `[binary] listing` and `listing_para_bias`, for the `dw offset` tables in extracted data.

## [gate]

For tools/gate.py, the build driver and gate (docs/readability.md, "The gate"). Commands are split like a shell line, may use `{python}`, `{exhume}`, `{config}`, `{root}` and `{build}`, and run in the project root.

- `link`, `mod_link`: the exact link and the modding build (UW2: `examples/uw2/link.py`, with `--mod` for the second).
- `exact_exe`, `mod_exe`: where they write the EXE, relative to build.
- `known_diffs`: optional, and best left out. Without it the exact link must be byte-identical to the original, as UW2's is. A project whose exact link still gets a few bytes wrong can list each as `[offset, original byte, linked byte]` while it looks for the cause; then any other difference fails the gate, and so does one of these not differing. UW2 had two such bytes until their cause was found (profiles/borland-tc101/linker.md, "Segment classes").
- `sessions`, `batch`: DOS sessions at once for the gate alone (default: `[toolchain] sessions`, else what suits the DOS in use; `EXHUME_DOS_SESSIONS` wins) and sources per session (default 8).
- `boot`: rungame.mjs steps for `gate.py boot`, which also uses [run].

## [cparse]

Optional, for the parser behind declinv.py, structrec.py and headergen.py (tools/cparse.py, `setup()`). It learns all three from the headers in `[project] include` without this section, which adds or overrides:

- `typedefs`: `{name = "type"}`, a typedef's type as the DOS compiler sees it, for struct layout, signedness and parameter names. From the headers, a typedef takes its first definition whose type is known (portable.h's `#ifdef __TURBOC__` branch: `int16` is `int`), and one the headers define one way only (`InputFn`) is also written out when declarations are compared.
- `empty_macros`: names of object-like macros the DOS build defines empty (`HOST_LAYOUT_BEGIN`), which the parser reads as nothing rather than as part of the next declaration. From the headers: any macro some branch defines empty.
- `param_macros`: names of one-parameter macros that wrap a parameter list (`int far f OLDSTYLE((char c));`). From the headers: a macro whose body is `()` or its parameter; with `()` the declaration is old-style to the compiler.

## [repocheck]

Optional, for tools/repocheck.py when the config sits at the repository root: `ban`, more file extensions that must never be committed (a game's data formats), and `allow`, regular expressions for paths exempt from the binary checks.

## [run]

Notes for rungame.mjs (`data`, `exe_name`, `skip`), which takes them as arguments.

## [port]

For a native port of the matched tree (docs/port.md): tools/portcheck.py, portbuild.py, portstubs.py, layoutcheck.py, intaudit.py and widths.py. tools/portcfg.py has every key with its default; the ones a project usually sets:

- `dir`: the project's own port C (default `src/port`): its bindings for the runtime (`portgame.h`, `asmgame.h`, `ailgame.h`), its replacements for the game's assembly modules and DOS-only C, the translated modules, the link stubs. List it in `[project] exclude` so the DOS build never sees it. It is on the port's include path ahead of the runtime's directories.
- `runtime`: Exhume's runtime (default this checkout's `runtime/`), which the tools compile where it is (docs/port.md, "The runtime"): its `port/` C with the project's, its `replay/replay.c` as shared C, and its `include/portable.h` staged beside `[project] include` for every DOS build. `runtime_exclude`: paths under it the project does not build.
- `compat`, `stand_ins`: the header force-included into every game source and the stand-ins for the compiler's headers (default the runtime's `port/compat.h` and `port/include`).
- `shared`: directories of the project's own C that both the port and the replay DOS build compile and the gate does not (default none; the runtime's replay.c always is); list them in `exclude` too.
- `out`, `exe`, `name`: the build directory under `[project] build` (debug and coverage builds go to `OUT-debug` and `OUT-cov`), the program's name, and the prefix of its messages (default the program's name).
- `backend`: the platform backend under the runtime's `port/platform/` (default `sdl3`).
- `optimise`: directories under `dir`, and the runtime's `port/` directories of the same names, compiled with `-O2` in the normal build.
- `dos_only`: the header comment that keeps a game source out of the port (default `port: dos-only`).
- `[[port.vendor]]`: third-party C a setup script fetched and the port compiles when present (`name`, `dir`, `sources`, `define`, `for`: the port file that gets the define, `missing`); `[[port.pkg]]`: libraries linked when pkg-config finds them (`name`, `define`, `for`, `label`, `missing`). Neither is ever committed (docs/third-party.md).
- `[port.stub_notes]`: what each generated stub file holds, for its header.
- `[port.layout]`: `file_records` (`{tag = why}`, the structs read from or written to files, which must keep their DOS layout), `io_calls` (the calls that move a record as bytes), `probe` (the DOS compile line of the layout probe).

## [replay]

The record and replay harness and its golden references (tools/replay.py, golden.py, replaydos.mjs; docs/port.md, "Verifying a port"). The keys, all in examples/uw2/exhume.toml:

- `sessions`, `golden`: the recordings (`NAME.rec`, with `NAME.cfg` for a session with its own configuration) and their goldens.
- `magic`: the four characters that start a recording (the bindings' `RP_MAGIC`).
- `exe_name`, `data`, `data_skip`: the name the replay DOS build runs under, the game's directory staged as `C:\`, and paths left out of it.
- `cfg_path`: where a session's configuration goes in the game's tree, in DOS and in the port's home directory.
- `saves`: directories of saved games a session writes, compared byte for byte.
- `link`: the modding link command, to which tools/replay.py adds `--out`, `--obj STEM=PATH` and `--add STEM=PATH` (the shared C).
- `shared_opts`: the compiler switches of the shared C in the DOS build.
- `hooks`: the portable.h hooks whose use means a source is compiled with `-DREPLAY`.
- `env`, `out_files`: the environment variables passed to the replay DOS build when set (default `UWRPCK`, `UWRPTRACE`, `UWRPFB`, `UWRPFULL`, `UWRPHOOK`: runtime/replay/replay.c's first comment) and the files a DOS run brings back.
- `port_args`, `port_sound_logs`, `driver_check`, `driver_marker`: how the port is run under replay, the flags of its sound driver logs, and the command that checks them against the real driver (`{ail}`, `{hw}`).
- `order`, `stage_from`: the sessions' order, and the sessions that replay a saved game another one writes.
- `dos_backend`: sessions whose DOS replays must run in a particular DOS, `jsdos` or `dosbox-x` (`{ session = "jsdos" }`), for `dos`, `check` and `golden`; the others use DOSBox-X when it is installed (UW1's `sound` replays in js-dos, since in DOSBox-X its replay hangs when the introduction's speech starts).
- `c0_signature`, `c0_signature_at`: the C runtime's copyright string and its DS offset, by which the null-pointer check finds DGROUP in js-dos after the program exits (default the profile's).
- `[replay.steps]`, `[replay.derive]`, `[replay.cfgs]`: each session's steps for tools/replaydos.mjs (`@NAME` takes another list's steps; a name starting with `_` is not a session), sessions made from others (`from`, `wait_scale`), and the configuration files recorded with sessions.
- `[[replay.mask]]`: byte ranges of a dump section that are the program's machinery (`section`, `lo`, `hi`, `scope`: `always` differ between any two runs, `cross` between DOS and the port, and `why`).
- `[[replay.string_junk]]`: a string the program copies with one byte past its 0 (`section`, `start`, `len`).
- `[replay.segments]`: `first_block_para` and `exe_range` (the EXE paragraphs that move with the load segment) and `slots` (`{section = [offsets]}`, the words that hold a far block's segment). `heap_block` (optional) is the index of a SEGS word that is a far heap block: a slot word that is the same distance above it in both builds is the same, since where DOS's far heap starts moves with the size of the build (UW1's replay build moved it by a paragraph when it grew).
- `[replay.nulls]`: `ds_forms` (the forms DS:0..3 may take, hex with `??` for any byte) and `sum_from`, `sum_to`, `sum_expect` (C0's null-pointer checksum).

## [fuzz]

tools/fuzzasm.py's routine fuzzing: `targets`, a Python file of target definitions (examples/uw2/port/fuzz_targets.py), and `host_glue`, the C file fuzzhost.c includes for the project's call kinds and memory regions.

## [asm2c]

tools/asm2c.py's static recompiler: `spec`, a Python file with the modules, code segments, hand-written ranges and overrides (examples/uw2/port/asm2c_spec.py); `overrides`, a Python file of the project's whose `OVERRIDES` (and `PATCH_OVERRIDDEN`, if it has one) are merged over the spec's, for a spec published apart from the decompilation (UW2: `tools/asm2c.py`, UW2Decomp's own, relative to `[project] root`).

## [sound]

`drivers`: for tools/ailcheck.py, the game's AIL music driver file of each driver kind of runtime/port/sound/aildrv.h (`{ 2 = "SOUND/DM03.ADV", ... }`), relative to `[replay] data`.

`extensions`: the project's own C for the sound library, relative to `[project] root`: a game-specific FM extension for runtime/port/sound/yamaha.c (`struct AilFmExt`, yamaha.h), which the project's `ailgame.h` names as `AIL_FM_EXT` (UW2: `["src/port/sound/tvfx.c"]`, Origin's TVFX). tools/portbuild.py compiles a listed file outside `[port] dir` with the port and says when one is missing; one inside it is compiled already.

## [ci]

For tools/citemplates.py (docs/ci-bundle.md): `vars`, a TOML file of the CI templates' values (examples/uw2/ci.toml), over tools/templates/ci/defaults.toml; any other key here overrides one value. `python3 tools/citemplates.py --list` prints the variables.

## [test]

tools/test.py's tiers: `fast` and `full`, each a list of `[name, command, needs]`; without them the defaults in the tool. `coverage_doc` is where tools/coverage.py writes its page.

## Source directives

Each source names its target and switches in its first lines; the comment markers work in C and, after a `;`, in assembly:

```
/* target: ovr154 */
/* opts: -mm -1 -G -O -Y -d */
```

A data-only assembly source that the link uses but that has no target table is marked `/* fardata */` (UW2's FARDATA.ASM).

## Target tables

`targets/SEG.tsv`:

```
# segment ovr154 base 0x9AD50 size 0x1B37 [org 0x0]
# far NAME PPPP:OOOO          (optional: where a piece of a shared far segment goes)
name	listing_name	0xOFFSET	0xSIZE
```

base is the file offset of the file's first byte, size its length, org how far that is past the segment's paragraph. Offsets are from base.
