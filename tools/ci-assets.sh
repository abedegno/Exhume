#!/bin/sh
# CI only (docs/ci-bundle.md): decrypt a project's private asset bundle (the owner's own copy of
# the program and the toolchain's disk images, from a private repository) into DEST, check every
# file against the bundle's SHA256SUMS, and point the tools at it. From UW2Decomp's
# tools/ci-assets.sh.
#
#   ASSETS_AGE_KEY=... sh tools/ci-assets.sh BUNDLE.tar.gz.age DEST [--require PATH ...] [--export NAME=PATH ...]
#
# The age secret key comes from the environment (ASSETS_AGE_KEY, or the variable AGE_KEY_ENV
# names) and goes to age on its standard input, never to a file. Nothing in the bundle is listed
# or printed: the checks say only how many files passed. Each --require PATH (relative to DEST)
# must be in the bundle besides SHA256SUMS. On success it prints each --export NAME=PATH as
# NAME=DEST/PATH (by default TC_DISKS=tc, TASM_DISKS=tasm, GAME_DIR=game), and appends them to
# $GITHUB_ENV when that is set, for the later steps. DEST is removed by the workflow's clean-up
# step; nothing under it may be cached or uploaded.
#
# Making a bundle (on the owner's machine; docs/ci-bundle.md has rotation): put game/, tc/,
# tasm/ and a MANIFEST.txt in an empty directory, and in it run
#   find game tc tasm MANIFEST.txt -type f | sort | xargs shasum -a 256 > SHA256SUMS
#   COPYFILE_DISABLE=1 tar --no-xattrs -czf - game tc tasm MANIFEST.txt SHA256SUMS | age -r RECIPIENT -o ci-assets.tar.gz.age
set -eu
bundle=${1:?usage: ci-assets.sh BUNDLE.tar.gz.age DEST [--require PATH ...] [--export NAME=PATH ...]}
dest=${2:?usage: ci-assets.sh BUNDLE.tar.gz.age DEST [--require PATH ...] [--export NAME=PATH ...]}
shift 2
keyvar=${AGE_KEY_ENV:-ASSETS_AGE_KEY}
eval "key=\${$keyvar:-}"
[ -n "$key" ] || { echo "ci-assets: $keyvar is not set"; exit 1; }
require=""; exports=""
while [ $# -gt 0 ]; do
  case $1 in
    --require) require="$require $2"; shift 2 ;;
    --export) exports="$exports $2"; shift 2 ;;
    *) echo "ci-assets: unknown argument $1"; exit 2 ;;
  esac
done
[ -n "$exports" ] || exports="TC_DISKS=tc TASM_DISKS=tasm GAME_DIR=game"
umask 077
rm -rf "$dest"; mkdir -p "$dest"; dest=$(cd "$dest" && pwd)
# GNU tar warns about the macOS extended attributes a bundle made on a Mac carries
quiet=; tar --version 2>/dev/null | grep -q GNU && quiet=--warning=no-unknown-keyword
printf '%s\n' "$key" | age -d -i - "$bundle" | tar $quiet -xzf - -C "$dest"
for f in SHA256SUMS $require; do
  [ -f "$dest/$f" ] || { echo "ci-assets: the bundle has no $f"; exit 1; }
done
n=$(grep -c . "$dest/SHA256SUMS")
if command -v sha256sum >/dev/null 2>&1; then sum="sha256sum"; else sum="shasum -a 256"; fi
( cd "$dest" && $sum -c --quiet SHA256SUMS >/dev/null 2>&1 ) || { echo "ci-assets: SHA256SUMS does not verify"; exit 1; }
echo "ci-assets: $n files verified against SHA256SUMS"
vars=""
for e in $exports; do vars="$vars${vars:+
}${e%%=*}=$dest/${e#*=}"; done
echo "$vars"
[ -z "${GITHUB_ENV:-}" ] || echo "$vars" >> "$GITHUB_ENV"
