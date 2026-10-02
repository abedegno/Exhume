# exhume.toml

Every Exhume tool reads one project file. It is found from `--config PATH`, then the `EXHUME_CONFIG` environment variable, then the first `exhume.toml` in the current directory or a parent. `python3 tools/config.py` prints how it resolved. The full example is `examples/uw2/exhume.toml`.

Paths are relative to `[project] root` (and root to the config file) unless absolute. `~` and `${VAR}` / `${VAR:-default}` are expanded in every string, so one file can serve a checkout in place and a run that writes elsewhere.

## [project]

- `name`: a label.
- `root`: the decompilation repository.
- `src`, `targets`, `symbols`, `matched`, `map`: sources, target tables, the symbol map, the list of matched segments, the map directory. Sources may sit in subdirectories of `src` (one per subsystem, say); every tool finds them through tools/sources.py, by stem (the file name without extension, upper case, unique across the tree) or by DOS segment.
- `exclude`: directories under `src` (relative to it) that hold no sources for the DOS build, such as a host port's own code; never searched for sources (UW2: `port`).
- `include`: the shared headers (default `src/include`), never searched for sources. tools/build.py stages them in C:\ beside each source, and tools/srcdeps.py hashes a source together with the headers it includes, so the gate and the modding build recompile every includer of a changed header.
- `build`: where objects (`build/STEM/STEM.OBJ`, with `BUILD.LOG`), the link and the queue go. It may be outside root; Exhume's own proof sets it to `~/Exhume/build/uw2` so the UW2Decomp checkout is never written.
- `queue`: the build queue directory, relative to build.

## [binary]

- `exe`: the original executable, the user's own copy.
- `dgroup_para`: DGROUP's load paragraph (DS:0 is at header + paragraph * 16). `tools/fingerprint.py` suggests it; confirm it with a string the code references.
- `overlay_segment`: a regular expression matching overlay target names, whose first group is the overlay's index in the overlay segment table (UW2: `ovr(\d{3})`).
- `listing`, `listing_para_bias`, `listing_stub_prefix`: the IDA listing, what IDA adds to load paragraphs in segment names, and the prefix of its overlay stub segments.

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
- `known_diffs`: `[offset, original byte, linked byte]` for each byte the exact link is known to get wrong (UW2: two). Any other difference fails the gate, and so does one of these not differing.
- `sessions`, `batch`: DOS sessions at once for the gate alone (default: `[toolchain] sessions`, else what suits the DOS in use; `EXHUME_DOS_SESSIONS` wins) and sources per session (default 8).
- `boot`: rungame.mjs steps for `gate.py boot`, which also uses [run].

## [repocheck]

Optional, for tools/repocheck.py when the config sits at the repository root: `ban`, more file extensions that must never be committed (a game's data formats), and `allow`, regular expressions for paths exempt from the binary checks.

## [run]

Notes for rungame.mjs (`data`, `exe_name`, `skip`), which takes them as arguments.

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
