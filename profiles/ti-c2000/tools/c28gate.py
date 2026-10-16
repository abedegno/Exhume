"""The whole-image gate for a C2000 image: prove every source, link the whole image with TI's
linker, and compare it with the original, word for word.

    python3 tools/gate.py check [--with SRC ...] [--fail-test] [-v]
    (Exhume's gate.py hands over to main(); or run this file with --config PATH)

1. Verify. Every source under src/ (not the include directory) is compiled, or assembled, and
   matched as match.py does: each function, switch table, const object and named section must be
   EXACT at its map address. Objects are named by their file's stem, so stems must be unique.
2. Library. c28libfp.py places the runtime library's members in the image; the data sections
   their code uses go where the image's relocated words say.
3. Link. One lnk2000 run (the native one, or the project compiler's under Wine with
   [c2000.gate] linker = "cgt") places:
   - every proved section at its address (a ramfunc with load and run addresses);
   - each library member's sections at theirs, the member pulled with -u and named as
     lib<member>(section);
   - everything else that is not fill as blobs of the original words, one section per run,
     counted as original words;
   - the output section .cinit: the sources' and the members' .cinit sections in the order of
     [c2000.gate] cinit_order (the original link's order of the files with initialised data),
     at [c2000.gate] cinit; the linker ends the table itself (--rom_model);
   - .stack at [c2000.gate] stack, and anything without an address in parking ranges outside
     the device.
   Symbols the objects use and do not define come from the map, except those a placed library
   member defines and those the linker makes (cinit, binit, pinit, __stack and the like).
4. Compare. The linked .cinit records must be the image's, in order. hex2000 writes the linked
   image (holes filled) as a binary like the original's; it must be identical. A table says how
   many words each kind of source supplied (the coverage).

--with SRC compiles SRC in place of the src/ file with the same stem. --strict fails while any
word still comes from a blob. --fail-test changes one
word of a proved function in the linked output and checks that the compare reports exactly that
word, so a pass cannot come from comparing the original with itself. The Wine servers are stopped
at the end. Exit status 0 when every source is proved and the image is IDENTICAL."""
import argparse, collections, os, re, shutil, struct, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import c28tc, c28map, c28match, c28libfp

# names the linker defines itself under --rom_model; the compare checks them
LINKER_SYMS = {'cinit', 'binit', 'pinit', '__stack', '__STACK_END', '__STACK_SIZE', '___cinit__', '___binit__',
               '___pinit__', '___TI_pprof_out_hndl', '___TI_prof_data_start', '___TI_prof_data_size',
               '___c_args__', '___text__', '___etext__', 'etext'}
PARK_SPAN = 0x80000
TOP = 0x400000              # the 22-bit address space


def quote(s):
    return '"%s"' % s if re.search(r'[^\w.$]', s) else s


def memory(s):
    """MEMORY over the whole address space, so a section at a fixed address is always inside a
    range (outside every range the linker makes up a DEFAULT range, and a second one then fails
    to overlap it), with a parking range on each page for what is not compared."""
    out = ['MEMORY', '{']
    for page, park, tag in ((0, s.park, 'P'), (1, s.park_data, 'D')):
        if park: out.append('  PAGE %d: LOW%s : origin = 0x0, length = 0x%X' % (page, tag, park))
        out.append('  PAGE %d: PARK%s : origin = 0x%X, length = 0x%X' % (page, tag, park, PARK_SPAN))
        if park + PARK_SPAN < TOP:
            out.append('  PAGE %d: HIGH%s : origin = 0x%X, length = 0x%X' % (page, tag, park + PARK_SPAN, TOP - park - PARK_SPAN))
    return '\n'.join(out + ['}']) + '\n'


def parked(s, addr):
    return addr is not None and (s.park <= addr < s.park + PARK_SPAN or s.park_data <= addr < s.park_data + PARK_SPAN)


class Gate:
    def __init__(self, s, out=print):
        self.s, self.out = s, out
        self.tc = c28tc.Toolchain(s)
        self.dir = os.path.join(s.build, 'gate')
        self.g = s.gate

    def sources(self, extra=()):
        srcs = self.s.sources()
        for x in extra:
            srcs = [p for p in srcs if c28tc.stem(p) != c28tc.stem(x)] + [os.path.abspath(x)]
        return srcs

    def run(self, with_=(), fail_test=False, verbose=False, strict=False):
        s, out, tc = self.s, self.out, self.tc
        t0 = time.time()
        shutil.rmtree(self.dir, ignore_errors=True)
        os.makedirs(self.dir)
        img = c28tc.Image.of(s)
        m = c28map.Map.from_dir(s.map_dir)
        owner = {k: r for k, r in m.owners().items() if img.contains(k)}
        ok = True

        # ---- 1. verify ------------------------------------------------------------------------
        proved, sections, objs, symdefs, defined_by_src = {}, [], [], {}, set()
        cinit_in, status_of = {}, {}
        srcs = self.sources(with_)
        out('verify: %d source%s' % (len(srcs), '' if len(srcs) == 1 else 's'))
        wine = c28tc.Wine(s) if s.wine and s.prefix and os.path.isdir(s.prefix or '') else None
        try:
            if wine: wine.__enter__()
            for src in srcs:
                st = c28tc.stem(src)
                try:
                    results, notes, unres, o, obj = c28match.match_source(
                        tc, src, keep=os.path.join(self.dir, 'src', st), wine=wine, m=m, img=img)
                except c28tc.ToolError as e:
                    out('  %s: FAIL\n%s' % (os.path.relpath(src, s.root), e)); ok = False; continue
                objs.append(obj)
                placements, sd, targets, _, _ = c28match.plan(o, m, img, s.park, s.park_data)
                good = [r for r in results if r.exact]
                unplaced = [n for n in notes if 'not compared' in n or 'no sw_' in n or 'wants' in n]
                status = 'EXACT' if len(good) == len(results) and not unplaced and not unres else 'FAIL'
                status_of[st] = status
                nf = sum(1 for r in results if r.code)
                out('  %-36s %-5s %d function%s, %d data object%s' % (
                    os.path.relpath(src, s.root), status, nf, '' if nf == 1 else 's',
                    len(results) - nf, '' if len(results) - nf == 1 else 's'))
                if status != 'EXACT':
                    ok = False
                    c28match.report(tc, img, results, notes, out=out)
                for r in results:
                    for k in range(r.addr, r.addr + r.size): proved[k] = st if r in good else None
                for name, base, page in placements:
                    sec = o.sections[name]
                    load = run = base
                    for r, sname, off, ld in targets:
                        if sname == name and ld != r.run:
                            load = ld - off; break
                    if name.startswith('.cinit'):
                        cinit_in.setdefault(st, []).append((obj, name, sec.size, len(c28tc.cinit_records(sec.data))))
                        continue
                    if parked(s, base): load = run = None
                    sections.append((obj, name, load, run, page))
                defined_by_src.update(o.defined())
                symdefs.update(sd)
        finally:
            if wine: wine.__exit__(None, None, None)
        for n in [n for n in symdefs if n in defined_by_src]: del symdefs[n]

        # ---- 2. library -----------------------------------------------------------------------
        lib, libdir, members, found = c28libfp.find(tc, img)
        libname = os.path.basename(lib)
        lib_words, lib_secs, used = set(), [], {}
        for f in found:
            lib_secs.append((f['member'], f['section'], f['addr'], 0))
            lib_words.update(range(f['addr'], f['addr'] + f['size']))
            used.setdefault(f['member'], members[f['member']])
        placed = {(mm, sn) for mm, sn, _, _ in lib_secs}
        member_of = {}
        for mm, o in used.items():
            for n, (sn, v) in o.defined().items(): member_of.setdefault(n, (mm, sn, v))
        rts_from_image = {}
        for f in found:
            if f['kind'] != 'text': continue
            o = used[f['member']]
            for sym, vals in c28tc.derive_bases(o.sections[f['section']], f['addr'], img).items():
                v = collections.Counter(vals).most_common(1)[0][0]
                if sym in o.sections:
                    target = (f['member'], sym, v)
                elif sym in member_of and used[member_of[sym][0]].sections[member_of[sym][1]].kind in ('bss', 'data'):
                    mm, sn, off = member_of[sym]
                    target = (mm, sn, v - off)
                else:
                    target = None
                if target:
                    mm, sn, a = target
                    if (mm, sn) not in placed and used[mm].sections[sn].kind in ('bss', 'data'):
                        lib_secs.append((mm, sn, a, 1 if used[mm].sections[sn].kind == 'bss' or not img.contains(a) else 0))
                        placed.add((mm, sn))
                    continue
                if sym == '(internal)' or sym in member_of or sym in defined_by_src: continue
                mv = m.resolve(sym)
                if mv is not None and mv != v and sym not in LINKER_SYMS:
                    out('  library: %s is 0x%X in the image, 0x%X in the map' % (sym, v, mv)); ok = False
                rts_from_image[sym] = v
        order = list(self.g.get('cinit_order', []))
        # a member named in cinit_order: its .cinit goes into .cinit; a member with no code in the
        # image is linked for its records, its .ebss where the image's code has its symbols
        for e in order:
            if not e.startswith('rts:'): continue
            mm = e[4:]
            if mm not in members: out('  cinit_order: %s is not a member of %s' % (mm, libname)); ok = False; continue
            if mm not in used:
                o = used[mm] = members[mm]
                for n, (sn, v) in o.defined().items():
                    if o.sections[sn].kind == 'bss' and n in rts_from_image and (mm, sn) not in placed:
                        lib_secs.append((mm, sn, rts_from_image[n] - v, 1)); placed.add((mm, sn))
            for name in used[mm].sections:
                if name.startswith('.cinit'): placed.add((mm, name))
        for mm, o in used.items():
            for name, sec in o.sections.items():
                if sec.kind in ('text', 'data', 'bss') and sec.size and not name.startswith('.debug') and (mm, name) not in placed:
                    lib_secs.append((mm, name, None, 1))
        for n, v in rts_from_image.items():
            if n not in LINKER_SYMS: symdefs.setdefault(n, v)
        # a symbol a linked member defines comes from the member: a definition from the map would
        # keep the linker from taking the member at all
        for mm, o in used.items():
            for n in o.defined(): symdefs.pop(n, None)
        for n in LINKER_SYMS: symdefs.pop(n, None)

        # ---- 3. what supplies each word, blobs and the link ------------------------------------
        supplied = {}
        for k in range(img.base, img.end):
            if k in proved: supplied[k] = 'proved' if proved[k] else 'unproved'
            elif k in lib_words: supplied[k] = 'library'
            elif k not in owner and img.W(k) == s.fill: supplied[k] = 'fill'
            else: supplied[k] = 'original'
        cinit_spec, cin_lo, cin_rec_end, cin_img, n_src_records = [], None, None, [], 0
        if order or cinit_in:
            if 'cinit' not in self.g: out('  [c2000.gate] cinit (the start of the image\'s .cinit) is not set'); return 1
            cin_lo = c28tc._int(self.g['cinit'])
            cin_img = c28tc.cinit_records(img.words(cin_lo, img.end - cin_lo))
            cin_rec_end = cin_lo + (cin_img[-1][0] + cin_img[-1][2] + cin_img[-1][1] if cin_img else 0)
            k = cin_lo
            for e in order:
                if e.startswith('rts:'):
                    mm = e[4:]
                    if mm not in used: continue
                    n = sum(sec.size for name, sec in used[mm].sections.items() if name.startswith('.cinit'))
                    cinit_spec.append('%s<%s>(.cinit)' % (libname, mm))      # its subsections in the linker's order
                    for j in range(k, k + n): supplied[j] = 'library'
                    k += n
                elif e in cinit_in:
                    for obj, name, size, nrec in cinit_in.pop(e):
                        cinit_spec.append('"%s"(%s)' % (obj, name))
                        n_src_records += nrec
                        for j in range(k, k + size): supplied[j] = 'proved' if status_of.get(e) == 'EXACT' else 'unproved'
                        k += size
                else:
                    out('  cinit_order: %s has no .cinit' % e); ok = False
            for st in cinit_in:
                out('  %s: .cinit records, but not in [c2000.gate] cinit_order' % st); ok = False
            if k != cin_rec_end:
                out('  cinit: the sections in cinit_order are %d words, the image\'s records %d' % (k - cin_lo, cin_rec_end - cin_lo))
                ok = False
            tail = int(self.g.get('cinit_tail', 2))
            for j in range(cin_rec_end, min(cin_rec_end + tail, img.end)): supplied[j] = 'linker'
        breaks = {x for x in (cin_lo, cin_rec_end) if x is not None}
        blobs, k = [], img.base
        while k < img.end:
            if supplied[k] != 'original': k += 1; continue
            j = k + 1
            while j < img.end and supplied[j] == 'original' and j - k < 0x4000 and j not in breaks: j += 1
            blobs.append((k, j)); k = j
        objs_all = list(objs)
        if blobs:
            with open(os.path.join(self.dir, 'blobs.asm'), 'w') as f:
                f.write('; the words of the image that no proved source or library member supplies yet\n')
                for lo, hi in blobs:
                    f.write('\t.sect "blob_%06X"\n' % lo)
                    ws = img.words(lo, hi - lo)
                    for i in range(0, len(ws), 16):
                        f.write('\t.word ' + ','.join('0x%04X' % x for x in ws[i:i + 16]) + '\n')
            # cl2000 assembles it, so the object claims the objects' memory model and target options
            model = [x for x in s.cflags if re.match(r'(-v\d|-ml$|-mt$|--float_support|--abi|--tmu|--vcu|--cla|--unified|--large)', x)]
            tc.run_native('cl2000', model + ['--abi=coffabi', '-c', 'blobs.asm'], cwd=self.dir)
            objs_all.append(os.path.join(self.dir, 'blobs.obj'))

        with open(os.path.join(self.dir, 'gate.cmd'), 'w') as f:
            f.write('-a\n-o gate.out\n-m gate.map\n--rom_model\n--stack_size=0x%X\n--disable_auto_rts\n--disable_clink\n'
                    % c28tc._int(self.g.get('stack_size', 0x400)))
            for o in objs_all: f.write('"%s"\n' % o)
            f.write('-i "%s"\n-l %s\n' % (os.path.dirname(lib), libname))
            # a member is taken from the library only for a symbol something needs: ask for one of each
            for mm, o in sorted(used.items()):
                g = sorted(n for n, (sn, v) in o.defined().items() if o.sections[sn].kind == 'text') or sorted(o.defined())
                if g: f.write('-u %s\n' % g[0])
            f.write(memory(s))
            f.write('SECTIONS\n{\n')
            for lo, hi in blobs:
                f.write('  blob_%06X : load = 0x%X, PAGE = 0\n' % (lo, lo))
            if cinit_spec:
                f.write('  .cinit : {\n%s  } load = 0x%X, PAGE = 0\n' % (''.join('    %s\n' % x for x in cinit_spec), cin_lo))
            if 'stack' in self.g:
                f.write('  .stack : load = 0x%X, PAGE = 1\n' % c28tc._int(self.g['stack']))
            for obj, name, load, run, page in sections:
                spec = '{ "%s"(%s) }' % (obj, name)
                oname = '%s_%s' % (c28tc.stem(obj), re.sub(r'\W', '_', name))
                if load is None:
                    f.write('  %s : %s > %s\n' % (oname, spec, 'PARKP, PAGE = 0' if page == 0 else 'PARKD, PAGE = 1'))
                elif load != run:
                    f.write('  %s : %s load = 0x%X, run = 0x%X, PAGE = %d\n' % (oname, spec, load, run, page))
                else:
                    f.write('  %s : %s load = 0x%X, PAGE = %d\n' % (oname, spec, load, page))
            for mm, name, addr, page in lib_secs:
                oname = 'rts_%s_%s' % (c28tc.stem(mm), re.sub(r'\W', '_', name))
                spec = '{ %s<%s>(%s) }' % (libname, mm, name)
                if addr is None: f.write('  %s : %s > PARKD, PAGE = 1\n' % (oname, spec))
                else: f.write('  %s : %s load = 0x%X, PAGE = %d\n' % (oname, spec, addr, page))
            f.write('}\n')
            for n, v in sorted(symdefs.items()): f.write('%s = 0x%X;\n' % (n, v))
        linker = self.g.get('linker', 'native')
        if linker == 'native':
            p = tc.run_native('lnk2000', ['gate.cmd'], cwd=self.dir, check=False)
            lver = 'native ' + c28tc.cgt_version(os.path.dirname(s.native))
        else:
            p = tc.tool_in(s.cgt, 'lnk2000', ['gate.cmd'], self.dir)
            lver = c28tc.cgt_version(s.cgt)
        gout = os.path.join(self.dir, 'gate.out')
        log = (p.stdout + p.stderr).strip()
        if p.returncode or not os.path.exists(gout):
            out('link: FAIL\n' + log); c28tc.stop_wine(s); return 1
        if log and verbose: out(log)
        lo_ = tc.obj(gout)
        got = [r[3] for r in c28tc.cinit_records(lo_.sections['.cinit'].data)] if '.cinit' in lo_.sections else []
        want = [r[3] for r in cin_img]
        cin_bad = [i for i in range(max(len(got), len(want))) if i >= len(got) or i >= len(want) or got[i] != want[i]]

        # ---- 4. compare -----------------------------------------------------------------------
        built = self.hex(gout, 'gate.bin')
        diffs = [img.base + i for i in range(min(len(built), len(img.w))) if built[i] != img.w[i]]
        identical = list(built) == list(img.w)
        count = collections.Counter(supplied.values())
        out('library: %d sections of %d members of %s placed' % (len(found), len(set(f['member'] for f in found)), lib))
        if rts_from_image and verbose:
            out('  from the image: ' + ', '.join('%s=0x%X' % kv for kv in sorted(rts_from_image.items())))
        if cin_lo is not None:
            out('cinit: %d record%s linked (%d from the sources, %d from the library), %s' % (
                len(got), '' if len(got) == 1 else 's', n_src_records, len(got) - n_src_records,
                "the image's records in the image's order" if not cin_bad else
                '%d NOT as the image, the first at record #%d' % (len(cin_bad), cin_bad[0])))
            if cin_bad: ok = False
        out('link: lnk2000 %s, %d blob section%s' % (lver, len(blobs), '' if len(blobs) == 1 else 's'))
        out('')
        out('| Supplied by | Words | Share |')
        out('|---|---|---|')
        total = img.end - img.base
        for k, label in (('proved', 'Proved sources'), ('library', 'Runtime library members'),
                         ('linker', 'Made by the linker (end of .cinit)'), ('fill', 'Fill (holes)'),
                         ('original', 'Original words (blobs)'), ('unproved', 'Sources that do not match (linked as compiled)')):
            if k in ('unproved', 'linker') and not count[k]: continue
            out('| %s | %d | %.1f%% |' % (label, count[k], 100.0 * count[k] / total))
        out('')
        if identical:
            out('IDENTICAL: the linked image is %s word for word' % os.path.relpath(s.image, s.root))
        else:
            ok = False
            out('DIFFERENT: %d words differ%s' % (len(diffs), '' if len(built) == len(img.w) else ' (size %d words)' % len(built)))
            for d in diffs[:12]:
                r = owner.get(d)
                out('  %06X  built %04X  image %04X  %s (%s)' % (d, built[d - img.base], img.W(d), r.name if r else '-', supplied.get(d)))
        if strict and count['original']:
            ok = False
            out('strict: %d words still come from blobs of the original' % count['original'])
        if fail_test:
            ok &= self.fail_test(gout, lo_, img, sections, owner)
        out('%s  (%.1f s)' % ('PASS' if ok else 'FAIL', time.time() - t0))
        c28tc.stop_wine(s)
        return 0 if ok else 1

    def hex(self, gout, name):
        """hex2000's image of a linked .out over the image's range: ROMS origin and length count
        bytes there, twice the word address."""
        s = self.s
        with open(os.path.join(self.dir, 'hex.cmd'), 'w') as f:
            f.write('"%s"\n-b\n-image\n-order %s\n-romwidth 16\n-fill 0x%04X\n' % (gout, 'MS' if s.byte_order == 'big' else 'LS', s.fill))
            f.write('ROMS\n{\n  FLASH: origin = 0x%X, length = 0x%X, romwidth = 16, files = { %s }\n}\n'
                    % (2 * s.base, 2 * (c28tc.Image.of(s).end - s.base), name))
        self.tc.run_native('hex2000', ['hex.cmd'], cwd=self.dir)
        with open(os.path.join(self.dir, name), 'rb') as f: b = f.read()
        return struct.unpack(('>' if s.byte_order == 'big' else '<') + '%dH' % (len(b) // 2), b)

    def fail_test(self, gout, lo_, img, sections, owner):
        """Change one word of the first placed function in the linked output; the compare must
        report exactly that word."""
        pick = next(((n, ld) for _, n, ld, rn, pg in sections if ld is not None and ld == rn and n.startswith('.text')
                     and img.contains(ld)), None)
        if pick is None: self.out('fail test: no function to change'); return False
        addr = pick[1]
        sec = next((x for x in lo_.sections.values() if x.data and x.paddr <= addr < x.paddr + len(x.data)), None)
        if sec is None: self.out('fail test: 0x%06X is in no output section' % addr); return False
        with open(gout, 'rb') as f: b = bytearray(f.read())
        at = sec.ptr + 2 * (addr - sec.paddr)
        w = struct.unpack_from('<H', b, at)[0] ^ 0x0100
        struct.pack_into('<H', b, at, w)
        bad = os.path.join(self.dir, 'gate_fail.out')
        with open(bad, 'wb') as f: f.write(b)
        built = self.hex(bad, 'gate_fail.bin')
        diffs = [img.base + i for i in range(min(len(built), len(img.w))) if built[i] != img.w[i]]
        r = owner.get(addr)
        good = diffs == [addr]
        self.out('fail test: one word changed at %06X (%s) in the linked output; the compare reports %s: %s'
                 % (addr, r.name if r else '-', ', '.join('%06X' % d for d in diffs[:4]) or 'nothing', 'PASS' if good else 'FAIL'))
        return good


def run(s, argv, out=print):
    ap = argparse.ArgumentParser(prog='gate.py check', description='The whole-image gate for a C2000 image.')
    ap.add_argument('--with', dest='with_', action='append', default=[])
    ap.add_argument('--fail-test', action='store_true')
    ap.add_argument('--strict', action='store_true', help='fail while any word comes from a blob')
    ap.add_argument('-v', action='store_true')
    a = ap.parse_args(argv)
    try:
        return Gate(s, out).run(a.with_, a.fail_test, a.v, a.strict)
    except c28tc.ToolError as e:
        out(str(e)); c28tc.stop_wine(s); return 2


def main(cfg, argv, cfgfile=None):
    """Entry from Exhume's tools/gate.py: `check` and `exact` both run the gate."""
    if argv and argv[0] in ('-h', '--help'): print(__doc__); return 0
    return run(c28tc.Settings.from_config(cfg), argv)


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(c28tc.EXHUME, 'tools'))
    import config
    p, rest = config.pop_config(sys.argv[1:])
    if rest and rest[0] in ('check', 'exact'): rest = rest[1:]
    sys.exit(main(config.load(p), rest))
