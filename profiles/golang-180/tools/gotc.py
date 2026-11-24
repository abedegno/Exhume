"""Go 1.8.0 toolchain runner, configuration, ELF/.gopclntab fingerprinter, and helper wrapper.

This module backs the `golang-180` Exhume profile:
  - reads project settings from exhume.toml ([project], [binary], [toolchain], [go18], [[go18.unit]])
    merged with profiles/golang-180/profile.toml;
  - runs reproducible Go 1.8.0 linux/amd64 builds in Docker (`golang:1.8.0`) or a local GOROOT;
  - locates or builds the profile's Go helper binaries (`gocmp`, `gofuncs`) from tools/go/;
  - inspects ELF64 Go binaries purely in Python (sections, .gopclntab header, Go version, flags).
"""
import hashlib
import os
import re
import shlex
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.dirname(HERE)
EXHUME = os.path.dirname(os.path.dirname(PROFILE))


class ToolError(Exception):
    pass


def _x(p):
    return os.path.expanduser(p) if isinstance(p, str) else p


def _rel(root, p):
    if not p:
        return None
    p = _x(p)
    return p if os.path.isabs(p) else os.path.normpath(os.path.join(root, p))


class Unit:
    """One Go verification unit: a GOPATH workspace, build package, output binary, and function regex."""

    def __init__(self, name, gopath, pkg='pocmain', out='poc.bin', regex='.*',
                 packages=(), notes='', wip=False):
        self.name = name
        self.gopath = os.path.abspath(gopath)
        self.pkg = pkg
        self.out = out
        self.regex = regex
        self.packages = tuple(packages)
        self.notes = notes
        self.wip = bool(wip)

    @property
    def bin_path(self):
        return self.out if os.path.isabs(self.out) else os.path.join(self.gopath, self.out)

    @property
    def src_dir(self):
        return os.path.join(self.gopath, 'src')

    def source_hash(self):
        """SHA-256 over all .go/.s files in this unit's GOPATH/src (excluding vendor unless needed)."""
        h = hashlib.sha256()
        h.update(f'{self.pkg}\0{self.regex}\0'.encode())
        src = self.src_dir
        if not os.path.isdir(src):
            return h.hexdigest()
        for root, dirs, files in os.walk(src):
            dirs[:] = sorted(d for d in dirs if not d.startswith('.'))
            for fn in sorted(files):
                if not fn.endswith(('.go', '.s')):
                    continue
                p = os.path.join(root, fn)
                rel = os.path.relpath(p, src)
                h.update(rel.encode() + b'\0')
                with open(p, 'rb') as f:
                    h.update(f.read())
                h.update(b'\0')
        return h.hexdigest()

    def __repr__(self):
        return f'Unit({self.name!r}, gopath={self.gopath!r}, pkg={self.pkg!r}, regex={self.regex!r})'


class Settings:
    """Project and profile settings for golang-1.8.0 tools."""

    def __init__(self, root, image=None, build=None, map_dir=None, matched=None,
                 runner='docker', docker_image='golang:1.8.0', platform='linux/amd64',
                 goroot=None, goos='linux', goarch='amd64', cgo_enabled='0',
                 gcflags='-trimpath=/build', ldflags='-s -w', tools_dir=None,
                 units=(), first_party=()):
        self.root = os.path.abspath(root)
        self.image = _rel(self.root, image) if image else None
        self.build = _rel(self.root, build) if build else os.path.join(self.root, 'build')
        self.map_dir = _rel(self.root, map_dir) if map_dir else os.path.join(self.root, 'map')
        self.matched = _rel(self.root, matched) if matched else os.path.join(self.root, 'matched.txt')
        if runner not in ('docker', 'docker-daemon', 'local'):
            raise ToolError(f'[go18] runner must be "docker", "docker-daemon", or "local", got {runner!r}')
        self.runner = runner
        self.docker_image = docker_image
        self.platform = platform
        self.goroot = _rel(self.root, goroot) if goroot else None
        self.goos = goos
        self.goarch = goarch
        self.cgo_enabled = str(cgo_enabled)
        self.gcflags = gcflags
        self.ldflags = ldflags
        self.tools_dir = _rel(self.root, tools_dir) if tools_dir else None
        self.units = list(units)
        self.first_party = tuple(first_party)

    @classmethod
    def from_config(cls, cfg):
        g = dict(cfg.raw.get('go18', {}))
        prof = cfg.profile.get('go', {})
        units = []
        for u in g.get('unit', []):
            if 'name' not in u or 'gopath' not in u:
                raise ToolError('each [[go18.unit]] entry requires "name" and "gopath"')
            units.append(Unit(
                name=u['name'],
                gopath=_rel(cfg.root, u['gopath']),
                pkg=u.get('pkg', g.get('pkg', 'pocmain')),
                out=u.get('out', g.get('out', 'poc.bin')),
                regex=u.get('regex', '.*'),
                packages=u.get('packages', ()),
                notes=u.get('notes', ''),
                wip=u.get('wip', False),
            ))
        if not units and g.get('gopath'):
            units.append(Unit(
                name=g.get('name', cfg.name),
                gopath=_rel(cfg.root, g['gopath']),
                pkg=g.get('pkg', 'pocmain'),
                out=g.get('out', 'poc.bin'),
                regex=g.get('regex', '.*'),
            ))
        return cls(
            root=cfg.root,
            image=cfg.image or cfg.exe_path,
            build=cfg.build,
            map_dir=cfg.map,
            matched=cfg.matched,
            runner=os.environ.get('EXHUME_GO_RUNNER', g.get('runner', 'docker')),
            docker_image=os.environ.get('EXHUME_GO_IMAGE', g.get('docker_image', prof.get('docker_image', 'golang:1.8.0'))),
            platform=g.get('platform', prof.get('platform', 'linux/amd64')),
            goroot=os.environ.get('EXHUME_GO_ROOT', g.get('goroot') or cfg.home),
            goos=g.get('goos', prof.get('goos', 'linux')),
            goarch=g.get('goarch', prof.get('goarch', 'amd64')),
            cgo_enabled=g.get('cgo_enabled', prof.get('cgo_enabled', '0')),
            gcflags=g.get('gcflags', prof.get('gcflags', '-trimpath=/build')),
            ldflags=g.get('ldflags', prof.get('ldflags', '-s -w')),
            tools_dir=g.get('tools_dir'),
            units=units,
            first_party=g.get('first_party', ()),
        )

    def resolve_unit(self, spec=None):
        """Find a configured Unit by name, source file path inside its GOPATH, or package prefix."""
        if not spec:
            if len(self.units) == 1:
                return self.units[0], None
            raise ToolError('specify a unit name or .go source path (configured units: %s)'
                            % (', '.join(u.name for u in self.units) or 'none'))
        for u in self.units:
            if u.name == spec:
                return u, None
        abs_spec = os.path.abspath(spec)
        if os.path.exists(abs_spec):
            for u in self.units:
                try:
                    if os.path.commonpath([abs_spec, u.gopath]) == u.gopath:
                        # Infer package regex if the file lives under gopath/src/<pkg>/
                        rel = os.path.relpath(abs_spec, u.src_dir)
                        if not rel.startswith('..') and os.sep in rel:
                            pkg = os.path.dirname(rel).replace(os.sep, '/')
                            if pkg != u.pkg:
                                return u, '^' + re.escape(pkg) + r'\.'
                        return u, None
                except ValueError:
                    pass
        for u in self.units:
            if spec in u.packages or re.search(u.regex, spec + '.'):
                return u, '^' + re.escape(spec) + r'\.'
        raise ToolError(f'unknown unit or source {spec!r} (configured units: {", ".join(u.name for u in self.units) or "none"})')


def settings(path=None):
    sys.path.insert(0, os.path.join(EXHUME, 'tools'))
    import config
    return Settings.from_config(config.load(path))


class CmpRow:
    __slots__ = ('status', 'name', 'orig_size', 'cand_size')

    def __init__(self, status, name, orig_size=0, cand_size=0):
        self.status = status
        self.name = name
        self.orig_size = orig_size
        self.cand_size = cand_size


class CmpResult:
    def __init__(self, rows, raw_stdout):
        self.rows = rows
        self.raw_stdout = raw_stdout
        self.matched = [r for r in rows if r.status == 'MATCH']
        self.lines_only = [r for r in rows if r.status == 'LINES']
        self.diffs = [r for r in rows if r.status == 'DIFF']
        self.missing = [r for r in rows if r.status == 'MISSING']

    @property
    def total(self):
        return len(self.rows)

    @property
    def ok(self):
        return self.total > 0 and len(self.matched) == self.total

    @property
    def total_bytes(self):
        return sum(r.orig_size for r in self.rows)

    @property
    def matched_bytes(self):
        return sum(r.orig_size for r in self.matched)


class RawResult:
    def __init__(self, total_bytes, rel_bytes, unmasked_diffs, details, raw_stdout):
        self.total_bytes = total_bytes
        self.rel_bytes = rel_bytes
        self.unmasked_diffs = unmasked_diffs
        self.details = details
        self.raw_stdout = raw_stdout

    @property
    def ok(self):
        return self.total_bytes > 0 and self.unmasked_diffs == 0


_CMP_ROW_RE = re.compile(r'^(MATCH|LINES|DIFF|MISSING)\s+(\S+)(?:\s+\((\d+)\s+vs\s+(\d+)\s+bytes\))?')
_RAW_SUM_RE = re.compile(r'^(\d+)\s+bytes;\s+(\d+)\s+differ inside PC-relative displacement fields;\s+(\d+)\s+differ elsewhere')


def parse_cmp_output(text):
    rows = []
    for line in text.splitlines():
        m = _CMP_ROW_RE.match(line)
        if m:
            rows.append(CmpRow(
                m.group(1),
                m.group(2),
                int(m.group(3)) if m.group(3) else 0,
                int(m.group(4)) if m.group(4) else 0,
            ))
    return CmpResult(rows, text)


def parse_raw_output(text):
    details = []
    tot = rel = bad = 0
    found = False
    for line in text.splitlines():
        m = _RAW_SUM_RE.match(line.strip())
        if m:
            tot, rel, bad = int(m.group(1)), int(m.group(2)), int(m.group(3))
            found = True
        elif line.startswith(('UNMASKED ', 'SIZE ', 'MISSING ')):
            details.append(line)
    if not found and details:
        bad = len(details)
    return RawResult(tot, rel, bad, details, text)


class Toolchain:
    """Runs golang:1.8.0 builds and gocmp/gofuncs helpers."""

    def __init__(self, s):
        self.s = s
        self._tools = {}

    def ensure_tool(self, name):
        """Locate prebuilt gocmp/gofuncs or build from profiles/golang-1.8.0/tools/go/<name>."""
        if name in self._tools:
            return self._tools[name]
        candidates = []
        if self.s.tools_dir:
            candidates.append(os.path.join(self.s.tools_dir, name))
        candidates.append(os.path.join(self.s.root, 'bin', name))
        cached = os.path.join(self.s.build, 'go18-tools', name)
        candidates.append(cached)
        src_dir = os.path.join(HERE, 'go', name)
        src_main = os.path.join(src_dir, 'main.go')
        src_mtime = os.path.getmtime(src_main) if os.path.exists(src_main) else 0
        for p in candidates:
            if os.path.isfile(p) and os.access(p, os.X_OK):
                # Prefer configured tools_dir / project bin as-is; rebuild cached copy if stale
                if p != cached or os.path.getmtime(p) >= src_mtime:
                    self._tools[name] = p
                    return p
        if not os.path.isdir(src_dir):
            raise ToolError(f'helper tool {name} not found and source missing at {src_dir}')
        os.makedirs(os.path.dirname(cached), exist_ok=True)
        r = subprocess.run(['go', 'build', '-o', cached, '.'], cwd=src_dir,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise ToolError(f'failed to build helper {name}:\n{r.stdout}{r.stderr}')
        self._tools[name] = cached
        return cached

    def daemon_name(self):
        h = hashlib.sha256(f'{self.s.root}|{self.s.docker_image}|{self.s.platform}'.encode()).hexdigest()[:10]
        return f'exhume-go18-{h}'

    def ensure_daemon(self):
        """Ensure a persistent golang:1.8.0 container mounted at /work is running."""
        name = self.daemon_name()
        r = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', name],
                           capture_output=True, text=True)
        if r.returncode == 0 and r.stdout.strip() == 'true':
            return name
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        r2 = subprocess.run([
            'docker', 'run', '-d', '--rm',
            '--name', name,
            '--platform', self.s.platform,
            '-v', f'{self.s.root}:/work',
            '-w', '/work',
            self.s.docker_image,
            'sleep', 'infinity',
        ], capture_output=True, text=True)
        if r2.returncode != 0:
            raise ToolError(f'failed to start persistent docker container {name}:\n{r2.stdout}{r2.stderr}')
        return name

    def stop_daemon(self):
        name = self.daemon_name()
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)

    def build(self, unit, out_rel=None, extra_gcflags='', capture_asm=False, timeout=300):
        """Build unit with golang:1.8.0; returns (binary_path, stderr_or_asm)."""
        out_name = out_rel or unit.out
        if os.path.isabs(out_name):
            try:
                out_in_gopath = os.path.relpath(out_name, unit.gopath)
                if out_in_gopath.startswith('..'):
                    raise ToolError(f'output path {out_name} must lie inside GOPATH {unit.gopath}')
            except ValueError as exc:
                raise ToolError(str(exc)) from exc
        else:
            out_in_gopath = out_name
        out_host = os.path.join(unit.gopath, out_in_gopath)
        os.makedirs(os.path.dirname(out_host), exist_ok=True)

        gcflags = self.s.gcflags
        if extra_gcflags:
            gcflags = (gcflags + ' ' + extra_gcflags).strip()
        if capture_asm and '-S' not in shlex.split(gcflags):
            gcflags = (gcflags + ' -S').strip()

        if self.s.runner == 'docker-daemon':
            rel_gp = os.path.relpath(unit.gopath, self.s.root)
            if rel_gp.startswith('..'):
                raise ToolError(f'docker-daemon requires unit GOPATH {unit.gopath} inside project root {self.s.root}')
            cname = self.ensure_daemon()
            c_gp = '/work' if rel_gp == '.' else '/work/' + rel_gp.replace(os.sep, '/')
            c_out = c_gp + '/' + out_in_gopath.replace(os.sep, '/')
            d_gcflags = gcflags.replace('/build', c_gp)
            cmd = [
                'docker', 'exec',
                '-w', c_gp,
                '-e', f'GOPATH={c_gp}',
                '-e', f'GOOS={self.s.goos}',
                '-e', f'GOARCH={self.s.goarch}',
                '-e', f'CGO_ENABLED={self.s.cgo_enabled}',
                cname,
                'go', 'build', '-i',
            ]
            if capture_asm:
                # Recompile only this unit's packages (not stdlib) so -S takes ~0.5s instead of ~15s
                for p in (unit.packages or ()):
                    pkg_dir = os.path.join(unit.src_dir, p)
                    if os.path.isdir(pkg_dir):
                        for fn in os.listdir(pkg_dir):
                            if fn.endswith('.go'):
                                os.utime(os.path.join(pkg_dir, fn), None)
            if d_gcflags:
                cmd += ['-gcflags', d_gcflags]
            if self.s.ldflags:
                cmd += ['-ldflags', self.s.ldflags]
            cmd += ['-o', c_out, unit.pkg]
            env = None
        elif self.s.runner == 'docker':
            container_out = '/build/' + out_in_gopath.replace(os.sep, '/')
            cmd = [
                'docker', 'run', '--rm',
                '--platform', self.s.platform,
                '-v', f'{unit.gopath}:/build',
                '-w', '/build',
                '-e', 'GOPATH=/build',
                '-e', f'GOOS={self.s.goos}',
                '-e', f'GOARCH={self.s.goarch}',
                '-e', f'CGO_ENABLED={self.s.cgo_enabled}',
                self.s.docker_image,
                'go', 'build',
            ]
            if capture_asm:
                cmd.append('-a')
            if gcflags:
                cmd += ['-gcflags', gcflags]
            if self.s.ldflags:
                cmd += ['-ldflags', self.s.ldflags]
            cmd += ['-o', container_out, unit.pkg]
            env = None
        else:
            if not self.s.goroot:
                raise ToolError('[go18] runner = "local" requires [go18] goroot (or [toolchain] home)')
            gobin = os.path.join(self.s.goroot, 'bin', 'go')
            if not os.path.exists(gobin):
                raise ToolError(f'go binary not found at {gobin}')
            local_gcflags = gcflags.replace('/build', unit.gopath)
            cmd = [gobin, 'build']
            if capture_asm:
                cmd.append('-a')
            if local_gcflags:
                cmd += ['-gcflags', local_gcflags]
            if self.s.ldflags:
                cmd += ['-ldflags', self.s.ldflags]
            cmd += ['-o', out_host, unit.pkg]
            env = dict(os.environ)
            env.update(
                GOROOT=self.s.goroot,
                GOPATH=unit.gopath,
                GOOS=self.s.goos,
                GOARCH=self.s.goarch,
                CGO_ENABLED=self.s.cgo_enabled,
            )

        r = subprocess.run(cmd, cwd=unit.gopath, env=env, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 or not os.path.exists(out_host):
            raise ToolError(f'go1.8.0 build failed for {unit.name} ({unit.pkg}):\n{r.stdout}{r.stderr}')
        return out_host, r.stderr

    def cmp(self, orig, cand, regex, verbose=False, first_window=0):
        gocmp = self.ensure_tool('gocmp')
        cmd = [gocmp]
        if verbose:
            cmd.append('-v')
        if first_window > 0:
            cmd += ['-first', str(first_window)]
        cmd += ['cmp', orig, cand, regex]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise ToolError(f'gocmp cmp failed:\n{r.stdout}{r.stderr}')
        return parse_cmp_output(r.stdout)

    def types(self, binary, regex):
        gl = self.ensure_tool('golift')
        r = subprocess.run([gl, 'types', binary, regex], capture_output=True, text=True)
        if r.returncode != 0:
            raise ToolError(f'golift types failed:\n{r.stdout}{r.stderr}')
        return r.stdout

    def lift(self, binary, regex):
        gl = self.ensure_tool('golift')
        r = subprocess.run([gl, 'lift', binary, regex], capture_output=True, text=True)
        if r.returncode != 0:
            raise ToolError(f'golift lift failed:\n{r.stdout}{r.stderr}')
        return r.stdout

    def raw(self, orig, cand, regex):
        gocmp = self.ensure_tool('gocmp')
        r = subprocess.run([gocmp, 'raw', orig, cand, regex], capture_output=True, text=True)
        if r.returncode != 0:
            raise ToolError(f'gocmp raw failed:\n{r.stdout}{r.stderr}')
        return parse_raw_output(r.stdout)

    def dis(self, binary, regex, show_addr=False):
        gocmp = self.ensure_tool('gocmp')
        cmd = [gocmp]
        if show_addr:
            cmd.append('-addr')
        cmd += ['dis', binary, regex]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise ToolError(f'gocmp dis failed:\n{r.stdout}{r.stderr}')
        return r.stdout

    def str_at(self, binary, addr, length):
        gocmp = self.ensure_tool('gocmp')
        r = subprocess.run([gocmp, 'str', binary, str(addr), str(length)], capture_output=True, text=True)
        if r.returncode != 0:
            raise ToolError(f'gocmp str failed:\n{r.stdout}{r.stderr}')
        return r.stdout

    def gofuncs(self, binary):
        gf = self.ensure_tool('gofuncs')
        r = subprocess.run([gf, binary], capture_output=True, text=True)
        if r.returncode != 0:
            raise ToolError(f'gofuncs failed:\n{r.stdout}{r.stderr}')
        return r.stdout


# ---- Pure-Python ELF64 / .gopclntab fingerprinter ------------------------------------------------

_ELF_MACHINES = {
    3: 'x86',
    40: 'arm',
    62: 'amd64',
    183: 'arm64',
}

_PCLN_MAGICS = {
    0xfffffffb: 'go1.2-1.15',
    0xfffffffa: 'go1.16-1.17',
    0xfffffff0: 'go1.18-1.19',
    0xfffffff1: 'go1.20+',
}


def inspect_elf_go(data):
    """Inspect raw ELF bytes for Go compiler/linker metadata. Pure Python (no external tools).

    Returns a dict of ELF and Go facts, or raises ValueError if data is not a valid ELF image.
    """
    if len(data) < 64 or data[:4] != b'\x7fELF':
        raise ValueError('not an ELF binary')
    ei_class = data[4]
    ei_data = data[5]
    if ei_class != 2 or ei_data != 1:
        raise ValueError(f'unsupported ELF class/endianness ({ei_class}, {ei_data}); expected ELF64 little-endian')

    e_type, e_machine, _ver, e_entry, e_phoff, e_shoff, _flags, _ehsize, e_phentsize, e_phnum, \
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<HHIQQQIHHHHHH', data, 16)

    # Program headers (check PT_INTERP / PT_DYNAMIC for CGO / dynamic linking)
    has_interp = False
    has_dynamic = False
    if e_phoff and e_phentsize >= 4 and e_phoff + e_phnum * e_phentsize <= len(data):
        for i in range(e_phnum):
            p_type = struct.unpack_from('<I', data, e_phoff + i * e_phentsize)[0]
            if p_type == 3:  # PT_INTERP
                has_interp = True
            elif p_type == 2:  # PT_DYNAMIC
                has_dynamic = True

    sections = {}
    if e_shoff and e_shnum and e_shentsize >= 64 and e_shoff + e_shnum * e_shentsize <= len(data):
        raw_secs = []
        for i in range(e_shnum):
            off = e_shoff + i * e_shentsize
            sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size = struct.unpack_from('<IIQQQQ', data, off)
            raw_secs.append((sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size))
        strtab = b''
        if e_shstrndx < len(raw_secs):
            _, _, _, _, st_off, st_size = raw_secs[e_shstrndx]
            strtab = data[st_off:st_off + st_size]
        for sh_name, sh_type, sh_flags, sh_addr, sh_offset, sh_size in raw_secs:
            end = strtab.find(b'\x00', sh_name)
            name = strtab[sh_name:end].decode('latin1', 'replace') if end >= 0 else ''
            if name:
                sections[name] = {
                    'type': sh_type,
                    'flags': sh_flags,
                    'addr': sh_addr,
                    'offset': sh_offset,
                    'size': sh_size,
                }

    pcln_info = None
    if '.gopclntab' in sections:
        sec = sections['.gopclntab']
        off, sz = sec['offset'], sec['size']
        if off + 16 <= len(data) and sz >= 16:
            magic, pad1, pad2, min_lc, ptr_size = struct.unpack_from('<IBBBB', data, off)
            nfunc = None
            if magic == 0xfffffffb and ptr_size == 8:
                nfunc = struct.unpack_from('<Q', data, off + 8)[0]
            elif magic in (0xfffffffa, 0xfffffff0, 0xfffffff1) and ptr_size == 8:
                nfunc = struct.unpack_from('<Q', data, off + 8)[0]
            pcln_info = {
                'magic': magic,
                'format': _PCLN_MAGICS.get(magic, f'unknown({magic:#x})'),
                'min_lc': min_lc,
                'ptr_size': ptr_size,
                'nfunc': nfunc,
                'size': sz,
            }

    # Detect Go runtime version strings in .rodata or whole binary
    rodata = b''
    if '.rodata' in sections:
        s = sections['.rodata']
        rodata = data[s['offset']:s['offset'] + s['size']]
    search_buf = rodata or data
    # Go packs string literals in .rodata without NUL separators (sorted by length, e.g. "...getwdgo1.8hosts...")
    ver_hits = sorted(set(
        m.group(0).decode('ascii')
        for m in re.finditer(rb'go1\.\d{1,2}(?:\.\d{1,2})?(?:(?:rc|beta)\d+)?(?![0-9])', search_buf)
    ))
    # Build ID from .note.go.buildid if present
    build_id = None
    if '.note.go.buildid' in sections:
        s = sections['.note.go.buildid']
        note = data[s['offset']:s['offset'] + s['size']]
        if len(note) >= 16:
            namesz, descsz = struct.unpack_from('<II', note, 0)
            n_align = (namesz + 3) & ~3
            desc = note[12 + n_align:12 + n_align + descsz]
            build_id = desc.rstrip(b'\x00').decode('latin1', 'replace')

    return {
        'arch': _ELF_MACHINES.get(e_machine, f'machine({e_machine})'),
        'entry': e_entry,
        'size': len(data),
        'dynamic': has_interp or has_dynamic,
        'stripped_symtab': '.symtab' not in sections,
        'stripped_dwarf': '.debug_info' not in sections,
        'has_gopclntab': '.gopclntab' in sections,
        'pclntab': pcln_info,
        'text_size': sections['.text']['size'] if '.text' in sections else 0,
        'rodata_size': sections['.rodata']['size'] if '.rodata' in sections else 0,
        'go_versions': ver_hits,
        'build_id': build_id,
        'sections': sorted(sections.keys()),
    }
