"""Disassemble words of a C2000 image with TI's dis2000.

    python3 c28disasm.py START [END | +COUNT]     # word addresses in hex; END is exclusive
    python3 c28disasm.py NAME                     # a function or data row of the map
    python3 c28disasm.py ... --run ADDR           # show the words as if at ADDR (a ramfunc's RAM)
    python3 c28disasm.py ... --load               # a ramfunc at its load address, not its run one
    python3 c28disasm.py ... --raw                # no map names
    (--config PATH, or $EXHUME_CONFIG)

dis2000 reads only linked objects, so the words go into one: a placeholder object, made once per
address and length, holding a single section of `.space` linked at the address. Its raw data is
then overwritten with the words (ofd2000 gives the file offset) and `dis2000 -i` (data as text)
reads them. The placeholder is cached in <build>/dis, so a disassembly costs one dis2000 run,
with nothing assembled. Call and branch targets the map names are shown with their names."""
import os, re, struct, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import c28tc, c28map

_DIS = re.compile(r'^([0-9a-fA-F]{8})\s+([0-9a-fA-F]{4})\s+(.*)$')
_CONT = re.compile(r'^([0-9a-fA-F]{8})\s+([0-9a-fA-F]{4})\s*$')


def placeholder(tc, addr, n):
    """(path, raw data offset) of a linked object with one section of n words at addr."""
    d = os.path.join(tc.s.build, 'dis')
    os.makedirs(d, exist_ok=True)
    key = os.path.join(d, 'ph_%06X_%X' % (addr, n))
    if os.path.exists(key + '.out') and os.path.exists(key + '.ptr'):
        with open(key + '.ptr') as f: return key + '.out', int(f.read())
    with c28tc.scratch(tc.s, 'ph') as w:
        with open(os.path.join(w, 'ph.asm'), 'w') as f:
            f.write('\t.sect "img"\n\t.space %d\n' % (16 * n))      # .space counts bits
        with open(os.path.join(w, 'ph.cmd'), 'w') as f:
            f.write('MEMORY { PAGE 0: M : origin = 0x%X, length = 0x%X }\n' % (addr, n))
            f.write('SECTIONS { img : > M, PAGE 0 }\n')
        tc.run_native('asm2000', ['-v28', '--float_support=fpu32', 'ph.asm'], cwd=w)
        tc.run_native('lnk2000', ['-a', '-o', 'ph.out', 'ph.obj', 'ph.cmd'], cwd=w)
        o = tc.obj(os.path.join(w, 'ph.out'))
        sec = o.sections['img']
        if sec.size != n or sec.paddr != addr: raise c28tc.ToolError('placeholder: img is not where it was linked')
        os.replace(os.path.join(w, 'ph.out'), key + '.out.tmp')
        os.replace(key + '.out.tmp', key + '.out')
        with open(key + '.ptr.tmp', 'w') as f: f.write(str(sec.ptr))
        os.replace(key + '.ptr.tmp', key + '.ptr')
        return key + '.out', sec.ptr


def patched(path, ptr, ws, dst):
    with open(path, 'rb') as f: b = bytearray(f.read())
    b[ptr:ptr + 2 * len(ws)] = struct.pack('<%dH' % len(ws), *ws)
    with open(dst, 'wb') as f: f.write(b)


def parse(text):
    """dis2000's listing as [(address, [words], text)]; a parallel instruction's second half
    (`|| MOV32 ...`, printed on a line of its own) is appended to the first."""
    out = []
    for line in text.splitlines():
        m = _DIS.match(line)
        if m and m.group(3).strip() and not m.group(3).startswith('  '):
            out.append([int(m.group(1), 16), [int(m.group(2), 16)], ' '.join(m.group(3).split())])
        elif out and line.strip().startswith('||') and not _DIS.match(line):
            out[-1][2] += ' ' + ' '.join(line.split())
        else:
            m = _CONT.match(line) or (m if m and not m.group(3).strip() else None)
            if m and out and int(m.group(1), 16) == out[-1][0] + len(out[-1][1]):
                out[-1][1].append(int(m.group(2), 16))
    return [tuple(x) for x in out]


def disasm(tc, ws, addr):
    """dis2000's reading of the words ws placed at word address addr."""
    if not ws: return []
    path, ptr = placeholder(tc, addr, len(ws))
    with c28tc.scratch(tc.s, 'dis') as w:
        p = os.path.join(w, 'w.out')
        patched(path, ptr, list(ws), p)
        return parse(tc.run_native('dis2000', ['-i', p], cwd=w).stdout)


def labels(m):
    names = {}
    for r in m.functions: names.setdefault(c28map.run_addr(r), r.name)
    for r in m.data: names.setdefault(r.addr, r.name)
    return names


def annotate(text, names):
    if not re.match(r'^(LCR|LB|LC|FFC|MOVL XAR\d, #)', text): return text
    return re.sub(r'0x[0-9a-f]{4,6}\b',
                  lambda m: m.group(0) + (' <%s>' % names[int(m.group(0), 16)] if int(m.group(0), 16) in names else ''),
                  text)


def main(argv):
    import argparse
    sys.path.insert(0, os.path.join(c28tc.EXHUME, 'tools'))
    import config
    path, argv = config.pop_config(argv)
    ap = argparse.ArgumentParser(prog='c28disasm.py', description=__doc__.split('\n\n')[0])
    ap.add_argument('start'); ap.add_argument('end', nargs='?')
    ap.add_argument('--run'); ap.add_argument('--load', action='store_true'); ap.add_argument('--raw', action='store_true')
    a = ap.parse_args(argv)
    s = c28tc.settings(path); tc = c28tc.Toolchain(s)
    img = c28tc.Image.of(s); m = c28map.Map.from_dir(s.map_dir)
    run = None
    try:
        start = int(a.start, 16)
        n = 0x40 if a.end is None else int(a.end[1:], 16) if a.end.startswith('+') else int(a.end, 16) - start
    except ValueError:
        r = m.frows.get(a.start) or m.drows.get(a.start)
        if r is None: sys.exit(f'{a.start}: not in the map')
        start, n = r.addr, r.size or 0x40
        if c28map.run_addr(r) != r.addr and not a.load: run = c28map.run_addr(r)
    if a.run: run = int(a.run, 16)
    if n <= 0 or not img.contains(start, n): sys.exit('range outside the image')
    at = run if run is not None else start
    names = {} if a.raw else labels(m)
    for addr, ws, text in disasm(tc, img.words(start, n), at):
        if addr in names and not a.raw: print(f'{names[addr]}:')
        load = '' if run is None else '%06x ' % (addr - at + start)
        print(f'{load}{addr:06x}  {c28tc.fmt_words(ws):<10} {annotate(text, names)}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
