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
from pathlib import Path

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
        for m in _INC.finditer(Path(p).read_bytes()):
            name = os.path.basename(m.group(1).decode('latin1').replace('\\', '/')).lower()
            if name in seen: continue
            seen.add(name)
            h = idx.get(name)
            if not h: continue
            order.append(h); walk(h)
    walk(path)
    return order


def source_hash(path, cfg=None):
    h = hashlib.sha1(Path(path).read_bytes())
    cfg = cfg or config.load()
    for p in headers(path, cfg):
        h.update(b'\0' + os.path.basename(p).encode() + b'\0' + Path(p).read_bytes())
    from compile_context import context_source, uses_multi_context, resolve_context_graph
    if uses_multi_context(path,cfg):
        graph=resolve_context_graph(path,cfg)
        h.update(b'\0context-dag-v1\0')
        for node in (*graph.helpers,graph.primary):
            h.update(b'\0'+os.path.basename(node.source).encode()+b'\0'+Path(node.source).read_bytes())
            for header in headers(node.source,cfg):
                h.update(b'\0'+os.path.basename(header).encode()+b'\0'+Path(header).read_bytes())
            h.update(b'\0'+node.target.encode()+b'\0'+Path(cfg.targets,node.target+'.tsv').read_bytes())
        return h.hexdigest()
    helper = context_source(path, cfg)
    if helper:
        h.update(b'\0context-v1\0' + os.path.basename(helper).encode() + b'\0' + Path(helper).read_bytes())
        for p in headers(helper, cfg):
            h.update(b'\0' + os.path.basename(p).encode() + b'\0' + Path(p).read_bytes())
        # Both ranges define the validated split and the original address context.
        for source in (path, helper):
            target = re.search(rb'/\*\s*target:\s*(\w+)\s*\*/', Path(source).read_bytes())
            if target:
                name = target[1].decode('ascii') + '.tsv'
                h.update(b'\0context-target\0' + name.encode() + b'\0' +
                         Path(cfg.targets, name).read_bytes())
    return h.hexdigest()


if __name__ == '__main__':
    cpath, a = config.pop_config(sys.argv[1:])
    c = config.load(cpath)
    if not a: sys.exit(__doc__)
    for s in a:
        print(s, source_hash(s, c))
        for h in headers(s, c): print('   ', os.path.relpath(h, c.root))
