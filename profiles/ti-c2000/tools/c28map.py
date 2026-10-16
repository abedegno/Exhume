"""The map of a C2000 image: map/functions.tsv and map/data.tsv.

Each file has a header line and one row per object, tab-separated:

    addr    size    name    kind    notes
    080120  42      loop_step c
    08027A  15      sub_08027A  ramfunc run=0x00B000
    00A400  1       mode    var

addr is a hex word address, size a decimal word count. Function kinds: c, ramfunc (loaded in
flash, run from RAM: the notes give run=0x...), rts (a runtime library member), asm (hand
assembly). Data kinds are free (var, const, switch, cinit ...). Notes hold key=value pairs:
run= (a ramfunc's run address), section= (a section with no symbol, placed at this row, such
as an assembly file's reset branch), alias= (other names of a library entry point, comma-separated).

Code refers to a ramfunc by its run address, so a ramfunc's name resolves to that. A name the
map does not have resolves only when it spells its address: sub_08027A, var_A404, c_0802DA,
ram_A4F0, sw_08013A (or 0x0802DA). That is also the wrong-name trap: a ramfunc's placeholder
that is no longer a map row resolves to the load address it spells, not to its run address."""
import os, re

FCOLS = ['addr', 'size', 'name', 'kind', 'notes']


class Row:
    __slots__ = ('addr', 'size', 'name', 'kind', 'notes')

    def __init__(self, addr, size, name, kind='', notes=''):
        self.addr, self.size, self.name, self.kind, self.notes = addr, size, name, kind, notes

    def __getitem__(self, k):
        return getattr(self, k)

    def __repr__(self):
        return f'Row(0x{self.addr:X}, {self.size}, {self.name!r}, {self.kind!r}, {self.notes!r})'


def parse_tsv(text):
    rows = []
    for line in text.splitlines():
        if line.startswith('#') or not line.strip(): continue
        f = line.rstrip('\n').split('\t')
        if f[0] == 'addr': continue
        f += [''] * (5 - len(f))
        rows.append(Row(int(f[0], 16), int(f[1]) if f[1] else 0, f[2], f[3], f[4]))
    return rows


def read_tsv(path):
    if not os.path.exists(path): return []
    with open(path) as f:
        return parse_tsv(f.read())


def format_tsv(rows):
    out = ['\t'.join(FCOLS)]
    for r in rows:
        out.append('%06X\t%s\t%s\t%s\t%s' % (r.addr, r.size if r.size else '', r.name, r.kind, r.notes))
    return '\n'.join(out) + '\n'


def write_tsv(path, rows):
    with open(path + '.tmp', 'w') as f: f.write(format_tsv(rows))
    os.replace(path + '.tmp', path)


def note(row, key):
    """key=value from a row's notes (run=0x00B000)."""
    m = re.search(r'(?:^|[ ;])' + re.escape(key) + r'=(\S+)', row.notes or '')
    return m.group(1).rstrip(';,') if m else None


def run_addr(row):
    """The address a function runs at: a ramfunc's run= note, else its address."""
    r = note(row, 'run')
    return int(r, 16) if r else row.addr


HEXNAME = re.compile(r'^(?:sub|fn|var|d|c|ram|sw|tbl|g)_([0-9A-Fa-f]{4,6})$')


def cname(sym):
    """A COFF symbol's C name: C names carry a leading underscore in the COFF ABI."""
    return sym[1:] if sym.startswith('_') else sym


class Map:
    def __init__(self, functions, data):
        self.functions, self.data = list(functions), list(data)
        self.frows = {r.name: r for r in self.functions}
        self.drows = {r.name: r for r in self.data}
        t = {}
        for r in self.functions:
            t.setdefault(r.name, run_addr(r))
            for n in (note(r, 'alias') or '').split(','):
                if n: t.setdefault(cname(n), run_addr(r))
        for r in self.data:
            t.setdefault(r.name, r.addr)
        self.symtab = t

    @classmethod
    def from_dir(cls, d):
        return cls(read_tsv(os.path.join(d, 'functions.tsv')), read_tsv(os.path.join(d, 'data.tsv')))

    def resolve(self, sym):
        """A COFF symbol -> its address, or None: a map name (run address for a ramfunc), a name
        that spells its address, or 0x...."""
        n = cname(sym)
        if n in self.symtab: return self.symtab[n]
        if sym in self.symtab: return self.symtab[sym]
        m = HEXNAME.match(n)
        if m: return int(m.group(1), 16)
        if re.match(r'^0x[0-9A-Fa-f]+$', n): return int(n, 16)
        return None

    def section_row(self, section):
        """The row whose notes say section=NAME (a section no symbol places)."""
        for r in self.functions + self.data:
            if note(r, 'section') == section: return r
        return None

    def owners(self):
        """{word address: row} for every row that lies in the image's address space."""
        out = {}
        for r in self.functions + self.data:
            for k in range(r.addr, r.addr + r.size): out.setdefault(k, r)
        return out
