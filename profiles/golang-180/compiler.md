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
  with `--guard` on the file's other functions (`--fast --sweep` scores from the object file and
  tries every single edit and pair first); (5) when the search stalls on a plateau, trace the
  function with `gotrace.py` and read which IDs each site popped (2f); (6) if another release was
  built with another Go version, score the ordinary forms of 2h against both (2g); (7) for the
  words of one call in the wrong order, `goordergoal.py` (2i).

## 2f. The free list as `dec` reads it

`tools/gotrace.py` builds the image's own compiler with logging added and prints, for one function,
every value ID the `dec` pass takes and which pass, block and line freed it. Read from those
traces, the list `dec` pops (top first) has three parts:

- **D**, the values `opt deadcode` freed, in reverse. Later blocks come first. Inside the entry
  block the values `opt` created come first, newest first (a dead `SB` for every constant string,
  a dead `Const64` for every string length that repeats an earlier one), and then the dead values
  built before `opt` ran, e.g., the zero value of a struct temporary.
- **O**, the unused copies that `opt` reset to `OpInvalid`, e.g., the `StructSelect`s left by
  building a struct field by field.
- **E**, what is left of the values `early deadcode` freed after `short circuit`, `decompose user`
  and `opt` took theirs. Each of those allocations pops E in turn, so the n-th allocation gets
  E's n-th entry. E's top is the last blocks of the function. E's bottom is the entry block.

Before `dec` runs, `decompose builtin` pops two IDs for every named string or interface value and
three for every named slice (a variable assigned twice counts twice). Then `dec` takes a fixed
number per statement, e.g., seven for `x, _ := f(g("k"), ...)` with string argument and result
(`OffPtr Store`, `Load Load OffPtr`, `OffPtr Store`). The pair of a line is ascending or descending
according to the two list entries it lands on. When the list runs out, IDs are fresh and
ascending, so every later pair is `(ptr,len)`.

What follows from the model, measured on an HTTP handler `main.finishJob` (2,112 B, twelve
`r.FormValue` lines) and a template function `(*Builder).template` (2,592 B):

- **The first lines of a function depend on its last lines.** The first pairs land in D on dead
  `SB` and `Const64` values. Those values took their IDs from E's top, which holds the copies of
  the live variables in the blocks of the function's last `&&` and `||` expressions (one block
  pair per operand). Regrouping such a chain, or adding an operand, moves pairs sixty lines
  earlier.
- **A run of `(ptr,len)` pairs at the end of a straight-line block usually means the list ran
  out.** In `finishJob` the original's last four pairs were all ascending. The fitted source had
  74 list entries where about 55 were needed, and the empty inlined call that had been fitted
  earlier added five of the extra entries.
- **One pair can be pinned by a boundary.** A pair that straddles the end of D and the start of
  O, or the end of O and the start of E, is ascending whatever the IDs are, because O and the
  bottom of D hold old, low IDs. If the original has such a pair one position away from where the
  boundary falls, the count of entries above the boundary is off by one or two.
- **Check whether any alignment can work before searching.** For the five result words of
  `b, err := ioutil.ReadFile(...)` slide the five positions over the whole list. In `template` no
  position anywhere gave the original's order, on the fitted source or on the clean one. The dead
  `OffPtr`s of promoted field reads come in adjacent pairs in descending line order, and such
  pairs can never give an ascending pair that lies between two others. So 100,000 candidates
  from a search whose edits only slide the list found nothing. `goordergoal.py` searches for
  different list content instead (see 2i).

Go 1.7.5 has the same passes and the same list, with one difference that changes everything
downstream. A call ends its block, so the same function has several times as many blocks and
copies (199 early dead values against 88), the list never runs out, and the entry block is short.

## 2g. A second release built with another Go version

Vendors rebuild. Of the releases of one program, some were built with Go 1.8, the next patch
releases with Go 1.7.5 and later ones with Go 1.9.2 (the version string is in each binary). When
a function has the same size and line range in two releases built by one compiler, its source
did not change. When two such releases come from two compilers, the real source has to reproduce
both, and you can test a candidate against both with `tools/godual.py`.

- **A knob fitted to one compiler does nothing for the other.** In `finishJob` two empty inlined
  calls and a no-op conversion took Go 1.8 from 24 differing bytes to 8. Under Go 1.7.5 the
  function was 24 bytes off with or without them.
- **Both compilers respond to how the source is written.** Grouping an `&&` chain, putting a
  constant first in a comparison and naming a temporary each changed which lines were wrong under
  both. A form that is closer under both is evidence about the source. A form that is closer
  under one only is a fit.
- **The two wanted pair patterns are twice the constraints.** The same twelve lines wanted
  `<><<><>><<<<` under Go 1.8 and `<<<><<>>>>>>` under Go 1.7.5 (`gotrace.py trace --second`).
- **Ask which entries the second compiler has too many of.** `gotrace.deletions` removes entries
  from the list and reports the smallest sets that give the wanted pattern. For `finishJob`
  under Go 1.7.5 every answer was one entry from the group of a struct temporary's zero value
  plus one from a call result, both built before `opt` ran. A deletion of such a value is a
  change source can make by itself. A deletion of a recycled ID would renumber others. The
  answer pointed at how the struct was built, and the source was found there (2h).
- **Go 1.9 and later keep the inlining tree** (`tools/goinltree.py`), stripped or not. A later
  release showed one inlined call in `finishJob` (`time.Time.Unix`) and none in `template`. So the
  empty inlined calls fitted in both stood for ordinary statements, and no helper was missing.
- **Pick the release with the same source.** The template function had the same line range in
  the first three Go 1.7.5 releases and one more line in later ones. Compare against the former.

- **Find what the vendor changed before you fit anything** (`tools/gomoved.py A B`). The tool
  compares each function's file and lines in the two releases. A function that starts lower or
  covers more lines means its file was edited. A package's `init` is given the line after the
  last line of the package's last file, so a new file shows there. Two results from one target:
  - A set of one-line wrappers matched the first release as plain calls and the second only
    with each argument passed through an identity helper. No single form satisfied both
    compilers. The package's `init` had moved from `store_linux.go:60` to `store_other.go:10`:
    the vendor had added a nine-line file holding the helper. With that file both releases
    matched, each from its own source.
  - A function with unchanged lines differed in one loop header. Three functions of another
    package sat six lines lower in the second release, and the Go 1.9 release showed the loop
    inlining a helper from that package. The helper was the six new lines.
  A function whose own lines did not move can still inline a helper from a file that did. When
  one form suits each compiler and no form suits both, suspect a vendor change and look for
  moved lines in the packages the function calls.
- **Keep the second release's changes apart from the tree.** Put the files that reproduce them
  in an overlay directory that only the second compiler's build copies over the source. The
  functions they touch can then still be checked, and the tree stays the first release's source.
- **Under Go 1.7.5 a local's name decides its stack slot.** Slots are sorted by name
  (`cmpstackvarlt`), spilled temporaries are named `autotmp_NNNN`, the words of a string or
  interface local are `name.ptr`, `name.len`, `name.itab` and `name.data`, and an inlined call's
  result is `~r0`. A slot swap between a local and a temporary means the local's name sorts on
  the other side of `autotmp_`. The assembly listing (`-gcflags -S`) prints the name of every
  slot of your own build, which shows at once which names are out of order. Several variables
  with one name (two `~r0`) are a tie, and the sort breaks a tie by the order and number of all
  declared variables, so it can move when an unrelated local is added.

Set-up is one container per toolchain with the project on the same mount. `godual.py score` prints
both results for a file. `godual.py enum SPEC.json` scores a list of named rewrites one, two and
three at a time. With host-native compilers (`tools/gonative.py`) one score took 1.4 seconds.

## 2h. Source that compiles to the same code and numbers values differently

These are ordinary ways to write the same thing. Each emits identical instructions and leaves
different dead values, so each is a candidate for the real source, and each was seen to move
pairs under both compilers. Try them before any knob.

- **A struct argument built in a named local.** `db.add(user, id, Entry{Time: t, Level: lvl,
  Text: "done"})` and
  ```go
  var entry Entry
  entry.Time = t
  entry.Level = lvl
  entry.Text = "done"
  err := db.add(user, id, entry)
  ```
  store the same words. For the literal, `gc/sinit.go:anylit` zeroes a temporary and assigns the
  fields. Both forms zero and assign, and they differ in which values hold the intermediate
  structs and when those die. `finishJob` matched both releases with the named local and with no
  knob at all, after every knob search on the literal form had failed. The function above it in
  the same file used the named local, which was the hint.
- **The grouping of `&&` and `||`.** `a && b && c && d` is `((a && b) && c) && d`. Each of the
  five groupings of four operands creates the join blocks in another order. A join created
  before the blocks of its right operand has a lower block number and newer values, which puts
  an ascending step into E.
- **Which operand comes first when one is a new constant.** `x.Kind == 4001` loads and then
  makes the constant. `4001 == x.Kind` makes the constant first. `x > 0` against `0 < x` changes
  nothing, because the zero constant already exists and is shared.
- **Declaration style.** `var id string` at the top and `id = r.FormValue("ID")` later, against
  `id := ...`. Each named string or interface value costs `decompose builtin` two pops.
- **A named value for a temporary.** `v := r.FormValue("k"); n, _ := strconv.ParseInt(v, 10, 64)`,
  `now := time.Now(); t := now.Unix()`, `if e := err; e != nil`.
- **`else` after a branch that returns**, and `switch { case a, b: }` for `if a || b`. The join
  block of an `if` is created before its bodies.

`tools/gorandperm.py` has the grouping (`m_assoc`), a folded-away operand (`m_trueop`) and the
`else` form (`else_wrap`) as edits. The others are quick to write as a `godual.py enum` spec.

## 2i. What a knob does besides its purpose, and three false results

- **`x+0`, `x|0` and `x-0` create the function's zero constant where they stand.** The constant is
  cached, and an instruction that later materialises a zero (`XORL`) carries the line of the
  first use. `template` had every byte right and one line number wrong, 10 for 23, because of
  three `+0` in a dead expression on line 10. A unary minus pads with one dead value and no
  constant. `(-len(s))` in place of `(len(s)+0)` gave the match.
- **`_ = x` is a read.** If `x` was defined in another block it leaves one dead copy in the
  current block. So the same knob does different things in different blocks.
- **A variable that is live across a join gets a copy there.** A folded zero made early and used
  late (`z := len(s) - len(s)` ... `n+z`) adds copies at every join in between and its values are
  not freed before `dec`. Use it in the block that makes it.
- **An empty inlined call (`func nop() {}`) starts a new block.** After a `return` it also makes
  a dead block, so the next join gets a phi for every live variable. It fits many things and
  stands for none of them (2g).
- **Not folded in Go 1.8:** `int64(v)` of an `int16` variable that holds zero. It emits code.

Three results in this work were wrong, and each cost hours:

1. A search located the five words of a call's results at a fixed position in the list. An edit
   that made `dec` split another value first moved the words, and the search reported a perfect
   order for a function that was no better. Locate values by their own allocation pattern
   (`gotrace.load_group`) and confirm an order from the disassembly after the call.
2. Two runs used one work directory name inside the container. Each compiled the other's files
   and both reported scores for the wrong text. A work directory has to carry the run's name
   (`gorandperm.py` and `goordergoal.py` use the output directory's), and an improvement should be
   measured twice before it is kept.
3. A source form was dropped after one test that did not compile because the edit was on the
   wrong lines. The form was the answer (2h, first item). A candidate that fails to build has
   not been tested.

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
