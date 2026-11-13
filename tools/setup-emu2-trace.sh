#!/bin/sh
# Build the tracing emu2 into tools/emu2-trace (ignored by git): dmsc/emu2 at the commit
# tools/setup-emu2.sh pins, with tools/emu2-date.patch and then tools/emu2-trace.patch applied.
# With EMU2_TRACE=FILE set it records, by program-file offset, which words of the program were
# used as data or code addresses and which instructions ran (src/trace.c in the patch says
# how), for layout audits and code coverage. Without EMU2_TRACE it runs like tools/emu2.
# Needs git, make and a C compiler. Safe to run again.      usage: sh tools/setup-emu2-trace.sh
set -e
here=$(cd "$(dirname "$0")" && pwd); dir="$here/emu2-trace"
commit=9d8698d06e67359bc8b230c2d19d5b1503e60819
[ -d "$dir/.git" ] || git clone -q https://github.com/dmsc/emu2 "$dir"
cd "$dir"
want=$(cat "$here/emu2-date.patch" "$here/emu2-trace.patch" | git hash-object --stdin)
if [ "$(git rev-parse HEAD)" != "$commit" ] || [ "$(cat .exhume-patches 2>/dev/null)" != "$want" ]; then
  git cat-file -e "$commit^{commit}" 2>/dev/null || git fetch -q origin
  git checkout -q -f "$commit"; git clean -q -f -d src
  git apply "$here/emu2-date.patch"; git apply "$here/emu2-trace.patch"
  echo "$want" > .exhume-patches
fi
make -s >/dev/null 2>&1 || { echo "emu2-trace: the build failed (run make in tools/emu2-trace to see why)"; exit 1; }
echo "emu2-trace: tools/emu2-trace/emu2 (dmsc/emu2 at $(git rev-parse --short HEAD), with the date and trace patches)"
