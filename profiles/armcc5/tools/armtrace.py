"""armcc's register-allocator trace: compile one source with the trace on, and read the trace.

    python3 armtrace.py [--config PATH] run SRC [--out TRACE] [--level N] [--local FILE]
                                        [--prefix DIR] [--opts '...'] [--timeout S]
    python3 armtrace.py parse TRACE

armcc 5's back end has a register-allocation trace compiled into the release build. It is off:
it prints only when the back end's debug level is 5 or more, and no command-line option sets
that level (compiler.md, "Tracing the register allocator"). `run` compiles SRC under winedbg and,
before the compile starts, sends winedbg the commands that make the running process see a debug
level of N (default 5). Nothing on disk changes: the commands write only to the memory of the
process winedbg started. The trace goes where armcc's diagnostics go, here `--errors=TRACE`.

Exhume does not ship those commands. They name addresses inside one armcc.exe build, and they
are kept in a local, git-ignored file, armtrace.local.toml (see armtrace.local.example.toml next
to this profile). `{level}` in a command is replaced by the level, so `{level:02x}` gives it in
hex. The file is looked for in this order: --local FILE, $EXHUME_ARMTRACE, the project root,
then profiles/armcc5/. If it names a toolchain `home`, `run` refuses another home: addresses
found for one build mean nothing in another.

`run` needs host Wine (EXHUME_ARMCC_RUNNER=wine) with winedbg. Options come from SRC's
`/* opts: */` line or the project's c_opts, unless --opts gives them. --prefix runs in a Wine
prefix of its own; a new prefix is warmed first with `wine cmd /c exit` and no pipes
(linker.md: the first program under a new wineserver otherwise keeps its output pipes open and
never returns). Trace one function at a time: a source holding only that function and the
declarations it needs gives one graph colouring per pass, where a whole file gives one for
every function, in the order armcc generates them.

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


# ---------------------------------------------------------------- running a traced compile


def find_local(explicit=None, root=None):
    for p in (explicit, os.environ.get('EXHUME_ARMTRACE'),
              root and os.path.join(root, 'armtrace.local.toml'),
              os.path.join(PROFILE, 'armtrace.local.toml')):
        if p and os.path.exists(p): return p
    raise SystemExit('no armtrace.local.toml: copy profiles/armcc5/armtrace.local.example.toml and fill '
                     'in the commands for your armcc build (compiler.md, "Tracing the register allocator")')


def load_local(path):
    with open(path, 'rb') as f: t = tomllib.load(f).get('trace', {})
    if not t.get('commands'): raise SystemExit(f'{path}: [trace] commands is empty')
    return t


def debugger_script(commands, level):
    """winedbg's input: the local commands with the level filled in, then run to the end."""
    return ''.join(c.format(level=level) + '\n' for c in commands) + 'cont\nquit\n'


def warm(env, wine='wine'):
    """Start the prefix's server and Wine's services with no pipes attached (linker.md)."""
    subprocess.run([wine, 'cmd', '/c', 'exit'], env=env, stdin=subprocess.DEVNULL,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=300)


def run(cfg, src, out, level=5, local=None, prefix=None, opts=None, timeout=900):
    sys.path.insert(0, HERE)
    import armvariant as V
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


def main(argv):
    sys.path.insert(0, os.path.join(EXHUME, 'tools'))
    import config
    path, a = config.pop_config(argv)
    if not a or a[0] not in ('run', 'parse'):
        print(__doc__); return 2
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    if a[0] == 'parse':
        for line in summary(parse(a[1])): print(line)
        return 0
    cfg = config.load(path)
    src = a[1]
    out = opt('--out', os.path.join(cfg.build, 'trace', os.path.splitext(os.path.basename(src))[0] + '.trace'))
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    out, have_obj, cs = run(cfg, src, out, int(opt('--level', 5)), opt('--local'), opt('--prefix'),
                            opt('--opts'), int(opt('--timeout', 900)))
    print(out, '(object written)' if have_obj else '(no object)')
    for line in summary(cs): print(' ', line)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
