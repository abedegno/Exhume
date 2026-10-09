#!/bin/sh
# The Emscripten SDK for the web build (docs/WEB.md): emsdk cloned into EMSDK_DIR (default
# ~/emsdk) and the pinned version installed and activated. Then: . ~/emsdk/emsdk_env.sh
set -e
ver=${EMSDK_VERSION:-4.0.15}
dir=${EMSDK_DIR:-$HOME/emsdk}
[ -d "$dir" ] || git clone -q https://github.com/emscripten-core/emsdk "$dir"
cd "$dir" && git pull -q && ./emsdk install "$ver" && ./emsdk activate "$ver" >/dev/null
echo "emsdk $ver in $dir: . $dir/emsdk_env.sh"
