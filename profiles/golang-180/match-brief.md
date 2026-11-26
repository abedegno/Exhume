# Brief: match one Go package under `golang-180` (`golang:1.8.0`)

You write Go source that `golang:1.8.0` (`linux/amd64`, `CGO_ENABLED=0`, GOPATH,
`-gcflags "-trimpath=/build"`, `-ldflags "-s -w"`) turns back into the exact machine code and
`.gopclntab` line table of one package in the target ELF binary.

## Rules

- **Do not spawn sub-agents.** Work alone.
- **No git commands.**
- Edit only your assigned package directory and harness (`pocmain`) if needed. Put scratch files in
  `build/scratch/<unit>/`. Never write to fixed paths in `/tmp`.
- Check upstream sources first before hand-decompiling a package that looks third-party or forked
  (`skills/recover-linked-library`).
- **Budget:** about 20 builds per stubborn function, and the total in your assignment. At the
  budget, stop and report your best version.

## Tools (run from the project root)

```sh
EX=exhume
PY=python3
T=$EX/profiles/golang-180/tools

# Map & inspect target binary
$PY $T/gomap.py packages                             # list packages and function/byte counts
$PY $T/gomap.py funcs '^applib/crypto\.'           # list functions, sizes, file:line ranges
$PY $T/gomap.py skeleton applib/crypto derive.go   # line-numbered skeleton from .gopclntab
$PY $T/goclosures.py --funcs-tsv map/functions.tsv   # recover original local/receiver names from closures
$PY $T/gopin_vendor.py --repo REPO --prefix IMPORT   # pin upstream vendor repo commit by .gopclntab lines

# Compact line-grouped lift & type extraction (use short pkg name for types, e.g. '^comms\.')
./bin/golift lift TARGET_BIN '^pkg\.Func$'
./bin/golift types TARGET_BIN '^pkg\.'

# Disassemble with .rodata string previews
$PY $T/gomatch.py --dis '^applib/crypto\.DeriveKey' --addr

# Build and compare a unit or package (checks cmp code+lines AND raw unmasked bytes)
# Note: direct `gocmp` takes flags BEFORE subcommand (`gocmp -v -first 40 cmp ORIG CAND REGEX`)
$PY $EX/tools/match.py UNIT_OR_REGEX                 # build with golang:1.8.0 and compare
$PY $EX/tools/match.py UNIT_OR_REGEX --no-build      # re-compare last build
$PY $EX/tools/match.py UNIT_OR_REGEX -v              # side-by-side diff for mismatches

# Pin blank lines deterministically for LINES-only functions
$PY $T/golinepin.py UNIT FILE.go --no-build --apply

# Diagnose [RSP+offset] swaps (Go 1.8 sorts locals alphabetically!)
$PY $T/goslotmap.py UNIT FUNC                        # builds with -S and maps stack slots

# Search variants (Python VARIANTS file or JSON choice-point spec with --climb)
$PY $T/gobatch.py UNIT FILE.go FUNC_REGEX SPEC.py
$PY $T/gobatch.py UNIT FILE.go FUNC_REGEX SPEC.json --climb

# Permuters for right-sized functions (both leave the unit's source alone unless told otherwise)
$PY $T/gopermute.py UNIT FILE.go FULL_FUNC_NAME      # structural idioms, one at a time
$PY $T/gorandperm.py UNIT FILE.go FUNC_REGEX --guard 'OTHER_FUNCS_IN_FILE' --minutes 30 \
    [--pkg MAIN_PKG] [--workers N]                   # parallel random parity knobs -> BUILD/gorandperm/*/best.go

# Fast scoring and searching (one package compile per candidate, scored from the object file)
$PY $T/gorandperm.py UNIT FILE.go FUNC_REGEX --fast --sweep --minutes 30 --out DIR
                                                     # every single edit and pair first, then random; give each run its own --out
$PY $T/gonative.py build NAME golang:1.8             # on an arm64 host: the same Go version built for the host
export EXHUME_GO_NATIVE=NAME:/opt/go1.8              # ... and used by --fast, gotrace, goordergoal (checked byte-identical)

# Which value IDs did each load pair get, and who freed them (compiler.md 2f)
$PY $T/gotrace.py trace UNIT FILE.go SSA_NAME FUNC_REGEX --rows 80
$PY $T/gotrace.py trace UNIT FILE.go SSA_NAME FUNC_REGEX --second C[:GOROOT]   # under a second Go version

# A second release built with another Go version (compiler.md 2g, 2h)
$PY $T/goinltree.py LATER_BIN 'FUNC_REGEX'           # Go >= 1.9: which calls were inlined, and where
$PY $T/godual.py score UNIT FILE.go FUNC_REGEX --second C[:GOROOT] --target2 OTHER_BIN
$PY $T/godual.py enum  UNIT FILE.go FUNC_REGEX SPEC.json --second C[:GOROOT] --target2 OTHER_BIN --depth 3

# The words of one call's results in the wrong order (compiler.md 2i)
$PY $T/goordergoal.py UNIT FILE.go SSA_NAME FUNC_REGEX --line L --kinds slice,iface --want 2,1,3,4,0

# Verify whole project / accept a matched unit
$PY $EX/tools/merge.py UNIT
$PY $EX/tools/gate.py check
```

## Workflow

1. **Read the map first.** Run `gomap.py funcs '^PKG\.'` to get every function's filename, line
   range, and byte size. Note every `.go` file in the package — including `pkg.init` (which proves
   the exact import set and package globals).
2. **Generate line skeletons.** Run `gomap.py skeleton PKG FILE.go` and inspect the disassembly with
   `--addr` so every string literal, error message, salt, and constant is read verbatim from
   `.rodata` — never guessed.
3. **Match imports and `pkg.init` first.** Ensure the harness (`main.go`) references every exported
   function (and any unexported function reachable through them) so `cmd/link` retains all symbols
   you need to compare.
4. **Match function by function, pinning line numbers.**
   - Every instruction carries `file:line` in `.gopclntab`. Put each statement on the exact line the
     binary reports; pad unknown header/comment blocks with `//` lines.
   - Start with simple, idiomatic Go 1.8 code. Read `profiles/golang-180/compiler.md` before
     guessing obscure tricks.
5. **Diagnose mismatches systematically:**
   - **Only `LINES` differs:** code bytes already match! Run `golinepin.py` or adjust blank lines /
     comments so statements land on the binary's line numbers.
   - **Same instructions, only `[RSP+0x...]` offsets differ:** Go 1.8 sorts locals alphabetically
     within each type/pointer class. Run `goclosures.py` + `goslotmap.py` and adjust local/receiver
     names (including receivers of leaf-inlined callees!) to match the binary's slot order.
   - **Same size, only `(ptr,len)` / `(itab,data)` / `(cap,len,ptr)` load order swapped:** Go 1.8's
     SSA `f.freeValues` LIFO recycling flipped tie-breaking (`compiler.md` §2b). Try no-op
     conversions (`string(expr)`, `int(expr)`), `switch` vs range `if`, `if/else` vs early return,
     `x += -1` vs `x--`, or named vs unnamed results — test rapidly with `go tool compile -S` on a
     small `/tmp` snippet inside the container.
   - **Extra/missing `CALL` or different branch shape:** check leaf-inlined helpers (Go 1.8 inlines
     small leaf functions like `switch` predicates), `case A, B:` order, `default:` placement,
     `for i := range` vs `for _, x := range`, `bytes.Buffer{}` vs `&bytes.Buffer{}` / `new(...)`,
     and value struct literal + `&ret` vs `&T{...}`.
   - **Stubborn function:** write a small hypothesis-driven variant spec and run `gobatch.py`.
   - **Still stuck, or every edit makes it worse:** look for knobs earlier work left in the function
     (`(x+0)` chains, `unsafe` struct punning, `; _ = x`). They hide each other; rewrite them as
     natural source first (`compiler.md` §2e), then search: `gopermute.py`, then `gorandperm.py`.
     If all of a function's swaps go the same way, one dead value near the function's **end** may
     flip them all (§2b, "which knob reaches which site"). Two inlined constructors whose
     `newobject` results sit in swapped slots are a sort tie, not a naming problem (§2c).
   - **A knob search has stalled:** trace the function (`gotrace.py`, §2f) before running it
     longer. If the original's last pairs are all `(ptr,len)`, its free list ran out and yours is
     too long. If no position of the list gives the wanted order, no shift will, and the list's
     content has to change.
   - **Another release was built with another Go version:** score candidates against both
     (`godual.py`, §2g). A knob moves one compiler only. Ordinary rewrites move both (§2h): a
     struct built in a named local instead of a literal argument, the grouping of an `&&` chain,
     a constant written first, `var x T` then `=`. Try those before knobs, and look at how
     neighbouring functions in the same file are written.
   - **Everything matches and one instruction has the wrong line:** a `+0` knob created the zero
     constant too early (§2i). Pad with a unary minus instead.
6. **Parallel multi-worker builds (when scaling across a large binary):**
   - Partition files strictly disjointly across workers.
   - Each worker must build to its own private `-o /work/build/cand_GROUP.bin` **without `-i`**
     (concurrent `go build -i` races on `pkg/` archives).
   - Workers that try variants of the **same** file need their own copy of the GOPATH
     (`gorandperm.py` does this). Editing one tree in place races even with write-then-rename: on a
     macOS Docker bind mount another build briefly sees the file as missing.
   - Compare baselines with `LC_ALL=C sort` on both sides; `comm` on lists sorted under different
     locales reports hundreds of false regressions.
   - Snapshot `src/` before each wave and check `comm -23 match_baseline.txt match_now.txt` after
     each wave to guarantee zero regressions.
7. **Do not believe a search result until you have seen it in the disassembly.** A scorer that
   reads positions in a list or shares a work directory with another run can report a match that is
   not there (§2i). Rebuild the whole program and read the instructions.
8. **Verify raw bytes.** A function is only complete when **both**:
   - `gocmp cmp` reports `MATCH` (normalized disassembly + `.gopclntab` line table), AND
   - `gocmp raw` reports `0 differ elsewhere` (0 unmasked machine-code byte differences).

## Report (your final message)

- Each function's status (`MATCH` + 0 unmasked bytes, or remaining diff).
- `match.py` summary line (`N/N functions match`, raw byte counts).
- Constants / `.rodata` strings verified with `--addr`.
- Builds used.
- Any newly observed Go 1.8.0 compiler behavior (with the Go source and disassembly evidence).
