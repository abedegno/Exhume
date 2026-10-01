#!/bin/sh
# Unpack Turbo Assembler 2.0 from Borland's 360K disk images into DEST (default
# ~/Exhume/toolchain/borland-tc101/TASM) and check it is the expected build.
# Not redistributed with Exhume: you supply the disk images. Needs mtools and 7z.
# usage: profiles/borland-tc101/setup-tasm.sh DIR_WITH_Disk01.img [DEST]
# Then add DEST/TASM.EXE to [toolchain] stage in exhume.toml.
set -e
src=${1:?usage: setup-tasm.sh DIR_WITH_DISK_IMAGES [DEST]}
here=$(cd "$(dirname "$0")" && pwd)
dest=${2:-$here/../../toolchain/borland-tc101/TASM}
tmp=$(mktemp -d)
mcopy -n -i "$src/Disk01.img" ::TASM.ZIP "$tmp"/
mkdir -p "$dest"; 7z x -y -bso0 -o"$dest" "$tmp/TASM.ZIP"; rm -rf "$tmp"
sum=$(md5 -q "$dest/TASM.EXE" 2>/dev/null || md5sum "$dest/TASM.EXE" | cut -d' ' -f1)
[ "$sum" = b68a63d6a94672910d4149fad4c18f00 ] && echo "TASM ready in $dest: TASM.EXE is the expected 2.0 build" \
  || { echo "TASM.EXE checksum $sum is not the expected 2.0 build"; exit 1; }
