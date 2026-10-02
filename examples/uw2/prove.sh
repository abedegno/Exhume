#!/bin/sh
# Prove Exhume reproduces UW2Decomp's results with its own tools, writing nothing into the
# UW2Decomp checkout: every output goes to $OUT (default ~/Exhume/build/uw2).
#
#   examples/uw2/prove.sh [--map] [--no-boot]
#
# 1. build every source (build.py --all) and compare each object with UW2Decomp's own build
#    (segments, data, publics, externs, fixups; the dependency timestamps differ);
# 2. match.py with a fresh build of one overlay C file, one resident C file and one assembly
#    module, compared line for line with UW2Decomp's match.py on its own objects;
# 3. verify.py on every source, compared line for line with UW2Decomp's verify.py;
# 4. symbols.tsv rebuilt from scratch (rebuild-symbols.py), compared with UW2Decomp's, and
#    merge.py's gates run on one file;
# 5. the reference link (examples/uw2/link.py) and exediff, compared with UW2Decomp's
#    linked EXE when there is one, and the snapshot it leaves for the modding build compared
#    with UW2Decomp's;
# 6. tools/addrscan.py, compared with UW2Decomp's addrscan.py on the same objects;
# 7. the modding build (link.py --mod) with no source changed, which must equal the exact link;
# 8. the modding build with size-changing edits made in a copy of src/ (never in UW2Decomp):
#    a longer string and a new line of code in an overlay (OVR101, the attributes panel), and
#    new initialised data and code in a resident file (SEG039), booted into the 3D view with
#    tools/rungame.mjs, which looks for the marker the new code changes in memory (skip the
#    boot with --no-boot);
# 9. with --map, the map pipeline into a copy of the map (the files whose inputs are only the
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
# a segment's source, relative to the project (sources live in subsystem directories and are
# found by their /* target: */ line, so this survives renames)
seg() { $PY "$ex/tools/sources.py" "$1" | head -1 | cut -f3; }

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
for f in $(seg ovr154) $(seg seg012) $(seg seg022); do
  $PY "$ex/tools/match.py" $f > "$P/match-$(basename $f).txt"
  tail -1 "$P/match-$(basename $f).txt" | sed 's/^/   /'
  if [ -x "$UW/.venv/bin/python" ]; then
    "$UW/.venv/bin/python" tools/match.py $f --no-build > "$P/match-$(basename $f).uw2decomp.txt"
    cmp -s "$P/match-$(basename $f).txt" "$P/match-$(basename $f).uw2decomp.txt" && echo "   same report as UW2Decomp's match.py" || echo "   REPORT DIFFERS from UW2Decomp's match.py"
  fi
done

echo "== 3. verify every source"
ok=0; bad=0; same=0; differ=0
for f in $($PY "$ex/tools/sources.py" | awk -F'\t' '$2 != "-" {print $3}'); do
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
$PY "$ex/tools/merge.py" $(seg ovr154) | sed 's/^/   merge: /'

echo "== 5. link"
start=$(date +%s)
$PY "$ex/examples/uw2/link.py" > "$P/link.txt" 2>&1 || true
echo "   $(( $(date +%s) - start )) s"; grep -v "^dos-mcp\|^done" "$P/link.txt" | tail -6 | sed 's/^/   /'
[ -f "$UW/build/LINK/out/UW2.EXE" ] && { cmp -s "$OUT/LINK/out/UW2.EXE" "$UW/build/LINK/out/UW2.EXE" && echo "   byte-identical to UW2Decomp's linked EXE" || echo "   DIFFERS from UW2Decomp's linked EXE"; }
if [ ! -f "$OUT/LINK/base/layout.json" ]; then echo "   no snapshot for the modding build: some object did not verify"
elif [ -f "$UW/build/LINK/base/layout.json" ]; then
  cmp -s "$OUT/LINK/base/layout.json" "$UW/build/LINK/base/layout.json" && echo "   snapshot (LINK/base/layout.json) equal to UW2Decomp's" || echo "   snapshot DIFFERS from UW2Decomp's"
fi

echo "== 6. addrscan"
$PY "$ex/tools/addrscan.py" > "$P/addrscan.txt" 2> "$P/addrscan.err"; tail -1 "$P/addrscan.err" | sed 's/^/   /'
if [ -x "$UW/.venv/bin/python" ] && [ -f "$UW/tools/addrscan.py" ]; then
  PYTHONDONTWRITEBYTECODE=1 "$UW/.venv/bin/python" tools/addrscan.py > "$P/addrscan.uw2decomp.txt" 2> "$P/addrscan.uw2decomp.err"
  cmp -s "$P/addrscan.txt" "$P/addrscan.uw2decomp.txt" && echo "   same report as UW2Decomp's addrscan.py" || echo "   REPORT DIFFERS from UW2Decomp's addrscan.py"
fi

echo "== 7. modding build, no source changed"
$PY "$ex/examples/uw2/link.py" --mod > "$P/modlink.txt" 2>&1 || true
grep "^changed sources\|^modding build\|near code offsets" "$P/modlink.txt" | sed 's/^/   /'
cmp -s "$OUT/MODLINK/out/UW2.EXE" "$OUT/LINK/out/UW2.EXE" && echo "   byte-identical to the exact link" || echo "   DIFFERS from the exact link"

echo "== 8. modding build, sources changed in size (in a copy of src/)"
rm -rf "$OUT/modsrc"; cp -R "$UW/src" "$OUT/modsrc"
$PY - "$OUT/modsrc" "$(seg ovr101 | sed 's#^src/##')" "$(seg seg039 | sed 's#^src/##')" <<'EOF'
import sys, os
d, OVR101, SEG039 = sys.argv[1:4]     # the files of ovr101 (character creation) and seg039 (strings)
def edit(name, old, new):
    p = os.path.join(d, name); s = open(p, encoding='latin1').read()
    if s.count(old) != 1: sys.exit(f'{name}: the text to change is not there once: {old!r}')
    open(p, 'w', encoding='latin1', newline='').write(s.replace(old, new))
# an overlay: a longer string, and a new line (two more calls and a sum) on the attributes panel
edit(OVR101, 'string_to_screen("Str:", 0x5D, 0x96);', 'string_to_screen("Strength:", 0x5D, 0x96);')
edit(OVR101, '    string_to_screen("Vit:", 0x5D, 0x60);\n    string_to_screen(buf, 0x8C - string_width(buf), 0x60);\n}',
     '    string_to_screen("Vit:", 0x5D, 0x60);\n    string_to_screen(buf, 0x8C - string_width(buf), 0x60);\n'
     '    itoa(playerdat->attr[0] + playerdat->attr[1], buf, 10);\n    string_to_screen("S+D:", 0x5D, 0x52);\n'
     '    string_to_screen(buf, 0x8C - string_width(buf), 0x52);\n}')
# a resident file: new initialised data, and code at start-up that changes it (EXHUME-0 to
# EXHUME-1), which moves every resident segment after it, the far data and all of DGROUP's
# _DATA after SEG039's
edit(SEG039, 'char aRb_4[] = "rb";\n', 'char aRb_4[] = "rb";\nchar exhume_marker[] = "EXHUME-0 resident code and data grew";\n')
edit(SEG039, 'unsigned char far init_strings(void)\n{\n    int i, j;\n',
     'unsigned char far init_strings(void)\n{\n    int i, j;\n'
     '    for (j = 0; exhume_marker[j] != \'-\'; j++) ;\n    exhume_marker[j + 1] = exhume_marker[j + 1] + 1;\n')
print(f'   edited {OVR101} (ovr101) and {SEG039} (seg039) in', d)
EOF
EXHUME_SRC="$OUT/modsrc" $PY "$ex/examples/uw2/link.py" --mod > "$P/modlink-edit.txt" 2>&1 || true
grep "^changed sources\|^modding build\|failed" "$P/modlink-edit.txt" | sed 's/^/   /'
cp "$OUT/MODLINK/out/UW2.EXE" "$P/UW2-mod.EXE" 2>/dev/null || true
if [ "$1" != "--no-boot" ] && [ "$2" != "--no-boot" ]; then
  # title and introduction to the main menu, Create Character, the attributes (OVR101's
  # panel, "Strength:" and "S+D:"), the rest of character creation, a name, and into the 3D view
  node "$ex/tools/rungame.mjs" --data "${UW2_DATA:-$HOME/UWGOG/UW2}" --as UW2.EXE --skip SAVE0.pristine "$P/UW2-mod.EXE" "$P/mod-" \
    w:14000 k:Escape w:3000 k:Escape w:3000 s:menu k:Enter w:3000 k:Enter w:2000 k:Enter w:2000 k:Enter w:2000 s:stats \
    k:Enter w:1500 k:Enter w:1500 k:Enter w:1500 k:Enter w:1500 k:Enter w:1500 k:Enter w:1500 k:Enter w:1500 k:Enter w:1500 k:Enter w:1500 \
    k:a,b,c w:500 k:Enter w:2000 k:Enter w:5000 k:Escape w:8000 k:Escape w:8000 s:game m:EXHUME-1 > "$P/modboot.txt" 2>&1 \
    && echo "   booted; the marker EXHUME-1 is in memory" || echo "   the marker EXHUME-1 was NOT found in memory"
  grep "^memory\|^shot" "$P/modboot.txt" | sed 's/^/   /'
  echo "   read the screenshots: mod-stats.png should show Strength: and S+D:, mod-game.png the 3D view"
fi

if [ "$1" = "--map" ]; then
  echo "== 9. map pipeline"
  rm -rf "$OUT/map"; mkdir -p "$OUT/map"; cp "$UW"/map/pairs_*.tsv "$OUT/map/"
  for t in doslist locate callgraphs anchors align files; do $PY "$ex/tools/$t.py" > "$P/map-$t.txt" 2>&1; done
  for f in dos_procs dos_code segments procs dos_calls fm_calls; do
    cmp -s "$OUT/map/$f.tsv" "$UW/map/$f.tsv" && echo "   same $f.tsv" || echo "   DIFF $f.tsv"
  done
  echo "   (anchors, functions, files depend on what is matched now; compare them with UW2Decomp's tools run on the same inputs)"
fi
