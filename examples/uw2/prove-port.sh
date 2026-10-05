#!/bin/sh
# Prove Exhume's port tools and runtime on UW2Decomp's native port, writing nothing into the
# UW2Decomp checkout: everything happens in snapshots of its HEAD under $OUT (default
# ~/Exhume/build/uw2-port), made with git archive, with its gitignored toolchain (TC, TASM)
# copied in and its fetched or built helpers (fmtowns, .venv, node_modules, tools/emu2,
# tools/libs, tools/nuked-opl3) linked. UW2Decomp builds against this checkout's runtime,
# where it is (EXHUME is set to this checkout for its own tools too).
#
#   examples/uw2/prove-port.sh [--dos] [--keep] [STEP ...]     STEP: build verify runtime fuzz asm2c toolkit sound ci
#
# build    Exhume's gate passes on the snapshot; portbuild.py links a uw2port byte-identical to the
#          one UW2Decomp's portbuild.py links in the same tree; replay.py build gives the replay DOS
#          EXE the committed goldens were made with (same SHA-256)
# verify   replay.py verify all: every session identical to UW2Decomp's committed golden
#          (with --dos also replay.py golden all --check: each session twice in DOS, identical to
#          each other and to the golden)
# runtime  the snapshot holds no copy of a runtime file (no file of the runtime's names in
#          src/port, src/include or src/replay, nor one with a runtime file's contents), and has
#          the bindings runtime/README.md names; built against this checkout's runtime the gate
#          passes and the port verifies every session (with --dos, the replay DOS build, which
#          compiles the runtime's portable.h and replay.c where they are, reproduces every golden)
# fuzz     fuzzasm.py's quick run with examples/uw2/port/fuzz_targets.py
# asm2c    asm2c.py --check, and regenerating every translation reproduces UW2Decomp's files, which
#          then build, verify every session and fuzz clean
# toolkit  portcheck, portstubs --check, widths --diff, intaudit, layoutcheck: the same output
#          as UW2Decomp's tools
# sound    ailcheck.py on the three sessions with a music card; and the WAV of everything the
#          cards play in the whole sound and soundfm sessions, rendered by the snapshot's port,
#          byte-identical to the one BASE's port renders (BASE: a UW2Decomp ref built with its
#          own tools; UW2: v1.2.0-rc9, the last release with its own copies of the runtime; with
#          PATCH and no BASE, the unpatched REF)
# ci       tools/templates/ci instantiated for UW2 equals UW2Decomp's workflows and actions, and
#          actionlint passes on them
#
# REF names the UW2Decomp commit to snapshot (default HEAD); its working tree is never read,
# but for the gitignored helpers named above. PATCH, a patch for UW2Decomp (git diff, -p1), is
# applied to every snapshot: a change to UW2Decomp proved before it is committed there.
#
# UW2's game code is UW2Decomp's, never Exhume's: its bindings for the runtime (src/port's
# portgame.h, asmgame.h, ailgame.h, src/include's hookgame.h, rpgame.h), the sound library's
# time-variant effects ([sound] extensions, src/port/sound/tvfx.c), the renderer's divide fault
# handlers (src/port/x86/divfault.c, asm_divfault) and asm2c's overrides ([asm2c] overrides,
# tools/asm2c.py) come from the snapshot.
#
# Needs: UW2Decomp at $UW2DECOMP (default ~/underworld-exhumed/uw2) with TC/, TASM/, .venv (iced-x86,
# unicorn), node_modules, tools/emu2 and tools/nuked-opl3 in place; your UW2.EXE and game
# directory (UW2_EXE, UW2_DIR); SDL3 and libmt32emu (brew install sdl3 mt32emu); DOSBox-X for
# --dos; actionlint for ci.
set -e
here=$(cd "$(dirname "$0")" && pwd); ex=$(cd "$here/../.." && pwd)
UW=${UW2DECOMP:-$HOME/underworld-exhumed/uw2}
OUT=${OUT:-$ex/build/uw2-port}
case "$OUT/" in "$UW"/*) echo "refusing: OUT is inside $UW"; exit 1;; esac
PY=${PY:-$ex/.venv/bin/python}
DOS=0; KEEP=0; STEPS=""
for a in "$@"; do case "$a" in --dos) DOS=1;; --keep) KEEP=1;; *) STEPS="$STEPS $a";; esac; done
[ -n "$STEPS" ] || STEPS="build verify runtime fuzz asm2c toolkit sound ci"
export EXHUME_CONFIG="$here/exhume.toml"
export EXHUME="$ex"           # UW2Decomp's own tools (its tools/exhume.py) build against this runtime
unset EXHUME_BUILD EXHUME_SRC EXHUME_SYMBOLS EXHUME_MATCHED EXHUME_MAP
P="$OUT/proof"; mkdir -p "$P"
# the UW2Decomp commit proved against (REF, default HEAD), named so the report says which
REF=$(git -C "$UW" rev-parse "${REF:-HEAD}")
echo "UW2Decomp $(git -C "$UW" log -1 --format='%h %s' "$REF")"

# a snapshot of UW2Decomp's HEAD at $1, with the toolchain copied (tcc.mjs and dosrun copy their
# stage with cpSync, which copies a symbolic link as a link) and the helpers linked
snapshot() {   # DIR [nopatch|REF]
  d=$1; at=$REF
  case "$2" in ""|nopatch) ;; *) at=$2;; esac
  if [ -d "$d" ] && [ "$KEEP" = 1 ]; then return; fi
  rm -rf "$d"; mkdir -p "$d"
  git -C "$UW" archive "$at" | tar -x -C "$d"
  # git apply, which creates and deletes files as the patch says, with no repository above the
  # snapshot (OUT may be inside Exhume's own tree)
  if [ -n "$PATCH" ] && [ -z "$2" ]; then
    ( cd "$d" && GIT_CEILING_DIRECTORIES="$(dirname "$d")" git apply -p1 "$PATCH" ) || { echo "PATCH does not apply to $d"; exit 1; }
  fi
  cp -R "$UW/TC" "$d/TC"; cp -R "$UW/TASM" "$d/TASM"
  for x in fmtowns .venv node_modules; do [ -e "$UW/$x" ] && [ ! -e "$d/$x" ] && ln -s "$UW/$x" "$d/$x"; done
  for x in emu2 libs nuked-opl3; do [ -e "$UW/tools/$x" ] && [ ! -e "$d/tools/$x" ] && ln -s "$UW/tools/$x" "$d/tools/$x"; done
  return 0
}
X() { d=$1; shift; ( cd "$d" && UW2DECOMP="$d" EXHUME_TC="$d/TC" EXHUME_TASM="$d/TASM" "$PY" "$ex/tools/$@" ); }
S="$OUT/snap"

for step in $STEPS; do case $step in
build)
  echo "== build"
  snapshot "$S"
  X "$S" gate.py check > "$P/gate.txt" 2>&1 && echo "   gate: $(tail -1 "$P/gate.txt")" || { echo "   gate FAILED ($P/gate.txt)"; exit 1; }
  ( cd "$S" && rm -rf build/port build/port-uw2decomp && .venv/bin/python tools/portbuild.py > "$P/portbuild.uw2decomp.txt" && mv build/port build/port-uw2decomp )
  X "$S" portbuild.py > "$P/portbuild.txt" && tail -1 "$P/portbuild.txt" | sed 's/^/   /'
  same=0; diff=0
  for o in $(cd "$S/build/port-uw2decomp" && find . -name '*.o'); do
    if cmp -s "$S/build/port-uw2decomp/$o" "$S/build/port/$o"; then same=$((same+1)); else diff=$((diff+1)); echo "   object differs: $o"; fi
  done
  cmp -s "$S/build/port-uw2decomp/uw2port" "$S/build/port/uw2port" && echo "   uw2port byte-identical to UW2Decomp's portbuild.py's ($same objects equal, $diff differ)" || echo "   uw2port DIFFERS ($diff objects differ)"
  X "$S" replay.py build > "$P/replaybuild.txt" 2>&1
  want=$(grep -o '"exe_sha256": "[0-9a-f]*"' "$S/tests/replay/golden/newgame/golden.json" | cut -d'"' -f4)
  have=$(shasum -a 256 "$S/build/replay/UW2.EXE" | cut -d' ' -f1)
  [ "$want" = "$have" ] && echo "   replay DOS build: the EXE the goldens were made with ($have)" || echo "   replay DOS build DIFFERS from the goldens' ($have, want $want)"
  ;;
verify)
  echo "== verify"
  X "$S" replay.py verify all > "$P/verify.txt" 2>&1 || true
  grep "^[a-z]" "$P/verify.txt" | sed 's/^/   /'
  if [ "$DOS" = 1 ]; then
    X "$S" replay.py golden all --check > "$P/golden-check.txt" 2>&1 || true
    sed -n '/^== golden/,$p' "$P/golden-check.txt" | sed 's/^/   /'
  fi
  ;;
runtime)
  echo "== runtime: UW2Decomp builds against this checkout's runtime, with no copy of it"
  snapshot "$S"
  RT="$ex/runtime"; bad=0
  # no file of a runtime file's name where the copies used to be, and no file with a runtime
  # file's contents anywhere in the snapshot's src
  for f in $(cd "$RT" && find port include replay -type f \( -name '*.c' -o -name '*.h' \)); do
    b=$(basename "$f")
    case $f in
      port/*) [ -e "$S/src/port/${f#port/}" ] && { echo "   a copy of runtime/$f: src/port/${f#port/}"; bad=1; } ;;
      include/*) [ -e "$S/src/include/$b" ] && { echo "   a copy of runtime/$f: src/include/$b"; bad=1; } ;;
      replay/*) [ -e "$S/src/replay/$(echo "$b" | tr a-z A-Z)" ] && { echo "   a copy of runtime/$f: src/replay"; bad=1; } ;;
    esac
  done
  for f in $(cd "$S/src" && find . -type f \( -iname '*.c' -o -iname '*.h' \)); do
    for g in $(cd "$RT" && find . -type f -name "$(basename "$f" | tr A-Z a-z)"); do
      cmp -s "$S/src/$f" "$RT/$g" && { echo "   src/${f#./} is runtime/${g#./}"; bad=1; }
    done
  done
  [ $bad = 0 ] && echo "   no copy of a runtime file in the snapshot's src ($(cd "$RT" && find port include replay -type f \( -name '*.c' -o -name '*.h' \) | wc -l | tr -d ' ') runtime files)"
  miss=""
  for f in src/port/portgame.h src/port/asmgame.h src/port/ailgame.h src/include/hookgame.h src/include/rpgame.h; do
    [ -f "$S/$f" ] || miss="$miss $f"
  done
  [ -z "$miss" ] && echo "   the bindings: src/port/portgame.h, asmgame.h, ailgame.h, src/include/hookgame.h, rpgame.h" || { echo "   bindings MISSING:$miss"; bad=1; }
  X "$S" gate.py check > "$P/runtime-gate.txt" 2>&1 && echo "   gate: $(tail -1 "$P/runtime-gate.txt")" || { echo "   gate FAILED ($P/runtime-gate.txt)"; bad=1; }
  [ -x "$S/build/port/uw2port" ] || X "$S" portbuild.py > /dev/null
  X "$S" replay.py verify all > "$P/runtime-verify.txt" 2>&1 || true
  grep "^verify:" "$P/runtime-verify.txt" | sed 's/^/   /'
  if [ "$DOS" = 1 ]; then
    X "$S" replay.py golden all --check > "$P/runtime-golden-check.txt" 2>&1 || true
    sed -n '/^== golden/,$p' "$P/runtime-golden-check.txt" | sed 's/^/   /'
  fi
  [ $bad = 0 ] || exit 1
  ;;
fuzz)
  echo "== fuzz"
  [ -x "$S/build/port/uw2port" ] || X "$S" portbuild.py > /dev/null
  X "$S" fuzzasm.py > "$P/fuzz.txt" 2>&1 || true
  tail -1 "$P/fuzz.txt" | sed 's/^/   /'
  ( cd "$S" && .venv/bin/python tools/fuzzasm.py > "$P/fuzz.uw2decomp.txt" 2>&1 || true )
  strip() { sed 's/([0-9.]* s)//; s/, [0-9.]* s$//' "$1"; }
  [ "$(strip "$P/fuzz.txt")" = "$(strip "$P/fuzz.uw2decomp.txt")" ] && echo "   the same report as UW2Decomp's fuzzasm.py, but for the timings" || echo "   report DIFFERS from UW2Decomp's fuzzasm.py"
  # a target made to compare registers the C is known not to set must fail
  printf '%s\n' "import importlib.util, fuzzasm" \
    "spec = importlib.util.spec_from_file_location('uwt', '$here/port/fuzz_targets.py')" \
    "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)" \
    "REGIONS = m.REGIONS" \
    "t = [x for x in m.TARGETS if x.name == 'expand.pal_63'][0]" \
    "t.regs = fuzzasm.GPR" \
    "TARGETS = [t]" > "$P/neg_targets.py"
  sed "s#^targets = .*#targets = \"$P/neg_targets.py\"#" "$here/exhume.toml" > "$P/neg.toml"
  ( cd "$S" && UW2DECOMP="$S" EXHUME_CONFIG="$P/neg.toml" "$PY" "$ex/tools/fuzzasm.py" > "$P/fuzz-neg.txt" 2>&1 ) \
    && echo "   negative check FAILED: comparing registers the C does not set found no difference" \
    || echo "   negative check, every register of expand.pal_63: $(tail -1 "$P/fuzz-neg.txt")"
  ;;
asm2c)
  echo "== asm2c"
  snapshot "$S"
  X "$S" asm2c.py --check > "$P/asm2c-check.txt" 2>&1 && echo "   asm2c.py --check: the committed translations are up to date" || { echo "   asm2c.py --check: OUT OF DATE"; cat "$P/asm2c-check.txt"; }
  A="$OUT/snap-asm2c"; snapshot "$A"
  files=$("$PY" -c "
import importlib.util
s = importlib.util.spec_from_file_location('s', '$here/port/asm2c_spec.py'); m = importlib.util.module_from_spec(s); s.loader.exec_module(m)
print(' '.join([o for _, o, _ in m.MODULES] + [m.MODTAB]))")
  ( cd "$A" && rm -f $files )
  X "$A" asm2c.py > "$P/asm2c-regen.txt" 2>&1
  n=0; bad=0
  for f in $files; do if cmp -s "$UW/$f" "$A/$f"; then n=$((n+1)); else bad=$((bad+1)); echo "   regenerated file DIFFERS: $f"; fi; done
  echo "   regenerated from nothing: $n files identical to UW2Decomp's committed translations, $bad different"
  echo "   the regenerated translations, with the runtime's asmrt:"
  X "$A" portbuild.py > "$P/asm2c-portbuild.txt" 2>&1 && tail -1 "$P/asm2c-portbuild.txt" | sed 's/^/   /' || { echo "   portbuild FAILED"; tail -20 "$P/asm2c-portbuild.txt"; }
  X "$A" replay.py verify all > "$P/asm2c-verify.txt" 2>&1 || true
  grep "^verify:" "$P/asm2c-verify.txt" | sed 's/^/   /'
  X "$A" fuzzasm.py > "$P/asm2c-fuzz.txt" 2>&1 || true
  tail -1 "$P/asm2c-fuzz.txt" | sed 's/^/   /'
  ;;
toolkit)
  echo "== toolkit"
  snapshot "$S"
  [ -x "$S/build/port/uw2port" ] || X "$S" portbuild.py > /dev/null
  # each tool's output against UW2Decomp's own tool's, in the same tree
  same() {   # name, Exhume's command (tools/...), UW2Decomp's command (tools/...), files to compare too
    n=$1; a=$2; b=$3; shift 3
    X "$S" $a > "$P/tk-$n.txt" 2>&1 || true
    for f in "$@"; do [ -f "$S/$f" ] && cp "$S/$f" "$P/tk-$n.$(basename $f).exhume"; done
    ( cd "$S" && .venv/bin/python tools/$b > "$P/tk-$n.uw2decomp.txt" 2>&1 ) || true
    ok=1; cmp -s "$P/tk-$n.txt" "$P/tk-$n.uw2decomp.txt" || ok=0
    for f in "$@"; do [ -f "$S/$f" ] && { cmp -s "$S/$f" "$P/tk-$n.$(basename $f).exhume" || ok=0; }; done
    [ $ok = 1 ] && echo "   $n: the same output as UW2Decomp's ($(head -1 "$P/tk-$n.txt" | cut -c1-90))" || echo "   $n: output DIFFERS from UW2Decomp's (diff $P/tk-$n.txt $P/tk-$n.uw2decomp.txt)"
  }
  same portcheck portcheck.py portcheck.py
  same portstubs "portstubs.py --check" "portstubs.py --check"
  same widths "widths.py --diff src" "widths.py --diff src"
  same intaudit intaudit.py intaudit.py build/port/intaudit.txt
  same layoutcheck layoutcheck.py layoutcheck.py build/layout/dos.txt build/layout/host.txt
  X "$S" portbuild.py > /dev/null      # the port's objects again, which portcheck rewrote
  ;;
sound)
  echo "== sound"
  snapshot "$S"
  [ -x "$S/build/port/uw2port" ] || X "$S" portbuild.py > /dev/null
  X "$S" replay.py drivers > "$P/sound-drivers.txt" 2>&1 || true
  echo "   Exhume's ailcheck.py on the port built with the runtime, the three sessions with a music card:"
  grep "calls, " "$P/sound-drivers.txt" | sed 's/^/   /'
  # everything the cards play in a whole session (FM music, the digital effects), rendered by
  # the snapshot's port and by BASE's (built with its own tools)
  B="${BASE:-}"
  [ -z "$B" ] && [ -n "$PATCH" ] && B=$REF
  H=""
  if [ -n "$B" ]; then
    H="$OUT/snap-base"; snapshot "$H" "$(git -C "$UW" rev-parse "$B")"
    [ -x "$H/build/port/uw2port" ] || ( cd "$H" && .venv/bin/python tools/portbuild.py > "$P/sound-base-portbuild.txt" 2>&1 )
  else
    echo "   no BASE (a UW2Decomp ref to compare the WAVs with): the WAVs are only rendered"
  fi
  for s in sound soundfm; do
    for v in "$S" $H; do
      w="$OUT/wav/$(basename "$v")-$s"; rm -rf "$w"; mkdir -p "$w/home/DATA"
      cp "$v/tests/replay/$s.cfg" "$w/home/DATA/UW.CFG"
      "$v/build/port/uw2port" --data "${UW2_DIR:-$HOME/UWGOG/UW2}" --home "$w/home" --hidden --exit-on-halt \
        --exit-after 600000 --replay "$v/tests/replay/$s.rec" --audio-wav "$w/out.wav" > "$w/log" 2>&1 || true
    done
    a="$OUT/wav/$(basename "$S")-$s/out.wav"
    if [ -n "$H" ]; then
      h="$OUT/wav/$(basename "$H")-$s/out.wav"
      cmp -s "$a" "$h" && echo "   $s: the WAV of the whole session ($(wc -c < "$a" | tr -d ' ') bytes) is byte-identical to UW2Decomp $(git -C "$UW" rev-parse --short "$B")'s port's" \
        || echo "   $s: the WAV DIFFERS from UW2Decomp $(git -C "$UW" rev-parse --short "$B")'s port's"
    else
      echo "   $s: $(wc -c < "$a" | tr -d ' ') bytes"
    fi
  done
  ;;
ci)
  echo "== ci"
  snapshot "$S"     # REF's .github, with PATCH applied
  C="$OUT/ci-uw2"; rm -rf "$C"
  "$PY" "$ex/tools/citemplates.py" --config "$here/exhume.toml" "$C" > /dev/null
  n=0
  for f in workflows/accuracy.yml workflows/nightly.yml workflows/port.yml workflows/repocheck.yml actions/linux-tools/action.yml actions/uw2-assets/action.yml; do
    if cmp -s "$S/.github/$f" "$C/.github/$f"; then n=$((n+1)); else echo "   DIFFERS from UW2Decomp's: .github/$f"; fi
  done
  echo "   the templates with UW2's values: $n of 6 files byte-identical to UW2Decomp's .github"
  ( cd "$C" && actionlint .github/workflows/*.yml ) && echo "   actionlint $(actionlint -version | head -1): no findings in UW2's" || echo "   actionlint FOUND PROBLEMS in UW2's"
  G="$OUT/ci-generic"; rm -rf "$G"
  "$PY" "$ex/tools/citemplates.py" --vars "$ex/tools/templates/ci/defaults.toml" "$G" > /dev/null
  ( cd "$G" && actionlint .github/workflows/*.yml ) && echo "   actionlint: no findings in the defaults' (a project with Exhume in .exhume)" || echo "   actionlint FOUND PROBLEMS in the defaults'"
  # tools/ci-assets.sh against UW2Decomp's on a dummy bundle made here with a throwaway age key
  B="$OUT/ci-bundle"; rm -rf "$B"; mkdir -p "$B/b/game/UW2" "$B/b/tc" "$B/b/tasm"
  ( cd "$B/b" && echo fake > game/UW2/UW2.EXE && for i in 1 2 3 4; do echo "d$i" > "tc/Disk0$i.img"; done && echo t > tasm/Disk01.img \
    && echo "a dummy bundle" > MANIFEST.txt && find game tc tasm MANIFEST.txt -type f | sort | xargs shasum -a 256 > SHA256SUMS )
  age-keygen -o "$B/key.txt" 2>/dev/null
  ( cd "$B/b" && COPYFILE_DISABLE=1 tar --no-xattrs -czf - game tc tasm MANIFEST.txt SHA256SUMS ) | age -r "$(age-keygen -y "$B/key.txt")" -o "$B/b.age"
  K=$(grep AGE-SECRET "$B/key.txt")
  cp "$S/tools/ci-assets.sh" "$B/uw2-ci-assets.sh"
  UW2_ASSETS_AGE_KEY="$K" sh "$B/uw2-ci-assets.sh" "$B/b.age" "$B/out-uw2" | sed "s#$B/out-uw2#DEST#" > "$B/uw2.txt"
  AGE_KEY_ENV=UW2_ASSETS_AGE_KEY UW2_ASSETS_AGE_KEY="$K" sh "$ex/tools/ci-assets.sh" "$B/b.age" "$B/out-ex" \
    --require game/UW2/UW2.EXE --require tc/Disk01.img --require tc/Disk02.img --require tc/Disk03.img --require tc/Disk04.img \
    --require tasm/Disk01.img --export UW2_EXE=game/UW2/UW2.EXE --export UW2_DIR=game/UW2 --export TC_DISKS=tc --export TASM_DISKS=tasm \
    | sed "s#$B/out-ex#DEST#" > "$B/ex.txt"
  cmp -s "$B/uw2.txt" "$B/ex.txt" && diff -r "$B/out-uw2" "$B/out-ex" > /dev/null \
    && echo "   ci-assets.sh: the same output and tree as UW2Decomp's on a dummy bundle ($(head -1 "$B/ex.txt"))" \
    || echo "   ci-assets.sh: DIFFERS from UW2Decomp's on a dummy bundle"
  rm -rf "$B"
  ;;
*) echo "== $step: no such step"; exit 2;;
esac; done
