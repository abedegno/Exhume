# Go 1.8.0 (`gc`, `linux/amd64`) — Compiler Behavior

Everything below was measured against `golang:1.8.0` (`linux/amd64`, `CGO_ENABLED=0`, GOPATH mode,
`-gcflags "-trimpath=/build"`, `-ldflags "-s -w"`) while matching a stripped, statically linked Go 1.8.0 server binary
(`store/fs`, `store/errors` — 32/32 functions; `applib/crypto` — 17/17 functions, 10,432 bytes,
0 unmasked byte diffs).

## 1. Build flags and `.gopclntab`

- **`-ldflags "-s -w"`** strips the ELF symbol table (`.symtab`) and DWARF debug info, but **never
  strips `.gopclntab`**. The Go runtime requires `.gopclntab` for stack unwinding, GC stack maps,
  and `runtime.Caller`.
- `.gopclntab` preserves, for every function in the binary:
  - exact package-qualified function/method name (`pkg.Func`, `pkg.(*Type).Method`, `pkg.Func.func1`),
  - exact `[Entry, End)` code bounds,
  - source file path (trimmed by `-trimpath`), and
  - **exact source line number for every machine instruction**.
- **`-gcflags "-trimpath=/build"`** strips the build root prefix from `.gopclntab` file paths while
  retaining package-relative filenames.
- Because line numbers are recorded per instruction, matching both machine code **and** the
  `.gopclntab` line table (`gocmp cmp`) is a strong structural constraint:
  - Blank lines, comments, and `const` blocks above a function pin its exact start line.
  - Line jumps inside a function reveal blank lines, multi-line statements, `if`/`else` blocks, and
    helper functions living above the caller in the same file.

## 2. Stack slot ordering depends on local variable names

Go 1.8's `gc` stack frame allocator (`cmd/compile/internal/gc/pgen.go`, `cmpstackvarlt`) sorts
non-argument local variables of the same pointer-ness and alignment **alphabetically by identifier
name** before assigning `[RSP+offset]` slots.

- Renaming a local variable (e.g., `ct` vs `ret`, `mode` vs `encrypter`, `raw` vs `decoded`)
  changes its sort key relative to sibling locals of the same type/class and permutes their stack
  offsets — even when every instruction opcode and register is identical.
- Use `goslotmap.py` (`-gcflags "-trimpath=/build -S"`) to map every named local and `autotmp_N`
  in your build to the `[RSP+0x...]` offset in the original binary.
- When the instructions agree and only `[RSP+0x...]` displacements are swapped, rename the local
  so its lexicographical position sits between the neighbours whose offsets bound it.
- Verified on `applib/crypto.encryptV1` and `decryptV1`: pinning the
  lexicographical ordering of `block`, `encrypter`/`decrypter`, `iv`, `key`, `padded`, `payload`,
  `plain`, `ret`/`ct`, `salt`, `unpadded` produced 0 byte diffs across 2,544 bytes.
- **Anonymous `autotmp_N` vs named locals:** Converting a multi-line struct literal into a named
  temp (`ev := make(...); &T{ev: ev}`) replaces an `autotmp_N` (whose name `""..autotmp_N` sorts
  before all user identifiers) with `"ev"` (which sorts after `"client"`). Measured on
  `app/events.(*Hub).register`.
- **Package-global `autotmp_N` decimal-digit boundary (`9` → `10`):** Go 1.8's `statuniqgen`
  counter is package-global, and `cmpstackvarlt` sorts `autotmp_N` names **lexicographically as
  strings** (`"autotmp_10" < "autotmp_8"`). If a function's temporaries cross from 1-digit to
  2-digit (`autotmp_8, 9, 10, 11`), their stack slots flip compared to a build where all four have
  the same digit count (e.g. `autotmp_10..13`). Measured on `app/events.(*Hub).Publish`:
  an inlined/unreferenced `NewHub()` above `Publish` (lines 53–57) bumps `statuniqgen`
  into 2 digits and resolved all 10 remaining stack-offset byte diffs to 0.
- **Recovering original variable names from closure types (`goclosures.py`):** Go 1.8 emits
  `type..hash.struct { F uintptr; pkg.var1 T1; ... }` symbols in `.gopclntab` for comparable
  closure environments. Inspecting these reveals the author's exact receiver, parameter, and
  captured local names (e.g. C++-style `this` receivers, `retn` return vars, `merr` multierrors),
  eliminating guesswork when fixing `cmpstackvarlt` stack-slot swaps.
- **Inlined receiver & parameter names affect caller stack slots:** When a leaf method
  `func (this *T) Add(r *U)` is inlined into a caller, its receiver and parameter names enter the
  caller's `cmpstackvarlt` alphabetical sort (`m < rs < r` vs `r < rs < this` permutes stack slots
  in the caller even when the caller itself is untouched).

## 2b. SSA `f.freeValues` LIFO recycling (`(ptr,len)` / `(itab,data)` load-order swaps)

A very common Go 1.8.0 diff has identical instructions, stack offsets, and size, except two
independent loads of a decomposed multi-word value (`string` ptr/len, `interface`/`error`
itab/data, or `slice` ptr/len/cap) are swapped (e.g. `MOVQ 8(AX), CX; MOVQ (AX), DX` vs
`MOVQ (AX), CX; MOVQ 8(AX), DX`).

- **Root cause:** In Go 1.8.0's SSA backend (`cmd/compile/internal/ssa/schedule.go` and
  `ssa/func.go:85`), the scheduler breaks ties between equal-score, same-line instructions solely
  by SSA value ID (`x.ID > y.ID`), and freed SSA value IDs are recycled from a **LIFO stack**
  (`f.freeValues`). When `decompose builtin` splits `Load <string>` / `Load <error>` / `makeslice`
  into 2–3 loads:
  - If `f.freeValues` holds **≥ 2 recycled IDs** (pushed in ascending order `[..., v34, v35]`),
    popping LIFO assigns the *higher* ID to `ptr`/`itab` and the *lower* ID to `len`/`data` →
    `len`/`data` schedules **first**.
  - If `f.freeValues` holds **0–1 IDs**, `ptr`/`itab` gets the *lower* ID and `len`/`data` gets a
    fresh *higher* ID → `ptr`/`itab` schedules **first**.
- **Proven zero-instruction controls** (change dead/transient SSA value count before
  `decompose builtin` without changing emitted code size):
  1. **No-op type conversion (`string(expr)`, `int(expr)`):** Consumes 1 SSA value ID during early
     passes and compiles to 0 extra machine instructions. Verified on `app/web.ListenAndServe`
     (3,184 B) and `ListenAndServeAll.func1` (400 B).
  2. **`switch x { case A, B, C: ... default: ... }` vs `if x >= A && x <= C`:** `switch` creates
     an extra transient SSA value that gets freed into `f.freeValues`, shifting parity while
     compiling to the identical range comparison (`sub/cmp/ja`). Verified on
     `applib.(*Profile).SetPassword` (384 B).
  3. **`if ... { return ... } else { return ... }` vs early return without `else`** (verified on
     `app/config.(*Theme).Color`, 192 B), **`x += -1` vs `x--`** (verified on
     `awaitWorker`, 1,840 B), **1-case `select { case v := <-ch: }`** vs bare `<-ch`,
     **named vs unnamed return parameters**, and **temporary variable vs inline expression**.
- **Fast iteration:** Test candidate Go snippets in a tiny `/tmp/test.go` inside the Docker
  container with `go tool compile -S` (or `GOSSAFUNC='(*T).Method' go tool compile ...` to inspect
  `ssa.html`) instead of relinking the full binary. `GOSSAFUNC=name go build ...` on a whole
  package writes `ssa.html` into the **package source directory**: move it out at once.

### Where the recycled IDs come from, and which knob reaches which site

Read from `ssa/func.go` (`newValue`, `freeValue`), `ssa/deadcode.go` and `ssa/rewrite.go`, and
confirmed on two functions of one program, `main.cachedSize` and `main.(*App).Run`,
by dumping `ssa.html`:

- `f.freeValues` is one linked list per function, pushed by `deadcode` (dead values freed in
  `f.Blocks` order, then value order within a block) and by the end of every rewrite pass
  (values left `OpInvalid`). During a rewrite pass nothing is freed, so the `dec` pass only pops.
- `dec` walks `f.Blocks` in order and allocates as it rewrites: `(Load <error>)` becomes
  `IMake (Load itab) (Load data)` with the **itab load allocated first**, then the data load,
  then the `OffPtr`; string and slice loads likewise allocate `ptr` first. A decomposed
  `Store <string>` / `Store <error>` allocates an `OffPtr` and a second `Store`.
- After `schedule`, ties on one line are broken by **ascending value ID**. So a pair comes out
  `(data, itab)` or `(len, ptr)` exactly when the two IDs it popped were in descending order.
  IDs are reused across passes, so the stack is not sorted: in `cachedSize` one return path's
  itab load popped `v105` while its data load popped `v4`.
- The top of the stack is whatever was freed **last**, which is the **last block** of the
  function, and `dec` hands it to the **first** block. So:
  - one extra dead value near the **end of the function** shifts every pair in the function by
    one position, and every swap tends to flip at once. A function whose swaps all go the same
    way (all `(len,ptr)` where the original is `(ptr,len)`) wants exactly such a shift.
    `(*App).Run` went from 10 swap sites to 2 (and one line-table entry) with two `+0` on
    `*delayMS` at its last statement;
  - a dead value early in the function lands deep in the stack and reaches only the late pops;
  - two knobs at different depths flip the sites between them. A shift by two restores the
    original pairing.
- Only **non-constant** expressions count. `10 * time.Minute + 0`, `int(5)` and other constant
  expressions are folded by the type checker before SSA exists and change nothing.
- The int64 result of a call keeps its own (old) ID while the error beside it is split into two
  fresh ones, so a three-value return like `(n int64, err error)` can come out in any of the six
  orders. `cachedSize`'s three `return measureSize(name)` paths need three different ones.

## 2c. Equal sort keys: inlined results and the unstable sort

`cmpstackvarlt` (§2) ends with `a.Sym.Name < b.Sym.Name`, but the frame sort is
`sort.Sort(byStackVar(Curfn.Func.Dcl))` (`gc/pgen.go:260`), which is **not stable**. Every
inlined call's unnamed result is a fresh local called `~r0` (`gc/inl.go`, `retvar`: `lookupN("~r", i)`),
so two inlined constructors in one function (`x = pkg.NewA()` ... `y = pkg.NewB()`, each
`return &T{...}`) have **identical** sort keys, and which one gets the higher slot depends on
how many other locals the function declares and where they sit in `Dcl`.

- Symptom: two `runtime.newobject` results stored to `[RSP+0x50]` / `[RSP+0x60]` the wrong way
  round, nowhere near the edit that caused it.
- Measured on `(*App).Run` (6,832 B): removing the unrelated local `dbPath := dbFile` ninety lines
  further down swapped the slots of `syncer = sync.NewSyncer()` and `hub = events.NewHub()`;
  naming the first one, `s := sync.NewSyncer(); syncer = s`, swapped them back.
- Remedy: add or remove one named local elsewhere in the function, or give one of the two results
  a name (as above) so the tie is gone.

## 2d. Line attribution that a rewrite can move

- **Tagged vs tagless `switch`:** in `switch x { case A: }` the load of `x` is attributed to the
  `switch` line; in `switch { case x == A: }` it stays on the `case` line, like an `if`. Replacing
  `if t == A { ... } else if t == B { ... } else { ... }` with a tagless switch kept the original's
  lines and changed the SSA value count (an HTTP router method, 15,632 B: the last
  `(ptr,len)` swap went away and the function matched).
- **A constant passed to a multi-line call** is attributed to the call's closing `)` line, like
  the call. Copying it into a local first (`p := constName` on its own line) moves its `LEA` to
  that line. A `LEA` of a constant string on the line of the call's last argument in the original
  means it was passed directly.
- **Inlined helpers take the call site's line.** An inlined same-package helper
  (`isSyncable` in `sync.go`) shows up as `CMP`s on the line of its call,
  never on its own lines. The line table cannot prove that the original had no such helper.

## 2e. Earlier knobs hide each other: natural source first

`(x+0)` chains, `unsafe` struct punning and similar parity knobs (§2b) compensate for each other.
Once a function carries several, removing any one makes it worse, so no single edit (by hand or
by a permuter) can get out. Before searching knobs, rewrite every knob in the function as the
code a programmer would have written, and only then search.

- `main.findFile` (864 B) built a `*disk.Store` through
  `(*disk.Store)(unsafe.Pointer(&struct{ dir string }{dir: path}))` because the field was
  unexported, plus an 18-deep `((len(name)+0)...)*0 == 0 &&` to cancel the effect. Exporting the
  field (`Dir string`; field names of a struct do not reach any function's code) and writing
  `&disk.Store{Dir: path}` with a plain `if err != nil` took it from 24 to 8 raw diff bytes.
- In the same file, `measureSize` got worse (9 → 221) when its `+0`s were removed: those knobs
  are still standing in for something the natural source does differently. Keep such knobs, note
  them, and come back.
- Order of attack for a right-sized function that still differs: (1) undo knobs, write natural
  source; (2) structural idioms one at a time (`gopermute.py`, §5–7); (3) the parity mechanics
  above, by hand, for one or two sites; (4) `gorandperm.py`, the random parallel knob search,
  with `--guard` on the file's other functions; (5) when the search stalls on a plateau, dump
  `ssa.html` for the function and read which IDs each site popped.

## 3. Dependency stubs: escape analysis, inlining, and `init()`

When matching a package with lightweight dependency stubs instead of full vendored/sibling packages
(as in a pilot unit for one package with its dependencies stubbed), three compiler properties of every stubbed dependency affect
code generation in the caller:

1. **Inlining (`//go:noinline`):** A trivial 1-line stub (`return nil`) gets leaf-inlined into the
   caller, shrinking the caller and dropping the `CALL`. Mark non-inlined dependency methods with
   `//go:noinline` and a non-trivial body.
2. **Escape analysis:** Go 1.8 performs inter-package escape analysis via export data. If a real
   dependency lets an argument escape (e.g. `websocket.Codec.Send(ws, v)` calling `cd.Marshal(v)`),
   a stub that ignores `v` lets Go keep `string(serialized)` on a 32-byte stack buffer instead of
   calling `runtime.slicebytetostring(nil, ...)` + `runtime.convT2E`. Make stubs escape arguments
   the same way the real dependency does.
3. **Package `init()`:** `cmd/compile` only emits `CALL <dep.init>` inside `<pkg>.init` if the
   imported package actually has a non-empty `init()`. Add a non-empty `func init()` to stubs of
   dependencies that have `init()` in the target binary.

## 4. Leaf-only inlining (no mid-stack inlining)

Go 1.8 only inlines leaf functions within the compiler's node-cost budget (mid-stack inlining was
not added until Go 1.12).

- Small pure helpers (such as a `switch` over a few integer constants returning `bool`) are inlined
  into callers **and still leave their own line numbers or affect the caller's line table**.
- Evidence (`applib/crypto/hash.go`, `v2.py`, `v4.py`, `v6.py`):
  - `HashCheck_Password`, `HashCheck_Lkey`, and `HashSet_Lkey` all check whether `format` is an
    LKEY format (`100`, `101`, `102`).
  - Writing `format >= 100 && format <= 102` inline emitted a range subtraction/compare sequence
    that did not match the binary.
  - Extracting `isLkeyFormat(format int) bool` with:
    ```go
    switch format {
    case PASSWORD_FORMAT_LKEY_PLAINTEXT, PASSWORD_FORMAT_LKEY_BCRYPT, PASSWORD_FORMAT_LKEY_SHA512_BCRYPT:
        return true
    default:
        return false
    }
    ```
    inlined cleanly into all three callers and matched all three functions byte-for-byte (with
    `isLkeyFormat` itself elided from the binary because it was never taken by address and every
    call was inlined).

## 5. `switch` statements and case ordering

- Multi-value cases (`case A, B:`) test values in **source order**. Swapping `case A, B:` to
  `case B, A:` flips the `CMP` immediate order (`v7.py`).
- Placing the fallback `return` inside `default:` versus after the closing `}` of the `switch` can
  change branch layout, `f.freeValues` load order, and line-table attribution (`v9.py`, `v12.py`,
  `app/remote.NewRemoteConn`).
- A `switch` on a `uint64` with dense/sparse integer constants (`0`, `2000`, `2001`, `2002` in
  `encryption.go`) generates a binary-search / compare tree whose exact jump structure depends on
  whether `default:` contains an extra `if format > 2002` check.

## 6. Return values, loops, and error returns

- Named return parameters (`func f(...) (res string, err error)`) initialize return slots
  differently and alter stack-slot names compared to unnamed returns (`v1.py`, `v3.py`, `v5.py`,
  `v8.py`). Start with unnamed returns unless disassembly shows explicit zeroing of named result
  slots before the first branch or bare `return` patterns.
- Tail calls do not exist in Go 1.8 `gc`, but `return callee(...)` directly copies results into
  the caller's return slots without an intermediate local (`v5.py`: `s, err := hashSet(...); return s, err`
  generates extra stack moves compared to `return hashSet(...)`).
- **`for i := range slice` vs `for _, elem := range slice`:** On slices of large structs,
  `for _, elem := range slice` emits a per-iteration `duffcopy` into `elem`, whereas
  `for i := range slice` indexing `slice[i]` avoids the copy completely.
- **Preventing compile-time string folding:** `"prefix/" + pkg.Version()` (where
  `func Version() string { return "1.2.3" }` is leaf-inlined) prevents Go 1.8 constant-folding and
  emits `runtime.concatstring2("prefix/", "1.2.3")` at runtime.
- **Cross-file constant-node line attribution:** Go 1.8 shares some lowered constant nodes across
  a package (notably `time.internalToUnix = -0xe7791f700` from inlined `time.Time.Unix()`),
  attributing the `MOV` immediate in `.gopclntab` to the first file/line in the package that
  materialized it.
- **`string(buf.Bytes())` vs `buf.String()`:** `(*bytes.Buffer).String()` begins with
  `if b == nil { return "<nil>" }`; calling `string(buf.Bytes())` skips the nil-receiver check.

## 7. Composite literals, escape analysis, and allocations

- `buf := bytes.Buffer{}` followed by `&buf` allocates/zeroes a stack or escaped `bytes.Buffer`
  differently from `buf := &bytes.Buffer{}`, `new(bytes.Buffer)`, `bytes.NewBuffer(nil)`, or
  `bytes.NewBufferString("")` (`v10.py`, `v11.py`).
  - Evidence (`applib/crypto/encv2.go`): `buf := bytes.Buffer{}` + `gzip.NewWriterLevel(&buf, ...)`
    matched byte-for-byte, while `&bytes.Buffer{}`, `new(bytes.Buffer)`, and `bytes.NewBuffer(nil)`
    all differed in `runtime.newobject` / zeroing code.
  - **Value struct literal + `&ret` (`ret := T{...}; return &ret`):** Distinct from `ret := &T{...}`!
    `ret := T{...}` builds the struct in a stack temp, emits `runtime.newobject` at the closing `}`
    line before evaluating dynamic field expressions, then copies via `duffcopy` / `typedmemmove`.
    Verified across `app/feed.NewFeedDB` (576 B), `app/web.ListenAndServe` (3,184 B),
    `app/sync.NewSyncer` (1,408 B), `ListContents`, and `api_SetProfile`.
  - **Field assignment vs composite literal on padded structs:** On a struct with internal padding
    (`QueuedObject{Flags int8; Value Object}`), a composite literal zeroes the
    padding/struct before assigning fields, whereas `var ex T; ex.Flags = ...; ex.Value = ...`
    writes fields directly without zeroing padding (verified on `app/sync.(*Syncer).Enqueue`,
    shrinking 560 B → exact 528 B).
  - **Line attribution inside composite literals:** Assigning `Status: int(http.StatusOK)` or
    using `const` vs literal values or post-literal field assignments (`ret.Status = ...`) changes
    whether a field store is attributed to the literal's opening `{` line or the field's own line.
- `string(salt) + string(iv) + string(ret)` compiles directly to `runtime.concatstring3`, which is
  unmistakable in disassembly (`encv1.go`).
- `[]byte(str)` calls `runtime.stringtoslicebyte` (with a 32-byte stack buffer optimization when
  non-escaping); `string(b)` calls `runtime.slicebytetostring`.

## 8. Package `init()` and `<autogenerated>` wrapper line numbers

- **`pkg.init` exact structure:**
  1. Calls imported package `init()`s in **file-alphabetical, source-order-of-first-import** across
     all `.go` files in the package.
  2. Evaluates package-level `var` initializers in declaration/dependency order (each with its own
     `file:line`). Note that reading a var from an imported package (`var X = other.Default.N`)
     emits runtime `MOV` instructions in `init`, whereas same-package constants fold statically.
  3. Calls explicit `func init()` functions (`pkg.init.1`, `pkg.init.2`, ...) in file-alphabetical
     then declaration order.
  4. `pkg.init`'s prologue and epilogue carry **`<alphabetically_last_file.go>:(total_lines + 1)`**
     — so trailing newlines in the alphabetically last `.go` file control `pkg.init`'s line number.
- **`<autogenerated>` sequential line counter:**
  - Go 1.8 numbers auto-generated pointer/value method wrappers sequentially (`1, 2, 3, ...`) per
    package during compilation — **including wrappers for methods that `cmd/link` later
    dead-code-eliminates!**
  - A **value-receiver** method on `T` emits a pointer wrapper `(*T).M` (+1 in `T`'s package).
  - A **pointer-receiver** method on `*E` emits **0** wrappers in `E`'s package, but emits **+1
    promoted wrapper** on every struct in any package that embeds `E` by value!
  - Dead-code-eliminated dummy unexported methods (placed in a strategically named file, e.g.
    `Type_z.go` alphabetically after `Type_easyjson.go`) can align `<autogenerated>` line numbers
    with 0 extra bytes in the linked binary.

## 9. Upstream, vendored, and code-generated packages (zero-LLM recovery)

Before hand-decompiling any package, check for bulk zero-LLM wins in this order:

1. **Exact upstream / vendored commits (`gopin_vendor.py`):** Recover the exact git commit of every
   `vendor/` and forked upstream package by scoring historical commits against `.gopclntab`
   function declaration lines (`map/functions.tsv`). Pinning real `vendor/` packages into
   `src/vendor/` guarantees identical inter-package escape analysis, inlining, type descriptors,
   and `pkg.init` chains across the entire binary.
2. **`easyjson` generated files (`easyjson*Decode*`/`Encode*`):** Reconstruct the Go struct
   definitions from `.typelink` (`golift types`), note that `easyjson` emits methods in **reverse
   (LIFO)** declaration order per file, compile the exact pinned `easyjson` CLI inside the Go 1.8
   container, and run `easyjson -all` — typically matching 100% of generated marshalers/unmarshalers
   verbatim.
3. **`go-bindata` embedded assets (`_bindata`, `_bintree`, `Asset*`):** Extract compressed byte
   slices and `FileInfo` metadata directly from `.rodata`/`.data` and emit verbatim `go-bindata`
   Go source.
