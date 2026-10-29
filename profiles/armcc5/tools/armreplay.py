"""Replay armcc's register allocator on a traced graph: check it, explain a tie, sweep orders.

    python3 armreplay.py [--config PATH] check TRACE [--index N] [--otime | --no-otime] [--src SRC]
    python3 armreplay.py [--config PATH] tie TRACE A B [--index N] [--otime | --no-otime]
    python3 armreplay.py [--config PATH] sweep TRACE --webs WEBS.json (--swap A:B | --want WANT.json)
                         [--vars a,b,c] [--top N] [--jobs N] [--otime | --no-otime]
    python3 armreplay.py [--config PATH] sweep TRACE --webs WEBS.json --order a,b,c [--against TRACE2]

TRACE is a level-5 allocator trace (armtrace.py run). The replay starts from the trace's first
graph, the nodes' initial work lists and their weighted reference counts, and runs armcc 5.06's
simplify and select as compiler.md describes them ("How the register allocator chooses"): four
indexed heaps whose keys are read when they are compared and are not re-sorted when a key
changes, move clashes added at each removal, the strict coalescing test, a second pass that
removes the first pass's spills first, and the vote among copy partners. With no vote a node
takes the first free register in the build's order: r0-r7, ip, r8-r11, lr from a preferred set
(r0-r3, ip and registers already handed out) without -Otime; r0-r3, ip, r4-r11, lr with it.
--otime or --no-otime chooses; otherwise -Otime is read from the project's options (SRC's
`/* opts: */` line with --src, else c_opts), and with no project, the rule that reproduces the
trace's colours is used.

  check  replay every pass and compare it with the trace: each removal, the heaps before it, the
         clash sets and events of each removal, the colour order, each colour and its free
         registers, the spills, and the final graph's colours. A pair the graph dump draws only
         as a clash but the trace's removals list as a move too is a hidden move: check finds
         them and replays again until none is new. 'replay agrees' is the proof that the replay
         can stand for the compiler on this function.
  tie    follow two nodes (two values the image has the other way round) through simplification:
         their list, heap position and degrees after every removal that changes them, which one
         left first, and their keys at that moment. When the one removed first had the larger key,
         the heap's stale order decided it, and no edit of their own statements will move it.
  sweep  declaration order only renumbers the variables' homes (compiler.md). WEBS.json names
         each variable's home nodes in the traced source's declaration order:
             {"vars": [["i", [431, 432]], ["n", [433]], ["v", [434, 435]]]}
         sweep renumbers the homes for every order of --vars (default all), replays each, and
         ranks the orders by how many nodes' registers differ from the target: --swap A:B is the
         trace's own colours with nodes A and B exchanged, --want a JSON object {node: register}
         in the traced numbering. --order scores one order and prints its colours; with --against
         TRACE2 (a real trace of the source in that order) it compares node for node, the check to
         make before trusting a sweep. A score of 0 is a candidate to build, never a match.

Registers are numbered 0-12 and 14 (12 is ip, 14 lr); -5 is a spill.
"""
import collections, itertools, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, HERE)
import armtrace

K = 14
XL, LO, HI, HNS, SIMP, COAL, PRE, UNINIT = 3, 2, 5, 6, 7, 8, 1, 0
SN = {'NS_ExtraLowNode': XL, 'NS_LowNode': LO, 'NS_HighNode': HI, 'NS_HighNonSpill': HNS,
      'NS_PreColoured': PRE, 'NS_Uninitialised': UNINIT, 'NS_SimplifiedNode': SIMP, 'NS_CoalescedNode': COAL}
NS = {v: k for k, v in SN.items()}
LISTS = {'extralownodes': XL, 'lownodes': LO, 'highnodes': HI, 'highnonspills': HNS}
REGS = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 14]
ORDER = [0, 1, 2, 3, 4, 5, 6, 7, 12, 8, 9, 10, 11, 14]          # without -Otime
ORDER_OTIME = [0, 1, 2, 3, 12, 4, 5, 6, 7, 8, 9, 10, 11, 14]    # with -Otime
PREFERRED = (0, 1, 2, 3, 12)
SPILL = -5
NULL = 0xF00000
RNAME = {12: 'ip', 14: 'lr', SPILL: 'spill'}


def rname(c):
    return RNAME.get(c, f'r{c}' if isinstance(c, int) and 0 <= c <= 11 else str(c))


def real(c):
    return c is not None and 0 <= c <= 14


# ---------------------------------------------------------------- the work lists


class Heap:
    """An indexed binary heap as the allocator keeps it: keys are read live by cmp, so a key
    that changes without a sift leaves the array out of order, and later pops follow the
    positions left behind."""

    def __init__(self, cmp, pos):
        self.a, self.cmp, self.pos = [], cmp, pos

    def _swap(self, i, j):
        a = self.a
        a[i], a[j] = a[j], a[i]
        self.pos[a[i]] = i; self.pos[a[j]] = j

    def up(self, i):
        while i:
            p = (i - 1) // 2
            if self.cmp(self.a[p], self.a[i]) < 0: break
            self._swap(p, i); i = p
        return i

    def down(self, i):
        a, n = self.a, len(self.a)
        while 2 * i + 1 < n:
            c = 2 * i + 1
            if c + 1 < n and self.cmp(a[c + 1], a[c]) < 0: c += 1
            if self.cmp(a[i], a[c]) < 0: break
            self._swap(i, c); i = c
        return i

    def push(self, v):
        self.a.append(v); self.pos[v] = len(self.a) - 1
        self.up(len(self.a) - 1)

    def remove(self, v):
        i = self.pos.pop(v)
        last = self.a.pop()
        if i < len(self.a):
            self.a[i] = last; self.pos[last] = i
            self.down(i); self.up(self.pos[last])

    def pop(self):
        v = self.a[0]
        del self.pos[v]
        last = self.a.pop()
        if self.a:
            self.a[0] = last; self.pos[last] = 0
            self.down(0)
        return v

    def marks(self):
        """[(node, stale)] as the trace prints a list: stale when a child sorts before it."""
        a, n = self.a, len(self.a)
        return [(v, (2 * i + 1 < n and self.cmp(a[2 * i + 1], v) < 0) or
                 (2 * i + 2 < n and self.cmp(a[2 * i + 2], v) < 0)) for i, v in enumerate(a)]


class State:
    """One simplify pass. C: clashes, X: move clashes, M: moves (copy partners)."""

    def __init__(self, G, move_clashes=True):
        nodes = G['nodes']
        self.C = {n: set(G['C'].get(n, ())) for n in nodes}
        self.X = {n: set() for n in nodes}
        self.M = {n: set(G['M'].get(n, ())) for n in nodes}
        self.direct, self.uncoal, self.refs = G['direct'], G['uncoal'], G['refs']
        self.nonspill, self.second = G['nonspill'], move_clashes
        self.status, self.pos, self.alias, self.stack, self.snap, self.log = {}, {}, {}, [], {}, []
        refs, sec, C, X, M = self.refs, self.second, self.C, self.X, self.M
        deg = lambda n: len(C[n])
        deg2 = lambda n: len(C[n]) + len(X[n])

        def keys(a, b, *fs):
            for f in fs:
                r = f(a) - f(b)
                if r: return r
            return b - a                      # on a tie of every key, the higher number first

        def cmp_low(a, b):
            fs = [refs.get, lambda n: len(M[n])] + ([deg2] if sec else []) + [deg]
            return keys(a, b, *fs)

        def cmp_high(a, b):
            fs = [refs.get, lambda n: len(M[n])] + ([lambda n: -deg2(n)] if sec else []) + [lambda n: -deg(n)]
            return keys(a, b, *fs)

        def cmp_hns(a, b):
            fs = [deg, refs.get, lambda n: len(M[n])] + ([deg2] if sec else [])
            return keys(a, b, *fs)

        self.H = {XL: Heap(cmp_low, self.pos), LO: Heap(cmp_low, self.pos), HI: Heap(cmp_high, self.pos),
                  HNS: Heap(cmp_hns, self.pos)}
        for n in sorted(nodes):
            s = G['init'][n]
            if s in self.H: s = self.calc_status(n)
            self.status[n] = s
            if s in self.H: self.H[s].push(n)

    def calc_status(self, n):
        d = len(self.C[n])
        if self.second:
            if d + len(self.X[n]) < K: return XL
        elif d < K:
            return XL
        if d < K: return LO
        return HNS if n in self.nonspill else HI

    def update(self, n):
        s = self.status.get(n, UNINIT)
        if s in (SIMP, COAL, UNINIT, PRE): return
        t = self.calc_status(n)
        if t != s:
            self.log.append(('chg', n, NS[s], NS[t]))
            self.H[s].remove(n); self.status[n] = t; self.H[t].push(n)

    def add_x(self, a, b):
        if a == b or b in self.C[a] or self.status.get(a) == UNINIT or self.status.get(b) == UNINIT: return
        self.X[a].add(b)

    def simplify(self, v, second=True):
        """Remove v from the graph. second=False is the plain removal of a pass-1 spill at the
        start of pass 2: clash neighbours are updated, no move clashes are made."""
        C, X, M = self.C, self.X, self.M
        self.snap[v] = (frozenset(C[v]), frozenset(X[v]))
        for w in C[v]: C[w].discard(v)
        if second:
            for w in X[v]: X[w].discard(v)
        self.status[v] = SIMP
        if not second:
            for w in sorted(C[v], reverse=True): self.update(w)
        else:
            if len(C[v]) * len(M[v]) < 1000:
                for m in sorted(M[v], reverse=True):
                    if self.status.get(m) in (SIMP, COAL): continue
                    if X[v] & M[m]: self.log.append(('int', 'move', m))
                    elif C[v] & M[m]: self.log.append(('int', 'clashes', m))
                    else:
                        self.log.append(('amc', m))
                        for e in sorted(C[v], reverse=True): self.add_x(m, e); self.add_x(e, m)
                        for e in sorted(X[v], reverse=True): self.add_x(m, e); self.add_x(e, m)
                self.log.append(('mkrel',))
                for m in [m for m in M[v] if self.status.get(m) not in (SIMP, COAL)]:
                    for p in M[v]:
                        if p != m: M[m].add(p)
            for w in sorted(X[v], reverse=True): self.update(w)
            for w in sorted(C[v], reverse=True): self.update(w)
            for w in sorted(M[v], reverse=True): self.update(w)
        if not self.coalesce(v): self.stack.append(v)

    def coalesce(self, v):
        """Merge v into its one direct copy partner when the partner is (extra) low and every
        clash of v is already a clash of the partner."""
        cand = [m for m in sorted(self.M[v], reverse=True)
                if frozenset((v, m)) in self.direct and self.status.get(m) != COAL]
        if not cand:
            self.log.append(('coal', "can't coalesce - has no direct moves")); return False
        if len(cand) > 1:
            self.log.append(('coal', "can't coalesce - has more than 1 move")); return False
        p = cand[0]
        s = self.status.get(p)
        if s not in (LO, XL):
            self.log.append(('coal', "can't coalesce with %d - wrong status %s" % (p, NS[s]))); return False
        if frozenset((v, p)) in self.uncoal:
            self.log.append(('coal', "can't coalesce with %d - controller says uncoalesceable" % p)); return False
        if self.C[v] <= self.C[p]:
            self.log.append(('coal', 'coalescing with %d' % p))
            self.status[v] = COAL; self.alias[v] = p
            return True
        self.log.append(('coal', "can't coalesce with %d - %d's clashes aren't a subset of %d's" % (p, v, p)))
        return False

    def pick(self):
        for s in (XL, LO, HI, HNS):
            if self.H[s].a: return self.H[s].pop()
        return None

    def run(self):
        while True:
            v = self.pick()
            if v is None: return self
            self.simplify(v)


# ---------------------------------------------------------------- the graph from a trace


def graph(T, hidden=()):
    """The allocator's input from a parsed colouring: nodes, clashes, moves, direct copies,
    weighted references, non-spill nodes, pairs the trace says cannot coalesce, initial lists."""
    g0, P1 = T['g0'], T['passes'][0]
    init = {n: SN[v[2]] for n, v in P1['init'].items()}
    nodes = [n for n, s in init.items() if s in (XL, LO, HI, HNS, PRE)]
    ns = set(nodes)
    C, M = collections.defaultdict(set), collections.defaultdict(set)
    for a, b in g0['clash']:
        if a in ns and b in ns: C[a].add(b); C[b].add(a)
    for (a, b) in g0['move']:
        if a in ns and b in ns: M[a].add(b); M[b].add(a)
    for a, b in hidden:
        M[a].add(b); M[b].add(a)
    direct = {frozenset(k) for k, w in g0['move'].items() if w == 5} | {frozenset(k) for k in hidden}
    refs = {}
    for P in T['passes']:
        for s in P['steps']: refs[s['n']] = s['refs']
        for c in P['col']: refs.setdefault(c['n'], c['refs'])
    for n in nodes: refs.setdefault(n, 0)
    uncoal = set()
    for P in T['passes']:
        for s in P['steps']:
            for t in s['coal'] or []:
                if 'uncoalesceable' in t: uncoal.add(frozenset((s['n'], int(t.split()[3]))))
    nonspill = {n for n, s in init.items() if s == HNS}
    return dict(nodes=nodes, C=C, M=M, direct=direct, refs=refs, nonspill=nonspill, uncoal=uncoal, init=init)


def nullset(T, passno=1):
    """Nodes the trace could not give a register because their variable's home already has one."""
    return frozenset(c['n'] for c in T['passes'][passno - 1]['col'] if c.get('colour') == NULL)


def move_clashes(T):
    return bool(T['header'].get('second_clash_set', 1))


# ---------------------------------------------------------------- select


def colour(st, otime, nulls=frozenset()):
    """Colour the simplify stack in reverse. Returns ({node: register}, {node: (free, free
    after move clashes, how)}) with coalesced nodes given their partner's register."""
    col = {n: n for n, s in st.status.items() if s == PRE}
    order = ORDER_OTIME if otime else ORDER
    handed, rec, alias = set(), {}, st.alias

    def get(w):
        while w in alias: w = alias[w]
        return col.get(w)

    for v in reversed(st.stack):
        Cv, Xv = st.snap[v]
        cc = {get(w) for w in Cv}
        A = [r for r in REGS if r not in cc]
        xc = {get(w) for w in Xv}
        B = [r for r in A if r not in xc]
        unc = sum(1 for w in Cv if not real(get(w)))

        def vote(S):
            cnt, best, bc = {}, None, 0
            for m in sorted(st.M[v], reverse=True):
                if m in alias: continue
                c = col.get(m)
                if real(c) and c in S:
                    k = cnt.get(c, 0) + 1 + (frozenset((m, v)) in st.direct)
                    cnt[c] = k
                    if k > bc or (k == bc and c < best): best, bc = c, k
            return best

        def choose(S):
            if v in nulls or not S: return None
            if not otime:
                pref = [r for r in S if r in PREFERRED or r in handed]
                if pref and unc < len(pref):
                    c = next(r for r in order if r in pref); handed.add(c); return c
            c = next(r for r in order if r in S); handed.add(c); return c

        if not A:
            c, how = SPILL, 'spill'
        else:
            c, how = vote(B), 'vote'
            if c is None: c, how = choose(B), 'first'
            if c is None: c, how = vote(A), 'vote-all'
            if c is None: c, how = choose(A), 'first-all'
            if c is None: c, how = NULL, 'null'
        col[v] = c; rec[v] = (A, B, how)
    for v in st.alias:
        col[v] = get(v)
    return col, rec


def replay(T, otime, G=None, hidden=(), passes=None, rel=None):
    """Replay every pass as the trace's colouring would run them. Returns a list of
    (state, colours, records), one per pass: pass 2 runs when pass 1 spilled. rel renumbers
    the trace's own node numbers when G is a renumbered graph."""
    G = G or graph(T, hidden)
    sec = move_clashes(T)
    f = (lambda ns: frozenset(rel.get(n, n) for n in ns)) if rel else (lambda ns: ns)
    st = State(G, sec).run()
    col, rec = colour(st, otime, f(nullset(T, 1)))
    out = [(st, col, rec)]
    spills = [v for v in reversed(st.stack) if col.get(v) == SPILL]
    if spills and (passes is None or passes > 1):
        st2 = State(G, sec)
        for v in reversed(spills):
            st2.H[st2.status[v]].remove(v)
            st2.simplify(v, False)
        st2.run()
        col2, rec2 = colour(st2, otime, f(nullset(T, 2)) if len(T['passes']) > 1 else frozenset())
        out.append((st2, col2, rec2))
    return out


def final_colours(results):
    return results[-1][1]


# ---------------------------------------------------------------- check against the trace


def _lists_diff(st, L, tag):
    errs = []
    for k, s in LISTS.items():
        if k not in L: continue
        got = st.H[s].marks()
        if got != L[k]:
            i = next((j for j in range(min(len(got), len(L[k]))) if got[j] != L[k][j]), min(len(got), len(L[k])))
            errs.append(f'{tag} {k} differs at {i}: replay {got[i:i + 3]} trace {L[k][i:i + 3]}')
    return errs


def check_pass(T, passno, G, spills_first=(), maxerr=5):
    """Replay one simplify pass following the trace and record every difference. When the
    replay picks another node, the trace's pick is taken so the rest can still be checked.
    Returns (state, {kind: [differences]}, hidden moves found)."""
    P = T['passes'][passno - 1]
    S = P['steps']
    st = State(G, move_clashes(T))
    C0 = {n: set(G['C'][n]) for n in G['nodes']}
    err, hid = collections.defaultdict(list), set()

    def sets(i, v, s):
        if 'before' not in s: return
        for k, mine in (('Clashes', st.C[v]), ('Extra', st.X[v]), ('Moves', st.M[v])):
            tr = {m for m, _ in s['before'].get(k, ())}
            if tr != mine:
                err['sets'].append((i, v, k, 'replay only', sorted(mine - tr)[:8], 'trace only', sorted(tr - mine)[:8]))
                if k == 'Moves':
                    hid.update((v, p) for p in tr - mine if p in C0[v])
                    st.M[v] = set(tr)
                if k == 'Extra': st.X[v] = set(tr)

    i = 0
    for v in spills_first:
        s = S[i]
        if not (s['pre'] and s['n'] == v):
            err['pick'].append((i, 'replay pre-spill', v, 'trace', s['n'])); return st, err, hid
        st.H[st.status[v]].remove(v)
        sets(i, v, s)
        st.log = []
        st.simplify(v, False)
        i += 1
    while i < len(S):
        s = S[i]
        v = st.pick()
        if v != s['n']:
            err['pick'].append((i, 'replay', v, NS.get(st.status.get(v)), 'trace', s['n'], s['status']))
            if len(err['pick']) >= maxerr: break
            if v is not None: st.H[st.status[v]].push(v)
            st.H[st.status[s['n']]].remove(s['n'])
            v = s['n']
        # the trace prints the lists last after taking the node off its heap
        if s['lists'] is not None and len(err['lists']) < maxerr:
            err['lists'] += _lists_diff(st, s['lists'], f'removal {i}')
        sets(i, v, s)
        st.log = []
        st.simplify(v, True)
        tev = list(s['ev']) + [('coal', t) for t in (s['coal'] or [])[:1]]
        if st.log != tev and len(err['events']) < maxerr:
            j = next((j for j in range(min(len(st.log), len(tev))) if st.log[j] != tev[j]), min(len(st.log), len(tev)))
            err['events'].append((i, v, 'replay', st.log[j:j + 3], 'trace', tev[j:j + 3]))
        i += 1
    return st, err, hid


def check_colours(T, passno, st, otime):
    P = T['passes'][passno - 1]
    col, rec = colour(st, otime, nullset(T, passno))
    order_ok = [c['n'] for c in P['col']] == list(reversed(st.stack))
    bad = []
    for c in P['col']:
        n = c['n']
        A, B, how = rec.get(n, (None, None, None))
        if col.get(n) != c.get('colour') or ('avail' in c and A != c['avail']) or \
                ('avail_mc' in c and B != c['avail_mc']):
            bad.append((n, 'replay', rname(col.get(n)), how, 'trace', rname(c.get('colour'))))
    return col, order_ok, bad


def check(T, otime, maxerr=5, rounds=6):
    """Check the replay against the trace, finding hidden moves until none is new.
    Returns (report lines, ok)."""
    hidden = set()
    for _ in range(rounds):
        G = graph(T, hidden)
        st, err, hid = check_pass(T, 1, G, maxerr=maxerr)
        if not hid - hidden: break
        hidden |= hid
    lines = [f'hidden moves: {len(hidden)}']
    ok = True
    col, order_ok, bad = check_colours(T, 1, st, otime)
    states = [(st, err, col, order_ok, bad)]
    if len(T['passes']) > 1:
        spills = [x[0] for x in T['passes'][0]['spills']]
        st2, err2, _ = check_pass(T, 2, G, list(reversed(spills)), maxerr)
        col2, order2, bad2 = check_colours(T, 2, st2, otime)
        states.append((st2, err2, col2, order2, bad2))
        mine = [v for v in reversed(st.stack) if col.get(v) == SPILL]
        if mine != spills:
            ok = False
            lines.append(f'pass 1 spills: replay {mine[:8]} trace {spills[:8]}')
    for k, (s, e, c, o, b) in enumerate(states):
        P = T['passes'][k]
        lines.append(f'pass {k + 1}: {len(P["steps"])} removals, {sum(len(v) for v in e.values())} differences; '
                     f'colour order {"same" if o else "differs"}; {len(P["col"])} coloured, {len(b)} differ')
        for kind, v in e.items():
            if v: ok = False
            for x in v[:maxerr]: lines.append(f'  {kind}: {x}')
        if not o or b: ok = False
        for x in b[:maxerr]: lines.append(f'  colour: {x}')
    final = T.get('final')
    if final:
        fc = states[-1][2]
        fbad = [(n, rname(fc.get(n)), rname(c)) for n, (c, t) in final['nodes'].items() if t != 'REAL' and fc.get(n) != c]
        lines.append(f'final graph: {len(final["nodes"])} nodes, {len(fbad)} colours differ {fbad[:5]}')
        if fbad: ok = False
    lines.append('replay agrees with the trace' if ok else 'replay DIFFERS from the trace')
    return lines, ok, hidden


def infer_otime(T):
    """The colour rule that reproduces the trace's colours (True: -Otime), or None if neither."""
    G = graph(T)
    st = State(G, move_clashes(T)).run()
    want = {c['n']: c.get('colour') for c in T['passes'][0]['col']}
    score = {}
    for otime in (True, False):
        col, _ = colour(st, otime, nullset(T))
        score[otime] = sum(col.get(n) != c for n, c in want.items())
    best = min(score, key=score.get)
    return best if score[best] < score[not best] or score[best] == 0 else None


# ---------------------------------------------------------------- following a tie


def tie(T, a, b, otime, hidden=()):
    """Follow nodes a and b through pass 1's simplification. Returns report lines."""
    G = graph(T, hidden)
    st = State(G, move_clashes(T))
    out, prev, step = [], None, 0

    def where(v):
        s = st.status.get(v)
        return (NS.get(s, str(s))[3:], st.pos.get(v), len(st.C[v]), len(st.C[v]) + len(st.X[v]), len(st.M[v]))

    first = None
    while True:
        cur = (where(a), where(b))
        if cur != prev:
            out.append(f'after {step:5d} removals: ' + '; '.join(
                f'{n}: {w[0]} pos {w[1]} degree {w[2]} with move clashes {w[3]} moves {w[4]}' for n, w in zip((a, b), cur)))
            prev = cur
        v = st.pick()
        if v is None: break
        if v in (a, b) and first is None:
            first = v
            other = b if v == a else a
            h = st.H.get(st.status.get(other))
            if h is not None and st.status.get(other) == st.status.get(v):
                c = h.cmp(other, v)
                if c < 0:
                    why = f'stale heap order: {other} sorts first but sits at position {st.pos.get(other)}'
                elif c == v - other:
                    why = 'every key ties, and the higher node number leaves first'
                else:
                    why = 'the keys decide it'
                out.append(f'removal {step}: {v} leaves first; {why}')
                for n in (v, other):
                    out.append(f'  {n}: refs {st.refs.get(n)} moves {len(st.M[n])} degree {len(st.C[n])} '
                               f'with move clashes {len(st.C[n]) + len(st.X[n])}')
            else:
                out.append(f'removal {step}: {v} leaves first from {NS.get(st.status.get(v))}; '
                           f'{other} is {NS.get(st.status.get(other))}, so the lists decide it')
        elif v in (a, b):
            out.append(f'removal {step}: {v} leaves')
        st.simplify(v); step += 1
    col, _ = colour(st, otime, nullset(T))
    out.append(f'colours: {a} {rname(col.get(a))}, {b} {rname(col.get(b))} '
               f'(the one removed first is coloured last)')
    return out


# ---------------------------------------------------------------- declaration orders


def load_webs(path):
    with open(path) as f: w = json.load(f)
    v = w['vars'] if isinstance(w, dict) else w
    return [(name, [int(x) for x in nodes]) for name, nodes in v]


def order_relabel(webs, order):
    """{old node: new node} for the declarations in `order`: homes are numbered consecutively,
    in declaration order, from the lowest home."""
    homes = dict(webs)
    nxt = min(h for _, hs in webs for h in hs)
    rel = {}
    for var in order:
        for h in homes[var]: rel[h] = nxt; nxt += 1
    return rel


def relabel_graph(G, rel):
    f = lambda x: rel.get(x, x)
    return dict(nodes=sorted(f(x) for x in G['nodes']),
                C={f(a): {f(b) for b in s} for a, s in G['C'].items()},
                M={f(a): {f(b) for b in s} for a, s in G['M'].items()},
                direct={frozenset(f(x) for x in p) for p in G['direct']},
                refs={f(a): r for a, r in G['refs'].items()},
                nonspill={f(x) for x in G['nonspill']},
                uncoal={frozenset(f(x) for x in p) for p in G['uncoal']},
                init={f(a): s for a, s in G['init'].items()})


def orders(webs, vars_=None):
    """Every declaration order that permutes `vars_` among the places they hold."""
    names = [n for n, _ in webs]
    vs = vars_ or names
    slots = [i for i, n in enumerate(names) if n in vs]
    for perm in itertools.permutations([names[i] for i in slots]):
        o = list(names)
        for i, n in zip(slots, perm): o[i] = n
        yield tuple(o)


def predict(T, G, webs, order, otime):
    """Colours, in the traced numbering, of the source with its declarations in `order`."""
    rel = order_relabel(webs, order)
    col = final_colours(replay(T, otime, relabel_graph(G, rel), rel=rel))
    inv = {v: k for k, v in rel.items()}
    return {inv.get(k, k): c for k, c in col.items()}, rel


def score(col, want):
    return [n for n, r in want.items() if col.get(n) != r]


_W = {}


def _init(T, G, webs, want, otime):
    _W.update(T=T, G=G, webs=webs, want=want, otime=otime)


def _one(order):
    col, _ = predict(_W['T'], _W['G'], _W['webs'], order, _W['otime'])
    return len(score(col, _W['want'])), order


def sweep(T, webs, want, otime, vars_=None, jobs=1, hidden=()):
    G = graph(T, hidden)
    todo = list(orders(webs, vars_))
    if jobs > 1 and len(todo) > 1:
        import multiprocessing as mp
        with mp.get_context('fork').Pool(jobs, _init, (T, G, webs, want, otime)) as p:
            res = p.map(_one, todo, chunksize=max(1, len(todo) // (jobs * 8)))
    else:
        _init(T, G, webs, want, otime)
        res = [_one(o) for o in todo]
    return sorted(res)


# ---------------------------------------------------------------- command line


def _rule(a, cfg_path):
    if '--otime' in a: return True
    if '--no-otime' in a: return False
    try:
        sys.path.insert(0, os.path.join(EXHUME, 'tools'))
        import config
        cfg = config.load(config.find(cfg_path))
        opts = cfg.directives(a[a.index('--src') + 1])[1] if '--src' in a else cfg.c_opts
        return '-Otime' in (opts or '').split()
    except SystemExit:
        return None


def main(argv):
    sys.path.insert(0, os.path.join(EXHUME, 'tools'))
    import config
    path, a = config.pop_config(argv)
    if not a or a[0] not in ('check', 'tie', 'sweep'):
        print(__doc__); return 2
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    T = armtrace.pick(armtrace.parse(a[1]), int(opt('--index')) if '--index' in a else None)
    otime = _rule(a, path)
    if otime is None:
        otime = infer_otime(T)
        print(f'-Otime rule from the trace: {otime}' if otime is not None else
              'neither colour rule reproduces the trace; using the -Otime rule')
        otime = True if otime is None else otime
    if a[0] == 'check':
        lines, ok, _ = check(T, otime, int(opt('--max', 5)))
        print('\n'.join(lines))
        return 0 if ok else 1
    hidden = check(T, otime)[2]
    if a[0] == 'tie':
        print('\n'.join(tie(T, int(a[2]), int(a[3]), otime, hidden)))
        return 0
    webs = load_webs(opt('--webs'))
    G = graph(T, hidden)
    base = final_colours(replay(T, otime, G))
    if '--order' in a:
        order = opt('--order').split(',')
        col, rel = predict(T, G, webs, order, otime)
        if '--against' in a:
            T2 = armtrace.pick(armtrace.parse(opt('--against')), int(opt('--index2')) if '--index2' in a else None)
            got = {c['n']: c.get('colour') for P in T2['passes'][-1:] for c in P['col']}
            if T2.get('final'): got.update({n: c for n, (c, t) in T2['final']['nodes'].items() if t != 'REAL'})
            mine = {rel.get(n, n): c for n, c in col.items() if n > 14}
            bad = [(n, rname(mine.get(n)), rname(r)) for n, r in got.items() if n > 14 and mine.get(n) != r]
            print(f'{len(got)} nodes in the second trace, {len(bad)} differ from the prediction {bad[:8]}')
            return 0 if not bad else 1
        changed = sorted((n, rname(base.get(n)), rname(c)) for n, c in col.items() if n > 14 and base.get(n) != c)
        print(f'{len(changed)} nodes change register against the traced order:')
        for x in changed[:40]: print('  ', x)
        return 0
    if '--swap' in a:
        x, y = (int(s) for s in opt('--swap').split(':'))
        want = {n: c for n, c in base.items() if n > 14}
        want[x], want[y] = base.get(y), base.get(x)
    else:
        with open(opt('--want')) as f: want = {int(k): v for k, v in json.load(f).items()}
    vars_ = opt('--vars').split(',') if '--vars' in a else None
    jobs = int(opt('--jobs', max(1, min(8, (os.cpu_count() or 2) - 2))))
    res = sweep(T, webs, want, otime, vars_, jobs, hidden)
    top = int(opt('--top', 10))
    hist = collections.Counter(s for s, _ in res)
    print(f'{len(res)} orders; best {res[0][0]}; at 0: {hist.get(0, 0)}')
    for s, o in res[:top]: print(f'  {s:4d}  {",".join(o)}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
