"""armcc's register-allocator trace: compile one source with the trace on, and read the trace.

    python3 armtrace.py [--config PATH] run SRC [--out TRACE] [--level N] [--local FILE]
                                        [--prefix DIR] [--opts '...'] [--timeout S]
                                        [--runner wine|linux] [--method preload|gdb]
                                        [--profile full|light] [--only K]
    python3 armtrace.py parse TRACE
    python3 armtrace.py survey ARMCC_HOME [--local FILE] [--src SRC] [--opts '...']

armcc 5's back end has a register-allocation trace compiled into the release build. It is off:
it prints only when the back end's debug level is 5 or more, and no command-line option sets
that level (compiler.md, "Tracing the register allocator"). `run` compiles SRC so that the
running compiler process sees a debug level of N (default 5). Nothing on disk changes: only the
memory of the one compiler process is written. The trace goes where armcc's diagnostics go,
here `--errors=TRACE`.

Two runners:

  wine   (the default with EXHUME_ARMCC_RUNNER=wine) the project's MDK armcc.exe under winedbg:
         the local file's [trace] commands are sent to winedbg before the compile starts.
  linux  the Linux build of the same compiler (ARM Compiler 5 for Linux, a 32-bit x86 program),
         home from EXHUME_ARMCC_LINUX, mappings from EXHUME_ARMCC_MAPPINGS. With --method preload
         (the default) a small LD_PRELOAD library (armtrace_shim/armtrace_shim.c, built on first
         use with `cc -m32`; no 32-bit C library is needed) writes the bytes into the process when
         it starts; with --method gdb, gdb does (slower: about 80 ms more per compile, and only
         the plain level). The library replaces a tail jump to the getter with a jump to its own
         getter, which can answer a different level per call site, so a trace can hold only the
         parts it needs (--profile light, below) and only one graph colouring (--only K).

Exhume does not ship the addresses. They belong to one compiler build and are kept in a local,
git-ignored file, armtrace.local.toml (see armtrace.local.example.toml next to this profile).
`{level}` in a winedbg command is replaced by the level, so `{level:02x}` gives it in hex. The
file is looked for in this order: --local FILE, $EXHUME_ARMTRACE, the project root, then
profiles/armcc5/. If [trace] names a toolchain `home`, `run` refuses another home: addresses
found for one build mean nothing in another. A [linux.NAME] table is used when its `armcc`
names the Linux compiler being run.

`survey ARMCC_HOME` helps fill a [linux.NAME] table once its `hook` is known: it compiles a
source (by default a small built-in one) with the hook in place, lists the call sites of the
getter, compiles once per site with only that site traced, and prints the sites by what they
print (start, simplify, colouring, ...), ready to paste under [linux.NAME.sites].

Profiles (linux): `full` is every site at the level; `light` is the sites armtrace.parse_light
reads, each at the level that prints it: the start of each colouring, the passes and spills, each
removal with its weighted references, each move neighbour list, each colouring with the
registers its coloured neighbours take away (and from whom), its colour, and the coalescings.
It is about a tenth of the full trace and carries no graph dump and no heaps; armreplay cannot
check it, but it names, for each value, the order and the clashes that decided its register.

Options come from SRC's `/* opts: */` line or the project's c_opts, unless --opts gives them.
--prefix runs in a Wine prefix of its own; a new prefix is warmed first with `wine cmd /c exit`
and no pipes (linker.md: the first program under a new wineserver otherwise keeps its output
pipes open and never returns). Trace one function at a time, or use --only K (linux) for the
K-th colouring (from 1, in the order armcc generates the functions): a whole file gives one
colouring for every function.

`parse` prints what each graph colouring in a trace holds: its nodes, passes, removals, colours
and spills. armreplay.py replays a colouring and checks the replay against it.

The parsed form (parse_text) is a list of colourings, each a dict:

    header   {'num_nodes': N, 'second_clash_set': 0/1, ...} from the '*****' line
    g0       the first graph: {'nodes': {n: (colour, type)}, 'clash': {(a, b)},
             'move': {(a, b): width}}  (width 5 is a direct copy)
    passes   one or two: {'init': {n: (size, type, status, colour)}, 'steps': [...],
             'col': [...], 'spills': [(node, ...)], 'coalesced': [...], 'chg': [...]}
             A step is one removal ('*** Simplify called'): n, status, refs, pre (a spilled
             node removed first in pass 2), lists (the work lists as the trace prints them last
             before it, once the node has left its heap, each [(node, stale)]), before/after
             (its clash sets), ev (status changes and move-clash
             events in order) and coal (the coalescing lines). A col entry is one colouring
             ('Colouring N'): n, refs, type, avail, avail_mc, votes, colour.
    final    the last graph dump, or None

parse_light reads a light (or full) trace into a smaller form: a list of colourings, each
{'header', 'passes': [{'steps': [(n, refs)], 'movenb': {n: [partners]}, 'col': [(n, refs,
colour, marks)], 'spills': [n], 'coalesced': [(a, b)]}]}, marks being [(register, neighbour,
'c' for a clash or 'm' for a move clash)].
"""
import os, re, subprocess, sys, tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.dirname(HERE)
EXHUME = os.path.dirname(os.path.dirname(PROFILE))

INTS = re.compile(r'-?\d+')
START = '***** Start of graph colouring'


def ints(s):
    return [int(x) for x in INTS.findall(s)]


# ---------------------------------------------------------------- parsing


def _listline(s):
    """'extralownodes: 1 2x 3' -> [(1, False), (2, True), (3, False)]: x marks a heap entry
    out of order with a child."""
    return [(int(t.rstrip('x')), t.endswith('x')) for t in s.split(':', 1)[1].split()]


_EDGE = re.compile(r'N(\d+) -- N(\d+) \[style=(\w+), width=(\d+)\]')
_NODE = re.compile(r'N(\d+) \[style=filled, label="n\d+=(-?\d+)\\n\((\w+|\(unset\))\)')


def _graph(lines, i):
    nodes, clash, move = {}, set(), {}
    i += 1
    while not lines[i].startswith('---- END CLASH GRAPH'):
        m = _EDGE.match(lines[i])
        if m:
            a, b = int(m[1]), int(m[2])
            if m[3] == 'solid': clash.add((a, b))
            else: move[(a, b)] = int(m[4])
        else:
            m = _NODE.match(lines[i])
            if m: nodes[int(m[1])] = (int(m[2]), m[3])
        i += 1
    return {'nodes': nodes, 'clash': clash, 'move': move}, i + 1


_SET = re.compile(r'(Clashes|Whole node clashes|Extra clashes|Moves|Opt clashes) *\((\d+)\) *= ?(.*)')


def _sets(lines, i):
    d = {}
    while i < len(lines):
        m = _SET.match(lines[i])
        if not m: break
        d[m[1].split()[0]] = [(int(a), int(b) if b else None)
                              for a, b in re.findall(r'(-?\d+)(?:\((-?\d+)\))?', m[3])]
        i += 1
    return d, i


_INIT = re.compile(r'node (\d+) \(size (\d+), type ([^)]*)\)\)? initialised to status (\w+) and colour (-?\d+)')
_SIMP = re.compile(r'\*\*\* Simplify called for register (\d+) status = (\w+) refs = (\d+)')
_CHG = re.compile(r'Changing status for (\d+) from (\w+) to (\w+)')
_COL = re.compile(r'Colouring (\d+)\. Refs = (\d+)\. Type = (\w+)')
_LISTS = ('lownodes:', 'extralownodes:', 'highnodes:', 'highnonspills:')
_COAL = (" can't coalesce", ' coalescing with', ' whose colour', ' which is coalesced')


def _pass(lines, i, end):
    P = {'init': {}, 'nsns': [], 'steps': [], 'col': [], 'spills': [], 'coalesced': [], 'chg': [],
         'other': []}
    lists = step = col = None
    pre = None
    while i < end:
        l = lines[i]
        if l.startswith('node ') and 'initialised to status' in l:
            m = _INIT.match(l)
            P['init'][int(m[1])] = (int(m[2]), m[3], m[4], int(m[5]))
        elif l.startswith('Nonspill-nonspill clashes'):
            i += 1
            while i < end and re.match(r'\s+\d+ nclashes', lines[i]):
                m = re.match(r'\s+(\d+) nclashes = (\d+) clashes = (.*)', lines[i])
                P['nsns'].append((int(m[1]), ints(m[3])))
                i += 1
            continue
        elif l.startswith(_LISTS):
            lists = lists or {}
            lists[l.split(':')[0]] = _listline(l)
        elif l.startswith('Pre-spilling:'):
            pre = ints(l)[0]
        elif l.startswith('*** Simplify called'):
            m = _SIMP.match(l)
            step = {'n': int(m[1]), 'status': m[2], 'refs': int(m[3]), 'lists': lists,
                    'pre': pre == int(m[1]), 'ev': [], 'coal': None}
            lists = pre = None
            P['steps'].append(step)
        elif l.startswith('Before clash adjustment'):
            step['before'], i = _sets(lines, i + 1)
            continue
        elif l.startswith('After clash adjustment'):
            step['after'], i = _sets(lines, i + 1)
            continue
        elif l.startswith('Changing status for'):
            m = _CHG.match(l)
            e = (int(m[1]), m[2], m[3])
            if step is None: P['chg'].append(e)
            else: step['ev'].append(('chg',) + e)
        elif l.startswith('Adding move clashes for the move-neighbours'):
            pass
        elif l.startswith(' adding move clashes of'):
            step['ev'].append(('amc', ints(l)[1]))
        elif l.startswith(' clashes of') or l.startswith(' move clashes of'):
            step['ev'].append(('int', l.split()[0], ints(l)[1]))
        elif l.startswith('Making move-neighbours'):
            step['ev'].append(('mkrel',))
        elif l.startswith('Trying to coalesce'):
            step['coal'] = []
        elif l.startswith(_COAL):
            if step is None and P['coalesced']:
                P['coalesced'][-1] = P['coalesced'][-1] + (l.strip(),)
            elif step is not None:
                step['coal'].append(l.strip())
        elif l.startswith('Node ') and 'coalesced with' in l:
            P['coalesced'].append((len(P['col']), tuple(ints(l))))
        elif l.startswith('Colouring '):
            m = _COL.match(l)
            col = {'n': int(m[1]), 'refs': int(m[2]), 'type': m[3], 'votes': [], 'opp': []}
            P['col'].append(col)
            step = None
        elif l.startswith(' Register '):
            col['sets'], i = _sets(lines, i + 1)
            continue
        elif l.startswith('  Available regs (move_clashes):'):
            col['avail_mc'] = ints(l.split(':', 1)[1])
        elif l.startswith('  Available regs:'):
            col['avail'] = ints(l.split(':', 1)[1])
        elif l.startswith('Count '):
            col['votes'].append(tuple(ints(l)))
        elif l.startswith('  Opportunistic'):
            col['opp'].append(tuple(ints(l)))
        elif l.startswith('  Colour = '):
            col['colour'] = ints(l)[0]
        elif l.startswith('Actual spill:'):
            P['spills'].append(tuple(ints(l)))
        elif (l.strip() == '' or l.startswith(('Pass 1', 'Second pass', 'Simplifying ', 'minpos'))
              or l.strip().startswith('Marking')):
            pass
        else:
            P['other'].append((i, l))
        i += 1
    return P


def _colouring(lines):
    hdr = dict((k, int(v)) for k, v in re.findall(r'(\w+)=(-?\d+)', lines[0]))
    i = next(k for k, l in enumerate(lines) if l.startswith('---- BEGIN CLASH GRAPH'))
    g0, i = _graph(lines, i)
    while i < len(lines) and not (lines[i].startswith('node ') and 'initialised' in lines[i]):
        i += 1
    second = [k for k in range(i, len(lines)) if lines[k].startswith('Second pass.')]
    check = [k for k in range(i, len(lines)) if lines[k].startswith('Checking colours')]
    ck = check[0] if check else len(lines)
    passes = [_pass(lines, i, second[0] if second else ck)]
    if second:
        passes.append(_pass(lines, second[0] + 1, ck))
    gi = [k for k in range(ck, len(lines)) if lines[k].startswith('---- BEGIN CLASH GRAPH')]
    final = _graph(lines, gi[0])[0] if gi else None
    return {'header': hdr, 'g0': g0, 'passes': passes, 'final': final}


def parse_text(text):
    """Every graph colouring in a trace, in order."""
    lines = text.split('\n')
    starts = [k for k, l in enumerate(lines) if l.startswith(START)]
    return [_colouring(lines[a:b]) for a, b in zip(starts, starts[1:] + [len(lines)])]


def parse(path):
    with open(path, errors='replace') as f:
        return parse_text(f.read())


def pick(colourings, index=None):
    """One colouring: the one at index, else the largest."""
    if not colourings: raise SystemExit('no graph colouring in the trace (was the level set?)')
    if index is not None: return colourings[index]
    return max(colourings, key=lambda c: c['header'].get('num_nodes', len(c['g0']['nodes'])))


def summary(colourings):
    out = []
    for k, c in enumerate(colourings):
        ps = '; '.join(f'pass {j + 1}: {len(p["steps"])} removals, {len(p["col"])} coloured, '
                       f'{len(p["spills"])} spills' for j, p in enumerate(c['passes']))
        out.append(f'{k}: {c["header"].get("num_nodes", "?")} nodes, {len(c["g0"]["clash"])} clashes, '
                   f'{len(c["g0"]["move"])} moves; {ps}')
    return out


# ---------------------------------------------------------------- the light form

_L_SIMP = re.compile(r'\*\*\* Simplify called for register (\d+) status = \w+ refs = (\d+)')
_L_COL = re.compile(r'Colouring (\d+)\. Refs = (\d+)\.')
_L_MARK = re.compile(r'\s+Marking (\d+) unavailable due to clash between \d+ and (\d+)\(-?\d+\) \((clash|move clash)\)')
_L_MNB = re.compile(r'Adding move clashes for the move-neighbours of (\d+):(.*)')
_L_COAL = re.compile(r'Node (\d+) coalesced with (\d+)')


def parse_light(text, index=None):
    """The light form of every colouring in a trace (or only the index-th): see the module
    docstring. Reads a full trace too (it skips what it does not need)."""
    out = []
    P = col = None
    k = -1
    for l in text.split('\n'):
        c = l[:3]
        if c == '***':
            if l.startswith(START):
                k += 1
                if index is not None and k != index:
                    P = None; continue
                hdr = dict((a, int(b)) for a, b in re.findall(r'(\w+)=(-?\d+)', l))
                P = {'steps': [], 'movenb': {}, 'col': [], 'spills': [], 'coalesced': []}
                out.append({'header': hdr, 'passes': [P]})
                col = None
            elif P is not None and l.startswith('*** Simplify'):
                m = _L_SIMP.match(l)
                P['steps'].append((int(m[1]), int(m[2])))
            continue
        if P is None: continue
        if c == '   ' and l.startswith('     Marking'):
            m = _L_MARK.match(l)
            if m and col is not None: col[3].append((int(m[1]), int(m[2]), 'c' if m[3] == 'clash' else 'm'))
        elif c == 'Col':
            m = _L_COL.match(l)
            if m:
                col = [int(m[1]), int(m[2]), None, []]
                P['col'].append(col)
        elif c == '  C' and l.startswith('  Colour = '):
            if col is not None: col[2] = int(l[11:].split('.')[0])
        elif c == 'Add' and l.startswith('Adding move clashes for the move-neighbours'):
            m = _L_MNB.match(l)
            P['movenb'][int(m[1])] = [int(x) for x in m[2].split()]
        elif c == 'Sec' and l.startswith('Second pass.'):
            P = {'steps': [], 'movenb': {}, 'col': [], 'spills': [], 'coalesced': []}
            out[-1]['passes'].append(P)
            col = None
        elif c == 'Act' and l.startswith('Actual spill:'):
            P['spills'].append(ints(l)[0])
        elif c == 'Nod' and 'coalesced with' in l:
            m = _L_COAL.match(l)
            if m: P['coalesced'].append((int(m[1]), int(m[2])))
    return out


def light_colours(L):
    """{node: register} of a light colouring's last pass (a coalesced node takes its partner's)."""
    P = L['passes'][-1]
    col = {n: c for n, _, c, _ in P['col'] if c is not None}
    for a, b in P['coalesced']:
        if a not in col:
            x = b
            for _ in range(64):
                if x in col: col[a] = col[x]; break
                y = next((q for p, q in P['coalesced'] if p == x), None)
                if y is None: break
                x = y
    return col


# ---------------------------------------------------------------- running a traced compile


def find_local(explicit=None, root=None):
    for p in (explicit, os.environ.get('EXHUME_ARMTRACE'),
              root and os.path.join(root, 'armtrace.local.toml'),
              os.path.join(PROFILE, 'armtrace.local.toml')):
        if p and os.path.exists(p): return p
    raise SystemExit('no armtrace.local.toml: copy profiles/armcc5/armtrace.local.example.toml and fill '
                     'in the commands for your armcc build (compiler.md, "Tracing the register allocator")')


def load_local(path, need='wine'):
    """The [trace] table (need='wine') of the local file, or its whole contents (need=None)."""
    with open(path, 'rb') as f: d = tomllib.load(f)
    if need is None: return d
    t = d.get('trace', {})
    if not t.get('commands'): raise SystemExit(f'{path}: [trace] commands is empty')
    return t


# ---------------------------------------------------------------- the Linux compiler

SHIM_SRC = os.path.join(HERE, 'armtrace_shim', 'armtrace_shim.c')

# what each call site of the getter prints first (survey), and the lowest level I saw print it
# with all sites at that level (a site alone can need more); the light profile is the kinds
# parse_light reads
KINDS = [
    ('start', r'\*\*\*\*\* Start of graph colouring', 1), ('pass1', r'Pass 1', 2),
    ('second', r'Second pass\.', 1), ('spill', r'Actual spill:', 1), ('prespill', r'Pre-spilling:', 1),
    ('ensure', r'Ensuring graph colourability', 1), ('lists0', r'lownodes:', 1),
    ('simplify', r'\*\*\* Simplify called', 1), ('colouring', r'Colouring \d+\. Refs', 1),
    ('colour', r'  Colour = ', 1), ('mark', r'\s+Marking .*\(clash\)', 1),
    ('markmove', r'\s+Marking .*\(move cla', 1), ('movenb', r'Adding move clashes for the move-neighbours', 3),
    ('coalesced', r'Node \d+ coalesced with', 1), ('coalesced2', r' which is coalesced with', 1),
    ('coalcolour', r' whose colour is', 1), ('coalescing', r' coalescing with', 1),
    ('graph', r'---- BEGIN CLASH GRAPH', 3), ('init', r'node \d+ \(size', 2), ('lists', r'lownodes:', 2),
    ('before', r'Before clash adjustment', 2), ('after', r'After clash adjustment', 2),
    ('register', r' Register \d+:', 2), ('avail', r'  Available regs', 2), ('status', r'Changing status for', 3),
    ('votes', r'minpos = ', 1), ('opportunistic', r'  Opportunistic coalescing', 1),
    ('trycoal', r'Trying to coalesce', 1), ('cantcoal', r" can't coalesce", 1),
]
LIGHT = ('start', 'second', 'spill', 'simplify', 'colouring', 'colour', 'mark', 'markmove', 'movenb',
         'coalesced', 'coalesced2', 'coalcolour')


def linux_entry(local, armcc):
    """The [linux.NAME] table of the local file whose `armcc` is this compiler."""
    d = load_local(local, None)
    real = os.path.realpath(os.path.expanduser(armcc))
    for name, t in (d.get('linux') or {}).items():
        if os.path.realpath(os.path.expanduser(t.get('armcc', ''))) == real:
            if not t.get('hook'): raise SystemExit(f'{local}: [linux.{name}] has no hook')
            return dict(t, name=name)
    raise SystemExit(f'{local}: no [linux.NAME] table names {real} as its armcc')


def shim_path():
    """The LD_PRELOAD library, built from armtrace_shim.c on first use (in the user's cache,
    named by the source's hash) with the first compiler that makes 32-bit x86 code."""
    import hashlib, shutil
    with open(SHIM_SRC, 'rb') as f: h = hashlib.sha1(f.read()).hexdigest()[:12]
    d = os.environ.get('EXHUME_CACHE') or os.path.join(os.path.expanduser('~'), '.cache', 'exhume')
    so = os.path.join(d, f'armtrace_shim-{h}.so')
    if os.path.exists(so): return so
    os.makedirs(d, exist_ok=True)
    tmp = so + f'.{os.getpid()}'
    for cc in ('cc', 'clang', 'gcc'):
        if not shutil.which(cc): continue
        r = subprocess.run([cc, '-m32', '-O2', '-fPIC', '-shared', '-nostdlib', '-o', tmp, SHIM_SRC],
                           capture_output=True, text=True)
        if r.returncode == 0:
            os.replace(tmp, so); return so
    raise SystemExit('could not build armtrace_shim.so: a C compiler that accepts -m32 is needed')


def trace_env(entry, level=5, profile='full', only=None, survey=False):
    """The environment that turns the trace on in a Linux armcc started with it: LD_PRELOAD and
    the shim's variables. profile 'full' is every site at level; 'light' the LIGHT kinds only, at
    level (each site prints only its own lines); only=K traces only the K-th colouring."""
    env = {'LD_PRELOAD': shim_path(), 'ARMTRACE_HOOK': entry['hook']}
    sites = entry.get('sites') or {}
    if profile == 'full':
        env['ARMTRACE_LEVELS'] = str(level)
    else:
        missing = [k for k in ('start', 'simplify', 'colouring', 'colour', 'mark') if k not in sites]
        if missing: raise SystemExit(f'[linux.{entry["name"]}.sites] lacks {", ".join(missing)} (armtrace.py survey)')
        env['ARMTRACE_LEVELS'] = '0' + ''.join(f',{sites[k]}={level}' for k in LIGHT if k in sites)
    if only:
        if 'start' not in sites: raise SystemExit(f'[linux.{entry["name"]}.sites] has no start site (armtrace.py survey)')
        env['ARMTRACE_ONLY'] = f'{sites["start"]}:{int(only)}'
    if survey: env['ARMTRACE_SURVEY'] = '1'
    return env


def linux_armcc(home=None):
    home = home or os.environ.get('EXHUME_ARMCC_LINUX')
    if not home or not os.path.isfile(os.path.join(home, 'bin', 'armcc')):
        raise SystemExit('the linux runner needs EXHUME_ARMCC_LINUX: a Linux armcc home (bin/armcc)')
    maps = os.environ.get('EXHUME_ARMCC_MAPPINGS')
    if not maps: raise SystemExit('the linux runner needs EXHUME_ARMCC_MAPPINGS: <MDK>/ARM/sw/mappings')
    return os.path.join(home, 'bin', 'armcc'), dict(os.environ, ARM_TOOL_VARIANT='mdk_lite', ARM_PRODUCT_PATH=maps)


def gdb_script(entry, level):
    """gdb's input for --method gdb: at the first instruction, the hook address made to answer level
    (push imm8; pop eax; ret, in the place of the tail jump), then run to the end."""
    a = entry['hook'].split(':')[0]
    return ('set pagination off\nset startup-with-shell off\nstarti\n'
            f'set *(unsigned int*)0x{a} = 0xc358{level:02x}6a\ncontinue\nquit\n')


def run_linux(armcc, env, args, cwd, out, method='preload', entry=None, level=5, timeout=900, extra_env=None):
    """Compile with the trace on; returns the completed process."""
    if method == 'gdb':
        import tempfile
        with tempfile.NamedTemporaryFile('w', suffix='.gdb', delete=False) as f:
            f.write(gdb_script(entry, level)); g = f.name
        try:
            return subprocess.run(['gdb', '-q', '-batch', '-x', g, '--args', armcc] + args + ['--errors=' + out],
                                  cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout)
        finally:
            os.remove(g)
    e = dict(env, **(extra_env or {}))
    return subprocess.run([armcc] + args + ['--errors=' + out], cwd=cwd, env=e, capture_output=True,
                          text=True, timeout=timeout)


SURVEY_SRC = """extern int g[16];
extern void use(int, int);
int f(int a, int b, int c, int d)
{
    int i, s = 0, t = 1, u = 2, v = 3, w = 4, x = 5, y = 6, z = 7;
    for (i = 0; i < a; i++) {
        s += g[i & 15] * b; t ^= s << 1; u += t * c; v -= u >> 2; w += v * d; x ^= w; y += x * i; z -= y;
        if (s > c) use(s + t + u, v + w + x + y + z);
    }
    return s + t + u + v + w + x + y + z + a * c;
}
"""


def survey(home, local, src=None, opts='--cpu Cortex-M3 -O1'):
    """[(site, kind or '?', first line)] of the getter's call sites in one compile."""
    import tempfile
    armcc, env = linux_armcc(home)
    entry = linux_entry(local, armcc)
    work = tempfile.mkdtemp(prefix='armtrace-survey-')
    try:
        if src is None:
            src = os.path.join(work, 'survey.c')
            with open(src, 'w') as f: f.write(SURVEY_SRC)
        args = ['-c'] + opts.split() + ['-W', os.path.abspath(src), '-o', os.path.join(work, 'x.o')]
        out = os.path.join(work, 'x.txt')
        r = run_linux(armcc, env, args, work, out, extra_env=trace_env(entry, 0, survey=True))
        # (armcc sends its own stderr to the --errors file, and the shim's lines with it)
        with open(out, errors='replace') as f: said = f.read()
        sites = sorted(set(re.findall(r'armtrace_shim: site ([0-9a-f]{8})', r.stderr + said)))
        res = []
        for site in sites:
            e = trace_env(entry, 0)
            e['ARMTRACE_LEVELS'] = f'0,{site}=9'
            run_linux(armcc, env, args, work, out, extra_env=e)
            with open(out, errors='replace') as f:
                first = next((l for l in f.read().split('\n') if l.strip()), '')
            kind = next((k for k, pat, _ in KINDS if re.match(pat, first)), '?')
            res.append((site.lstrip('0'), kind, first[:70]))
        return res
    finally:
        import shutil
        shutil.rmtree(work, ignore_errors=True)


def debugger_script(commands, level):
    """winedbg's input: the local commands with the level filled in, then run to the end."""
    return ''.join(c.format(level=level) + '\n' for c in commands) + 'cont\nquit\n'


def warm(env, wine='wine'):
    """Start the prefix's server and Wine's services with no pipes attached (linker.md)."""
    subprocess.run([wine, 'cmd', '/c', 'exit'], env=env, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)


def run(cfg, src, out, level=5, local=None, prefix=None, opts=None, timeout=900, runner=None,
        method='preload', profile='full', only=None):
    sys.path.insert(0, HERE)
    import armvariant as V
    runner = runner or ('linux' if os.environ.get('EXHUME_ARMCC_RUNNER') == 'linux' else 'wine')
    if runner == 'linux':
        return run_linux_src(cfg, src, out, level, local, opts, timeout, method, profile, only)
    t = load_local(find_local(local, cfg.root))
    R = V.Runner(cfg, prefix)
    if R.tc.runner != 'wine':
        raise SystemExit('armtrace run needs host Wine with winedbg (EXHUME_ARMCC_RUNNER=wine)')
    if t.get('home') and os.path.realpath(os.path.expanduser(t['home'])) != R.tc.home:
        raise SystemExit(f'the local trace commands are for {t["home"]}, the project uses {R.tc.home}')
    if opts is None:
        _, opts = cfg.directives(src)
        opts = opts or cfg.c_opts
    src = os.path.abspath(src); out = os.path.abspath(out)
    obj = os.path.splitext(out)[0] + '.o'
    for p in (out, obj):
        if os.path.exists(p): os.remove(p)
    if prefix and not os.path.exists(os.path.join(prefix, 'system.reg')):
        warm(R.env, V.WINE)
    exe = os.path.join(R.tc.home, 'ARM', 'ARMCC', 'bin', 'armcc.exe')
    cmd = [V.WINE, 'winedbg', exe, '-c'] + R.opts(opts) + ['--errors=' + out, src, '-o', obj]
    try:
        subprocess.run(cmd, cwd=os.path.dirname(src), env=R.env, input=debugger_script(t['commands'], level),
                       text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
    except subprocess.TimeoutExpired:
        server = os.path.join(os.path.dirname(V.WINE), 'wineserver') if os.sep in V.WINE else 'wineserver'
        subprocess.run([server, '-k'], env=R.env, capture_output=True)
        raise SystemExit(f'winedbg did not finish in {timeout} s (prefix server stopped)')
    if not os.path.exists(out): raise SystemExit('no trace written')
    cs = parse(out)
    if not cs: raise SystemExit(f'{out}: no graph colouring (did armcc stop on an error, or the level not take?)')
    return out, os.path.exists(obj), cs


def run_linux_src(cfg, src, out, level=5, local=None, opts=None, timeout=900, method='preload',
                  profile='full', only=None):
    """run() with the Linux compiler: (trace path, object written?, parsed colourings)."""
    armcc, env = linux_armcc()
    entry = linux_entry(find_local(local, cfg.root), armcc)
    if method == 'gdb' and (profile != 'full' or only):
        raise SystemExit('--method gdb gives the full trace only (use the preload method for --profile or --only)')
    if opts is None:
        _, opts = cfg.directives(src)
        opts = opts or cfg.c_opts
    opts = opts.replace('{root}', os.path.realpath(cfg.root)).replace('-I/work/', '-I' + os.path.realpath(cfg.root) + '/')
    src = os.path.abspath(src); out = os.path.abspath(out)
    obj = os.path.splitext(out)[0] + '.o'
    for p in (out, obj):
        if os.path.exists(p): os.remove(p)
    args = ['-c'] + opts.split() + [src, '-o', obj]
    extra = None if method == 'gdb' else trace_env(entry, level, profile, only)
    try:
        run_linux(armcc, env, args, os.path.dirname(src), out, method, entry, level, timeout, extra)
    except subprocess.TimeoutExpired:
        raise SystemExit(f'the traced compile did not finish in {timeout} s')
    if not os.path.exists(out): raise SystemExit('no trace written')
    with open(out, errors='replace') as f: text = f.read()
    cs = parse_text(text) if profile == 'full' else parse_light(text)
    if not cs: raise SystemExit(f'{out}: no graph colouring (did armcc stop on an error, or the level not take?)')
    return out, os.path.exists(obj), cs


def main(argv):
    sys.path.insert(0, os.path.join(EXHUME, 'tools'))
    import config
    path, a = config.pop_config(argv)
    if not a or a[0] not in ('run', 'parse', 'survey'):
        print(__doc__); return 2
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    if a[0] == 'parse':
        with open(a[1], errors='replace') as f: text = f.read()
        cs = parse_text(text) if '---- BEGIN CLASH GRAPH' in text else None
        if cs:
            for line in summary(cs): print(line)
        else:
            for k, L in enumerate(parse_light(text)):
                print(f'{k}: {L["header"].get("num_nodes", "?")} nodes (light); ' + '; '.join(
                    f'pass {j + 1}: {len(P["steps"])} removals, {len(P["col"])} coloured, {len(P["spills"])} spills'
                    for j, P in enumerate(L['passes'])))
        return 0
    if a[0] == 'survey':
        root = None
        try: root = config.load(path).root
        except BaseException: pass
        res = survey(a[1], find_local(opt('--local'), root), opt('--src'), opt('--opts', '--cpu Cortex-M3 -O1'))
        print('# call sites of the getter, by what each prints first; for [linux.NAME.sites]:')
        seen = set()
        for site, kind, first in res:
            if kind != '?' and kind not in seen:
                seen.add(kind); print(f'{kind} = "{site}"    # {first}')
            else:
                print(f'# {site}: {first}')
        return 0
    cfg = config.load(path)
    src = a[1]
    out = opt('--out', os.path.join(cfg.build, 'trace', os.path.splitext(os.path.basename(src))[0] + '.trace'))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    prof = opt('--profile', 'full')
    out, have_obj, cs = run(cfg, src, out, int(opt('--level', 5)), opt('--local'), opt('--prefix'),
                            opt('--opts'), int(opt('--timeout', 900)), opt('--runner'), opt('--method', 'preload'),
                            prof, opt('--only'))
    print(out, '(object written)' if have_obj else '(no object)')
    if prof == 'full':
        for line in summary(cs): print(' ', line)
    else:
        print(f'  {len(cs)} colourings (light)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
