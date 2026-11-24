"""A distance from armcc's allocator trace, for armpermute2.py's --trace-key.

When a function is down to register ties (its instructions agree with the image's, only some
registers differ), most single edits leave the count where it is: an edit can move a value's
weighted reference count, or its place in the colouring order, towards the image's register
without changing any register yet. The instruction count then gives a search no slope. This
module measures that slope from a light allocator trace of the variant (armtrace.py, Linux
compiler, --profile light, only the function's colouring):

1. The function's instructions come from the scorer's own placement of the object (no fromelf):
   permscore.Target.place_function, decoded with capstone. Their registers are split into webs
   and the webs matched to the trace's nodes (armnodemap.match), with the clashes and moves the
   light trace gives (the registers a node's coloured neighbours took from it, and the move
   neighbours each removal lists). The webs depend only on the code, so they are kept per code.
2. The wanted register of a node: where the scorer's aligned instruction diff pairs two
   instructions that differ only in registers, each register of the build that the image has
   otherwise names the node of the web there, and the image's register is the one wanted.
3. The distance (`order`): for each wanted node whose colour is not the wanted register, the
   neighbours that held the wanted register when it was coloured (the trace's "Marking R
   unavailable due to clash between N and M(R)") were coloured before it. The node would need
   to be coloured before them, that is removed after them. Its term is 1 plus, for each such
   neighbour, its distance in the colouring order divided by GAP_CAP (32), at most 1: a
   neighbour coloured just before counts little, one coloured 32 or more places before counts 1.
   (A first version divided by the number of nodes coloured; on a 5,600-node function every
   change it showed came from nodes added elsewhere, which move all the positions a little.)
   A precoloured neighbour counts 1 (no order moves it). A wanted register that was free but
   not chosen (another vote or the first-free order won) counts 1. Satisfied nodes count 0.
   `refs` is a second measure, printed beside it: per blocking neighbour, how many weighted
   references it has more than the node, in units of 32 (one use of a plain local).

Result: (distance, details) where details names, per unsatisfied node, its register, the one
wanted, its references, and its blockers with their order gap and references.
"""
import collections, hashlib, os, re, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path: sys.path.insert(0, HERE)
import armnodemap, armtrace, permscore

REAL = set(range(15))         # the precoloured nodes: registers 0-12 and 14
GAP_CAP = 32                  # an order gap of this many colourings or more counts as a whole one
REG = re.compile(r'\b(r1[0-2]|r\d|sb|sl|fp|ip|lr)\b')


def light_T(L):
    """(armnodemap-style T with a 'final' graph, {node: move partners}) from a light colouring."""
    col = armtrace.light_colours(L)
    nodes = {n: (c, 'INT32') for n, c in col.items() if n not in REAL and c is not None and c >= 0}
    clash = set()
    moves = collections.defaultdict(set)
    for P in L['passes']:
        for a, ps in P['movenb'].items():
            for b in ps:
                if a not in REAL and b not in REAL: moves[a].add(b); moves[b].add(a)
    P = L['passes'][-1]
    for n, _, _, marks in P['col']:
        for _, nb, k in marks:
            if k == 'c' and nb not in REAL: clash.add((n, nb))
    return {'final': {'nodes': nodes, 'clash': clash}}, moves


def _cs():
    import capstone
    md = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB)
    return md


class Mapper:
    """Per target: the image's rows (once) and, per code, the instructions and webs."""

    def __init__(self, target):
        self.t = target
        img_word = lambda a: struct.unpack('<I', target.img.bytes(a, 4))[0] if target.addr <= a < target.addr + target.want_size else None
        self.exp_rows = permscore.rows_of(target.want, target.addr, target.idata, img_word, target.fn_mask)
        self.webs = {}                    # code hash -> (ins, roots, clash, touch)
        self.md = None
        self.dec = {}
        self.maps = {}                    # (code, light graph) -> node map

    def code(self, raw):
        o = self.t._read(raw)
        return self.t.place_function(o)            # (code, data addresses, at)

    def insns(self, code, data, at):
        h = hashlib.blake2b(code + repr(sorted(data)).encode(), digest_size=12).digest()
        w = self.webs.get(h)
        if w is not None: return w
        isdata = [at + k in data for k in range(len(code))]
        # the zero halfword before a literal is padding, not an instruction
        for k in range(0, len(code) - 3, 2):
            if not isdata[k] and isdata[k + 2] and (at + k) % 4 == 2 and code[k] == 0 and code[k + 1] == 0:
                isdata[k] = isdata[k + 1] = True
        ranges, k = [], 0
        while k < len(code):
            if isdata[k]: k += 1; continue
            e = k
            while e < len(code) and not isdata[e]: e += 1
            ranges.append((k, e)); k = e
        ins = armnodemap.decode(code, ranges)
        roots, clash, touch, _, _, _ = armnodemap.webs(ins)
        w = (ins, roots, clash, touch)
        if len(self.webs) > 64: self.webs.clear()
        self.webs[h] = w
        return w

    def _regs(self, b, a):
        """(op string with registers masked, [registers]) of one instruction's bytes."""
        k = (b, a & 2)
        r = self.dec.get(k)
        if r is None:
            if self.md is None: self.md = _cs()
            i = next(self.md.disasm(b, a), None)
            if i is None: r = (None, [])
            else:
                regs = [armnodemap.NUM[armnodemap.ALIAS.get(x, x)] for x in REG.findall(i.op_str)]
                r = (i.mnemonic + ' ' + REG.sub('R', i.op_str), regs)
            if len(self.dec) > 200000: self.dec.clear()
            self.dec[k] = r
        return r

    def reg_diffs(self, code, data, at):
        """[(offset in the function, register the image has, register the build has)] at the
        instructions the aligned diff pairs that differ only in registers."""
        t = self.t

        def word(a):
            k = a - at
            return struct.unpack_from('<I', code, k)[0] if 0 <= k <= len(code) - 4 else None
        got = permscore.rows_of(code, at, data, word, t.fn_mask)
        _, ops = permscore.diff_count(t.exp, permscore.normalise(got, t.fn_mask))
        out = []
        for tag, i1, i2, j1, j2 in ops:
            if i2 - i1 != j2 - j1: continue
            for i, j in zip(range(i1, i2), range(j1, j2)):
                ea, ga = self.exp_rows[i][0], got[j][0]
                eb = t.want[ea - t.addr: ea - t.addr + 4]
                gb = code[ga - at: ga - at + 4]
                eb = eb[:permscore.thumb_len(eb[0] | eb[1] << 8)] if len(eb) >= 2 else eb
                gb = gb[:permscore.thumb_len(gb[0] | gb[1] << 8)] if len(gb) >= 2 else gb
                (em, er), (gm, gr) = self._regs(bytes(eb), ea), self._regs(bytes(gb), ga)
                if em is None or em != gm or len(er) != len(gr): continue
                for ro, rb in zip(er, gr):
                    if ro != rb: out.append((ga - at, ro, rb))
        return out

    def wanted(self, raw, L):
        """({node: wanted register}, info) of a variant: its object and its light colouring. The
        node map is kept per code and graph (an edit that changes only reference counts or the
        order keeps both)."""
        code, data, at = self.code(raw)
        ins, roots, clash, touch = self.insns(code, data, at)
        T, moves = light_T(L)
        gk = hashlib.blake2b(code + repr((sorted(T['final']['nodes'].items()), sorted(T['final']['clash']),
                                          sorted((a, sorted(b)) for a, b in moves.items()))).encode(), digest_size=16).digest()
        hit = self.maps.get(gk)
        if hit is None:
            hit = armnodemap.match(T, roots, clash, touch, ins, moves=moves, quiet=True) + (getattr(armnodemap.match, 'check', None),)
            if len(self.maps) > 256: self.maps.clear()
            self.maps[gk] = hit
        m, cost, nw, nn, check = hit
        byins = collections.defaultdict(list)
        for n, ws in m.items():
            reg = ws[0][1]
            for w in ws:
                for k in touch[w]: byins[ins[k]['addr']].append((n, reg))
        want, site = {}, {}
        diffs = self.reg_diffs(code, data, at)
        for off, ro, rb in diffs:
            for n, reg in byins.get(off, ()):
                if reg == rb: want[n] = ro; site.setdefault(n, off)
        return want, {'diffs': len(diffs), 'placed': len(m), 'webs': nw, 'groups': nn, 'check': check,
                      'sites': sorted(set(off for off, _, _ in diffs)), 'node_site': site}


def distance(L, want):
    """(order distance, refs distance, [detail per unsatisfied node]) of a light colouring."""
    P = L['passes'][-1]
    pos, rec = {}, {}
    for i, (n, r, c, marks) in enumerate(P['col']):
        pos[n], rec[n] = i, (r, c, marks)
    rep = {}
    for a, b in P['coalesced']: rep[a] = b
    d_order = d_refs = 0.0
    det = []
    for n, R in sorted(want.items()):
        x = n
        for _ in range(64):
            if x in rec or x not in rep: break
            x = rep[x]
        if x not in rec: continue
        r, c, marks = rec[x]
        if c == R: continue
        bl = sorted({nb for reg, nb, k in marks if reg == R})
        term, tr, parts = 1.0, 1.0, []
        for b in bl:
            if b in REAL or b not in pos:
                term += 1.0; tr += 1.0; parts.append((b, None, None)); continue
            gap = pos[x] - pos[b]
            term += min(GAP_CAP, max(0, gap)) / GAP_CAP
            rb = rec[b][0]
            tr += max(0, rb - r + 1) / 32.0
            parts.append((b, gap, rb))
        d_order += term
        d_refs += tr
        det.append((n, x, c, R, r, pos[x], parts))
    return round(d_order, 4), round(d_refs, 4), det


def fmt_detail(det, limit=40):
    out = []
    for n, x, c, R, r, p, parts in det[:limit]:
        bl = ', '.join(f'{b}' + ('(pre)' if g is None else f'(gap {g}, refs {rb})') for b, g, rb in parts) or 'free'
        out.append(f'node {n}' + (f' (as {x})' if x != n else '') + f': r{c} wanted r{R}, refs {r}, colour #{p}; held by {bl}')
    return out


class Tracer:
    """The traced compile and the distance, for one function of one source file (armpermute2's
    --trace-key). The Linux compiler only (the native runner): the local file's [linux.NAME]
    table for that armcc, the light profile, only the function's colouring."""

    def __init__(self, runner, local=None, root=None):
        if getattr(runner, 'name', '') != 'native':
            raise SystemExit('--trace-key needs the native runner (the Linux armcc)')
        self.armcc = os.path.join(runner.home, 'bin', 'armcc')
        self.entry = armtrace.linux_entry(armtrace.find_local(local, root), self.armcc)
        self.env = None
        self.only = None
        self.mapper = None
        self.cache = {}

    def setup(self, target, func, base_raw, compile_fn, work, only=None):
        """Find which colouring is FUNC's (the K-th function of the object, when the trace has one
        colouring per function, else the largest) and make the environment that traces only it.
        compile_fn(extra options, environment) -> (object bytes or None, log)."""
        import orcobj
        self.target = target
        if only is None:
            path = os.path.join(work, 'trace-setup.txt')
            raw, log = compile_fn(['--errors=' + path], armtrace.trace_env(self.entry, 5, 'light'))
            with open(path, errors='replace') as f: text = f.read()
            os.remove(path)
            Ls = armtrace.parse_light(text)
            o, _ = orcobj.read(raw=base_raw)
            names = [n for _, n in sorted(((s.sec, s.value & ~1), s.name) for s in o.symbols.values()
                                          if s.type == 'Code' and isinstance(s.sec, int))]
            if len(Ls) == len(names) and func in names:
                only, how = names.index(func) + 1, f'function {names.index(func) + 1} of {len(names)}'
            else:
                only = 1 + max(range(len(Ls)), key=lambda i: Ls[i]['header'].get('num_nodes', 0))
                how = f'the largest of {len(Ls)} colourings ({len(names)} functions)'
            self.nodes = Ls[only - 1]['header'].get('num_nodes') if Ls else None
            print(f'  trace key: {func} is colouring {only} ({how}, {self.nodes} nodes); {self.entry["name"]} hook', flush=True)
        self.only = int(only)
        self.env = armtrace.trace_env(self.entry, 5, 'light', only=self.only)
        self.mapper = Mapper(target)

    def measure(self, compile_fn, work):
        """(distance, refs distance, details, info) of one source, or None. compile_fn as setup's;
        it compiles the text to measure."""
        path = os.path.join(work, f'trace-{os.getpid()}.txt')
        raw, log = compile_fn(['--errors=' + path], self.env)
        try:
            with open(path, errors='replace') as f: text = f.read()
            os.remove(path)
        except OSError:
            return None
        if raw is None: return None
        key = hashlib.blake2b(text.encode('latin1', 'replace') + hashlib.blake2b(raw[0x34:]).digest(), digest_size=16).digest()
        hit = self.cache.get(key)
        if hit is not None: return hit
        Ls = armtrace.parse_light(text)
        if len(Ls) != 1: return None
        try:
            want, info = self.mapper.wanted(raw, Ls[0])
        except Exception:
            return None
        d, dr, det = distance(Ls[0], want)
        info['wanted'] = len(want)
        res = (d, dr, det, info)
        if len(self.cache) > 4096: self.cache.clear()
        self.cache[key] = res
        return res
