"""Fast scoring for gorandperm.py: compile one package, score its functions from the object.

`go build -i` of a whole unit costs seconds per candidate even when one file changed (the go
tool checks every package for staleness, recompiles the packages that import the changed one,
and links). The score needs none of that: the compiler's package archive already holds each
function's bytes, relocation ranges and line numbers, and Go 1.8's `go tool objdump` prints
them. So a candidate is: write one file, run the compiler on its package, dump the wanted
functions, compare with the image.

Set-up (once per run, `Fast.prepare`):
  * the unit is built once with `go build -i -x` in a scratch GOPATH on the project mount, with
    one file of the package touched, and the compiler line for the package is taken from the
    `-x` output (so -p, -importmap, -D and the file list are the go tool's own);
  * the package's sources and the installed `pkg/` tree are copied to a directory on the
    container's own filesystem (not the host mount, which is slow), one source copy per worker;
  * `gocmp ref IMAGE REGEX` gives, per wanted function of the image, its bytes and the source
    line of each instruction.

Per candidate (`Fast.evaluate`): one `docker exec` that writes the file from stdin, compiles,
and dumps the functions whose name contains the wanted bare name. Score per function:

  * a size other than the image's: 1,000,000 + the difference (as gorandperm's own score);
  * else the bytes that differ outside the object's relocation ranges, plus the instructions
    that differ in such a byte or in their source line, plus STRUCT (10,000) for each of those
    instructions that differs in more than its registers (`gocmp mask`), so that a result is
    ordered by its structural differences first and its register ties second.

Relocated fields are not compared, so a call to the wrong function scores 0 here: a 0 is
always confirmed by the caller with a whole build and gocmp. `Result.key` is a hash of the
dumped functions (bytes, relocations, lines), so equal code is scored once.

Only the `docker-daemon` runner is supported (a persistent container to exec into).

Host-native compiler. On a machine whose CPU is not the image's (an arm64 host running the amd64
toolchain image under emulation) the compiler itself is the slow part. The Go compiler is a cross
compiler, so the same Go version built for the host writes the same object for the target. Set
`EXHUME_GO_NATIVE=CONTAINER:GOROOT` to a running container of the host's architecture that has the
project at the same mount point and that Go version at GOROOT: set-up (the `go build -i -x`) still
runs in the toolchain container, the target's std export data is copied over, and each candidate
is then compiled and dumped in the native one. `prepare` compiles the unchanged file with both
and refuses the native compiler unless the two archives are byte-identical.
"""
import hashlib
import os
import re
import shlex
import subprocess

import gotc

_RELOC = re.compile(r'\[(\d+):(\d+)\]R_')


class FastError(Exception):
    pass


def parse_objdump(text):
    """{symbol: [(line, bytes, [(start, end), ...]), ...]} from `go tool objdump` of an archive."""
    funcs, cur = {}, None
    for ln in text.split("\n"):
        if ln.startswith("TEXT "):
            name = ln[5:].split("(SB)")[0].replace("%22%22", '""')
            cur = funcs.setdefault(name, [])
            continue
        if cur is None or not ln.startswith("\t"):
            continue
        f = [x for x in ln.split("\t") if x]        # tabwriter output: the empty columns vary
        if len(f) < 3 or not f[1].startswith("0x"):
            continue
        try:
            line = int(f[0].rsplit(":", 1)[1])
            code = bytes.fromhex(f[2])
        except (ValueError, IndexError):
            continue
        cur.append((line, code, [(int(a), int(b)) for a, b in _RELOC.findall(ln)]))
    return funcs


def parse_ref(text):
    """{name: (bytes, {offset: line}, {offset: masked form})} from `gocmp ref`."""
    out, cur = {}, None
    for ln in text.split("\n"):
        f = ln.split(" ")
        if f[0] == "FUNC":
            cur = (bytes.fromhex(f[3]), {}, {})
            out[f[1]] = cur
        elif f[0] == "I" and cur is not None:
            cur[1][int(f[1])] = int(f[3])
            if len(f) > 4:
                cur[2][int(f[1])] = f[4]
    return out


STRUCT = 10_000     # one structural difference outweighs any number of register ties


def first_diff(ref, insts):
    """(offset, source line) of the first instruction that differs from the image's, or None."""
    rbytes, rlines, _ = ref
    off = 0
    for line, code, rel in insts:
        if off + len(code) > len(rbytes):
            return off, line
        if rlines.get(off, line) != line or any(
                b != rbytes[off + i] and not any(a <= i < e for a, e in rel) for i, b in enumerate(code)):
            return off, line
        off += len(code)
    return None


def score_func(ref, insts, masks=None):
    """Score one function's object instructions against the image's (bytes, lines, masked forms).
    With MASKS (the masked form of each object instruction), an instruction whose masked form is
    not the image's at that offset is a structural difference and costs STRUCT more: an
    instruction the image has elsewhere, or a load from another slot, against a register tie."""
    rbytes, rlines, rmasks = ref
    size = sum(len(c) for _, c, _ in insts)
    # the image's function runs to the next one: drop its int3 padding
    if len(rbytes) > size and all(b == 0xcc for b in rbytes[size:]):
        rbytes = rbytes[:size]
    if len(rbytes) != size:
        return 1_000_000 + abs(len(rbytes) - size)
    sc, off = 0, 0
    for n, (line, code, rel) in enumerate(insts):
        bad = 0
        for i, b in enumerate(code):
            if b != rbytes[off + i] and not any(a <= i < e for a, e in rel):
                bad += 1
        if bad or rlines.get(off, line) != line:
            sc += bad + 1
            if masks is not None and not rel and (off not in rmasks or rmasks[off] != masks[n]
                                                  or rlines.get(off, line) != line):
                sc += STRUCT
        off += len(code)
    return sc


class Fast:
    def __init__(self, settings, tc, unit, rel, func_regex, tag):
        if settings.runner != 'docker-daemon':
            raise FastError('fast scoring needs the docker-daemon runner')
        self.s, self.tc, self.unit, self.rel, self.rx = settings, tc, unit, rel, func_regex
        self.pkg_dir = os.path.dirname(rel)                    # relative to GOPATH/src
        self.file = os.path.basename(rel)
        self.tag = re.sub(r'\W+', '_', tag)
        self.croot = f'/tmp/gofast/{self.tag}'
        self.container = tc.ensure_daemon()
        self.bare = re.sub(r'[\\^$]', '', func_regex).split('.')[-1].strip('()*')
        self.cache = {}
        self.first = {}      # object key -> (offset, line) of the first differing instruction
        self.structure = True
        self.gocmp = tc.ensure_tool('gocmp')
        self.xc = self.container          # where candidates are compiled (the native container, if any)
        self.objdump = 'go tool objdump'
        self.native = False

    # ------------------------------------------------------------ set-up
    def _exec(self, script, stdin=None, timeout=600):
        return subprocess.run(['docker', 'exec', '-i', self.container, 'sh', '-c', script],
                              input=stdin, capture_output=True, text=True, timeout=timeout)

    def prepare(self, workers, scratch_host):
        """Capture the package's compile line, copy sources and pkg/ into the container."""
        s, unit = self.s, self.unit
        gp_host = os.path.join(scratch_host, 'gopath')
        os.makedirs(gp_host, exist_ok=True)
        for d in ('src', 'pkg'):
            if os.path.isdir(os.path.join(unit.gopath, d)):
                subprocess.run(['rsync', '-a', '--delete', os.path.join(unit.gopath, d) + '/',
                                os.path.join(gp_host, d) + '/'], check=True)
        rel_gp = os.path.relpath(gp_host, s.root)
        if rel_gp.startswith('..'):
            raise FastError(f'scratch {gp_host} must lie inside the project root {s.root}')
        c_gp = '/work/' + rel_gp.replace(os.sep, '/')
        env = (f'export GOPATH={c_gp} GOOS={s.goos} GOARCH={s.goarch} '
               f'CGO_ENABLED={s.cgo_enabled}; cd {c_gp}; ')
        gcflags = (s.gcflags or '').replace('/build', c_gp)
        build = 'go build -i -x' + (f' -gcflags {shlex.quote(gcflags)}' if gcflags else '') \
            + (f' -ldflags {shlex.quote(s.ldflags)}' if s.ldflags else '') \
            + f' -o {c_gp}/fast.bin {shlex.quote(unit.pkg)}'
        r = self._exec(env + build.replace(' -x', '') + f' && touch src/{self.rel} && ' + build)
        if r.returncode != 0:
            raise FastError(f'set-up build failed:\n{r.stdout[-2000:]}{r.stderr[-2000:]}')
        want_cd = f'{c_gp}/src/{self.pkg_dir}'
        cd, line = None, None
        for ln in r.stderr.split("\n"):
            if ln.startswith('cd '):
                cd = ln[3:].strip()
            elif '/compile ' in ln and cd == want_cd:
                line = ln
                break
        if line is None:
            raise FastError(f'no compile line for {self.pkg_dir} in the go build -x output')
        args = shlex.split(line.replace('$WORK', '/nonexistent-work'))
        cmd, i = [args[0]], 1
        while i < len(args):
            a = args[i]
            if a == '-o':
                cmd += ['-o', 'OUT.a']
                i += 2
            elif a in ('-I', '-trimpath') and i + 1 < len(args) and args[i + 1].startswith('/nonexistent-work'):
                i += 2
            elif a == '-I' and args[i + 1].startswith(c_gp + '/pkg'):
                cmd += ['-I', self.croot + '/pkg' + args[i + 1][len(c_gp + '/pkg'):]]
                i += 2
            else:
                cmd.append(a)
                i += 1
        self.compile = ' '.join(shlex.quote(a) for a in cmd)
        # sources of the package per worker and one pkg/ copy, on the container's own filesystem
        script = (f'rm -rf {self.croot} && mkdir -p {self.croot} && cp -r {c_gp}/pkg {self.croot}/pkg && '
                  + ' && '.join(f'mkdir -p {self.croot}/w{n} && cp {want_cd}/*.go {want_cd}/*.s {self.croot}/w{n}/ 2>/dev/null; true'
                                for n in range(workers)))
        r = self._exec(script)
        if r.returncode != 0:
            raise FastError(f'copy into the container failed:\n{r.stderr[-2000:]}')
        nat = os.environ.get('EXHUME_GO_NATIVE')
        if nat:
            self._use_native(nat, script, want_cd)
        ref = subprocess.run([self.tc.ensure_tool('gocmp'), 'ref', s.image, self.rx],
                             capture_output=True, text=True)
        self.ref = parse_ref(ref.stdout)
        if not self.ref:
            raise FastError(f'no function of the image matches {self.rx}')
        # the object names its own package "": find the prefix that maps it to the image's names
        self.prefix = None
        return self

    def _use_native(self, spec, copy_script, src_dir):
        """Switch candidate compiles to the host-native toolchain SPEC = 'container:GOROOT'."""
        nc, _, ngoroot = spec.partition(':')
        s = self.s

        def nx(script, stdin=None):
            return subprocess.run(['docker', 'exec', '-i', nc, 'sh', '-c', script],
                                  input=stdin, capture_output=True, text=True, timeout=600)
        r = nx(f'GOROOT={ngoroot} {ngoroot}/bin/go env GOHOSTOS GOHOSTARCH GOVERSION 2>/dev/null; GOROOT={ngoroot} {ngoroot}/bin/go version')
        f = r.stdout.split()
        if r.returncode != 0 or len(f) < 2:
            raise FastError(f'no Go toolchain at {ngoroot} in container {nc}: {r.stderr[-300:]}')
        tooldir = f'{ngoroot}/pkg/tool/{f[0]}_{f[1]}'
        want = self._exec('go version').stdout.split()[2:3]
        if want and want[0] not in r.stdout:
            raise FastError(f'native toolchain is not {want[0]}: {r.stdout.strip()[-80:]}')
        # the target's std export data, as the toolchain container has it now
        eroot = self._exec('go env GOROOT').stdout.strip()
        tgt = f'{s.goos}_{s.goarch}'
        # Copy it only when it differs. Every run in the native container shares this directory, so
        # replacing it while another run compiles makes that run's candidates fail to build.
        fp = "cd %s/pkg/" + tgt + " 2>/dev/null && find . -name '*.a' | sort | xargs sha1sum | sha1sum"
        if nx(fp % ngoroot).stdout.split()[:1] != self._exec(fp % eroot).stdout.split()[:1]:
            tmp = f'{ngoroot}/pkg/.{tgt}.{os.getpid()}'
            p1 = subprocess.Popen(['docker', 'exec', self.container, 'tar', 'cf', '-', '-C', f'{eroot}/pkg', tgt], stdout=subprocess.PIPE)
            p2 = subprocess.run(['docker', 'exec', '-i', nc, 'sh', '-c',
                                 f'rm -rf {tmp} && mkdir -p {tmp} && tar xf - -C {tmp} && rm -rf {ngoroot}/pkg/{tgt} && '
                                 f'mv {tmp}/{tgt} {ngoroot}/pkg/{tgt} && rmdir {tmp}'],
                                stdin=p1.stdout, capture_output=True)
            p1.stdout.close()           # or a reader that failed early leaves the writer blocked for ever
            if p2.returncode:
                p1.kill()
            p1.wait()
            if p1.returncode or p2.returncode:
                raise FastError(f'copying std export data to {nc} failed: {p2.stderr[-300:]}')
        r = nx(copy_script)
        if r.returncode != 0:
            raise FastError(f'copy into the native container failed:\n{r.stderr[-2000:]}')
        args = shlex.split(self.compile)
        env = f'GOROOT={ngoroot} GOOS={s.goos} GOARCH={s.goarch} '
        ncompile = env + ' '.join(shlex.quote(a) for a in [f'{tooldir}/compile'] + args[1:])
        # same input, both compilers: the archives must be the same bytes
        with open(os.path.join(self.unit.src_dir, self.rel)) as fh:
            text = fh.read()
        w0 = f'{self.croot}/w0'
        a = self._exec(f'cd {w0} && cat > {shlex.quote(self.file)} && {self.compile} >/dev/null 2>&1 && sha1sum < OUT.a', stdin=text)
        b = nx(f'cd {w0} && cat > {shlex.quote(self.file)} && {ncompile} >/dev/null 2>&1 && sha1sum < OUT.a', stdin=text)
        if a.returncode or b.returncode or a.stdout != b.stdout or not a.stdout.strip():
            raise FastError(f'native compiler in {nc} does not reproduce the toolchain container\'s object '
                            f'({a.stdout.strip()[:12] or a.stderr[-80:]} vs {b.stdout.strip()[:12] or b.stderr[-80:]})')
        self.xc, self.compile, self.native = nc, ncompile, True
        self.native_goroot, self.native_tooldir = ngoroot, tooldir
        self.objdump = f'GOROOT={ngoroot} {ngoroot}/bin/go tool objdump'

    # ------------------------------------------------------------ per candidate
    def evaluate(self, worker, text):
        """(score or None, key). None: did not compile or a wanted function is missing."""
        wdir = f'{self.croot}/w{worker}'
        script = (f'cd {wdir} && cat > {shlex.quote(self.file)} && {self.compile} >/dev/null 2>&1 && '
                  f'{self.objdump} -s {shlex.quote(re.escape(self.bare))} OUT.a')
        try:
            r = subprocess.run(['docker', 'exec', '-i', self.xc, 'sh', '-c', script],
                               input=text, capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            return None, None
        if r.returncode != 0:
            return None, None
        funcs = parse_objdump(r.stdout)
        if self.prefix is None:
            for name in self.ref:
                for sym in funcs:
                    if sym.startswith('"".') and name.endswith(sym[2:]):
                        self.prefix = name[:len(name) - len(sym[2:])]
                        break
                if self.prefix is not None:
                    break
            if self.prefix is None:
                return None, None
        total, h = 0, hashlib.sha1()
        for name in sorted(self.ref):
            insts = funcs.get('""' + name[len(self.prefix):]) if name.startswith(self.prefix) else None
            if insts is None:
                return None, None
            for line, code, rel in insts:
                h.update(b'%d|%s|%s\n' % (line, code.hex().encode(), repr(rel).encode()))
            h.update(b'--\n')
        key = h.hexdigest()
        if key in self.cache:
            return self.cache[key], key
        for name in self.ref:
            insts = funcs['""' + name[len(self.prefix):]]
            masks = None
            if self.structure:
                m = subprocess.run([self.gocmp, 'mask'], input="\n".join(c.hex() for _, c, _ in insts),
                                   capture_output=True, text=True).stdout.split("\n")
                masks = m if len(m) >= len(insts) else None
            total += score_func(self.ref[name], insts, masks)
        self.cache[key] = total
        self.first[key] = min((d for d in (first_diff(self.ref[n], funcs['""' + n[len(self.prefix):]])
                                           for n in self.ref) if d), default=None)
        return total, key
