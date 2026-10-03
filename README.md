# Exhume

Exhume is a toolkit and a set of Claude Code skills for byte-matching decompilation of old DOS programs: writing C and assembly source that the original compiler, assembler and linker turn back into the same executable, byte for byte.

It is for retro game preservation: people who want a game's source back in a form that provably is the game, as the starting point for study, fixes and ports. It was extracted from UW2Decomp, the decompilation of Ultima Underworld II (DOS, 1993), which is its first case study: all of that game's code rebuilt from source with Borland's 1990 tools, linked into an EXE byte-identical to the original, that runs, and that shows a source change on screen (docs/case-study-uw2.md).

## What is in it

- `tools/`: the machinery. A headless DOS runner for the original toolchain (`dosrun.mjs`, in emu2, DOSBox-X, DOSBox Staging or js-dos behind one interface in `dosbackend.mjs`, with `dosbatch.py` for many sources at once and `setup-emu2.sh` to build emu2), builds (`build.py`), per-function matching with fixups masked (`match.py`), verification of everything matching masks (`verify.py`), merge gates (`merge.py`, `rebuild-symbols.py`), the map of a binary from an IDA listing and a symbol-bearing sibling build (`doslist.py`, `locate.py`, `callgraphs.py`, `anchors.py`, `align.py`, `files.py`, `targets.py`, `callpairs.py`, `sibling.py`), an assembly draft generator (`asmgen.py`), a build queue with per-file budgets for sandboxed agents (`buildd.py`, `remote.sh`), EXE comparison (`exediff.py`) and segment class experiments on objects (`omfclass.py`), booting a build, taking screenshots and searching its memory (`rungame.mjs`), the modding build's snapshot and changed-source compiles (`modding.py`), a scan for addresses written as numbers (`addrscan.py`), a screenshot comparison for bisecting (`pngdiff.py`), and a first look at an unknown EXE (`fingerprint.py`). The gate that proves the whole tree in one command (`gate.py`, with `repocheck.py` for CI, `install-hooks.sh` and `templates/`), sources in subsystem directories found by segment (`sources.py`) and hashed with their headers (`srcdeps.py`). The readability pass's tools: a declaration inventory (`declinv.py`), struct reconciliation (`structrec.py`), a header generator (`headergen.py`), raw offset and accessor macro tools (`rawoffsets.py`, `accessors.py`), warnings compared (`buildwarn.py`), and comment edits proved to change only comments (`comments.py`). Everything project-specific is read from the project's `exhume.toml` (docs/config.md).
- `profiles/borland-tc101/`: Turbo C++ 1.01, TASM 2.0 and TLINK 3.01. The command lines and name rules the tools use (`profile.toml`), and what matching taught about each tool (`compiler.md`, `assembler.md`, `linker.md`). profiles/README.md lists the profiles that would come next.
- `skills/`: Claude Code skills, one per stage: fingerprint-toolchain, map-binary, match-file, match-asm, verify-and-merge, link-and-diff, modding-build (changes of any size, and the layout audit), readability-pass (headers, constants, fields, names and comments, every byte kept), orchestrate (running several agents), port-prep.
- `docs/`: the method end to end (method.md), the configuration (config.md), the link stage, the modding build and layout audits (link.md), the readability pass (readability.md), and the UW2 case study with its numbers.
- `examples/uw2/`: UW2's configuration, its link stage as a reference implementation, a script that proves Exhume reproduces UW2Decomp's results, and the readability pass's plan, struct specs and scripts.

## Requirements

- Python 3.11 or later, with `iced-x86` (`python3 -m venv .venv && .venv/bin/pip install iced-x86`).
- Node 20 or later, and `npm install` in this directory, which fetches [dos-mcp](https://www.npmjs.com/package/dos-mcp) (a headless js-dos). js-dos runs the program itself (`rungame.mjs`), and runs the DOS toolchain when nothing faster is installed.
- Optional, for speed: git, make and a C compiler, with which `sh tools/setup-emu2.sh` builds [emu2](https://github.com/dmsc/emu2) into `tools/emu2`; the toolchain then runs about twenty times faster than in js-dos (on UW2 the whole gate takes 8 seconds instead of 74). Or DOSBox-X (`brew install dosbox-x`), nearly as fast. docs/method.md, "Choosing the DOS", has the choice and the measurements.
- The toolchain, supplied by you: for the Borland profile, the Turbo C++ 1.01 and Turbo Assembler 2.0 disk images, which `profiles/borland-tc101/setup-tc.sh` and `setup-tasm.sh` unpack (with mtools and 7z) and check by MD5. Exhume never contains compilers or other Borland software.
- The program, supplied by you. Exhume never contains game data, and nothing a link takes from your copy of the game is ever written outside the build directory.
- An IDA listing of the executable for the map stage, and optionally a sibling build with symbols.

## Quick start

```sh
npm install
python3 -m venv .venv && .venv/bin/pip install iced-x86
sh tools/setup-emu2.sh                                  # optional: the fast DOS for the toolchain
node tools/dosbackend.mjs                               # prints the DOS the toolchain will use
profiles/borland-tc101/setup-tc.sh   ~/disks/tc101      # your Disk01.img..Disk04.img
profiles/borland-tc101/setup-tasm.sh ~/disks/tasm20     # your TASM Disk01.img

.venv/bin/python tools/fingerprint.py ~/games/GAME/GAME.EXE
```

To make the skills available to Claude Code, link them into a skills directory, for example `ln -s ~/Exhume/skills/* ~/.claude/skills/` (or a project's `.claude/skills/`). The skills refer to tools by paths relative to this checkout.

Then make a project directory with an `exhume.toml` (start from examples/uw2/exhume.toml), and follow docs/method.md, using the skills for each stage. In a project, the loop for one file is:

```sh
export EXHUME_CONFIG=~/myproject/exhume.toml
python3 ~/Exhume/tools/match.py src/FILE.C --dis some_function
python3 ~/Exhume/tools/verify.py src/FILE.C
python3 ~/Exhume/tools/merge.py src/FILE.C
```

Once the whole program matches and links, the gate proves the tree in one command, and is what every later change must pass: copy `tools/templates/Makefile` beside the project's exhume.toml, fill in its `[gate]` section, and run `make check` (or `python3 ~/Exhume/tools/gate.py check`).

To check the toolkit against UW2 (needs a UW2Decomp checkout, its build, the toolchain and your UW2.EXE): `examples/uw2/prove.sh --map`, and `python3 tools/gate.py check` with `EXHUME_CONFIG=examples/uw2/exhume.toml`.

## Status

- Proven on one game, UW2, with one toolchain profile. `examples/uw2/prove.sh` rebuilds all 154 UW2 sources, matches and verifies them with the same results as UW2Decomp's own tools, and links an EXE byte-identical to UW2Decomp's and to UW2.EXE itself (docs/case-study-uw2.md has the table). The gate requires that: no known differing bytes. Its modding build gives the same EXE with nothing changed, and with sources grown in an overlay and a resident file it boots into the 3D view.
- The C and assembly stages (fingerprint, map, match, verify, merge) are general, given a profile. The verify rules assume a Borland medium-model program with VROOMM overlays; other layouts will need work there.
- The link stage is a reference implementation for UW2 only (examples/uw2/, docs/link.md): most of it encodes facts measured from UW2.EXE. The modding build's general parts (tools/modding.py) and the layout scan (tools/addrscan.py) are tools; the scan assumes a Borland medium-model program with a VROOMM segment table.
- The readability pass is proven on UW2: Exhume's gate passes on UW2Decomp's final tree, and its header generator, given UW2's plan, reproduces UW2Decomp's shared-headers commit token for token. The C parser behind the readability tools reads the top level of period C, not all of C, and the struct layout rules are Borland's.
- Only the Borland Turbo C++ 1.01 profile exists.

## License

MIT; see LICENSE. The toolkit contains no game data and no Borland binaries: each project supplies its own executable and toolchain.
