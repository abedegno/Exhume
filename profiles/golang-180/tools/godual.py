#!/usr/bin/env python3
"""godual.py: score source against TWO releases built by two Go versions.

A vendor often ships neighbouring releases built with different compilers (here: some with Go 1.8,
some with Go 1.7.5). When a function's source did not change between two such releases, the real
source must reproduce BOTH. A dead-code knob fitted to one compiler's value numbering almost never
moves the other, so the second release tells real source from a fit, and it responds to exactly
the things worth trying: how an expression is grouped, operand order, how a value is built.

    godual.py score UNIT FILE.go FUNC_REGEX --second C[:GOROOT] --target2 BIN [--source ALT.go ...]
    godual.py enum  UNIT FILE.go FUNC_REGEX SPEC.json --second C[:GOROOT] --target2 BIN
                    [--base ALT.go] [--depth 1|2|3] [--workers N] [--out FILE.json]

--second names a running container that holds the second toolchain, with the project mounted at the
same place as the first (`docker run -d --name go175 --platform linux/amd64 -v ROOT:/work
golang:1.7.5 sleep infinity`), or CONTAINER:GOROOT for a toolchain at another path, e.g. a host-native
one from gonative.py. --target2 is the release that toolchain built. Check first that the function
has the same line range and, compiler apart, the same shape in both (goinltree.py / gomap.py).

`score` prints, per source: the first compiler's object score (gofast: structure first) and, for
the second, the differing bytes outside relocations and the source lines they are on.

`enum` scores rewrites exhaustively. SPEC.json: {"name": [[line, "old", "new"], ...], ...}; each
rewrite replaces OLD by NEW once on that line (OLD "" means the line must be blank or `//` and
becomes NEW). All singles, then all pairs (--depth 2), then triples of the rewrites that appear in
the 40 best results (--depth 3). A rewrite here should be something a programmer could have written
instead: a named local for a temporary, `var x T` then `=`, `c == x` for `x == c`, `a && (b && c)`,
a literal against field-by-field assignment. Sort key: wrong pairs under the first compiler plus
wrong lines under the second.

As a library: `Second(container, goroot, target, tag)`, `.prepare(src_dir, pkg, workers)`,
`.score(worker, rel, text, regex) -> (bytes or None, [lines])`, `.compile_line(rel)`;
`Dual(settings, tc, unit, rel, regex, tag, second, workers)`, `.score(worker, text)`.
"""
import argparse
import itertools
import json
import os
import queue
import re
import shlex
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gofast
import gotc


class Second:
    """A second Go toolchain in a container, and the release it built."""

    def __init__(self, container, goroot=None, target=None, tag='dual', goos='linux', goarch='amd64',
                 goroot_final='/usr/local/go'):
        self.c, self.goroot, self.target = container, goroot, target
        self.tag = re.sub(r'\W+', '_', tag)
        self.root = f'/tmp/godual/{self.tag}'
        env = f'CGO_ENABLED=0 GOOS={goos} GOARCH={goarch}'
        # a toolchain that is not at its usual path would otherwise write its own path into the
        # binary's file table, which changes the size of the line tables
        self.env = (f'GOROOT={goroot} GOROOT_FINAL={goroot_final} ' + env) if goroot else env
        self.go = f'{goroot}/bin/go' if goroot else 'go'

    def sh(self, script, stdin=None, timeout=1800):
        return subprocess.run(['docker', 'exec', '-i', self.c, 'sh', '-c', script],
                              input=stdin, capture_output=True, text=True, timeout=timeout)

    def _build(self, w, extra=''):
        g = f'{self.root}/w{w}'
        return (f'cd {g} && GOPATH={g} {self.env} {self.go} build -i{extra} -gcflags -trimpath={g} '
                f"-ldflags '-s -w' -o {self.out_c}/{self.tag}_w{w}.bin {self.pkg}")

    def prepare(self, src_mount, pkg, out_host, out_mount, workers=1):
        """One GOPATH copy per worker on the container's own disk (SRC_MOUNT: the unit's GOPATH/src
        as the container sees it); binaries go to OUT_MOUNT (= OUT_HOST on the host)."""
        self.pkg, self.out_h, self.out_c, self.workers = pkg, out_host, out_mount, workers
        os.makedirs(out_host, exist_ok=True)
        script = (f'rm -rf {self.root} && mkdir -p {self.root}/w0 && cp -r {src_mount} {self.root}/w0/src && '
                  + self._build(0) + ''.join(f' && cp -r {self.root}/w0 {self.root}/w{n}' for n in range(1, workers)))
        r = self.sh(script)
        if r.returncode:
            raise gotc.ToolError(f'second toolchain: set-up build failed:\n{r.stderr[-1500:]}')
        return self

    def build(self, w, rel, text):
        """Write TEXT as REL in worker W's GOPATH and build; the binary's host path, or None."""
        r = self.sh(f'cat > {self.root}/w{w}/src/{shlex.quote(rel)} && ' + self._build(w), stdin=text, timeout=900)
        return None if r.returncode else os.path.join(self.out_h, f'{self.tag}_w{w}.bin')

    def score(self, w, rel, text, regex, gocmp):
        """(differing bytes outside relocations, sorted lines they are on); (None, []) when it does
        not build; (-1, []) when a function's size differs from the release's."""
        b = self.build(w, rel, text)
        if b is None:
            return None, []
        out = subprocess.run([gocmp, '-v', '-first', '100000', 'cmp', self.target, b, regex],
                             capture_output=True, text=True).stdout
        if any(x != y for x, y in re.findall(r'\((\d+) vs (\d+) bytes\)', out)):
            return -1, []
        base = re.escape(os.path.basename(rel))
        lines = sorted({int(m.group(1)) for m in re.finditer(r'^\s+\* ' + base + r':(\d+)', out, re.M)})
        raw = subprocess.run([gocmp, 'raw', self.target, b, regex], capture_output=True, text=True).stdout
        return sum(1 for ln in raw.splitlines() if ln.startswith('UNMASKED')), lines

    def compile_line(self, rel):
        """The second toolchain's own compile command for REL's package (argv list, from `go build
        -x` in worker 0), for running a logging compiler of that version (gotrace.py)."""
        g = f'{self.root}/w0'
        r = self.sh(f'touch {g}/src/{shlex.quote(rel)} && ' + self._build(0, ' -x'))
        want, cd = f'{g}/src/{os.path.dirname(rel)}', None
        for ln in r.stderr.split('\n'):
            if ln.startswith('cd '):
                cd = ln[3:].strip()
            elif '/compile ' in ln and cd == want:
                return want, shlex.split(ln.replace('$WORK', '/nonexistent-work'))
        raise gotc.ToolError(f'second toolchain: no compile line for {rel}')


class Dual:
    def __init__(self, settings, tc, unit, rel, regex, tag, second, workers=1):
        s = settings
        self.rel, self.rx, self.second = rel, regex, second
        self.gocmp = tc.ensure_tool('gocmp')
        self.f = gofast.Fast(s, tc, unit, rel, regex, tag).prepare(
            workers, os.path.join(s.build, 'gorandperm-wk', re.sub(r'\W+', '_', tag), 'fast'))
        mount = lambda p: '/work/' + os.path.relpath(p, s.root).replace(os.sep, '/')
        out = os.path.join(s.build, 'godual')
        second.prepare(mount(unit.src_dir), unit.pkg, out, mount(out), workers)

    def score(self, w, text):
        """(first compiler's object score or None, second's differing bytes, second's wrong lines)."""
        s1, _ = self.f.evaluate(w, text)
        if s1 is None:
            return None, None, []
        b, lines = self.second.score(w, self.rel, text, self.rx, self.gocmp)
        return s1, b, lines


def key(sc):
    """Sort key of a score: unusable last; else wrong pairs (first) + wrong lines (second)."""
    s1, b, lines = sc
    if s1 is None or b is None or b < 0 or s1 >= 1_000_000:
        return (10**6,)
    return ((s1 // gofast.STRUCT + 1) // 2 + len(lines), (s1 // gofast.STRUCT + 1) // 2, len(lines), s1, b)


def apply_rewrites(base_lines, spec, names):
    """BASE with the named rewrites applied, or None when one does not fit its line."""
    out = list(base_lines)
    for n in names:
        for line, old, new in spec[n]:
            cur = out[line - 1]
            if old == '':
                if cur.strip() not in ('', '//'):
                    return None
                out[line - 1] = new
            elif old in cur:
                out[line - 1] = cur.replace(old, new, 1)
            else:
                return None
    return '\n'.join(out)


def enumerate_rewrites(dual, base_text, spec, depth, workers, say=print):
    base = base_text.split('\n')
    q = queue.Queue()
    for i in range(workers):
        q.put(i)

    def one(names):
        t = apply_rewrites(base, spec, names)
        if t is None:
            return names, (None, None, [])
        w = q.get()
        try:
            return names, dual.score(w, t)
        finally:
            q.put(w)
    results = [((), dual.score(0, base_text))]
    say(f'base: {results[0][1]}')
    stage = [(n,) for n in spec]
    for d in range(1, depth + 1):
        got = list(ThreadPoolExecutor(workers).map(one, stage))
        results += got
        usable = sorted((r for r in results if key(r[1])[0] < 10**6), key=lambda r: key(r[1]))
        say(f'{d} at a time: {len(got)} tried, {sum(1 for r in got if key(r[1])[0] < 10**6)} usable; best {usable[0] if usable else None}')
        if usable and key(usable[0][1])[0] == 0:
            break
        live = [n for (n,), sc in results[1:1 + len(spec)] if key(sc)[0] < 10**6]
        if d == 1:
            stage = list(itertools.combinations(live, 2))
        else:
            top = []
            for names, _ in usable[:40]:
                top += [n for n in names if n not in top]
            stage = list(itertools.combinations(top[:22], 3))
    return sorted(results, key=lambda r: key(r[1]))


def main(argv=None):
    ap = argparse.ArgumentParser(description='Score Go source against two releases built by two Go versions')
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('score', 'enum'):
        p = sub.add_parser(name)
        p.add_argument('unit')
        p.add_argument('file', help='source file relative to the unit\'s GOPATH/src')
        p.add_argument('func', help='gocmp regex (must fit the function in both releases)')
        if name == 'enum':
            p.add_argument('spec', help='JSON: {"name": [[line, "old", "new"], ...]}')
            p.add_argument('--base', help='start from this text instead of the unit\'s file')
            p.add_argument('--depth', type=int, default=2, choices=(1, 2, 3))
            p.add_argument('--out')
        else:
            p.add_argument('--source', action='append', help='score this text (repeatable); default: the unit\'s file')
        p.add_argument('--second', required=True, help='CONTAINER[:GOROOT] of the second toolchain')
        p.add_argument('--target2', required=True, help='the release the second toolchain built')
        p.add_argument('--workers', type=int, default=4)
        p.add_argument('--config')
    a = ap.parse_args(argv)
    s = gotc.settings(a.config)
    tc = gotc.Toolchain(s)
    unit, _ = s.resolve_unit(a.unit)
    c, _, groot = a.second.partition(':')
    tag = 'dual_' + re.sub(r'\W+', '_', a.func)[-40:]
    workers = a.workers if a.cmd == 'enum' else 1
    second = Second(c, groot or None, os.path.join(s.root, a.target2) if not os.path.isabs(a.target2) else a.target2,
                    tag, s.goos, s.goarch)
    dual = Dual(s, tc, unit, a.file, a.func, tag, second, workers)
    own = os.path.join(unit.src_dir, a.file)
    if a.cmd == 'score':
        for path in (a.source or [own]):
            s1, b, lines = dual.score(0, open(path).read())
            print(f'{path}: first compiler score {s1}; second: {b} bytes differ, lines {lines}')
        return 0
    spec = json.load(open(a.spec))
    res = enumerate_rewrites(dual, open(a.base or own).read(), spec, a.depth, workers)
    for names, sc in res[:25]:
        print(key(sc)[:3], sc, '|', ' + '.join(names) or '(base)')
    if a.out:
        json.dump([[list(n), list(sc)] for n, sc in res], open(a.out, 'w'))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except gotc.ToolError as e:
        sys.exit(f'godual: {e}')
