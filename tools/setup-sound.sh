#!/bin/sh
# Fetch Nuked OPL3 (github.com/nukeykt/Nuked-OPL3, LGPL-2.1), the OPL emulator of
# runtime/port/sound/audio.c, into DIR (default ./tools/nuked-opl3, the project's; ignored by
# git) at a pinned commit, and check the two files by SHA-256. A project names DIR in
# exhume.toml as a [[port.vendor]] (UW2: tools/nuked-opl3, define AUDIO_HAVE_OPL, the
# runtime's audio.c's), and tools/portbuild.py compiles opl3.c from there when it
# is present; without it the port builds with no OPL chip and plays no FM music. Nothing of it
# is ever committed (docs/third-party.md): the LGPL is met by source distribution and a
# relinkable build, and fetching at setup keeps the repository free of it. From UW2Decomp's
# tools/setup-sound.sh. Needs curl. Safe to run again.
#
#   sh tools/setup-sound.sh [DIR]
#
# The MT-32 backend uses libmt32emu (munt, LGPL-2.1-or-later): from Homebrew on macOS (brew
# install mt32emu), built by tools/setup-libs.sh on Linux; found through pkg-config when the
# port is built. This script only says whether it is there.
set -e
dir=${1:-tools/nuked-opl3}
commit=765ec962e473aeb767e4cba74ffdc8f588ffbfe8
sum_c=59eb873fdb6d52bc7977a0fcbb97c1bae03dd71cc88e3acddbcc01b7121bfe03
sum_h=a84266b8d71a4929f15f573afbe407fd24310b77757d855835dacabf68763679
sha() { if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi | cut -d' ' -f1; }
ok() { [ -f "$dir/opl3.c" ] && [ -f "$dir/opl3.h" ] && [ "$(sha "$dir/opl3.c")" = "$sum_c" ] && [ "$(sha "$dir/opl3.h")" = "$sum_h" ]; }
if ! ok; then
  mkdir -p "$dir"
  for f in opl3.c opl3.h LICENSE; do
    curl -fsSL "https://raw.githubusercontent.com/nukeykt/Nuked-OPL3/$commit/$f" -o "$dir/$f.tmp" && mv "$dir/$f.tmp" "$dir/$f"
  done
  ok || { echo "nuked-opl3: the files fetched do not match their pinned SHA-256"; exit 1; }
fi
echo "nuked-opl3: $dir (nukeykt/Nuked-OPL3 at ${commit%"${commit#???????}"}, LGPL-2.1)"
if pkg-config --exists mt32emu 2>/dev/null; then
  echo "mt32emu: $(pkg-config --modversion mt32emu) (LGPL-2.1-or-later), the MT-32 backend is built"
else
  echo "mt32emu: not found (brew install mt32emu, or tools/setup-libs.sh, for the MT-32 and CM-32L backend)"
fi
