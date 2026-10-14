"""The gate's data stage: const data, the region table and the RW image, linked naturally.

Called by armgate.run(). armcc gives an object one .constdata, one .data and one .bss for the
data its functions use, and armlink lays each kind out in object link order, so each kind's
rows split into one run per file, and one object order serves all three kinds. This links
every source that owns a data row, compiled with its function bodies taken out (data pointing
at code would otherwise keep that code and its callees, over MDK-Lite's 32 KB), in that order:

    LR_DATA START { ER_CONST (every .constdata, Region$$Table)  ER_RAM (.data, .bss, the
                    startup file's heap and stack) }

with every other symbol from symdefs. armlink then builds the region table and compresses the
RW data after it, as the original link did. MicroLIB's scatter-load code and region handlers
are linked from the library at the image's addresses (found from the image's own region table
and __scatterload), so the table holds the original's handler addresses. The load image from
START to the end of the compressed data is compared with the image, and every data symbol's
address with the map.

A stripped source keeps a function whose body declares a static (its data is that function's),
and gains one function referencing the globals the file owns, so armcc still gives them one
section per kind and armlink keeps exactly them: data the original's armlink dropped (nothing
referenced it) is dropped here too."""
import collections, os, re
from dataclasses import dataclass, field
import armtc, armmap, armmatch, ehex, rwdata
import armgate as G


def strip_functions(text, tag):
    """(source, kept functions, their static names): every top-level function body replaced
    by `;` unless it declares a static."""
    res, j, n = [], 0, len(text)
    kept, statics = [], set()
    depth = 0
    while j < n:
        c = text[j]
        if text.startswith('/*', j):
            k = text.find('*/', j + 2); k = n if k < 0 else k + 2; res.append(text[j:k]); j = k; continue
        if text.startswith('//', j):
            k = text.find('\n', j); k = n if k < 0 else k; res.append(text[j:k]); j = k; continue
        if c in '"\'':
            k = j + 1
            while k < n and text[k] != c: k += 2 if text[k] == '\\' else 1
            res.append(text[j:k + 1]); j = k + 1; continue
        if text.startswith('#', j) and (j == 0 or text[j - 1] == '\n'):
            k = j
            while True:            # a preprocessor line, with its continuations
                e = text.find('\n', k); e = n if e < 0 else e
                if e > 0 and text[e - 1] == '\\' and e < n: k = e + 1; continue
                break
            res.append(text[j:e]); j = e; continue
        if depth == 0 and c == '{':
            head = ''.join(res)
            hs = head[max(head.rfind(';'), head.rfind('}')) + 1:]
            hs_code = re.sub(r'/\*.*?\*/|//[^\n]*|^\s*#[^\n]*', ' ', hs, flags=re.S | re.M)
            before = hs_code.split('(')[0]
            if hs_code.rstrip().endswith(')') and '=' not in before and not re.search(r'\b(struct|union|enum)\b', before):
                d, k = 1, j + 1
                while k < n and d:
                    if text.startswith('/*', k):
                        e = text.find('*/', k + 2); k = n if e < 0 else e + 2; continue
                    if text.startswith('//', k):
                        e = text.find('\n', k); k = n if e < 0 else e; continue
                    if text[k] in '"\'':
                        q = text[k]; k += 1
                        while k < n and text[k] != q: k += 2 if text[k] == '\\' else 1
                        k += 1; continue
                    d += {'{': 1, '}': -1}.get(text[k], 0); k += 1
                body = text[j:k]
                own = re.findall(r'\bstatic\b[^;{}()]*?\b([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?:=|;)', body)
                if own:
                    name = re.search(r'([A-Za-z_]\w*)\s*\($', before.strip() + '(')
                    kept.append(name.group(1) if name else '')
                    statics |= set(own)
                    res.append(body)
                else:
                    res.append(';')
                j = k; continue
        if c == '{': depth += 1
        elif c == '}': depth -= 1
        res.append(c); j += 1
    return ''.join(res), [k for k in kept if k], statics


def refs_function(names, tag):
    # stores through a parameter: armcc drops all but the last store to a local, volatile or not,
    # and with them the references
    refs = ' '.join(f'out[{i}] = (const void *)&{x};' for i, x in enumerate(names))
    return f'\nvoid gate_refs_{tag}(const void **out) {{ {refs} }}\n'


def extern_copies(text, names):
    """A copy of data another file owns becomes a declaration, so it takes no room here."""
    for d in names:
        text = re.sub(r'^(?!\s*extern\b)([^;=\n{}()#]*\b' + re.escape(d) + r'\b[^;=\n{}()]*;)', r'extern \1', text, flags=re.M)
    return text


def link_order(first):
    """One object order for all kinds, from each kind's address order ({file: [lowest address
    per kind or None]}), merged topologically. Returns (order, files in a conflict)."""
    files = sorted(first)
    succ = collections.defaultdict(set); indeg = {f: 0 for f in files}
    kinds = max((len(v) for v in first.values()), default=0)
    for k in range(kinds):
        seq = sorted((first[f][k], f) for f in files if first[f][k] is not None)
        for (_, x), (_, y) in zip(seq, seq[1:]):
            if y not in succ[x]: succ[x].add(y); indeg[y] += 1
    lowest = {f: min(a for a in first[f] if a is not None) for f in files}
    order, ready = [], sorted((f for f in files if indeg[f] == 0), key=lambda g: (lowest[g], g))
    while ready:
        f = ready.pop(0); order.append(f)
        for y in sorted(succ[f]):
            indeg[y] -= 1
            if indeg[y] == 0: ready.append(y)
        ready.sort(key=lambda g: (lowest[g], g))
    return order, [f for f in files if f not in order]


def image_end(img):
    """Where the image's load data ends: after its compressed or copied RW data."""
    regions = rwdata.find_table(img)
    ends = []
    for r in regions:
        if r.kind == 'compressed':
            _, used = rwdata.lz77.decompress(img.data[r.load - img.base:], r.size); ends.append(r.load + used)
        elif r.kind == 'copy':
            ends.append(r.load + r.size)
    return (max(ends) + 3) & ~3 if ends else None        # the load region ends on a 4-byte boundary


@dataclass
class DataResult:
    ok: bool = False
    lines: list = field(default_factory=list)
    got: bytes = None              # the linked load image from start
    start: int = None
    pool: bytes = None             # __scatterload's region table words, as this link made them


def kind_of(r, ram):
    if r.addr < G.RAM: return 0                    # const
    if ram is not None:
        k = ram.kind_at(r.addr)
        if k is not None: return 2 if k == 'zero' else 1
    return 2 if r.kind == 'zi' else 1


def run(cfg, plan, workers):
    res = DataResult()
    say = res.lines.append
    tc = armtc.toolchain(cfg)
    img = ehex.Image(cfg.image)
    try:
        regions = rwdata.find_table(img); ram = rwdata.RamImage(img, regions)
    except ValueError:
        regions, ram = [], None
    spec = plan.spec
    start = spec.data_start if spec.data_start is not None else max(p.end for p in spec.pieces)
    end = plan.data_end or spec.data_end or (image_end(img) if regions else None) or img.end
    res.start = start
    work = os.path.join(plan.work, 'data'); os.makedirs(work, exist_ok=True)
    rows = [r for r in plan.m.data if r.addr >= G.RAM or r.addr >= start]
    owner, unowned, asm = {}, [], []
    for r in rows:
        f = plan.owners.get(r.name, '')
        if f.endswith('.s'): asm.append(r); continue
        if f and f in plan.objs: owner[r.name] = f
        elif f: say(f'{f} owns {r.name} but has no object (it failed to compile)'); return res
        else: unowned.append(r)
    first = {}
    for r in rows:
        f = owner.get(r.name)
        if not f: continue
        first.setdefault(f, [None] * 3)
        k = kind_of(r, ram)
        first[f][k] = r.addr if first[f][k] is None else min(first[f][k], r.addr)
    order, conflict = link_order(first)
    if conflict:
        say('link order conflict (an owner is wrong) among: ' + ', '.join(conflict)); return res
    # 1. each owner's data-only source, 2. compiled (on the workers), 3. placed, in link order
    jobs, keep = [], {}
    for f in order:
        s = G.stem(f)
        src = os.path.join(cfg.root, f)
        with open(src, encoding='latin1') as fh: text = fh.read()
        dups = plan.dups.get(f, set())
        text = extern_copies(text, dups)
        body, kept, statics = strip_functions(text, s)
        _, info = plan.objs[f]
        defs = G.defined_names(info)
        mine = [r.name for r in rows if owner.get(r.name) == f and r.name in defs and r.name not in statics]
        keep[f] = kept
        dsrc = os.path.join(work, s + '_data.c')
        with open(dsrc, 'w', encoding='latin1') as fh: fh.write(body + refs_function(mine, s))
        h = os.path.join(work, s + '.names.h')
        with open(h, 'w') as fh: fh.write(G.names_header(plan.ln, dups, s))
        jobs.append((dsrc, os.path.join(work, s + '_data.o'), h, '-I' + tc.inside(os.path.dirname(src))))
    results = workers.map(G.task_compile, jobs)
    objs, defined, code_fixed, keeps = [], set(), [], []
    for f, (dsrc, obj, _, _), (ok, log, info) in zip(order, jobs, results):
        if not ok: say(f'{f}: compile failed'); res.lines += log.strip().splitlines()[-8:]; return res
        with open(os.path.join(cfg.root, f), encoding='latin1') as fh:
            extra = {plan.ln.get(k, k): v for k, v in armmap.annotations(fh.read()).items() if k not in plan.dups.get(f, set())}
        placements, d, errors, unplaced = armmatch.place(info, plan.m, extra, True)
        if errors: say(f'{f}: ' + '; '.join(errors)); return res
        defined |= set(d)
        o = os.path.basename(obj)
        size_of = {n: z for n, z in info.sections.values()}
        for sec, addr in placements:
            if sec.startswith('i.'): code_fixed.append((o, sec, addr, size_of.get(sec) or 0))
        keeps += [f'{o}(i.gate_refs_{G.stem(f)})'] + [f'{o}(i.{k})' for k in keep[f]]
        objs.append(obj)
    # code ending where the const data begins shares the const region, first in it
    touching = sorted([c for c in code_fixed if c[2] < start <= c[2] + c[3] + 3], key=lambda c: c[2])
    code_fixed = [c for c in code_fixed if c not in touching]
    lead = touching[0][2] if touching else start
    if lead % 4:
        pad = os.path.join(work, 'gate_dpad.c')
        with open(pad, 'w') as fh: fh.write(f'__attribute__((section(".pad"))) const unsigned char gate_dpad[{lead % 4}] = {{ 0 }};\n')
        ok, log = tc.compile(pad, os.path.join(work, 'gate_dpad.o'), cfg.c_opts)
        if not ok: say(log); return res
        objs.insert(0, os.path.join(work, 'gate_dpad.o')); keeps.append('gate_dpad.o(.pad)')
    first_sel = ('gate_dpad.o (.pad, +FIRST) ' if lead % 4 else '') + ' '.join(f'{o}({sec})' for o, sec, _, _ in touching)
    so = None
    if spec.startup in plan.objs:
        sobj, sinfo = plan.objs[spec.startup]
        so = os.path.basename(sobj)
        objs.append(sobj)
        defined |= {x.name for x in sinfo.symbols if x.bind == 'Gb' and x.sec != 'Ref'}
        keeps += [f'{so}({n})' for i, (n, _) in sinfo.sections.items() if armmatch.is_zi(sinfo, i)]
    rw_at = next((r.exec for r in regions if r.kind != 'zero'), None)
    zi_at = next((r.exec for r in regions if r.kind == 'zero'), None)
    if rw_at is None: rw_at = zi_at if zi_at is not None else G.RAM
    ab = G.aside_base(plan)
    ram_lines = [f'  ER_RAM 0x{rw_at:08X} {{ *(.data) *(.bss)' + (f' {so} (+ZI)' if so else '') + ' }']
    if spec.split_zi and zi_at is not None:
        ram_lines = [f'  ER_RW 0x{rw_at:08X} {{ *(.data) }}',
                     f'  ER_ZI 0x{zi_at:08X} {{ *(.bss)' + (f' {so} (+ZI)' if so else '') + ' }']
    head = [f'LR_DATA 0x{lead & ~3:08X} {{',
            f'  ER_CONST 0x{lead & ~3:08X} {{ {first_sel} *(.constdata) *(.conststring) *(Region$$Table) }}'] + ram_lines + ['}']
    # the scatter-load machinery: a stub requests __scatterload; its and the handlers' names are
    # left out of symdefs so they come from the library, and a first link shows which members
    sl_img = img.data.find(rwdata.SCATTERLOAD)
    sl_addr = img.base + sl_img if sl_img >= 0 else None
    machinery = set()
    for a in [sl_addr] + handler_words(img):
        if a is None: continue
        row = plan.m.row_at(a & ~1)
        if row: machinery |= {row.name, f'sub_{row.addr:08X}'}
    stub = os.path.join(work, 'gate_datastub.c')
    # armlink removes unused sections only from a link with an entry point: the stub gives one
    # (code only: a const table here would be laid out with the const data)
    with open(stub, 'w') as fh:
        fh.write('extern void __scatterload(void);\nvoid gate_dataentry(void) { __scatterload(); }\n' if sl_addr
                 else 'void gate_dataentry(void) { }\n')
    ok, log = tc.compile(stub, os.path.join(work, 'gate_datastub.o'), cfg.c_opts)
    if not ok: say(log); return res
    objs.append(os.path.join(work, 'gate_datastub.o')); keeps.append('gate_datastub.o(*)')
    entries = [e for e in plan.m.symdef_entries(defined, {}) if e[2] not in machinery]
    sym = os.path.join(work, 'data.sym')
    with open(sym, 'w') as fh: fh.write(armmap.symdefs_text(entries) + G.extra_lines(plan, defined))
    rest = [f'LR_REST 0x{ab:08X} {{', f'  ER_REST 0x{ab:08X} {{ gate_datastub.o (+RO) .ANY (+RO) }}', f'  ER_RREST 0x{G.LIBREST:08X} {{ .ANY (+RW +ZI) }}', '}']
    scatter = os.path.join(work, 'data.sct'); axf = os.path.join(work, 'data.axf')
    lib_fixed = []
    for attempt in (1, 2):
        # code that data points at, and the library's machinery, at the image's addresses (a
        # section off a 4-byte boundary after a pad, as everywhere)
        clines, cpads = G.fixed_scatter(code_fixed + lib_fixed, 'C', ab)
        inputs = list(objs)
        if cpads:
            pc = os.path.join(work, G.PAD + '.c')
            with open(pc, 'w') as fh: fh.write(G.pad_source(cpads))
            ok, log = tc.compile(pc, os.path.join(work, G.PAD + '.o'), cfg.c_opts)
            if not ok: say(log); return res
            inputs.append(os.path.join(work, G.PAD + '.o'))
        with open(scatter, 'w') as fh: fh.write('\n'.join(head + clines + rest) + '\n')
        extra_args = ['--no_startup', '--entry', 'gate_dataentry', '--diag_suppress', '6305,6314,6329']
        for k in keeps: extra_args += ['--keep', k]
        args = G.link_args(plan, cfg, tc, scatter, inputs + [sym], axf, extra_args, True)
        if os.path.exists(axf): os.remove(axf)
        rc, log = tc.run('armlink', args, work)
        if rc or not os.path.exists(axf): say('link failed'); res.lines += log.strip().splitlines()[-12:]; return res
        hexf = os.path.join(work, 'data.hex')
        rc, log = tc.run('fromelf', ['--i32combined', tc.inside(axf), '-o', tc.inside(hexf)], work)
        if rc: say(log); return res
        linked = ehex.Image(hexf)
        syms, secs, loads = G.listing(os.path.splitext(axf)[0] + '.lst')
        if attempt == 2 or sl_addr is None: break
        lib_fixed = machinery_sections(img, linked, syms, secs, sl_addr, say)
        if not lib_fixed: break
    lbase, lsize = loads.get('LR_DATA', (lead & ~3, 0))
    got = linked.bytes(start, lbase + lsize - start) if lbase + lsize > start else b''
    res.got = got
    want = img.bytes(start, end - start)
    diff = [i for i in range(min(len(got), len(want))) if got[i] != want[i]]
    say(f'{len(order)} files own data; {len(unowned)} rows unowned ({sum(r.size for r in unowned)} bytes)'
        + (f': {", ".join(r.name for r in unowned[:8])}' if unowned else ''))
    if asm: say(f'data rows owned by assembly sources (not linked here): {", ".join(r.name for r in asm[:8])}')
    same = not diff and len(got) == len(want)
    say(f'load image 0x{start:08X}-0x{end:08X}: got 0x{len(got):X} bytes, ' +
        ('MATCHES' if same else f'{len(diff)} bytes differ, first at 0x{start + diff[0]:08X}' if diff
         else f'size 0x{len(got):X} want 0x{len(want):X}'))
    info = tc.symbols(axf)
    at = {s.name: s.value for s in info.symbols if s.type == 'Data'}
    moved = [(r.name, r.addr, at[r.name]) for r in plan.m.data if r.name in at and at[r.name] != r.addr and r in rows]
    say(f'data symbols at their map address: {sum(1 for r in rows if r.name in at) - len(moved)}, moved: {len(moved)}')
    for n, w, g in moved[:15]: say(f'  {n}: map 0x{w:08X}, linked 0x{g:08X}')
    if sl_addr is not None:
        w = ((sl_addr + 4) & ~3) + 24
        try:
            res.pool = linked.bytes(w, 8)
        except ValueError:
            say('__scatterload was not linked at its address: its region table words are unchecked')
    res.ok = same and not moved
    return res


def handler_words(img):
    """The handler address of each entry of an image's region table ([] if none is found)."""
    import struct
    at = img.data.find(rwdata.SCATTERLOAD)
    if at < 0: return []
    pc = img.base + at
    s, e = struct.unpack_from('<II', img.data, ((pc + 4) & ~3) + 24 - img.base)
    if not (img.base <= s <= e <= img.end): return []
    return [struct.unpack_from('<I', img.data, a + 12 - img.base)[0] for a in range(s, e, 16)]


def machinery_sections(img, linked, syms, secs, sl_addr, say):
    """[(object, section, address, size)]: where the library's scatter-load code and region handlers
    go so the region table and __scatterload come out as the image's: each handler the linked
    table names, at the address the image's table gives for the same entry."""
    want, got = handler_words(img), handler_words(linked)
    by_addr = {}
    for name, (a, kind, obj, sec) in syms.items():
        if 'Code' in kind: by_addr.setdefault(a & ~1, (name, obj, sec))
    base_of = {(obj, sec): a for a, z, sec, obj in secs}
    size_of = {(obj, sec): z for a, z, sec, obj in secs}
    out = {}

    def fix(linked_addr, image_addr):
        hit = by_addr.get(linked_addr & ~1)
        if not hit: return
        name, obj, sec = hit
        b = base_of.get((obj, sec))
        if b is None: return
        out[(obj, sec)] = (image_addr & ~1) - ((linked_addr & ~1) - b)
    sl = syms.get('__scatterload')
    if sl: fix(sl[0], sl_addr)
    if len(got) != len(want):
        say(f'the linked region table has {len(got)} entries, the image\'s {len(want)}')
    for g, w in zip(got, want): fix(g, w)
    return [(o, s, a, size_of[(o, s)]) for (o, s), a in sorted(out.items(), key=lambda x: x[1])]
