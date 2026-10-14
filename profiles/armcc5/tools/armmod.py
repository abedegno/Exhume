"""The modded build for a linked image: the original firmware with new code in free flash, and
proof that nothing else changed.

    python3 tools/gate.py [--config PATH] game            (Exhume's gate.py hands over to main())
    python3 profiles/armcc5/tools/armmod.py [--config PATH] [--jobs N]

1. Copies the project (not .git, the build directory or the toolchain) to <build>/mod/tree and
   edits the copy as [mod] says: `replace` rules (each a string that must occur exactly `count`
   times), `patches` (applied with patch -p1) and an `edit` command. The matched tree is never
   touched, so its gate still proves the original.
2. Compiles [mod] sources and links them as one more piece at [mod] code (free flash after the
   image), their RAM at [mod] ram (above everything the image uses, the stack included), every
   other symbol at its map address. The mod's RAM is not in the original's region table, so
   nothing initialises it at reset, zero or not: the mod sets its own state up (on first use, or
   in a reset hook the edits point the vector at); RW data in the mod is noted.
3. Runs the gate (armgate.run) in the copy with the mod's symbols added to every link, so edited
   sources may call or point at the new code. Each piece and the data stage are linked as for the
   exact build; the result is the copy's whole image.
4. Checks that image against the original word by word: every difference must be one the
   [[mod.allow]] rules explain (a word that changes from one symbol's address to another's, a
   literal pool entry moved to the same offset in a new table, a Thumb immediate changed, or any
   change in a named range), and each rule must account for exactly its `count`, when it gives
   one. With no edits the image must be identical.
5. Writes the image with the new piece in the original file's format (<build>/mod/NAME-mod.hex
   by default), and the new piece's symbols beside it.

docs/config.md has [mod]; skills/modding-build the method."""
import fnmatch, os, re, shlex, shutil, subprocess, sys
from dataclasses import dataclass, field
import armtc, armmap, ehex, rwdata
import armgate as G

THUMB_IMM8 = {'movs': 0b00100, 'cmp': 0b00101, 'adds': 0b00110, 'subs': 0b00111}   # 001 op Rd imm8


def _int(v):
    return v if isinstance(v, int) else int(str(v), 0)


def load_mod(cfg):
    m = cfg.raw.get('mod')
    if not m: raise SystemExit(f'{cfg.file}: no [mod] section')
    for k in ('code', 'ram'):
        if k not in m: raise SystemExit(f'[mod] {k} is not set')
    return m


# ---- 1. the copy ---------------------------------------------------------------------------------
def copy_tree(cfg, tree, ignore=()):
    """The project as it stands on disk, without .git, the build directory or the toolchain."""
    if os.path.exists(tree): shutil.rmtree(tree)
    skip = {os.path.realpath(p) for p in (cfg.build, cfg.home or '', os.path.join(cfg.root, '.git'), tree) if p}

    def ign(d, names):
        out = [n for n in names if os.path.realpath(os.path.join(d, n)) in skip]
        out += [n for n in names if any(fnmatch.fnmatch(os.path.relpath(os.path.join(d, n), cfg.root), p) for p in ignore)]
        return out
    shutil.copytree(cfg.root, tree, ignore=ign, symlinks=True)
    rel = os.path.relpath(cfg.file, cfg.root)
    if rel.startswith('..'): raise SystemExit(f'{cfg.file}: the modded build needs the config inside the project root')
    return os.path.join(tree, rel)


def apply_edits(tree, mod, cfg_vars, log):
    for r in mod.get('replace', []):
        p = os.path.join(tree, r['file'])
        with open(p, encoding='latin1') as f: t = f.read()
        n = t.count(r['old']); want = int(r.get('count', 1))
        if n != want: raise SystemExit(f'{r["file"]}: expected {want} of {r["old"]!r}, found {n}')
        with open(p, 'w', encoding='latin1') as f: f.write(t.replace(r['old'], r['new']))
        log(f'edited {r["file"]} ({n} x {r["old"].strip()[:40]!r})')
    for patch in mod.get('patches', []):
        rp = subprocess.run(['patch', '-p1', '--forward', '-i', os.path.join(cfg_vars['root'], patch)],
                            cwd=tree, capture_output=True, text=True)
        if rp.returncode: raise SystemExit(f'{patch}: does not apply\n{rp.stdout}{rp.stderr}')
        log(f'applied {patch}')
    if mod.get('edit'):
        cmd = [x.format(**cfg_vars, tree=tree) for x in shlex.split(mod['edit'])]
        rp = subprocess.run(cmd, cwd=tree, capture_output=True, text=True)
        if rp.returncode: raise SystemExit(f'[mod] edit failed\n{rp.stdout}{rp.stderr}')
        log(f'ran {mod["edit"]}')


# ---- 2. the new piece -----------------------------------------------------------------------------
@dataclass
class ModPiece:
    base: int
    data: bytes
    at: dict                       # name -> (address, is code)
    symdefs: str


def link_mod_piece(cfg, tc, tree, mod, log):
    code, ram = _int(mod['code']), _int(mod['ram'])
    work = os.path.join(tree, 'build', 'modpiece'); os.makedirs(work, exist_ok=True)
    incs = [os.path.join(tree, d) for d in mod.get('include', [])] + [cfg.include.replace(cfg.root, tree, 1)]
    objs = []
    for s in mod.get('sources', []):
        src = os.path.join(tree, s)
        obj = os.path.join(work, G.stem(src) + '.o')
        if src.endswith('.s'):
            rc, out = tc.run('armasm', shlex.split(G.source_opts(cfg, src)) + [tc.inside(src), '-o', tc.inside(obj)], os.path.dirname(src))
            ok = rc == 0 and os.path.exists(obj)
        else:
            opts = G.source_opts(cfg, src) + ''.join(f' -I{tc.inside(d)}' for d in [os.path.dirname(src)] + incs if os.path.isdir(d))
            ok, out = tc.compile(src, obj, G.host_opts(tc, cfg, opts))
        if not ok: raise SystemExit(f'{s}: compile failed\n{out}')
        objs.append(obj)
    if not objs: return None
    m = armmap.Map.from_dir(os.path.join(tree, os.path.relpath(cfg.map, cfg.root)))
    defined = set()
    for o in objs: defined |= {x.name for x in tc.symbols(o).symbols if x.bind in ('Gb', 'Wk') and x.sec != 'Ref'}
    sym = os.path.join(work, 'image.sym')
    with open(sym, 'w') as f: f.write(armmap.symdefs_text(m.symdef_entries(defined, {})))
    sct = os.path.join(work, 'mod.sct')
    with open(sct, 'w') as f:
        f.write(f'LR_MOD 0x{code:08X} {{\n  ER_MOD 0x{code:08X} {{ *(+RO) }}\n'
                f'  ER_MODRAM 0x{ram:08X} UNINIT {{ *(+RW +ZI) }}\n}}\n')
    axf = os.path.join(work, 'mod.axf')
    if os.path.exists(axf): os.remove(axf)
    lst = os.path.join(work, 'mod.lst')
    args = ['--cpu', G.cpu(cfg), '--scatter', tc.inside(sct), '--no_startup', '--no_remove',
            '--diag_suppress', '6305,6314,6329', '--map', '--list', tc.inside(lst)]
    if G.microlib(cfg): args.append('--library_type=microlib')
    if cfg.home: args += ['--libpath', tc.inside(os.path.join(cfg.home, 'ARM', 'ARMCC', 'lib'))]
    rc, out = tc.run('armlink', args + [tc.inside(o) for o in objs + [sym]] + ['-o', tc.inside(axf)], work)
    if rc or not os.path.exists(axf): raise SystemExit('the mod piece failed to link\n' + out)
    _, secs, _ = G.listing(lst)
    with open(lst, encoding='latin1') as f:
        rw = [l for l in f if G._LMAP.match(l) and G._LMAP.match(l).group(3) == 'Data' and G._LMAP.match(l).group(4) == 'RW']
    if rw:              # armcc puts small zero globals in .data too (--bss_threshold): a note, not an error
        log('NOTE the mod\'s RW data gets no initial values at reset (nothing copies or zeroes the mod\'s RAM): '
            + ', '.join(l.split()[-2] + ' ' + l.split()[-1] for l in rw))
    hexf = os.path.join(work, 'mod.hex')
    rc, out = tc.run('fromelf', ['--i32combined', tc.inside(axf), '-o', tc.inside(hexf)], work)
    if rc: raise SystemExit(out)
    base, data = ehex.read(hexf)
    info = tc.symbols(axf)
    at = {s.name: (s.value & ~1 if s.type == 'Code' else s.value, s.type == 'Code') for s in info.symbols
          if s.type in ('Code', 'Data') and s.name in defined}          # not the symdefs' absolute ones
    lines = [f'0x{a | (1 if c else 0):08x} {"T" if c else "D"} {n}' for n, (a, c) in sorted(at.items())]
    log(f'mod piece: 0x{base:08X}-0x{base + len(data):08X} ({len(data)} bytes), {len(at)} symbols')
    return ModPiece(base, data, at, '\n'.join(lines) + '\n')


# ---- 4. what may differ ---------------------------------------------------------------------------
def check_allowed(orig, new, base, rules, resolve):
    """(ok, report): every differing word of new against orig (both from base; beyond either's
    end a byte is 0xFF) explained by a rule, and each rule's count met. resolve(name) gives
    (address, is code) of a map or mod symbol."""
    def addr(name, thumb=False):
        a, code = resolve(name)
        return a | (1 if code and thumb else 0)
    counts = [0] * len(rules)
    bad = []
    n = max(len(orig), len(new))
    o_b = orig + b'\xff' * (n - len(orig)); n_b = new + b'\xff' * (n - len(new))
    ranges = []
    for i, r in enumerate(rules):
        if r['kind'] == 'range':
            if 'symbol' in r:
                a, _ = resolve(r['symbol']); ranges.append((i, a, a + _int(r['size'])))
            else:
                ranges.append((i, _int(r['start']), _int(r['end'])))
    for off in range(0, n, 4):
        o, w = o_b[off:off + 4], n_b[off:off + 4]
        if o == w: continue
        a = base + off
        hit = next((i for i, s, e in ranges if s <= a < e), None)
        if hit is not None: counts[hit] += 1; continue
        ov, nv = int.from_bytes(o.ljust(4, b'\xff'), 'little'), int.from_bytes(w.ljust(4, b'\xff'), 'little')
        done = False
        for i, r in enumerate(rules):
            k = r['kind']
            if k == 'word':
                if 'at' in r and _int(r['at']) != a: continue
                if ov == addr(r['from'], True) and nv == addr(r['to'], True): counts[i] += 1; done = True; break
            elif k == 'pool':
                f, t = addr(r['from']), addr(r['to'])
                size = _int(r['size']) if 'size' in r else resolve_size(resolve, r['from'])
                if f <= ov < f + size and nv == ov - f + t: counts[i] += 1; done = True; break
        if done: continue
        ok = True
        for h in (0, 2):                    # Thumb immediates, a halfword at a time
            ho, hn = int.from_bytes(o[h:h + 2], 'little'), int.from_bytes(w[h:h + 2], 'little')
            if ho == hn: continue
            for i, r in enumerate(rules):
                if r['kind'] != 'imm': continue
                op = THUMB_IMM8[r['insn']]
                if ho >> 11 == op and hn >> 11 == op and (ho & 0x0700) == (hn & 0x0700) \
                   and ho & 0xFF == _int(r['old']) and hn & 0xFF == _int(r['new']):
                    counts[i] += 1; break
            else:
                ok = False
        if not ok: bad.append(f'0x{a:08X}: {ov:08X} -> {nv:08X}')
    lines = []
    for i, r in enumerate(rules):
        want = r.get('count')
        label = f'{r["kind"]} ' + ', '.join(f'{k}={v}' for k, v in r.items() if k not in ('kind', 'count'))
        lines.append(f'{counts[i]} x {label}' + (f' (want {want})' if want is not None and counts[i] != want else ''))
        if want is not None and counts[i] != _int(want): bad.append(f'{label}: {counts[i]} found, want {want}')
    if bad: lines += ['not explained or not as expected:'] + ['  ' + b for b in bad[:30]]
    return not bad, lines


def resolve_size(resolve, name):
    size = getattr(resolve, 'size', None)
    if size is None: raise SystemExit(f'pool rule for {name}: give a size')
    return size(name)


# ---- the whole build ------------------------------------------------------------------------------
@dataclass
class ModResult:
    ok: bool
    lines: list = field(default_factory=list)
    out: str = None
    orig: bytes = None
    new: bytes = None
    base: int = None
    resolve: object = None


def build(cfg, cfg_path=None, jobs=None, log=print):
    mod = load_mod(cfg)
    res = ModResult(ok=False)
    tree = os.path.join(cfg.build, 'mod', 'tree')
    log(f'copying the project to {tree}')
    tree_cfg_path = copy_tree(cfg, tree, mod.get('copy_ignore', []))
    apply_edits(tree, mod, {'root': cfg.root, 'build': cfg.build, 'python': sys.executable}, log)
    overrides = {'home': cfg.home, 'image': cfg.image}
    tcfg = G.load_config(tree_cfg_path, overrides)
    tc = armtc.toolchain(tcfg)
    piece = link_mod_piece(tcfg, tc, tree, mod, log)
    img = ehex.Image(cfg.image)
    if piece:
        # the new piece in free flash and RAM nothing else uses
        end = piece.base + len(piece.data)
        inside = [a for a in range(max(piece.base, img.base), min(end, img.end)) if img.data[a - img.base] != 0xFF]
        if inside: raise SystemExit(f'[mod] code 0x{piece.base:08X}: the image has data there (0x{inside[0]:08X})')
        top = max([r.exec + r.size for r in _regions(img)] + [int.from_bytes(img.bytes(img.base, 4), 'little')])
        if _int(mod['ram']) < top: raise SystemExit(f'[mod] ram 0x{_int(mod["ram"]):08X} is below 0x{top:08X}, '
                                                    'the top of what the image uses (data and stack)')
    lines = []
    r = G.run(tcfg, tree_cfg_path, overrides, jobs=jobs, extra_symdefs=piece.symdefs if piece else '', log=lines.append)
    for l in lines:
        if l.startswith(('piece', 'data stage', '    load image', 'FAIL', '    link failed')): log(l)
    if r.image is None or any(p.got is None for p in r.pieces.values()) or not (r.data and r.data.got):
        log('\n'.join(lines)); log('the gate in the copy did not link every piece'); return res
    m = armmap.Map.from_dir(cfg.map)

    linked = {}                    # code symbols of the pieces' links: labels the map has no row for
    for p in r.pieces.values(): linked.update(p.at)

    def resolve(name):
        if piece and name in piece.at: return piece.at[name]
        row = m.by_name.get(name)
        if row is not None: return row.addr, name in m.code_names
        if name in linked: return linked[name], True
        raise SystemExit(f'[mod] allow: no symbol {name} in the map, the image\'s links or the mod')
    resolve.size = lambda name: m.by_name[name].size
    orig = img.bytes(r.base, min(img.end, r.base + len(r.image)) - r.base)
    ok, report = check_allowed(orig, r.image, r.base, mod.get('allow', []), resolve)
    for l in report: log('    ' + l)
    log('against the original: ' + ('every difference explained' if ok else 'FAIL'))
    res.orig, res.new, res.base, res.resolve = orig, r.image, r.base, resolve
    full_end = max(r.base + len(r.image), piece.base + len(piece.data) if piece else 0)
    full = bytearray(b'\xff' * (full_end - r.base))
    full[:len(r.image)] = r.image
    if piece: full[piece.base - r.base:piece.base - r.base + len(piece.data)] = piece.data
    name = os.path.splitext(os.path.basename(cfg.image))[0]
    hexlike = open(cfg.image, 'rb').read(1) == b':'
    out = os.path.join(cfg.root, mod['out']) if mod.get('out') else \
        os.path.join(cfg.build, 'mod', name + '-mod' + ('.hex' if hexlike else '.bin'))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, 'wb') as f:
        f.write(ehex.write(r.base, bytes(full), like=cfg.image).encode('ascii') if hexlike else bytes(full))
    if piece:
        with open(os.path.splitext(out)[0] + '.syms.tsv', 'w') as f:
            f.write(''.join(f'{n}\t{a:08X}\t{"code" if c else "data"}\n' for n, (a, c) in sorted(piece.at.items())))
    log(f'wrote {out}')
    res.ok, res.out = ok, out
    return res


def _regions(img):
    try:
        return rwdata.find_table(img)
    except ValueError:
        return []


def main(cfg, argv, cfg_path=None):
    if '-h' in argv or '--help' in argv: print(__doc__); return 0
    jobs = int(argv[argv.index('--jobs') + 1]) if '--jobs' in argv else None
    r = build(cfg, cfg_path, jobs=jobs, log=lambda s='': print(s, flush=True))
    return 0 if r.ok else 1


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'tools'))
    import config, armmod
    path, a = config.pop_config(sys.argv[1:])
    sys.exit(armmod.main(config.load(path), a, path))
