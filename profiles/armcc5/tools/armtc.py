"""Arm Compiler 5.06u7 (MDK-Lite) in a long-lived Docker container with Wine.

One container per (toolchain home, project root): the root is mounted at /work and the home
(the directory holding ARM/ARMCC and ARM/sw, from setup-mdk.sh) at /tc. Tools run by
`docker exec`, so builds can run side by side. With runner 'wine' (EXHUME_ARMCC_RUNNER=wine, for
a host with Wine and no Docker) the tools run under the host's Wine on host paths instead. Objects
are Keil's MDK-Lite "ORC" format and are only ever read by Keil's fromelf and armlink."""
import fcntl, hashlib, os, re, subprocess, tempfile
from dataclasses import dataclass

NOISE = re.compile(r'xfeatures|ThreadContextSignals')


def clean(text):
    """Drop the lines Rosetta prints when it trips over an x86 signal frame."""
    return '\n'.join(l for l in text.splitlines() if not NOISE.search(l))


@dataclass
class Sym:
    name: str
    value: int
    bind: str          # Lc, Gb, Wk
    sec: object        # section index, or 'Abs' / 'Ref'
    type: str          # Code, Data, Sect, File, --
    size: int | None


@dataclass
class ObjectInfo:
    sections: dict     # index -> (name, size or None)
    symbols: list
    align: dict = None  # index -> alignment in bytes
    flags: dict = None  # index -> 'SHF_ALLOC + SHF_EXECINSTR' etc.
    types: dict = None  # index -> 'SHT_PROGBITS', 'SHT_NOBITS' ...


_SECT = re.compile(r"^\*\* Section #(\d+) '([^']+)'(?: \((\w+)\))?(?: \[([^\]]*)\])?")
_SIZE = re.compile(r'^\s*Size\s*:\s*(\d+) bytes(?: \(alignment (\d+)\))?')
_SYMNAME = re.compile(r'^\s*\d+\s+(\S+)\s*$')                      # a long name, alone on its line
_SYMREST = re.compile(r'^\s+0x([0-9a-fA-F]+)\s+(Lc|Gb|Wk)\s+(\d+|Abs|Ref)\s+(\S+)\s+\S+(?:\s+0x([0-9a-fA-F]+))?\s*$')
_SYM = re.compile(r'^\s*\d+\s+(\S+)\s+0x([0-9a-fA-F]+)\s+(Lc|Gb|Wk)\s+(\d+|Abs|Ref)\s+(\S+)\s+\S+(?:\s+0x([0-9a-fA-F]+))?\s*$')


def parse_symbols(text):
    """Sections and symbols from `fromelf --text -s` of one object."""
    sections, symbols, cur, align, flags, types = {}, [], None, {}, {}, {}
    pending = None
    for line in text.splitlines():
        if pending:
            m = _SYMREST.match(line)
            if m:
                val, bind, sec, typ, size = m.groups()
                symbols.append(Sym(pending, int(val, 16), bind, int(sec) if sec.isdigit() else sec, typ,
                                   int(size, 16) if size else None))
                pending = None; continue
            pending = None
        m = _SECT.match(line)
        if m:
            cur = int(m.group(1)); sections[cur] = (m.group(2), None)
            types[cur] = m.group(3) or ''; flags[cur] = m.group(4) or ''
            continue
        m = _SIZE.match(line)
        if m and cur is not None and sections[cur][1] is None:
            sections[cur] = (sections[cur][0], int(m.group(1)))
            if m.group(2): align[cur] = int(m.group(2))
            continue
        m = _SYMNAME.match(line)
        if m and not _SYM.match(line):
            pending = m.group(1); continue
        m = _SYM.match(line)
        if m:
            name, val, bind, sec, typ, size = m.groups()
            symbols.append(Sym(name, int(val, 16), bind, int(sec) if sec.isdigit() else sec, typ,
                               int(size, 16) if size else None))
    # a section's size from its own section symbol when the header gave none
    for s in symbols:
        if s.type == 'Sect' and isinstance(s.sec, int) and s.size is not None:
            n, z = sections.get(s.sec, (s.name, None))
            if z is None: sections[s.sec] = (n, s.size)
    return ObjectInfo(sections, symbols, align, flags, types)


class Toolchain:
    def __init__(self, home, root, image='armcc-wine', runner=None):
        self.home = os.path.realpath(home); self.root = os.path.realpath(root); self.image = image
        self.runner = runner or os.environ.get('EXHUME_ARMCC_RUNNER', 'docker')
        if not os.path.isfile(os.path.join(self.home, 'ARM', 'ARMCC', 'bin', 'armcc.exe')):
            raise SystemExit(f'{self.home}: no ARM/ARMCC/bin/armcc.exe (run profiles/armcc5/setup-mdk.sh)')
        self.name = 'armcc5-' + hashlib.sha1(f'{self.home}|{self.root}'.encode()).hexdigest()[:10]
        self._started = False

    def inside(self, host):
        """The container's path for a host path under the project root (the host path under Wine)."""
        if self.runner == 'wine': return os.path.realpath(host)
        rel = os.path.relpath(os.path.realpath(host), self.root)
        if rel.startswith('..'): raise ValueError(f'{host} is outside {self.root}')
        return '/work' if rel == '.' else '/work/' + rel.replace(os.sep, '/')

    def start(self):
        """Start this root's container unless it runs. A lock file makes concurrent cold starts
        (buildd jobs, threads) wait for one another instead of removing each other's container."""
        if self._started or self.runner == 'wine': return
        with open(os.path.join(tempfile.gettempdir(), self.name + '.lock'), 'w') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            r = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', self.name], capture_output=True, text=True)
            if r.stdout.strip() != 'true':
                subprocess.run(['docker', 'rm', '-f', self.name], capture_output=True)
                subprocess.run(['docker', 'run', '-d', '--name', self.name, '--platform', 'linux/amd64',
                                '-v', f'{self.root}:/work', '-v', f'{self.home}:/tc:ro', '-w', '/work',
                                '-e', 'ARM_TOOL_VARIANT=mdk_lite',
                                '-e', 'ARM_PRODUCT_PATH=Z:\\tc\\ARM\\sw\\mappings',
                                self.image, 'sh', '-c', 'wineserver -p -f'],
                               check=True, capture_output=True)
        self._started = True

    def run(self, tool, args, cwd):
        """(exit status, output) of one Keil tool. Exit status above 1 is a crash (Rosetta
        kills an x86 process now and then): retried twice. 0 and 1 are the tool's own answer."""
        self.start()
        if self.runner == 'wine':
            cmd = ['wine', os.path.join(self.home, 'ARM', 'ARMCC', 'bin', f'{tool}.exe')] + args
            kw = dict(cwd=cwd, env=dict(os.environ, ARM_TOOL_VARIANT='mdk_lite', WINEDEBUG='-all',
                      ARM_PRODUCT_PATH='Z:' + self.home.replace('/', '\\') + '\\ARM\\sw\\mappings'))
        else:
            cmd = ['docker', 'exec', '-w', self.inside(cwd), self.name, 'wine', f'/tc/ARM/ARMCC/bin/{tool}.exe'] + args
            kw = {}
        for attempt in range(3):
            r = subprocess.run(cmd, capture_output=True, text=True, **kw)
            out = clean(r.stdout + r.stderr)
            if r.returncode in (0, 1): return r.returncode, out
        return r.returncode, out

    def compile(self, src, obj, opts):
        os.makedirs(os.path.dirname(obj), exist_ok=True)
        if os.path.exists(obj): os.remove(obj)
        rc, out = self.run('armcc', ['-c'] + opts.split() + [self.inside(src), '-o', self.inside(obj)], os.path.dirname(obj))
        return rc == 0 and os.path.exists(obj), out

    def assemble(self, src, obj, opts):
        os.makedirs(os.path.dirname(obj), exist_ok=True)
        if os.path.exists(obj): os.remove(obj)
        import shlex
        rc, out = self.run('armasm', shlex.split(opts) + [self.inside(src), '-o', self.inside(obj)], os.path.dirname(obj))
        return rc == 0 and os.path.exists(obj), out

    def symbols(self, obj):
        rc, out = self.run('fromelf', ['--text', '-s', self.inside(obj)], os.path.dirname(obj))
        if rc: raise RuntimeError(out)
        return parse_symbols(out)

    def link(self, inputs, scatter, axf):
        if os.path.exists(axf): os.remove(axf)
        args = ['--cpu', 'Cortex-M3', '--scatter', self.inside(scatter), '--no_startup', '--keep', '*',
                '--datacompressor', 'off', '--no_scanlib', '--diag_suppress', '6305,6314'] + [self.inside(i) for i in inputs] + ['-o', self.inside(axf)]
        rc, out = self.run('armlink', args, os.path.dirname(axf))
        return rc == 0 and os.path.exists(axf), out

    def tobin(self, axf, outdir):
        """{region name: bytes}, one file per execution region from fromelf --bin."""
        if os.path.isdir(outdir):
            for n in os.listdir(outdir): os.remove(os.path.join(outdir, n))
        os.makedirs(outdir, exist_ok=True)
        rc, out = self.run('fromelf', ['--bin', self.inside(axf), '-o', self.inside(outdir) + '/'], os.path.dirname(axf))
        if rc: raise RuntimeError(out)
        out = {}
        for n in sorted(os.listdir(outdir)):
            with open(os.path.join(outdir, n), 'rb') as f: out[n] = f.read()
        return out

    def stop(self):
        """Remove this root's container (tests with throwaway roots call it)."""
        if self.runner == 'wine': return
        subprocess.run(['docker', 'rm', '-f', self.name], capture_output=True)
        self._started = False


def toolchain(cfg):
    if not cfg.home: raise SystemExit('[toolchain] home is not set (the directory setup-mdk.sh filled)')
    return Toolchain(cfg.home, cfg.root, cfg.profile.get('docker', {}).get('image', 'armcc-wine'))
