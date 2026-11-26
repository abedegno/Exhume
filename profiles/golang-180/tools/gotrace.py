#!/usr/bin/env python3
"""gotrace.py: watch the compiler hand out SSA value IDs for one function.

A swapped `(ptr,len)` or `(itab,data)` pair is decided by which recycled value IDs the `dec` pass
pops (compiler.md 2b, 2f). This tool builds a copy of the toolchain's own compiler with logging
added (no behaviour change) and reads its log:

    gotrace.py build [--native CONTAINER:GOROOT] [--second CONTAINER[:GOROOT]]
    gotrace.py trace UNIT FILE.go SSA_NAME FUNC_REGEX [--source ALT.go] [--rows N] [--second C[:GOROOT]]

`build` copies GOROOT inside the container, patches `ssa/func.go`, `ssa/compile.go` (and, on Go 1.8,
`gc/pgen.go` for the frame's variable list) and builds `cmd/compile` to
BUILD/gotrace/compile-GOVERSION-ARCH on the project mount. The patch is applied by this script from
the anchors below, so it follows the source the image really has; it is checked on Go 1.7.5 and
1.8.0. SSA_NAME is the function's name as the SSA back end knows it: `Func`, `(*T).Method`,
`T.Method`, `Func.func1`.

`trace` compiles only the function's package (gofast's captured compile line), so it takes about
a second. It prints the length of the free list before `dec`, the order of each `dec` load pair by
source line (`<`: the first word's ID is lower, so it is loaded first) and, with --rows, every ID
`dec` takes with the pass, op, block and line that freed it.

Log records (tab separated), for scripts (`Tracer.trace` returns them as lists):
    alloc PASS vID fresh|stack OP bN FILE:LINE     a value allocated (fresh ID or popped from the list)
    free  PASS vID OP bN FILE:LINE                 a value freed (pushed)
    stack before_dec|before_decompose_builtin|before_decompose_user N vID    the list, top first
    order after_schedule|after_regalloc bN I vID OP FILE:LINE                final value order
    dcl   before|sorted|offset ...                 (Go 1.8) the frame's variable list around its sort

As a library: `Tracer(settings, tc, unit, rel, ssa_name, func_regex, tag, workers)`, `.trace(text,
worker)`, `summary(rows)`, `load_group(rows, line, kinds)`, `inversions(ids, want)`,
`deletions(table, want)`. Set EXHUME_GO_NATIVE (see gofast.py) to trace with a host-native compiler.
"""
import argparse
import itertools
import os
import re
import shlex
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gofast
import gotc

# ---------------------------------------------------------------- the logging patch

_FUNC_GO = '''
// gotrace: logging only (GOTRACE_FN = SSA function name, GOTRACE_OUT = file to append to).
var traceFn = os.Getenv("GOTRACE_FN")
var traceOut *os.File

func (f *Func) tracing() bool { return traceFn != "" && f.Name == traceFn }

func (f *Func) tracef(format string, a ...interface{}) {
	if traceOut == nil {
		var err error
		traceOut, err = os.OpenFile(os.Getenv("GOTRACE_OUT"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)
		if err != nil {
			traceOut = os.Stderr
		}
	}
	fmt.Fprintf(traceOut, format+"\\n", a...)
}

func (f *Func) tracePass() string {
	if f.pass == nil {
		return "build"
	}
	return strings.Replace(f.pass.name, " ", "_", -1)
}

func (f *Func) traceLine(line int32) string {
	s := f.Config.fe.Line(line)
	if i := strings.LastIndex(s, "/"); i >= 0 {
		s = s[i+1:]
	}
	return s
}

func (f *Func) traceStack(tag string) {
	n := 0
	for v := f.freeValues; v != nil; v = v.argstorage[0] {
		f.tracef("stack\\t%s\\t%d\\tv%d", tag, n, v.ID)
		n++
	}
	f.tracef("stackend\\t%s\\t%d", tag, n)
}

func (f *Func) traceBlocks(tag string) {
	for _, b := range f.Blocks {
		for i, v := range b.Values {
			f.tracef("order\\t%s\\tb%d\\t%d\\tv%d\\t%s\\t%s", tag, b.ID, i, v.ID, v.Op, f.traceLine(v.Line))
		}
	}
}
'''


def _once(text, old, new, what):
    if text.count(old) != 1:
        raise gotc.ToolError(f'gotrace patch: anchor for {what} found {text.count(old)} times; this Go '
                             f'version needs its own anchors')
    return text.replace(old, new, 1)


def _imports(text, names):
    m = re.search(r'import \(\n((?:\t.*\n)*?)\)', text)
    if not m:
        raise gotc.ToolError('gotrace patch: no import block')
    have = m.group(1)
    add = ''.join(f'\t"{n}"\n' for n in names if f'"{n}"' not in have)
    return text[:m.end(1)] + add + text[m.end(1):]


def patch_func_go(t):
    t = _imports(t, ['os', 'strings'])
    t = _once(t, '\tvar v *Value\n\tif f.freeValues != nil {\n\t\tv = f.freeValues',
              '\tvar v *Value\n\tfrom := "fresh"\n\tif f.freeValues != nil {\n\t\tfrom = "stack"\n\t\tv = f.freeValues',
              'newValue')
    t = _once(t, '\tv.Line = line\n\tb.Values = append(b.Values, v)\n\treturn v\n}\n',
              '\tv.Line = line\n\tb.Values = append(b.Values, v)\n\tif f.tracing() {\n'
              '\t\tf.tracef("alloc\\t%s\\tv%d\\t%s\\t%s\\tb%d\\t%s", f.tracePass(), v.ID, from, op, b.ID, f.traceLine(line))\n'
              '\t}\n\treturn v\n}\n' + _FUNC_GO, 'newValue end')
    t = _once(t, '\tif v.Uses != 0 {\n\t\tf.Fatalf("value %s still has %d uses", v, v.Uses)\n\t}\n',
              '\tif v.Uses != 0 {\n\t\tf.Fatalf("value %s still has %d uses", v, v.Uses)\n\t}\n\tif f.tracing() {\n'
              '\t\tf.tracef("free\\t%s\\tv%d\\t%s\\tb%d\\t%s", f.tracePass(), v.ID, v.Op, v.Block.ID, f.traceLine(v.Line))\n\t}\n',
              'freeValue')
    return t


def patch_compile_go(t):
    t = _imports(t, ['strings'])
    return _once(t, '\t\ttStart := time.Now()\n\t\tp.fn(f)\n\t\ttEnd := time.Now()\n',
                 '\t\tif f.tracing() {\n\t\t\tf.tracef("pass\\t%s", strings.Replace(p.name, " ", "_", -1))\n'
                 '\t\t\tif p.name == "dec" || p.name == "decompose user" || p.name == "decompose builtin" {\n'
                 '\t\t\t\tf.traceStack("before_" + strings.Replace(p.name, " ", "_", -1))\n\t\t\t}\n\t\t}\n'
                 '\t\ttStart := time.Now()\n\t\tp.fn(f)\n\t\ttEnd := time.Now()\n'
                 '\t\tif f.tracing() && (p.name == "schedule" || p.name == "regalloc") {\n'
                 '\t\t\tf.traceBlocks("after_" + p.name)\n\t\t}\n', 'the pass loop')


def patch_pgen_go(t):
    """Go 1.8 only: the frame's variable list before and after its (unstable) sort, and the offsets."""
    t = _imports(t, ['os'])
    return _once(t, '\tsort.Sort(byStackVar(Curfn.Func.Dcl))\n',
                 '\tdcltrace := os.Getenv("GOTRACE_FN") != "" && Curfn.Func.Nname != nil && Curfn.Func.Nname.Sym.Name == os.Getenv("GOTRACE_FN")\n'
                 '\tvar dclout *os.File\n\tif dcltrace {\n'
                 '\t\tdclout, _ = os.OpenFile(os.Getenv("GOTRACE_OUT"), os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0644)\n'
                 '\t\tfor i, n := range Curfn.Func.Dcl {\n'
                 '\t\t\tfmt.Fprintf(dclout, "dcl\\tbefore\\t%d\\t%s\\t%d\\t%v\\t%v\\t%v\\t%d\\t%d\\t%s\\n", i, n.Sym.Name, n.Class, n.Used, haspointers(n.Type), n.Name.Needzero, n.Type.Width, n.Xoffset, linestr(n.Lineno))\n'
                 '\t\t}\n\t}\n\tsort.Sort(byStackVar(Curfn.Func.Dcl))\n\tif dcltrace {\n'
                 '\t\tfor i, n := range Curfn.Func.Dcl {\n\t\t\tfmt.Fprintf(dclout, "dcl\\tsorted\\t%d\\t%s\\n", i, n.Sym.Name)\n\t\t}\n'
                 '\t\tdefer func() {\n\t\t\tfor _, n := range Curfn.Func.Dcl {\n\t\t\t\tif n.Class == PAUTO {\n'
                 '\t\t\t\t\tfmt.Fprintf(dclout, "dcl\\toffset\\t%s\\t%d\\t%d\\n", n.Sym.Name, n.Xoffset, n.Type.Width)\n'
                 '\t\t\t\t}\n\t\t\t}\n\t\t\tdclout.Close()\n\t\t}()\n\t}\n', 'the frame sort')


def _sh(container, script, stdin=None, timeout=1800):
    return subprocess.run(['docker', 'exec', '-i', container, 'sh', '-c', script],
                          input=stdin, capture_output=True, text=True, timeout=timeout)


def toolchain_info(container, goroot=None):
    """(GOROOT, go version string, host os_arch) of the toolchain in CONTAINER."""
    pre = f'GOROOT={goroot} {goroot}/bin/' if goroot else ''
    r = _sh(container, f'{pre}go env GOROOT GOHOSTOS GOHOSTARCH; {pre}go version')
    f = r.stdout.split()
    if r.returncode != 0 or len(f) < 6:
        raise gotc.ToolError(f'no Go toolchain in {container} ({goroot or "PATH"}): {r.stderr[-300:]}')
    return f[0], f[5], f'{f[1]}_{f[2]}'


def compiler_path(mount_build, version, arch):
    return f'{mount_build}/gotrace/compile-{version}-{arch}'


def build_compiler(container, mount_build, goroot=None, say=print):
    """Build the logging compiler of the toolchain in CONTAINER; returns its path in the container.
    MOUNT_BUILD is the project's build directory as the container sees it (on the project mount)."""
    root, version, arch = toolchain_info(container, goroot)
    out = compiler_path(mount_build, version, arch)
    work = f'/tmp/gotrace-build-{version}'
    go = f'GOROOT={work} {root}/bin/go'
    r = _sh(container, f'rm -rf {work} && cp -r {root} {work} && mkdir -p {os.path.dirname(out)}')
    if r.returncode:
        raise gotc.ToolError(f'copying GOROOT failed: {r.stderr[-400:]}')
    files = [('ssa/func.go', patch_func_go), ('ssa/compile.go', patch_compile_go)]
    if version.startswith('go1.8'):
        files.append(('gc/pgen.go', patch_pgen_go))
    for rel, fn in files:
        p = f'{work}/src/cmd/compile/internal/{rel}'
        src = _sh(container, f'cat {p}').stdout
        r = _sh(container, f'cat > {p}', stdin=fn(src))
        if r.returncode:
            raise gotc.ToolError(f'writing {p} failed')
    say(f'building the logging compiler for {version} ({arch}) ...')
    r = _sh(container, f'cd {work}/src && {go} build -a -o {out} cmd/compile && rm -rf {work}')
    if r.returncode:
        raise gotc.ToolError(f'building cmd/compile failed:\n{r.stderr[-1500:]}')
    return out


# ---------------------------------------------------------------- tracing


class Tracer:
    """Trace one function of one package; `trace(text)` compiles TEXT as the unit's file REL."""

    def __init__(self, settings, tc, unit, rel, ssa_name, func_regex, tag, workers=1):
        s = settings
        self.f = gofast.Fast(s, tc, unit, rel, func_regex, tag).prepare(
            workers, os.path.join(s.build, 'gorandperm-wk', re.sub(r'\W+', '_', tag), 'fast'))
        self.ssa = ssa_name
        f = self.f
        mount_build = '/work/' + os.path.relpath(s.build, s.root).replace(os.sep, '/')
        goroot = f.native_goroot if f.native else None
        _, version, arch = toolchain_info(f.xc, goroot)
        tcompile = compiler_path(mount_build, version, arch)
        if _sh(f.xc, f'test -x {tcompile}').returncode:
            build_compiler(f.xc, mount_build, goroot)
        args = shlex.split(f.compile)
        i = next(k for k, a in enumerate(args) if a.endswith('/compile'))
        self.tcompile = ' '.join(a if '=' in a and k < i else shlex.quote(a)
                                 for k, a in enumerate(args[:i] + [tcompile] + args[i + 1:]))

    def trace(self, text, worker=0):
        f = self.f
        wdir = f'{f.croot}/w{worker}'
        script = (f'cd {wdir} && cat > {shlex.quote(f.file)} && rm -f T.log && '
                  f'GOTRACE_FN={shlex.quote(self.ssa)} GOTRACE_OUT={wdir}/T.log {self.tcompile} '
                  f'>/dev/null 2>ERR.txt && cat T.log || {{ cat ERR.txt >&2; exit 3; }}')
        r = _sh(f.xc, script, stdin=text, timeout=300)
        if r.returncode:
            raise gotc.ToolError(r.stderr[-800:])
        return parse(r.stdout)


class SecondTracer:
    """The same trace under a second Go version (godual.Second, already prepared): its logging
    compiler (`gotrace.py build --second ...`) run with that toolchain's own compile line."""

    def __init__(self, second, rel, ssa_name, mount_build):
        self.second, self.rel, self.ssa = second, rel, ssa_name
        _, version, arch = toolchain_info(second.c, second.goroot)
        tcompile = compiler_path(mount_build, version, arch)
        if _sh(second.c, f'test -x {tcompile}').returncode:
            build_compiler(second.c, mount_build, second.goroot)
        self.dir, args = second.compile_line(rel)
        cmd, i = [tcompile], 1
        while i < len(args):
            if args[i] == '-o':
                cmd += ['-o', f'{second.root}/TRACE.a']
                i += 2
            elif args[i] == '-I' and args[i + 1].startswith('/nonexistent-work'):
                i += 2
            else:
                cmd.append(args[i])
                i += 1
        env = (f'GOROOT={second.goroot} ' if second.goroot else '') + 'GOOS=linux GOARCH=amd64 '
        self.cmd = env + ' '.join(shlex.quote(a) for a in cmd)

    def trace(self, text):
        r, log = self.second.root, f'{self.second.root}/T.log'
        res = _sh(self.second.c, f'cat > {r}/w0/src/{shlex.quote(self.rel)} && cd {self.dir} && rm -f {log} && '
                  f'GOTRACE_FN={shlex.quote(self.ssa)} GOTRACE_OUT={log} {self.cmd} >/dev/null 2>{r}/ERR && cat {log} '
                  f'|| {{ cat {r}/ERR >&2; exit 3; }}', stdin=text, timeout=300)
        if res.returncode:
            raise gotc.ToolError(res.stderr[-800:])
        return parse(res.stdout)


def parse(text):
    return [ln.split('\t') for ln in text.split('\n') if ln]


def summary(rows):
    """(length of the free list before dec, {line: ['<' or '>' per load pair]}, table).
    table: one tuple per value `dec` allocates, in order:
    (index, vID, fresh|stack, op, line, freeing pass, op when freed, block, line where freed)."""
    last, stack, dec, seen = {}, [], [], False
    for f in rows:
        if f[0] == 'free' and not seen:
            last[f[2]] = (f[1], f[3], f[4], f[5].split(':')[-1])
        elif f[0] == 'stack' and f[1] == 'before_dec':
            stack.append(f[3])
            seen = True
        elif f[0] == 'alloc' and f[1] == 'dec':
            dec.append(f)
    pairs, table, prev, used = {}, [], None, set()
    for i, a in enumerate(dec):
        line = a[6].split(':')[-1] if len(a) > 6 else '?'
        info = last.get(a[2], ('?',) * 4) if a[3] != 'fresh' else ('fresh',) * 4
        table.append((i, a[2], a[3], a[4], line) + info)
        if (a[4] == 'Load' and prev is not None and prev[4] == 'Load' and prev_line == line
                and (i - 1) not in used and line.isdigit()):
            pairs.setdefault(int(line), []).append('<' if int(prev[2][1:]) < int(a[2][1:]) else '>')
            used.add(i)
        prev, prev_line = a, line
    return len(stack), pairs, table


KINDS = {'string': ['Load', 'Load', 'OffPtr'], 'iface': ['Load', 'Load', 'OffPtr'],
         'slice': ['Load', 'Load', 'OffPtr', 'Load', 'OffPtr']}


def load_group(rows, line, kinds):
    """IDs of the loads `dec` makes for consecutive multi-word values on LINE, e.g. the results of
    `b, err := f()` are kinds ['slice', 'iface'] (words ptr, len, cap, itab, data). The group is
    found by its own op pattern, never by a fixed offset: an edit that makes `dec` split something
    else first moves it. Returns [ids] in word order, or None when the pattern is not on that line.
    When it occurs more than once the last occurrence is returned (results follow arguments)."""
    pat = [op for k in kinds for op in KINDS[k]]
    dec = [(f[4], int(f[2][1:]), f[6].split(':')[-1]) for f in rows if f[0] == 'alloc' and f[1] == 'dec']
    hit = None
    for k in range(len(dec) - len(pat) + 1):
        if all(dec[k + j][0] == pat[j] and dec[k + j][2] == str(line) for j in range(len(pat))):
            hit = k
    if hit is None:
        return None
    return [dec[hit + j][1] for j, op in enumerate(pat) if op == 'Load']


def inversions(ids, want):
    """How many pairs of words are emitted in the wrong relative order. IDS: the words' value IDs in
    word order; WANT: the original's emission order as word indexes (read it from the disassembly).
    The scheduler emits same-line loads in ascending ID, so 0 means the original's order."""
    got = sorted(range(len(ids)), key=lambda i: ids[i])
    return sum(1 for a in range(len(want)) for b in range(a + 1, len(want))
               if got.index(want[a]) > got.index(want[b]))


def deletions(table, want, upto=3, span=60):
    """Smallest sets of entries which, deleted from the list `dec` reads, give the wanted pair orders.
    WANT: {line: '<' or '>'} for the first load pair of each line. Returns [[table rows]]. Only a
    deletion of a value that was built before the optimiser ran (a low, never recycled ID) is a
    change real source can make on its own; deleting a recycled one would also renumber others."""
    ids = [int(t[1][1:]) for t in table]
    pos = {}
    for i, t in enumerate(table):
        if t[3] == 'Load' and t[4].isdigit() and int(t[4]) in want and int(t[4]) not in pos:
            pos[int(t[4])] = i
    lines = [ln for ln in sorted(want) if ln in pos]

    def ok(seq):
        return all((seq[pos[ln]] < seq[pos[ln] + 1]) == (want[ln] == '<') for ln in lines)
    for k in range(1, upto + 1):
        sols = [d for d in itertools.combinations(range(min(span, len(ids))), k)
                if ok([x for i, x in enumerate(ids) if i not in d])]
        if sols:
            return [[table[i] for i in d] for d in sols]
    return []


def main(argv=None):
    ap = argparse.ArgumentParser(description='SSA value-ID allocator trace for one Go function')
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build', help='build the logging compiler')
    b.add_argument('--native', help='CONTAINER:GOROOT of a host-native toolchain (see gonative.py)')
    b.add_argument('--second', help='CONTAINER[:GOROOT] of a second Go version (see godual.py)')
    b.add_argument('--config')
    t = sub.add_parser('trace', help='trace one function')
    t.add_argument('unit')
    t.add_argument('file', help='source file relative to the unit\'s GOPATH/src')
    t.add_argument('ssa_name')
    t.add_argument('func', help='gocmp regex of the function in the image')
    t.add_argument('--source', help='compile this text instead of the unit\'s own file')
    t.add_argument('--rows', type=int, default=0, help='print the first N values dec takes, with provenance')
    t.add_argument('--second', help='trace under a second Go version: CONTAINER[:GOROOT] (see godual.py)')
    t.add_argument('--config')
    a = ap.parse_args(argv)
    s = gotc.settings(a.config)
    tc = gotc.Toolchain(s)
    mount_build = '/work/' + os.path.relpath(s.build, s.root).replace(os.sep, '/')
    if a.cmd == 'build':
        spec = a.native or a.second
        if spec:
            c, _, groot = spec.partition(':')
            print(build_compiler(c, mount_build, groot or None))
        else:
            print(build_compiler(tc.ensure_daemon(), mount_build))
        return 0
    unit, _ = s.resolve_unit(a.unit)
    path = a.source or os.path.join(unit.src_dir, a.file)
    if a.second:
        import godual
        c, _, groot = a.second.partition(':')
        sec = godual.Second(c, groot or None, None, 'gotrace2_' + re.sub(r'\W+', '_', a.ssa_name), s.goos, s.goarch)
        mnt = lambda p: '/work/' + os.path.relpath(p, s.root).replace(os.sep, '/')
        out = os.path.join(s.build, 'godual')
        sec.prepare(mnt(unit.src_dir), unit.pkg, out, mnt(out))
        T = SecondTracer(sec, a.file, a.ssa_name, mount_build)
    else:
        T = Tracer(s, tc, unit, a.file, a.ssa_name, a.func, 'gotrace_' + re.sub(r'\W+', '_', a.ssa_name))
    n, pairs, table = summary(T.trace(open(path).read()))
    print(f'free list before dec: {n} entries; dec takes {len(table)} values')
    print('load pairs by line:', ' '.join(f'L{k}{"".join(v)}' for k, v in sorted(pairs.items())))
    for row in table[:a.rows]:
        print('  %3d %-6s %-6s %-7s L%-5s <- %-16s %-11s %-5s L%s' % row)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except gotc.ToolError as e:
        sys.exit(f'gotrace: {e}')
