"""TI C2000 code generation tools for the ti-c2000 profile: the project's settings, Wine job
prefixes, the compiler and the native tools, COFF objects read through ofd2000, relocation
fields, the image and its .cinit records.

The compiler can be a Windows build run under Wine (every version TI publishes; the only choice
before 18.12) or a native build (macOS x86_64 and Linux from 18.12 on). Which one a compiler home
is follows from what its bin/ holds: cl2000.exe means Wine, cl2000 means native. ofd2000,
dis2000, ar2000, asm2000, lnk2000 and hex2000 always come from a native home ([c2000] native):
they read COFF objects of every version and are much faster than a Wine start.

Every Wine run takes a job prefix of its own: an APFS clone (cp -Rc; a reflink copy elsewhere)
of the master prefix under <build>/wine/jobN, held with an flock while in use, so tools can run
side by side without sharing one wineserver. A Wine program that hangs is ended after the
timeout by stopping its prefix's server (wineserver -k), then tried once more."""
import fcntl, glob, os, re, shlex, shutil, socket, struct, subprocess, sys, tempfile
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.dirname(HERE)
EXHUME = os.path.dirname(os.path.dirname(PROFILE))


class ToolError(Exception):
    pass


def _x(p):
    return os.path.expanduser(p) if isinstance(p, str) else p


def _int(v):
    if v is None or isinstance(v, int): return v
    return int(str(v), 0)


# ---- settings -------------------------------------------------------------------------------------
class Settings:
    """What the tools need, from exhume.toml ([project], [toolchain], [binary], [c2000],
    [c2000.gate]) and the profile's profile.toml. Tests build one directly."""

    def __init__(self, root, image=None, base=0, words=None, byte_order='big', fill=0xFFFF,
                 cgt=None, cflags='', tool_cflags='', native=None, wine=None, prefix=None,
                 others=None, rtslib=None, src=None, include=None, exclude=(), map_dir=None,
                 build=None, jobs=8, timeout=300, gate=None, park=0x200000, park_data=0x300000):
        self.root = os.path.abspath(root)
        self.image = image
        self.base, self.words = _int(base), _int(words)
        if byte_order not in ('big', 'little'): raise ToolError('[c2000] byte_order is "big" or "little"')
        self.byte_order, self.fill = byte_order, _int(fill)
        self.cgt = _x(cgt)
        self.cflags = shlex.split(cflags) if isinstance(cflags, str) else list(cflags)
        self.tool_cflags = shlex.split(tool_cflags) if isinstance(tool_cflags, str) else list(tool_cflags)
        self.native, self.wine, self.prefix, self.others = _x(native), _x(wine), _x(prefix), _x(others)
        rl = _x(rtslib)
        if rl and not os.path.isabs(rl) and self.cgt: rl = os.path.join(self.cgt, rl)
        self.rtslib = rl
        self.src = src or os.path.join(self.root, 'src')
        self.include = include or os.path.join(self.src, 'include')
        self.exclude = list(exclude)
        self.map_dir = map_dir or os.path.join(self.root, 'map')
        self.build = build or os.path.join(self.root, 'build')
        self.jobs, self.timeout = int(jobs), int(timeout)
        self.gate = dict(gate or {})
        self.park, self.park_data = _int(park), _int(park_data)

    @classmethod
    def from_config(cls, cfg):
        """From Exhume's tools/config.py Config."""
        c = dict(cfg.raw.get('c2000', {}))
        prof = cfg.profile.get('c2000', {})
        if 'base' not in c: raise ToolError('[c2000] base (the word address of the image\'s first word) is not set')
        rel = lambda p: os.path.normpath(os.path.join(cfg.root, _x(p))) if p else None
        return cls(cfg.root, image=cfg.image, base=c['base'], words=c.get('words'),
                   byte_order=c.get('byte_order', prof.get('byte_order', 'big')),
                   fill=c.get('fill', prof.get('fill', 0xFFFF)), cgt=cfg.home, cflags=cfg.c_opts,
                   tool_cflags=c.get('tool_opts', prof.get('tool_opts', '')), native=rel(c.get('native')),
                   wine=_x(c.get('wine')), prefix=rel(c.get('prefix')), others=_x(c.get('others')),
                   rtslib=c.get('rtslib', prof.get('rtslib')), src=cfg.src, include=cfg.include,
                   exclude=cfg.exclude, map_dir=cfg.map, build=cfg.build, jobs=c.get('jobs', 8),
                   timeout=c.get('timeout', 300), gate=c.get('gate', {}),
                   park=c.get('park', prof.get('park', 0x200000)),
                   park_data=c.get('park_data', prof.get('park_data', 0x300000)))

    def sources(self, exts=('.c', '.asm')):
        """Every source under src/ (not the include directory or [project] exclude), sorted by
        path. Objects are named by their file's stem, so stems must be unique."""
        out = []
        skip = [os.path.abspath(self.include)] + [os.path.abspath(x) for x in self.exclude]
        for d, dirs, files in os.walk(self.src):
            if any(os.path.commonpath([os.path.abspath(d), s]) == s for s in skip):
                dirs[:] = []; continue
            dirs.sort()
            out += [os.path.join(d, n) for n in sorted(files) if n.endswith(exts)]
        seen = {}
        for p in out:
            s = stem(p)
            if s in seen:
                raise ToolError(f'two sources with the stem {s}: {seen[s]} and {p}')
            seen[s] = p
        return out

    def source(self, name):
        """A path, or the file name or stem of a source under src/."""
        if os.path.exists(name): return os.path.abspath(name)
        for p in self.sources():
            b = os.path.basename(p)
            if name in (b, stem(p)): return p
        raise ToolError(f'no source {name} under {self.src}')


def stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def settings(path=None):
    """Settings from the project's exhume.toml (--config PATH, $EXHUME_CONFIG, or the nearest)."""
    sys.path.insert(0, os.path.join(EXHUME, 'tools'))
    import config
    return Settings.from_config(config.load(path))


# ---- per-file lines --------------------------------------------------------------------------------
_DIRECTIVE = re.compile(r'^\s*(?://|/\*|;)\s*(cgt|opts|cflags)\s*:\s*(.*?)\s*(?:\*/)?\s*$', re.M)


def directives(text):
    """A source's own lines, read from its first 4000 characters:
    `/* cgt: 16.9.6.LTS */` (or `// cgt:`, or `; cgt:` in assembly) picks another installed
    compiler; `/* opts: ... */` replaces the default options; `/* cflags: ... */` adds to them.
    Returns {'cgt': str or None, 'opts': [..] or None, 'cflags': [..]}."""
    out = {'cgt': None, 'opts': None, 'cflags': []}
    for m in _DIRECTIVE.finditer(text[:4000]):
        k, v = m.group(1), m.group(2)
        if k == 'cgt' and out['cgt'] is None: out['cgt'] = v.split()[0] if v.split() else None
        elif k == 'opts' and out['opts'] is None: out['opts'] = shlex.split(v)
        elif k == 'cflags': out['cflags'] += shlex.split(v)
    return out


def file_directives(src):
    with open(src, encoding='latin1') as f:
        return directives(f.read(4000))


def cgt_homes(others):
    """Compiler homes matched by a glob of their cl2000 or cl2000.exe."""
    if not others: return []
    return sorted({os.path.dirname(os.path.dirname(p)) for p in glob.glob(_x(others))})


def resolve_cgt(want, others):
    """A compiler home for a version ('16.9.6.LTS', '15.12.3') or a path."""
    if os.path.isdir(_x(want)): return os.path.abspath(_x(want))
    for home in cgt_homes(others):
        b, parent = os.path.basename(home), os.path.basename(os.path.dirname(home))
        if b == want or b.endswith('_' + want) or parent == want: return home
    raise ToolError(f'compiler {want} is not installed (looked in {others})')


def cgt_version(home):
    m = re.search(r'(\d+\.\d+\.\d+(?:\.\w+)?)', os.path.basename(home or ''))
    return m.group(1) if m else os.path.basename(home or '')


# ---- running programs ------------------------------------------------------------------------------
def run(args, cwd=None, check=True, env=None, timeout=300, on_hang=None):
    """Run a program; on a hang, call on_hang (Wine: stop the prefix's server) and try once more."""
    for attempt in (1, 2):
        try:
            p = subprocess.run(args, cwd=cwd, capture_output=True, text=True, errors='replace', env=env,
                               stdin=subprocess.DEVNULL, timeout=timeout)
            break
        except subprocess.TimeoutExpired:
            if on_hang: on_hang()
            if attempt == 2:
                raise ToolError(f'{os.path.basename(args[0])} hung for {timeout} s twice')
    if check and p.returncode:
        raise ToolError(f'{os.path.basename(args[0])} failed ({p.returncode}):\n{p.stdout}{p.stderr}')
    return p


def clone_tree(src, dst):
    """A copy-on-write clone where the file system has one (APFS: cp -Rc; Btrfs, XFS: reflink)."""
    if sys.platform == 'darwin':
        subprocess.run(['cp', '-Rc', src, dst], check=True)
    else:
        subprocess.run(['cp', '-a', '--reflink=auto', src, dst], check=True)


def _server_alive(prefix):
    try:
        st = os.stat(prefix)
    except OSError:
        return False
    sock = f'/tmp/.wine-{os.getuid()}/server-{st.st_dev:x}-{st.st_ino:x}/socket'
    if not os.path.exists(sock): return False
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        s.connect(sock); return True
    except OSError:
        return False
    finally:
        s.close()


class Wine:
    """with Wine(settings) as w: w.run([exe, args...], cwd)

    Takes the first free job prefix of [c2000] jobs (or waits for job 0), cloning the master
    prefix into it on first use. A server that stays 60 s after its last client is started with
    no pipes, then a first program, so a tool's output pipe never stays open behind it."""

    def __init__(self, s, job=None):
        self.s, self.job, self.lock = s, job, None

    def __enter__(self):
        s = self.s
        if not s.wine: raise ToolError('[c2000] wine (the directory holding wine and wineserver) is not set')
        if not s.prefix or not os.path.isdir(s.prefix):
            raise ToolError(f'[c2000] prefix: no Wine prefix at {s.prefix}')
        d = os.path.join(s.build, 'wine')
        os.makedirs(d, exist_ok=True)
        jobs = [self.job] if self.job is not None else range(max(1, s.jobs))
        for n in jobs:
            f = open(os.path.join(d, f'job{n}.lock'), 'w')
            try:
                fcntl.flock(f, fcntl.LOCK_EX | (0 if self.job is not None else fcntl.LOCK_NB))
            except BlockingIOError:
                f.close(); continue
            self.lock, self.n = f, n
            break
        if self.lock is None:
            f = open(os.path.join(d, 'job0.lock'), 'w'); fcntl.flock(f, fcntl.LOCK_EX)
            self.lock, self.n = f, 0
        self.prefix = os.path.join(d, f'job{self.n}')
        if not os.path.exists(os.path.join(self.prefix, 'system.reg')):
            shutil.rmtree(self.prefix, ignore_errors=True)
            clone_tree(s.prefix, self.prefix)
        self.env = dict(os.environ, WINEPREFIX=self.prefix, WINEDEBUG='-all', WINEDLLOVERRIDES='mscoree,mshtml=')
        self.env['PATH'] = s.wine + os.pathsep + self.env.get('PATH', '')
        self.wine = os.path.join(s.wine, 'wine')
        self.server = os.path.join(s.wine, 'wineserver')
        if not _server_alive(self.prefix):
            kw = dict(env=self.env, start_new_session=True, stdin=subprocess.DEVNULL,
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            subprocess.run([self.server, '-p60'], **kw)
            subprocess.run([self.wine, 'cmd', '/c', 'exit'], timeout=s.timeout, **kw)
        return self

    def kill(self):
        subprocess.run([self.server, '-k'], env=self.env, capture_output=True)

    def run(self, args, cwd=None, check=True):
        return run([self.wine] + list(args), cwd=cwd, check=check, env=self.env, timeout=self.s.timeout,
                   on_hang=self.kill)

    def __exit__(self, *a):
        self.lock.close()


def stop_wine(s):
    """Stop the wineserver of every job prefix that has one running."""
    d = os.path.join(s.build, 'wine')
    if not os.path.isdir(d) or not s.wine: return 0
    n = 0
    for name in sorted(os.listdir(d)):
        p = os.path.join(d, name)
        if os.path.isdir(p) and _server_alive(p):
            subprocess.run([os.path.join(s.wine, 'wineserver'), '-k'], env=dict(os.environ, WINEPREFIX=p),
                           capture_output=True)
            n += 1
    return n


# ---- the tools ---------------------------------------------------------------------------------------
class Toolchain:
    def __init__(self, s):
        self.s = s

    def native(self, tool):
        if not self.s.native: raise ToolError('[c2000] native (the bin directory of a native CGT, 18.12 or later) is not set')
        p = os.path.join(self.s.native, tool)
        if not os.path.exists(p): raise ToolError(f'{p}: not found')
        return p

    def run_native(self, tool, args, cwd=None, check=True):
        return run([self.native(tool)] + list(args), cwd=cwd, check=check, timeout=self.s.timeout)

    def home_for(self, src=None, cgt=None):
        """The compiler home for a source: an explicit one, the file's `cgt:` line, or the default."""
        if cgt: return resolve_cgt(cgt, self.s.others)
        if src and not src.endswith('.out'):
            want = file_directives(src)['cgt']
            if want: return resolve_cgt(want, self.s.others)
        if not self.s.cgt: raise ToolError('[toolchain] home (the compiler home) is not set')
        return self.s.cgt

    def tool_in(self, home, tool, args, cwd, wine=None, check=False):
        """Run bin/<tool> of a compiler home: natively, or its .exe under Wine."""
        exe = os.path.join(home, 'bin', tool)
        if os.path.exists(exe) and os.access(exe, os.X_OK):
            return run([exe] + list(args), cwd=cwd, check=check, timeout=self.s.timeout)
        if not os.path.exists(exe + '.exe'): raise ToolError(f'{home}: no bin/{tool} or bin/{tool}.exe')
        if wine is not None: return wine.run([exe + '.exe'] + list(args), cwd=cwd, check=check)
        with Wine(self.s) as w:
            return w.run([exe + '.exe'] + list(args), cwd=cwd, check=check)

    def flags_for(self, src, opts=None, extra=()):
        d = file_directives(src)
        base = list(opts) if opts is not None else (d['opts'] if d['opts'] is not None else list(self.s.cflags))
        return base + d['cflags'] + list(self.s.tool_cflags) + list(extra)

    def compile(self, src, outdir, opts=None, cgt=None, extra=(), keep_asm=False, wine=None):
        """Compile a C file (or assemble a .asm file: cl2000 does both) into outdir; the object path.
        opts replaces the file's own options; cgt names another compiler (a version or a home)."""
        src, outdir = os.path.abspath(src), os.path.abspath(outdir)
        os.makedirs(outdir, exist_ok=True)
        home = self.home_for(src, cgt)
        obj = os.path.join(outdir, stem(src) + '.obj')
        if os.path.exists(obj): os.remove(obj)
        args = self.flags_for(src, opts, extra) + ['-c', '--obj_directory=' + outdir, '--asm_directory=' + outdir]
        for d in (self.s.include, os.path.dirname(src)):
            if d and os.path.isdir(d): args.append('-I' + d)
        args.append('-I' + os.path.join(home, 'include'))
        if keep_asm: args.append('-k')
        p = self.tool_in(home, 'cl2000', args + [src], outdir, wine=wine)
        if p.returncode or not os.path.exists(obj):
            raise ToolError(f'cl2000 failed on {src}:\n{p.stdout}{p.stderr}')
        return obj

    def obj(self, path):
        x = self.run_native('ofd2000', ['-x', '--obj_display=none,sections,relocs,symbols,header', path]).stdout
        with open(path, 'rb') as f: raw = f.read()
        return parse_ofd(x, raw, path)


# ---- COFF objects through ofd2000 ------------------------------------------------------------------
class Section:
    def __init__(self, name, size, data, relocs, kind, page=0, paddr=0, vaddr=0, ptr=0, align=1):
        self.name, self.size, self.data, self.relocs = name, size, data, relocs
        self.kind, self.page, self.paddr, self.vaddr, self.ptr, self.align = kind, page, paddr, vaddr, ptr, align


class Obj:
    def __init__(self, path, sections, symbols):
        self.path, self.sections, self.symbols = path, sections, symbols

    def defined(self):
        """Global symbols defined here: name -> (section, offset)."""
        return {s['name']: (s['section'], s['value']) for s in self.symbols
                if s['sclass'] == 'C_EXT' and s['kind'] == 'defined'}

    def statics(self):
        """Static functions and variables (C_LABEL; C_STAT is accepted too), not section names or
        compiler-made labels ($C$L1): name -> (section, offset)."""
        return {s['name']: (s['section'], s['value']) for s in self.symbols
                if s['sclass'] in ('C_STAT', 'C_LABEL') and s['kind'] == 'defined' and s['section']
                and not s['name'].startswith(('$', '.')) and s['name'] != s['section']}

    def undefined(self):
        return sorted({s['name'] for s in self.symbols if s['sclass'] == 'C_EXT' and s['kind'] == 'undefined'})


def parse_ofd(xml_text, raw, path=''):
    """An Obj from `ofd2000 -x` output and the file's bytes. Sizes are in 16-bit words. ofd2000
    nests <section> inside symbols too, so only the direct children of <ti_coff> are sections."""
    root = ET.fromstring(xml_text.encode('latin1', 'replace'))
    coff = root.find('.//ti_coff')
    if coff is None: raise ToolError(f'{path}: not a COFF object')
    sections, symbols = {}, []
    for s in coff.findall('section'):
        name = s.findtext('name')
        size = int(s.findtext('raw_data_size') or '0', 16)
        ptr = int(s.findtext('file_offsets/raw_data_ptr') or '0', 16)
        kind = 'text' if s.find('text') is not None else 'bss' if s.find('bss') is not None else \
            'data' if s.find('data') is not None else 'other'
        if s.find('copy') is not None or s.find('dummy') is not None: kind = 'copy'
        nw = size // 2
        data = list(struct.unpack('<%dH' % nw, raw[ptr:ptr + 2 * nw])) if nw and ptr and kind != 'bss' else []
        relocs = []
        for r in s.iter('reloc_entry'):
            sym = r.findtext('symbol_relative') or ('(internal)' if r.find('internal') is not None else None)
            relocs.append((int(r.findtext('virtual_addr'), 16), r.findtext('type'), sym))
        pg = s.findtext('page')
        sections[name] = Section(name, nw if kind == 'bss' else len(data), data, relocs, kind,
                                 int(pg, 16) if pg else 0, int(s.findtext('physical_addr') or '0', 16),
                                 int(s.findtext('virtual_addr') or '0', 16), ptr,
                                 int(s.findtext('alignment') or '1', 16))
    for y in coff.findall('symbol_table/symbol'):
        symbols.append(dict(name=y.findtext('name'), kind=y.findtext('kind'), section=y.findtext('section'),
                            sclass=y.findtext('storage_class'), value=int(y.findtext('value') or '0', 16)))
    return Obj(path, sections, symbols)


# the bits of each word a relocation type writes, as (word offset from the relocation, mask)
RELOC_FIELDS = {
    'R_REL22': [(0, 0x003F), (1, 0xFFFF)],      # LCR/LB/MOVL XARn,#22: 6 bits in the opcode, 16 in the next word
    'R_PARTLS6': [(0, 0x003F)],                 # @6-bit direct offset in the opcode
    'R_PARTLS7': [(0, 0x007F)],
    'R_PARTMS16': [(0, 0xFFFF)],                # MOVW DP,#16: the data page
    'R_PARTMS9': [(0, 0x01FF)],
    'R_RELWORD': [(0, 0xFFFF)],
    'R_ANKHI16': [(0, 0xFFFF)],
    'R_ABS16_OC': [(0, 0xFFFF)],
    'RE_XSTFLD': [(0, 0xFFFF)],                 # the store at the end of an expression relocation
    'R_ANKPTR': [(0, 0xFFFF), (1, 0xFFFF)],     # .cinit addresses and pointer data: 32 bits
    'R_RELLONG': [(0, 0xFFFF), (1, 0xFFFF)],
}
RELOC_NOFIELD = ('RE_PUSH', 'RE_PUSHUK', 'RE_ASR', 'RE_ADD', 'RE_SUB', 'RE_AND', 'RE_OR', 'RE_SR',
                 'RE_SL', 'RE_PUSHSK', 'RM_OBJ', 'RM_RANGE', 'RM_DWARF_LENGTH')


def reloc_masks(sec):
    """Per word of sec, the bits relocations write (0 for a word no relocation touches). An
    unknown type is taken to write two whole words."""
    m = [0] * len(sec.data)
    for off, typ, sym in sec.relocs:
        if typ in RELOC_NOFIELD: continue
        for k, bits in RELOC_FIELDS.get(typ, [(0, 0xFFFF), (1, 0xFFFF)]):
            if 0 <= off + k < len(m): m[off + k] |= bits
    return m


def decode(typ, ws, k):
    """The address (or field) a relocation holds in the words ws at k."""
    try:
        if typ == 'R_REL22': return ((ws[k] & 0x3F) << 16) | ws[k + 1]
        if typ in ('R_RELLONG', 'R_ANKPTR'): return ws[k] | ws[k + 1] << 16
        if typ == 'R_PARTMS16': return ws[k] << 6
        if typ == 'R_PARTLS6': return ws[k] & 0x3F
        if typ == 'R_PARTLS7': return ws[k] & 0x7F
        return ws[k]
    except IndexError:
        return None


def derive_bases(sec, load, img):
    """Where the image puts the targets of sec's relocations, for sec's words placed at `load`:
    {symbol: [address of the symbol's offset 0, one per use]}. R_REL22 and 32-bit pointers give
    the address at once; a direct-page access gives it from the MOVW DP page (R_PARTMS16) and
    the 6-bit offset (R_PARTLS6) that follows it for the same symbol."""
    out, page = {}, {}
    for off, typ, sym in sorted(sec.relocs):
        if not sym or typ in RELOC_NOFIELD or off >= len(sec.data): continue
        a = load + off
        if not img.contains(a, 2 if typ in ('R_REL22', 'R_ANKPTR', 'R_RELLONG') else 1): continue
        if typ == 'R_REL22':
            out.setdefault(sym, []).append(decode(typ, img.words(a, 2), 0) - decode(typ, sec.data, off))
        elif typ in ('R_ANKPTR', 'R_RELLONG'):
            out.setdefault(sym, []).append(decode(typ, img.words(a, 2), 0) - decode(typ, sec.data, off))
        elif typ == 'R_PARTMS16':
            page[sym] = (img.W(a), sec.data[off])
        elif typ == 'R_PARTLS6' and sym in page:
            ip, op = page[sym]
            out.setdefault(sym, []).append((ip << 6 | img.W(a) & 0x3F) - (op << 6 | sec.data[off] & 0x3F))
    return out


# ---- the image ---------------------------------------------------------------------------------------
class Image:
    """The original image as 16-bit words at word addresses base .. end."""

    def __init__(self, ws, base):
        self.w, self.base, self.end = tuple(ws), base, base + len(ws)

    @classmethod
    def load(cls, path, base, words=None, byte_order='big'):
        if not path or not os.path.exists(path): raise ToolError(f'no image at {path} ([binary] image)')
        with open(path, 'rb') as f: b = f.read()
        if len(b) % 2: raise ToolError(f'{path}: an odd number of bytes, not 16-bit words')
        ws = struct.unpack(('>' if byte_order == 'big' else '<') + '%dH' % (len(b) // 2), b)
        if words is not None and len(ws) != words:
            raise ToolError(f'{path}: {len(ws)} words, [c2000] words says {words}')
        return cls(ws, base)

    @classmethod
    def of(cls, s):
        return cls.load(s.image, s.base, s.words, s.byte_order)

    def contains(self, a, n=1):
        return self.base <= a and a + n <= self.end

    def W(self, a):
        return self.w[a - self.base]

    def words(self, a, n):
        if not self.contains(a, n): raise ToolError(f'0x{a:X}+{n} is outside the image')
        return list(self.w[a - self.base:a - self.base + n])


def cinit_records(ws):
    """The records of a COFF .cinit table: [(offset, count, header words, record words)], up to the
    0 word that ends it. A record is a count (negative: a 32-bit address follows; positive: a
    16-bit one), the address, then count words of data."""
    out, p = [], 0
    while p < len(ws):
        c = ws[p]
        if c == 0: break
        n, h = (0x10000 - c, 3) if c & 0x8000 else (c, 2)
        if p + h + n > len(ws): break
        out.append((p, n, h, tuple(ws[p:p + h + n])))
        p += h + n
    return out


def fmt_words(ws):
    return ' '.join('%04x' % w for w in ws)


def scratch(s, prefix='c28'):
    d = os.path.join(s.build, 'tmp')
    os.makedirs(d, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix + '-', dir=d)
