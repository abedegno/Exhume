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

`gocmpx.py` compares functions of two binaries and gives each one of these statuses: MATCH, LINES (same code, a different line table), INLINE (same code and lines, a different inlined call), DIFF, SIZE or MISSING. Calls and jumps that leave the function must reach a function of the same name in both binaries. References to data are masked.

```
gocmpx.py REF CAND REGEX
gocmpx.py REF CAND --diff NAME
```

The first form prints one line per function of REF whose name matches REGEX. The second form prints one function of both binaries side by side, after padding is trimmed. Each row shows the offset, the instruction with call targets and function addresses as names, the source file and line, and the innermost inlined callee. A row is marked `*` when the instructions differ and `~` when only the line or the inlined callee differs. The two sides are aligned by a sequence diff, so the listing also works when the sizes differ.

### gogatex.py

`gogatex.py` is the gate. It compares every first-party function of a candidate with the reference and keeps a status file with one row per function. A function is first-party when its file starts with the prefix, or when its name starts with a `--name-prefix`. The second rule also selects closures made by inlined library calls and functions that the compiler generates, such as method wrappers.

```
gogatex.py REF CAND STATUS_TSV --prefix shop/ [--exclude /vendor/] [--name-prefix shop/]
           [--src DIR] [--only REGEX] [--accept-regressions]
```

The last line reads `matched N of M; fitted F`. When a function that matched before no longer matches, the gate prints `REGRESSED` and its name, exits with status 1 and leaves the status file as it was. The regression is reported again on every run until it is fixed, or until it is recorded with `--accept-regressions`. With `--only REGEX`, only the functions whose names match are scored, and the other rows of the status file are kept.

### gotypes.py

`gotypes.py` recovers Go type declarations from the runtime type information in a binary. It writes structs with their fields, embedded fields and tags, interfaces with their methods, and other named types. A type that the program never needs at run time is not in the binary.

```
gotypes.py BINARY PKG_REGEX
```

### goskel.py

`goskel.py` writes a skeleton source tree that builds. Every function becomes a one-line stub on the line where it starts in the binary, and the named types of each package go into its `types_recovered.go`. Signatures come from method tables where the linker kept them, and are otherwise guessed from the argument layout. The file `UNKNOWN.txt` lists every guess.

```
goskel.py BINARY OUT_DIR --prefix shop/ [--modcache DIR] [--keep TAG] [--adopt-unmarked-stubs]
```

## Stub markers

The skeleton tool owns only the lines that carry its markers. A stub is one line of the shape below.

```
func Total(items []Item) int { panic("todo") } // skel:stub
```

The rules are these:

- A line that ends in `// skel:stub` or `// skel:import` and is exactly as the tool wrote it belongs to the tool. A later run may replace it, move it or blank it.
- To claim a function, write its body and remove the marker. The tool then treats the function as hand-written and never stubs it again.
- A marked line that was edited by hand is kept as hand-written, and the tool prints a warning naming it.
- Every other line of a file stays as it was. New stubs and imports go on free lines, which are blank lines outside any declaration, comment or string. A stub whose line is taken goes to the next free line and is listed as moved.
- `types_recovered.go` belongs to the tool and is rewritten on each run. Each declaration in it follows a `// skel:type` line. If the file holds a declaration without that line, the run stops and writes nothing, so that the declaration can be moved to another file first.
- A source file that the tool cannot scan, such as one with a backslash and a newline inside a quoted string, stops the run with a message naming the file, and nothing is written.

## The keep tag

The linker drops every function that nothing calls, and the functions of a skeleton are mostly not called. With `--keep TAG`, each package also gets a file `zz_keep_<name>.go` that is built only with that tag. It holds one variable whose initialiser names every function and method of the package, so the linker keeps them all and the gate can compare them. A build without the tag is the real program.

Taking a function's address in this way is assumed not to change the function's code. Functions that match the reference while built with the tag support this, because the reference was built without it.

## Limits

- References to data are masked by the comparator. A wrong string or global of the same size still matches.
- Package init functions cannot match under the keep tag. The keep variable is built with `append` so that it is set at run time, and that code goes into the package's init function.
- Only Go 1.26 binaries were tested, and only non-PIE linux/amd64 ELF files. The readers accept Go 1.20 and later, but older layouts have not been checked against real binaries.
