"""What a source's object depends on besides its own text: the shared headers it includes.

    headers(path, cfg)       the project's headers the source includes, `#include "name.h"`
                             resolved in [project] include and then the runtime's include
                             (portable.h; config.py's includes), recursively, each once, in
                             order of first inclusion. Names not found there (the compiler's own
                             headers) are left out: the toolchain's hash covers those.
    source_hash(path, cfg)   SHA-1 of the source's bytes, followed by the name and bytes of
                             each of those headers. A source that includes none hashes to
                             plain sha1(bytes), so hashes recorded before a project had
                             headers stay valid.

The gate (tools/gate.py), the modding build (tools/modding.py) and a link's snapshot all use
source_hash, so editing a header recompiles every source that includes it, and nothing else.

    python3 tools/srcdeps.py [--config PATH] SRC ...      prints each source's headers and hash
"""
import os, re, sys, hashlib
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config

_INC = re.compile(rb'^[ \t]*#[ \t]*include[ \t]*"([^"]+)"', re.M)


def _index(cfg):
    """{lower-case name: path} of the include directories, the project's first (DOS names are
    case-blind)."""
    out = {}
    for d in cfg.includes:
        if os.path.isdir(d):
            for n in os.listdir(d): out.setdefault(n.lower(), os.path.join(d, n))
    return out


def headers(path, cfg=None):
    cfg = cfg or config.load()
    idx = _index(cfg); seen, order = set(), []

    def walk(p):
        for m in _INC.finditer(open(p, 'rb').read()):
            name = os.path.basename(m.group(1).decode('latin1').replace('\\', '/')).lower()
            if name in seen: continue
            seen.add(name)
            h = idx.get(name)
            if not h: continue
            order.append(h); walk(h)
    walk(path)
    return order


def source_hash(path, cfg=None):
    h = hashlib.sha1(open(path, 'rb').read())
    for p in headers(path, cfg):
        h.update(b'\0' + os.path.basename(p).encode() + b'\0' + open(p, 'rb').read())
    return h.hexdigest()


if __name__ == '__main__':
    cpath, a = config.pop_config(sys.argv[1:])
    c = config.load(cpath)
    if not a: sys.exit(__doc__)
    for s in a:
        print(s, source_hash(s, c))
        for h in headers(s, c): print('   ', os.path.relpath(h, c.root))
