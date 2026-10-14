"""The whole-image gate for a linked image (the armcc5 profile's gate).

    python3 tools/gate.py [--config PATH] check [--piece N] [--jobs N]   (Exhume's gate.py hands over)
    python3 profiles/armcc5/tools/armgate.py [--config PATH] [--piece N] [--jobs N]

Rebuilds the image from the sources and compares it with the original, byte for byte, then
writes it (<build>/gate/rebuilt.hex, or .bin for a raw image) in the original file's own layout
and compares the files. MDK-Lite refuses to link more than 32 KB, so the image is linked in
pieces, each at its own address:

- Pieces (the pieces file, [gate] pieces): runs of consecutive map rows, each starting on a
  4-byte boundary at a section start, each small enough for one Lite link (the 32 KB counts
  every section linked, the piece's files' other sections too). A piece is linked *naturally*:
  one execution region at its start holding the function sections of every row in it, in no
  particular order, so armlink chooses order and padding as the original link did. Every other
  section of the piece's files is placed FIXED at its map address and every other symbol comes
  from a symdefs file, so each reference resolves to the image's own address.
- The startup piece (startup = true): the startup file's vector table first, then MicroLIB's
  entry code, the startup code and the library members, each where the original link put them.
  The library comes from the toolchain itself; a stub requests what the project's objects
  reference, so the members load in the original's resolution passes, and the image's own RW
  data is linked so armlink adds the same decompressor.
- The data stage (armdata.py): const data, the region table and the compressed RW image, from
  every source that owns a data row, with only its data kept.

Names: armlink lays an execution region out by section name, so a placeholder (sub_ADDR) is
renamed at compile time to a name that sorts between its neighbours (link_names). Owners: a
row's file is the map's file column, else the one source defining it; a named row nobody
defines is a library member. A function or global a file defines although another file owns
it (a partial file's copy) is renamed in that file and linked out of the way.

The pieces file (default pieces.toml in the project root; or [[gate.piece]] in exhume.toml):

    known_diffs = ["sub_08004120"]     # functions not matched yet: allowed to differ
    [[piece]]
    start = "08000000"
    end = "080000CC"
    startup = true
    [[piece]]
    start = "080000CC"
    end = "0800061C"
    [data]                             # optional; both default from the image
    start = "08000758"
    end = "08000818"

Each piece's bytes and every function in it are compared; a function that differs passes only
if known_diffs lists it. With Wine, the compiles and the piece links run in worker processes,
each with its own Wine prefix and a persistent wineserver (`jobs`). docs/config.md has [gate]."""
import collections, dataclasses, os, re, shutil, subprocess, sys, time, tomllib
from dataclasses import dataclass, field
import armtc, armmap, armmatch, ehex, rwdata

LIB = '<library>'                  # owner of a row the toolchain's library supplies
RAM = 0x20000000
LIBREST = 0x0F800000               # library data nothing compares: out of the way
STUB, PAD = 'gate_libstub', 'gate_pad'
_UNDEF = re.compile(r"L6218E: Undefined symbol (\S+) \(referred from (\S+?)\)")
_OVERLAP = re.compile(r'Load region (LR_\w+) .*? overlaps with Load region (LR_\w+)')
# armlink --symbols: "    name   0x08000035   Thumb Code   28  init.o(.text)"
_LSYM = re.compile(r'^\s{4}(\S+)\s+0x([0-9a-fA-F]{8})\s+(Section|Thumb Code|ARM Code|Data|Number)\s+(\d+)\s+(\S+?)\((.+)\)\s*$')
# armlink --map: "    0x08000058   0x00000056   Code   RO   69    .text   mc_w.l(__dczerorl2.o)"
_LMAP = re.compile(r'^\s*0x([0-9a-fA-F]{8})\s+0x([0-9a-fA-F]{8})\s+(Code|Data|Zero|PAD)\s+(RO|RW)\s+\d+\s+(?:\*\s+)?(\S+)\s+(\S+)\s*$')
_LOAD = re.compile(r'Load Region (\w+) \(Base: 0x([0-9a-fA-F]+), Size: 0x([0-9a-fA-F]+).*?(?:COMPRESSED\[0x([0-9a-fA-F]+)\])?\)')


# ---- configuration ---------------------------------------------------------------------------
@dataclass
class Piece:
    start: int
    end: int
    startup: bool = False


@dataclass
class Spec:
    pieces: list
    known: set
    data_start: int = None
    data_end: int = None
    startup: str = None            # the startup source, relative to the root
    library: str = '*.l'           # scatter module pattern for the toolchain's libraries
    libpath: str = None
    exclude: list = field(default_factory=list)
    jobs: int = 1
    allow_gaps: bool = False
    aside: list = field(default_factory=list)     # library objects linked out of the startup piece
    symdefs: str = ''              # symbols the original link took from outside the sources
    split_zi: bool = False         # the data stage's zero-init data in a region of its own


def _hex(v):
    return v if isinstance(v, int) else int(str(v), 16)


def load_spec(cfg):
    g = cfg.raw.get('gate', {})
    if 'piece' in g:
        spec_raw = g
    else:
        path = os.path.join(cfg.root, g.get('pieces', 'pieces.toml'))
        if not os.path.exists(path): raise SystemExit(f'{path}: no pieces file ([gate] pieces) and no [[gate.piece]]')
        with open(path, 'rb') as f: spec_raw = tomllib.load(f)
    pieces = [Piece(_hex(p['start']), _hex(p['end']), bool(p.get('startup'))) for p in spec_raw.get('piece', [])]
    if not pieces: raise SystemExit('the gate has no pieces')
    for p in pieces:
        if p.start % 4: raise SystemExit(f'piece 0x{p.start:08X}: a piece starts on a 4-byte boundary (armlink: L6244E)')
        if p.end <= p.start: raise SystemExit(f'piece 0x{p.start:08X}: end 0x{p.end:08X} is not after its start')
    d = spec_raw.get('data', g.get('data', {}))
    jobs = os.environ.get('EXHUME_GATE_JOBS') or g.get('jobs') or min(12, os.cpu_count() or 1)
    return Spec(pieces=pieces, known=set(spec_raw.get('known_diffs', g.get('known_diffs', []))),
                data_start=_hex(d['start']) if 'start' in d else None,
                data_end=_hex(d['end']) if 'end' in d else None,
                startup=g.get('startup'), library=g.get('library', '*.l'),
                libpath=os.path.join(cfg.root, g['libpath']) if g.get('libpath') else
                        (os.path.join(cfg.home, 'ARM', 'ARMCC', 'lib') if cfg.home else None),
                exclude=[os.path.normpath(os.path.join(cfg.src, x)) for x in g.get('exclude', [])],
                jobs=int(jobs), allow_gaps=bool(g.get('allow_gaps', False)), aside=list(g.get('aside', [])),
                split_zi=bool(d.get('split_zi', False)),
                symdefs=_read(os.path.join(cfg.root, g['symdefs'])) if g.get('symdefs') else '')


def _read(path):
    with open(path) as f: return f.read()


def cpu(cfg):
    m = re.search(r'--cpu[ =](\S+)', cfg.c_opts or '')
    return m.group(1) if m else 'Cortex-M3'


def microlib(cfg):
    return '--library_type=microlib' in (cfg.c_opts or '')


def project_sources(cfg, spec):
    """Every .c and .s under src, but the shared headers and the excluded directories."""
    skip = [os.path.normpath(cfg.include)] + [os.path.normpath(x) for x in getattr(cfg, 'exclude', [])] + spec.exclude
    out = []
    for dirpath, dirs, files in os.walk(cfg.src):
        if any(os.path.commonpath([os.path.normpath(dirpath), d]) == d for d in skip): dirs[:] = []; continue
        for n in files:
            if n.endswith(('.c', '.s')): out.append(os.path.join(dirpath, n))
    out.sort()
    seen = {}
    for p in out:
        s = stem(p)
        if s in seen: raise SystemExit(f'two sources with the stem {s}: {seen[s]} and {p}')
        seen[s] = p
    return out


def stem(path):
    return os.path.splitext(os.path.basename(path))[0]


def source_opts(cfg, src, text=None):
    """A source's switches: its /* opts: */ line (C) or ; opts: line (assembly)."""
    if src.endswith('.s'):
        if text is None:
            with open(src, encoding='latin1') as f: text = f.read(4000)
        m = re.search(r'^\s*;\s*opts:\s*(.*)$', text, re.M)
        return m.group(1).strip() if m else (cfg.asm_opts or f'--cpu {cpu(cfg)}')
    return cfg.directives(src)[1] or cfg.c_opts


def host_opts(tc, cfg, opts):
    """Under host Wine, -I paths written for the Docker mount (/work/...) name the project root."""
    if tc.runner == 'wine': return opts.replace('-I/work/', '-I' + os.path.realpath(cfg.root) + '/')
    return opts


# ---- names --------------------------------------------------------------------------------------
def link_names(m, pieces):
    """({placeholder: link name}, notes). armlink lays a region out in ASCII order of section name
    (i.<function>), so a placeholder in a natural piece is renamed to the last real name before it,
    '_' and its address (delay_08004120), which sorts between its named neighbours. A source that
    still spells a named row as sub_ADDR gets the row's name. Names out of order are noted: the
    piece will not match until a name is corrected."""
    natural = [p for p in pieces if not p.startup]
    out, notes = {}, []
    for p in natural:
        prev, names = '', []
        for r in sorted((r for r in m.functions if p.start <= r.addr < p.end), key=lambda r: r.addr):
            ph = f'sub_{r.addr:08X}'
            name = f'{prev or "A0"}_{r.addr:08X}' if r.name == ph else r.name
            if r.name != ph: prev = r.name
            if name != ph: out[ph] = name
            names.append((r.addr, name))
        bad = [(a, x, y) for (a, x), (_, y) in zip(names, names[1:]) if x >= y]
        for a, x, y in bad[:5]:
            notes.append(f'piece 0x{p.start:08X}: {x} (0x{a:08X}) does not sort before {y}: armlink will '
                         f'lay them out in the other order; one of the names is wrong')
    for r in m.functions:          # a named row outside the natural pieces: its placeholder still works
        ph = f'sub_{r.addr:08X}'
        if r.name != ph and ph not in out: out[ph] = r.name
    return out, notes


def renamed(m, ln):
    return armmap.Map([dataclasses.replace(r, name=ln.get(r.name, r.name)) for r in m.functions], m.data)


def names_header(ln, dups=(), tag=''):
    return ''.join(f'#define {k} {v}\n' for k, v in sorted(ln.items())) + \
        ''.join(f'#undef {d}\n#define {d} {ln.get(d, d)}__dup_{tag}\n' for d in sorted(dups))


# ---- scatter pieces shared by every link ----------------------------------------------------------
def fixed_scatter(fixed, tag, aside_base, k0=0):
    """Scatter lines placing sections [(obj, section, addr, size)] at their addresses, and the
    pads they need [(k, bytes, kind)]. A section that starts off a 4-byte boundary right where
    another ends shares its region (armlink rounds a region's end up to 4), across objects too; a
    group that starts off a boundary goes in a region at the aligned address below, after a pad
    +FIRST, its load address out of the way."""
    lines, pads, groups = [], [], []
    for o, sec, addr, z in sorted(fixed, key=lambda f: f[2]):
        if groups:
            po, ps, pa, pz = groups[-1][-1]
            if (addr >= RAM) == (pa >= RAM) and addr == pa + pz and (addr % 4 or (pa + pz) % 4):
                groups[-1].append((o, sec, addr, z)); continue
        groups.append([(o, sec, addr, z)])
    for n, g in enumerate(groups, 1):
        first = g[0][2]
        sels = ' '.join(f'{o}({s}{", +FIRST" if j == 0 and not first % 4 and len(g) > 1 else ""})'
                        for j, (o, s, _, _) in enumerate(g))
        if first % 4 and first < RAM:
            # in flash, a root region at the aligned address below (MicroLIB's handlers must be in
            # a root region); the pad covers bytes no other section of the link can hold
            k = k0 + len(pads) + 1; pads.append((k, first % 4, 'ro'))
            lines += [f'LR_{tag}{n} 0x{first & ~3:08X} {{',
                      f'  ER_{tag}{n} 0x{first & ~3:08X} FIXED {{ {PAD}.o(.pad{k}, +FIRST) {sels} }}', '}']
        elif first % 4:
            k = k0 + len(pads) + 1; pads.append((k, first % 4, armmap._kind(g[0][1])))
            lines += [f'LR_{tag}{n} 0x{aside_base + 0x100000 + 0x1000 * k:08X} {{',
                      f'  ER_{tag}{n} 0x{first & ~3:08X} {{ {PAD}.o(.pad{k}, +FIRST) {sels} }}', '}']
        else:
            lines += [f'LR_{tag}{n} 0x{first:08X} {{', f'  ER_{tag}{n} 0x{first:08X} FIXED {{ {sels} }}', '}']
    return lines, pads


def pad_source(pads):
    src = ['/* pads for fixed sections off a 4-byte boundary */']
    for k, b, kind in pads:
        if kind == 'ro': src.append(f'__attribute__((section(".pad{k}"))) const unsigned char gate_pad{k}[{b}] = {{ 0 }};')
        elif kind == 'rw': src.append(f'__attribute__((section(".pad{k}"))) unsigned char gate_pad{k}[{b}] = {{ 1 }};')
        else: src.append(f'__attribute__((section(".pad{k}"), zero_init)) unsigned char gate_pad{k}[{b}];')
    return '\n'.join(src) + '\n'


def stub_source(names, tag='gate_libstub'):
    """A table of the entry points a link must pull from the library, kept with --keep."""
    return ''.join(f'extern void {n}(void);\n' for n in names) + \
        f'void (*const {tag}[])(void) = {{ {", ".join(names) or "0"} }};\n'


def listing(path):
    """(symbols {name: (addr, kind, object, section)}, sections [(addr, size, section, object)],
    load regions {name: (base, size)}) from an armlink --map --symbols --list file."""
    syms, secs, loads = {}, [], {}
    if not os.path.exists(path): return syms, secs, loads
    with open(path, encoding='latin1') as f: text = f.read()
    for l in text.splitlines():
        m = _LSYM.match(l)
        if m:
            name, a, kind, _, obj, sec = m.groups()
            if kind != 'Section': syms.setdefault(name, (int(a, 16), kind, obj, sec))
            continue
        m = _LMAP.match(l)
        if m:
            a, z, kind, _, sec, obj = m.groups()
            if kind != 'PAD': secs.append((int(a, 16), int(z, 16), sec, re.sub(r'^.*\((.*)\)$', r'\1', obj)))
            continue
        m = _LOAD.search(l)
        if m: loads[m.group(1)] = (int(m.group(2), 16), int(m.group(4) or m.group(3), 16))
    return syms, secs, loads


# ---- the plan every worker gets ------------------------------------------------------------------
@dataclass
class Plan:
    spec: Spec
    m0: object                     # the map as written
    m: object                      # functions under their link names
    ln: dict
    owners: dict                   # link name -> source (relative to root), LIB or ''
    objs: dict                     # source -> (object path, ObjectInfo)
    dups: dict                     # source -> names it defines that another file owns
    lib_refs: list                 # library entry points the project's objects reference
    image_path: str
    root: str
    image_base: int
    image_end: int
    work: str
    extra_symdefs: str = ''
    data_end: int = None


def aside_base(plan):
    """Where a link puts what has no address in the image: past the image, within bl range."""
    return ((plan.image_end + 0xFFFF) & ~0xFFFF) + 0x10000


def extra_lines(plan, defined):
    """[gate] symdefs and a modded build's own symbols, less what this link defines."""
    return ''.join(l + '\n' for l in (plan.spec.symdefs + '\n' + plan.extra_symdefs).splitlines()
                   if l.split() and not l.startswith('#') and l.split()[-1] not in defined)


def lib_names(plan):
    """Every name of a library row (its link name and placeholder), never given by symdefs."""
    out = set()
    for r in plan.m.functions:
        if plan.owners.get(r.name) == LIB: out |= {r.name, f'sub_{r.addr:08X}'}
    return out


def link_args(plan, cfg, tc, scatter, inputs, axf, extra, lib):
    args = ['--cpu', cpu(cfg), '--scatter', tc.inside(scatter)] + extra
    if lib:
        if microlib(cfg): args.append('--library_type=microlib')
        if plan.spec.libpath: args += ['--libpath', tc.inside(plan.spec.libpath)]
    else:
        args.append('--no_scanlib')
    lst = os.path.splitext(axf)[0] + '.lst'
    return args + ['--map', '--symbols', '--list', tc.inside(lst)] + [tc.inside(i) for i in inputs] + ['-o', tc.inside(axf)]


def run_link(tc, args, work, axf, stub=None, stub_names=None, compile_stub=None):
    """Link; a stub entry point the library does not export (a member's local helper) is dropped
    from the stub and the link run again. Returns (ok, log)."""
    for _ in range(4):
        if os.path.exists(axf): os.remove(axf)
        rc, log = tc.run('armlink', args, work)
        undef = {n for n, frm in _UNDEF.findall(log) if stub and os.path.basename(frm).startswith(STUB)}
        if not undef or not stub_names: return rc == 0 and os.path.exists(axf), log
        stub_names[:] = [n for n in stub_names if n not in undef]
        ok, clog = compile_stub()
        if not ok: return False, clog
    return rc == 0 and os.path.exists(axf), log


def place_files(plan, files, start, end, defined):
    """Compile-time placement of each file's sections: (in the piece [(obj, section, addr)],
    fixed [(obj, section, addr, size)], dumped [(obj, section)], errors)."""
    piece, fixed, dump, errors = [], [], [], []
    for f in files:
        if f not in plan.objs: errors.append(f'{f}: no object (it failed to compile)'); continue
        obj, info = plan.objs[f]
        with open(os.path.join(plan.root, f), encoding='latin1') as fh: text = fh.read()
        dups = plan.dups.get(f, set())
        extra = {plan.ln.get(k, k): v for k, v in armmap.annotations(text).items() if k not in dups}
        placements, d, errs, unplaced = armmatch.place(info, plan.m, extra, True)
        if errs: errors.append(f'{f}: ' + '; '.join(errs)); continue
        o = os.path.basename(obj)
        dump += [(o, x) for x in unplaced]
        defined |= set(d)
        size_of = {n: z for n, z in info.sections.values()}
        for sec, addr in placements:
            if armmatch.is_code(info, next(i for i, (n, _) in info.sections.items() if n == sec)) and start <= addr < end:
                piece.append((o, sec, addr))
            else:
                fixed.append((o, sec, addr, size_of.get(sec) or 0))
    return piece, fixed, dump, errors


def input_order(objs, fixed):
    """Objects on the command line in the order their data lies: same-named sections (.data,
    .bss, .constdata) in one region keep command-line order, which is the original link order."""
    first = {}
    for o, sec, addr, z in fixed:
        if not sec.startswith('i.'): first[o] = min(first.get(o, 1 << 40), addr)
    return sorted(objs, key=lambda p: (first.get(os.path.basename(p), 1 << 40), os.path.basename(p)))


@dataclass
class PieceResult:
    got: bytes = None
    at: dict = field(default_factory=dict)        # link name -> linked address
    msgs: list = field(default_factory=list)
    masked: set = field(default_factory=set)       # offsets checked elsewhere (the data stage)


def link_piece(cfg, tc, plan, piece):
    """One natural piece: the function sections of its rows in one region at its start."""
    start, end = piece.start, piece.end
    work = os.path.join(plan.work, f'{start:08X}'); os.makedirs(work, exist_ok=True)
    res = PieceResult()
    rows = [r for r in plan.m.functions if start <= r.addr < end]
    files = []
    for r in rows:
        f = plan.owners.get(r.name)
        if f and f != LIB and f not in files: files.append(f)
    lib_rows = [r for r in rows if plan.owners.get(r.name) == LIB]
    defined = set()
    sel, fixed, dump, errors = place_files(plan, files, start, end, defined)
    if errors: res.msgs += errors; return res
    ab = aside_base(plan)
    lines = [f'LR_PIECE 0x{start:08X} {{', f'  ER_PIECE 0x{start:08X} {{'] + \
            [f'    {o}({s})' for o, s, _ in sorted(sel, key=lambda p: -p[2])] + \
            (['    .ANY (+RO)'] if lib_rows else []) + ['  }', '}']
    flines, pads = fixed_scatter(fixed, 'F', ab)
    lines += flines
    if dump:
        lines += [f'LR_DUMP 0x{ab:08X} {{', f'  ER_DUMP 0x{ab:08X} {{ ' + ' '.join(f'{o}({x})' for o, x in dump) + ' }', '}']
    inputs = input_order([plan.objs[f][0] for f in files], fixed)
    stub_names = [r.name for r in lib_rows]
    stub_c = os.path.join(work, STUB + '.c'); stub_o = os.path.join(work, STUB + '.o')

    def compile_stub():
        with open(stub_c, 'w') as f: f.write(stub_source(stub_names))
        return tc.compile(stub_c, stub_o, cfg.c_opts)
    if lib_rows:
        ok, log = compile_stub()
        if not ok: res.msgs += [log]; return res
        inputs.append(stub_o)
        lines += [f'LR_STUB 0x{ab + 0x40000:08X} {{', f'  ER_STUB 0x{ab + 0x40000:08X} {{ {STUB}.o (+RO +RW +ZI) }}', '}',
                  f'LR_LIBREST 0x{LIBREST:08X} {{', f'  ER_LIBREST 0x{LIBREST:08X} {{ .ANY (+RW +ZI) }}', '}']
    if pads:
        pc = os.path.join(work, PAD + '.c')
        with open(pc, 'w') as f: f.write(pad_source(pads))
        ok, log = tc.compile(pc, os.path.join(work, PAD + '.o'), cfg.c_opts)
        if not ok: res.msgs += [log]; return res
        inputs.append(os.path.join(work, PAD + '.o'))
    libn = lib_names(plan)
    sym = os.path.join(work, 'piece.sym')
    with open(sym, 'w') as f:
        f.write(armmap.symdefs_text([e for e in plan.m.symdef_entries(defined, {}) if e[2] not in libn]) + extra_lines(plan, defined))
    scatter = os.path.join(work, 'piece.sct')
    with open(scatter, 'w') as f: f.write('\n'.join(lines) + '\n')
    axf = os.path.join(work, 'piece.axf')
    args = link_args(plan, cfg, tc, scatter, inputs + [sym], axf,
                     ['--no_startup', '--keep', '*', '--datacompressor', 'off', '--diag_suppress', '6305,6314,6329'], bool(lib_rows))
    ok, log = run_link(tc, args, work, axf, stub=lib_rows, stub_names=stub_names, compile_stub=compile_stub)
    # a known diff that grows its piece runs into the fixed region after it: move those regions
    # aside (a call into them then differs too, in the caller) and link again
    for attempt in range(3):
        over = sorted({b for a, b in _OVERLAP.findall(log) if a == 'LR_PIECE' and b.startswith('LR_F')})
        if not over: break
        with open(scatter) as f: text = f.read()
        for j, lr in enumerate(over):
            er = 'ER_' + lr[3:]
            text = re.sub(rf'^{lr} 0x[0-9A-F]+ {{\n  {er} 0x[0-9A-F]+ FIXED',
                          f'{lr} 0x{ab + 0x60000 + 0x1000 * (attempt * 16 + j):08X} {{\n  {er} +0', text, flags=re.M)
        with open(scatter, 'w') as f: f.write(text)
        res.msgs.append('moved aside (the piece grew): ' + ', '.join(over))
        ok, log = run_link(tc, args, work, axf, stub=lib_rows, stub_names=stub_names, compile_stub=compile_stub)
    if not ok: res.msgs += ['link failed', log.strip()]; return res
    res.got = tc.tobin(axf, os.path.join(work, 'bin'))['ER_PIECE']
    info = tc.symbols(axf)
    res.at = {s.name: s.value & ~1 for s in info.symbols if s.type == 'Code' and start <= (s.value & ~1) < start + len(res.got)}
    return res


def ram_blobs(img, fixed_ram, sp, startup_zi):
    """What the startup piece links in RAM so armlink builds a region table of the image's kinds,
    and so adds the same decompressor and handlers: the image's own RW bytes for each compressed
    or copied region, a zero-init array for each zero region, around the fixed RAM sections of
    the piece's own files. The startup file's zero-init sections (heap, stack) end the last zero
    region, which is cut so the stack ends at the initial stack pointer. Returns
    [(kind, addr, bytes or size, followed by the startup file's zero-init sections)]."""
    try:
        regions = rwdata.find_table(img); ram = rwdata.RamImage(img, regions)
    except ValueError:
        return []
    zeros = [r for r in regions if r.kind == 'zero']
    last_zero = max(zeros, key=lambda r: r.exec) if zeros and startup_zi else None
    out = []
    for r in regions:
        lo, hi = r.exec, r.exec + r.size
        if r is last_zero and sp is not None and lo <= sp - startup_zi <= hi: hi = sp - startup_zi
        spans = [(lo, hi)]
        for a, z in sorted(fixed_ram):
            nxt = []
            for s0, e0 in spans:
                if a + z <= s0 or a >= e0: nxt.append((s0, e0)); continue
                if a > s0: nxt.append((s0, a))
                if a + z < e0: nxt.append((a + z, e0))
            spans = nxt
        for s0, e0 in spans:
            s0 = (s0 + 3) & ~3
            if s0 >= e0: continue
            out.append(('zi', s0, e0 - s0, False) if r.kind == 'zero' else ('rw', s0, ram.bytes(s0, e0 - s0), False))
        if r is last_zero:
            if out and out[-1][0] == 'zi' and out[-1][1] + out[-1][2] == hi: out[-1] = out[-1][:3] + (True,)
            else: out.append(('zi', hi, 0, True))
    return out


def blob_source(blobs):
    src = ['/* the image\'s RAM regions, so the link builds the same region table kinds */']
    for k, (kind, addr, data, _) in enumerate(blobs):
        if kind == 'rw':
            src.append(f'__attribute__((section(".blob{k}"))) unsigned char gate_blob{k}[{len(data)}] = '
                       '{' + ','.join(str(b) for b in data) + '};')
        elif data:
            src.append(f'__attribute__((section(".blob{k}"), zero_init)) unsigned char gate_blob{k}[{data}];')
    return '\n'.join(src) + '\n'


def scatterload_pool(img_bytes, base):
    """Offsets (from base) of __scatterload's two literal words, the region table's base and
    limit, when bytes hold MicroLIB's __scatterload; else None."""
    at = img_bytes.find(rwdata.SCATTERLOAD)
    if at < 0: return None
    pc = base + at
    w = ((pc + 4) & ~3) + 24 - base
    return w


def link_startup_piece(cfg, tc, plan, piece):
    """The startup piece: the vector table first, then MicroLIB's entry code, the startup code,
    the library members and any C functions up to the piece's end."""
    start, end = piece.start, piece.end
    work = os.path.join(plan.work, f'{start:08X}'); os.makedirs(work, exist_ok=True)
    res = PieceResult()
    sfile = plan.spec.startup
    if not sfile or sfile not in plan.objs:
        res.msgs.append('no startup source: set [gate] startup, or give one assembly source a RESET area'); return res
    sobj, sinfo = plan.objs[sfile]
    so = os.path.basename(sobj)
    rows = [r for r in plan.m.functions if start <= r.addr < end]
    cfiles = []
    for r in rows:
        f = plan.owners.get(r.name)
        if f and f not in (LIB, sfile) and f not in cfiles: cfiles.append(f)
    defined = {x.name for x in sinfo.symbols if x.bind == 'Gb' and x.sec != 'Ref'}
    sel, fixed, dump, errors = place_files(plan, cfiles, start, end, defined)
    if errors: res.msgs += errors; return res
    img = ehex.Image(plan.image_path)
    sp = int.from_bytes(img.bytes(start, 4), 'little') if img.base <= start < img.end - 4 else None
    zi_secs = [(n, z or 0) for i, (n, z) in sinfo.sections.items() if armmatch.is_zi(sinfo, i)]
    startup_zi = sum(z for _, z in zi_secs)
    blobs = ram_blobs(img, [(a, z) for _, _, a, z in fixed if a >= RAM], sp, startup_zi)
    ab = aside_base(plan)
    lines = [f'LR_PIECE 0x{start:08X} {{', f'  ER_PIECE 0x{start:08X} {{',
             f'    {so} (RESET, +First)', '    *(.ARM.Collect*)', f'    {so} (+RO)', f'    {plan.spec.library} (+RO)'] + \
            [f'    {o}({s})' for o, s, _ in sel] + ['  }', '}']
    flines, pads = fixed_scatter(fixed, 'F', ab)
    inputs = [sobj] + input_order([plan.objs[f][0] for f in cfiles], fixed)
    stub_names = list(plan.lib_refs)
    stub_c = os.path.join(work, STUB + '.c'); stub_o = os.path.join(work, STUB + '.o')

    def compile_stub():
        with open(stub_c, 'w') as f: f.write(stub_source(stub_names))
        return tc.compile(stub_c, stub_o, cfg.c_opts)
    ok, log = compile_stub()
    if not ok: res.msgs += [log]; return res
    inputs.append(stub_o)
    blob_c = os.path.join(work, 'gate_blob.c'); blob_o = os.path.join(work, 'gate_blob.o')
    if blobs:
        with open(blob_c, 'w') as f: f.write(blob_source(blobs))
        ok, log = tc.compile(blob_c, blob_o, cfg.c_opts)
        if not ok: res.msgs += [log]; return res
        inputs.append(blob_o)
    if pads:
        pc = os.path.join(work, PAD + '.c')
        with open(pc, 'w') as f: f.write(pad_source(pads))
        ok, log = tc.compile(pc, os.path.join(work, PAD + '.o'), cfg.c_opts)
        if not ok: res.msgs += [log]; return res
        inputs.append(os.path.join(work, PAD + '.o'))
    libn = lib_names(plan)
    in_piece = set()
    for r in rows: in_piece |= {r.name, f'sub_{r.addr:08X}'}
    sym = os.path.join(work, 'piece.sym')
    with open(sym, 'w') as f:
        f.write(armmap.symdefs_text([e for e in plan.m.symdef_entries(defined, {}) if e[2] not in libn | in_piece])
                + extra_lines(plan, defined | in_piece))
    scatter = os.path.join(work, 'piece.sct')
    axf = os.path.join(work, 'piece.axf')
    aside = list(plan.spec.aside)
    for attempt in range(3):
        alines = [f'LR_ELSE 0x{ab:08X} {{',
                  f'  ER_ASIDE 0x{ab:08X} {{ ' + ' '.join(f'{o} (+RO)' for o in aside + [STUB + '.o']) +
                  (' ' + ' '.join(f'{o}({x})' for o, x in dump) if dump else '') + ' }',
                  '  ER_TABLE +0 { *(Region$$Table) }']
        for k, (kind, addr, data, with_startup) in enumerate(blobs):
            body = (f'gate_blob.o (.blob{k})' if (kind == 'rw' or data) else '') + \
                   (f' {so} (+ZI)' if with_startup else '')
            alines.append(f'  ER_BLOB{k} 0x{addr:08X} {{ {body.strip()} }}')
        if not any(b[3] for b in blobs) and zi_secs:
            alines.append(f'  ER_STARTUP_ZI +0 {{ {so} (+ZI) }}')
        alines.append('}')
        with open(scatter, 'w') as f: f.write('\n'.join(lines + flines + alines) + '\n')
        keep = ['--keep', f'{so}(RESET)', '--keep', f'{STUB}.o(*)'] + (['--keep', 'gate_blob.o(*)'] if blobs else [])
        args = link_args(plan, cfg, tc, scatter, inputs + [sym], axf, keep + ['--diag_suppress', '6305,6314,6329,6238'], True)
        ok, log = run_link(tc, args, work, axf, stub=True, stub_names=stub_names, compile_stub=compile_stub)
        if not ok: res.msgs += ['link failed', log.strip()]; return res
        # a library member the image has elsewhere (a later piece) goes out of the way
        syms, secs, _ = listing(os.path.splitext(axf)[0] + '.lst')
        mine = {os.path.basename(o) for o in inputs}
        rows_out = {r.name: r.addr for r in plan.m.functions if not start <= r.addr < end}
        more = sorted({obj for n, (a, kind, obj, sec) in syms.items()
                       if obj not in mine and start <= (a & ~1) < end and n in rows_out} - set(aside))
        if not more: break
        aside += more
        res.msgs.append('library members the image has elsewhere, linked aside: ' + ', '.join(more))
    res.got = tc.tobin(axf, os.path.join(work, 'bin'))['ER_PIECE']
    info = tc.symbols(axf)
    res.at = {s.name: s.value & ~1 for s in info.symbols if s.type == 'Code' and start <= (s.value & ~1) < start + len(res.got)}
    w = scatterload_pool(res.got, start)
    if w is not None:
        res.masked = set(range(w, w + 8))
        res.msgs.append('__scatterload: the region table\'s base and limit words are checked by the data stage')
    return res


# ---- the plan: compile everything once, decide owners ---------------------------------------------
def compile_one(cfg, tc, src, obj, header, extra=''):
    """Compile (or assemble) one source with the link-names header and extra switches:
    (ok, log, ObjectInfo)."""
    if src.endswith('.s'):
        opts = source_opts(cfg, src)
        import shlex
        os.makedirs(os.path.dirname(obj), exist_ok=True)
        if os.path.exists(obj): os.remove(obj)
        rc, log = tc.run('armasm', shlex.split(opts) + [tc.inside(src), '-o', tc.inside(obj)], os.path.dirname(src))
        ok = rc == 0 and os.path.exists(obj)
    else:
        opts = host_opts(tc, cfg, source_opts(cfg, src))
        if header: opts += ' --preinclude=' + tc.inside(header)
        ok, log = tc.compile(src, obj, (opts + ' ' + extra).strip())
    return ok, log, (tc.symbols(obj) if ok else None)


def defined_names(info):
    return {s.name for s in info.symbols if s.type in ('Code', 'Data') and isinstance(s.sec, int)
            and re.match(r'^[A-Za-z_$][\w$]*$', s.name)}


def referenced(info):
    return {s.name for s in info.symbols if s.sec == 'Ref'}


def make_plan(cfg, spec, pool, work, extra_symdefs='', data_end=None, log=print):
    """Compile every source (on the pool) and decide every row's owner."""
    m0 = armmap.Map.from_dir(cfg.map)
    ln, notes = link_names(m0, spec.pieces)
    m = renamed(m0, ln)
    srcs = project_sources(cfg, spec)
    rel = {s: os.path.relpath(s, cfg.root) for s in srcs}
    objdir = os.path.join(work, 'obj'); os.makedirs(objdir, exist_ok=True)
    hdr = os.path.join(objdir, 'gate_names.h')
    with open(hdr, 'w') as f: f.write(names_header(ln))
    jobs = [(s, os.path.join(objdir, stem(s) + '.o'), hdr) for s in srcs]
    t = time.time()
    results = pool.map(task_compile, jobs)
    objs, failed = {}, []
    for (s, o, _), (ok, clog, info) in zip(jobs, results):
        if ok: objs[rel[s]] = (o, info)
        else: failed.append(f'{rel[s]}: compile failed\n    ' + '\n    '.join(clog.strip().splitlines()[-8:]))
    log(f'compiled {len(srcs)} sources in {time.time() - t:.0f}s')
    index = collections.defaultdict(set)
    for f, (o, info) in objs.items():
        for n in defined_names(info): index[n].add(f)
    owners, problems = {}, []
    for r in m.functions + m.data:
        f = r.file
        if not f:
            cands = sorted(index.get(r.name, set()) | index.get(f'sub_{r.addr:08X}', set()))
            if len(cands) == 1: f = cands[0]
            elif len(cands) > 1: problems.append(f'0x{r.addr:08X} {r.name}: defined in {", ".join(cands)}; '
                                                 'give the row its file in the map')
            elif r in m.functions and armmap.placeholder(r.name) is None: f = LIB
        elif not os.path.exists(os.path.join(cfg.root, f)):
            problems.append(f'0x{r.addr:08X} {r.name}: the map names {f}, which does not exist')
        owners[r.name] = f or ''
    dups = {}
    for f, (o, info) in objs.items():
        d = {n for n in defined_names(info) if owners.get(n) not in (None, '', f, LIB)}
        if d: dups[f] = d
    if dups:          # a partial file's copies of what another file owns: renamed in that file
        again = []
        for f, d in dups.items():
            if f.endswith('.s'):
                problems.append(f'{f} defines {", ".join(sorted(d))}, owned elsewhere (not renamed in assembly)'); continue
            h = os.path.join(objdir, stem(f) + '.names.h')
            with open(h, 'w') as fh: fh.write(names_header(ln, d, stem(f)))
            again.append((os.path.join(cfg.root, f), objs[f][0], h))
        for (s, o, _), (ok, clog, info) in zip(again, pool.map(task_compile, again)):
            if ok: objs[rel[s]] = (o, info)
            else: failed.append(f'{rel[s]}: compile failed\n    {clog.strip()}')
    project_defs = set()
    refs = set()
    for f, (o, info) in objs.items():
        project_defs |= {s.name for s in info.symbols if s.bind in ('Gb', 'Wk') and s.sec != 'Ref'}
        refs |= referenced(info)
    map_names = {r.name for r in m.functions + m.data} | {f'sub_{r.addr:08X}' for r in m.functions}
    lib_rows = {r.name for r in m.functions if owners.get(r.name) == LIB}
    # linker-made names (Lib$$Request$$armlib, Region$$Table$$Base) are never requested
    lib_refs = sorted(n for n in refs - project_defs if (n in lib_rows or n not in map_names) and '$$' not in n)
    if not spec.startup:
        cands = [f for f, (o, info) in objs.items() if f.endswith('.s') and 'RESET' in {n for n, _ in info.sections.values()}]
        if len(cands) == 1: spec.startup = cands[0]
    img = ehex.Image(cfg.image)
    plan = Plan(spec=spec, m0=m0, m=m, ln=ln, owners=owners, objs=objs, dups=dups, lib_refs=lib_refs,
                image_path=cfg.image, root=cfg.root, image_base=img.base, image_end=img.end, work=work,
                extra_symdefs=extra_symdefs, data_end=data_end)
    return plan, notes, problems, failed


# ---- workers --------------------------------------------------------------------------------------
_W = {}


def _setup(cfg, tc):
    _W['cfg'], _W['tc'] = cfg, tc


def _wineserver():
    """A persistent wineserver for this process's prefix: without one every Keil tool call boots
    a server, about 6 s under Rosetta. A no-op when one already runs there."""
    wine = shutil.which('wine')
    server = shutil.which('wineserver') or (os.path.join(os.path.dirname(wine), 'wineserver') if wine else None)
    if server:
        subprocess.Popen([server, '-p'], env=os.environ, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _init(queue, cfg_path, overrides):
    n = queue.get()
    if os.environ.get('EXHUME_ARMCC_RUNNER', 'docker') == 'wine':
        base = os.environ.get('EXHUME_GATE_WINEPREFIX', os.path.expanduser('~/.exhume-wine'))
        prefix = os.environ['WINEPREFIX'] = os.path.join(base, f'job{n}')
        # a new prefix would stop at Wine's offer to install Mono and Gecko, which nothing here needs
        os.environ.setdefault('WINEDLLOVERRIDES', 'mscoree,mshtml=')
        wine = shutil.which('wine')
        server = shutil.which('wineserver') or (os.path.join(os.path.dirname(wine), 'wineserver') if wine else None)
        if not os.path.exists(os.path.join(prefix, 'system.reg')):
            os.makedirs(prefix, exist_ok=True)
            boot = shutil.which('wineboot') or (os.path.join(os.path.dirname(wine), 'wineboot') if wine else None)
            if boot: subprocess.run([boot, '-i'], env=os.environ, capture_output=True, timeout=600)
        if server:        # a persistent server: a no-op if one already runs in this prefix
            subprocess.Popen([server, '-p'], env=os.environ, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    cfg = load_config(cfg_path, overrides)
    _setup(cfg, armtc.toolchain(cfg))


def load_config(path, overrides=None):
    import config
    cfg = config.Config(path)
    for k, v in (overrides or {}).items(): setattr(cfg, k, v)
    return cfg


def task_compile(job):
    return compile_one(_W['cfg'], _W['tc'], *job)


def task_piece(job):
    plan, i = job
    p = plan.spec.pieces[i]
    try:
        return (link_startup_piece if p.startup else link_piece)(_W['cfg'], _W['tc'], plan, p)
    except Exception as e:            # report it with the piece rather than lose the gate
        import traceback
        return PieceResult(msgs=[f'piece 0x{p.start:08X}: {type(e).__name__}: {e}', traceback.format_exc()])


class Serial:
    """The pool's interface, one call at a time in this process."""
    def map(self, fn, items, chunksize=1): return [fn(x) for x in items]

    def apply_async(self, fn, args):
        r = fn(*args)
        return type('Done', (), {'get': lambda self: r})()

    def close(self): pass

    def join(self): pass


def pool(cfg, cfg_path, overrides, jobs):
    if jobs <= 1:
        if os.environ.get('EXHUME_ARMCC_RUNNER', 'docker') == 'wine': _wineserver()
        _setup(cfg, armtc.toolchain(cfg)); return Serial()
    import multiprocessing as mp
    ctx = mp.get_context('spawn')
    manager = ctx.Manager(); queue = manager.Queue()
    for n in range(jobs): queue.put(n)
    p = ctx.Pool(jobs, initializer=_init, initargs=(queue, cfg_path, overrides))
    p._gate_manager = manager          # keep the queue's manager alive as long as the pool
    _setup(cfg, armtc.toolchain(cfg))
    return p


# ---- the gate -------------------------------------------------------------------------------------
@dataclass
class Result:
    ok: bool
    base: int = None
    image: bytes = None            # the rebuilt image from the first piece to the data's end
    out: str = None                # where it was written
    plan: object = None
    pieces: dict = field(default_factory=dict)     # start -> PieceResult
    data: object = None


def judge(plan, p, res, want, log):
    """(passed, rebuilt bytes) for one piece's result. Masked offsets (checked elsewhere) are
    compared as the image's."""
    start, end = p.start, p.end
    rows = [r for r in plan.m.functions if start <= r.addr < end]
    got = bytes(want[i] if i in res.masked and i < len(want) else b for i, b in enumerate(res.got))
    bad, shifted = [], []
    for r in rows:
        g = res.at.get(r.name, r.addr) - start
        if got[g:g + r.size] != want[r.addr - start:r.addr - start + r.size]: bad.append(r.name)
        elif g != r.addr - start: shifted.append(r.name)
    if shifted:
        log(f'    shifted by a known diff before them, else equal: {", ".join(shifted[:8])}' + (' ...' if len(shifted) > 8 else ''))
    same = got[:len(want)] == want and 0 <= len(got) - len(want) < 4 and not any(got[len(want):])
    known = {plan.ln.get(k, k) for k in plan.spec.known} | plan.spec.known
    unknown = [b for b in bad if b not in known]
    grew = (shifted or any('moved aside' in x for x in res.msgs)) and any(b in known for b in bad)
    if grew and unknown:            # consequences of a known diff that changed size (shifted calls)
        log(f'    affected by a known diff that changed size: {", ".join(unknown)}')
        unknown = []
    names = {plan.ln.get(r.name, r.name): r.name for r in plan.m0.functions}
    state = 'MATCHES' if same else (f'{len(bad)} functions differ' +
                                    (f', {len(unknown)} not known: {", ".join(names.get(u, u) for u in unknown[:8])}'
                                     if unknown else ' (all known)'))
    log(f'piece 0x{start:08X}-0x{end:08X} ({len(rows)} functions): {state}')
    return not unknown and (same or bool(bad)), res.got[:end - start]


def run(cfg, cfg_path=None, overrides=None, only=None, jobs=None, extra_symdefs='', data_end=None, log=print):
    """The gate. only: one piece's index (no data stage, no image). Returns a Result."""
    import armdata
    spec = load_spec(cfg)
    if jobs is not None: spec.jobs = jobs
    work = os.path.join(cfg.build, 'gate')
    os.makedirs(work, exist_ok=True)
    extra_symdefs = extra_symdefs or (open(os.environ['EXHUME_GATE_EXTRA_SYMDEFS']).read()
                                      if os.environ.get('EXHUME_GATE_EXTRA_SYMDEFS') else '')
    if data_end is None and os.environ.get('EXHUME_GATE_DATA_END'): data_end = int(os.environ['EXHUME_GATE_DATA_END'], 16)
    t0 = time.time()
    workers = pool(cfg, cfg_path or cfg.file, overrides, spec.jobs)
    try:
        plan, notes, problems, failed = make_plan(cfg, spec, workers, work, extra_symdefs, data_end, log)
        for x in notes + problems: log('NOTE ' + x)
        for x in failed: log('FAIL ' + x)
        result = Result(ok=not failed, plan=plan)
        pieces = list(enumerate(spec.pieces))
        if only is not None: pieces = [pieces[only]]
        jobsq = {}
        for i, p in pieces:
            missing = [r for r in plan.m.functions if p.start <= r.addr < p.end and not plan.owners.get(r.name)]
            if missing:
                log(f'piece {i} 0x{p.start:08X}-0x{p.end:08X}: no source for ' + ', '.join(r.name for r in missing[:12]))
                result.ok = False; continue
            jobsq[i] = workers.apply_async(task_piece, ((plan, i),))
        data = None
        if only is None:
            data = armdata.run(cfg, plan, workers)
        img = ehex.Image(cfg.image)
        rebuilt = {}
        for i, p in pieces:
            if i not in jobsq: continue
            res = jobsq[i].get()
            result.pieces[p.start] = res
            for x in res.msgs: log('    ' + x)
            if res.got is None:
                log(f'piece {i} 0x{p.start:08X}-0x{p.end:08X}: FAILED'); result.ok = False; continue
            want = img.bytes(p.start, p.end - p.start)
            if res.masked and data is not None and data.pool is not None:
                # the region table's words in __scatterload, from the data stage's own link
                w = min(res.masked)
                res.got = res.got[:w] + data.pool + res.got[w + 8:]
                if data.pool != want[w:w + 8]:
                    log('    __scatterload\'s region table words differ from the image'); result.ok = False
                res.masked = set()
            ok, b = judge(plan, p, res, want, log)
            if not ok: result.ok = False
            rebuilt[p.start] = b
        if only is not None:
            log(f'-- piece {only}: ' + ('PASS' if result.ok else 'FAIL') + f' in {time.time() - t0:.0f}s')
            return result
        log('data stage:')
        if data is None:
            log('    not run'); result.ok = False
        else:
            for x in data.lines: log('    ' + x)
            if not data.ok: result.ok = False
            result.data = data
        base = min(p.start for p in spec.pieces)
        dend = data.start + len(data.got) if data and data.got else max(p.end for p in spec.pieces)
        full = bytearray(b'\xff' * (dend - base))
        orig_end = min(img.end, dend)
        full[:orig_end - base] = img.bytes(base, orig_end - base)
        covered = [False] * len(full)
        for s0, b in rebuilt.items():
            full[s0 - base:s0 - base + len(b)] = b
            for x in range(s0 - base, s0 - base + len(b)): covered[x] = True
        if data and data.got:
            full[data.start - base:data.start - base + len(data.got)] = data.got
            for x in range(data.start - base, data.start - base + len(data.got)): covered[x] = True
        gaps = _runs([i for i, c in enumerate(covered) if not c])
        if gaps:
            log('not rebuilt (taken from the original): ' + ', '.join(f'0x{base + a:08X}-0x{base + b:08X}' for a, b in gaps[:8]))
            if not spec.allow_gaps: result.ok = False
        result.base, result.image = base, bytes(full)
        result.out = write_image(cfg, work, base, bytes(full), log)
    finally:
        workers.close(); workers.join()
    known = sorted(spec.known)
    log(f'-- gate: ' + ('PASS' + (f' (with known diffs: {", ".join(known)})' if known else '') if result.ok else 'FAIL')
        + f' in {time.time() - t0:.0f}s')
    return result


def _runs(xs):
    out = []
    for x in xs:
        if out and out[-1][1] == x: out[-1][1] = x + 1
        else: out.append([x, x + 1])
    return [tuple(r) for r in out]


def write_image(cfg, work, base, data, log):
    """The rebuilt image in the original file's format, compared with it file for file."""
    with open(cfg.image, 'rb') as f: orig = f.read()
    if orig.startswith(b':'):
        text = ehex.write(base, data, like=cfg.image).encode('ascii')
        out = os.path.join(work, 'rebuilt.hex')
        mine, theirs = text.splitlines(), orig.splitlines()
        bad = sum(1 for x, y in zip(theirs, mine) if x != y) + abs(len(theirs) - len(mine))
        state = 'IDENTICAL to the original file' if text == orig else f'{bad} of {len(theirs)} lines differ'
    else:
        text = data if base == 0 else bytes(base) + data
        out = os.path.join(work, 'rebuilt.bin')
        state = 'IDENTICAL to the original file' if text == orig else 'differs from the original file'
    with open(out, 'wb') as f: f.write(text)
    log(f'image: {out}; {state}')
    return out


def main(cfg, argv, cfg_path=None):
    if '-h' in argv or '--help' in argv: print(__doc__); return 0
    only = int(argv[argv.index('--piece') + 1]) if '--piece' in argv else None
    jobs = int(argv[argv.index('--jobs') + 1]) if '--jobs' in argv else None
    r = run(cfg, cfg_path, only=only, jobs=jobs, log=lambda s='': print(s, flush=True))
    return 0 if r.ok else 1


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'tools'))
    import config, armgate          # one copy of this module: the workers' state lives in it
    path, a = config.pop_config(sys.argv[1:])
    sys.exit(armgate.main(config.load(path), a, path))
