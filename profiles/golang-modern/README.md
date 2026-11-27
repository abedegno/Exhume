# golang-modern

This profile is for matching programs built by a recent Go compiler (Go 1.20 and later, tested on Go 1.26) for linux/amd64. Unlike the golang-180 profile, it does not fingerprint a toolchain or drive the compiler itself. A project brings its own build script, and these tools read the reference binary and the candidate binary that the build produces. They tell which functions match, show what differs, and write a skeleton source tree that builds and puts every function on the line where the original has it.

The tools are pure Python and need `objdump` (LLVM or GNU) on the PATH. The tests build a small fixture module, `shop`, with the host's Go, so they need `go` as well. Run them from the repository root.

```
python3 -m unittest discover -s profiles/golang-modern/tests -t .
```

## The tools

### gobin.py

`gobin.py` reads a Go ELF binary: its functions, line tables, inlining trees and build info. It works on stripped and unstripped binaries, linked internally or externally. The other tools import it.

```
gobin.py funcs BINARY
gobin.py lines BINARY NAME
gobin.py inltree BINARY NAME
gobin.py buildinfo BINARY
```

### gocmpx.py

`gocmpx.py` compares functions of two binaries and gives each one of these statuses: MATCH, LINES (same code, a different line table), INLINE (same code and lines, a different inlined call), DIFF, SIZE or MISSING. Calls and jumps that leave the function must reach a function of the same name in both binaries. String literals, type descriptors and itabs are compared by content or name; other references to data are masked.

```
gocmpx.py REF CAND REGEX
gocmpx.py REF CAND --diff NAME
```

What a MATCH vouches for: the instructions are equal apart from the 4-byte displacements of calls, jumps that leave the function and RIP-relative operands; every call or jump that leaves the function reaches the same offset of a function of the same name; and of the RIP-relative (data) operands, those that point at a function or a function value point at the same function, those that point at a type descriptor or an itab name the same types, and those that load the address of a string literal whose length is loaded beside it (in the next argument register, or stored as the second word of a string header in the same block) point at equal bytes. Every other data operand is masked: globals, jump tables, other static data, and strings whose length cannot be paired with the address. Equal sources never give a DIFF, because a length is only paired in those two forms, and the pairing found in the reference is used at the same offset of the candidate. The command prints to stderr how many data operands of the MATCH functions were compared by content, by type, by function and how many stayed masked; `--diff` prints the same counts for its function, and `gogatex.py` for the functions it scores.

The first form prints one line per function of REF whose name matches REGEX. The second form prints one function of both binaries side by side, after padding is trimmed. Each row shows the offset, the instruction with call targets and function addresses as names, the source file and line, and the innermost inlined callee. A row is marked `*` when the instructions differ and `~` when only the line or the inlined callee differs. The two sides are aligned by a sequence diff, so the listing also works when the sizes differ.

### gogatex.py

`gogatex.py` is the gate. It compares every first-party function of a candidate with the reference and keeps a status file with one row per function. A function is first-party when its file starts with the prefix, or when its name starts with a `--name-prefix`. The second rule also selects closures made by inlined library calls and functions that the compiler generates, such as method wrappers.

```
gogatex.py REF CAND STATUS_TSV --prefix shop/ [--exclude /vendor/] [--name-prefix shop/]
           [--src DIR] [--only REGEX] [--accept-regressions]
           [--accept-regressions-from NAME]...
```

The last line reads `matched N of M; fitted F; waiting W; K of the matched call unmatched code`. N counts the rows that are MATCH and M all rows. F counts the functions whose source holds a `// fitted:` comment. W counts the functions marked as waiting (see below) that are not MATCH. K counts the MATCH rows whose `waits` is not empty. When a function that matched before no longer matches, the gate prints `REGRESSED` and its name, exits with status 1 and leaves the status file as it was. The regression is reported again on every run until it is fixed, or until it is recorded with `--accept-regressions`. A row that matched before and is missing from the new result for any reason (gone from the reference, no longer selected) counts as a regression too. `--accept-regressions-from NAME` accepts a narrower set: a regressed row whose `waits` in the old file names NAME, when NAME is MATCH in this run (or, under `--only` and not scored, in the file, as long as NAME is still selected in the reference), and whose new status is SIZE, DIFF, LINES or INLINE. A callee's body can change a function's code, but it cannot make the function MISSING or DUP, so those stay regressions. Such a row prints `ACCEPTED`, its name, `caused by NAME` and its new status, and is written with that status. When another regression is refused, nothing is written, and the line starts with `WOULD-ACCEPT` instead. Use it when NAME has just got its real body and its callers changed through inlining or escape analysis. Any other regression still prints `REGRESSED` and blocks the write, so the file is written, with exit status 0, only when every regression is accepted. The option can be given more than once. A selection that yields no function at all, for example a wrong `--prefix`, is a tool error (exit 2) and the file is left alone. When a regression blocks the write, the last line gives the totals of the unchanged file and says so. With `--only REGEX`, only the functions whose names match are scored, and the other rows of the status file are kept. A first run with `--only`, when there is no status file yet, produces a file that holds only the scored rows.

The status file has the columns `name`, `file`, `status`, `fitted`, `since` and `waits`. A file of the first five columns, written by an older gate, is read with empty `waits`. The `waits` column names the functions that this function calls, jumps to or takes the address of in the reference's code, and that are either first-party and not MATCH in this run, or absent from the candidate. When such a function gets its real body, the caller can change through inlining or escape analysis, so a MATCH with a non-empty `waits` may not last. Only the function's own instructions are read: a call made inside an inlined callee counts when its call instruction is in the function. The names are sorted and separated by single spaces. A Go symbol name can hold commas, semicolons and spaces (a generic instantiation over a struct type), so no separator is safe on its own: in each name `%` is written as `%25` and a space as `%20`. With `--src`, `waits` also names the functions that the reference always inlines and that are still stubs. Such a function has no entry in the function table and so no row of its own, but the inlining tree of each function it was inlined into names it, with its file and start line. The gate adds it to the caller's `waits` when it is first-party by the same rules as above (its file, or its name), has no row, and its declaration line in the source tree still ends in the `// skel:stub` marker that `goskel.py` writes, with or without words after it such as `inline-only`. Without `--src`, only called functions are listed.

The `waits` lists are recomputed for every row on every run that writes the status file, including runs with `--only`. A row that was not scored in the run gets its list from the reference's calls and inlining tree and the statuses of the run, without being compared again, so a list never names a function that has matched since. A row that `--only` keeps for a function that is no longer selected keeps its old list, because the reference no longer has its code. A row with the status DUP has an empty list.

#### The waiting marker

A function can carry the comment `// waits:` on the line directly above its declaration, or as a trailing comment on the declaration line. Anything after the colon is ignored, so it can hold a note. The marker says that the body is believed to be right, and that it cannot match until the functions it calls are written. The gate reads it from the source tree given with `--src`, for every selected function, whether scored in the run or not. When two functions start on the same line, only the first of them gets the marker, as with `// fitted:`.

The marker adds two kinds of line to the output, which come after the `duplicate names` line and before the `scored` line.

- `RESCORE<TAB>name` is printed for a marked function that is not MATCH and whose `waits` is empty. As far as the gate can tell, every function it calls or inlines now matches, so nothing is left to wait for, and the function should be given to a person or an agent again.
- `WAIT-DONE<TAB>name` is printed for a marked function that is MATCH. The marker can then be deleted.

Both are worked out over the status file as it stands, like the totals, so when a regression blocks the write they use the old file. A marked function counts as matched only when it is MATCH, and the marker changes nothing about the regression rules.

The gate also checks the data operands that the comparator masks, which are mostly package variables. Over all the functions scored MATCH in the run, it pairs each masked operand's address in the reference with its address in the candidate, at the same instruction. A reference address should always pair with one candidate address, and the reverse. Pairings that break this are grouped into conflicts. A conflict is decided when the pairing used by the most functions has at least ten times as many functions as every other pairing in it. In a decided conflict, every operand of the other pairings is printed as `GLOBAL<TAB>name<TAB>+OFF: PAIRING, but MAJORITY`, where each pairing reads `REF -> CAND (N operands in M functions)`. These functions are the likely culprits. One line, `GLOBAL-BYSTANDERS<TAB>N functions share the other pairing, first NAME+OFF`, sums up the majority. When the counts are closer than that, the majority can just as well be the wrong side, so the gate names no culprit. Every operand of the conflict is printed as `GLOBAL<TAB>name<TAB>+OFF: PAIRING conflicts with OTHER`, where OTHER is the largest other pairing, and there is no bystanders line. Both pairings and their counts are on each line, so the reader can judge which side is wrong. A function that names the wrong variable of the right size shows up here, and so does a variable whose type has the wrong size, such as an empty struct in the candidate, which shares its address with other empty variables. These lines are warnings: they change no status and no exit status. The line `globals: K functions likely wrong, B bystanders, U undecided in G conflicts` comes before the last line. K counts the likely culprits of decided conflicts, B the functions of their majorities that are not culprits elsewhere, U the functions of undecided conflicts that are not culprits elsewhere, and G all conflicts. Under `--only` it ends with `(partial run: unscored functions not checked)`. Read-only data is checked too: the linker merges objects there by content in the same way in both binaries, so equal sources still pair one to one.

### gotypes.py

`gotypes.py` recovers Go type declarations from the runtime type information in a binary. It writes structs with their fields, embedded fields and tags, interfaces with their methods, and other named types. A type that the program never needs at run time is not in the binary.

```
gotypes.py BINARY PKG_REGEX
```

### gosig.py

`gosig.py` recovers how many result registers a function has, for functions whose signature is not in the type information. It reads the whole binary and works out, for every function, the registers that must hold a result (read after a direct call before they are written, written before a `ret` and not read again, or passed on by a caller whose results are known) and the registers that may hold one (not garbage at any `ret`). The methods with known types seed both. A function gets an answer only when the two sets agree, it has at least one direct call site, no result is on the stack, and its results are not a mix of integer and floating-point registers. The answer is one class per register, `int` or `float`; how registers group into strings, slices, interfaces or structs stays unknown, so a function that returns a string shows as two `int`.

```
gosig.py BINARY REGEX
```

Each line gives the name, the classes (`-` for no results, `?` for no answer) and the number of direct call sites. It needs `objdump` (LLVM or GNU), as `gocmpx.py` does.

### goskel.py

`goskel.py` writes a skeleton source tree that builds. Every function becomes a one-line stub on the line where it starts in the binary, and the named types of each package go into its `types_recovered.go`. Signatures come from method tables where the linker kept them, and are otherwise guessed: the parameters from the argument layout, the results with `gosig.py`. The file `UNKNOWN.txt` lists every guess.

A function that the compiler inlined at every call has no entry of its own in the function table, but the inlining trees of its callers still name it, with its file and start line. The tool writes a stub for each such function under the prefix, on its start line in its file, and creates the file, or the package, when the tree does not have it. Its signature is unknown, so the stub has no parameters and ends in `// skel:stub inline-only signature unknown`. A method gets its receiver from the name; when the package declares no type of that name, the method is skipped. The instances of a generic function give one stub with as many type parameters as the instances have. `UNKNOWN.txt` lists each of these functions with the number of functions it was inlined into.

```
goskel.py BINARY OUT_DIR --prefix shop/ [--modcache DIR] [--keep TAG] [--adopt-unmarked-stubs]
          [--no-noinline-directives]
```

## Stub markers

The skeleton tool owns only the lines that carry its markers. A stub is one line of the shape below.

```
func Total(items []Item) int { panic("todo") } // skel:stub
```

The rules are these:

- A line that ends in `// skel:stub` or `// skel:import` and is exactly as the tool wrote it belongs to the tool. A later run may replace it, move it or blank it. So does a line that is exactly `//go:noinline // skel:noinline` (see below).
- To claim a function, write its body and remove the marker. The tool then treats the function as hand-written and never stubs it again.
- A marked line that was edited by hand is kept as hand-written, and the tool prints a warning naming it.
- Every other line of a file stays as it was. New stubs and imports go on free lines, which are blank lines outside any declaration, comment or string. A stub whose line is taken goes to the next free line and is listed as moved.
- `types_recovered.go` belongs to the tool and is rewritten on each run. Each declaration in it follows a `// skel:type` line. If the file holds a declaration without that line, the run stops and writes nothing, so that the declaration can be moved to another file first.
- A source file that the tool cannot scan, such as one with a backslash and a newline inside a quoted string, stops the run with a message naming the file, and nothing is written.

## The noinline directive on stubs

A stub is so small that the compiler inlines it into every function that calls it. A caller whose body is already right then compiles to the wrong code, because the inlined panic ends it early, and it cannot match until the callee is written too. When the reference never inlines that callee, its callers in the reference make a real call. The directive `//go:noinline` above the stub makes the candidate's callers make a real call as well.

The tool writes the line `//go:noinline // skel:noinline` directly above a stub when the function has its own entry in the function table and no inlining tree in the binary names it. The compiler reads only the first word after `//go:`, so the marker after it does not change the directive. A stub marked `inline-only` never gets it, and neither does a function that the binary inlines even once, because its callers in the reference inlined it. Generic functions are skipped as well, since their instances are named by shape and the tool cannot tell from the names whether one was inlined. The line goes in only when the line above the stub is blank, is a free line, and holds no code of any function in the binary's line table. Otherwise the stub gets no directive, and `UNKNOWN.txt` lists it with the reason.

A directive line exactly as written belongs to the tool, like a marker line. When a function is claimed, the next run blanks the directive above it, since it belonged to the stub. A comment written by hand in its place is left alone. The directive only ever replaces a blank line, so no other line of a file moves. The option `--no-noinline-directives` writes none and blanks those of earlier runs.

The directive cannot fix every caller. A caller that passes pointers to a stub can still compile differently from the reference, because the compiler's escape analysis reads the stub's body, which panics, and not the real one.

## The keep tag

The linker drops every function that nothing calls, and the functions of a skeleton are mostly not called. With `--keep TAG`, each package also gets a file `zz_keep_<name>.go` that is built only with that tag. It holds one variable whose initialiser names every function and method of the package, so the linker keeps them all and the gate can compare them. A build without the tag is the real program.

Taking a function's address in this way is assumed not to change the function's code. Functions that match the reference while built with the tag support this, because the reference was built without it.

## Limits

- References to data are masked by the comparator. A wrong string or global of the same size still matches.
- Package init functions cannot match under the keep tag. The keep variable is built with `append` so that it is set at run time, and that code goes into the package's init function.
- Only Go 1.26 binaries were tested, and only non-PIE linux/amd64 ELF files. The readers accept Go 1.20 and later, but older layouts have not been checked against real binaries.
