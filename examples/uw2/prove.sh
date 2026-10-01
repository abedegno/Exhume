#!/bin/sh
# Prove Exhume reproduces UW2Decomp's results with its own tools, writing nothing into the
# UW2Decomp checkout: every output goes to $OUT (default ~/Exhume/build/uw2).
#
#   examples/uw2/prove.sh [--map]
#
# 1. build every source (build.py --all) and compare each object with UW2Decomp's own build
#    (segments, data, publics, externs, fixups; the dependency timestamps differ);
# 2. match.py with a fresh build of one overlay C file, one resident C file and one assembly
#    module, compared line for line with UW2Decomp's match.py on its own objects;
# 3. verify.py on every source, compared line for line with UW2Decomp's verify.py;
# 4. symbols.tsv rebuilt from scratch (rebuild-symbols.py), compared with UW2Decomp's, and
#    merge.py's gates run on one file;
# 5. the reference link (examples/uw2/link.py) and exediff, compared with UW2Decomp's
#    linked EXE when there is one;
# 6. with --map, the map pipeline into a copy of the map (the files whose inputs are only the
#    EXE, the listing and the sibling must come out identical).
# Needs: UW2Decomp at $UW2DECOMP (default ~/UW2Decomp) with its build/ populated for the
# comparisons, the toolchain and UW2.EXE as in examples/uw2/exhume.toml.
set -e
here=$(cd "$(dirname "$0")" && pwd); ex=$(cd "$here/../.." && pwd)
UW=${UW2DECOMP:-$HOME/UW2Decomp}
OUT=${OUT:-$ex/build/uw2}
case "$OUT/" in "$UW"/*) echo "refusing: OUT is inside $UW"; exit 1;; esac
export EXHUME_CONFIG="$here/exhume.toml" EXHUME_BUILD="$OUT" EXHUME_SYMBOLS="$OUT/symbols.tsv" EXHUME_MAP="$OUT/map" EXHUME_MATCHED="$OUT/matched.txt"
PY=${PY:-$ex/.venv/bin/python}
for k in build symbols matched map queue; do     # every path a tool may write
  v=$($PY "$ex/tools/config.py" $k)
  case "$v/" in "$UW"/*) echo "refusing: $k resolves inside $UW ($v)"; exit 1;; esac
done
mkdir -p "$OUT" "$OUT/proof"; P="$OUT/proof"
cp "$UW/symbols.tsv" "$OUT/symbols.tsv"; cp "$UW/matched.txt" "$OUT/matched.txt"
cd "$UW"     # sources are named relative to the project, as agents name them

echo "== 1. build everything"
start=$(date +%s); $PY "$ex/tools/build.py" --all > "$P/build.txt"; tail -1 "$P/build.txt"
echo "   $(( $(date +%s) - start )) s"
$PY - "$OUT" "$UW/build" <<'EOF'
import sys, os, glob
sys.path.insert(0, os.path.join(os.environ['EXHUME_CONFIG'].rsplit('/examples/', 1)[0], 'tools'))
from fixups import fixups
key = lambda o: (o['segs'], {k: bytes(v) for k, v in o['data'].items()}, o['pubs'], o['ext'], o['fixups'])
same = diff = 0
for p in sorted(glob.glob(os.path.join(sys.argv[1], '*', '*.OBJ'))):
    stem = os.path.basename(p)[:-4]; q = os.path.join(sys.argv[2], stem, stem + '.OBJ')
    if not os.path.exists(q): continue
    if key(fixups(open(p, 'rb').read())) == key(fixups(open(q, 'rb').read())): same += 1
    else: diff += 1; print('   object differs:', stem)
print(f'   objects equal to UW2Decomp\'s: {same}, different: {diff}')
EOF

echo "== 2. match, fresh builds"
for f in src/PLAYER.C src/SEG012.C src/SEG022.ASM; do
  $PY "$ex/tools/match.py" $f > "$P/match-$(basename $f).txt"
  tail -1 "$P/match-$(basename $f).txt" | sed 's/^/   /'
  if [ -x "$UW/.venv/bin/python" ]; then
    "$UW/.venv/bin/python" tools/match.py $f --no-build > "$P/match-$(basename $f).uw2decomp.txt"
    cmp -s "$P/match-$(basename $f).txt" "$P/match-$(basename $f).uw2decomp.txt" && echo "   same report as UW2Decomp's match.py" || echo "   REPORT DIFFERS from UW2Decomp's match.py"
  fi
done

echo "== 3. verify every source"
ok=0; bad=0; same=0; differ=0
for f in $(grep -l "/\* target:" src/*.C src/*.ASM); do
  b=$(basename $f)
  if $PY "$ex/tools/verify.py" $f > "$P/verify-$b.txt"; then ok=$((ok+1)); else bad=$((bad+1)); echo "   problems: $f"; fi
  if [ -x "$UW/.venv/bin/python" ]; then
    "$UW/.venv/bin/python" tools/verify.py $f > "$P/verify-$b.uw2decomp.txt" || true
    if cmp -s "$P/verify-$b.txt" "$P/verify-$b.uw2decomp.txt"; then same=$((same+1)); else differ=$((differ+1)); echo "   output differs: $f"; fi
  fi
done
echo "   verified: $ok, with problems: $bad; output equal to UW2Decomp's verify.py: $same, different: $differ"

echo "== 4. symbols.tsv from scratch"
$PY "$ex/tools/rebuild-symbols.py" | sed 's/^/   /'
diff "$OUT/symbols.tsv" "$UW/symbols.tsv" | sed 's/^/   /' || true
$PY "$ex/tools/merge.py" src/PLAYER.C | sed 's/^/   merge: /'

echo "== 5. link"
start=$(date +%s)
$PY "$ex/examples/uw2/link.py" > "$P/link.txt" 2>&1 || true
echo "   $(( $(date +%s) - start )) s"; grep -v "^dos-mcp\|^done" "$P/link.txt" | tail -6 | sed 's/^/   /'
[ -f "$UW/build/LINK/out/UW2.EXE" ] && { cmp -s "$OUT/LINK/out/UW2.EXE" "$UW/build/LINK/out/UW2.EXE" && echo "   byte-identical to UW2Decomp's linked EXE" || echo "   DIFFERS from UW2Decomp's linked EXE"; }

if [ "$1" = "--map" ]; then
  echo "== 6. map pipeline"
  rm -rf "$OUT/map"; mkdir -p "$OUT/map"; cp "$UW"/map/pairs_*.tsv "$OUT/map/"
  for t in doslist locate callgraphs anchors align files; do $PY "$ex/tools/$t.py" > "$P/map-$t.txt" 2>&1; done
  for f in dos_procs dos_code segments procs dos_calls fm_calls; do
    cmp -s "$OUT/map/$f.tsv" "$UW/map/$f.tsv" && echo "   same $f.tsv" || echo "   DIFF $f.tsv"
  done
  echo "   (anchors, functions, files depend on what is matched now; compare them with UW2Decomp's tools run on the same inputs)"
fi
