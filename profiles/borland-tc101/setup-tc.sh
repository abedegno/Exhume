#!/bin/sh
# Unpack Turbo C++ 1.01 from Borland's four 720K disk images into DEST (default
# ~/Exhume/toolchain/borland-tc101/TC) and check it is the expected build.
# Borland released Turbo C++ 1.01 free of charge through its "Antique Software" programme;
# Exhume never contains it: you supply the disk images. Needs mtools (mcopy) and 7z:
#   brew install mtools p7zip        (or your system's packages)
# usage: profiles/borland-tc101/setup-tc.sh DIR_WITH_Disk01.img..Disk04.img [DEST]
# Then point [toolchain] home and stage in exhume.toml at DEST.
set -e
src=${1:?usage: setup-tc.sh DIR_WITH_DISK_IMAGES [DEST]}
here=$(cd "$(dirname "$0")" && pwd)
dest=${2:-$here/../../toolchain/borland-tc101/TC}
tmp=$(mktemp -d)
for d in "$src"/Disk0[1-4].img; do mcopy -n -i "$d" '::*.ZIP' "$tmp"/; done
mkdir -p "$dest"
# the compiler, linker and librarian, headers, startup code (C0.ASM), the libraries; XLIB.ZIP
# holds OVERLAY.LIB (the VROOMM overlay manager) and TLIB
for z in TCC BIN1 BIN2 INCLUDE STARTUP LLIB MLIB SLIB CLIB HLIB XLIB; do
  [ -f "$tmp/$z.ZIP" ] && 7z x -y -bso0 -o"$dest" "$tmp/$z.ZIP"
done
rm -rf "$tmp"
sum=$(md5 -q "$dest/TCC.EXE" 2>/dev/null || md5sum "$dest/TCC.EXE" | cut -d' ' -f1)
[ "$sum" = db4c0704f7091b8d875be038bee2bf1e ] && echo "Turbo C++ ready in $dest: TCC.EXE is the expected 1.01 build" \
  || { echo "TCC.EXE checksum $sum is not the expected 1.01 build"; exit 1; }
