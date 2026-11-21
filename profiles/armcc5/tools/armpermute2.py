"""Fast source permuter for one function: island search over catalogue rewrite rules, with
real compiles in a worker pool and an in-process scorer.

    python3 armpermute2.py [--config PATH] SRC.c FUNC [--runner native|wine] [--jobs N]
        [--islands K] [--iters N] [--time SECONDS] [--seed S] [--resume] [--out DIR]
        [--weights FILE.json] [--only RULE,...] [--skip RULE,...] [--cut auto|on|off]
        [--armcc-home DIR] [--mappings DIR] [--exec CMD] [--no-screen] [--pair P] [--everywhere P]

What it does, per variant: pick an island (an independent chain with its own base source),
pick a rule by weight (permrules.RULES: the edit kinds that produced matches, both ways) and
apply it once, at every site of what it chose (--everywhere probability), or two rules at
distant sites as one step (--pair probability); skip the text if its normalised form (tokens
without whitespace) was seen; compile it in a worker (permrun: one armcc process per variant,
one worker per job, work files in /dev/shm) and score FUNC in the worker (permscore: object
read in-process, FUNC placed at its map address; 0 only for equal bytes). The island keeps a
variant that is no worse, now and then one a little worse (--temp), and goes back to the best
after --restart rounds without progress. Order of goodness: exact; right size and unchanged
neighbours, by differing instructions; then wrong size, by size difference and instructions.
So a lower count at the wrong size is never progress.

A new best is screened before it is kept (permscreen: clang, no new unsequenced or
uninitialised warnings) and written to OUT/best.c with its edit trail; a score of 0 stops the
run. Every attempt is logged to OUT/attempts.tsv (island, rules, sites, result, size); the end
of a run prints per-rule statistics (tried, compiled, improved, accepted) and the throughput.
State (island bases, best, seen hashes, rule statistics) is kept in OUT/state.json for --resume;
every new best also goes to OUT/best/<time>_<score>.c with its edit list. Long runs escalate by
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

OUT defaults to <build>/permute2/FUNC. SRC is never modified. Rerun match.py on any result."""
import collections, hashlib, json, math, os, queue, random, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
for p in (HERE, os.path.join(EXHUME, 'tools')):
    if p not in sys.path: sys.path.insert(0, p)
import permc, permrules as R, permrun, permscore, permscreen, orcobj        # noqa: E402


def arg(a, name, default=None, conv=str):
    if name in a:
        i = a.index(name)
        return conv(a[i + 1])
    return default


def norm_hash(toks):
    return hashlib.blake2b(' '.join(t[1] for t in toks).encode('latin1'), digest_size=12).hexdigest()


# ---------------------------------------------------------------- start-up helpers

def array_sizes(runner, cfg, src, text, toks, types, target_map, work):
    """{name: N} for `extern T x[];` arrays whose map row gives their size: sizeof(T) comes from
    one probe compile of the file with a table of sizeof()s appended."""
    cands = []
    for name, (ty, dims, extern) in types.globals.items():
        if extern and dims and dims.replace(' ', '') == '[]' and name in target_map.by_name:
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
        row = target_map.by_name[name].size
        if sz and row % sz == 0 and row // sz > 0: out[name] = row // sz
    return out


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
    bodies = {}
    for i in range(len(toks) - 1):
        if toks[i][0] == 'id' and toks[i + 1][1] == '(':
            try:
                s, lb, rb = permc.find_function(toks, toks[i][1])
                bodies.setdefault(toks[i][1], (lb, rb))
            except LookupError:
                pass
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


# ---------------------------------------------------------------- the search

class Island:
    def __init__(self, k, text, toks, ctx, result, trail):
        self.k, self.text, self.toks, self.ctx, self.result, self.trail = k, text, toks, ctx, result, trail
        self.stale = 0
        self.best_key = result.key()


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
        self.temp = arg(a, '--temp', 0.6, float)
        self.dead_penalty = arg(a, '--dead-penalty', 0.0, float)
        self.noop = collections.Counter()      # (rule, site) -> variants that left the code as it was
        self.tabu_after = arg(a, '--tabu', 2, int)
        self.restart = arg(a, '--restart', 400, int)
        self.screen_on = '--no-screen' not in a
        kind = arg(a, '--runner', os.environ.get('EXHUME_PERMUTE_RUNNER', 'native'))
        self.runner = permrun.make_runner(kind, cfg, {'armcc_home': arg(a, '--armcc-home'),
                                                      'mappings': arg(a, '--mappings'), 'exec': arg(a, '--exec')})
        self.opts = permrun.source_opts(cfg, self.src, self.text)
        self.work = os.path.join(permrun.ram_dir(), f'pmt-{os.getpid()}')
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
        self.seen = set()
        self.log = open(os.path.join(self.out, 'attempts.tsv'), 'a')

    # ---- start-up
    def start(self):
        t0 = time.time()
        os.makedirs(self.work, exist_ok=True)
        raw, log = permrun.compile_once(self.runner, self.cfg, self.src, self.text, os.path.join(self.work, 'base'))
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
                                 os.path.join(self.work, 'sizes'))
        if self.sizes: print(f'  extern arrays sizeable from the map: {len(self.sizes)}', flush=True)
        self.mode = 'whole'
        cut = arg(self.a, '--cut', 'off')
        if cut != 'off':
            self.mode = self.check_cut(toks, tail, raw, cut)
        ctx = self.make_ctx(toks)
        self.base_result = r0
        self.screen = permscreen.Screen(self.src, self.opts, self.text, self.armcc_include()) if self.screen_on else None
        self.islands = [Island(k, self.text, toks, ctx, r0, []) for k in range(self.nisl)]
        self.best = Island(-1, self.text, toks, ctx, r0, [])
        self.origin = Island(-2, self.text, toks, ctx, r0, [])
        h0 = hashlib.blake2b(raw[0x34:], digest_size=12).hexdigest()
        for x in self.islands + [self.best, self.origin]: x.ohash = h0
        self.seen.add(norm_hash(toks))
        self.dead0 = dead_count(toks, self.func)
        self.front = {}                     # dead-code edits added -> (key, text, result)
        if '--resume' in self.a: self.load_state()

    def armcc_include(self):
        home = arg(self.a, '--armcc-home') or os.environ.get('EXHUME_ARMCC_LINUX')
        return os.path.join(home, 'include') if home else None

    def make_ctx(self, toks):
        ctx = R.Ctx(toks, self.tail, self.func, self.types)
        ctx.array_sizes = self.sizes
        return ctx

    def check_cut(self, toks, tail, whole_raw, cut):
        """The fastest cut form whose FUNC code equals the whole unit's ('prefix': the file up
        to FUNC; 'stub': other bodies as prototypes; or both), else the whole unit."""
        t0 = time.time()
        permrun.compile_once(self.runner, self.cfg, self.src, self.text, os.path.join(self.work, 'cut'))
        best = ('whole', time.time() - t0)
        print(f'  whole unit: {best[1]:.2f} s per compile')
        for form in ('prefix', 'stub', 'prefix+stub'):
            m = self.check_cut1(toks, tail, whole_raw, form)
            if m and m[1] < best[1] * 0.92: best = m
        return best[0]

    def cut_of(self, form, toks):
        if form == 'prefix': return prefix_text(toks, self.tail, self.func)
        if form == 'stub': return cut_text(toks, self.tail, self.func, self.types)
        t, tail = permc.tokenize(prefix_text(toks, self.tail, self.func))
        return cut_text(t, tail, self.func, self.types)

    def check_cut1(self, toks, tail, whole_raw, form):
        text = self.cut_of(form, toks)
        t0 = time.time()
        raw, log = permrun.compile_once(self.runner, self.cfg, self.src, text, os.path.join(self.work, 'cut'))
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

    # ---- variants
    def eff_weight(self, r):
        """The rule's weight scaled by how often it changes the code at all (a bandit over the
        logged outcomes: a rule whose variants mostly compile to the object already in hand
        is proposed less)."""
        s = self.stats[r.id]
        changed = (s['compiled'] - s['noop'] + 5) / (s['compiled'] + 10)
        return self.weights[r.id] * (0.15 + 0.85 * changed)

    def pick_rule(self):
        ws = [self.eff_weight(r) for r in self.rules]
        x = self.rng.random() * sum(ws)
        for r, w in zip(self.rules, ws):
            x -= w
            if x <= 0: return r
        return self.rules[-1]

    def mutate(self, isl):
        """(toks, [(rule, desc, meaning)]) or None."""
        steps = 2 if self.rng.random() < self.p_pair else 1
        if self.depth and self.rng.random() < 0.6:          # escalated: deeper compound moves
            steps = self.rng.randint(2, 2 + self.depth)
        toks, ctx, trail = isl.toks, isl.ctx, []
        for s in range(steps):
            for attempt in range(12):
                r = self.pick_rule()
                every = r.everywhere and self.rng.random() < self.p_every
                try:
                    ed = r.fn(ctx, self.rng, everywhere=every)
                except Exception as e:
                    self.stats[r.id]['crash'] += 1; ed = None
                if ed is None:
                    self.stats[r.id]['nosite'] += 1; continue
                try:
                    nt = R.apply(toks, ed.spans)
                except ValueError:
                    self.stats[r.id]['badedit'] += 1; continue
                if self.noop[(r.id, ed.desc)] >= self.tabu_after:      # tabu: a move that already
                    self.stats[r.id]['tabu'] += 1; continue           # left the code unchanged
                trail.append((r.id + ('*' if every else ''), ed.desc, ed.meaning))
                toks = nt
                break
            else:
                return None
            if s + 1 < steps:
                try:
                    ctx = self.make_ctx(toks)
                except Exception:
                    return None
        return toks, trail

    def job_text(self, toks):
        if self.mode.startswith('cut'):
            return self.cut_of(self.mode.split(':')[1], toks)
        return permc.render(toks, self.tail)

    # ---- run
    def run(self):
        self.start()
        if self.best.result.exact:
            print('already exact'); return 0
        pool = permrun.Pool(self.runner, self.target, self.opts, os.path.splitext(os.path.basename(self.src))[0],
                            self.jobs, os.path.join(self.work, 'pool'))
        results = queue.Queue()
        pending = {}
        nid = 0
        t_start = time.time()
        gen_time = 0.0
        last_report = t_start
        done_exact = False
        try:
            while True:
                elapsed = time.time() - t_start
                stop = elapsed > self.tlimit or self.counts['submitted'] >= self.iters or done_exact
                while not stop and len(pending) < 2 * self.jobs:
                    g0 = time.perf_counter()
                    isl = self.islands[nid % self.nisl]
                    m = None
                    for _ in range(20):
                        m = self.mutate(isl)
                        self.counts['generated'] += 1
                        self.escalate()
                        if m is None: continue
                        h = norm_hash(m[0])
                        if h in self.seen:
                            self.counts['dup_text'] += 1; m = None; continue
                        self.seen.add(h)
                        break
                    if m is None:
                        gen_time += time.perf_counter() - g0
                        nid += 1; continue
                    toks, trail = m
                    text = self.job_text(toks)
                    gen_time += time.perf_counter() - g0
                    pending[nid] = (isl.k, toks, trail, time.time())
                    for rid, _, _ in trail: self.stats[rid.rstrip('*')]['tried'] += 1
                    pool.pool.apply_async(permrun._worker_job, ((nid, text),), callback=results.put,
                                          error_callback=lambda e, n=nid: results.put((n, None, None, 0, 0, repr(e))))
                    self.counts['submitted'] += 1
                    nid += 1
                if not pending: break
                try:
                    res = results.get(timeout=5)
                except queue.Empty:
                    continue
                key, r, ohash, tc, ts, log = res
                isl_k, toks, trail, t_sub = pending.pop(key)
                self.counts['compile_s'] += tc; self.counts['score_s'] += ts
                done_exact = self.consider(isl_k, toks, trail, r, ohash, log) or done_exact
                if time.time() - last_report > 60:
                    last_report = time.time()
                    self.report_line(time.time() - t_start)
                    self.save_state()
            el = time.time() - t_start
            if '--no-reduce' not in self.a and self.best.text != self.text:
                self.reduce(pool)
        finally:
            pool.close()
            self.log.close()
        self.save_state()
        self.final_report(el, gen_time)
        return 0 if self.best.result.exact else 1

    def consider(self, isl_k, toks, trail, r, ohash, log):
        isl = self.islands[isl_k]
        rules = '+'.join(t[0] for t in trail)
        desc = ' | '.join(t[1] for t in trail)
        if r is not None and not r.ok:                 # compiled, but FUNC could not be scored
            log, r = 'not scored: ' + r.error, None
        if r is None:
            self.counts['failed'] += 1
            for rid, _, _ in trail: self.stats[rid.rstrip('*')]['failed'] += 1
            self.log.write(f'{time.time():.0f}\t{isl_k}\t{rules}\tFAILED\t\t\t{desc}\t{log.splitlines()[0] if log else ""}\n')
            return False
        self.counts['compiled'] += 1
        if ohash in self.obj_seen: self.counts['dup_obj'] += 1
        self.obj_seen.add(ohash)
        for rid, _, _ in trail: self.stats[rid.rstrip('*')]['compiled'] += 1
        if ohash == getattr(isl, 'ohash', None):            # the code did not change
            for rid, desc, _ in trail:
                self.noop[(rid.rstrip('*'), desc)] += 1; self.stats[rid.rstrip('*')]['noop'] += 1
        k = r.key()
        if self.dead_penalty:
            # prefer natural edits: each dead-code construct added costs this many instructions
            d = dead_count(toks, self.func) - self.dead0
            k = (k[0], k[1], k[2] + self.dead_penalty * max(0, d)) if k[0] else k
        old = getattr(isl, 'key', None) or isl.result.key()
        accepted = False
        strict = isl.k % 2 == 1          # odd islands keep only improvements: they stay near
        if k < old or (k == old and not strict):   # their base, where a one- or two-edit answer is
            accepted = True                        # found; drifting islands collect neutral edits
        elif not strict and k[0] == old[0] and k[1] == old[1] and self.temp > 0:
            accepted = self.rng.random() < math.exp(-(k[2] - old[2]) / self.temp)
        better = k < old
        if better:
            for rid, _, _ in trail: self.stats[rid.rstrip('*')]['improved'] += 1
        self.log.write(f'{time.time():.0f}\t{isl_k}\t{rules}\t{r.diff}\t{r.size:#x}\t{"acc" if accepted else ""}\t{desc}\t{ohash}\n')
        d = dead_count(toks, self.func) - self.dead0
        f = self.front.get(d)
        if f is None or k < f[0]:                  # screened when reported, not here (clang
            self.front[d] = (k, toks, r)           # in the parent would starve the workers)
        newbest = k < (getattr(self.best, 'key', None) or self.best.result.key())
        if newbest:
            text = permc.render(toks, self.tail)
            ok, why = self.screen.check(text) if self.screen else (True, [])
            if not ok:
                self.counts['screened_out'] += 1
                for rid, _, _ in trail: self.stats[rid.rstrip('*')]['screened_out'] += 1
                self.log.write(f'{time.time():.0f}\t{isl_k}\t{rules}\tSCREENED\t\t\t{desc}\t{"; ".join(why)[:300]}\n')
                return False
            if r.exact and self.mode.startswith('cut'):
                raw, lg = permrun.compile_once(self.runner, self.cfg, self.src, text, os.path.join(self.work, 'confirm'))
                if raw is None or not self.target.score(raw).exact:
                    self.log.write(f'{time.time():.0f}\t{isl_k}\t{rules}\tCUT-ZERO-NOT-WHOLE\n'); return False
            accepted = True
            for rid, _, _ in trail: self.stats[rid.rstrip('*')]['new_best'] += 1
            ctx = self.make_ctx(toks)
            self.best = Island(-1, text, toks, ctx, r, isl.trail + [(rules, desc)])
            self.best.key, self.best.ohash = k, ohash
            self.write_best()
            print(f'  new best {r.text()}  [{rules}] {desc[:150]}', flush=True)
        if accepted:
            for rid, _, _ in trail: self.stats[rid.rstrip('*')]['accepted'] += 1
            if not newbest:
                try:
                    ctx = self.make_ctx(toks)
                except Exception:
                    return False
            isl.text, isl.toks, isl.ctx, isl.result = None, toks, ctx, r
            isl.ohash, isl.key = ohash, k
            isl.trail = isl.trail + [(rules, desc)]
        if better or newbest: isl.stale = 0
        else: isl.stale += 1
        if isl.stale > self.restart:
            # back to the best, or every other time to the original source (neutral edits the
            # best carries can block an answer that is one edit from the original)
            self.counts['restarts'] += 1
            src = self.best if self.counts['restarts'] % 2 or self.origin is None else self.origin
            isl.toks, isl.ctx, isl.result, isl.trail = src.toks, src.ctx, src.result, list(src.trail)
            isl.key, isl.ohash = getattr(src, 'key', None), getattr(src, 'ohash', None)
            isl.stale = 0
        return r.exact and not r.neighbours

    obj_seen = set()
    origin = None

    def reduce(self, pool):
        """armreduce.py's reduction of the best source to the line hunks that matter: drop each
        hunk in turn, keep the drop when FUNC still scores no worse (exact stays exact)."""
        import armreduce
        a = self.text.split('\n'); b = self.best.text.split('\n')
        limit = self.best.result.key()
        n = [0]

        def ok(lines):
            text = '\n'.join(lines)
            if self.mode.startswith('cut'):
                toks, _ = permc.tokenize(text)
                text = self.cut_of(self.mode.split(':')[1], toks)
            _, r, _, _, _, _ = pool.pool.apply(permrun._worker_job, ((-1, text),))
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
        duplicate texts. Above 60% the neighbourhood is exhausted: go one level deeper (moves of
        up to 3-6 edits), take a fresh seed, shake the rule weights, and send half the islands
        back to the original source. Below 25%, step back a level."""
        g = self.counts['generated']
        if g - self.win[0] < 3000: return
        dup = (self.counts['dup_text'] - self.win[1]) / max(1, g - self.win[0])
        self.win = (g, self.counts['dup_text'])
        if dup > 0.6 and self.depth < 4:
            self.depth += 1
            self.rng = random.Random(self.rng.random())
            self.weights = {k: v * math.exp(self.rng.gauss(0, 0.6)) for k, v in self.base_weights.items()}
            for isl in self.islands[::2]:
                src = self.origin
                isl.toks, isl.ctx, isl.result, isl.trail = src.toks, src.ctx, src.result, []
                isl.key, isl.ohash = None, src.ohash
            self.counts['escalations'] += 1
            print(f'  escalate: duplicate texts {dup:.0%}, depth {self.depth}, new seed and rule mix', flush=True)
        elif dup > 0.6:
            self.weights = {k: v * math.exp(self.rng.gauss(0, 0.6)) for k, v in self.base_weights.items()}
            self.depth = 1                     # cycle: back to shallow with yet another mix
            print(f'  escalate: duplicate texts {dup:.0%}, depth reset with a new rule mix', flush=True)
        elif dup < 0.25 and self.depth:
            self.depth -= 1

    depth = 0
    win = (0, 0)

    def write_best(self):
        os.makedirs(os.path.join(self.out, 'best'), exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        tag = 'exact' if self.best.result.exact else f'{self.best.result.diff}'
        with open(os.path.join(self.out, 'best', f'{stamp}_{tag}.c'), 'w', encoding='latin1') as f: f.write(self.best.text)
        with open(os.path.join(self.out, 'best', f'{stamp}_{tag}.txt'), 'w') as f:
            f.write(f'{self.func}: {self.best.result.text()}\n')
            for rules, desc in self.best.trail: f.write(f'{rules}\t{desc}\n')
        with open(os.path.join(self.out, 'best.c'), 'w', encoding='latin1') as f: f.write(self.best.text)
        with open(os.path.join(self.out, 'best.txt'), 'w') as f:
            f.write(f'{self.func}: {self.best.result.text()}\n')
            for rules, desc in self.best.trail: f.write(f'{rules}\t{desc}\n')

    def report_line(self, el):
        c = self.counts
        print(f'  [{el:6.0f} s] submitted {c["submitted"]} compiled {c["compiled"]} failed {c["failed"]} '
              f'dup text {c["dup_text"]} dup obj {c["dup_obj"]}  best {self.best.result.text()}  '
              f'{c["compiled"] / max(el, 1e-9):.1f}/s', flush=True)

    def final_report(self, el, gen_time):
        c = self.counts
        print(f'\n{self.func}: best {self.best.result.text()} after {el:.0f} s')
        for rules, desc in self.best.trail: print(f'    {rules}: {desc}')
        print('\nrule              tried  compiled  failed  improved  accepted  new_best  nosite  screened  noop  tabu')
        for r in sorted(self.rules, key=lambda r: -self.stats[r.id]['tried']):
            s = self.stats[r.id]
            print(f'{r.id:16} {s["tried"]:7} {s["compiled"]:9} {s["failed"]:7} {s["improved"]:9} {s["accepted"]:9} '
                  f'{s["new_best"]:9} {s["nosite"]:7} {s["screened_out"]:9} {s["noop"]:5} {s["tabu"]:5}')
        print('\nPareto front (dead-code constructs added against the start, best score with that many):')
        for d in sorted(self.front):
            k, toks, r = self.front[d]
            if all(self.front[e][0] > k for e in self.front if e < d):
                text = permc.render(toks, self.tail)
                ok, why = self.screen.check(text) if self.screen else (True, [])
                with open(os.path.join(self.out, f'best_dead{d:+d}.c'), 'w', encoding='latin1') as fh: fh.write(text)
                print(f'  {d:+d}: {r.text()}   (best_dead{d:+d}.c){"" if ok else "  SCREEN: " + "; ".join(why)[:200]}')
        jobs = self.jobs
        print(f'\nthroughput: generated {c["generated"]} ({c["generated"] / el:.1f}/s), submitted {c["submitted"]}, '
              f'compiled {c["compiled"]} ({c["compiled"] / el:.1f}/s, {c["compiled"] / el / jobs:.2f}/s per worker), '
              f'failed {c["failed"]}, duplicate texts {c["dup_text"]}, duplicate objects {c["dup_obj"]}, '
              f'screened out {c["screened_out"]}, restarts {c["restarts"]}')
        n = max(1, c['compiled'] + c['failed'])
        print(f'per variant: generate {1000 * gen_time / max(1, c["submitted"]):.2f} ms (parent), compile '
              f'{1000 * c["compile_s"] / n:.0f} ms, score {1000 * c["score_s"] / max(1, c["compiled"]):.2f} ms (worker); mode {self.mode}')
        with open(os.path.join(self.out, 'rules.json'), 'w') as f:
            json.dump({k: dict(v) for k, v in self.stats.items()}, f, indent=1)

    # ---- state
    def save_state(self):
        st = {'func': self.func, 'seed': self.seed, 'counts': dict(self.counts),
              'stats': {k: dict(v) for k, v in self.stats.items()}, 'best_trail': self.best.trail,
              'islands': []}
        for isl in self.islands:
            p = os.path.join(self.out, f'island{isl.k}.c')
            with open(p, 'w', encoding='latin1') as f: f.write(permc.render(isl.toks, self.tail))
            st['islands'].append({'k': isl.k, 'trail': isl.trail})
        with open(os.path.join(self.out, 'seen.txt'), 'w') as f: f.write('\n'.join(sorted(self.seen)))
        with open(os.path.join(self.out, 'state.json'), 'w') as f: json.dump(st, f)

    def load_state(self):
        p = os.path.join(self.out, 'state.json')
        if not os.path.exists(p): return
        with open(p) as f: st = json.load(f)
        if os.path.exists(os.path.join(self.out, 'seen.txt')):
            with open(os.path.join(self.out, 'seen.txt')) as f: self.seen |= set(f.read().split())
        bp = os.path.join(self.out, 'best.c')
        cands = [bp] + [os.path.join(self.out, f'island{i["k"]}.c') for i in st['islands']]
        for k, path in enumerate(cands):
            if not os.path.exists(path): continue
            with open(path, encoding='latin1') as f: text = f.read()
            raw, log = permrun.compile_once(self.runner, self.cfg, self.src, text, os.path.join(self.work, 'resume'))
            if raw is None: continue
            r = self.target.score(raw)
            toks, _ = permc.tokenize(text)
            isl = Island(k - 1, text, toks, self.make_ctx(toks), r, st['best_trail'] if k == 0 else st['islands'][k - 1]['trail'])
            if k == 0:
                self.best = isl
            elif k - 1 < len(self.islands):
                self.islands[k - 1] = Island(k - 1, text, toks, isl.ctx, r, isl.trail)
        for k, v in st.get('stats', {}).items(): self.stats[k].update(v)
        print(f'  resumed: best {self.best.result.text()}', flush=True)


def main(argv):
    import config
    path, a = config.pop_config(argv)
    args = [x for x in a if not x.startswith('--')]
    # values of --options are not positional
    vals = {a[i + 1] for i, x in enumerate(a) if x.startswith('--') and i + 1 < len(a) and not a[i + 1].startswith('--')
            and x not in ('--resume', '--no-screen')}
    args = [x for x in args if x not in vals]
    if len(args) < 2: sys.exit(__doc__)
    cfg = config.load(path)
    return Search(cfg, args[0], args[1], a).run()


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
