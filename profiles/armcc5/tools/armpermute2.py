"""Fast source permuter for one function: island search over catalogue rewrite rules, with
variants made, compiled and scored in a worker pool.

    python3 armpermute2.py [--config PATH] SRC.c FUNC [--runner native|wine] [--jobs N]
        [--islands K] [--iters N] [--time SECONDS] [--seed S] [--resume] [--out DIR]
        [--weights FILE.json] [--only RULE,...] [--skip RULE,...] [--cut auto|on|off]
        [--armcc-home DIR] [--mappings DIR] [--exec CMD] [--no-screen] [--pair P] [--everywhere P]
        [--dead-penalty N] [--move-penalty N] [--rebase MINUTES] [--cross P] [--simplify SHARE]
        [--no-reduce] [--no-focus] [--focus-cold P] [--focus-near LINES]
        [--no-sweep] [--sweep-max N] [--sweep-share F] [--pair-share F] [--pairs M] [--pair-removals R]
        [--beam B] [--beam-share F] [--sweep-only]
        [--origin SHARE] [--flat-score] [--seed-source FILE]... [--seed-dir DIR] [--seed-factor F]
        [--debt-bases N] [--debt-share F] [--resweep LEVELS]

First a sweep, so that an answer of one or two edits does not hang on the seed: every single
edit of the original source is listed (for the probe rules by enumeration: each insertion
point with each operand near it against 0, the nearest constants, the function's shared
constants, the nearest operand; for the other rules by draws with fixed seeds until 60 in a
row give nothing new; at most --sweep-max per rule, 3000), compiled and scored; then the
--pairs (40) best singles that changed the object, one per distinct object, are applied two at
a time; then a beam: the --beam (10) best distinct pairs each get one of the best 60 singles as
a third edit, the best of those a fourth, up to six, while a level betters the one before. With
--time the singles get --sweep-share of it (0.25), the pairs --pair-share (0.15) and the beam
--beam-share (0.15),
probes in tiers so that a limit thins every part of the function alike. The per-rule results
are printed and every result is in OUT/sweep.tsv. After the first tiers, the probe families
(a shift probe's constant, a comparison's operand) of which a member improved the score are
tried first. Each removal of something the original has (a probe, its dead constructs, a
normalised form) is also paired with the best --pair-removals (300) singles, since a removal
alone often scores no better. The best result becomes the best and the
even islands start from the best distinct results; the odd islands start on the original, and
--origin (0.15) of all later jobs are made from the original too (two edits at once, the
singles being done). --no-sweep skips the stage; --sweep-only stops after it.

What it does, per variant: the parent picks an island (an independent chain with its own base
source) and hands a worker a job: the base's name, a seed, the island's key. The worker parses
the base (once per base, kept by its hash), picks a rule by weight (permrules.RULES: the edit
kinds that produced matches, both ways) and applies it once, at every site of what it chose
(--everywhere probability), or two rules at distant sites as one step (--pair probability);
drops the text if its normalised form (tokens without whitespace) is in the set of texts
already made, which all workers share; compiles it (permrun: one armcc process per variant,
work files in /dev/shm) and scores FUNC (permscore: object read in-process, FUNC placed at its
map address; 0 only for equal bytes). It sends back the score, the size, the trail and the
text's hash, and the text with its moves only when the island or the best may keep it. The
island keeps a variant that is no worse, now and then one a little worse (--temp), and goes
back to the best after --restart rounds without progress. Order of goodness: exact; right size
and unchanged neighbours, by differing instructions; then wrong size, by size difference and
instructions. So a lower count at the wrong size is never progress.

The parent does no parsing in the loop: it costs a fraction of a millisecond per variant
(printed at the end, with the workers' generation time), so the workers are never short of jobs.

Bases without dead code: a source matched with probes can be a dead end, since the probes fit
the code around them. So each dead construct of the given source (a probe, a comma probe in a
condition, a dead or repeated store) is removed, one at a time, each kind together and all
together; every removal that compiles and changes FUNC's code is a base with its own sweep
(singles, pairs, beam), however much worse it scores at first (--debt-bases, 16; they share
--debt-share of --time, 0.3). When a sweep's best has half its start's count or less, the
sweep is made again from that best (--resweep levels, 1). The rules that send several sites
through one helper or local (index_helper, setter, getter, addr_helper, expr_helper,
local_cache, cache_group, arg_local, uniform) are also listed at every subset of their sites
(all subsets up to 8 sites; of more, the runs of neighbours and a sample).

Other starts: each --seed-source FILE is another complete text of the same source file (the
function as a sibling release has it, an older commit, a hand variant). It is compiled and
scored at the start, and kept when its count is within --seed-factor (2) times the best start's
plus 5. --seed-dir DIR takes every *.c of a folder; without it, <tree>/variants/FUNC is looked
for beside exhume.toml. A run leaves OUT/variants/: the best text of each distinct residue (set
of structural differences left), as candidates for that folder. A kept one gets an equal part of the sweep's time and its own islands (the islands go
to the starts in turn); every result that descends from it has a first move `seed` that names
the file, in best.txt and in the logs.

The key (trust): a result is ordered by exact; then right size with unchanged neighbours; then
its structural difference (differing instructions with the registers masked: an instruction
the image does not have, lacks or has elsewhere); then the rest of the differing instructions
(register ties), with the penalties below added. So no number of register ties pays for a wrong
instruction. Scores print as `30 (structure 4, registers 26)`; best.txt lists the structural
differences. --flat-score gives the old single count.

Parsimony: each move in a variant's list costs --move-penalty differing instructions in the
acceptance key (default 0.15), so of two sources with the same code the shorter list wins and a
long chain of neutral edits is not progress; and an island that has given no new best for
--rebase minutes (default 10, 0 for never) starts again from the best as reduced.

Focus: after each improvement of an island, the source lines its differing instructions come
from are worked out (one more compile of that text with -g; the line rows of .debug_line are
read in-process, and used only when FUNC's code is the same with -g as without: this compiler
build refuses -S, --asm and --interleave). An edit that touches a line within --focus-near
lines (3) of such a line is always kept; any other is kept with probability --focus-cold
(0.25). A base whose function has the wrong size has no hot lines (the diff is of shifted code). Edits that are global by nature are exempt: compound moves, edits outside FUNC's body
and edits to a local's declaration. --no-focus turns it off.

A new best is screened before it is kept (permscreen: clang, no new unsequenced or
uninitialised warnings) and written to OUT/best.c with its edit trail; a score of 0 stops the
run. Every attempt is logged to OUT/attempts.tsv (island, rules, sites, result, size); the end
of a run prints per-rule statistics (tried, compiled, improved, accepted) and the throughput.
State (island bases, best, seen hashes, rule statistics) is kept in OUT/state.json for --resume;
the end report lists the time and compile count of each new best, and OUT/yield.json and
OUT/weights.json hold what each rule gave per compile and weights for a later run's --weights;
every new best also goes to OUT/best/<time>_<score>.c with its edit list, and the lowest count
seen, however long its list, to OUT/lowest.c. Long runs escalate by
themselves when most generated texts are duplicates (deeper compound moves, a new seed and rule
mix, half the islands back to the original). --dead-penalty N counts each dead-code construct
added (stored comparison, unused local, pointer store) as N more differing instructions, and
the end of a run prints the Pareto front of score against dead constructs; --tabu N skips a
(rule, site) move after it has left the code unchanged N times, and rule weights are scaled by
how often each rule changes the code at all.

Cut mode (--cut auto): compile less than the whole unit when a start-up check shows FUNC's
code is the same. Forms tried: 'prefix' (the file up to the end of FUNC; armcc parses the whole file
before it generates any code, so code generation for the later functions is saved; invalid
when FUNC calls a function defined later, because armcc uses a same-file callee's register
usage), 'stub' (other function bodies replaced by prototypes, except static and inline ones and
the functions FUNC calls, transitively), and both. A form is used when FUNC's placed bytes equal the whole build's
('cut') or differ only in pc-relative displacements ('cut-masked'), and it is at least 8%
faster; a 0 from a cut file is always confirmed by a whole-unit compile.

Moves are kept per island as patches (span edits with a few tokens of context), so another
island's moves can be replayed on an island (--cross probability, crossover) and each new best
is reduced over its moves before it is written (a move is left out when FUNC still scores no
worse; compound moves stay whole). A share of the islands (--simplify, at least two) starts from
normalised forms of the base: dead constructs removed, externs sized, pointer arithmetic to
fields, goto chains to tests, locals inlined, else-if flattened. Struct layouts for the pointer
moves come from one probe compile (offsets and sizes of the fields of the structs FUNC's
globals have); unsized extern arrays take their size from the map row or, for an array placed
by an at: line, from the gap to the next known address.

OUT defaults to <build>/permute2/FUNC. SRC is never modified. Rerun match.py on any result."""
import shlex
import collections, hashlib, json, math, mmap, os, queue, random, re, struct, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
for p in (HERE, os.path.join(EXHUME, 'tools')):
    if p not in sys.path: sys.path.insert(0, p)
import permc, permrules as R, permrun, permscore, permscreen, orcobj        # noqa: E402


SAME_ONLY = False        # --same-only: no edit that may change what the program does (meaning 'type')


def arg(a, name, default=None, conv=str):
    if name in a:
        i = a.index(name)
        return conv(a[i + 1])
    return default


def norm_hash(toks):
    return hashlib.blake2b(' '.join(t[1] for t in toks).encode('latin1'), digest_size=12).hexdigest()


# ---------------------------------------------------------------- start-up helpers

def array_sizes(runner, cfg, src, text, toks, types, target_map, work, pins=None):
    """{name: N} for `extern T x[];` arrays whose map row gives their size, or, for an array
    placed by an at: line of the source, the gap to the next known address (map rows and pins):
    sizeof(T) comes from one probe compile of the file with a table of sizeof()s appended."""
    pins = pins or {}
    known = sorted({r.addr for r in target_map.by_name.values()} | set(pins.values()))
    import bisect

    def row_size(name):
        if name in target_map.by_name: return target_map.by_name[name].size
        a = pins.get(name)
        if a is None: return None
        k = bisect.bisect_right(known, a)
        return known[k] - a if k < len(known) and known[k] - a < 0x1000 else None
    cands = []
    for name, (ty, dims, extern) in types.globals.items():
        if extern and dims and dims.replace(' ', '') == '[]' and row_size(name):
            cands.append((name, ty))
    if not cands: return {}
    probe = text + '\nconst unsigned int pmt_sizeof_table[] = {' + ', '.join(f'sizeof({ty})' for _, ty in cands) + '};\n'
    raw, log = permrun.compile_once(runner, cfg, src, probe, work)
    if raw is None: return {}
    o, info = orcobj.read(raw=raw)
    sym = next((s for s in o.symbols.values() if s.name == 'pmt_sizeof_table'), None)
    if sym is None or not isinstance(sym.sec, int): return {}
    data = o.sections[sym.sec].data
    out = {}
    for k, (name, ty) in enumerate(cands):
        sz = int.from_bytes(data[sym.value + 4 * k:sym.value + 4 * k + 4], 'little')
        row = row_size(name)
        if sz and row % sz == 0 and row // sz > 0: out[name] = row // sz
    return out


def struct_layouts(runner, cfg, src, text, toks, types, func, work):
    """{struct type: [(field, offset, size, element size, count or None)]} for the struct types
    of the globals FUNC names, from one probe compile of the file with a table of offsets and
    sizes appended (pointer arithmetic over a record is turned into fields with it)."""
    try:
        s, lb, rb = permc.find_function(toks, func)
    except LookupError:
        return {}
    used = {toks[i][1] for i in range(lb, rb) if toks[i][0] == 'id'}
    tys = set()
    for name in used:
        g = types.globals.get(name)
        if g:
            ty = g[0].replace('struct ', '').strip()
            if ty in types.struct_fields and types.struct_fields[ty]: tys.add(ty)
    entries = [(ty, f, dims) for ty in sorted(tys) for f, fty, dims in types.struct_fields[ty]]
    if not entries: return {}
    items = []
    for ty, f, dims in entries:
        items += [f'__INTADDR__(&((({ty} *)0)->{f}))', f'sizeof((({ty} *)0)->{f})',
                  f'sizeof((({ty} *)0)->{f}' + ('[0])' if dims else ')')]
    probe = text + '\nconst unsigned int pmt_layout_table[] = {' + ', '.join(items) + '};\n'
    raw, log = permrun.compile_once(runner, cfg, src, probe, work)
    if raw is None: return {}
    o, info = orcobj.read(raw=raw)
    sym = next((x for x in o.symbols.values() if x.name == 'pmt_layout_table'), None)
    if sym is None or not isinstance(sym.sec, int): return {}
    data = o.sections[sym.sec].data
    out = {}
    for k, (ty, f, dims) in enumerate(entries):
        off, sz, esz = (int.from_bytes(data[sym.value + 12 * k + 4 * j:sym.value + 12 * k + 4 * j + 4], 'little') for j in range(3))
        out.setdefault(ty, []).append((f, off, sz, esz, sz // esz if dims and esz else None))
    return out


# ---------------------------------------------------------------- moves as patches
# Each applied rule step is kept as a patch: its span edits with a few tokens of context, so it
# can be replayed on another text (crossover between islands) or left out (reduction of a new
# best over its moves, which keeps compound moves whole).

CTXN = 6


def make_patch(toks, spans, words=None):
    """[(tokens before, old tokens, tokens after, new tokens)] per span; the context grows until
    it is found in one place only (repetitive code would otherwise be ambiguous on replay)."""
    words = words or [t[1] for t in toks]
    out = []
    for a, b, new in sorted(spans, key=lambda s: (s[0], s[1])):
        n = CTXN
        while True:
            before, after = words[max(0, a - n):a], words[b:b + n]
            if len(_find(words, before + words[a:b] + after)) == 1 or n >= 200: break
            n *= 2
        out.append((before, words[a:b], after, [tuple(x) for x in new]))
    return out


def _find(words, pat):
    out, i, n = [], -1, len(pat)
    while pat:
        try:
            i = words.index(pat[0], i + 1)
        except ValueError:
            break
        if words[i:i + n] == pat:
            out.append(i)
            if len(out) > 1: break
    return out


def apply_patch(toks, patch):
    """toks with each span of patch put where its context finds it (in exactly one place), or
    None. Context that is found nowhere is cut to four, then three tokens a side."""
    words = [t[1] for t in toks]
    spans = []
    for before, old, after, new in patch:
        hits = _find(words, before + old + after)
        n = min(len(before), len(after), 4)
        while not hits and n >= 3:
            # the place was edited close by (another move of a pair): less context, still one place
            before, after = before[len(before) - n:], after[:n]
            hits = _find(words, before + old + after)
            n -= 1
        if len(hits) != 1: return None
        a = hits[0] + len(before)
        spans.append((a, a + len(old), [permc.Tok(*x) for x in new]))
    try:
        return R.apply(toks, spans)
    except ValueError:
        return None


def make_move(rule, desc, toks, ed, words=None):
    """A move from an Edit on toks: one patch, or for a compound move one patch per step."""
    m = {'rule': rule, 'desc': desc}
    if getattr(ed, 'parts', None): m['seq'] = [make_patch(t, sp) for t, sp in ed.parts]
    else: m['patch'] = make_patch(toks, ed.spans, words)
    return m


SEED_TOKS = []           # the tokens of each --seed-source text (set before the workers fork)


def apply_move(toks, m):
    if 'seed' in m: return SEED_TOKS[m['seed']]        # the whole text of a seed source, in place of the original
    for p in m.get('seq') or [m['patch']]:
        toks = apply_patch(toks, p)
        if toks is None: return None
    return toks


def move_sig(m):
    if 'sig' not in m:
        m['sig'] = hashlib.blake2b(json.dumps(m.get('seq') or m.get('patch') or ['seed', m.get('seed')]).encode('latin1', 'replace'), digest_size=8).hexdigest()
    return m['sig']


def legacy_moves(origin, toks):
    """Moves for an island text whose moves were not kept (a run resumed from an older state):
    one per token hunk of its difference from the original."""
    import difflib
    sm = difflib.SequenceMatcher(None, [t[1] for t in origin], [t[1] for t in toks], autojunk=False)
    out = []
    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == 'equal': continue
        out.append({'rule': 'legacy', 'desc': f'hunk at token {i1}', 'patch': make_patch(origin, [(i1, i2, toks[j1:j2])])})
    # a hunk's context is from the original; later hunks are replayed on earlier results, so
    # contexts that overlap an earlier hunk would not be found: such a list is one move
    t = origin
    for m in out:
        t = apply_move(t, m)
        if t is None:
            return [{'rule': 'legacy', 'desc': 'whole difference', 'patch': make_patch(origin, [(0, len(origin), toks)])}]
    return out


def apply_all(toks, moves):
    for m in moves:
        toks = apply_move(toks, m)
        if toks is None: return None
    return toks


def prefix_text(toks, tail, func):
    """The file up to the end of FUNC's definition. Only valid when FUNC calls nothing defined
    after it: armcc uses a same-file callee's register usage (checked at start-up)."""
    s, lb, rb = permc.find_function(toks, func)
    return permc.render(toks[:rb + 1], '\n')


def callees(toks, func, pair=None):
    """Names FUNC calls, directly or through functions of the file it calls (transitively):
    armcc uses a callee's register usage when the callee is defined in the same file, so their
    bodies must stay in a cut file."""
    pair = pair or permc.match_brackets(toks)
    bodies, none = {}, set()
    for i in range(len(toks) - 1):
        if toks[i][0] == 'id' and toks[i + 1][1] == '(' and toks[i][1] not in bodies and toks[i][1] not in none:
            try:
                s, lb, rb = permc.find_function(toks, toks[i][1], pair)
                bodies[toks[i][1]] = (lb, rb)
            except LookupError:
                none.add(toks[i][1])
    keep, todo = set(), [func]
    while todo:
        f = todo.pop()
        if f in keep or f not in bodies: continue
        keep.add(f)
        lb, rb = bodies[f]
        todo += [toks[j][1] for j in range(lb, rb) if toks[j][0] == 'id' and toks[j + 1][1] == '(']
    return keep


def cut_text(toks, tail, func, types, keep=None):
    """FUNC plus the file's declarations: other function definitions become prototypes,
    except static and inline ones and the functions FUNC calls (see callees)."""
    pair = permc.match_brackets(toks)
    keep = callees(toks, func, pair) if keep is None else keep
    spans = []
    depth = 0
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        if t[1] == '{':
            # a function body at file scope: preceded by ) (or K&R declarations)
            j = i - 1
            while j >= 0 and toks[j][1] != ')' and toks[j][1] not in (';', '}', '=') and toks[j][0] != 'pp': j -= 1
            if j >= 0 and toks[j][1] == ')':
                s = j
                while s > 0 and toks[s - 1][0] != 'pp' and toks[s - 1][1] not in (';', '}'): s -= 1
                words = {x[1] for x in toks[s:i]}
                lp = pair[j]
                name = toks[lp - 1][1]
                if name != func and name not in keep and not ({'static', '__inline', '__forceinline', 'inline'} & words):
                    spans.append((i, pair[i] + 1, [permc.Tok('op', ';', '')]))
            i = pair[i] + 1; continue
        i += 1
    return permc.render(R.apply(toks, spans), tail)


DEAD = re.compile(r'\{\s*(?:unsigned\s+int|uint)\s+z\s*;\s*z\s*=|\{\s*unsigned\s+int\s+z\s*=|\bpmt_u\d+\s*=|\*pmt_p\d+\s*=')


def dead_count(toks, func):
    """Dead-code constructs in FUNC (stored comparisons, unused locals, pointer stores): the
    edits a hand sweep already tries, and the ones most likely to be fits."""
    try:
        s, lb, rb = permc.find_function(toks, func)
    except LookupError:
        return 0
    return len(DEAD.findall(' '.join(t[1] for t in toks[lb:rb + 1])))


# ---------------------------------------------------------------- shared between parent and workers

_Q = struct.Struct('<Q')


class SharedSet:
    """A set of 64-bit keys in anonymous shared memory, made before the workers fork so that all
    of them and the parent see one table (open addressing, no lock: a lost race costs one
    duplicate compile, a full neighbourhood overwrites the key's home slot). It holds the texts
    already made, so a worker drops a duplicate before it compiles it, and the tabu moves."""

    def __init__(self, bits=22):
        self.mask = (1 << bits) - 1
        self.mm = mmap.mmap(-1, 8 << bits)

    @staticmethod
    def key(h):
        """A hex digest's first 64 bits, or a hash of any other value; never 0 (an empty slot)."""
        if isinstance(h, str): return int(h[:16], 16) or 1
        return int.from_bytes(hashlib.blake2b(repr(h).encode('latin1', 'replace'), digest_size=8).digest(), 'little') or 1

    def add(self, h):
        """True when h was not in the set (it is now)."""
        k = self.key(h); i = k & self.mask; mm = self.mm
        for _ in range(64):
            v = _Q.unpack_from(mm, 8 * i)[0]
            if v == k: return False
            if v == 0:
                _Q.pack_into(mm, 8 * i, k); return True
            i = (i + 1) & self.mask
        _Q.pack_into(mm, 8 * (k & self.mask), k)
        return True

    def __contains__(self, h):
        k = self.key(h); i = k & self.mask; mm = self.mm
        for _ in range(64):
            v = _Q.unpack_from(mm, 8 * i)[0]
            if v == k: return True
            if v == 0: return False
            i = (i + 1) & self.mask
        return False


def pen_key(k, d, nm, dead_penalty, move_penalty):
    """The acceptance key of a result key k: each dead-code construct added (d) and each move of
    the variant's list (nm) costs a share of a differing instruction, so of two sources with
    the same code the one with the shorter list wins. An exact result is not penalised."""
    if not k[0] or k[0] == 3: return k
    return k[:-1] + (k[-1] + dead_penalty * max(0, d or 0) + move_penalty * nm,)


def token_lines(toks):
    """The 1-based source line each token starts on (as the compiler numbers the rendered text)."""
    out, ln = [], 1
    for t in toks:
        ln += t[2].count('\n')
        out.append(ln)
        if '\n' in t[1]: ln += t[1].count('\n')
    return out


def remap_lines(old, new, lines):
    """Where the 1-based lines of text old are in text new: lines before the first changed line
    stay, lines after the last one shift by the change in length, lines between follow a line
    diff of the changed part (a changed line goes to the start of what replaced it)."""
    a, b = old.split('\n'), new.split('\n')
    n = min(len(a), len(b))
    lo = 0
    while lo < n and a[lo] == b[lo]: lo += 1
    hi = 0
    while hi < n - lo and a[len(a) - 1 - hi] == b[len(b) - 1 - hi]: hi += 1
    ea, eb = len(a) - hi, len(b) - hi
    mid = None
    out = set()
    for ln in lines:
        i = ln - 1
        if i < lo: out.add(ln)
        elif i >= ea: out.add(ln + len(b) - len(a))
        else:
            if mid is None:
                mid = {}
                if (ea - lo) * max(1, eb - lo) <= 250000:
                    import difflib
                    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a[lo:ea], b[lo:eb], autojunk=False).get_opcodes():
                        for x in range(i1, i2):
                            mid[lo + x] = lo + (j1 + x - i1 if tag == 'equal' else min(j1, max(0, eb - lo - 1)))
            j = mid.get(i, lo)
            if j < len(b): out.add(j + 1)
    return tuple(sorted(out))


def edit_focus(ctx, tline, ed, hot):
    """Does an Edit touch a hot line? True or False, or None for an edit that is global by
    nature and so exempt from the focus: a compound move, an edit with no span inside FUNC's
    body (an extern, a helper's definition, the prototype) and an edit whose spans in the body
    only change locals' declarations (a type, the order, a split). A span that lies wholly in
    declarations is not judged (so a local inlined or initialised is judged by its other
    spans: the uses, the assignment); an insertion is judged by the line of the token it goes
    before. hot holds the hot lines already widened by the window."""
    if getattr(ed, 'parts', None): return None
    decls = getattr(ctx, '_pmt_decls', None)
    if decls is None:
        decls = ctx._pmt_decls = set()
        for l in ctx.locals.values():
            if l.node is not None: decls.update(range(l.node.start, l.node.end))
    toks, n = ctx.toks, len(tline)
    judged = found = False
    for a, b, new in ed.spans:
        if a <= ctx.lb or a > ctx.rb: continue
        insertion = b == a + 1 and len(new) > 1 and new[-1][1] == toks[a][1]
        if not insertion and b > a and all(x in decls for x in range(a, b)): continue
        judged = True
        la = tline[min(a, n - 1)]
        lb = la if insertion else tline[min(max(a, b - 1), n - 1)]
        if not found: found = any(x in hot for x in range(la, lb + 1))
    return found if judged else None


# ---- the single edits of a source (the sweep)

def span_key(ed):
    """What an Edit does to the tokens, as a hashable value; None when its spans overlap."""
    spans = sorted(ed.spans, key=lambda x: (x[0], x[1]))
    if any(spans[n][1] > spans[n + 1][0] for n in range(len(spans) - 1)): return None
    return tuple((a, b, tuple(t[1] for t in new)) for a, b, new in spans)


SUBSET_RULES = ('index_helper', 'setter', 'getter', 'addr_helper', 'expr_helper', 'local_cache', 'cache_group', 'arg_local', 'uniform')


def site_subsets(ctx, r, ed):
    """[Edit] of an Edit made at several sites (reads or stores sent through one helper or local),
    made at some of them only: its spans that replace code in FUNC's body are the sites; what
    it inserts and what it writes outside the body (the helper, the local's declaration) stays.
    Every non-empty proper subset of up to 8 sites; of more, every run of neighbouring sites and
    a fixed sample of 120 others."""
    if getattr(ed, 'parts', None): return []
    keep, sites = [], []
    for sp in ed.spans:
        a, b, new = sp
        ins = b == a + 1 and len(new) > 1 and new[-1][1] == ctx.toks[a][1]
        (keep if (a <= ctx.lb or a > ctx.rb or ins or b == a) else sites).append(sp)
    n = len(sites)
    if n < 2 or not keep: return []
    sites.sort(key=lambda x: x[0])
    if n <= 8:
        masks = range(1, (1 << n) - 1)
    else:
        runs = {((1 << (j - i)) - 1) << i for i in range(n) for j in range(i + 1, n + 1)} - {(1 << n) - 1}
        rng = random.Random(f'sites:{r.id}:{ed.desc}')
        masks = sorted(runs | {rng.randrange(1, (1 << n) - 1) for _ in range(120)})
    out = []
    for m in masks:
        idx = [k for k in range(n) if m >> k & 1]
        e = R.Edit(keep + [sites[k] for k in idx], f'{ed.desc} [sites {",".join(str(k + 1) for k in idx)} of {n}]', meaning=ed.meaning)
        out.append(e)
    return out


def list_singles(ctx, r, sweep_max, weight):
    """[(tier, Edit, family)] of the single edits rule r can make on ctx, each once: what its
    enumeration lists (permrules: `fn.enumerate(ctx)`, the probe rules), lowest tier first; for
    a rule without one, draws with fixed seeds until 60 in a row give nothing new, the first
    15 x weight of them (10 to 120) in tier 0, the rest in tier 2.5. At most sweep_max."""
    out, seen = [], set()

    def add(tier, ed, fam=None):
        key = span_key(ed)
        if key is None or key in seen: return False
        seen.add(key)
        out.append((tier, ed, fam))
        return True
    def subsets():
        """After a helper-type rule's edits: each of them at every subset of its sites (tier 2,
        one family with the whole edit, so that the subsets go first when the whole improved)."""
        if r.id not in SUBSET_RULES: return out
        for n, (tier, ed, fam) in enumerate(list(out)):
            subs = site_subsets(ctx, r, ed)
            if not subs: continue
            fam = fam or f'sites of {r.id}: {ed.desc}'
            out[n] = (tier, ed, fam)
            for e in subs:
                if len(out) >= sweep_max: return out
                add(2, e, fam)
        return out
    en = getattr(r.fn, 'enumerate', None)
    if en is not None:
        try:
            items = sorted(en(ctx), key=lambda x: x[0])
        except Exception:
            items = []
        for it in items:
            if len(out) >= sweep_max: break
            add(it[0], it[1], it[2] if len(it) > 2 else None)
        return subsets()
    miss = seed = 0
    first = max(10, min(120, int(15 * weight)))
    while miss < 60 and len(out) < sweep_max:
        every = r.everywhere and seed % 3 == 2
        try:
            ed = r.fn(ctx, random.Random(f'{r.id}:{seed}'), everywhere=every)
        except Exception:
            ed = None
        if ed is not None and SAME_ONLY and ed.meaning == 'type': ed = None
        seed += 1
        if ed is not None and add(0 if len(out) < first else 2.5, ed): miss = 0
        else: miss += 1
    return subsets()


def simplify_singles(ctx):
    """[(0, Edit, None)]: the normalised forms of the source the simplify-first islands start from."""
    out, seen = [], set()
    for attempt in range(40):
        try:
            ed = R.r_normalise(ctx, random.Random(f'simplify:{attempt}'), full=attempt < 20,
                               imode=('runs', 'run', 'every', 'single')[attempt % 4])
        except Exception:
            ed = None
        key = span_key(ed) if ed is not None else None
        if key is not None and key not in seen:
            seen.add(key); out.append((0, ed, None))
    return out


def sweep_order(items):
    """items (tuples that start tier, rule id, x, is a simplify form) in the order they are
    tried: the first pass first (the simplified forms, then the rules in turn: each rule's
    first edit, then each rule's second, ...); within a later tier a fixed shuffle, so that a
    time limit thins every rule and every part of the function alike."""
    order = list(range(len(items)))
    random.Random(0).shuffle(order)
    turn, cnt = {}, collections.Counter()
    for n, x in enumerate(items):
        if x[0] == 0:
            turn[n] = -1 if x[3] else cnt[x[1]]
            cnt[x[1]] += 1
    pos = {n: p for p, n in enumerate(order)}
    order.sort(key=lambda n: (0, turn[n], n) if items[n][0] == 0 else (items[n][0], pos[n], n))
    return [items[n] for n in order]


def cut_form(form, toks, tail, func, types):
    """The text compiled for a cut mode's form ('prefix', 'stub' or 'prefix+stub')."""
    if form == 'prefix': return prefix_text(toks, tail, func)
    if form == 'stub': return cut_text(toks, tail, func, types)
    t, tl = permc.tokenize(prefix_text(toks, tail, func))
    return cut_text(t, tl, func, types)


class Base:
    """One parsed base text in a worker."""
    __slots__ = ('text', 'toks', 'ctx', 'tline')

    def __init__(self, text, toks, ctx):
        self.text, self.toks, self.ctx, self.tline = text, toks, ctx, None

    def lines(self):
        if self.tline is None: self.tline = token_lines(self.toks)
        return self.tline


class Gen:
    """The generator a worker holds (made in the parent before the pool forks): it parses a base
    text once per base (kept by the text's hash; the parent writes each base to the bases
    folder and names it in the job), applies rules to it under the job's seed, drops a text
    already in the shared set, compiles and scores the variant, and returns the score, the
    size, the trail, the text's hash, and the text with its moves only when the parent may keep
    it (it beats the island's key as of the job, the best, or a place on the Pareto front)."""
    mode = 'whole'
    dead_penalty = move_penalty = 0.0
    dead0 = 0
    p_pair, p_every = 0.3, 0.35
    seen = tabu = wmm = None
    bases_dir = None
    focus, focus_cold, focus_near = False, 0.25, 3
    src_name = None
    origin_toks = None
    cap = 8
    sizes = layouts = None
    c_opts = None               # the file's compile options as one line (for permrules.r_file_opts)

    def __init__(self, func, tail, types, rules, **kw):
        self.func, self.tail, self.types, self.rules = func, tail, types, list(rules)
        self.__dict__.update(kw)
        self.cache = collections.OrderedDict()
        self.root_ctx = {}

    # ---- what the parent uses too
    def make_ctx(self, toks):
        ctx = R.Ctx(toks, self.tail, self.func, self.types)
        ctx.array_sizes = self.sizes or {}
        ctx.layouts = self.layouts or {}
        ctx.c_opts = self.c_opts
        return ctx

    def dead_of(self, toks, ctx=None):
        """Weighted dead-code constructs of FUNC (permrules.dead_score)."""
        try:
            return R.dead_score(ctx or self.make_ctx(toks))
        except Exception:
            return dead_count(toks, self.func)

    def job_text(self, toks):
        if self.mode.startswith('cut'):
            return cut_form(self.mode.split(':')[1], toks, self.tail, self.func, self.types)
        return permc.render(toks, self.tail)

    # ---- in a worker
    def base(self, bh):
        b = self.cache.get(bh)
        if b is not None:
            self.cache.move_to_end(bh); return b
        with open(os.path.join(self.bases_dir, bh + '.c'), encoding='latin1') as f: text = f.read()
        toks, _ = permc.tokenize(text)
        b = self.cache[bh] = Base(text, toks, self.make_ctx(toks))
        while len(self.cache) > self.cap: self.cache.popitem(last=False)
        return b

    def weights(self):
        if self.wmm is None: return [r.weight for r in self.rules]
        return struct.unpack_from(f'<{len(self.rules)}d', self.wmm, 0)

    @staticmethod
    def pick(rules, ws, rng):
        x = rng.random() * sum(ws)
        for r, w in zip(rules, ws):
            x -= w
            if x <= 0: return r
        return rules[-1]

    def mutate(self, base, rng, depth=0, ws=None, hot=None, st=None, min_steps=1):
        """(toks, [(rule, desc, meaning)], [(rule, desc, toks before, Edit)]) or None: one rule
        applied once (at every site of what it chose with probability p_every), two with
        probability p_pair, more when the search has escalated (depth). With hot lines (a set,
        already widened), an edit that touches none of them is kept with probability focus_cold."""
        ws = ws or self.weights()
        st = st if st is not None else collections.Counter()
        steps = 2 if rng.random() < self.p_pair else 1
        if depth and rng.random() < 0.6:          # escalated: deeper compound moves
            steps = rng.randint(2, 2 + depth)
        steps = max(steps, min_steps)
        toks, ctx, tline = base.toks, base.ctx, (base.lines() if hot else None)
        trail, made = [], []
        for s in range(steps):
            for attempt in range(40 if hot else 12):
                r = self.pick(self.rules, ws, rng)
                every = r.everywhere and rng.random() < self.p_every
                try:
                    ed = r.fn(ctx, rng, everywhere=every)
                except Exception:
                    st[(r.id, 'crash')] += 1; ed = None
                if ed is not None and SAME_ONLY and ed.meaning == 'type': ed = None
                if ed is None:
                    st[(r.id, 'nosite')] += 1; continue
                try:
                    nt = R.apply(toks, ed.spans)
                except ValueError:
                    st[(r.id, 'badedit')] += 1; continue
                if self.tabu is not None and (r.id, ed.desc) in self.tabu:      # a move that already
                    st[(r.id, 'tabu')] += 1; continue                           # left the code unchanged
                if hot:
                    f = edit_focus(ctx, tline, ed, hot)
                    if f is False and rng.random() >= self.focus_cold:
                        st[(r.id, 'cold')] += 1; continue
                    st[(r.id, 'hot' if f else 'exempt' if f is None else 'cold_kept')] += 1
                trail.append((r.id + ('*' if every else ''), ed.desc, ed.meaning))
                made.append((r.id, ed.desc, toks, ed))
                toks = nt
                break
            else:
                return None
            if s + 1 < steps:
                try:
                    ctx = self.make_ctx(toks)
                except Exception:
                    return None
                if hot: tline = token_lines(toks)
        return toks, trail, made

    @staticmethod
    def cross(base, cross):
        """base with the moves of another island replayed on it (those whose context is found)."""
        k, theirs = cross
        toks, used = base.toks, []
        for m in theirs:
            nt = apply_move(toks, m)
            if nt is not None: toks = nt; used.append(m)
        if not used: return None
        return toks, [('cross', f'{len(used)} moves of island {k}', 'same')], used

    def run(self, job):
        kind, nid, p = job
        out = {'id': nid, 'gen': 0, 'dup': 0}
        t0 = time.perf_counter()
        base, made = None, ()
        if kind == 'gen':
            st = collections.Counter()
            base = self.base(p['bh'])
            rng = random.Random(p['seed'])
            ws = self.weights()
            hot = p.get('hot')
            hot = {l + d for l in hot for d in range(-self.focus_near, self.focus_near + 1)} if hot and self.focus else None
            m = h = None
            for attempt in range(20):
                out['gen'] += 1
                m = self.cross(base, p['cross']) if p.get('cross') and attempt == 0 else None
                if m is None: m = self.mutate(base, rng, p.get('depth', 0), ws, hot, st, p.get('steps', 1))
                if m is None: continue
                h = norm_hash(m[0])
                if self.seen is not None and not self.seen.add(h):
                    out['dup'] += 1; m = None; continue
                break
            out['st'] = dict(st)
            if m is None:
                out['none'] = True; out['tg'] = time.perf_counter() - t0
                return out
            toks, out['trail'], made = m
            nm = p['nm'] + len(made)
        elif kind == 'list':                       # the single edits of the original by one rule, as moves
            root = p.get('root', 0)                # 0 the original; any other start by the moves that make it
            if root not in self.root_ctx:
                rt = apply_all(self.origin_toks, p.get('rmoves') or [])
                if rt is None: raise ValueError('the start does not replay from the original')
                if len(self.root_ctx) > 24: self.root_ctx.clear()
                self.root_ctx[root] = (rt, self.make_ctx(rt), [t[1] for t in rt])
            rtoks, ctx, rwords = self.root_ctx[root]
            if p['rule'] is None: rid, items = 'normalise', simplify_singles(ctx)
            else:
                r = next(x for x in self.rules if x.id == p['rule'])
                rid, items = r.id, list_singles(ctx, r, p['max'], p['weight'])
            out['items'] = [(tier, rid, ed.desc, p['rule'] is None, fam,
                             hashlib.blake2b(repr(span_key(ed)).encode('latin1', 'replace'), digest_size=10).digest(),
                             make_move(rid, ed.desc, rtoks, ed, rwords)) for tier, ed, fam in items]
            out['tg'] = time.perf_counter() - t0
            return out
        elif kind == 'moves':                      # a move list replayed on the original
            toks = apply_all(self.origin_toks, p['moves'])
            if toks is None and len(p['moves']) >= 2 and p.get('either'):      # a pair: the other order
                toks = apply_all(self.origin_toks, p['moves'][:-2] + [p['moves'][-1], p['moves'][-2]])
                out['swapped'] = True
            if toks is None:
                out['none'] = True; return out
            h, nm = norm_hash(toks), len(p['moves'])
            if p.get('nocompile'):
                out['h'] = h; return out
            if p.get('mark') and self.seen is not None and not self.seen.add(h):
                out['none'] = out['dupe'] = True; return out               # a text already made
        else:                                      # 'text': a whole source the parent made
            toks, _ = permc.tokenize(p['text'])
            h, nm = norm_hash(toks), p.get('nm', 0)
            if self.seen is not None: self.seen.add(h)
        out['h'] = h
        ctext = self.job_text(toks)
        out['tg'] = time.perf_counter() - t0
        raw, r, ohash, out['tc'], out['ts'], log = permrun.worker_score(ctext)
        out['r'] = r
        if r is None:
            out['log'] = log; return out
        out['ohash'] = ohash
        k = r.key()
        if not r.ok:
            out['k'] = k; return out
        old, thr, bkey, force = p.get('old'), p.get('thr'), p.get('bkey'), p.get('want')

        def wanted(x):
            return (old is not None and x < old) or (thr is not None and x <= thr) or (bkey is not None and x < bkey)
        d = None
        if self.dead_penalty and k[0] and (force or p.get('dead') or wanted(pen_key(k, 0, nm, 0, self.move_penalty))):
            # prefer natural edits: each dead-code construct added costs this many instructions
            # (worked out only where it can decide something: it needs a parse of the variant)
            try:
                d = self.dead_of(toks) - self.dead0
            except Exception:
                d = None
        k = out['k'] = pen_key(k, d, nm, self.dead_penalty, self.move_penalty)
        out['d'] = d
        want = force or wanted(k) or (p.get('low') is not None and r.key() < p['low'])
        if not want and d is not None and p.get('front') is not None:
            f = p['front'].get(round(d * 4) / 4)
            want = f is None or k < f
        if want:
            t1 = time.perf_counter()
            text = out['text'] = ctext if self.mode == 'whole' else permc.render(toks, self.tail)
            out['moves'] = [x if isinstance(x, dict) else make_move(*x) for x in made]
            improved = force or (old is not None and k < old) or (bkey is not None and k < bkey)
            if improved and not r.exact and not permscore.Result.flat:
                out['sdiff'] = permrun._W['target'].struct_diff(raw)      # which instructions are wrong
            if self.focus and not r.exact and not improved and p.get('hot') and base is not None and r.size_ok:
                out['hot'] = remap_lines(base.text, text, p['hot'])       # the base's hot lines, where they are now
            out['tg'] += time.perf_counter() - t1
            if self.focus and not r.exact and improved and r.size_ok:
                # (at a wrong size the instruction diff is of shifted code: its lines mean little,
                # so a base of the wrong size has no hot lines and its edits go anywhere)
                t1 = time.perf_counter()
                out['hot'] = self.hot_of(text, raw)                       # an improvement: its own line rows
                out['th'] = time.perf_counter() - t1
        return out

    def hot_of(self, text, raw):
        """The source lines of text that the differing instructions of FUNC come from (a tuple),
        or None: one more compile with -g, used only when FUNC's code is the same with it."""
        if self.mode != 'whole':
            raw, _, _ = permrun.worker_compile(text)
            if raw is None: return None
        raw_g, _, _ = permrun.worker_compile(text, ['-g'])
        if raw_g is None: return None
        hot = permrun._W['target'].hot_lines(raw, raw_g, self.src_name)
        return tuple(hot) if hot else None


# ---------------------------------------------------------------- the search

class Island:
    def __init__(self, k, text, result, trail, key=None, ohash=None):
        self.k, self.text, self.result, self.trail = k, text, result, list(trail)
        self.key = key if key is not None else result.key()
        self.ohash = ohash
        self.moves = []
        self.home = None            # where restarts to the original go (a simplified base)
        self.stale = 0
        self.bh = self.h = self.hot = None      # base file hash, normalised text hash, hot lines
        self.strict = k % 2 == 1    # keeps only improvements (the odd islands)
        self.root = None            # the start it belongs to (the original or a seed source)
        self.anchor = False
        self.t_gain = time.time()   # when it last gave a new best or was sent back to the best

    def take(self, src):
        """Restart from src (the best, the original or a home)."""
        self.text, self.result, self.trail = src.text, src.result, list(src.trail)
        self.key, self.ohash, self.moves = src.key, src.ohash, list(src.moves)
        self.bh, self.h, self.hot = src.bh, src.h, src.hot
        self.stale = 0

    def copy(self, k):
        x = Island(k, self.text, self.result, self.trail, self.key, self.ohash)
        x.moves, x.bh, x.h, x.hot = list(self.moves), self.bh, self.h, self.hot
        return x


Pending = collections.namedtuple('Pending', 'isl bh moves trail margin force vtrail vmoves t')
FLAGS = ('--resume', '--no-screen', '--no-reduce', '--no-focus', '--no-sweep', '--sweep-only', '--same-only', '--flat-score')


class Search:
    def __init__(self, cfg, src, func, a):
        self.cfg, self.src, self.func, self.a = cfg, os.path.abspath(src), func, a
        with open(self.src, encoding='latin1') as f: self.text = f.read()
        self.out = arg(a, '--out') or os.path.join(cfg.build, 'permute2', func)
        os.makedirs(self.out, exist_ok=True)
        self.jobs = arg(a, '--jobs', max(1, (os.cpu_count() or 2) - 1), int)
        self.nisl = arg(a, '--islands', max(2, min(16, self.jobs // 2)), int)
        self.seed = arg(a, '--seed', int(time.time()) & 0xFFFF, int)
        self.rng = random.Random(self.seed)
        self.iters = arg(a, '--iters', 10 ** 12, int)
        self.tlimit = arg(a, '--time', 10 ** 9, float)
        self.p_pair = arg(a, '--pair', 0.3, float)
        self.p_every = arg(a, '--everywhere', 0.35, float)
        global SAME_ONLY
        SAME_ONLY = '--same-only' in a
        self.p_cross = arg(a, '--cross', 0.06, float)
        self.simplify = arg(a, '--simplify', 0.25, float)     # share of islands started from a normalised source
        self.temp = arg(a, '--temp', 0.6, float)
        self.dead_penalty = arg(a, '--dead-penalty', 0.0, float)
        self.move_penalty = arg(a, '--move-penalty', 0.15, float)
        self.rebase_s = 60 * arg(a, '--rebase', 10.0, float)   # minutes without a new best before an island goes back to it
        self.focus = '--no-focus' not in a
        permscore.Result.flat = '--flat-score' in a      # the old single count as the key (before the workers fork)
        self.history = []                   # (seconds, compiles, score, moves) at each new best
        self.sdiffs = {}                    # text hash -> its structural differences (from the worker that scored it)
        self.sweep_on = '--no-sweep' not in a
        self.sweep_max = arg(a, '--sweep-max', 3000, int)      # single edits per rule
        self.sweep_share = arg(a, '--sweep-share', 0.25, float) # of --time, for the single edits
        self.pair_share = arg(a, '--pair-share', 0.15, float)   # of --time, for the pairs
        self.beam = arg(a, '--beam', 10, int)                   # the best results of a level given one more edit
        self.beam_share = arg(a, '--beam-share', 0.15, float)   # of --time, for the third and further edits
        self.pair_top = arg(a, '--pairs', 40, int)              # the best singles paired with each other
        self.pair_rem = arg(a, '--pair-removals', 300, int)     # the best singles paired with each removal
        self.debt_max = arg(a, '--debt-bases', 16, int)        # bases made by removing the source's dead constructs
        self.debt_share = arg(a, '--debt-share', 0.3, float)  # of --time, for the sweeps of those bases together
        self.resweep = arg(a, '--resweep', 1, int)             # sweeps again from a sweep's best when it halved the count
        self.residues = {}                                     # structural signature -> (key, text, score, moves, lines)
        self.p_origin = arg(a, '--origin', 0.15, float)         # share of jobs made from the original source
        self.focus_cold = arg(a, '--focus-cold', 0.25, float)
        self.focus_near = arg(a, '--focus-near', 3, int)
        self.noop = collections.Counter()      # (rule, site) -> variants that left the code as it was
        self.tabu_after = arg(a, '--tabu', 2, int)
        self.restart = arg(a, '--restart', 400, int)
        self.screen_on = '--no-screen' not in a
        kind = arg(a, '--runner', os.environ.get('EXHUME_PERMUTE_RUNNER', 'native'))
        self.runner = permrun.make_runner(kind, cfg, {'armcc_home': arg(a, '--armcc-home'),
                                                      'mappings': arg(a, '--mappings'), 'exec': arg(a, '--exec')})
        self.opts = permrun.source_opts(cfg, self.src, self.text)
        self.work = os.path.join(permrun.ram_dir(), f'pmt-{os.getpid()}')
        self.pwork = os.path.join(self.work, 'parent')         # the parent's own compiles (one Wine prefix)
        self.bases_dir = os.path.join(self.work, 'bases')
        self.stem = os.path.splitext(os.path.basename(self.src))[0]
        self.rules = list(R.RULES)
        only = arg(a, '--only'); skip = arg(a, '--skip')
        if only: self.rules = [r for r in self.rules if r.id in only.split(',')]
        if skip: self.rules = [r for r in self.rules if r.id not in skip.split(',')]
        w = arg(a, '--weights')
        self.weights = {r.id: r.weight for r in self.rules}
        if w:
            with open(w) as f: self.weights.update({k: v for k, v in json.load(f).items() if k in self.weights})
        self.base_weights = dict(self.weights)
        self.stats = collections.defaultdict(collections.Counter)
        self.counts = collections.Counter()
        # shared with the workers (made before the pool forks)
        self.seen = SharedSet(arg(a, '--seen-bits', 22, int))   # normalised texts already made
        self.tabu = SharedSet(16)                               # (rule, site) moves not to make again
        self.wmm = mmap.mmap(-1, 8 * max(1, len(self.rules)))   # the rules' effective weights
        self.seen_new = []                  # hashes to append to OUT/seen.txt
        self.bases = set()                  # base files written
        self.obj_seen = set()
        self.force = set()
        self.t_submit = self.t_result = self.t_best = 0.0
        self.log = open(os.path.join(self.out, 'attempts.tsv'), 'a')

    # ---- start-up
    def compile(self, text, extra=()):
        """(object bytes or None, log) of one compile in the parent."""
        return permrun.compile_once(self.runner, self.cfg, self.src, text, self.pwork, extra)

    def start(self):
        t0 = time.time()
        os.makedirs(self.bases_dir, exist_ok=True)
        raw, log = self.compile(self.text)
        if raw is None: raise SystemExit('the source does not compile:\n' + log[-3000:])
        self.base_raw = raw
        self.target = permscore.Target(self.cfg, self.text, self.func, raw)
        r0 = self.target.score(raw)
        print(f'{self.func}: start {r0.text()}  (compile + target {time.time() - t0:.1f} s)', flush=True)
        toks, tail = permc.tokenize(self.text)
        self.tail = tail
        incdirs = [o[2:] for o in self.opts if o.startswith('-I')]
        self.types = permc.file_types(self.src, self.text, incdirs)
        self.types.scan_globals(toks)
        self.sizes = array_sizes(self.runner, self.cfg, self.src, self.text, toks, self.types, self.target.m,
                                 self.pwork, getattr(self.target, 'extra', None))
        if self.sizes: print(f'  extern arrays sizeable from the map: {len(self.sizes)}', flush=True)
        self.layouts = struct_layouts(self.runner, self.cfg, self.src, self.text, toks, self.types, self.func, self.pwork)
        if self.layouts: print(f'  struct layouts read: {", ".join(sorted(self.layouts))}', flush=True)
        self.gen = Gen(self.func, tail, self.types, self.rules, sizes=self.sizes, layouts=self.layouts,
                       c_opts=shlex.join(self.opts[:-1]), p_pair=self.p_pair, p_every=self.p_every, dead_penalty=self.dead_penalty,
                       move_penalty=self.move_penalty, seen=self.seen, tabu=self.tabu, wmm=self.wmm,
                       bases_dir=self.bases_dir, focus_cold=self.focus_cold, focus_near=self.focus_near,
                       src_name=self.stem + '.c', origin_toks=toks, cap=self.nisl + 3)
        self.mode = 'whole'
        cut = arg(self.a, '--cut', 'off')
        if cut != 'off':
            self.mode = self.check_cut(toks, tail, raw, cut)
        self.gen.mode = self.mode
        ctx = self.gen.make_ctx(toks)
        self.origin_toks, self.origin_ctx = toks, ctx
        self.dead0 = self.gen.dead0 = self.gen.dead_of(toks, ctx)
        self.base_result = r0
        self.screen = permscreen.Screen(self.src, self.opts, self.text, self.armcc_include()) if self.screen_on else None
        self.origin = Island(-2, self.text, r0, [], r0.key(), r0.chash)
        self.origin.h = norm_hash(toks)
        self.origin.bh = self.put_base(self.text)
        self.seen.add(self.origin.h)
        if self.focus and not r0.exact:
            hot = self.hot_parent(self.text, raw)
            if hot is not None and not r0.size_ok:
                hot = ()
                print('  focus: the start has the wrong size, so no lines are favoured until a base has the right one', flush=True)
            if hot is None:
                self.focus = False
                print('  focus: off (a -g compile gives no line rows for FUNC, or other code for it)', flush=True)
            else:
                self.origin.hot = hot or None
                if hot: print(f'  focus: the differing instructions come from {len(hot)} source lines '
                              f'(-g compile, FUNC\'s code unchanged by it)', flush=True)
        self.gen.focus = self.focus
        self.low = (r0.key(), r0.text(), 0)       # the lowest result seen: key, text, moves
        self.origin.ridx, self.origin.name = 0, 'the original'
        self.roots = [self.origin] + self.seed_roots()      # the starts: the original and the seed sources kept
        n = len(self.roots)
        if n > 1 and arg(self.a, '--islands') is None: self.nisl = max(self.nisl, 2 * n)
        self.islands = []
        for k in range(self.nisl):          # the islands go to the starts in turn; of a start's islands every
            isl = self.roots[k % n].copy(k)  # second one keeps only improvements
            isl.root, isl.strict = self.roots[k % n], (k // n) % 2 == 1
            self.islands.append(isl)
        first = min(self.roots, key=lambda x: x.key)
        self.best = first.copy(-1)
        if first.result.key() < self.low[0]: self.low = (first.result.key(), first.result.text(), len(first.moves))
        self.anchors = []                   # jobs made from each start all through the run (--origin)
        for root in self.roots:
            a = root.copy(-100 - root.ridx)
            a.root, a.strict, a.anchor = root, True, True
            self.anchors.append(a)
        self.swept = set()                  # the base hashes of the starts whose single edits were swept
        self.debt = self.debt_roots() if self.sweep_on and '--resume' not in self.a else []
        self.front = {}                     # dead-code edits added -> (key, text, result)
        self.push_weights()
        if '--resume' in self.a: self.load_state()

    def seed_roots(self):
        """An island for each --seed-source FILE that is worth searching: another complete text
        of the source file (a sibling release's, an older commit's, a hand variant). Each is
        compiled and scored; it is kept when FUNC scores, with a count within --seed-factor
        (2) times the best start's plus 5. A kept one gets a share of the sweep and its own
        islands; what descends from it carries a first move `seed` naming the file."""
        files = [self.a[i + 1] for i, x in enumerate(self.a) if x == '--seed-source' and i + 1 < len(self.a)]
        # a folder of them: --seed-dir DIR, or by default <tree>/variants/FUNC beside exhume.toml
        import glob
        sdir = arg(self.a, '--seed-dir') or os.path.join(self.cfg.root, 'variants', self.func)
        files += [p for p in sorted(glob.glob(os.path.join(sdir, '*.c'))) if p not in files]
        factor = arg(self.a, '--seed-factor', 2.0, float)
        cands = []
        for path in files:
            with open(path, encoding='latin1') as f: text = f.read()
            name = os.path.basename(path)
            raw, log = self.compile(text)
            if raw is None:
                print(f'  seed source {path}: does not compile: {(log.strip().splitlines() or [""])[-1][:160]}', flush=True); continue
            r = self.target.score(raw)
            if not r.ok:
                print(f'  seed source {path}: not scored: {r.error}', flush=True); continue
            try:
                toks, _ = permc.tokenize(text)
                permc.find_function(toks, self.func)
            except Exception as e:
                print(f'  seed source {path}: {type(e).__name__}: {e}', flush=True); continue
            if norm_hash(toks) in {norm_hash(self.origin_toks)} | {norm_hash(c[5]) for c in cands}: continue      # the same text again
            cands.append((path, name, text, raw, r, toks))
        best = min([self.base_result.diff or 0] + [c[4].diff for c in cands]) if cands else 0
        out = []
        for path, name, text, raw, r, toks in cands:
            if not r.exact and r.diff > factor * best + 5:
                print(f'  seed source {path}: {r.text()}: left out (more than {factor:g} times the best start\'s {best} plus 5)', flush=True)
                continue
            self.types.scan_globals(toks)
            SEED_TOKS.append(toks)
            mv = [{'rule': 'seed', 'desc': f'seed source {name} ({r.text()})', 'seed': len(SEED_TOKS) - 1}]
            isl = Island(-10 - len(out), text, r, [('seed', mv[0]['desc'])], self.key_of(r, self.dead_delta(toks), 1), r.chash)
            isl.moves, isl.h, isl.bh = mv, norm_hash(toks), self.put_base(text)
            isl.ridx, isl.name = len(SEED_TOKS), 'seed source ' + name
            self.seen.add(isl.h)
            if self.focus and not r.exact and r.size_ok: isl.hot = self.hot_parent(text, raw) or None
            print(f'  seed source {path}: start {r.text()}: searched beside the original', flush=True)
            out.append(isl)
        return out

    def hot_parent(self, text, raw):
        """The hot lines of text (a tuple, empty when no instruction differs) from a -g compile
        in the parent, or None when the mapping cannot be had."""
        raw_g, _ = self.compile(text, ['-g'])
        if raw_g is None: return None
        hot = self.target.hot_lines(raw, raw_g, self.stem + '.c')
        return None if hot is None else tuple(hot)

    def armcc_include(self):
        home = arg(self.a, '--armcc-home') or os.environ.get('EXHUME_ARMCC_LINUX')
        return os.path.join(home, 'include') if home else None

    def key_of(self, r, d=None, nm=0):
        return pen_key(r.key(), d, nm, self.dead_penalty, self.move_penalty)

    def dead_delta(self, toks):
        return self.gen.dead_of(toks) - self.dead0 if self.dead_penalty else None

    def put_base(self, text):
        """The name of text in the bases folder the workers read (written once)."""
        bh = hashlib.blake2b(text.encode('latin1', 'replace'), digest_size=10).hexdigest()
        if bh not in self.bases:
            with open(os.path.join(self.bases_dir, bh + '.c'), 'w', encoding='latin1', errors='replace') as f: f.write(text)
            self.bases.add(bh)
        return bh

    def gc_bases(self, pending):
        """Delete the base files no island, home or waiting job names."""
        live = {p.bh for p in pending.values()} | {self.best.bh} | {x.bh for x in self.roots}      # (a debt base's file is not needed: the workers replay it)
        for isl in self.islands:
            live.add(isl.bh)
            if isl.home: live.add(isl.home.bh)
        for bh in self.bases - live:
            try:
                os.remove(os.path.join(self.bases_dir, bh + '.c'))
            except OSError:
                pass
        self.bases &= live

    def check_cut(self, toks, tail, whole_raw, cut):
        """The fastest cut form whose FUNC code equals the whole unit's ('prefix': the file up
        to FUNC; 'stub': other bodies as prototypes; or both), else the whole unit."""
        t0 = time.time()
        self.compile(self.text)
        best = ('whole', time.time() - t0)
        print(f'  whole unit: {best[1]:.2f} s per compile')
        for form in ('prefix', 'stub', 'prefix+stub'):
            m = self.check_cut1(toks, tail, whole_raw, form)
            if m and m[1] < best[1] * 0.92: best = m
        return best[0]

    def check_cut1(self, toks, tail, whole_raw, form):
        text = cut_form(form, toks, tail, self.func, self.types)
        t0 = time.time()
        raw, log = self.compile(text)
        dt = time.time() - t0
        if raw is None:
            print(f'  cut {form}: does not compile'); return None
        try:
            a = self.target.place_function(orcobj.read(raw=raw)[0])[0]
            b = self.target.place_function(orcobj.read(raw=whole_raw)[0])[0]
        except Exception as e:
            print(f'  cut {form}: does not place ({e})'); return None
        if a == b:
            print(f'  cut {form}: exact (FUNC equal byte for byte), {dt:.2f} s per compile'); return ('cut:' + form, dt)
        ra, rb = self.target.score(raw), self.target.score(whole_raw)
        if ra.ok and rb.ok and ra.diff == rb.diff and len(a) == len(b):
            print(f'  cut {form}: masked (pc-relative displacements differ), {dt:.2f} s per compile'); return ('cut-masked:' + form, dt)
        print(f'  cut {form}: other code for FUNC'); return None

    # ---- jobs
    def eff_weight(self, r):
        """The rule's weight scaled by how often it changes the code at all (a bandit over the
        logged outcomes: a rule whose variants mostly compile to the object already in hand
        is proposed less)."""
        s = self.stats[r.id]
        changed = (s['compiled'] - s['noop'] + 5) / (s['compiled'] + 10)
        return self.weights[r.id] * (0.15 + 0.85 * changed)

    def push_weights(self):
        """The rules' effective weights, into the memory the workers pick rules from."""
        if self.rules: struct.pack_into(f'<{len(self.rules)}d', self.wmm, 0, *[self.eff_weight(r) for r in self.rules])

    def cross_moves(self, isl):
        """(island number, its moves isl lacks, at most 20) for a crossover job, or None."""
        others = [o for o in self.islands if o is not isl and o.moves]
        if not others: return None
        o = self.rng.choice(others)
        have = {move_sig(m) for m in isl.moves}
        theirs = [m for m in o.moves if move_sig(m) not in have][-20:]      # each replay costs a scan
        return (o.k, theirs) if theirs else None

    def send(self, job, results):
        nid = job[1]
        self.pool.pool.apply_async(permrun._worker_gen, (job,), callback=results.put,
                                   error_callback=lambda e, n=nid: results.put({'id': n, 'err': repr(e)}))
        self.counts['submitted'] += 1

    def submit(self, nid, pending, results):
        """Hand one variant job for the next island to the workers: the base's name, a seed,
        and what decides whether its text is worth sending back."""
        isl = self.islands[nid % self.nisl]
        if self.p_origin and self.rng.random() < self.p_origin: isl = self.rng.choice(self.anchors)
        old, margin, thr = isl.key, None, None
        if not isl.strict:
            # the island's draw for a variant a little worse is made here, so the worker knows
            # what the island would keep: accepted when worse by no more than -temp * ln(u)
            margin = -self.temp * math.log(max(self.rng.random(), 1e-300)) if self.temp > 0 else 0.0
            thr = old[:-1] + (old[-1] + margin,)
        cross = self.cross_moves(isl) if self.p_cross and self.rng.random() < self.p_cross else None
        p = {'bh': isl.bh, 'seed': self.rng.getrandbits(48), 'depth': self.depth, 'cross': cross, 'old': old,
             'thr': thr, 'bkey': self.best.key, 'hot': isl.hot if self.focus else None, 'nm': len(isl.moves),
             'low': self.low[0]}
        if isl.anchor: cross = p['cross'] = None
        if isl.bh in self.swept and not cross:
            p['steps'] = 2                        # the single edits of the original were tried in the sweep
        if self.dead_penalty: p['front'] = {d: v[0] for d, v in self.front.items()}
        pending[nid] = Pending(isl.k, isl.bh, isl.moves, isl.trail, margin, False, None, None, time.time())
        self.send(('gen', nid, p), results)

    # ---- run
    def run(self):
        try:
            return self.run1()
        finally:
            self.cleanup()

    def run1(self):
        self.start()
        if self.best.result.exact:
            print('already exact'); return 0
        pool = permrun.Pool(self.runner, self.target, self.opts, self.stem, self.jobs, os.path.join(self.work, 'pool'), self.gen)
        results = queue.Queue()
        pending = {}
        nid = nres = 0
        t_start = self.t_run = time.time()
        cpu0 = time.process_time()
        last_report = t_start
        done_exact = False
        self.pool = pool
        try:
            if self.sweep_on and '--resume' not in self.a:
                self.sweep(t_start)
                done_exact = self.best.result.exact and not self.best.result.neighbours
                if '--sweep-only' in self.a: done_exact = True
            else:
                nid = self.simplify_islands(results, pending, nid)
            while True:
                c0 = time.perf_counter()
                while (len(pending) < 2 * self.jobs and not done_exact and time.time() - t_start <= self.tlimit
                       and self.counts['submitted'] < self.iters):
                    self.submit(nid, pending, results)
                    nid += 1
                self.t_submit += time.perf_counter() - c0
                if not pending: break
                try:
                    res = results.get(timeout=5)
                except queue.Empty:
                    # a job whose worker died never answers: forget it after ten compile time
                    # limits, so the run goes on (and can end)
                    lost = [n for n, q in pending.items() if time.time() - q.t > 10 * permrun.TIMEOUT]
                    for n in lost: del pending[n]
                    self.counts['lost'] += len(lost)
                    continue
                c0 = time.perf_counter()
                p = pending.pop(res['id'], None)
                if p is not None:
                    done_exact = self.consider(p, res) or done_exact
                    nres += 1
                    if nres % 64 == 0: self.push_weights()
                    if nres % 256 == 0: self.gc_bases(pending)
                self.t_result += time.perf_counter() - c0
                if time.time() - last_report > 60:
                    last_report = time.time()
                    self.report_line(time.time() - t_start)
                    self.save_state()
            el = time.time() - t_start
            self.cpu = time.process_time() - cpu0
            if '--no-reduce' not in self.a and self.best.text != self.text:
                if self.best.moves and getattr(self.best, 'reduced', False):
                    with open(os.path.join(self.out, 'reduced.c'), 'w', encoding='latin1') as f: f.write(self.best.text)
                    print(f'\nreduced over moves: {len(self.best.moves)} kept, in {os.path.join(self.out, "reduced.c")}')
                else:
                    self.reduce(pool)
        finally:
            pool.close()
            self.log.close()
        self.save_state()
        self.final_report(el)
        return 0 if self.best.result.exact else 1

    def cleanup(self):
        """Stop the parent's own compiler runner (its Wine prefix) and delete the work folder."""
        import shutil
        try:
            self.runner.stop()
        except Exception:
            pass
        shutil.rmtree(self.work, ignore_errors=True)

    # ---- the sweep: every single edit of the original, then pairs of the best of them
    def sweep_list(self):
        """[(tier, rule id, Edit, is a simplify form, family)] of the single edits of the
        original, in the order they are tried (list_singles per rule, one edit once). Nothing
        here depends on --seed. (The run itself has the workers list them, a rule per job.)"""
        ctx = self.origin_ctx
        out, seen = [], set()
        for rid, simp, items in ([('normalise', True, simplify_singles(ctx))] if self.simplify > 0 else []) + \
                [(r.id, False, list_singles(ctx, r, self.sweep_max, self.base_weights.get(r.id, r.weight))) for r in self.rules]:
            for tier, ed, fam in items:
                key = span_key(ed)
                if key in seen: continue
                seen.add(key)
                out.append((tier, rid, ed, simp, fam))
        return sweep_order(out)

    def pump(self, lists, deadline, **kw):
        """{index: result} of move lists replayed on the original, compiled and scored in the
        workers, a window of them at a time, until all are done, the deadline passes or one is
        exact with its neighbours unchanged."""
        q = queue.Queue()
        out, nxt, flying, stop = {}, 0, 0, False
        c = self.counts
        while True:
            while nxt < len(lists) and flying < 4 * self.jobs and not stop and time.time() < deadline:
                job = ('moves', nxt, dict(kw, moves=lists[nxt], dead=True, mark=True))
                self.pool.pool.apply_async(permrun._worker_gen, (job,), callback=q.put,
                                           error_callback=lambda e, n=nxt: q.put({'id': n, 'err': repr(e)}))
                nxt += 1; flying += 1
            if not flying: break
            try:
                res = q.get(timeout=10 * permrun.TIMEOUT)
            except queue.Empty:
                break
            flying -= 1
            out[res['id']] = res
            c['compile_s'] += res.get('tc', 0.0); c['score_s'] += res.get('ts', 0.0); c['wgen_s'] += res.get('tg', 0.0)
            r = res.get('r')
            if r is None:
                if not res.get('none') and 'err' not in res: c['failed'] += 1
                continue
            c['compiled'] += 1; c['sweep_compiled'] += 1
            if res.get('h'): self.seen_new.append(res['h'])
            if res.get('ohash'): self.obj_seen.add(res['ohash'])
            if r.ok and r.exact and not r.neighbours: stop = True
        return out

    def sweep(self, t_start):
        """The systematic first stage: for each start in turn (the original, then each seed
        source kept), each with an equal part of the sweep's time; then for each base made by
        removing dead constructs of the original (debt_roots), which share --debt-share."""
        T = self.tlimit if self.tlimit < 1e8 else float('inf')
        n = len(self.roots)
        for root in self.roots:
            if self.done(): break
            self.sweep_root(root, t_start if root is self.roots[0] else time.time(),
                            (self.sweep_share * T / n, self.pair_share * T / n, self.beam_share * T / n))
        for root in self.debt:
            if self.done(): break
            sl = self.debt_share * T / len(self.debt)
            self.sweep_root(root, time.time(), (0.7 * sl, 0.15 * sl, 0.15 * sl))
        self.t_best_sweep = self.t_best

    def done(self):
        return self.best.result.exact and not self.best.result.neighbours

    def debt_roots(self):
        """Bases made by removing what the original carries as dead code, one construct at a time
        (each probe, each dead or repeated store), all of one kind together and all together
        (permrules' probe_remove and remove_dead). A source matched with probes can be a dead end:
        its probes fit the code around them, and the real source is reached from the text
        without them, which scores worse at first. Each removal that compiles and changes
        FUNC's code is a base with its own sweep, however it scores (at most --debt-bases)."""
        if self.debt_max <= 0: return []
        ctx, toks = self.origin_ctx, self.origin_toks
        eds, seen = [], set()

        def add(rule, ed):
            key = span_key(ed) if ed is not None else None
            if key is not None and key not in seen:
                seen.add(key); eds.append((rule, ed))
        try:
            for it in R.r_probe_remove.enumerate(ctx): add('probe_remove', it[1])
        except Exception:
            pass
        try:
            for nd in R.dead_stores(ctx) + R.repeated_stores(ctx):
                add('remove_dead', R.Edit([(nd.start, nd.end, [])], 'remove the dead store ' + ' '.join(ctx.src(nd.start, nd.end).split())[:60], meaning='probe'))
        except Exception:
            pass
        for k in range(24):
            try:
                add('remove_dead', R.r_remove_dead(ctx, random.Random(f'debt:{k}'), everywhere=k % 2 == 0))
            except Exception:
                pass
        out, objs = [], {self.origin.ohash}
        words = [t[1] for t in toks]
        for rule, ed in eds:
            if len(out) >= self.debt_max: break
            try:
                nt = R.apply(toks, ed.spans)
            except ValueError:
                continue
            text = permc.render(nt, self.tail)
            raw, log = self.compile(text)
            if raw is None: continue
            r = self.target.score(raw)
            if not r.ok or r.chash in objs: continue
            objs.add(r.chash)
            isl = Island(-30 - len(out), text, r, [(rule, ed.desc)], self.key_of(r, self.dead_delta(nt), 1), r.chash)
            isl.moves, isl.h, isl.bh = [make_move(rule, ed.desc, toks, ed, words)], norm_hash(nt), self.put_base(text)
            isl.ridx, isl.name = 100 + len(out), 'the base without dead code: ' + ed.desc[:110]
            self.seen.add(isl.h)
            out.append(isl)
        if out:
            print(f'  bases without dead code: {len(out)} (each swept as a start, whatever it scores):', flush=True)
            for x in out: print(f'    {x.result.text():>28}  {x.trail[0][1][:130]}', flush=True)
        return out

    def sweep_root(self, root, t_start, spans, depth=0):
        """The sweep of one start. Singles: every single edit of it (sweep_list) is compiled and
        scored. Pairs: the best --pairs singles that changed the object (one per distinct
        object, improving ones first) are applied two at a time; then the beam. The best result
        becomes the best; the start's islands that are not strict start from the best distinct
        results, its strict ones stay on the start. When the best result has half the start's
        count or less, the sweep is made again from it (--resweep levels, half the time each).
        An exact result ends the run. spans: the seconds for singles, pairs and the beam."""
        t0 = time.time()
        P = list(root.moves)                   # the moves that make this start, before every move list
        if len(self.roots) + len(self.debt) > 1 or depth:
            print(f'  sweep of {root.name} (start {root.result.text()}):', flush=True)
        d_single = t_start + spans[0]
        d_pair = d_single + spans[1]
        d_third = d_pair + spans[2]
        # the workers list the singles, a rule per job (the compound rules take seconds on a
        # large function); one edit is kept once, for the first rule that makes it
        jobs = ([None] if self.simplify > 0 else []) + [r.id for r in self.rules]
        asyncs = [self.pool.pool.apply_async(permrun._worker_gen, (('list', n, {'rule': rid, 'max': self.sweep_max, 'root': root.ridx, 'rmoves': P,
                  'weight': self.base_weights.get(rid, 1.0)}),)) for n, rid in enumerate(jobs)]
        items, seen = [], set()
        for a in asyncs:
            try:
                res = a.get(20 * permrun.TIMEOUT)
            except Exception as e:
                res = {'err': repr(e)}
            if 'err' in res:
                self.counts['errors'] += 1
                if self.counts['errors'] <= 5: print('  worker error while listing: ' + res['err'] + '\n' + res.get('trace', ''), flush=True)
            for it in res.get('items') or []:
                if it[5] in seen: continue
                seen.add(it[5]); items.append(it)
        recs = [{'rule': rid, 'tier': tier, 'desc': desc, 'simp': simp, 'fam': fam, 'moves': [mv]}
                for tier, rid, desc, simp, fam, key, mv in sweep_order(items)]
        print(f'  sweep: {len(recs)} single edits listed in {time.time() - t0:.1f} s '
              f'({sum(1 for x in recs if x["tier"] == 0)} in the first pass; by tier: '
              + ', '.join(f'{t:g}: {n}' for t, n in sorted(collections.Counter(x["tier"] for x in recs).items())) + ')', flush=True)

        class Lazy(list):                      # the move lists of a selection of the singles
            def __getitem__(me, n):
                return P + recs[list.__getitem__(me, n)]['moves']
        k0, o0 = root.key, root.ohash
        # first the first pass and the probes' first tiers; then the rest, the families (a shift
        # probe's constant, a comparison's operand) of which a member improved the score first
        # a base without dead code is not swept to put probes back first: its first stage is the
        # first pass alone, and the probe tiers come after the site subsets
        debt = any(root is x for x in self.debt)
        early = [n for n, x in enumerate(recs) if x['tier'] < (1 if debt else 2)]
        got = self.pump(Lazy(early), d_single)
        res = {early[n]: rr for n, rr in got.items()}
        fams, whole = {}, {}
        for n, rr in res.items():
            f, r = recs[n]['fam'], rr.get('r')
            if not f or r is None or not r.ok: continue
            whole[f] = min(whole.get(f, rr['k']), rr['k'])
            if rr['k'] < k0: fams[f] = min(fams.get(f, rr['k']), rr['k'])
        late = [n for n, x in enumerate(recs) if x['tier'] >= (1 if debt else 2)]

        def rank(n):
            x = recs[n]
            f = x['fam']
            if f in fams: return (0, fams[f])                              # a member improved the start
            if f in whole and f.startswith('sites of '): return (1, whole[f])   # site subsets: the families whose whole edit scored best first
            return (2, x['tier']) if debt else (2,)
        late.sort(key=rank)
        if fams: print('  sweep: families tried first after the first tiers: ' + ', '.join(sorted(fams, key=fams.get)[:12])[:400], flush=True)
        if not any(rr.get('r') is not None and rr['r'].ok and rr['r'].exact and not rr['r'].neighbours for rr in res.values()):
            got = self.pump(Lazy(late), d_single)
            res.update({late[n]: rr for n, rr in got.items()})
        per = collections.defaultdict(collections.Counter)
        done = []
        for n, x in enumerate(recs):
            st = per[x['rule']]
            st['listed'] += 1
            rr = res.get(n)
            if rr is None: continue
            r = rr.get('r')
            if r is None or not r.ok:
                st['dup' if rr.get('dupe') else 'failed'] += 1; continue
            x.update(k=rr['k'], r=r, ohash=rr['ohash'], h=rr.get('h'), d=rr.get('d'))
            st['compiled'] += 1
            if x['ohash'] != o0: st['changed'] += 1
            if x['k'] < k0: st['improved'] += 1; self.stats[x['rule']]['sweep_improved'] += 1
            self.stats[x['rule']]['sweep'] += 1
            if 'best' not in st or x['k'] < st['best'][0]: st['best'] = (x['k'], r.text(), x['desc'])
            done.append(x)
        el = time.time() - t0
        print(f'  sweep: {len(done)} of {len(recs)} singles compiled in {el:.0f} s'
              + ('' if len(res) >= len(recs) else f' (time for {len(res)}: {spans[0]:.0f} s of --time; the rest not tried)')
              + f'; {sum(1 for x in done if x["ohash"] != o0)} changed the object, {sum(1 for x in done if x["k"] < k0)} improved', flush=True)
        print('  sweep rule         listed  compiled  failed  changed  improved  best')
        for rid, st in sorted(per.items(), key=lambda kv: (kv[1]['best'][0] if 'best' in kv[1] else (9,), kv[0])):
            b = st.get('best')
            print(f'    {rid:16} {st["listed"]:6} {st["compiled"]:9} {st["failed"]:7} {st["changed"]:8} {st["improved"]:9}  '
                  + (f'{b[1]}  {b[2][:90]}' if b else '-'), flush=True)
        done.sort(key=lambda x: x['k'])
        with open(os.path.join(self.out, 'sweep.tsv'), 'w' if root.ridx == 0 else 'a') as f:
            f.write('stage\trule\ttier\tscore\tkey\tobject\tedit\n')
            for x in done:
                f.write(f'single\t{x["rule"]}\t{x["tier"]}\t{x["r"].text()}\t{x["k"]}\t{x["ohash"]}\t{" ".join(x["desc"].split())}\n')
        exact = [x for x in done if x['r'].exact and not x['r'].neighbours]
        pairs = []
        if not exact and self.pair_top > 1:
            top, objs = [], set()
            for x in done:                     # sorted by key: improving ones first, then the nearest
                if x['ohash'] == o0 or x['ohash'] in objs: continue
                objs.add(x['ohash']); top.append(x)
                if len(top) >= self.pair_top: break
            nt = len(top)
            # and each removal of something the original has (a probe, the dead constructs, a
            # normalised form) with the best --pair-removals distinct singles: the removal alone
            # often scores no better, and what replaces the removed thing only pays with it
            rem, robj = [], set()
            for x in sorted((x for x in done if (x['rule'] in ('probe_remove', 'remove_dead') or x['simp']) and x['ohash'] != o0),
                            key=lambda x: (x['rule'] != 'probe_remove', x['k'])):
                if x['ohash'] not in robj and len(rem) < 6:      # single probes first, one per distinct object
                    robj.add(x['ohash']); rem.append(x)
            wide, objs = list(top), {x['ohash'] for x in top}
            for x in done:
                if len(wide) >= self.pair_rem: break
                if x['ohash'] == o0 or x['ohash'] in objs: continue
                objs.add(x['ohash']); wide.append(x)
            top = wide + [x for x in rem if not any(x is y for y in wide)]
            rset = {n for n, x in enumerate(top) if any(x is y for y in rem)}
            pset = {(a, b) for a in range(nt) for b in range(a + 1, nt)}
            pset |= {(min(a, b), max(a, b)) for a in rset for b in range(len(wide)) if a != b}
            # best first: by the ranks of the two singles (a removal counts as rank 0)
            pl = sorted(pset, key=lambda ab: ((0 if ab[0] in rset else ab[0]) + (0 if ab[1] in rset else ab[1]), ab))
            lists = [P + top[a]['moves'] + top[b]['moves'] for a, b in pl]
            t1 = time.time()
            res = self.pump(lists, d_pair, either=True)
            for n, (a, b) in enumerate(pl):
                rr = res.get(n)
                r = rr.get('r') if rr else None
                if r is None or not r.ok: continue
                mv = top[b]['moves'] + top[a]['moves'] if rr.get('swapped') else top[a]['moves'] + top[b]['moves']
                pairs.append({'rule': top[a]['rule'] + '+' + top[b]['rule'], 'tier': -1, 'desc': top[a]['desc'] + ' | ' + top[b]['desc'],
                              'moves': mv, 'k': rr['k'], 'r': r, 'ohash': rr['ohash'], 'h': rr.get('h'), 'd': rr.get('d'), 'simp': False,
                              'parents': min(top[a]['k'], top[b]['k'])})
            pairs.sort(key=lambda x: x['k'])
            print(f'  pairs: the {nt} best distinct singles two at a time, and {len(rem)} removals each with the best {len(wide)}: {len(pairs)} of {len(pl)} compiled in {time.time() - t1:.0f} s'
                  + ('' if len(res) >= len(pl) else f' (time for {len(res)})')
                  + f'; {sum(1 for x in pairs if x["k"] < x["parents"])} better than both their singles, {sum(1 for x in pairs if x["k"] < k0)} better than the start', flush=True)
            # further edits, a beam: the best --beam distinct results of a level, each with each of
            # the best 60 singles as one more edit, while a level betters the one before
            thirds, prev, level = [], pairs, 2
            ext = wide[:max(nt, 60)]
            while (prev and self.beam > 0 and level < 6 and time.time() < d_third
                   and not any(x['r'].exact and not x['r'].neighbours for x in prev)):
                bp, objs = [], set()
                for x in prev:
                    if x['ohash'] in objs or x['k'] >= k0: continue
                    objs.add(x['ohash']); bp.append(x)
                    if len(bp) >= self.beam: break
                cand = [(p, x) for x in ext for p in bp if not any(m is x['moves'][0] for m in p['moves'])]
                if not cand: break
                t2 = time.time()
                res = self.pump([P + p['moves'] + x['moves'] for p, x in cand], d_third)
                cur = []
                for n, (p, x) in enumerate(cand):
                    rr = res.get(n)
                    r = rr.get('r') if rr else None
                    if r is None or not r.ok: continue
                    cur.append({'rule': p['rule'] + '+' + x['rule'], 'tier': -level, 'desc': p['desc'] + ' | ' + x['desc'],
                                'moves': p['moves'] + x['moves'], 'k': rr['k'], 'r': r, 'ohash': rr['ohash'], 'h': rr.get('h'),
                                'd': rr.get('d'), 'simp': False, 'parents': p['k']})
                level += 1
                cur.sort(key=lambda x: x['k'])
                gain = bool(cur) and cur[0]['k'] < bp[0]['k']
                print(f'  {level} edits: the {len(bp)} best distinct results of {level - 1} edits each with one of the best {len(ext)} singles: '
                      f'{len(cur)} of {len(cand)} compiled in {time.time() - t2:.0f} s'
                      + ('' if len(res) >= len(cand) else f' (time for {len(res)})')
                      + f'; {sum(1 for x in cur if x["k"] < x["parents"])} better than what they extend'
                      + (f', best {cur[0]["r"].text()}' if cur else ''), flush=True)
                thirds += cur
                if not gain: break
                prev = cur
            with open(os.path.join(self.out, 'sweep.tsv'), 'a') as f:
                for x in pairs + thirds:
                    f.write(f'{1 - x["tier"]} edits\t{x["rule"]}\t\t{x["r"].text()}\t{x["k"]}\t{x["ohash"]}\t{" ".join(x["desc"].split())}\n')
            pairs += thirds
        allr = sorted(done + pairs, key=lambda x: x['k'])
        for x in allr[:8]:
            what = 'single ' if x['tier'] >= 0 else '%d edits' % (1 - x['tier'])
            print(f'    {what} {x["r"].text():>24}  [{x["rule"]}] {x["desc"][:140]}', flush=True)
        self.swept.add(root.bh)
        self.counts['sweep_s'] = time.time() - t0

        def fetch(x, k):
            """An island at the result x (its text asked of a worker), or None."""
            kk, rr = self.score_moves([P + x['moves']], want=True)[0]
            if kk is None or rr.get('text') is None: return None
            isl = Island(k, rr['text'], rr['r'], list(root.trail) + [(x['rule'], x['desc'])], kk, rr['ohash'])
            isl.moves, isl.h, isl.hot, isl.bh = P + list(x['moves']), rr.get('h'), rr.get('hot'), self.put_base(rr['text'])
            if isl.result.key() < self.low[0]: self.set_low(isl.result, isl.text, len(isl.moves))
            return isl
        # the best becomes the best; the even islands take the best distinct results
        seeds, objs = [], set()
        for x in allr:
            if x['k'] >= k0 or len(seeds) >= max(1, sum(1 for i in self.islands if i.root is root and not i.strict)): break
            if x['ohash'] in objs: continue
            objs.add(x['ohash']); seeds.append(x)
        even = [i for i in self.islands if i.root is root and not i.strict]
        got = []
        for n, x in enumerate(seeds):
            isl = fetch(x, -1)
            if isl is None: continue
            got.append(isl)
            if isl.key < self.best.key:
                if self.new_best(isl.text, isl.result, isl.key, isl.ohash, isl.trail, isl.moves, isl.h, isl.hot, x['rule'], x['desc']) is None:
                    isl = self.best
            if n < len(even): even[n].take(isl)
            elif not any(i.root is root for i in self.islands):
                # a base with no islands of its own: its result takes the worst island that is
                # not strict, when it is better than that
                worst = max((i for i in self.islands if not i.strict), key=lambda i: i.key, default=None)
                if worst is not None and isl.key < worst.key: worst.take(isl)
        bs = min((x for x in done), key=lambda x: x['k'], default=None)
        print(f'  {"sweep" if root is self.origin else "base"} result: start {root.result.text()}; best single '
              + (bs['r'].text() if bs else '-') + '; best after pairs and beam ' + (allr[0]['r'].text() if allr else '-')
              + (f'  [{allr[0]["rule"]}] {allr[0]["desc"][:100]}' if allr else ''), flush=True)
        # again from the best, when it is a different place: half the start's count or less
        if (depth < self.resweep and got and not self.done() and got[0].result.ok and root.result.ok
                and got[0].key < k0 and got[0].result.diff * 2 <= root.result.diff):
            child = got[0]
            self.nchild += 1
            child.ridx, child.name = 1000 + self.nchild, f'the best of the sweep before ({len(child.moves)} moves)'
            self.seen.add(child.h)
            self.sweep_root(child, time.time(), tuple(x / 2 for x in spans), depth + 1)
        # restarts of the even islands go to a normalised form of the original, as before
        simp, objs = [], set()
        for x in done:
            if x['simp'] and x['ohash'] not in objs and len(simp) < len(even):
                objs.add(x['ohash']); simp.append(x)
        for n, x in enumerate(simp):
            home = fetch(x, -3)
            if home is None: continue
            even[n].home = home
            if n >= len(seeds):                # no better start: the simplified form, as before
                even[n].take(home)
                print(f'  simplify-first island {even[n].k}: {x["desc"][:160]}', flush=True)

    def consider(self, p, res):
        """One result from a worker: statistics, then whether the island keeps the variant and
        whether it is a new best. True when it ends the run (exact, neighbours unchanged)."""
        c = self.counts
        isl = self.anchors[-100 - p.isl] if p.isl <= -100 else self.islands[p.isl]
        for (rid, name), n in (res.get('st') or {}).items(): self.stats[rid][name] += n
        c['generated'] += res.get('gen', 0); c['dup_text'] += res.get('dup', 0)
        c['wgen_s'] += res.get('tg', 0.0); c['compile_s'] += res.get('tc', 0.0); c['score_s'] += res.get('ts', 0.0)
        if 'th' in res: c['focus_compiles'] += 1; c['focus_s'] += res['th']
        self.escalate()
        if 'err' in res:
            c['errors'] += 1
            if c['errors'] <= 5: print('  worker error: ' + res['err'] + ('\n' + res['trace'] if res.get('trace') else ''), flush=True)
            return False
        if res.get('none'):                            # no rule found a site, or every text was a duplicate
            c['nogen'] += 1
            return False
        trail = res.get('trail') or p.vtrail or []
        if res.get('h'): self.seen_new.append(res['h'])
        self.keep_sdiff(res)
        c['made'] += 1
        rules = '+'.join(t[0] for t in trail)
        desc = ' | '.join(t[1] for t in trail)
        ids = [t[0].rstrip('*') for t in trail]
        for rid in ids: self.stats[rid]['tried'] += 1
        r, log = res.get('r'), res.get('log', '')
        if r is not None and not r.ok:                 # compiled, but FUNC could not be scored
            log, r = 'not scored: ' + r.error, None
        if r is None:
            c['failed'] += 1
            for rid in ids: self.stats[rid]['failed'] += 1
            self.log.write(f'{time.time():.0f}\t{p.isl}\t{rules}\tFAILED\t\t\t{desc}\t{log.splitlines()[0] if log else ""}\n')
            return False
        c['compiled'] += 1
        ohash = res['ohash']
        if ohash in self.obj_seen: c['dup_obj'] += 1
        self.obj_seen.add(ohash)
        for rid in ids: self.stats[rid]['compiled'] += 1
        if ohash == isl.ohash:                          # the code did not change
            for rid, (_, dsc, _) in zip(ids, trail):
                self.noop[(rid, dsc)] += 1; self.stats[rid]['noop'] += 1
                if self.noop[(rid, dsc)] == self.tabu_after: self.tabu.add((rid, dsc))
        k, d, text = res['k'], res.get('d'), res.get('text')
        old, bkey = isl.key, self.best.key
        strict = isl.strict              # strict islands keep only improvements: they stay near
        accepted = k < old               # their base, where a one- or two-edit answer is found;
        if not accepted and not strict:  # drifting islands collect neutral edits
            accepted = k == old or (p.margin is not None and k[:-1] == old[:-1] and k[-1] - old[-1] <= p.margin)
        better = k < old
        if p.force: accepted = True
        if better:
            for rid in ids: self.stats[rid]['improved'] += 1
        self.log.write(f'{time.time():.0f}\t{p.isl}\t{rules}\t{r.diff}\t{r.size:#x}\t{"acc" if accepted else ""}\t{desc}\t{ohash}\n')
        if d is not None and text is not None:
            dq = round(d * 4) / 4
            f = self.front.get(dq)
            if f is None or k < f[0]:                  # screened when reported, not here (clang
                self.front[dq] = (k, text, r)          # in the parent would starve the workers)
        if text is not None and r.key() < self.low[0]:
            # the lowest count seen whatever it took: kept apart, because with the per-move
            # penalty the best is the best for its length, not the lowest count
            self.set_low(r, text, len(p.moves) + len(res.get('moves') or p.vmoves or ()))
        newbest = k < bkey
        if (newbest or accepted) and text is None:
            # the island or the best moved on after the job went out and the worker judged the
            # text not worth sending: it is not rebuilt
            c['no_text'] += 1
            newbest = accepted = False
        moves = list(p.moves) + list(res.get('moves') or p.vmoves or ())
        if newbest:
            why = self.new_best(text, r, k, ohash, list(p.trail) + [(rules, desc)], moves, res.get('h'), res.get('hot'), rules, desc)
            if why is not None:
                if why == 'screen':
                    c['screened_out'] += 1
                    for rid in ids: self.stats[rid]['screened_out'] += 1
                self.log.write(f'{time.time():.0f}\t{p.isl}\t{rules}\t{"SCREENED" if why == "screen" else "CUT-ZERO-NOT-WHOLE"}\t\t\t{desc}\n')
                return False
            accepted = True
            for rid in ids: self.stats[rid]['new_best'] += 1
        if isl.anchor:
            # a job from the original: nothing moves the original; a new best goes to the island
            # that has gone longest without progress
            if newbest:
                for rid in ids: self.stats[rid]['accepted'] += 1
                t = max(self.islands, key=lambda x: x.stale)
                t.take(self.best); t.t_gain = time.time()
            return r.exact and not r.neighbours
        if accepted:
            for rid in ids: self.stats[rid]['accepted'] += 1
            if newbest:
                isl.take(self.best)                    # as reduced: the shorter list
                isl.t_gain = time.time()
            else:
                isl.text, isl.result, isl.key, isl.ohash = text, r, k, ohash
                isl.trail = list(p.trail) + [(rules, desc)]
                isl.moves, isl.h, isl.hot, isl.bh = moves, res.get('h'), res.get('hot'), self.put_base(text)
            if p.force: isl.home = isl.copy(-3)
        if better or newbest: isl.stale = 0
        else: isl.stale += 1
        if isl.stale > self.restart:
            # back to the best, or every other time to the original source (neutral edits the
            # best carries can block an answer that is one edit from the original)
            c['restarts'] += 1
            isl.take(self.best if c['restarts'] % 2 else (isl.home or isl.root or self.origin))
        elif self.rebase_s and time.time() - isl.t_gain > self.rebase_s:
            # parsimony: an island that has given no new best for --rebase minutes drops its
            # own drifted text and starts again from the best as reduced
            c['rebases'] += 1
            isl.take(self.best)
            isl.t_gain = time.time()
        return r.exact and not r.neighbours

    def new_best(self, text, r, k, ohash, trail, moves, h, hot, rules='', desc=''):
        """Make a result the best: screened (clang), a cut file's 0 confirmed on the whole unit,
        reduced over its moves, written to OUT. None when kept, else why not ('screen', 'cut')."""
        t0 = time.perf_counter()               # screening and reduction wait for clang and the workers
        try:
            ok, why = self.screen.check(text) if self.screen else (True, [])
            if not ok: return 'screen'
            if r.exact and self.mode.startswith('cut'):
                raw, lg = self.compile(text)
                if raw is None or not self.target.score(raw).exact: return 'cut'
            b = Island(-1, text, r, trail, k, ohash)
            b.moves, b.h, b.hot, b.bh = list(moves), h, hot, self.put_base(text)
            self.best = b
            if '--no-reduce' not in self.a: self.reduce_moves()
            self.write_best()
            rb = self.best.result
            pen = 0 if rb.exact else self.best.key[-1] - rb.key()[-1]       # what the dead-code and move penalties add to the key
            what = rb.text() + (f' +{pen:.2f} in penalties' if pen > 0.005 else '')
            print(f'  new best {what}  ({len(self.best.moves)} moves)  [{rules}] {desc[:150]}', flush=True)
            self.history.append((time.time() - self.t_run, self.counts['compiled'], what, len(self.best.moves)))
            return None
        finally:
            self.t_best += time.perf_counter() - t0

    def keep_sdiff(self, res):
        if res.get('sdiff') is not None and res.get('h'):
            if len(self.sdiffs) > 300: self.sdiffs.clear()
            self.sdiffs[res['h']] = res['sdiff']
            if res.get('text') is not None and res.get('k') is not None and res.get('r') is not None:
                # the residue: which instructions are structurally wrong, without their addresses
                sig = tuple(' '.join(x.split(' ', 2)[::2]) for x in res['sdiff'])
                old = self.residues.get(sig)
                if old is None or res['k'] < old[0]:
                    self.residues[sig] = (res['k'], res['text'], res['r'].text(), len(res.get('moves') or ()), res['sdiff'])

    def write_variants(self):
        """OUT/variants/: the best text of each distinct residue (the set of structural
        differences left), the 24 best of them: near-misses that fail in another place than the
        best does, as candidates for a tree's variants/FUNC folder of seed sources."""
        rows = sorted(self.residues.values(), key=lambda v: v[0])[:24]
        if not rows: return
        d = os.path.join(self.out, 'variants')
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, 'index.txt'), 'w') as idx:
            for n, (k, text, score, nm, lines) in enumerate(rows):
                name = f'{n:02d}_{score.split()[0]}.c'
                with open(os.path.join(d, name), 'w', encoding='latin1') as f: f.write(text)
                idx.write(f'{name}: {score}\n' + ''.join(f'    {x}\n' for x in lines) + '\n')
        print(f'variants: {len(rows)} sources with distinct structural residues in {d}')

    def set_low(self, r, text, nm):
        self.low = (r.key(), r.text(), nm)
        with open(os.path.join(self.out, 'lowest.c'), 'w', encoding='latin1') as f: f.write(text)
        with open(os.path.join(self.out, 'lowest.txt'), 'w') as f:
            f.write(f'{self.func}: {self.low[1]} with {self.low[2]} moves (not reduced)\n')

    def text_job(self, text):
        """The result of text compiled and scored in a worker (waited for)."""
        try:
            return self.pool.pool.apply(permrun._worker_gen, (('text', -1, {'text': text}),))
        except Exception:
            return {}

    def reduce(self, pool):
        """armreduce.py's reduction of the best source to the line hunks that matter: drop each
        hunk in turn, keep the drop when FUNC still scores no worse (exact stays exact)."""
        import armreduce
        a = self.text.split('\n'); b = self.best.text.split('\n')
        limit = self.best.result.key()
        n = [0]

        def ok(lines):
            r = self.text_job('\n'.join(lines)).get('r')
            n[0] += 1
            return r is not None and r.key() <= limit
        ops, keep = armreduce.reduce(a, b, ok)
        if keep is None:
            print('reduce: the best source does not reproduce its score'); return
        lines = armreduce.apply(a, b, ops, keep)
        text = '\n'.join(lines)
        if self.screen:
            good, why = self.screen.check(text)
            if not good:
                print('reduce: the reduced source fails the screen, kept unreduced: ' + '; '.join(why)); return
        with open(os.path.join(self.out, 'reduced.c'), 'w', encoding='latin1') as f: f.write(text)
        print(f'\nreduced to {len(keep)} of {len(ops)} hunks ({n[0]} compiles), in {os.path.join(self.out, "reduced.c")}:')
        for k in sorted(keep):
            _, i1, i2, j1, j2 = ops[k]
            print('  -', '\n    '.join(x.strip() for x in a[i1:i2]) or '(nothing)')
            print('  +', '\n    '.join(x.strip() for x in b[j1:j2]) or '(nothing)')

    def escalate(self):
        """For long unattended runs: every 3000 generated variants, look at the share that were
        duplicate texts. Above 45% the neighbourhood is thinning: go one level deeper (moves of
        up to 3-6 edits), take a fresh seed, shake the rule weights, and send half the islands
        back to the original source. Below 25%, step back a level."""
        g = self.counts['generated']
        if g - self.win[0] < 3000: return
        # the share of valid texts already seen (generated also counts moves with no site)
        made = self.counts['made']
        d_dup = self.counts['dup_text'] - self.win[1]
        dup = d_dup / max(1, d_dup + made - self.win[2])
        self.win = (g, self.counts['dup_text'], made)
        if dup > 0.45 and self.depth < 4:
            self.depth += 1
            self.rng = random.Random(self.rng.random())
            self.weights = {k: v * math.exp(self.rng.gauss(0, 0.6)) for k, v in self.base_weights.items()}
            for isl in self.islands[::2]: isl.take(isl.home or isl.root or self.origin)
            self.counts['escalations'] += 1
            print(f'  escalate: duplicate texts {dup:.0%}, depth {self.depth}, new seed and rule mix', flush=True)
        elif dup > 0.45:
            self.weights = {k: v * math.exp(self.rng.gauss(0, 0.6)) for k, v in self.base_weights.items()}
            self.depth = 1                     # cycle: back to shallow with yet another mix
            print(f'  escalate: duplicate texts {dup:.0%}, depth reset with a new rule mix', flush=True)
        elif dup < 0.25 and self.depth:
            self.depth -= 1
        self.push_weights()

    depth = 0
    nchild = 0
    debt = ()
    t_best_sweep = 0.0
    t_run = 0.0
    win = (0, 0, 0)
    origin = None
    cpu = 0.0

    def write_best(self):
        os.makedirs(os.path.join(self.out, 'best'), exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        tag = 'exact' if self.best.result.exact else f'{self.best.result.diff}'
        notes = self.best_notes()
        with open(os.path.join(self.out, 'best', f'{stamp}_{tag}.c'), 'w', encoding='latin1') as f: f.write(self.best.text)
        with open(os.path.join(self.out, 'best', f'{stamp}_{tag}.txt'), 'w') as f: f.write(notes)
        with open(os.path.join(self.out, 'best.c'), 'w', encoding='latin1') as f: f.write(self.best.text)
        with open(os.path.join(self.out, 'best.txt'), 'w') as f: f.write(notes)

    def best_notes(self):
        b = self.best
        out = [f'{self.func}: {b.result.text()}' + (f' (reduced over moves: {b.reduced_from} -> {len(b.moves)})'
                                                    if getattr(b, 'reduced', False) else '')]
        if b.moves:
            out.append('moves (each one needed, after reduction):' if getattr(b, 'reduced', False) else 'moves:')
            out += [f'  {m["rule"]}\t{m["desc"]}' for m in b.moves]
        if b.hot:
            out.append('source lines the differing instructions come from: ' + ' '.join(str(x) for x in b.hot))
        if self.sdiffs.get(b.h):
            out.append('structural differences (- the image, + this source; registers masked):')
            out += ['  ' + x for x in self.sdiffs[b.h]]
        try:
            ctx = self.gen.make_ctx(permc.tokenize(b.text)[0])
            kept = [ctx.src(n.start, n.end) for n in R.repeated_stores(ctx) + R.dead_stores(ctx)]
            if kept and getattr(b, 'reduced', False):
                out.append('redundant or dead stores that survived reduction (candidate real features):')
                out += ['  ' + ' '.join(x.split()) for x in kept]
        except Exception:
            pass
        out.append('trail of accepted steps:')
        out += [f'  {rules}\t{desc}' for rules, desc in b.trail]
        return '\n'.join(out) + '\n'

    def simplify_islands(self, results, pending, nid):
        """Start a share of the islands from normalised forms of the base (dead constructs
        removed, externs sized, pointers to fields, goto chains to tests, locals inlined,
        else-if flattened): a fitted source hides the plain one. Submitted as forced first jobs
        of those islands; the islands keep them as their home for restarts."""
        if self.simplify <= 0: return nid
        n = max(2, int(round(self.simplify * self.nisl)))
        targets = [i for i in self.islands if not i.strict and i.home is None and (i.root is None or i.root is self.origin)][:n]
        seen = set()
        modes = ['runs', 'run', 'every', 'single']
        for q, isl in enumerate(targets):
            for attempt in range(30):
                rng = random.Random(self.rng.random())
                try:
                    ed = R.r_normalise(self.origin_ctx, rng, full=attempt < 15, imode=modes[(q + attempt) % 4])
                    toks = R.apply(self.origin_toks, ed.spans) if ed else None
                except Exception:
                    toks = None
                if toks is None: continue
                h = norm_hash(toks)
                if h in seen: continue
                seen.add(h)
                moves = [make_move('normalise', ed.desc, self.origin_toks, ed)]
                pending[nid] = Pending(isl.k, isl.bh, [], [], None, True, [('normalise', ed.desc, 'probe')], moves, time.time())
                self.send(('text', nid, {'text': permc.render(toks, self.tail), 'want': True, 'nm': 1}), results)
                print(f'  simplify-first island {isl.k}: {ed.desc[:160]}', flush=True)
                nid += 1
                break
        return nid

    def score_moves(self, lists, **kw):
        """[(key or None, result dict)] of move lists replayed on the original, compiled and
        scored in the workers (all at once, waited for)."""
        jobs = [self.pool.pool.apply_async(permrun._worker_gen, (('moves', -1, dict(kw, moves=m, dead=True)),)) for m in lists]
        out = []
        for j in jobs:
            try:
                res = j.get(900)
            except Exception:
                res = {}
            r = res.get('r')
            self.keep_sdiff(res)
            out.append((res.get('k') if r is not None and r.ok else None, res))
        return out

    def reduce_moves(self):
        """Reduce the new best over its moves: leave out each move in turn (in parallel), then
        all that could go together, else one by one; keep a drop when FUNC scores no worse
        (the per-move penalty makes a shorter list that scores the same better).
        Moves stay whole, so a compound move is never cut into a partial edit."""
        b = self.best
        moves = b.moves
        if len(moves) < 2: return
        limit = b.key
        if self.score_moves([moves], nocompile=True)[0][1].get('h') != b.h:        # the list does not replay
            self.counts['no_replay'] += 1
            if self.counts['no_replay'] <= 3: print('  (the new best\'s moves do not replay from the original: not reduced)', flush=True)
            return

        def lst(keep):
            return [m for m, k in zip(moves, keep) if k]

        keep = [True] * len(moves)
        for _ in range(3):
            idx = [i for i in range(len(moves)) if keep[i]]
            cands = []
            for i in idx:
                kp = list(keep); kp[i] = False
                cands.append(lst(kp))
            ks = self.score_moves(cands)
            good = [i for i, (k, _) in zip(idx, ks) if k is not None and k <= limit]
            if not good: break
            kp = list(keep)
            for i in good: kp[i] = False
            k = self.score_moves([lst(kp)])[0][0]
            if k is not None and k <= limit:
                keep = kp
                continue
            for i in good[:40]:                         # one by one
                kp = list(keep); kp[i] = False
                k = self.score_moves([lst(kp)])[0][0]
                if k is not None and k <= limit: keep = kp
            break
        if all(keep): return
        kept = lst(keep)
        k, res = self.score_moves([kept], want=True)[0]
        text = res.get('text')
        if k is None or k > limit or text is None: return
        if self.screen:
            ok, why = self.screen.check(text)
            if not ok: return
        print(f'  reduced the new best over its moves: {len(moves)} -> {len(kept)}', flush=True)
        b.reduced_from, b.reduced = len(moves), True
        b.text, b.moves, b.key, b.result, b.ohash = text, kept, k, res['r'], res['ohash']
        b.h, b.hot, b.bh = res.get('h'), res.get('hot'), self.put_base(text)

    def report_line(self, el):
        c = self.counts
        print(f'  [{el:6.0f} s] submitted {c["submitted"]} compiled {c["compiled"]} failed {c["failed"]} '
              f'dup text {c["dup_text"]} dup obj {c["dup_obj"]}  best {self.best.result.text()} ({len(self.best.moves)} moves)  '
              f'{c["compiled"] / max(el, 1e-9):.1f}/s  zero-dead best {self.zero_dead()}', flush=True)

    def zero_dead(self):
        """The best score among variants with no dead-code construct added (penalised kinds)."""
        z = [v for d, v in self.front.items() if d <= 0]
        return min(z, key=lambda v: v[0])[2].text() if z else '-'

    def final_report(self, el):
        c = self.counts
        b, r0 = self.best.result, self.base_result
        print(f'\n{self.func}: best {b.text()} after {el:.0f} s, {len(self.best.moves)} moves; start {r0.text()}; '
              f'lowest count seen {self.low[1]} with {self.low[2]} moves' + (' (lowest.c)' if self.low[0] < b.key() else ''))
        print('  (order: exact, then right size with neighbours unchanged by count, then wrong size by size difference and '
              'count; among equals the shorter move list. "best" is the first in that order with the move penalty, '
              '"lowest count" the first without it'
              + ('; the start had the wrong size, so a right-size best can have a higher count than it' if not r0.size_ok and b.size_ok and not b.exact and b.diff > r0.diff else '') + ')')
        for rules, desc in self.best.trail: print(f'    {rules}: {desc}')
        print('\nrule              tried  compiled  failed  improved  accepted  new_best  nosite  screened  noop  tabu   hot  cold')
        for r in sorted(self.rules, key=lambda r: -self.stats[r.id]['tried']):
            s = self.stats[r.id]
            print(f'{r.id:16} {s["tried"]:7} {s["compiled"]:9} {s["failed"]:7} {s["improved"]:9} {s["accepted"]:9} '
                  f'{s["new_best"]:9} {s["nosite"]:7} {s["screened_out"]:9} {s["noop"]:5} {s["tabu"]:5} '
                  f'{s["hot"] + s["exempt"]:5} {s["cold"] + s["cold_kept"]:5}')
        print('\nPareto front (dead-code constructs added against the start, best score with that many):')
        for d in sorted(self.front):
            k, text, r = self.front[d]
            if all(self.front[e][0] > k for e in self.front if e < d):
                ok, why = self.screen.check(text) if self.screen else (True, [])
                with open(os.path.join(self.out, f'best_dead{d:+g}.c'), 'w', encoding='latin1') as fh: fh.write(text)
                print(f'  {d:+g}: {r.text()}   (best_dead{d:+g}.c){"" if ok else "  SCREEN: " + "; ".join(why)[:200]}')
        jobs = self.jobs
        print(f'\nthroughput: generated {c["generated"]} ({c["generated"] / el:.1f}/s), jobs {c["submitted"]}, '
              f'compiled {c["compiled"]} ({c["compiled"] / el:.1f}/s, {c["compiled"] / el / jobs:.2f}/s per worker), '
              f'failed {c["failed"]}, duplicate texts {c["dup_text"]}, duplicate objects {c["dup_obj"]}, '
              f'screened out {c["screened_out"]}, restarts {c["restarts"]}, back to the best {c["rebases"]}, '
              f'jobs with no new text {c["nogen"]}, results whose text was not sent {c["no_text"]}, worker errors {c["errors"]}, jobs lost {c["lost"]}')
        n = max(1, c['compiled'] + c['failed'])
        t_res = self.t_result - (self.t_best - self.t_best_sweep)
        print(f'per variant: parent {1000 * (self.t_submit + t_res) / n:.3f} ms (jobs {1000 * self.t_submit / n:.3f}, '
              f'results {1000 * t_res / n:.3f}; apart from that {self.t_best:.1f} s in all screening and reducing new bests; '
              f'the parent process used {1000 * self.cpu / n:.2f} ms of CPU per variant), '
              f'worker generate {1000 * c["wgen_s"] / n:.1f} ms, compile {1000 * c["compile_s"] / n:.0f} ms, '
              f'score {1000 * c["score_s"] / max(1, c["compiled"]):.2f} ms; mode {self.mode}')
        if self.focus:
            hot = sum(s['hot'] for s in self.stats.values()); ex = sum(s['exempt'] for s in self.stats.values())
            cold = sum(s['cold'] for s in self.stats.values()); ck = sum(s['cold_kept'] for s in self.stats.values())
            print(f'focus: edits at hot lines {hot}, exempt (global) {ex}, elsewhere kept {ck} and dropped {cold}; '
                  f'{c["focus_compiles"]} -g compiles for line rows ({c["focus_s"]:.1f} s of worker time)')
        if self.history:
            print('new bests, by time and compiles (is it still descending?):')
            for sec, n, text, nm in self.history: print(f'  {sec:7.0f} s  {n:8} compiles  {text}, {nm} moves')
            last = self.history[-1]
            print(f'  since the last one: {el - last[0]:.0f} s and {c["compiled"] - last[1]} compiles without a new best')
        self.write_yield()
        self.write_variants()
        with open(os.path.join(self.out, 'rules.json'), 'w') as f:
            json.dump({k: dict(v) for k, v in self.stats.items()}, f, indent=1)

    def write_yield(self):
        """OUT/yield.json: per rule, what its variants gave per compile (changed the code,
        improved an island, entered a best, improved the start as a single edit of the sweep).
        OUT/weights.json: the run's weights scaled by each rule's rate of such gains per compile
        against the average rate (between a quarter and four times; a rule with fewer than 30
        compiles keeps its weight), in the form --weights loads."""
        def gains(s): return s['improved'] + 2 * s['new_best'] + s['sweep_improved']
        tot_c = sum(s['compiled'] + s['sweep'] for s in self.stats.values()) or 1
        avg = (sum(gains(s) for s in self.stats.values()) + 1) / tot_c
        y, w = {}, {}
        for r in self.rules:
            s = self.stats[r.id]
            n = s['compiled']
            m = n + s['sweep']                 # compiles of the rule's variants, the sweep's singles among them
            y[r.id] = {'compiled': n, 'changed_code': n - s['noop'], 'improved': s['improved'], 'accepted': s['accepted'],
                       'new_best': s['new_best'], 'improved_per_compile': round(s['improved'] / n, 5) if n else None,
                       'sweep_singles': s['sweep'], 'sweep_improved': s['sweep_improved'],
                       'gains_per_compile': round(gains(s) / m, 5) if m else None}
            base = self.base_weights.get(r.id, r.weight)
            f = min(4.0, max(0.25, (gains(s) / m + avg * 30 / m) / (avg + avg * 30 / m))) if m >= 30 else 1.0
            w[r.id] = round(base * f, 3)
        with open(os.path.join(self.out, 'yield.json'), 'w') as f: json.dump(y, f, indent=1)
        with open(os.path.join(self.out, 'weights.json'), 'w') as f: json.dump(w, f, indent=1)

    # ---- state
    def save_state(self):
        st = {'func': self.func, 'seed': self.seed, 'counts': dict(self.counts),
              'stats': {k: dict(v) for k, v in self.stats.items()}, 'best_trail': self.best.trail,
              'islands': []}
        for isl in self.islands:
            with open(os.path.join(self.out, f'island{isl.k}.c'), 'w', encoding='latin1') as f: f.write(isl.text)
            st['islands'].append({'k': isl.k, 'trail': isl.trail, 'moves': [{k: v for k, v in m.items() if k != 'sig'} for m in isl.moves],
                                  'home': isl.home.text if isl.home else None})
        st['best_moves'] = [{k: v for k, v in m.items() if k != 'sig'} for m in self.best.moves]
        if self.seen_new:
            with open(os.path.join(self.out, 'seen.txt'), 'a') as f: f.write('\n'.join(self.seen_new) + '\n')
            self.seen_new = []
        with open(os.path.join(self.out, 'state.json'), 'w') as f: json.dump(st, f)

    def load_island(self, k, text, trail, mv):
        """An island from a saved text (compiled and scored here), or None."""
        raw, log = self.compile(text)
        if raw is None: return None
        r = self.target.score(raw)
        toks, _ = permc.tokenize(text)
        h = norm_hash(toks)
        if mv is not None:
            t = apply_all(self.origin_toks, mv)
            if t is None or norm_hash(t) != h: mv = None
        if mv is None: mv = legacy_moves(self.origin_toks, toks)       # an older state, or moves that no longer replay
        isl = Island(k, text, r, trail, self.key_of(r, self.dead_delta(toks), len(mv)),
                     r.chash)
        isl.moves, isl.h, isl.bh = mv, h, self.put_base(text)
        if self.focus and r.ok and not r.exact and r.size_ok: isl.hot = self.hot_parent(text, raw) or None
        return isl

    def load_state(self):
        p = os.path.join(self.out, 'state.json')
        if not os.path.exists(p): return
        with open(p) as f: st = json.load(f)
        if os.path.exists(os.path.join(self.out, 'seen.txt')):
            with open(os.path.join(self.out, 'seen.txt')) as f:
                for h in f.read().split(): self.seen.add(h)
        bp = os.path.join(self.out, 'best.c')
        cands = [bp] + [os.path.join(self.out, f'island{i["k"]}.c') for i in st['islands']]
        for k, path in enumerate(cands):
            if not os.path.exists(path): continue
            with open(path, encoding='latin1') as f: text = f.read()
            saved = None if k == 0 else st['islands'][k - 1]
            isl = self.load_island(k - 1, text, st['best_trail'] if k == 0 else saved['trail'],
                                   st.get('best_moves') if k == 0 else saved.get('moves'))
            if isl is None: continue
            if k == 0:
                self.best = isl
            elif k - 1 < len(self.islands):
                isl.root, isl.strict = self.islands[k - 1].root, self.islands[k - 1].strict
                self.islands[k - 1] = isl
                if saved.get('home'): isl.home = self.load_island(-3, saved['home'], [], None)
        for k, v in st.get('stats', {}).items(): self.stats[k].update(v)
        if self.best.result.key() < self.low[0]: self.low = (self.best.result.key(), self.best.result.text(), len(self.best.moves))
        self.push_weights()
        print(f'  resumed: best {self.best.result.text()} ({len(self.best.moves)} moves)', flush=True)


def main(argv):
    import config
    path, a = config.pop_config(argv)
    args = [x for x in a if not x.startswith('--')]
    # values of --options are not positional
    vals = {a[i + 1] for i, x in enumerate(a) if x.startswith('--') and i + 1 < len(a) and not a[i + 1].startswith('--')
            and x not in FLAGS}
    args = [x for x in args if x not in vals]
    if len(args) < 2: sys.exit(__doc__)
    cfg = config.load(path)
    return Search(cfg, args[0], args[1], a).run()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
