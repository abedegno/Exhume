"""Match one source against a linked image (the armcc5 profile's match.py).

    python3 tools/match.py SRC [--dis NAME]       (Exhume's match.py hands over to main())

Compiles SRC, lists its sections and symbols with fromelf, places every allocatable section
at the address the map (or a placeholder name, or an /* at: */ line) gives one of its
symbols, links it with a symdefs file defining every other map symbol at its real address,
and compares each region's bytes with the image. Every address is real, so a wrong extern
shows as a byte difference: there is no separate verify step. RAM sections are placed but not
compared until the map has the RW image."""
import difflib, os, sys
from dataclasses import dataclass, field
import armtc, armmap, ehex, rwdata

ALLOC = ('.constdata', '.conststring', '.data', '.bss')
RAM = 0x20000000
UNPLACED = 0x0F000000          # where data the map does not know is linked: no real address


def allocatable(name, flags=None):
    """A section the image holds: SHF_ALLOC when fromelf gave the flags, else armcc's names."""
    if flags is not None: return 'SHF_ALLOC' in flags
    return name.startswith('i.') or name in ALLOC


def is_code(info, idx):
    return 'SHF_EXECINSTR' in ((info.flags or {}).get(idx) or '') or info.sections[idx][0].startswith('i.')


def is_zi(info, idx):
    return (info.types or {}).get(idx) == 'SHT_NOBITS' or info.sections[idx][0] == '.bss'


def offset(sym):
    """A symbol's offset in its section: a Thumb function's value carries bit 0, data does not."""
    return sym.value & ~1 if sym.type == 'Code' else sym.value


def place(info, m, extra, unused_ok=False):
    """(placements [(section, addr)], defined {name: addr}, errors, unplaced [section]).

    A function section with no known address is an error. A data section with none is left
    unplaced and linked out of the way (UNPLACED); match() then refuses a whole match unless
    the source marks all its symbols /* dropped: */ (data the original's armlink left out)."""
    placements, defined, errors, unplaced = [], {}, [], []
    for idx, (sname, _) in sorted(info.sections.items()):
        if not allocatable(sname, (info.flags or {}).get(idx) if info.flags else None): continue
        members = [s for s in info.symbols if s.sec == idx and s.type in ('Code', 'Data')]
        bases = {}
        for s in members:
            a = m.address(s.name, extra)
            if a is not None: bases.setdefault(a - offset(s), []).append(s.name)
        if not bases:
            if is_code(info, idx) and not unused_ok:
                errors.append(f'no address for section {sname} (symbols: {", ".join(s.name for s in members) or "none"}); '
                              f'add an /* at: NAME 0xADDR */ line or a map row')
            else:
                unplaced.append(sname)            # data, or a library file's unused function
            continue
        if len(bases) > 1:
            errors.append(f'section {sname}: its symbols disagree on its address: ' +
                          '; '.join(f'0x{b:08X} from {", ".join(n)}' for b, n in sorted(bases.items())))
            continue
        base = next(iter(bases))
        al = (info.align or {}).get(idx, 1)
        if base % al:
            errors.append(f'section {sname} needs {al}-byte alignment, but its address 0x{base:08X} is not '
                          f'(a member of a wider type than the original, or __align)')
            continue
        placements.append((sname, base))
        for s in members: defined[s.name] = base + offset(s)
    return placements, defined, errors, unplaced


def section_bytes(region, size):
    """A region's bytes cut to its section's size: fromelf --bin pads a region to its alignment."""
    return region if size is None else region[:size]


def padding_ok(got, want, addr):
    """want is got plus 1-3 zero bytes of alignment padding ending on a 4-byte boundary."""
    extra = len(want) - len(got)
    return 0 < extra < 4 and want[:len(got)] == got and not any(want[len(got):]) and (addr + len(want)) % 4 == 0


def compare(got, want):
    """Offsets where got differs from want; a length difference counts at the shorter's end."""
    bad = [i for i in range(min(len(got), len(want))) if got[i] != want[i]]
    if len(got) != len(want): bad.append(min(len(got), len(want)))
    return bad


def disasm(buf, addr):
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    out, off = [], 0
    while off < len(buf):
        ins = next(md.disasm(buf[off:], addr + off, count=1), None)
        if ins is None:
            out.append(f'{addr + off:08X}  .short 0x{int.from_bytes(buf[off:off + 2], "little"):04X}'); off += 2; continue
        out.append(f'{ins.address:08X}  {ins.mnemonic} {ins.op_str}'); off += ins.size
    return out


@dataclass
class Result:
    whole: bool = False
    lines: list = field(default_factory=list)
    defined: dict = field(default_factory=dict)


def match(cfg, src, dis=None):
    res = Result()
    src = os.path.abspath(src)
    stem = os.path.splitext(os.path.basename(src))[0]
    work = os.path.join(cfg.build, stem); os.makedirs(work, exist_ok=True)
    tc = armtc.toolchain(cfg)
    with open(src, encoding='latin1') as f: text = f.read()
    obj = os.path.join(work, stem + '.o')
    if src.endswith('.s'):
        import re as _re
        m_opts = _re.search(r'^\s*;\s*opts:\s*(.*)$', text, _re.M)
        ok, log = tc.assemble(src, obj, m_opts.group(1) if m_opts else (cfg.asm_opts or '--cpu Cortex-M3'))
    else:
        _, opts = cfg.directives(src)
        ok, log = tc.compile(src, obj, opts or cfg.c_opts)
    if log.strip(): res.lines += log.strip().splitlines()
    if not ok: res.lines.append('-- compile failed'); return res
    info = tc.symbols(obj)
    m = armmap.Map.from_dir(cfg.map)
    extra = armmap.annotations(text)
    unused_ok = armmap.unused_dropped(text)
    placements, defined, errors, unplaced = place(info, m, extra, unused_ok)
    res.defined = defined
    if errors:
        res.lines += ['ERROR ' + e for e in errors]; res.lines.append('-- not linked'); return res
    scatter, regions = armmap.scatter_text(os.path.basename(obj), placements)
    dead = armmap.dropped(text)
    blocked = pending = False
    if unplaced:
        sel = ' '.join(f'{os.path.basename(obj)}({x})' for x in unplaced)
        scatter += f'LR_UNPLACED 0x{UNPLACED:08X} {{\n  ER_UNPLACED 0x{UNPLACED:08X} {{ {sel} }}\n}}\n'
        for x in unplaced:
            idx = next(i for i, (n, _) in info.sections.items() if n == x)
            syms = [s.name for s in info.symbols if s.sec == idx and s.type in ('Code', 'Data')]
            idx_code = is_code(info, idx)
            if idx_code and unused_ok:
                res.lines.append(f'NOTE section {x} ({", ".join(syms)}) is not in the image: unused, dropped by armlink')
            elif syms and set(syms) <= dead:
                res.lines.append(f'NOTE section {x} ({", ".join(syms)}) is marked dropped: linked at 0x{UNPLACED:08X}, not compared')
            else:
                blocked = True
                res.lines.append(f'UNPLACED section {x} ({", ".join(syms) or "none"}) is not in the map: not compared, so not a '
                                 f'whole match; give it an address, or mark data the original never had /* dropped: NAME */')
    with open(os.path.join(work, 'match.sct'), 'w') as f: f.write(scatter)
    with open(os.path.join(work, 'match.sym'), 'w') as f: f.write(armmap.symdefs_text(m.symdef_entries(set(defined), extra)))
    axf = os.path.join(work, stem + '.axf')
    inputs = [obj, os.path.join(work, 'match.sym')]
    if any(a % 4 for _, a in placements):
        pad_obj = os.path.join(work, armmap.PAD_OBJ); pad_c = os.path.join(work, 'exhume_pad.c')
        src_text = armmap.pad_source(placements)
        old = open(pad_c).read() if os.path.exists(pad_c) else None
        if old != src_text or not os.path.exists(pad_obj):
            with open(pad_c, 'w') as f: f.write(src_text)
            ok, log = tc.compile(pad_c, pad_obj, cfg.c_opts)
            if not ok: res.lines += log.splitlines() + ['-- pad object failed']; return res
        inputs.append(pad_obj)
    ok, log = tc.link(inputs, os.path.join(work, 'match.sct'), axf)
    if log.strip(): res.lines += log.strip().splitlines()
    if not ok: res.lines.append('-- link failed'); return res
    bins = tc.tobin(axf, os.path.join(work, 'bin'))
    img = ehex.Image(cfg.image)
    try:
        ram = rwdata.ram_image(img)
    except ValueError:
        ram = None            # no region table: RAM data can't be compared
    addr_of = dict(placements)
    names = {}
    for s in info.symbols:
        if s.type == 'Code' and isinstance(s.sec, int): names[info.sections[s.sec][0]] = s.name
    size_of = {n: z for n, z in info.sections.values()}
    sec_index = {n: i for i, (n, _) in info.sections.items()}
    code_sec = {n for i, (n, _) in info.sections.items() if is_code(info, i)}
    zi_sec = {n for i, (n, _) in info.sections.items() if is_zi(info, i)}
    good = total = 0; whole = True
    for region, section in regions.items():
        a = addr_of[section]
        got = section_bytes(bins.get(region, b'')[a % 4:], size_of.get(section))
        if section in zi_sec: got = bytes(size_of.get(section) or 0)     # fromelf writes no bytes for ZI
        label = names.get(section, section)
        if a >= RAM and ram is None:
            pending = True
            res.lines.append(f'{label:28} 0x{a:08X}  placed (RAM, not compared: the image has no region table)'); continue
        problems = []; padded = 0
        if section in code_sec:
            row = m.row_at(a)
            if row is None:
                problems.append('no map row (size unknown)'); n = len(got)
            else:
                n = row.size
                if row.size != len(got):
                    try:
                        full = img.bytes(a, row.size)
                    except ValueError:
                        full = b''
                    if padding_ok(got, full, a):
                        padded = row.size - len(got); got = got + bytes(padded)
                    else:
                        problems.append(f'size 0x{len(got):X} want 0x{row.size:X}')
        else:
            n = len(got)
            if a >= RAM and ram is not None and n:
                kinds = {ram.kind_at(x) for x in range(a, a + n)}
                if section in zi_sec and kinds != {'zero'}:
                    problems.append('zero-init section over initialised RAM in the original')
                if section not in zi_sec and 'zero' in kinds:
                    problems.append('initialised section over zero-init RAM in the original')
            for sym in info.symbols:
                if sym.sec != sec_index[section] or sym.type != 'Data' or sym.size is None: continue
                row = m.row_at(a + offset(sym))
                if row is None: problems.append(f'{sym.name}: no map row (size unknown)')
                elif row.size != sym.size: problems.append(f'{sym.name}: size 0x{sym.size:X} want 0x{row.size:X}')
        try:
            want = ram.bytes(a, n) if a >= RAM else img.bytes(a, n)
        except ValueError as e:
            res.lines.append(f'{label:28} 0x{a:08X}  {e}'); whole = False; continue
        bad = compare(got, want)
        total += len(got)
        if not bad and not problems:
            good += len(got); state = 'MATCH' + (f' (+{padded} padding)' if padded else '')
        else:
            whole = False
            state = '  '.join(([f'{len(bad)} bytes differ, first at +0x{bad[0]:X}'] if bad else []) + problems)
        res.lines.append(f'{label:28} 0x{a:08X}  {state}')
        if dis and dis == label:
            for t in difflib.unified_diff(disasm(want, a), disasm(got, a), 'image', 'build', lineterm='', n=2):
                res.lines.append('    ' + t)
    compared_ok = whole and total > 0
    res.whole = compared_ok and not blocked and not pending
    tail = '; WHOLE FILE MATCHES' if res.whole else \
           ('; PARTIAL (RAM data not compared yet)' if compared_ok and pending and not blocked else '')
    res.lines.append(f'-- {good}/{total} bytes matched' + tail)
    return res


def main(cfg, argv):
    if not argv or argv[0].startswith('-'): sys.exit(__doc__)
    dis = argv[argv.index('--dis') + 1] if '--dis' in argv else None
    r = match(cfg, argv[0], dis)
    print('\n'.join(r.lines))
    # 0 when a compare ran (matching or not, as Exhume's match.py); 1 when compile, placement or link failed
    return 0 if 'bytes matched' in r.lines[-1] else 1
