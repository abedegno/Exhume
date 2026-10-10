r"""Record a session in DOS and replay it in DOS and in the port, comparing the state dumps
(docs/port.md, "Verifying a port"; runtime/replay/replay.c has the formats).

    python3 tools/replay.py [--config PATH] build                    the replay DOS build: <work>/<exe_name>
    python3 tools/replay.py [--config PATH] record OUT [step ...]    record a session in DOS (steps: tools/replaydos.mjs)
    python3 tools/replay.py [--config PATH] record OUT --session NAME   ... with a session named in [replay.steps]
    python3 tools/replay.py [--config PATH] dos REC OUT              replay REC (a RECORD.OUT) in DOS
    python3 tools/replay.py [--config PATH] port REC OUT [--debug]   replay REC in the port (or its UBSan build)
    python3 tools/replay.py [--config PATH] compare A B [--same-build]  compare two STATE.OUT files (or their directories)
    python3 tools/replay.py [--config PATH] log REC                  what a recording holds
    python3 tools/replay.py [--config PATH] show STATE [PNGDIR]      list a dump's checkpoints; write each full one's screen as a PNG
    python3 tools/replay.py [--config PATH] nulls DIR                the null-pointer write check of a DOS run's directory
    python3 tools/replay.py [--config PATH] saves A B                compare the saved games ([replay] saves) two runs made
    ... --stage DIR                                                  with DIR's files (a saved game) put into the
                                                                     game's directory first, in DOS and in the port
    python3 tools/replay.py [--config PATH] check REC OUT            replay REC in DOS twice and in the port, compare all
                                                                     (with a sound card, the port's sound drivers too)
    python3 tools/replay.py [--config PATH] drivers [SESSION ...]    the port's sound drivers against the real ones
                                                                     ([replay] driver_check) on every session with a
                                                                     configuration of its own, or those named
    python3 tools/replay.py [--config PATH] golden [SESSION ...|all] [-j N] [--backend B] [--check]
                                                                     make the sessions' golden references from DOS
                                                                     (twice, checked identical)
    python3 tools/replay.py [--config PATH] verify [SESSION ...|all] [-j N] [--debug|--cov]
                                                                     replay the sessions in the port only, against
                                                                     the goldens (tools/golden.py has both)

The replay DOS build is the modding build with every source that uses the hooks of
portable.h ([replay] hooks) compiled with -DREPLAY (and every source with a NULLTRAP or
FARNULLREC mark with -DNULLTRAP, so the marked null pointers it reaches go to NULLTRAP.LOG),
and the [port] shared C
(the record and replay code, runtime/replay/replay.c's instance) linked in as more resident
modules: [replay] link is the modding link's command, to which this adds --out, --obj STEM=PATH
for each recompiled source and --add STEM=PATH for each shared one. Recording runs in js-dos
(tools/replaydos.mjs), the DOS replays in DOSBox-X when it is installed and otherwise js-dos
(replaydos.mjs --backend, or EXHUME_REPLAY_DOS); the port replays with [replay]
port_replay_flag.

A session can have a configuration file of its own ([replay] cfg_path in the game's tree;
UW2: DATA\UW.CFG's sound cards, [replay.cfgs]): recording writes it beside OUT/RECORD.OUT, and
a replay of REC uses the .cfg file beside it (tests/replay/sound.cfg for tests/replay/sound.rec)
or the file of that name in its directory, in DOS (replaydos.mjs --cfg) and in the port (its
home directory).

compare walks two dumps checkpoint by checkpoint. Each checkpoint names the event it was taken
at (the count of hook calls) and the clock, so a checkpoint whose header differs means the two
runs went different ways before it, and the comparison stops there. Within a checkpoint each
section is compared byte for byte, the screen also as the CRT controller would show it.
[[replay.mask]] ranges are the program's machinery, not game state: scope "always" differ
between any two runs, "cross" between DOS and the port; --same-build compares everything but
the "always" ones, for two runs of one build. Words that hold a far block's segment are
compared as which block they name ([replay.segments]), since DOS, DOSBox-X and the port load
the program at different segments.

Everything goes under [replay] work (default <build>/replay) unless an OUT says otherwise. The
game data comes from [replay] data.
"""
import os, sys, re, json, struct, zlib, hashlib, shutil, subprocess, shlex, copy

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
EXHUME = os.path.dirname(here)
import portcfg

CFG, ARGV = portcfg.cli()
RC = portcfg.replay(CFG)
root = CFG.root
OUT = RC.work
PY = os.path.join(EXHUME, '.venv', 'bin', 'python')
if not os.path.exists(PY): PY = sys.executable
DATA = RC.data
HOOKS = re.compile(r'\b(' + '|'.join(RC.hooks) + r')\(')
KINDS = {1: 'checkpoint', 2: 'periodic', 3: 'input', 4: 'end', 5: 'DESYNC', 6: 'hook'}
STREAMS = {1: 'TIME', 2: 'KEY', 3: 'MOUSE', 4: 'BUTTONS', 5: 'JOY', 6: 'JOYB', 7: 'MISC', 8: 'SOUND'}


def _sec(t): return (t + '    ')[:4]


# Byte ranges of a section that are the program's machinery rather than game state:
# (section, start, end, why). ALWAYS differ between any two runs, even of one build; CROSS
# between DOS and the port.
ALWAYS = [(_sec(m['section']), m['lo'], m['hi'], m.get('why', '')) for m in RC.mask if m.get('scope', 'always') == 'always']
CROSS = [(_sec(m['section']), m['lo'], m['hi'], m.get('why', '')) for m in RC.mask if m.get('scope') == 'cross']
# [{section, start, len}]: a string the program copies with its 0 and one byte more (UW2's
# GRCORE string entries, to 370D:4FA8): the byte after the 0 is whatever followed the string
# in the caller's buffer, stack junk, so it and the rest of the area are not compared
STRING_JUNK = [(_sec(j['section']), j['start'], j['len']) for j in RC.string_junk]
SEGCFG = RC.segments
SEG_BASE = SEGCFG.get('first_block_para')        # the EXE paragraph of SEGS' first block
SEG_RANGE = SEGCFG.get('exe_range', [0, 0])       # EXE paragraphs that move with the load segment
# the index of a SEGS word that is a far heap block: where DOS's far heap starts depends on the
# build's size (a replay build that grows can move it by a paragraph), so a word in the moving
# range that is the same distance from that block in each build is one
SEG_HEAP = SEGCFG.get('heap_block')


def _steps(name, seen=()):
    """A session's steps from [replay.steps]: '@NAME' takes another entry's steps in its place;
    [replay.derive] NAME = {from, wait_scale} makes one from another, every wait scaled."""
    if name in RC.derive:
        d = RC.derive[name]
        base = _steps(d['from'], seen + (name,))
        k = d.get('wait_scale', 1)
        return [f'w:{int(k * int(x[2:]))}' if x.startswith('w:') and k != 1 else x for x in base]
    out = []
    for x in RC.steps[name]:
        if x.startswith('@'):
            if x[1:] in seen: sys.exit(f'[replay.steps] {name}: {x} refers to itself')
            out += _steps(x[1:], seen + (name,))
        else: out.append(x)
    return out


SESSIONS = {n: _steps(n) for n in list(RC.steps) + list(RC.derive) if not n.startswith('_')}
CFGS = RC.cfgs
CFG_NAME = os.path.basename(RC.cfg_path.replace('\\', '/')) if RC.cfg_path else None


def cfg_of(rec):
    """The configuration of recording rec: the .cfg beside it, else its directory's [replay]
    cfg_path file (UW2: UW.CFG)."""
    cands = [os.path.splitext(rec)[0] + '.cfg']
    if CFG_NAME: cands.append(os.path.join(os.path.dirname(os.path.abspath(rec)), CFG_NAME))
    for c in cands:
        if os.path.exists(c): return c
    return None


def opts_of(src):
    return CFG.directives(src)[1]


def build_plan():
    """What the replay DOS build compiles: [(stem, path, options)] for the hook users (with
    -DREPLAY), the NULLTRAP users (with -DNULLTRAP) and the shared C, and the key of their
    sources' hashes and options, which names the build. The key also takes the source hashes
    the last exact link recorded (<build>/LINK/base/layout.json, tools/modding.py): the rest of
    the build is the matched objects of that link, so a change to any other source (an
    assembly module's segment class, say) makes a new build too."""
    import sources
    from srcdeps import source_hash
    shared = portcfg.shared_sources(CFG)
    todo = []
    for src in sources.all_sources(CFG) + shared:
        if not src.upper().endswith('.C'): continue
        text = open(src, encoding='latin1').read()
        defs = []
        if HOOKS.search(text) or src in shared: defs.append('-DREPLAY')
        if 'NULLTRAP(' in text or 'NULLREC(' in text: defs.append('-DNULLTRAP')
        if defs: todo.append((sources.stem(src), src, (opts_of(src) if src not in shared else RC.shared_opts) + ' ' + ' '.join(defs)))
    lay = os.path.join(CFG.build, 'LINK', 'base', 'layout.json')
    base = json.load(open(lay))['sources'] if os.path.exists(lay) else None
    key = hashlib.sha1(json.dumps([[(s, source_hash(p, CFG), o) for s, p, o in todo], base], sort_keys=True).encode()).hexdigest()
    return todo, key


def exe_path(): return os.path.join(OUT, RC.exe_name)


def build(quiet=False):
    """Compile the hook users with -DREPLAY (the NULLTRAP users with -DNULLTRAP too) and the
    shared C, and link them as the modding build with the shared C added. Cached by the
    sources' hashes."""
    import dosbatch
    todo, key = build_plan()
    exe = exe_path(); stamp = os.path.join(OUT, 'build.sha1')
    if os.path.exists(exe) and os.path.exists(stamp) and open(stamp).read() == key:
        if not quiet: print(f'replay build: {os.path.relpath(exe, root)} (up to date)')
        return exe
    if not RC.link: sys.exit('replay.py: [replay] link (the modding link command) is not set')
    objdir = os.path.join(OUT, 'obj')
    print(f'replay build: compiling {len(todo)} sources with -DREPLAY or -DNULLTRAP')
    opts = {s: o for s, _, o in todo}
    c2 = copy.copy(CFG); c2.build = objdir
    c2.directives = lambda src, _o=opts, _c=CFG: (_c.directives(src)[0], _o[_c.stem(src)])
    res = dosbatch.compile_many(c2, [p for _, p, _ in todo])
    bad = {k: '\n'.join(v[1]) for k, v in res.items() if not v[0]}
    if bad: sys.exit('replay build failed:\n' + '\n'.join(f'{k}: {v}' for k, v in bad.items()))
    shared = {sources_stem(p) for p in portcfg.shared_sources(CFG)}
    subst = dict(python=PY, exhume=EXHUME, config=CFG.file, root=root, build=CFG.build)
    cmd = [x.format(**subst) for x in shlex.split(RC.link)] + ['--out', os.path.join(OUT, 'link')]
    for s, _, _ in todo:
        obj = os.path.join(objdir, s, s + '.OBJ')
        cmd += ['--add' if s in shared else '--obj', f'{s}={obj}']
    r = subprocess.run(cmd, cwd=root, stdout=subprocess.DEVNULL if quiet else None)
    if r.returncode: sys.exit(f'replay build: the link failed (exit {r.returncode}); run the gate first, which leaves the exact link\'s snapshot the modding link starts from')
    shutil.copy(os.path.join(OUT, 'link', RC.exe_name), exe)
    open(stamp, 'w').write(key)
    print(f'replay build: {os.path.relpath(exe, root)}, {os.path.getsize(exe)} bytes')
    return exe


def sources_stem(p): return os.path.splitext(os.path.basename(p))[0].upper()


# ---- reading the files --------------------------------------------------------------------

def read_dump(path):
    """STATE.OUT as a list of checkpoints: dicts with kind, n, events, time and sections."""
    if os.path.isdir(path): path = os.path.join(path, 'STATE.OUT')
    d = open(path, 'rb').read(); p = 0; out = []
    while p + 20 <= len(d):
        if d[p:p + 4] != b'CKPT': raise SystemExit(f'{path}: no checkpoint at byte {p}')
        kind, n, ev, t, ns = struct.unpack_from('<HHIIH', d, p + 4); p += 18
        secs = {}
        for _ in range(ns):
            if p + 8 > len(d): break
            tag = d[p:p + 4].decode('latin1'); ln = struct.unpack_from('<I', d, p + 4)[0]; p += 8
            secs[tag] = d[p:p + ln]; p += ln
        out.append(dict(kind=kind, n=n, events=ev, time=t, secs=secs))
    return out


def read_log(path):
    """RECORD.OUT (runtime/replay/replay.c): the streams' runs decoded. Returns {stream: [runs]},
    each run (count, value), a SOUND run (count, value, moment) from version 3 (version 4's
    repeats expanded), and the call count the recording stopped at."""
    d = open(path, 'rb').read()
    if d[:4] != RC.magic or d[4] not in (2, 3, 4, 5): raise SystemExit(f'{path}: not a version 2 to 5 recording ({RC.magic.decode()})')
    ver = d[4]
    stop = struct.unpack_from('<I', d, 8)[0]
    data = {}; p = 12
    while p + 3 <= len(d):
        s, n = d[p], struct.unpack_from('<H', d, p + 1)[0]; p += 3
        data.setdefault(s, bytearray()).extend(d[p:p + n]); p += n
    out = {}
    for s, b in data.items():
        if s == 9:                     # format 5: the enhancements, names comma-separated
            out['ENH'] = [x for x in b.decode('ascii', 'replace').split(',') if x]
            continue
        runs = []; q = 0; t = 0
        while q < len(b):
            if s == 7:
                tag = b[q]; q += 1
                if tag == 7: runs.append(('WALL', struct.unpack_from('<I', b, q)[0])); q += 4
                else: runs.append(({8: 'SRAND', 9: 'CKPT'}.get(tag, tag), struct.unpack_from('<H', b, q)[0])); q += 2
                continue
            c = struct.unpack_from('<H', b, q)[0]; q += 2
            if s == 8 and ver >= 4 and c == 0:       # a repeat: the last k runs, n times
                k, n = struct.unpack_from('<BH', b, q); q += 3
                pat = runs[-k:]
                for _ in range(n): runs.extend(pat)
                continue
            if s == 1:
                dt = b[q]; q += 1
                if dt == 0xFF: t = struct.unpack_from('<I', b, q)[0]; q += 4
                else: t += dt
                runs.append((c, t))
            elif s == 2:
                r, n = struct.unpack_from('<HB', b, q); q += 3
                q += RC_KSTATE if n == 0xFF else 2 * n
                runs.append((c, r))
            elif s == 8 and ver >= 3: runs.append((c, *struct.unpack_from('<HH', b, q))); q += 4
            elif s in (4, 8): runs.append((c, struct.unpack_from('<H', b, q)[0])); q += 2
            else: runs.append((c, struct.unpack_from('<hh', b, q))); q += 4
        out[STREAMS.get(s, s)] = runs
    return out, stop


# the length of the key state a KEY run carries whole (runtime/replay: RP_KEYSTATE_LEN)
RC_KSTATE = int(CFG.raw.get('replay', {}).get('keystate_len', 0x89))


def log_summary(path):
    runs, stop = read_log(path)
    parts = [f'stopped at call {stop}' if stop else 'never stopped']
    for k, v in runs.items():
        if k == 'MISC': parts.append('MISC ' + ' '.join(f'{a}:{b:X}' for a, b in v)); continue
        if k == 'ENH': parts.append('enhancements ' + (', '.join(v) or 'none')); continue   # names (format 5), not runs
        parts.append(f'{k} {sum(r[0] for r in v)} calls in {len(v)} runs')
    keys = [r for c, r in runs.get('KEY', []) if r]
    if keys: parts.append('keys ' + ' '.join(f'{k:04X}' for k in keys))
    if 'TIME' in runs: parts.append(f"clock {runs['TIME'][0][1]:X} to {runs['TIME'][-1][1]:X}")
    return '; '.join(parts)


def scanout(secs):
    """The visible screen from the VGA and CRTC sections, as the CRT controller shows it
    (runtime/port/hw/vga.c's vga_scanout without pixel panning, which the dump does not hold):
    320 by 200 (or 400) palette indices."""
    vga, c = secs['VGA '], secs['CRTC']
    r07, r09, r0c, r0d, r13, r18 = c
    msl = (r09 & 0x1F) + 1; rows = min(400 // msl, 400)
    start = r0c << 8 | r0d; pitch = r13 * 2
    lc = r18 | (r07 & 0x10) << 4 | (r09 & 0x40) << 3
    pix = bytearray(320 * rows); addr = start
    planes = [vga[i * 0x10000:(i + 1) * 0x10000] for i in range(4)]
    for y in range(rows):
        sl = y * msl
        if y and sl > lc and sl - msl <= lc: addr = 0
        for x in range(320):
            pix[y * 320 + x] = planes[x & 3][(addr + (x >> 2)) & 0xFFFF]
        addr += pitch
    return bytes(pix), rows


def write_png(path, pix, w, h, pal6):
    pal = bytes(min(255, (v << 2) | (v >> 4)) for v in pal6)
    raw = b''.join(b'\0' + pix[y * w:(y + 1) * w] for y in range(h))
    def chunk(t, b): return struct.pack('>I', len(b)) + t + b + struct.pack('>I', zlib.crc32(t + b) & 0xFFFFFFFF)
    png = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 3, 0, 0, 0)) + \
        chunk(b'PLTE', pal) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')
    open(path, 'wb').write(png)


def label(ck):
    k = KINDS.get(ck['kind'], str(ck['kind']))
    n = ck['n']
    if ck['kind'] == 3: n = f"buttons {n & 0xFF}" if n & 0x8000 else f"key {n:04X}"
    return f"{k} {n} at event {ck['events']}, clock {ck['time']:X}"


# ---- comparing ------------------------------------------------------------------------------

def ranges(a, b, skip=()):
    """The differing byte ranges of two equal-length byte strings, merged when close."""
    out = []
    if a == b: return out
    for i in range(min(len(a), len(b))):
        if a[i] != b[i] and not any(lo <= i < hi for lo, hi in skip):
            if out and i - out[-1][1] <= 8: out[-1][1] = i + 1
            else: out.append([i, i + 1])
    return out


def segment_words(x, y, sa, sb):
    """The words where x holds one far block's segment in its build and y the same block's in
    the other (SEGS, runtime/replay/replay.c): stored far pointers, equal in meaning."""
    na, nb = struct.unpack(f'<{len(sa) // 2}H', sa), struct.unpack(f'<{len(sb) // 2}H', sb)
    pairs = {(u, v) for u, v in zip(na, nb) if u != v}
    # The EXE's own segments all move by one load segment, which the first block's pair gives:
    # a word that is an EXE paragraph (in [replay.segments] exe_range) plus the load segment in
    # each build is one.
    delta = (na[0] - nb[0]) & 0xFFFF
    base = SEG_BASE if SEG_BASE is not None else na[0]
    lo_a, lo_b = (na[0] - base) & 0xFFFF, (nb[0] - base) & 0xFFFF
    def moving(u, v):
        return SEG_RANGE[0] <= (u - lo_a) & 0xFFFF < SEG_RANGE[1] and SEG_RANGE[0] <= (v - lo_b) & 0xFFFF < SEG_RANGE[1]
    def same(u, v):
        if (u, v) in pairs: return True
        if SEG_BASE is None or not moving(u, v): return False
        if (u - v) & 0xFFFF == delta: return True
        return SEG_HEAP is not None and (u - na[SEG_HEAP]) & 0xFFFF == (v - nb[SEG_HEAP]) & 0xFFFF
    out = []; i = 0
    while i + 1 < len(x):
        if x[i:i + 2] != y[i:i + 2] and same(x[i] | x[i + 1] << 8, y[i] | y[i + 1] << 8):
            out.append((i, i + 2)); i += 2
        else: i += 1
    return out


def string_junk(tag, x):
    """The ranges after a copied string's 0 that hold junk ([[replay.string_junk]])."""
    out = []
    for t, start, ln in STRING_JUNK:
        if t != tag: continue
        z = x.find(b'\0', start, start + ln)
        if z >= 0: out.append((z + 1, start + ln))   # and the junk of longer strings before it
    return out


def compare(pa, pb, same_build=False, quiet=False):
    A, B = read_dump(pa), read_dump(pb)
    print(f'{pa}: {len(A)} checkpoints; {pb}: {len(B)}')
    worst = 0; first_bad = None
    for i in range(min(len(A), len(B))):
        a, b = A[i], B[i]
        if (a['kind'], a['n'], a['events'], a['time']) != (b['kind'], b['n'], b['events'], b['time']):
            print(f'checkpoint {i}: the runs went different ways: {label(a)} against {label(b)}')
            if 'CNTS' in a['secs'] and 'CNTS' in b['secs']:
                ca = struct.unpack(f"<{len(a['secs']['CNTS']) // 4}I", a['secs']['CNTS'])
                cb = struct.unpack(f"<{len(b['secs']['CNTS']) // 4}I", b['secs']['CNTS'])
                print('  calls by stream: ' + ', '.join(f'{STREAMS[k + 1]} {x}' + (f' against {y}' if x != y else '')
                                                    for k, (x, y) in enumerate(zip(ca, cb))))
            return 2
        notes = []
        for tag in sorted(set(a['secs']) | set(b['secs'])):
            if tag == 'NULL' and not same_build: continue
            x, y = a['secs'].get(tag), b['secs'].get(tag)
            if x is None or y is None: notes.append(f'{tag.strip()} only in one'); continue
            if tag == 'SEGS' and not same_build: continue
            skip = [(lo, hi) for s, lo, hi, _ in ALWAYS + ([] if same_build else CROSS) if s == tag]
            if len(x) != len(y): notes.append(f'{tag.strip()} lengths {len(x)} and {len(y)}'); continue
            if not same_build and 'SEGS' in a['secs'] and 'SEGS' in b['secs']:
                skip += segment_words(x, y, a['secs']['SEGS'], b['secs']['SEGS'])
            if not same_build: skip += string_junk(tag, x)
            r = ranges(x, y, skip)
            if not r: continue
            if tag == 'VGA ':
                sa, ha = scanout(a['secs']); sb, hb = scanout(b['secs'])
                px = sum(1 for k in range(min(len(sa), len(sb))) if sa[k] != sb[k])
                notes.append(f'VGA {sum(h - l for l, h in r)} bytes differ, screen {px} of {len(sa)} pixels')
            else:
                notes.append(f'{tag.strip()} {sum(h - l for l, h in r)} bytes differ: ' +
                             ', '.join(f'{l:X}..{h - 1:X}' for l, h in r[:6]) + (' ...' if len(r) > 6 else ''))
        if notes:
            worst = 1
            if first_bad is None: first_bad = i
            print(f'checkpoint {i} ({label(a)}): ' + '; '.join(notes))
        elif not quiet and a['kind'] != 2:
            print(f'checkpoint {i} ({label(a)}): identical')
    if len(A) != len(B):
        print(f'one run has {abs(len(A) - len(B))} more checkpoints (the shorter stopped earlier)')
        worst = max(worst, 1)
    print('identical' if worst == 0 else f'differences, the first at checkpoint {first_bad}' if first_bad is not None else 'differences')
    return worst


def saves(a, b):
    """Compares the saved games two runs made (their [replay] saves directories), file for file
    and byte for byte, case-blind on the names."""
    bad = 0; n = 0
    for d in RC.saves:
        da, db = os.path.join(a, d), os.path.join(b, d)
        fa = {f.upper(): f for f in os.listdir(da)} if os.path.isdir(da) else {}
        fb = {f.upper(): f for f in os.listdir(db)} if os.path.isdir(db) else {}
        for f in sorted(set(fa) | set(fb)):
            n += 1
            if f not in fa or f not in fb:
                print(f'{d}/{f}: only in {a if f in fa else b}'); bad = 1; continue
            x = open(os.path.join(da, fa[f]), 'rb').read(); y = open(os.path.join(db, fb[f]), 'rb').read()
            if x == y: print(f'{d}/{f}: identical, {len(x)} bytes'); continue
            r = ranges(x, y) if len(x) == len(y) else None
            print(f'{d}/{f}: differ' + (f', {len(x)} and {len(y)} bytes' if r is None else
                                         ': ' + ', '.join(f'{l:X}..{h - 1:X}' for l, h in r[:8])))
            bad = 1
    if not n: print('no saved games in either run')
    return bad


def show(path, pngdir=None):
    for i, ck in enumerate(read_dump(path)):
        secs = ' '.join(f'{t.strip()}:{len(v)}' for t, v in ck['secs'].items())
        print(f'{i:4d} {label(ck)}  [{secs}]')
        if pngdir and 'VGA ' in ck['secs']:
            os.makedirs(pngdir, exist_ok=True)
            pix, h = scanout(ck['secs'])
            write_png(os.path.join(pngdir, f'ck{i:04d}.png'), pix, 320, h, ck['secs']['PAL '])


def _form(ds, form):
    """DS:0.. against a form in hex, '??' for any byte."""
    for k in range(0, len(form), 2):
        h = form[k:k + 2]
        if h != '??' and (k // 2 >= len(ds) or ds[k // 2] != int(h, 16)): return False
    return True


def nulls(d):
    """The null-pointer write check of a DOS run: the NULL sections of its dumps (DS:0..30h and
    the vector table as the program saw them, and C0's checksum) and dos-mcp's reads after the
    program exited (memory.json). [replay.nulls]: ds_forms, the forms DS:0..3 may take (hex,
    '??' any byte; UW2: the two forms of ovr167's last overlay stub entry), and sum_from,
    sum_to, sum_expect, C0's checksum of DS:sum_from..sum_to-1."""
    N = RC.nulls
    forms = N.get('ds_forms', [])
    sfrom, sto, sexp = N.get('sum_from', 4), N.get('sum_to', 0x31), N.get('sum_expect', 0xCA5)
    bad = 0
    dump = read_dump(d)
    first = None
    for i, ck in enumerate(dump):
        s = ck['secs'].get('NULL')
        if not s: continue
        ds, chk, ivt = s[:0x31], struct.unpack_from('<H', s, 0x31)[0], s[0x33:]
        if first is None: first = (i, ds, ivt)
        if chk:
            print(f'checkpoint {i} ({label(ck)}): C0\'s checksum of DS:{sfrom:X}..{sto - 1:X}h is off by {chk:04X}: a null pointer write'); bad = 1
        if forms and not any(_form(ds, f) for f in forms):
            print(f'checkpoint {i}: DS:0..3 is {ds[:4].hex()}, none of the forms [replay.nulls] ds_forms allows'); bad = 1
    if first:
        i, ds, ivt = first; last = dump[-1]['secs']['NULL'][0x33:]
        changed = [v for v in range(256) if ivt[v * 4:v * 4 + 4] != last[v * 4:v * 4 + 4]]
        print(f'vector table: {len(changed)} vectors changed between checkpoint {i} and the last: ' +
              ' '.join(f'{v:02X}' for v in changed))
        print(f'DS:0..3 at the first checkpoint {ds[:4].hex()}, at the last {dump[-1]["secs"]["NULL"][:4].hex()}')
    mj = os.path.join(d, 'memory.json')
    if os.path.exists(mj):
        m = json.load(open(mj))
        for g in m.get('groups', []):
            b = bytes.fromhex(g['ds0_3f'])
            print(f"after exit, DGROUP at linear {g['ds_linear']:X}: DS:0..3 {b[:4].hex()}, C0 checksum "
                  f"{(sum(b[sfrom:sto]) - sexp) & 0xFFFF:04X} (0 when intact)")
    nl = os.path.join(d, 'NULLTRAP.LOG')
    if os.path.exists(nl):
        hits = open(nl, encoding='latin1').read().split()
        print(f'NULLTRAP.LOG: {len(hits)} null pointers met at marked sites: ' + ' '.join(sorted(set(hits))))
    else:
        print('NULLTRAP.LOG: none written (no marked null pointer met)')
    return bad


# ---- running ----------------------------------------------------------------------------------

STAGE = None   # --stage DIR: files (a saved game) put into the game's directory, DOS's and the port's home


def dos_can_replay(rec):
    """(True, '') if the DOS build can replay recording rec; else (False, why). A format 5
    recording was made in the port with enhancements, which DOS does not have."""
    d = open(rec, 'rb').read(15)
    if len(d) >= 5 and d[4] == 5:
        names = ''
        if len(d) >= 15 and d[12] == 9:
            n = struct.unpack_from('<H', d, 13)[0]
            names = open(rec, 'rb').read(15 + n)[15:].decode('ascii', 'replace')
        return False, f'{rec} was recorded in the port with enhancements ({names}); DOS cannot replay it'
    return True, ''


def run_dos(out, rec=None, steps=(), timeout=900, cfg=None, stage=None, log=None, backend=None, exe=None):
    """Runs the replay DOS build in tools/replaydos.mjs: records with steps, or replays rec.
    stage (default --stage's) is put into the game's directory first; log, a file the run's
    output goes to instead of the terminal; backend, replaydos's --backend (jsdos, dosbox-x;
    by default DOSBox-X for a replay when it is installed)."""
    if rec:
        ok, why = dos_can_replay(rec)
        if not ok: raise SystemExit('replay.py: ' + why)
    exe = exe or build()
    stage = stage or STAGE
    if not backend and rec:            # [replay] dos_backend: a session that needs a particular DOS
        backend = RC.dos_backend.get(os.path.splitext(os.path.basename(rec))[0])
    cmd = ['node', os.path.join(here, 'replaydos.mjs'), exe, out, '--timeout', str(timeout),
           '--data', DATA, '--exe-name', RC.exe_name]
    for s in RC.data_skip: cmd += ['--skip', s]
    for e in RC.env: cmd += ['--env', e]
    for f in RC.out_files: cmd += ['--out-file', f]
    for s in RC.saves: cmd += ['--save-dir', s]
    if RC.c0_signature: cmd += ['--sig', RC.c0_signature, '--sig-at', str(RC.c0_signature_at)]
    if RC.dosbox_conf: cmd += ['--dosbox-conf', RC.dosbox_conf]
    if stage: cmd += ['--stage', stage]
    if rec: cmd += ['--replay', rec]
    if backend: cmd += ['--backend', backend]
    cfg = cfg or (cfg_of(rec) if rec else None)
    if cfg: cmd += ['--cfg', cfg, '--cfg-path', RC.cfg_path]
    os.makedirs(out, exist_ok=True)
    if log:
        with open(log, 'w') as f: r = subprocess.run(cmd + list(steps), stdout=f, stderr=subprocess.STDOUT)
    else: r = subprocess.run(cmd + list(steps))
    return r.returncode


def port_env():
    """The environment to run the port in. On Windows a development build finds its DLLs on
    PATH, and those tools/setup-libs.sh built (libmt32emu) are in [port] libs' bin, which no shell
    puts there: without it the port exits 0xC0000135 (a DLL not found) before writing anything.
    From UW2Decomp's tools/replay.py."""
    env = dict(os.environ)
    if os.name == 'nt':
        libs = portcfg.port(CFG).libs
        dirs = [os.path.join(libs, 'bin'), os.path.join(libs, 'lib')]
        prefix = os.environ.get('MINGW_PREFIX')     # /clang64 in MSYS2's CLANG64 shell: SDL3 and the C++ runtime
        if prefix and shutil.which('cygpath'):
            w = subprocess.run(['cygpath', '-w', prefix + '/bin'], capture_output=True, text=True).stdout.strip()
            if w: dirs.append(w)
        extra = [d for d in dirs if os.path.isdir(d)]
        env['PATH'] = os.pathsep.join(extra + [env.get('PATH', '')])
    return env


def port_exe(variant=''):
    """The port's program in the variant's build: the name, or the name with .exe on Windows.
    EXHUME_PORT names another (tools/pkgcheck.py: the program from a player's package)."""
    if os.environ.get('EXHUME_PORT') and not variant: return os.environ['EXHUME_PORT']
    p = os.path.join(portcfg.variant_out(CFG, variant), RC.port_exe_name)
    return p + '.exe' if not os.path.exists(p) and os.path.exists(p + '.exe') else p


def port_data(out):
    """The game's directory as the port sees it in a replay: [replay] data, less [replay]
    data_skip, as the DOS runs stage it (UW1: the player's own saved games, which every session
    starts without). With nothing to skip it is the directory itself; else a directory under
    out of links to its entries, leaving out the skipped ones."""
    if not RC.data_skip: return DATA
    skip = {x.replace('\\', '/').strip('/').upper() for x in RC.data_skip}
    root = os.path.join(out, 'data')
    shutil.rmtree(root, ignore_errors=True)
    def link(rel):
        src = os.path.join(DATA, rel) if rel else DATA
        os.makedirs(os.path.join(root, rel), exist_ok=True)
        for name in sorted(os.listdir(src)):
            r = f'{rel}/{name}' if rel else name
            if r.upper() in skip: continue
            if os.path.isdir(os.path.join(src, name)) and any(k.startswith(r.upper() + '/') for k in skip):
                link(r)
            else:
                os.symlink(os.path.abspath(os.path.join(src, name)), os.path.join(root, r))
    link('')
    return root


def exit_after(cmd):
    """CMD with its --exit-after limit (milliseconds of the port's clock) replaced by
    $EXHUME_EXIT_AFTER when that is set: the web build's Node.js replays (make webcheck) run
    some sessions twenty to eighty times slower than the desktop's, close to the usual limit."""
    v = os.environ.get('EXHUME_EXIT_AFTER')
    if v and '--exit-after' in cmd:
        i = cmd.index('--exit-after')
        cmd = cmd[:i + 1] + [v] + cmd[i + 2:]
    return cmd


def run_port(rec, out, extra=(), stage=None, quiet=False, env=None):
    """Replays rec in the port into out. env: variables set (None: removed) for this run alone,
    on top of port_env(), so that runs side by side (tools/enhcheck.py) can each have their own
    test switches."""
    extra = list(extra)
    debug = '--debug' in extra
    if debug: extra.remove('--debug')
    cov = '--cov' in extra              # the coverage build (tools/coverage.py), writing LLVM_PROFILE_FILE
    if cov: extra.remove('--cov')
    stage = stage or STAGE
    exe = port_exe('debug' if debug else 'cov' if cov else '')
    if not os.path.exists(exe): sys.exit('replay.py: build the port first (tools/portbuild.py)')
    home = os.path.join(out, 'home'); shutil.rmtree(home, ignore_errors=True); os.makedirs(home)
    if stage: shutil.copytree(stage, home, dirs_exist_ok=True)
    cfg = cfg_of(rec)
    if cfg:
        dst = os.path.join(home, *RC.cfg_path.replace('\\', '/').split('/'))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy(cfg, dst)
    cmd = [exe, RC.port_data_flag, port_data(out), RC.port_home_flag, home] + RC.port_args + \
          [RC.port_replay_flag, os.path.abspath(rec)] + list(extra)
    penv = port_env()
    for k, v in (env or {}).items():
        if v is None: penv.pop(k, None)
        else: penv[k] = v
    r = subprocess.run(exit_after(cmd), capture_output=True, text=True, errors='replace', env=penv)
    open(os.path.join(out, 'port.log'), 'w').write(r.stdout + r.stderr)
    for f in ('STATE.OUT',):
        if os.path.exists(os.path.join(out, f)): os.remove(os.path.join(out, f))
        if os.path.exists(os.path.join(home, f)): shutil.copy(os.path.join(home, f), os.path.join(out, f))
    for d in RC.saves:         # the saved games, as DOS's runs copy theirs
        shutil.rmtree(os.path.join(out, d), ignore_errors=True)
        if os.path.isdir(os.path.join(home, d)):
            shutil.copytree(os.path.join(home, d), os.path.join(out, d))
    ub = [l for l in (r.stdout + r.stderr).splitlines() if 'runtime error' in l]
    if quiet: return r.returncode
    if debug: print(f'port: UBSan reported {len(ub)} runtime errors' + ''.join('\n  ' + l for l in sorted(set(ub))[:20]))
    tail = [l for l in (r.stdout + r.stderr).splitlines() if RC.port_name in l][-3:]
    print('port:', r.returncode, *tail, sep='\n  ')
    return r.returncode


def sound_check(*dirs):
    """With a sound card: each run's count of the reads where its own sound driver differed from
    the recording (DOS's SNDCHECK.OUT, the port's log); the port's must be 0, DOS's are the
    reference's own timing noise (docs/port.md, "Replays with a sound card")."""
    lines = []
    for d in dirs:
        for f, pat in (('SNDCHECK.OUT', 'sound reads'), ('port.log', f'{RC.port_name}: sound reads')):
            fp = os.path.join(d, f)
            if os.path.exists(fp):
                lines += [(d, l.strip()) for l in open(fp, errors='replace') if l.strip().startswith(pat)]
    if not lines: return 0
    print('\n== sound drivers against the recording')
    bad = 0
    for d, l in lines:
        print(f'{os.path.basename(d)}: {l}')
        m = re.search(r'sound reads: \d+, (\d+) where the port', l)
        if m and int(m.group(1)): bad = 1
    return bad


def driver_check(p):
    """With a music card: [replay] driver_check (UW2: tools/ailcheck.py, the real .ADV driver
    against the port's C driver write for write) on the port's driver logs; skipped when the
    logs are missing or the tool says it cannot run (no Unicorn)."""
    if not RC.driver_check or len(RC.port_sound_logs) < 2: return 0
    al, hl = os.path.join(p, 'ail.log'), os.path.join(p, 'hw.log')
    if not (os.path.exists(al) and os.path.exists(hl)): return 0
    marker = CFG.raw.get('replay', {}).get('driver_marker')
    if marker and marker not in open(al, errors='replace').read(200000): return 0
    print('\n== the port\'s music driver against the real one')
    subst = dict(python=PY, exhume=EXHUME, config=CFG.file, root=root, build=CFG.build, ail=al, hw=hl)
    r = subprocess.run([x.format(**subst) for x in shlex.split(RC.driver_check)], capture_output=True, text=True)
    out = (r.stdout + r.stderr).strip()
    if 'needs Unicorn' in out:
        print('skipped: no Unicorn in', PY); return 0
    print(out)
    return 1 if r.returncode else 0


def sound_logs(p):
    """The port's flags that write its driver logs into p ([replay] port_sound_logs)."""
    if len(RC.port_sound_logs) < 2: return []
    return [RC.port_sound_logs[0], os.path.join(p, 'ail.log'), RC.port_sound_logs[1], os.path.join(p, 'hw.log')]


def main(argv):
    global STAGE
    if '--stage' in argv:
        i = argv.index('--stage'); STAGE = os.path.abspath(argv[i + 1]); argv = argv[:i] + argv[i + 2:]
    if not argv: print(__doc__); return 2
    cmd, a = argv[0], argv[1:]
    if cmd == 'build': build(); return 0
    if cmd in ('golden', 'verify'):
        import golden
        return golden.main(cmd, a)
    if cmd == 'record':
        out = a[0]; steps = a[1:]; cfg = None
        if steps[:1] == ['--session']:
            name = steps[1]; steps = SESSIONS[name]
            if name in CFGS:
                os.makedirs(out, exist_ok=True)
                cfg = os.path.join(out, CFG_NAME)
                open(cfg, 'w', newline='').write(CFGS[name])
        rc = run_dos(out, None, steps, cfg=cfg)
        if os.path.exists(os.path.join(out, 'RECORD.OUT')):
            print('recorded:', log_summary(os.path.join(out, 'RECORD.OUT')))
        return rc
    if cmd == 'drivers':
        import golden
        names = a or [n for n in golden.SESSIONS if cfg_of(golden.rec_of(n))]
        bad = 0
        for n in names:
            d = os.path.join(OUT, 'drivers', n); os.makedirs(d, exist_ok=True)
            run_port(golden.rec_of(n), d, sound_logs(d), quiet=True)
            print(f'== {n}')
            bad = max(bad, driver_check(d))
        return bad
    if cmd == 'log': print(log_summary(a[0])); return 0
    if cmd == 'dos': return run_dos(a[1], a[0])
    if cmd == 'port': return run_port(a[0], a[1], a[2:])
    if cmd == 'compare': return compare(a[0], a[1], '--same-build' in a)
    if cmd == 'show': show(a[0], a[1] if len(a) > 1 else None); return 0
    if cmd == 'saves': return saves(a[0], a[1])
    if cmd == 'nulls': return nulls(a[0])
    if cmd == 'check':
        rec, out = a[0], a[1]
        d1, d2, p = os.path.join(out, 'dos1'), os.path.join(out, 'dos2'), os.path.join(out, 'port')
        for d in (d1, d2, p): os.makedirs(d, exist_ok=True)
        run_dos(d1, rec); run_dos(d2, rec)
        run_port(rec, p, sound_logs(p) if cfg_of(rec) else [])
        if os.path.exists(port_exe('debug')):
            pd = os.path.join(out, 'port-debug'); os.makedirs(pd, exist_ok=True); run_port(rec, pd, ['--debug'])
        print('\n== DOS against DOS'); r1 = compare(d1, d2, True, quiet=True)
        print('\n== DOS against the port'); r2 = compare(d1, p)
        print('\n== null pointers (DOS)'); r3 = nulls(d1)
        r4 = max(sound_check(d1, d2, p) if RC.sound_check else 0, driver_check(p))
        if any(os.path.isdir(os.path.join(x, s)) for x in (d1, p) for s in RC.saves):
            print('\n== saved games, DOS against DOS'); r4 = max(r4, saves(d1, d2))
            print('\n== saved games, DOS against the port'); r4 = max(r4, saves(d1, p))
        return max(r1, r2, r3, r4)
    print(__doc__); return 2


if __name__ == '__main__':
    sys.exit(main(ARGV))
