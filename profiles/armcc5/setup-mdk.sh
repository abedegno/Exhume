#!/bin/sh
# Unpack Arm Compiler 5.06u7 and MDK's licence mappings from your own copy of Keil MDK 5.36,
# and build the armcc-wine image. Exhume never contains Keil files.
#   usage: profiles/armcc5/setup-mdk.sh MDK536.EXE DEST
# DEST gets ARM/ARMCC and ARM/sw; point [toolchain] home at it. Needs 7z (brew install p7zip)
# and Docker.
set -e
exe=$1; dest=$2
[ -f "$exe" ] && [ -n "$dest" ] || { echo "usage: $0 MDK536.EXE DEST"; exit 2; }
here=$(cd "$(dirname "$0")" && pwd)
want=$(sed -n 's/^mdk_sha256 = "\(.*\)"/\1/p' "$here/profile.toml")
got=$(shasum -a 256 "$exe" | cut -d' ' -f1)
[ "$got" = "$want" ] || { echo "$exe: SHA-256 $got, want $want (MDK 5.36)"; exit 1; }
mkdir -p "$dest"
7z x -y -o"$dest" "$exe" 'ARM/ARMCC/*' 'ARM/sw/*' 'license_terms/*' >/dev/null
[ -f "$dest/ARM/ARMCC/bin/armcc.exe" ] && [ -f "$dest/ARM/sw/mappings/mdk_lite.elmap" ] \
  || { echo "$dest: armcc.exe or mdk_lite.elmap missing after extraction"; exit 1; }
docker build --platform linux/amd64 -t armcc-wine "$here"
echo "Arm Compiler 5 in $dest; image armcc-wine built"
