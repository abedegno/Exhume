"""Match one source against a C2000 image (the ti-c2000 profile's match.py).

    python3 tools/match.py SRC [FUNC ...] [--cgt VERSION|HOME] [--all-cgt] [-v] [--keep DIR]
    (Exhume's match.py hands over to main(); or run this file with --config PATH)

SRC is a path, or the file name or stem of a source under src/. It is compiled with the
project's options (a `/* opts: */` line replaces them, `/* cflags: */` adds to them, `/* cgt:
VERSION */` picks another installed compiler) and --gen_func_subsections=on, so each function is
a section of its own (.text:_name, ramfuncs:_name for `#pragma CODE_SECTION(f, "ramfuncs")`,
.switch:_name for its switch tables, .econst:_name for a const object). An .asm file is
assembled by the same cl2000. The object is then linked with the native lnk2000, each section
where the map puts it:

  - a function at its map address (a ramfunc at its run address; its words are compared with the
    image at its load address);
  - its switch tables at the map's sw_<function> row;
  - a const object at its data row (compared too);
  - a section no symbol places (an assembly file's reset branch) at the row whose notes say
    section=NAME (compared too);
  - .ebss where the map puts the variables it defines; file statics where the image's words put
    them (a note says so);
  - every symbol the file uses and does not define at its map address. A name the map does not
    have resolves only if it spells its address (sub_080120, var_A404).

So relocated words are not masked: the linker fills in each relocation, and it must come out as
the image's word, which checks that its target is the address the image uses. A target that does
not resolve is reported UNRESOLVED with the address the image's words hold.

Prints EXACT for each function or object that matches word for word, or the first differences
with both disassemblies side by side. --all-cgt compiles the file with every installed compiler
([c2000] others) and prints one line each. Exit status 0 when every object compared is EXACT."""
import argparse, os, re, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import c28tc, c28map, c28disasm

cname = c28map.cname


class Result:
    def __init__(self, name, addr, run, size, kind):
        self.name, self.addr, self.run, self.size, self.kind = name, addr, run, size, kind
        self.ours, self.diffs, self.relocs, self.problems, self.plain_diffs = [], [], [], [], []
        self.code = False

    @property
    def exact(self):
        return not self.diffs and not self.problems and len(self.ours) == self.size and all(r[3] for r in self.relocs)


def quote(sec):
    return '"%s"' % sec if re.search(r'[^\w.$]', sec) else sec


def link(tc, obj, outdir, placements, symdefs):
    """lnk2000 (native) of obj with each section at its address; the .out path."""
    cmd = os.path.join(outdir, 'm.cmd')
    with open(cmd, 'w') as f:
        f.write('-a\n-o %s\n-m %s\n' % (os.path.join(outdir, 'm.out'), os.path.join(outdir, 'm.map')))
        f.write('--disable_auto_rts\n--disable_clink\n')
        f.write('SECTIONS\n{\n')
        for sec, addr, page in placements:
            f.write('    %s : load = 0x%X, PAGE = %d\n' % (quote(sec), addr, page))
        f.write('}\n')
        for s, v in sorted(symdefs.items()):
            f.write('%s = 0x%X;\n' % (s, v))
    out = os.path.join(outdir, 'm.out')
    if os.path.exists(out): os.remove(out)
    p = tc.run_native('lnk2000', [obj, cmd], cwd=outdir, check=False)
    if p.returncode or not os.path.exists(out):
        raise c28tc.ToolError('lnk2000 failed:\n' + p.stdout + p.stderr)
    return out


def plan(o, m, img, park=0x200000, park_data=0x300000):
    """Placements and symbol definitions for object o:
    (placements [(section, address, page)], symdefs {symbol: address},
     targets [(Result, section, offset in section, load address)], notes, unresolved)."""
    placements, targets, notes = [], [], []
    allsyms = dict(o.statics()); allsyms.update(o.defined())
    bysec = {}
    for n, (sec, v) in allsyms.items(): bysec.setdefault(sec, []).append((v, n))
    parks = [park, park_data]
    for name, sec in o.sections.items():
        if sec.kind not in ('text', 'data', 'bss') or name.startswith('.debug') or name == '$build.attributes':
            continue
        if sec.size == 0: continue
        syms = sorted(bysec.get(name, []))
        base = None
        page = 0 if sec.kind == 'text' or name.startswith(('.econst', '.switch', '.cinit')) else 1
        if name.startswith('.switch:'):
            owner = cname(name.split(':', 1)[1])
            row = m.drows.get('sw_' + owner)
            if row:
                base = row.addr
                targets.append((Result('sw_' + owner, row.addr, row.addr, row.size, 'switch'), name, 0, row.addr))
            else:
                notes.append('%s: no sw_%s row in map/data.tsv' % (name, owner))
        elif name.startswith('.cinit'):
            pass
        else:
            for v, n in syms:
                c = cname(n)
                row = m.frows.get(c) if sec.kind == 'text' else m.drows.get(c)
                at = None
                if row:
                    at, size, load, kind = c28map.run_addr(row), row.size, row.addr, row.kind
                elif m.resolve(n) is not None:
                    at = m.resolve(n); size, load, kind = None, at, ('c' if sec.kind == 'text' else 'data')
                if at is None: continue
                b = at - v
                if base is None: base = b
                elif base != b:
                    notes.append('%s: %s wants the section at 0x%X, another symbol at 0x%X' % (name, n, b, base))
                if sec.kind == 'bss': continue
                if size is None:
                    size = next((w for w, _ in syms if w > v), sec.size) - v
                targets.append((Result(c, load, at, size, kind), name, v, load))
        if base is None and sec.kind in ('text', 'data') and not name.startswith(('.cinit', '.switch')):
            row = m.section_row(name)
            if row:
                base, page = row.addr, 0
                targets.append((Result(row.name, row.addr, row.addr, row.size, row.kind), name, 0, row.addr))
        if base is None and sec.kind in ('bss', 'data') and not name.startswith(('.cinit', '.switch')):
            # file statics have no map name: take the address the image's words give the section,
            # where this file's placed functions use it
            vals = []
            for tname, tsec in o.sections.items():
                if tsec.kind != 'text': continue
                for v, n in sorted(bysec.get(tname, [])):
                    row = m.frows.get(cname(n))
                    if row is None: continue
                    for sym, got in c28tc.derive_bases(tsec, row.addr - v, img).items():
                        if sym == name: vals += got
                    break
            if vals:
                base = max(set(vals), key=vals.count)
                notes.append('%s: placed at 0x%X from the image\'s words (%d of %d uses agree)'
                             % (name, base, vals.count(base), len(vals)))
        if base is not None and img.contains(base):
            page = 0
        if base is None:
            base = parks[page]
            parks[page] += sec.size + 64
            if sec.kind == 'text' or name.startswith(('.econst', '.switch')):
                notes.append('%s: no map address (not compared)' % name)
        placements.append((name, base, page))
    symdefs, unresolved = {}, []
    for n in o.undefined():
        v = m.resolve(n)
        if v is None:
            unresolved.append(n); v = 0
        symdefs[n] = v
    return placements, symdefs, targets, notes, unresolved


def linked_sections(tc, out):
    return {name: s.data for name, s in tc.obj(out).sections.items() if s.data}


def compare(o, linked, targets, unresolved, img):
    results = []
    for r, secname, off, load in targets:
        sec = o.sections[secname]
        r.code = sec.kind == 'text'
        r.ours = linked.get(secname, [])[off:off + r.size]
        want = img.words(load, r.size) if img.contains(load, r.size) else []
        if len(want) != r.size: r.problems.append('outside the image')
        r.diffs = [k for k in range(min(len(r.ours), len(want))) if r.ours[k] != want[k]]
        if len(r.ours) != r.size: r.problems.append('size %d, map %d' % (len(r.ours), r.size))
        bits = c28tc.reloc_masks(sec)[off:off + r.size]
        # where bits that no relocation writes differ, the code itself differs, and what the
        # relocations hold there says nothing
        r.plain_diffs = [k for k in r.diffs if k < len(bits) and (r.ours[k] ^ want[k]) & ~bits[k] & 0xFFFF]
        for roff, typ, sym in sec.relocs:
            if typ in c28tc.RELOC_NOFIELD or not off <= roff < off + r.size: continue
            k = roff - off
            fields = c28tc.RELOC_FIELDS.get(typ, [(0, 0xFFFF), (1, 0xFFFF)])
            ok = all(k + j < len(r.ours) and k + j < len(want) and (r.ours[k + j] ^ want[k + j]) & mk == 0
                     for j, mk in fields)
            r.relocs.append((k, typ, sym, ok, c28tc.decode(typ, want, k) if want else None))
            if sym in unresolved:
                r.problems.append('UNRESOLVED %s (the image has 0x%X at +%d)' % (sym, c28tc.decode(typ, want, k) or 0, k))
        results.append(r)
    return results


def side_by_side(tc, img, r, context=6, maxlines=24):
    """The first difference of r with a few instructions before it, ours beside the image's."""
    if not r.diffs and len(r.ours) == r.size: return []
    first = r.diffs[0] if r.diffs else min(len(r.ours), r.size)
    ours = c28disasm.disasm(tc, r.ours, r.run)
    theirs = c28disasm.disasm(tc, img.words(r.addr, r.size), r.run)
    lo = r.run + max(0, first - context * 2)
    a = [x for x in ours if x[0] >= lo][:maxlines]
    b = [x for x in theirs if x[0] >= lo][:maxlines]
    fmt = lambda x: ('%06x %-10s %s' % (x[0], c28tc.fmt_words(x[1]), x[2]))[:52] if x else ''
    lines = ['      %-52s | %s' % ('ours', 'image')]
    for i in range(max(len(a), len(b))):
        x = a[i] if i < len(a) else None
        y = b[i] if i < len(b) else None
        mark = '  ' if x and y and x[1] == y[1] and x[0] == y[0] else '**'
        lines.append('   %s %-52s | %s' % (mark, fmt(x), fmt(y)))
    return lines


def match_source(tc, src, funcs=None, cgt=None, keep=None, wine=None, m=None, img=None):
    """Compile, place, link and compare: (results, notes, unresolved, object, object path)."""
    s = tc.s
    src = os.path.abspath(src)
    work = keep or os.path.join(s.build, 'match', c28tc.stem(src))
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    obj = tc.compile(src, work, cgt=cgt, wine=wine)
    o = tc.obj(obj)
    m = m or c28map.Map.from_dir(s.map_dir)
    img = img or c28tc.Image.of(s)
    placements, symdefs, targets, notes, unresolved = plan(o, m, img, s.park, s.park_data)
    if funcs: targets = [t for t in targets if t[0].name in funcs]
    out = link(tc, obj, work, placements, symdefs)
    return compare(o, linked_sections(tc, out), targets, unresolved, img), notes, unresolved, o, obj


def report(tc, img, results, notes, verbose=False, out=print):
    ok = True
    for r in results:
        if r.exact:
            rel = ', '.join('%s ok' % cname(sy) for _, _, sy, _, _ in r.relocs[:6] if sy and not sy.startswith('$'))
            out('  %-28s %06X %5d  EXACT%s' % (r.name, r.addr, r.size, ('  (relocations: %s)' % rel) if verbose and rel else ''))
            continue
        ok = False
        what = []
        if r.diffs: what.append('%d words differ, first at +%d (%06X)' % (len(r.diffs), r.diffs[0], r.addr + r.diffs[0]))
        what += r.problems
        bad = [(sy, w) for k, t, sy, g, w in r.relocs if not g]
        if bad and not r.plain_diffs:
            what.append('relocations not as the image: ' + ', '.join('%s (image 0x%X)' % (cname(sy or '?'), w or 0) for sy, w in bad[:6]))
        out('  %-28s %06X %5d  DIFF: %s' % (r.name, r.addr, r.size, '; '.join(what)))
        if getattr(r, 'code', False):
            for line in side_by_side(tc, img, r): out(line)
    for n in notes: out('  note: ' + n)
    return ok


def run(s, argv, out=print):
    ap = argparse.ArgumentParser(prog='match.py', description='Match one source against a C2000 image.')
    ap.add_argument('src'); ap.add_argument('funcs', nargs='*')
    ap.add_argument('--cgt', help='another compiler: a version under [c2000] others, or a home')
    ap.add_argument('--keep', help='the work directory (default <build>/match/STEM)')
    ap.add_argument('-v', action='store_true', help='list the relocations of each exact function')
    ap.add_argument('--all-cgt', action='store_true', help='every installed compiler ([c2000] others)')
    a = ap.parse_args(argv)
    tc = c28tc.Toolchain(s)
    src = s.source(a.src)
    m = c28map.Map.from_dir(s.map_dir); img = c28tc.Image.of(s)
    if a.all_cgt:
        homes = c28tc.cgt_homes(s.others)
        if not homes: out('no compilers match [c2000] others'); return 2
        for home in homes:
            try:
                results, *_ = match_source(tc, src, a.funcs or None, cgt=home, m=m, img=img)
            except c28tc.ToolError:
                out('%-28s compile or link failed' % os.path.basename(home)); continue
            n = sum(1 for r in results if r.exact)
            out('%-28s %d of %d EXACT  %s' % (os.path.basename(home), n, len(results),
                                              ' '.join(r.name for r in results if not r.exact)))
        return 0
    try:
        results, notes, unresolved, o, obj = match_source(tc, src, a.funcs or None, cgt=a.cgt, keep=a.keep, m=m, img=img)
    except c28tc.ToolError as e:
        out(str(e)); return 2
    home = tc.home_for(src, a.cgt)
    out('%s: cl2000 %s %s' % (os.path.relpath(src), c28tc.cgt_version(home), ' '.join(tc.flags_for(src))))
    if not results:
        for n in notes: out('  note: ' + n)
        out('  nothing in this file has a map address'); return 1
    ok = report(tc, img, results, notes, a.v, out)
    out('%d of %d EXACT' % (sum(1 for r in results if r.exact), len(results)))
    return 0 if ok else 1


def main(cfg, argv):
    """Entry from Exhume's tools/match.py (cfg is its Config)."""
    if not argv or argv[0] in ('-h', '--help'): print(__doc__); return 0
    return run(c28tc.Settings.from_config(cfg), argv)


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(c28tc.EXHUME, 'tools'))
    import config
    p, rest = config.pop_config(sys.argv[1:])
    sys.exit(main(config.load(p), rest))
