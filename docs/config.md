# exhume.toml

Every Exhume tool reads one project file. It is found from `--config PATH`, then the `EXHUME_CONFIG` environment variable, then the first `exhume.toml` in the current directory or a parent. `python3 tools/config.py` prints how it resolved. The full example is `examples/uw2/exhume.toml`.

Paths are relative to `[project] root` (and root to the config file) unless absolute. `~` and `${VAR}` / `${VAR:-default}` are expanded in every string, so one file can serve a checkout in place and a run that writes elsewhere.

## [project]

- `name`: a label.
- `root`: the decompilation repository.
- `src`, `targets`, `symbols`, `matched`, `map`: sources, target tables, the symbol map, the list of matched segments, the map directory. Sources may sit in subdirectories of `src` (one per subsystem, say); every tool finds them through tools/sources.py, by stem (the file name without extension, upper case, unique across the tree) or by DOS segment.
- `exclude`: directories under `src` (relative to it) that hold no sources for the DOS build, such as a host port's own code; never searched for sources (UW2: `port`).
- `include`: the shared headers (default `src/include`), never searched for sources. tools/build.py stages them in C:\ beside each source, and tools/srcdeps.py hashes a source together with the headers it includes, so the gate and the modding build recompile every includer of a changed header. The runtime's `include/` (portable.h; `[port] runtime`) is staged and hashed the same way, after these, so a project header of the same name wins.
- `build`: where objects (`build/STEM/STEM.OBJ`, with `BUILD.LOG`), the link and the queue go. It may be outside root; Exhume's own proof sets it to `~/Exhume/build/uw2` so the Underworld Exhumed checkout is never written.
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

### [gate] for a linked image (armcc5)

A linked-image project's `gate.py check` (and `exact`) runs profiles/armcc5/tools/armgate.py,
which rebuilds the image in pieces and compares it with the original byte for byte, then writes
`<build>/gate/rebuilt.hex` (or `.bin`) in the original file's own layout and compares the files
(profiles/armcc5/linker.md, "The whole-image gate"). The keys above do not apply; these do, all
optional:

- `pieces`: the pieces file (default `pieces.toml` in the root). It holds `known_diffs` (names of
  functions not matched yet, allowed to differ), one `[[piece]]` per piece (`start`, `end` in hex;
  `startup = true` on the piece that holds the vector table, the startup code and the library's
  entry code) and an optional `[data]` with `start` (default the last piece's end) and `end`
  (default the end of the image's compressed or copied RW data). `[[gate.piece]]` and
  `[gate.data]` in exhume.toml may stand in for the file. Each piece starts on a 4-byte boundary
  at a section start and is small enough for one MDK-Lite link (32 KB, counting its files' other
  sections).
- `startup`: the startup source (default: the one assembly source with a `RESET` area).
- `symdefs`: a symdefs file of symbols the original link took from outside the sources (a stack
  top a scatter file defined), added to every link.
- `library`: the scatter module pattern of the toolchain's libraries (default `*.l`); `libpath`:
  their directory (default `[toolchain] home`'s `ARM/ARMCC/lib`); `aside`: library objects to link
  out of the startup piece by name (the gate finds most itself).
- `exclude`: directories under `src` the gate never compiles (a mod's own sources).
- `jobs`: worker processes (default one per core, up to 12). With the Wine runner each worker has
  its own Wine prefix under `EXHUME_GATE_WINEPREFIX` (default `~/.exhume-wine`) and a persistent
  wineserver. `EXHUME_GATE_JOBS` and `--jobs N` win over it; 1 runs everything in one process.
- `allow_gaps`: pass even if some bytes between the first piece and the data's end are rebuilt by
  nothing (default false).
- `[data] split_zi`: link zero-init data in its own region at the image's zero region address,
  rather than after the RW data in one region (default false).

`EXHUME_GATE_EXTRA_SYMDEFS` names a symdefs file added to every link, and `EXHUME_GATE_DATA_END`
the end of the data stage's comparison: hooks for a project's own modded build (armmod.py passes
the same through armgate.run()).

## [c2000] for a TI C2000 image

The ti-c2000 profile's tools (profiles/ti-c2000/tools/) read these, with `[toolchain] home` as
the compiler's directory (its `bin/cl2000.exe` runs under Wine, or a native `bin/cl2000`) and
`[toolchain] c_opts` as the default options (the profile's are `-v28 -ml -mt
--float_support=fpu32 -O1`). `[binary] image` is the image as a binary of 16-bit words.

- `base`: the word address of the image's first word (required). `words`: its length in words
  (default: the file's).
- `byte_order`: how the file stores each word, `big` (the default; hex2000 `-order MS`) or
  `little`. `fill`: the value of erased words (default 0xFFFF).
- `native`: the `bin` directory of a native CGT, 18.12 or later, for ofd2000, dis2000, ar2000,
  asm2000, lnk2000, hex2000 and the cl2000 that assembles the gate's blobs.
- `wine`: the directory holding `wine` and `wineserver` (on a Mac, `/Applications/Wine
  Devel.app/Contents/Resources/wine/bin`). `prefix`: the Wine prefix the compilers are installed
  in. Each tool run clones it into `<build>/wine/jobN` (an APFS clone, held with an flock); `jobs`
  says how many such prefixes (default 8), and `timeout` how many seconds a Wine program may run
  before its server is stopped and it is tried again (default 300).
- `others`: a glob of every installed compiler's `bin/cl2000.exe` (or `cl2000`), for
  `match.py --all-cgt` and for `/* cgt: VERSION */` lines.
- `rtslib`: the runtime library, relative to the compiler's home or a path (default
  `lib/rts2800_fpu32.lib`).
- `park`, `park_data`: where the tools link what has no address, on the program and data pages
  (default 0x200000 and 0x300000, outside an F2806x's or F2833x's memory map).

`[c2000.gate]`, for `gate.py check` (profiles/ti-c2000/tools/c28gate.py):

- `cinit`: the word address of the image's `.cinit` table. `cinit_order`: the files with
  initialised data, by stem, in the original link's order, with library members as
  `rts:member.obj` (profiles/ti-c2000/linker.md, ".cinit"). `cinit_tail`: the words the linker
  writes after the records (default 2).
- `stack`, `stack_size`: where `.stack` goes and its size, as the original link had them.
- `linker`: `native` (the default) or `cgt`, the compiler home's own lnk2000.

A C2000 source may carry `/* cgt: VERSION */` (another installed compiler, a directory name under
`others` such as `16.9.6.LTS`, or a path), `/* opts: ... */` (replaces the default options) and
`/* cflags: ... */` (added to them), in C or, after `;`, in assembly. Its variables sit between
`/* ebss-begin */` and `/* ebss-end */` for the `.ebss` solver.

## [mod]

The modded build of a linked image (`gate.py game`, profiles/armcc5/tools/armmod.py;
skills/modding-build). It copies the project to `<build>/mod/tree`, edits the copy, links the new
code as one more piece, runs the gate in the copy, and checks every difference from the original.

- `sources`: the new code, relative to the root. Keep it out of `src`, or list its directory in
  `[gate] exclude`, so the exact build never compiles it.
- `include`: more include directories for it (`[project] include` is always on the path).
- `code`, `ram`: where the new piece goes: free flash, and RAM above everything the image uses
  (its data and the stack; the build refuses less). Nothing initialises that RAM at reset.
- `[[mod.replace]]`: `file`, `old`, `new` and `count` (default 1): a string replaced in the copy,
  which must occur exactly `count` times. `patches`: patch files applied to the copy with
  `patch -p1`. `edit`: a command run in the copy (`{tree}`, `{root}`, `{build}`, `{python}`).
- `[[mod.allow]]`: the differences the edits may make, each with an optional exact `count`:
  `kind = "word"` (`from`, `to`, optional `at`): a word that held one symbol's address now holds
  another's (a vector, a pointer); `kind = "pool"` (`from`, `to`, optional `size`, default the map
  row's): a literal pool word into one table now points at the same offset in another;
  `kind = "imm"` (`insn` of `movs`, `cmp`, `adds`, `subs`; `old`, `new`): a 16-bit Thumb
  immediate; `kind = "range"` (`start` and `end`, or `symbol` and `size`): anything inside.
  Symbols come from the map, the mod and the gate's own links. With no edits no rule is needed,
  and the image must be identical.
- `out`: the output file (default `<build>/mod/NAME-mod.hex`, or `.bin` for a raw image), written
  in the original's format, with the new piece's symbols beside it (`.syms.tsv`).
- `copy_ignore`: glob patterns of paths not copied.

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
- `[[port.vendor]]`: third-party C a setup script fetched and the port compiles when present (`name`, `dir`, `sources`, `define`, `for`: the port file that gets the define, `missing`; `shared`, the base of its shared library's name in the release build, default the name's letters and digits in lower case); `[[port.pkg]]`: libraries linked when pkg-config finds them (`name`, `define`, `for`, `label`, `missing`). Neither is ever committed (docs/third-party.md).
- `libs`: where tools/setup-libs.sh builds SDL3 and libmt32emu where no package has them (default `tools/libs`); pkg-config searches it first, and on Linux the normal build finds its libraries there when run.
- `icon`: the Windows program's `.ico`, linked in as a resource (default `[package]`'s, `icon_dir/ICON.ico`, when it exists).
- `[port.stub_notes]`: what each generated stub file holds, for its header.
- `[port.layout]`: `file_records` (`{tag = why}`, the structs read from or written to files, which must keep their DOS layout), `io_calls` (the calls that move a record as bytes), `probe` (the DOS compile line of the layout probe).

## [package]

The players' packages (tools/package.py, `make package`; docs/port.md, "Packages for players") and the icon (tools/icons.py). tools/portcfg.py has every key with its default:

- `name`: the packages' and the macOS app's name (default `[project] name`): `NAME.app`, `NAME-VERSION-macos.zip`, `NAME-VERSION-linux-ARCH.tar.gz` and `.AppImage`, `NAME-VERSION-windows-x86_64.zip`.
- `title`, `comment`: the game's full name (the app's display name, the Linux menu entry's name) and the menu entry's comment.
- `bundle_id`, `category`, `min_macos`, `copyright`: the app's `Info.plist`.
- `launcher`: the Linux package's start script, `.desktop` and `.png` (default `name` in lower case).
- `readme`: the players' README (default `tools/dist/README-dist.txt`), its `@VERSION@` and `@MACOS_OPEN@` filled in; `texts`: the project files copied in as `.txt` (default `LICENSE`, `NOTICE`, `THIRD-PARTY-NOTICES`).
- `icon_dir`, `icon`: the icon's two SVG sources, `ICON.svg` and `ICON-small.svg`, and the files tools/icons.py writes beside them (default `tools/dist/icon`, `launcher`); `icon_header`: the window icon as C, for `PLAT_ICON` (default `[port] dir/platform/BACKEND/icon.h`).
- `env`: the prefix of the variables tools/package.py reads (default `name` in upper case): `ENV_CODESIGN_IDENTITY`, a Developer ID identity to sign the macOS app with, and `ENV_NOTARISED=1`.

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
- `order`, `stage_from`: the sessions' order, and the sessions that replay a saved game another one writes. The other may be a setup session, `setup/NAME` (`<sessions>/setup/NAME/session.rec`, with `session.cfg` and its `session.script`), whose script uses a test's cheats (recording format 6, runtime/README.md, "Cheats, for tests"): DOS cannot replay it, so tools/golden.py makes its saved game in the port, for `golden` and `verify` alike, and the golden keeps that saved game's digests.
- `dos_backend`: sessions whose DOS replays must run in a particular DOS, `jsdos` or `dosbox-x` (`{ session = "jsdos" }`), for `dos`, `check` and `golden`; the others use DOSBox-X when it is installed (UW1's `sound` replays in js-dos, since in DOSBox-X its replay hangs when the introduction's speech starts).
- `c0_signature`, `c0_signature_at`: the C runtime's copyright string and its DS offset, by which the null-pointer check finds DGROUP in js-dos after the program exits (default the profile's).
- `[replay.steps]`, `[replay.derive]`, `[replay.cfgs]`: each session's steps for tools/replaydos.mjs (`@NAME` takes another list's steps; a name starting with `_` is not a session), sessions made from others (`from`, `wait_scale`), and the configuration files recorded with sessions.
- `[[replay.mask]]`: byte ranges of a dump section that are the program's machinery (`section`, `lo`, `hi`, `scope`: `always` differ between any two runs, `cross` between DOS and the port, and `why`).
- `[[replay.string_junk]]`: a string the program copies with one byte past its 0 (`section`, `start`, `len`).
- `[replay.segments]`: `first_block_para` and `exe_range` (the EXE paragraphs that move with the load segment) and `slots` (`{section = [offsets]}`, the words that hold a far block's segment). `heap_block` (optional) is the index of a SEGS word that is a far heap block: a slot word that is the same distance above it in both builds is the same, since where DOS's far heap starts moves with the size of the build (UW1's replay build moved it by a paragraph when it grew). With it, `heap_from` (the EXE paragraph, counted from the load segment, below which no far heap block lies in any build) makes tools/golden.py's digests take a slot word from that paragraph up as a heap block, by its distance from that SEGS block, so the goldens survive a replay build that moves the heap.
- `[replay.nulls]`: `ds_forms` (the forms DS:0..3 may take, hex with `??` for any byte) and `sum_from`, `sum_to`, `sum_expect` (C0's null-pointer checksum).

## [fuzz]

tools/fuzzasm.py's routine fuzzing: `targets`, a Python file of target definitions (examples/uw2/port/fuzz_targets.py), and `host_glue`, the C file fuzzhost.c includes for the project's call kinds and memory regions.

## [vectors]

tools/vectors.py's test vectors: `targets`, a Python file of `V()` definitions (each routine, its input and output columns, its input generator and how a case becomes a call); `host_glue`, a C file compiled as port C that defines `fuzz_vector` for the port's side; `out`, the directory the CSV files are written to (default `vectors`). `--targets`, `--glue` and `--out` override them.

## [asm2c]

tools/asm2c.py's static recompiler: `spec`, a Python file with the modules, code segments, hand-written ranges and overrides (examples/uw2/port/asm2c_spec.py); `overrides`, a Python file of the project's whose `OVERRIDES` (and `PATCH_OVERRIDDEN`, if it has one) are merged over the spec's, for a spec published apart from the decompilation (UW2: `tools/asm2c.py`, UW2Decomp's own, relative to `[project] root`).

## [sound]

`drivers`: for tools/ailcheck.py, the game's AIL music driver file of each driver kind of runtime/port/sound/aildrv.h (`{ 2 = "SOUND/DM03.ADV", ... }`), relative to `[replay] data`.

`extensions`: the project's own C for the sound library, relative to `[project] root`: a game-specific FM extension for runtime/port/sound/yamaha.c (`struct AilFmExt`, yamaha.h), which the project's `ailgame.h` names as `AIL_FM_EXT` (UW2: `["src/port/sound/tvfx.c"]`, Origin's TVFX). tools/portbuild.py compiles a listed file outside `[port] dir` with the port and says when one is missing; one inside it is compiled already.

## [ci]

For tools/citemplates.py (docs/ci-bundle.md): `vars`, a TOML file of the CI templates' values (examples/uw2/ci.toml), over tools/templates/ci/defaults.toml and the names release.yml takes from `[package]` and `[port]`; any other key here overrides one value. `python3 tools/citemplates.py --list` prints the variables.

## [test]

tools/test.py's tiers: `fast` and `full`, each a list of `[name, command, needs]`; without them the defaults in the tool. `coverage_doc` is where tools/coverage.py writes its page.

## [difftest]

For the armcc5 profile's differential tests, profiles/armcc5/tools/emu/difftest.py
(docs/firmware-testing.md). Every key is optional and its command-line option wins.

- `reference`: the image the others are measured against (default `[binary] image`). An image
  is an Intel HEX, raw or ELF file.
- `reference_syms`: its symbols: a map directory, an ELF or a TSV (default `[project] map`).
- `candidate`, `candidate_syms`: the build under test and its symbols (default: the reference's
  symbols, right only for a build at the same addresses, such as the exact link).
- `src`: directories whose C definitions give argument and return widths, and the default list of
  functions (default `[project] src`).
- `runs` (default 200), `budget` (instructions per run, default 300000).
- `exclude`: regular expressions of function names left out of the default list.
- `init_rw`: true starts initialised data whose initial bytes differ between the builds from each
  build's own values (`--init-rw`).
- `flash`, `ram`, `scratch`: `[base, size]` of the regions (defaults `[0x08000000, 0x80000]`,
  `[0x20000000, 0x10000]`, `[0x30000000, 0x10000]`; pointer arguments point into scratch).
  `periph`: a list of `[base, size]` whose every read is random and every write recorded
  (default the first megabyte at 0x40000000 and at 0x50000000).

## [sysemu]

For the system emulator, profiles/armcc5/tools/emu/sysemu.py (docs/firmware-testing.md).

- `image`, `symbols`: what it boots and the names stubs and tests use (default `[binary] image`
  and `[project] map`).
- `flash`, `ram`: `[base, size]` (defaults `[0x08000000, 0x80000]`, `[0x20000000, 0x10000]`).
- `instr_per_ms` (default 30000): instructions in an emulated millisecond. `cpu_hz` (default
  72000000): the clock SysTick and the timers count. `irq_repeat` (default 32): deliveries of
  level-triggered interrupts in one millisecond.
- `on_reset`: `reboot` (default) or `stop`, on a reset request or the watchdog.
- `chip`: the models added (`stm32f1`, the default; anything else adds none). `uart_baud`
  (`{ 1 = 115200 }`, per USART), `flash_page` (default 0x400), `flash_kb` (default the `flash`
  size), `uid` (the unique ID, 24 hex digits).
- `plugins`: Python files, each with `attach(machine, options)`, that add the project's own models
  and stubs; `options` is this table, so a plugin reads its own subtable (`[sysemu.NAME]`;
  profiles/armcc5/tools/emu/example_device.py).
- `ram_fill`: a seed: RAM holds random bytes at reset, as at power-on.

## [openbuild]

For the open-toolchain build, profiles/armcc5/tools/openbuild/openbuild.py
(docs/firmware-testing.md).

- `out`: where the copy of the sources, the objects, `app.elf` and `app.hex` go (default
  `build/open`).
- `src`, `sources`, `exclude`: the source directory (default `[project] src`), globs of the C
  files to build relative to the root (default every `.c` under it) and globs left out.
- `include`: header directories besides `[project] include`. Each source's `/* opts: */` line
  gives its `-D` and `-I` as for armcc; `docker_root` (default `/work`) is the project's mount in
  the toolchain container, mapped back to the copy.
- `extra`: C or GNU assembler files added as they are: a startup file and vector table, the
  project's stand-ins for the library calls it makes (`sprintf`, float helpers).
- `cc` (default `clang`), `target` (`thumbv7m-none-eabi`), `cpu` (`cortex-m3`), `opt` (`-Os`),
  `cflags`, `defines`, `libs` (more objects or archives for the link).
- `short_enums` (default true), `default_rewrites` (default true: `__packed struct` and
  `__packed union`), `rewrite`: more `[pattern, replacement]` pairs (Python regular expressions)
  applied to the copy of every source and header, such as one for an embedded `__asm { }` block.
- `flash`, `ram`: `[origin, length]` for the linker script.
- `ram_layout` (default true) and `ram_syms` (default `[project] map`; an ELF or TSV also
  serves): every RAM symbol at the original's address. Without the map, RAM is in the linker's
  own order and the build says so.
- `entry` (default `main`), `runtime` (default true: link the profile's runtime.c and
  runtime.S), `ld`, `objcopy`, `objdump` (defaults the `arm-none-eabi-` tools).

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
