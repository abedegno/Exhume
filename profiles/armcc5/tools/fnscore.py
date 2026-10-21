"""Score every function of one source against the image, compile only.

    python3 fnscore.py [--config PATH] SRC [FUNC ...] [--all] [--pool]

The source is compiled with its own options (as armmatch compiles it) into build/fnscore/<stem>.
Each code symbol of each code section of the object is cut at the next symbol, its literal data
($d mapping symbols) skipped, and disassembled; so is the image at the function's map address
(its literals and strings found from the pc-relative loads and adr that read them, and a bounded
tbb/tbh table from the compare and branch that guard it). Before the two are compared, branch
targets become instruction indexes inside the function, bl targets and branches the object
leaves to a relocation are masked, and pc-relative offsets are dropped, so a size change
elsewhere neither hides nor invents a difference. The listings are aligned with branch labels
masked, and then each aligned branch must reach the instruction the alignment pairs with the
original's target.

One line per function: `=` when the instructions agree, else the number of differing
instructions; ` size 0xBUILD/0xMAP` when the sizes differ, and ` entry 0xBUILD/0xMAP` when the
function's offset in its section differs from its distance in the map from the section's lowest
mapped function (a function before it changed size). A code symbol with no map row is listed first as
UNMAPPED_CODE (a helper the original does not have). Then TOTAL, the sum.

FUNC names (or --all) also print the instruction diff of those functions; --pool prints, for
the named functions, the values their literal loads read, in code order, the image's beside the
build's (a build literal is its relocation's symbol, at its map address when the map has one,
plus the addend word; `*` marks a pair that differs).

Unlike armvariant.py, which scores one function of a --split_sections build, this scores a whole
file and works on a file built without --split_sections (one .text holding every function), as
objplace.py places it. It is a diagnostic for steering a search: only match.py or objplace.py
(the placed comparison, nothing masked) is a proof. Exit status 0 when every mapped function
scores `=` and none is unmapped, 1 otherwise, 2 when the compile fails."""
import difflib, os, re, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
for p in (HERE, os.path.join(EXHUME, 'tools')):
    if p not in sys.path: sys.path.insert(0, p)
import objplace          # noqa: E402

_MD = None


def md():
    global _MD
    if _MD is None:
        import capstone
        _MD = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    return _MD


def table_data(rows, ins):
    """The byte addresses of an armcc bounded pc-relative tbb/tbh table, with its padding, or an
    empty range.

    The table follows its tbb/tbh; its length comes from the unsigned bound check before it
    (`cmp` of the same index register, then `bhs` or `bhi`), which may be followed by an island
    of unconditional branches. Any other form is left undecoded rather than given a length
    guessed from nearby constants."""
    index = re.fullmatch(r'\[pc, (\w+)(?:, lsl #1)?\]', ins.op_str)
    if ins.mnemonic not in ('tbb', 'tbh') or index is None:
        return range(0)
    for i in range(len(rows) - 1, max(-1, len(rows) - 17), -1):
        m = re.fullmatch(r'cmp(?:\.w)? ' + re.escape(index[1]) + r', #(0x[0-9a-f]+|\d+)', rows[i][1])
        if m is None:
            continue
        following = rows[i + 1:]
        if not following or not re.match(r'bh[si](?:\.w)? ', following[0][1]):
            return range(0)
        if any(not re.match(r'b(?:\.w)? ', text) for _, text in following[1:]):
            return range(0)
        entries = int(m[1], 0) + int(following[0][1].startswith('bhi'))
        size = entries * (1 if ins.mnemonic == 'tbb' else 2)
        size = (size + 1) & ~1
        return range(ins.address + 4, ins.address + 4 + size)
    return range(0)


def decode(buf, at, skip=None, data=None, found=None):
    """[(address, text)] of the Thumb code in buf at address at. skip(addr) gives the bytes of
    data to skip there (0 for code); without it the byte addresses in data are skipped, and the
    bytes that pc-relative loads, adr and tbb/tbh tables read are added to found (data itself
    when found is not given) as they are met."""
    data = set() if data is None else data
    found = data if found is None else found
    rows, off = [], 0
    while off < len(buf):
        addr = at + off
        if skip:
            n = skip(addr)
        elif addr in data:
            n = 2
        elif addr % 4 == 2 and addr + 2 in data and buf[off:off + 2] == bytes(2):
            n = 2                                           # padding before a word-aligned literal
        else:
            n = 0
        if n:
            off += n; continue
        ins = next(md().disasm(buf[off:off + 4], addr, count=1), None)
        if ins is None:
            rows.append((addr, '.short 0x%04X' % int.from_bytes(buf[off:off + 2], 'little')))
            off += 2; continue
        text = f'{ins.mnemonic} {ins.op_str}'
        table = table_data(rows, ins)
        data.update(table); found.update(table)
        m = re.match(r'ldr(\.w)? \w+, \[pc, #(-?0x[0-9a-f]+|-?\d+)\]', text)
        if m:
            t = ((addr + 4) & ~3) + int(m.group(2), 0)
            found.update(range(t, t + 4))
        m = re.match(r'(adr(\.w)?|addw|subw) \w+, pc, #(0x[0-9a-f]+|\d+)', text) or \
            re.match(r'(adr)(\.w)? \w+, #(-?0x[0-9a-f]+)', text)
        if m:
            k = int(m.group(3), 0)
            t = ((addr + 4) & ~3) + (-k if m.group(1) == 'subw' else k)
            if at <= t < at + len(buf):                     # a string: to its NUL, word-aligned
                e = t
                while e < at + len(buf) and buf[e - at] != 0: e += 1
                found.update(range(t, (e + 4) & ~3))
        rows.append((addr, text))
        off += ins.size
    return rows


def image_data(img, lo, hi):
    """The byte addresses in [lo, hi) of the image that its code reads as literals, strings or
    tables (decoded again until no more are found)."""
    data = set()
    for _ in range(4):
        found = set()
        decode(img.bytes(lo, hi - lo), lo, data=data, found=found)
        if found == data: break
        data = found
    return data


def normalise(rows, relocated=()):
    """Branch targets as instruction indexes (L<n>); bl targets masked, and any branch at an
    address in relocated (its encoded target is the object's placeholder); pc-relative offsets
    dropped."""
    index = {a: i for i, (a, _) in enumerate(rows)}
    out = []
    for addr, text in rows:
        if re.match(r'(bl|blx) ', text):
            text = text.split()[0] + ' ?'
        elif addr in relocated and re.match(r'b', text):
            text = text.split()[0] + ' ?'
        else:
            m = re.match(r'(b|cbz|cbnz|b\.w|b[a-z][a-z](\.w)?) (.*)#0x([0-9a-f]+)$', text)
            if m:
                tgt = int(m.group(4), 16)
                text = f'{m.group(1)} {m.group(3)}L{index[tgt]}' if tgt in index else f'{m.group(1)} ?'
        text = re.sub(r'\[pc, #-?0x[0-9a-f]+\]', '[pc]', text)
        text = re.sub(r'(adr(\.w)?|addw|subw) (\w+), pc, #0x[0-9a-f]+', r'adr \3', text)
        out.append(text)
    return out


LABEL = re.compile(r'L(\d+)$')


def compare(exp, got):
    """The difflib opcodes of the differences between two normalised listings. The listings are
    aligned with branch labels masked; then each aligned branch must reach the instruction the
    alignment pairs with the original's target, or it counts as a difference."""
    mask = lambda xs: [LABEL.sub('L', x) for x in xs]
    blocks = difflib.SequenceMatcher(None, mask(exp), mask(got), autojunk=False).get_opcodes()
    pair = {}
    for tag, i1, i2, j1, j2 in blocks:
        if tag == 'equal':
            for k in range(i2 - i1): pair[i1 + k] = j1 + k
    ops = []
    for tag, i1, i2, j1, j2 in blocks:
        if tag != 'equal':
            ops.append((tag, i1, i2, j1, j2)); continue
        for k in range(i2 - i1):
            a, b = LABEL.search(exp[i1 + k]), LABEL.search(got[j1 + k])
            if a and b and pair.get(int(a.group(1))) != int(b.group(1)):
                ops.append(('replace', i1 + k, i1 + k + 1, j1 + k, j1 + k + 1))
    return ops


LITERAL = re.compile(r'ldr(\.w)? (\w+), \[pc, #(-?0x[0-9a-f]+|-?\d+)\]')


def literal_values(rows, word):
    """[(register, value)] of the pc-relative loads in rows; word(addr) reads a literal."""
    return [(m.group(2), word(((addr + 4) & ~3) + int(m.group(3), 0)))
            for addr, text in rows for m in [LITERAL.match(text)] if m]


def pool_lines(img, row, idata, o, idx, off, end, skip, amap):
    """The --pool lines of one function: the image's literal loads beside the build's."""
    sec = o.sections[idx]
    rels = {r.offset: r for r in o.relocs.get(idx, [])}

    def map_addr(rel):
        """The map address of a relocation's symbol; a section symbol's from another symbol of
        its section that the map has."""
        s = o.symbols.get(rel.sym)
        if rel.sname in amap.by_name: return amap.by_name[rel.sname].addr
        if s is None or s.type != 'Sect': return None
        for x in o.symbols.values():
            if x.sec == s.sec and x.type in ('Code', 'Data') and x.name in amap.by_name:
                return amap.by_name[x.name].addr - (x.value & ~1 if x.type == 'Code' else x.value)
        return None

    def build_word(a):
        w = struct.unpack_from('<I', sec.data, a)[0] if a + 4 <= len(sec.data) else 0
        rel = rels.get(a)
        if rel is None: return f'{w:08X}'
        at = map_addr(rel)
        return (f'{at:08X}' if at is not None else rel.sname) + (f'+{w:X}' if w else '')
    exp = literal_values(decode(img.bytes(row.addr, row.size), row.addr, data=set(idata)),
                         lambda a: '%08X' % struct.unpack('<I', img.bytes(a, 4))[0])
    got = literal_values(decode(bytes(sec.data[off:end]), off, skip), build_word)
    out = []
    for k in range(max(len(exp), len(got))):
        e = '%-4s %s' % exp[k] if k < len(exp) else ''
        g = '%-4s %s' % got[k] if k < len(got) else ''
        out.append('    %s %-20s %s' % (' ' if e.split()[-1:] == g.split()[-1:] else '*', e, g))
    return out


def score(o, amap, img, want=(), show_all=False, show_pool=False):
    """Score every mapped function of object o (an objplace.Obj) against the image: (lines,
    total differing instructions, unmapped code symbols)."""
    lines = []
    unmapped = [s.name for s in o.symbols.values()
                if s.type == 'Code' and isinstance(s.sec, int) and s.name not in amap.by_name]
    lines += ['UNMAPPED_CODE ' + n for n in unmapped]
    rows = [amap.by_name[s.name] for s in o.symbols.values() if s.type == 'Code' and s.name in amap.by_name]
    idata = image_data(img, min(r.addr for r in rows), max(r.addr + r.size for r in rows)) if rows else set()
    total = 0
    for idx, sec in sorted(o.sections.items()):
        if 'SHF_EXECINSTR' not in sec.flags: continue
        funcs = sorted((s.value & ~1, s.name) for s in o.symbols.values() if s.sec == idx and s.type == 'Code')
        mapped = [amap.by_name[n].addr for _, n in funcs if n in amap.by_name]
        if not mapped: continue
        base = min(mapped)                  # the section's start in the original
        states = objplace.mapping_states(o, idx)
        relocated = {r.offset for r in o.relocs.get(idx, [])}
        for k, (off, name) in enumerate(funcs):
            end = funcs[k + 1][0] if k + 1 < len(funcs) else len(sec.data)
            row = amap.by_name.get(name)
            if row is None: continue
            st = [(v, n) for v, n in states if v < end]

            def skip(addr, st=st, end=end):
                if objplace._state_at(st, addr) != '$d': return 0
                return min([v for v, _ in st if v > addr] + [end]) - addr
            got = normalise(decode(bytes(sec.data[off:end]), off, skip), relocated)
            exp = normalise(decode(img.bytes(row.addr, row.size), row.addr, data=set(idata)))
            ops = compare(exp, got)
            n = sum(max(i2 - i1, j2 - j1) for _, i1, i2, j1, j2 in ops)
            total += n
            size = '' if end - off == row.size else f' size {end - off:#x}/{row.size:#x}'
            expected = row.addr - base
            entry = '' if off == expected else f' entry {off:#x}/{expected:#x}'
            lines.append('%-20s %s%s%s' % (name, '=' if not n else n, size, entry))
            if show_pool and name in want:
                lines += pool_lines(img, row, idata, o, idx, off, end, skip, amap)
            if n and (show_all or name in want):
                for _, i1, i2, j1, j2 in ops:
                    lines += ['    - %4d %s' % (i1 + i, x) for i, x in enumerate(exp[i1:i2])]
                    lines += ['    + %4d %s' % (j1 + j, x) for j, x in enumerate(got[j1:j2])]
                    lines.append('')
    lines.append(f'TOTAL {total}')
    return lines, total, unmapped


def main(argv):
    import config, armtc, armmap, ehex
    path, a = config.pop_config(argv)
    args = [x for x in a if not x.startswith('--')]
    if not args: sys.exit(__doc__)
    cfg = config.load(path)
    src, want = os.path.abspath(args[0]), set(args[1:])
    stem = os.path.splitext(os.path.basename(src))[0]
    work = os.path.join(cfg.build, 'fnscore', stem); os.makedirs(work, exist_ok=True)
    tc = armtc.toolchain(cfg)
    obj = os.path.join(work, stem + '.o')
    ok, log = objplace.compile_source(cfg, tc, src, obj)
    if not ok:
        print('COMPILE FAILED')
        print('\n'.join(l for l in log.splitlines() if 'rror' in l)[:3000])
        return 2
    o = objplace.read_object(tc, obj)
    lines, total, unmapped = score(o, armmap.Map.from_dir(cfg.map), ehex.Image(cfg.image), want,
                                   '--all' in a, '--pool' in a)
    print('\n'.join(lines))
    return 0 if total == 0 and not unmapped else 1


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
