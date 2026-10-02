# exhume.toml

Every Exhume tool reads one project file. It is found from `--config PATH`, then the `EXHUME_CONFIG` environment variable, then the first `exhume.toml` in the current directory or a parent. `python3 tools/config.py` prints how it resolved. The full example is `examples/uw2/exhume.toml`.

Paths are relative to `[project] root` (and root to the config file) unless absolute. `~` and `${VAR}` / `${VAR:-default}` are expanded in every string, so one file can serve a checkout in place and a run that writes elsewhere.

## [project]

- `name`: a label.
- `root`: the decompilation repository.
- `src`, `targets`, `symbols`, `matched`, `map`: sources, target tables, the symbol map, the list of matched segments, the map directory.
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

## [names]

- `original_symbols`: a symbol table whose names count as original (the sibling's). symbols.tsv labels such names with `original_label`, runtime helpers `library`, and the rest `provisional`.

## [sibling]

A second build with symbols. `image` (a flat code image), `symbols` (TSV: name, hex address, size), `bits` (16 or 32), `code_end` (symbols above it are data), `extra_anchors` (optional TSV of hand-proven DOS name to sibling name pairs, header line first).

## [map]

- `library_segments`: segments holding startup code and the C library, left out of the alignment.

## [layout]

For `tools/addrscan.py`, which lists addresses written as numbers (docs/link.md, "Layout audits"). All optional.

- `far_data_entries`: `[first, last]` overlay segment table entries that hold far data; their paragraphs are reported as `FDnn`. Without it, every entry with flags 0 other than DGROUP's counts.
- `library_objects`: stems whose objects are not scanned because the link takes that code from a library (UW2: `SEG046`, the overlay manager).
- `dispatch`: code entered through a dispatcher. Each entry is `{ register = "bp", ds = "FD51", es = "FD51", ss = "FD51" }`: a label whose offset is loaded into that register (`mov bp,offset X`) is entered with those segment registers.

DGROUP is found by `[binary] dgroup_para`. The modding build also reads `[binary] listing` and `listing_para_bias`, for the `dw offset` tables in extracted data.

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
