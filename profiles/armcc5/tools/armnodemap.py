"""Map a traced function's allocator nodes to the instructions that use them.

    python3 armnodemap.py map TRACE LISTING FUNC [--index N] [--out MAP.json]
    python3 armnodemap.py want MAP.json DIFF.txt [--out WANT.json]

armcc's allocator trace (armtrace.py) names nodes but prints no code, so a register the image has
the other way round cannot be traced back to a node from the trace alone. `map` rebuilds the
same information from the code: LISTING is `fromelf -c` of the object the traced compile wrote
(same source and options; MDK-Lite objects keep their ELF headers obscured, fromelf reads them),
FUNC the function. Its instructions are decoded (registers read and written), the
registers' lifetimes are split into webs (the definitions that reach a common use, over the
function's branches), and two webs clash when one is live where the other is defined. The
traced final graph has a register for every node and the clashes between them; webs and nodes
are matched by register, then by refining labels over their clashes (each round adds the
sorted labels of the neighbours) and by order (node numbers grow with the code). Each match is
checked: the web's register is the node's colour by construction, and the report gives how
many webs and nodes stayed unmatched or ambiguous.

`want` turns a scorer's instruction diff (fnscore.py: `-  N insn` for the original, `+  N insn`
for the build, N the instruction index) into the registers the original gives the matched
nodes, as {node: register} for armreplay.py sweep --want and refs: an instruction whose only
difference is its registers says which register each of its webs has in the original.
"""
import collections, json, re, struct, sys

REGS = ['r0', 'r1', 'r2', 'r3', 'r4', 'r5', 'r6', 'r7', 'r8', 'r9', 'r10', 'r11', 'r12', 'sp', 'lr', 'pc']
ALIAS = {'sb': 'r9', 'sl': 'r10', 'fp': 'r11', 'ip': 'r12'}
NUM = {r: i for i, r in enumerate(REGS)}
ALLOC = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14]


# ---- object ------------------------------------------------------------------------------------
def listing_function(path, func):
    """(bytes, [(start, end)] code ranges, start, end) of FUNC from a fromelf -c listing, with
    offsets relative to its section."""
    cur, code, data, start, end, insec = None, [], {}, None, None, False
    LBL = re.compile(r'^    ([A-Za-z_$][\w$.]*)\s*$')
    INS = re.compile(r'^\s+0x([0-9a-f]{8}):\s+([0-9a-f]{4,8})\s')
    mode, lines = '$t', open(path, errors='replace').read().splitlines()
    for l in lines:
        if l.startswith('** Section'):
            if insec and start is not None: break
            cur = None; continue
        m = LBL.match(l)
        if m:
            n = m.group(1)
            if n.startswith('$t'): mode = '$t'
            elif n.startswith('$d'): mode = '$d'
            elif not n.startswith('$'):
                if start is not None: end = end; break
                if n == func: insec = True
                cur = n
            continue
        m = INS.match(l)
        if m and cur == func:
            a = int(m.group(1), 16); h = m.group(2)
            if start is None: start = a
            if mode == '$d':
                data[a] = bytes.fromhex(h)[::-1]
            else:
                bs = b''.join(bytes.fromhex(h[i:i + 4])[::-1] for i in range(0, len(h), 4))
                data[a] = bs
                code.append((a, a + len(bs)))
            end = a + len(data[a])
    if start is None: raise SystemExit(f'{func}: not in {path}')
    buf = bytearray(end)
    for a, b in data.items(): buf[a:a + len(b)] = b
    rng = []
    for a, b in code:
        if rng and rng[-1][1] == a: rng[-1] = (rng[-1][0], b)
        else: rng.append((a, b))
    return bytes(buf), rng, start, end


def decode(data, code):
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB); md.detail = True
    out = []
    for a, b in code:
        for i in md.disasm(data[a:b], a):
            try:
                rd, wr = i.regs_access()
            except capstone.CsError:
                rd, wr = [], []
            r = {NUM.get(ALIAS.get(i.reg_name(x), i.reg_name(x))) for x in rd} - {None}
            w = {NUM.get(ALIAS.get(i.reg_name(x), i.reg_name(x))) for x in wr} - {None}
            out.append(dict(addr=i.address, mn=i.mnemonic, op=i.op_str, r=r, w=w, size=i.size))
    return out


# ---- webs --------------------------------------------------------------------------------------
ROUNDS, WINDOW = 8, 0.15
BR = re.compile(r'^(b|b\.w|b[a-z]{2}|b[a-z]{2}\.w|cbz|cbnz)$')


def cfg(ins):
    at = {x['addr']: k for k, x in enumerate(ins)}
    succ = []
    for k, x in enumerate(ins):
        s = []
        mn = x['mn']
        tgt = re.search(r'#0x([0-9a-f]+)', x['op'])
        fall = k + 1 < len(ins)
        if mn in ('b', 'b.w') and tgt:
            s = [at[int(tgt.group(1), 16)]] if int(tgt.group(1), 16) in at else []
        elif BR.match(mn) and tgt and mn not in ('bl', 'blx', 'bx'):
            s = ([at[int(tgt.group(1), 16)]] if int(tgt.group(1), 16) in at else []) + ([k + 1] if fall else [])
        elif mn in ('bx',) or (mn.startswith('pop') and 15 in x['w']) or (mn.startswith('ldm') and 15 in x['w']):
            s = []
        elif mn in ('tbb', 'tbh'):
            s = [k + 1] if fall else []        # table targets follow; approximated by fall-through
        else:
            s = [k + 1] if fall else []
        succ.append(s)
    return succ


RETURNS = 0      # registers a return reads (0 for a void function)
PLUMBING = False # skip r0-r3 webs that only carry call arguments or results


def call_args(ins, k):
    """Argument registers of the call at k: the highest of r0-r3 written since the previous
    call or branch target in straight-line code, plus one (armcc sets arguments just before)."""
    hi = -1
    for j in range(k - 1, max(-1, k - 12), -1):
        x = ins[j]
        if x['mn'] in ('bl', 'blx') or BR.match(x['mn']) or x['mn'] in ('b', 'b.w'): break
        for r in x['w']:
            if r <= 3: hi = max(hi, r)
    return hi + 1


def effects(x, nargs_call=None):
    """(reads, writes) with calls made explicit: a bl reads its argument registers and writes the
    caller-saved ones; a return reads RETURNS registers."""
    r, w = set(x['r']), set(x['w'])
    if x['mn'] in ('bl', 'blx'):
        r |= set(range(x.get('nargs', 4) if nargs_call is None else nargs_call)); w |= {0, 1, 2, 3, 12, 14}
    if x['mn'] == 'bx' or (x['mn'].startswith('pop') and 15 in w):
        r |= set(range(RETURNS))
    return r & set(ALLOC), w & set(ALLOC)


def webs(ins):
    for k, x in enumerate(ins):
        if x['mn'] in ('bl', 'blx'): x['nargs'] = call_args(ins, k)
    succ = cfg(ins)
    pred = collections.defaultdict(list)
    for k, s in enumerate(succ):
        for t in s: pred[t].append(k)
    eff = [effects(x) for x in ins]
    # reaching definitions: def = (instruction index, register); -1 is the entry
    n = len(ins)
    IN = [dict() for _ in range(n)]
    OUT = [dict() for _ in range(n)]
    entry = {reg: frozenset({(-1, reg)}) for reg in ALLOC}
    changed = True
    while changed:
        changed = False
        for k in range(n):
            inn = dict(entry) if k == 0 else {}
            for p in pred[k]:
                for reg, ds in OUT[p].items(): inn[reg] = inn.get(reg, frozenset()) | ds
            out = dict(inn)
            for reg in eff[k][1]: out[reg] = frozenset({(k, reg)})
            if inn != IN[k] or out != OUT[k]:
                IN[k], OUT[k] = inn, out; changed = True
    # union definitions reaching a common use
    parent = {}
    def find(a):
        while parent.setdefault(a, a) != a: a = parent[a]
        return a
    for k in range(n):
        for reg in eff[k][0]:
            ds = sorted(IN[k].get(reg, ()))
            for d in ds[1:]: parent[find(d)] = find(ds[0])
            for d in ds: find(d)
    for k in range(n):
        for reg in eff[k][1]: find((k, reg))
    web = {d: find(d) for d in list(parent)}
    # liveness of webs: backwards, per instruction
    live_out = [set() for _ in range(n)]
    live_in = [set() for _ in range(n)]
    changed = True
    while changed:
        changed = False
        for k in range(n - 1, -1, -1):
            out = set()
            for t in succ[k]: out |= live_in[t]
            uses = {web[d] for reg in eff[k][0] for d in IN[k].get(reg, ()) if d in web}
            defs = {web[(k, reg)] for reg in eff[k][1] if (k, reg) in web}
            inn = uses | (out - defs)
            if out != live_out[k] or inn != live_in[k]:
                live_out[k], live_in[k] = out, inn; changed = True
    clash = collections.defaultdict(set)
    for k in range(n):
        for reg in eff[k][1]:
            d = web.get((k, reg))
            if d is None: continue
            for o in live_out[k]:
                if o != d and o[1] != d[1] or (o != d and o[1] == d[1] and False):
                    clash[d].add(o); clash[o].add(d)
    # the instructions each web touches
    touch = collections.defaultdict(set)
    for k in range(n):
        for reg in eff[k][0]:
            for d in IN[k].get(reg, ()):
                if d in web: touch[web[d]].add(k)
        for reg in eff[k][1]:
            if (k, reg) in web: touch[web[(k, reg)]].add(k)
    roots = sorted({w for w in web.values()})
    return roots, clash, touch, web, IN, eff


# ---- matching ----------------------------------------------------------------------------------
def refine(labels, adj, rounds=6):
    for _ in range(rounds):
        new = {v: (labels[v], tuple(sorted(labels[u] for u in adj.get(v, ())))) for v in labels}
        ids = {l: i for i, l in enumerate(sorted(set(new.values()), key=repr))}
        new = {v: ids[l] for v, l in new.items()}
        if len(set(new.values())) == len(set(labels.values())): return new
        labels = new
    return labels


def node_groups(T, col):
    """Nodes joined by a move and given one register: the copy between them is dropped, so the
    code has one web for the group. {node: group representative}."""
    import armreplay as R
    G = R.graph(T)
    parent = {n: n for n in col}
    def find(a):
        while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
        return a
    for a in col:
        for b in G['M'].get(a, ()):
            if b in col and col[a] == col[b]: parent[find(a)] = find(b)
    return {n: find(n) for n in col}


def align(ws, ns):
    """Align webs (code order) with node groups (node order) of one register: each group takes
    one web or a run of consecutive webs, and either side may skip one at a cost.
    Returns {group: [webs]}."""
    INF = float('inf')
    W, N = len(ws), len(ns)
    D = [[INF] * (N + 1) for _ in range(W + 1)]; B = [[None] * (N + 1) for _ in range(W + 1)]
    D[0][0] = 0
    for a in range(W + 1):
        for b in range(N + 1):
            d = D[a][b]
            if d == INF: continue
            for k in (1, 2, 3):                     # group b takes webs a..a+k-1
                if a + k <= W and b < N and d + (k - 1) * 0.6 < D[a + k][b + 1]:
                    D[a + k][b + 1] = d + (k - 1) * 0.6; B[a + k][b + 1] = (a, b, k)
            if a < W and d + 1 < D[a + 1][b]: D[a + 1][b] = d + 1; B[a + 1][b] = (a, b, 0)    # web unmatched
            if b < N and d + 1 < D[a][b + 1]: D[a][b + 1] = d + 1; B[a][b + 1] = (a, b, -1)   # group unmatched
    out, a, b = {}, W, N
    while (a, b) != (0, 0):
        pa, pb, k = B[a][b]
        if k > 0: out[ns[pb]] = ws[pa:pa + k]
        a, b = pa, pb
    return out, D[W][N]


def match(T, roots, clash, touch, ins, moves=None, quiet=False):
    """moves: {node: partners} in place of the trace's first graph (a light trace, permtrace.py)."""
    fin = T['final']
    col0 = {n: c for n, (c, t) in fin['nodes'].items() if t != 'REAL' and isinstance(c, int) and c >= 0}
    if moves is None:
        grp = node_groups(T, col0)
    else:
        parent = {n: n for n in col0}
        def find(a):
            while parent[a] != a: parent[a] = parent[parent[a]]; a = parent[a]
            return a
        for a in col0:
            for b in moves.get(a, ()):
                if b in col0 and col0[a] == col0[b]: parent[find(a)] = find(b)
        grp = {n: find(n) for n in col0}
    members = collections.defaultdict(list)
    for n, g in grp.items(): members[g].append(n)
    reps = sorted(members, key=lambda g: min(members[g]))
    def keep(r):
        if not touch.get(r): return False
        if r[0] >= 0 and ins[r[0]]['mn'].startswith(('pop', 'ldm')) and r[1] >= 4: return False   # a register restored
        if r[0] >= 0 and not any(r[1] in effects(ins[k])[0] for k in touch[r] if k != r[0]): return False   # never read
        # call plumbing lives in the precoloured r0-r3, not in an allocator node: a call's result,
        # or a value set up only to be passed to calls
        if PLUMBING and r[1] <= 3 and r[0] >= 0:
            if ins[r[0]]['mn'] in ('bl', 'blx'): return False
            reads = [k for k in touch[r] if k != r[0] and r[1] in effects(ins[k])[0]]
            if reads and all(ins[k]['mn'] in ('bl', 'blx') for k in reads): return False
        if r[0] < 0: return any(r[1] in effects(ins[k])[0] and not ins[k]['mn'].startswith('push') for k in touch[r])  # an argument that is used
        return True
    out, cost, nw = {}, 0, 0
    for reg in ALLOC:
        ws = sorted((r for r in roots if r[1] == reg and keep(r)), key=lambda r: (r[0] >= 0, min(touch[r])))
        ns = [g for g in reps if col0[g] == reg]
        nw += len(ws)
        m, c = align(ws, ns); cost += c
        for g, w in m.items():
            for n in members[g]: out[n] = w
    # refine: reassign each register's groups to webs by how well their traced clashes agree
    # with the code's clashes of the neighbours placed so far (greedy, within an order window)
    gadj = collections.defaultdict(set)
    for a, b in fin['clash']:
        if a in grp and b in grp and grp[a] != grp[b]: gadj[grp[a]].add(grp[b]); gadj[grp[b]].add(grp[a])
    place = {g: out[members[g][0]][0] for g in reps if members[g][0] in out}
    byreg = {}
    for reg in ALLOC:
        ws = sorted((r for r in roots if r[1] == reg and keep(r)), key=lambda r: (r[0] >= 0, min(touch[r])))
        ns = [g for g in reps if col0[g] == reg]
        byreg[reg] = (ws, ns)
    for rnd in range(ROUNDS):
        changed = 0
        for reg, (ws, ns) in byreg.items():
            if not ws or not ns: continue
            wr = {w: i / max(1, len(ws) - 1) for i, w in enumerate(ws)}
            cand = []
            for k, g in enumerate(ns):
                gr = k / max(1, len(ns) - 1)
                nb = [place[h] for h in gadj[g] if h in place and col0[h] != reg]
                nbs, nl = set(nb), len(nb)
                lo, hi = int((gr - WINDOW) * len(ws)), int((gr + WINDOW) * len(ws)) + 1
                for w in ws[max(0, lo):hi]:
                    cw = clash.get(w, ())
                    # (the neighbours that clash in the code count 1, the others -0.5)
                    c = len(nbs & cw) if len(nbs) == nl and isinstance(cw, set) else sum(1 for x in nb if x in cw)
                    s = c - 0.5 * (nl - c) - 4 * abs(gr - wr[w])
                    cand.append((s, g, w))
            cand.sort(key=lambda x: -x[0])
            usedg, usedw, new = set(), set(), {}
            for s, g, w in cand:
                if g in usedg or w in usedw: continue
                usedg.add(g); usedw.add(w); new[g] = w
            for g, w in new.items():
                if place.get(g) != w: changed += 1
                place[g] = w
        if not changed: break
    out = {n: [place[g]] for g in place for n in members[g]}
    # check: matched groups that clash in the trace should have webs that clash in the code
    agree = tot = 0
    seen = set()
    for a, b in fin['clash']:
        if a in out and b in out and (grp[a], grp[b]) not in seen:
            seen.add((grp[a], grp[b]))
            tot += 1
            agree += any(y in clash.get(x, ()) for x in out[a] for y in out[b])
    if not quiet: print(f'clash check: {agree} of {tot} traced clashes between placed nodes also clash in the code')
    match.check = (agree, tot)
    return out, cost, nw, len(reps)


def main(argv):
    if not argv or argv[0] not in ('map', 'want'):
        print(__doc__); return 2
    opt = lambda k, d=None: argv[argv.index(k) + 1] if k in argv else d
    if argv[0] == 'map':
        import os
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import armtrace
        T = armtrace.pick(armtrace.parse(argv[1]), int(opt('--index')) if opt('--index') else None)
        data, code, start, end = listing_function(argv[2], argv[3])
        ins = decode(data, code)
        roots, clash, touch, web, IN, eff = webs(ins)
        m, cost, nw, nn = match(T, roots, clash, touch, ins)
        print(f'{len(ins)} instructions, {nw} webs, {nn} node groups: {len(m)} nodes placed, alignment cost {cost:.1f}')
        res = {str(n): dict(reg=ws[0][1], insns=sorted(set().union(*(touch[w] for w in ws))),
                            first=ins[min(min(touch[w]) for w in ws)]['addr'] - start) for n, ws in m.items()}
        with open(opt('--out', 'nodemap.json'), 'w') as f:
            json.dump(dict(start=start, insns=[[x['addr'] - start, x['mn'], x['op']] for x in ins], nodes=res), f)
        return 0
    M = json.load(open(argv[1]))
    byins = collections.defaultdict(list)
    for n, v in M['nodes'].items():
        for k in v['insns']: byins[k].append((int(n), v['reg']))
    want = {}
    hunks = collections.defaultdict(lambda: ([], []))
    for l in open(argv[2]):
        m = re.match(r'^\s+([-+])\s+(\d+)\s+(.*)$', l)
        if m: hunks[int(m.group(2))][0 if m.group(1) == '-' else 1].append(m.group(3).strip())
    R = re.compile(r'\b(r1[0-2]|r\d|sb|sl|fp|ip|lr)\b')
    norm = lambda r: NUM[ALIAS.get(r, r)]
    for i, (orig, build) in hunks.items():
        if len(orig) != len(build): continue
        for j, (o, b) in enumerate(zip(orig, build)):
            if R.sub('R', o) != R.sub('R', b): continue
            for ro, rb in zip(R.findall(o), R.findall(b)):
                if ro == rb: continue
                for n, reg in byins.get(i + j, []):
                    if reg == norm(rb): want[n] = norm(ro)
    with open(opt('--out', 'want.json'), 'w') as f: json.dump({str(k): v for k, v in want.items()}, f)
    print(f'{len(want)} nodes with a register from the original')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
