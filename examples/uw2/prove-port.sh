#!/bin/sh
# Prove Exhume's port tools and runtime on UW2Decomp's native port, writing nothing into the
# UW2Decomp checkout: everything happens in snapshots of its HEAD under $OUT (default
# ~/Exhume/build/uw2-port), made with git archive, with its gitignored toolchain (TC, TASM)
# copied in and its fetched or built helpers (fmtowns, .venv, node_modules, tools/emu2,
# tools/libs, tools/nuked-opl3) linked.
#
#   examples/uw2/prove-port.sh [--dos] [--keep] [STEP ...]     STEP: build verify runtime fuzz asm2c toolkit sound ci
#
# build    Exhume's gate passes on the snapshot; portbuild.py links a uw2port byte-identical to the
#          one UW2Decomp's portbuild.py links in the same tree; replay.py build gives the replay DOS
#          EXE the committed goldens were made with (same SHA-256)
# verify   replay.py verify all: every session identical to UW2Decomp's committed golden
#          (with --dos also replay.py golden all --check: each session twice in DOS, identical to
#          each other and to the golden)
# runtime  a second snapshot with runtime/include/portable.h (with portable-uw2.h's bindings),
#          runtime/replay/replay.c and rpgame.h in place of UW2Decomp's portable.h and REPLAY.C:
#          the gate passes (the DOS bytes are the same), the port verifies every session (and
#          with --dos, the replay DOS build made with Exhume's replay.c reproduces every golden)
# fuzz     fuzzasm.py's quick run with examples/uw2/port/fuzz_targets.py
# asm2c    asm2c.py --check, and regenerating every translation reproduces UW2Decomp's files
# toolkit  portcheck, portstubs --check, widths --diff, intaudit, layoutcheck: the same output
#          as UW2Decomp's tools; and the runtime's portability layer swapped into a snapshot
#          (compat.h, the stand-in headers, plat.h and the SDL3 backend, parmap, frame, vga,
#          pit, asmrt) builds a port that verifies every session
# sound    the runtime's sound library swapped in: every session verifies, and ailcheck.py on the
#          three sessions with a music card
# ci       tools/templates/ci instantiated for UW2 equals UW2Decomp's workflows and actions, and
#          actionlint passes on them
#
# REF names the UW2Decomp commit to snapshot (default HEAD); its working tree is never read,
# but for the gitignored helpers named above. PATCH, a patch for UW2Decomp (git diff, -p1), is
# applied to every snapshot: a change to UW2Decomp proved before it is committed there (the sound
# step then also compares its WAVs with an unpatched snapshot's).
#
# UW2's game code is UW2Decomp's, never Exhume's: the sound library's time-variant effects
# ([sound] extensions, src/port/sound/tvfx.c), the renderer's divide fault handlers
# (src/port/x86/divfault.c, asm_divfault) and asm2c's overrides ([asm2c] overrides,
# tools/asm2c.py) come from the snapshot.
#
# Needs: UW2Decomp at $UW2DECOMP (default ~/UW2Decomp) with TC/, TASM/, .venv (iced-x86,
# unicorn), node_modules, tools/emu2 and tools/nuked-opl3 in place; your UW2.EXE and game
# directory (UW2_EXE, UW2_DIR); SDL3 and libmt32emu (brew install sdl3 mt32emu); DOSBox-X for
# --dos; actionlint for ci.
set -e
here=$(cd "$(dirname "$0")" && pwd); ex=$(cd "$here/../.." && pwd)
UW=${UW2DECOMP:-$HOME/UW2Decomp}
OUT=${OUT:-$ex/build/uw2-port}
case "$OUT/" in "$UW"/*) echo "refusing: OUT is inside $UW"; exit 1;; esac
PY=${PY:-$ex/.venv/bin/python}
DOS=0; KEEP=0; STEPS=""
for a in "$@"; do case "$a" in --dos) DOS=1;; --keep) KEEP=1;; *) STEPS="$STEPS $a";; esac; done
[ -n "$STEPS" ] || STEPS="build verify runtime fuzz asm2c toolkit sound ci"
export EXHUME_CONFIG="$here/exhume.toml"
unset EXHUME_BUILD EXHUME_SRC EXHUME_SYMBOLS EXHUME_MATCHED EXHUME_MAP
P="$OUT/proof"; mkdir -p "$P"
# the UW2Decomp commit proved against (REF, default HEAD), named so the report says which
REF=$(git -C "$UW" rev-parse "${REF:-HEAD}")
echo "UW2Decomp $(git -C "$UW" log -1 --format='%h %s' "$REF")"

# a snapshot of UW2Decomp's HEAD at $1, with the toolchain copied (tcc.mjs and dosrun copy their
# stage with cpSync, which copies a symbolic link as a link) and the helpers linked
snapshot() {   # DIR [nopatch]
  d=$1
  if [ -d "$d" ] && [ "$KEEP" = 1 ]; then return; fi
  rm -rf "$d"; mkdir -p "$d"
  git -C "$UW" archive "$REF" | tar -x -C "$d"
  if [ -n "$PATCH" ] && [ "$2" != nopatch ]; then patch -p1 -s -d "$d" < "$PATCH" || { echo "PATCH does not apply to $d"; exit 1; }; fi
  cp -R "$UW/TC" "$d/TC"; cp -R "$UW/TASM" "$d/TASM"
  for x in fmtowns .venv node_modules; do [ -e "$UW/$x" ] && [ ! -e "$d/$x" ] && ln -s "$UW/$x" "$d/$x"; done
  for x in emu2 libs nuked-opl3; do [ -e "$UW/tools/$x" ] && [ ! -e "$d/tools/$x" ] && ln -s "$UW/tools/$x" "$d/tools/$x"; done
  return 0
}
X() { d=$1; shift; ( cd "$d" && UW2DECOMP="$d" EXHUME_TC="$d/TC" EXHUME_TASM="$d/TASM" "$PY" "$ex/tools/$@" ); }
S="$OUT/snap"

# Exhume's runtime with UW2's bindings in place of UW2Decomp's own files, in snapshot $1:
# replay (portable.h, REPLAY.C), port (the portability layer, platform, memory, VGA, records,
# crash handler, Borland's library, stubs' body, asmrt), sound (the AIL 2 library and the PIT).
# What is UW2's own code stays the snapshot's: x86/divfault.c, sound/tvfx.c.
install_runtime() {
  rd=$1; shift; RT="$ex/runtime"; UB="$here/port"; pp="$rd/src/port"
  for part in "$@"; do case $part in
  replay)
    { cat "$UB/portable-uw2.h"; echo; cat "$RT/include/portable.h"; } > "$rd/src/include/portable.h"
    cp "$UB/rpgame.h" "$rd/src/include/rpgame.h"; cp "$RT/replay/replay.c" "$rd/src/replay/REPLAY.C" ;;
  port)
    { cat "$UB/compat-uw2.h"; echo; cat "$RT/port/compat.h"; } > "$pp/compat.h"
    { cat "$UB/plat-uw2.h"; echo; cat "$RT/port/platform/plat.h"; } > "$pp/platform/plat.h"
    cp "$RT/port/port.h" "$UB/portgame.h" "$pp/"
    cp "$RT"/port/include/*.h "$pp/include/"
    cp "$RT/port/platform/files.c" "$RT/port/platform/png.c" "$pp/platform/"; cp "$RT/port/platform/sdl3/plat_sdl3.c" "$pp/platform/sdl3/"
    cp "$RT/port/mem/parmap.c" "$RT/port/mem/frame.c" "$pp/mem/"; cp "$UB/nulls-uw2.c" "$pp/mem/nulls.c"
    cp "$RT/port/gfx/vga.c" "$pp/gfx/"; cp "$RT/port/sys/records.c" "$RT/port/sys/crash.c" "$RT/port/sys/borland.c" "$RT/port/sys/blackbox.c" "$pp/sys/"
    cp "$RT/port/stubs/stub.c" "$RT/port/stubs/stub.h" "$pp/stubs/"
    cp "$RT/port/x86/asmrt.h" "$RT/port/x86/asmrt.c" "$UB/asmgame.h" "$pp/x86/"
    [ -f "$pp/x86/divfault.c" ] || { echo "   UW2Decomp has no src/port/x86/divfault.c (asm_divfault): apply the patch (PATCH)"; exit 1; } ;;
  sound)
    for f in "$RT"/port/sound/*.c "$RT"/port/sound/*.h; do cp "$f" "$pp/sound/"; done
    cp "$UB/ailgame.h" "$pp/sound/ailgame.h"; cp "$RT/port/sys/pit.c" "$pp/sys/"
    [ -f "$rd/src/port/sound/tvfx.c" ] || { echo "   UW2Decomp has no src/port/sound/tvfx.c ([sound] extensions): apply the patch (PATCH)"; exit 1; } ;;
  esac; done
}

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
  echo "== runtime: Exhume's portable.h and replay.c in place of UW2Decomp's"
  R="$OUT/snap-runtime"; snapshot "$R"; install_runtime "$R" replay
  X "$R" gate.py check > "$P/runtime-gate.txt" 2>&1 && echo "   gate: $(tail -1 "$P/runtime-gate.txt")" || { echo "   gate FAILED ($P/runtime-gate.txt)"; exit 1; }
  X "$R" portbuild.py > "$P/runtime-portbuild.txt" && tail -1 "$P/runtime-portbuild.txt" | sed 's/^/   /'
  X "$R" replay.py verify all > "$P/runtime-verify.txt" 2>&1 || true
  grep "^[a-z]" "$P/runtime-verify.txt" | grep -v "^replay build" | sed 's/^/   /'
  if [ "$DOS" = 1 ]; then
    X "$R" replay.py golden all --check > "$P/runtime-golden-check.txt" 2>&1 || true
    sed -n '/^== golden/,$p' "$P/runtime-golden-check.txt" | sed 's/^/   /'
  fi
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
  echo "   the runtime's asmrt.h and asmrt.c with asmgame.h in place of UW2Decomp's (its own divfault.c kept):"
  [ -f "$A/src/port/x86/divfault.c" ] || { echo "   UW2Decomp has no src/port/x86/divfault.c (asm_divfault): apply the patch (PATCH)"; exit 1; }
  cp "$ex/runtime/port/x86/asmrt.h" "$ex/runtime/port/x86/asmrt.c" "$here/port/asmgame.h" "$A/src/port/x86/"
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
  echo "   the runtime's portability layer in place of UW2Decomp's (compat.h, the stand-in headers, port.h, plat.h, files.c, png.c, the SDL3 backend, parmap, frame, vga, records, crash, borland, blackbox, the stubs' body, asmrt):"
  K="$OUT/snap-toolkit"; snapshot "$K"; install_runtime "$K" replay port
  X "$K" gate.py check > "$P/tk-gate.txt" 2>&1 && echo "   gate: $(tail -1 "$P/tk-gate.txt")" || echo "   gate FAILED ($P/tk-gate.txt)"
  X "$K" portbuild.py > "$P/tk-portbuild.txt" 2>&1 && tail -1 "$P/tk-portbuild.txt" | sed 's/^/   /' || { echo "   portbuild FAILED"; tail -20 "$P/tk-portbuild.txt"; }
  X "$K" replay.py verify all > "$P/tk-verify.txt" 2>&1 || true
  grep "^verify:" "$P/tk-verify.txt" | sed 's/^/   /'
  X "$K" fuzzasm.py > "$P/tk-fuzz.txt" 2>&1 || true
  tail -1 "$P/tk-fuzz.txt" | sed 's/^/   /'
  X "$K" portstubs.py --check 2>&1 | sed 's/^/   portstubs: /'
  X "$K" portcheck.py 2>&1 | sed -n 2p | sed 's/^/   portcheck: /'
  ;;
sound)
  echo "== sound"
  snapshot "$S"
  [ -x "$S/build/port/uw2port" ] || X "$S" portbuild.py > /dev/null
  X "$S" replay.py drivers > "$P/sound-drivers.txt" 2>&1 || true
  echo "   Exhume's ailcheck.py on UW2Decomp's port, the three sessions with a music card:"
  grep "calls, " "$P/sound-drivers.txt" | sed 's/^/   /'
  echo "   the runtime's sound library and PIT, with the whole runtime, in place of UW2Decomp's (its tvfx.c through yamaha.h's hook):"
  N="$OUT/snap-sound"; snapshot "$N"; install_runtime "$N" replay port sound
  # the runtime's audio.c names its third-party macros AUDIO_HAVE_OPL and AUDIO_HAVE_MT32EMU
  sed 's/UW2_HAVE_OPL/AUDIO_HAVE_OPL/; s/UW2_HAVE_MT32EMU/AUDIO_HAVE_MT32EMU/' "$here/exhume.toml" > "$P/sound.toml"
  XS() { ( cd "$N" && UW2DECOMP="$N" EXHUME_TC="$N/TC" EXHUME_TASM="$N/TASM" EXHUME_CONFIG="$P/sound.toml" "$PY" "$ex/tools/$@" ); }
  XS gate.py check > "$P/sound-gate.txt" 2>&1 && echo "   gate: $(tail -1 "$P/sound-gate.txt")" || echo "   gate FAILED ($P/sound-gate.txt)"
  XS portbuild.py > "$P/sound-portbuild.txt" 2>&1 && tail -2 "$P/sound-portbuild.txt" | sed 's/^/   /' || { echo "   portbuild FAILED"; tail -20 "$P/sound-portbuild.txt"; }
  XS replay.py verify all > "$P/sound-verify.txt" 2>&1 || true
  grep "^verify:" "$P/sound-verify.txt" | sed 's/^/   /'
  grep "^sound\|^soundfm\|^soundmt" "$P/sound-verify.txt" | sed 's/^/   /'
  XS replay.py drivers > "$P/sound-drivers2.txt" 2>&1 || true
  grep "calls, " "$P/sound-drivers2.txt" | sed 's/^/   ailcheck: /'
  XS fuzzasm.py > "$P/sound-fuzz.txt" 2>&1 || true
  tail -1 "$P/sound-fuzz.txt" | sed 's/^/   /'
  # everything the cards play in a whole session (FM music, the digital effects), rendered by
  # UW2Decomp's port and by the one with Exhume's sound library: the same samples; with PATCH,
  # by an unpatched snapshot's port too, UW2Decomp's port as it is committed
  H=""
  if [ -n "$PATCH" ]; then
    H="$OUT/snap-head"; snapshot "$H" nopatch
    [ -x "$H/build/port/uw2port" ] || ( cd "$H" && .venv/bin/python tools/portbuild.py > "$P/sound-head-portbuild.txt" 2>&1 )
  fi
  for s in sound soundfm; do
    for v in "$S" "$N" $H; do
      w="$OUT/wav/$(basename "$v")-$s"; rm -rf "$w"; mkdir -p "$w/home/DATA"
      cp "$v/tests/replay/$s.cfg" "$w/home/DATA/UW.CFG"
      "$v/build/port/uw2port" --data "${UW2_DIR:-$HOME/UWGOG/UW2}" --home "$w/home" --hidden --exit-on-halt \
        --exit-after 600000 --replay "$v/tests/replay/$s.rec" --audio-wav "$w/out.wav" > "$w/log" 2>&1 || true
    done
    a="$OUT/wav/$(basename "$S")-$s/out.wav"; b="$OUT/wav/$(basename "$N")-$s/out.wav"
    cmp -s "$a" "$b" && echo "   $s: the WAV of the whole session ($(wc -c < "$a" | tr -d ' ') bytes) is byte-identical to UW2Decomp's port's" \
      || echo "   $s: the WAV DIFFERS from UW2Decomp's port's"
    if [ -n "$H" ]; then
      h="$OUT/wav/$(basename "$H")-$s/out.wav"
      cmp -s "$h" "$b" && echo "   $s: and to the unpatched UW2Decomp $(git -C "$UW" rev-parse --short "$REF")'s port's" \
        || echo "   $s: the WAV DIFFERS from the unpatched UW2Decomp's port's"
    fi
  done
  ;;
ci)
  echo "== ci"
  C="$OUT/ci-uw2"; rm -rf "$C"
  "$PY" "$ex/tools/citemplates.py" --config "$here/exhume.toml" "$C" > /dev/null
  n=0
  for f in workflows/accuracy.yml workflows/nightly.yml workflows/port.yml workflows/repocheck.yml actions/linux-tools/action.yml actions/uw2-assets/action.yml; do
    if git -C "$UW" show "$REF:.github/$f" | cmp -s - "$C/.github/$f"; then n=$((n+1)); else echo "   DIFFERS from UW2Decomp's: .github/$f"; fi
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
  git -C "$UW" show "$REF:tools/ci-assets.sh" > "$B/uw2-ci-assets.sh"
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
