"""Project configuration for every Exhume tool.

A project is described by an `exhume.toml` (see examples/uw2/exhume.toml). Tools find it from
`--config PATH` (handled by each tool that accepts it), then the EXHUME_CONFIG environment
variable, then the first exhume.toml in the current directory or a parent. Relative paths in
it are relative to [project] root, and root is relative to the config file. `~` and
`${VAR}` / `${VAR:-default}` are expanded in every string; `${EXHUME}` is
this Exhume checkout.

The config names the toolchain profile (profiles/<name>/profile.toml), which holds what is
true of the compiler, assembler and linker rather than of one game: command lines, name
rules, the library-name patterns, the linker's call rewrite.

    python3 tools/config.py [KEY]     prints the resolved config, or one value (for scripts)
"""
import os, re, sys, struct, tomllib

EXHUME = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# ${EXHUME} in exhume.toml names this checkout (a project's config can point at its examples)
os.environ.setdefault('EXHUME', EXHUME)


def _expand(s):
    def var(m):
        name, default = m.group(1), m.group(3)
        v = os.environ.get(name)
        return v if v else (default or '')
    s = re.sub(r'\$\{(\w+)(:-([^}]*))?\}', var, s)
    return os.path.expanduser(s)


def _walk(v):
    if isinstance(v, str): return _expand(v)
    if isinstance(v, list): return [_walk(x) for x in v]
    if isinstance(v, dict): return {k: _walk(x) for k, x in v.items()}
    return v


def find(explicit=None):
    if explicit: return os.path.abspath(explicit)
    if os.environ.get('EXHUME_CONFIG'): return os.path.abspath(os.path.expanduser(os.environ['EXHUME_CONFIG']))
    d = os.getcwd()
    while True:
        p = os.path.join(d, 'exhume.toml')
        if os.path.exists(p): return p
        if os.path.dirname(d) == d: break
        d = os.path.dirname(d)
    sys.exit('no exhume.toml: pass --config, set EXHUME_CONFIG, or run inside a project')


class Config:
    def __init__(self, path):
        self.file = path
        raw = _walk(tomllib.load(open(path, 'rb')))
        self.raw = raw
        p = raw.get('project', {})
        here = os.path.dirname(path)
        self.name = p.get('name', 'project')
        self.root = os.path.normpath(os.path.join(here, p.get('root', '.')))
        R = lambda k, d: os.path.normpath(os.path.join(self.root, p.get(k, d)))
        self.src = R('src', 'src')
        # shared headers: sources include them as "name.h"; the build stages them beside each
        # source (tools/build.py) and a source's hash covers the ones it includes (srcdeps.py)
        self.include = os.path.normpath(os.path.join(self.root, p['include'])) if p.get('include') \
            else os.path.join(self.src, 'include')
        # Exhume's runtime (runtime/README.md), the porting harness a project compiles where it
        # is, never copied: [port] runtime, default this checkout's
        pt = raw.get('port', {})
        self.runtime = os.path.normpath(os.path.join(self.root, pt['runtime'])) if pt.get('runtime') \
            else os.path.join(EXHUME, 'runtime')
        # the shared headers the DOS build stages and a source's hash follows: the project's
        # first, then the runtime's (portable.h), so a project's own header of a name wins
        self.includes = [self.include, os.path.join(self.runtime, 'include')]
        # directories under src that hold no sources for the DOS build (a port's own code)
        self.exclude = [os.path.normpath(os.path.join(self.src, x)) for x in p.get('exclude', [])]
        self.targets = R('targets', 'targets')
        self.symbols = R('symbols', 'symbols.tsv')
        self.matched = R('matched', 'matched.txt')
        self.map = R('map', 'map')
        self.build = R('build', 'build')
        self.queue = os.path.normpath(os.path.join(self.build, p.get('queue', 'queue')))
        b = raw.get('binary', {})
        self.exe_path = os.path.join(self.root, b['exe']) if 'exe' in b else None
        self.dgroup_para = b.get('dgroup_para')
        self.overlay_re = re.compile(b['overlay_segment']) if b.get('overlay_segment') else None
        self.listing = os.path.join(self.root, b['listing']) if b.get('listing') else None
        self.listing_para_bias = b.get('listing_para_bias', 0)
        self.listing_stub_prefix = b.get('listing_stub_prefix', 'stub')
        # load paragraphs of listing segments whose names carry none (UW1's seg051, seg004)
        self.listing_segments = {k: int(v) for k, v in b.get('listing_segments', {}).items()}
        t = raw.get('toolchain', {})
        self.profile_name = t.get('profile', 'borland-tc101')
        self.profile_dir = os.path.join(EXHUME, 'profiles', self.profile_name)
        self.profile = _walk(tomllib.load(open(os.path.join(self.profile_dir, 'profile.toml'), 'rb')))
        self.home = os.path.join(self.root, t['home']) if t.get('home') else None
        self.stage = [os.path.join(self.root, x) for x in t.get('stage', [])]
        prof_c = self.profile.get('c', {}); prof_a = self.profile.get('asm', {})
        self.c_opts = t.get('c_opts', prof_c.get('default_opts', ''))
        self.asm_opts = t.get('asm_opts', prof_a.get('default_opts', ''))
        # the DOS the toolchain runs in (tools/dosbackend.mjs) and how many sessions at once
        # (tools/dosbatch.py); the environment's EXHUME_DOS and EXHUME_DOS_SESSIONS win, and
        # setting them here passes the choice to every tool and DOS run this one starts
        self.dos = t.get('dos')
        self.dos_sessions = t.get('sessions')
        if self.dos and not os.environ.get('EXHUME_DOS'): os.environ['EXHUME_DOS'] = str(self.dos)
        if self.dos_sessions and not os.environ.get('EXHUME_DOS_SESSIONS'):
            os.environ['EXHUME_DOS_SESSIONS'] = str(self.dos_sessions)
        n = raw.get('names', {})
        self.original_label = n.get('original_label', 'original')
        self.original_symbols = os.path.join(self.root, n['original_symbols']) if n.get('original_symbols') else None
        self.sibling = raw.get('sibling', {})
        for k in ('image', 'symbols'):
            if self.sibling.get(k): self.sibling[k] = os.path.join(self.root, self.sibling[k])
        self.mapcfg = raw.get('map', {})
        self.run = raw.get('run', {})
        self._exe = None

    # ---- the binary ---------------------------------------------------------------
    @property
    def exe(self):
        if self._exe is None:
            if not self.exe_path or not os.path.exists(self.exe_path):
                sys.exit(f'the original executable is not at {self.exe_path}: set [binary] exe')
            self._exe = open(self.exe_path, 'rb').read()
        return self._exe

    def mz(self):
        """MZ facts: header size, end of the load module, relocation table as file offsets."""
        e = self.exe; w = lambda i: struct.unpack_from('<H', e, i)[0]
        hdr = w(8) * 16; cblp, cp = w(2), w(4)
        end = (cp - 1) * 512 + cblp if cblp else cp * 512
        rel = {}
        for i in range(w(6)):
            o, s = struct.unpack_from('<HH', e, w(0x18) + 4 * i)
            rel[hdr + s * 16 + o] = w(hdr + s * 16 + o)
        return dict(hdr=hdr, end=end, relocs=rel)

    def segtable(self):
        """Borland VROOMM overlay manager's segment table, from the FBOV block after the load
        module: (file offset of the table, entries of (paragraph, end, flags, start)). None
        when the EXE has no FBOV block."""
        e = self.exe; m = self.mz()
        if e[m['end']:m['end'] + 4] != b'FBOV': return None
        tab, n = struct.unpack_from('<II', e, m['end'] + 8)
        return tab, [struct.unpack_from('<4H', e, tab + 8 * i) for i in range(n)]

    @property
    def dgroup_file(self):
        return self.mz()['hdr'] + self.dgroup_para * 16

    def overlay_index(self, seg):
        """The overlay's segment table index for a target name, or None if it is resident."""
        if not self.overlay_re: return None
        m = self.overlay_re.fullmatch(seg)
        return int(m.group(1)) if m else None

    # ---- names --------------------------------------------------------------------
    def mangle(self, name):
        """The name as the object file spells it: prefix, then cut to the profile's limit."""
        c = self.profile.get('c', {})
        pre = c.get('public_prefix', '_'); lim = c.get('name_limit', 32)
        return (pre + name)[:lim + len(pre)]

    def is_code_class(self, cls):
        """True when an object's segment class marks code: it ends in the profile's
        code_class, in any case. A program's assembly modules may use other classes ending
        in CODE (UW2: ASMCODE, and lower-case code), which the linker orders and flags
        differently (profiles/borland-tc101/linker.md, "Segment classes"), so never test
        for the class spelled exactly."""
        suffix = self.profile.get('c', {}).get('code_class', 'CODE').upper()
        return bool(cls) and cls.upper().endswith(suffix)

    def is_library_name(self, name):
        return any(re.search(p, name) for p in self.profile.get('link', {}).get('library_name_patterns', []))

    def original_names(self):
        """Names from the sibling build's symbol table (with and without the trailing
        underscore Watcom adds), which count as original."""
        out = set()
        if self.original_symbols and os.path.exists(self.original_symbols):
            for l in open(self.original_symbols):
                n = l.split('\t')[0]; out.add(n); out.add('_' + n.rstrip('_'))
        return out

    # ---- project files -----------------------------------------------------------
    def stem(self, src):
        return os.path.splitext(os.path.basename(src))[0].upper()

    def obj(self, src_or_stem):
        s = self.stem(src_or_stem) if os.sep in src_or_stem or '.' in src_or_stem else src_or_stem
        return os.path.join(self.build, s, s + '.OBJ')

    def directives(self, src):
        """(target, opts) from a source's `/* target: X */` and `/* opts: ... */` lines."""
        text = open(src, encoding='latin1').read(4000)
        t = re.search(r'/\*\s*target:\s*(\w+)\s*\*/', text)
        o = re.search(r'/\*\s*opts:\s*([^*]+?)\s*\*/', text)
        asm = src.upper().endswith('.ASM')
        return (t.group(1) if t else None, o.group(1) if o else (self.asm_opts if asm else self.c_opts))

    def load_targets(self, seg):
        """A target table: (file base, size, rows of (name, listing name, offset, size), org)
        and any '# far NAME PPPP:OOOO' placement hints."""
        rows = []; base = size = None; org = 0; far = {}
        for l in open(os.path.join(self.targets, seg + '.tsv')):
            m = re.match(r'# segment \S+ base 0x([0-9A-Fa-f]+) size 0x([0-9A-Fa-f]+)(?: org 0x([0-9A-Fa-f]+))?', l)
            if m: base, size = int(m.group(1), 16), int(m.group(2), 16); org = int(m.group(3) or '0', 16); continue
            m = re.match(r'# far (\S+) ([0-9A-F]{4}):([0-9A-F]{4})', l)
            if m: far[m.group(1)] = (int(m.group(2), 16), int(m.group(3), 16)); continue
            if l.startswith('#') or not l.strip(): continue
            c, ida, o, s = l.rstrip('\n').split('\t')[:4]; rows.append((c, ida, int(o, 16), int(s, 16)))
        self.far_hints = far
        return base, size, rows, org

    def load_symbols(self):
        """symbols.tsv as {name: (address text, source label)}."""
        out = {}
        if os.path.exists(self.symbols):
            for l in open(self.symbols):
                if l.startswith('#') or not l.strip(): continue
                f = l.rstrip('\n').split('\t'); out[f[0]] = (f[1], f[2] if len(f) > 2 else '')
        return out

    def matched_set(self):
        if not os.path.exists(self.matched): return set()
        return {l.strip() for l in open(self.matched) if l.strip() and not l.startswith('#')}


_cache = {}
def load(explicit=None):
    p = find(explicit)
    if p not in _cache: _cache[p] = Config(p)
    return _cache[p]


def pop_config(argv):
    """Remove `--config PATH` from an argument list (a copy) and return (path, rest)."""
    a = list(argv)
    if '--config' in a:
        i = a.index('--config'); p = a[i + 1]; del a[i:i + 2]; return p, a
    return None, a


if __name__ == '__main__':
    path, rest = pop_config(sys.argv[1:])
    c = load(path)
    if rest:
        v = getattr(c, rest[0], None)
        print(v if v is not None else c.raw.get(rest[0], ''))
    else:
        for k in ('file', 'root', 'src', 'include', 'targets', 'symbols', 'matched', 'map', 'build', 'queue', 'exe_path',
                  'listing', 'profile_dir', 'home', 'stage', 'c_opts', 'asm_opts'):
            print(f'{k:12} {getattr(c, k)}')
