"""Search a source template exhaustively: compile every combination of its choice points and
score one function against the image, or tabulate what armcc makes of each.

    python3 armbrute.py [--config PATH] TEMPLATE.c FUNC [--jobs N] [--batch N] [--prefixes DIR]
                        [--shard I/N] [--start K] [--random N] [--climb [--restarts N]] [--regs]
    python3 armbrute.py [--config PATH] TEMPLATE.c FUNC --table [--regs | --pool] [--opts "..."]

TEMPLATE.c is a copy of the source with choice points written {{a||b||c}} (an empty option is
allowed: {{||x;}}); where an option holds C's || or ends in a closing brace, write
{{@a@|@b@|@c@}}. armoutline.py writes templates; a hand-written one is often better (a handful
of spellings of one statement times the declaration orders of the locals). Combination 0 (every first option) should be the source
as it is: it is scored first, and when it compiles to the image's size its literal-pool offsets
replace the image sweep's guesses.

Search (the default): the product of all choice points in mixed-radix order, or N random
combinations with --random, scored as armvariant.py scores (0: the image's instructions).
Every combination scoring 0 is appended to <build>/brute/FUNC/hits.c as a whole source, ready
for match.py; each new best to best.txt with its choice indices. --shard I/N takes every N-th
combination from the I-th, so N machines can split one template; --start resumes. --climb is
coordinate descent instead (each choice point in turn, all its options, keep the best; repeat
until a pass changes nothing; --restarts N climbs again from N random starts): for templates
far too large to enumerate. --regs scores the structure only (registers masked): for templates
that test structure, before the allocation is searched.

Table (--table): no image needed. Each combination's FUNC is reduced to its normalised listing
(--regs: its structural listing; --pool: its literal loads and pool, armvariant.py pool) and the
combinations are grouped by it, largest group first, with an example of each: what armcc makes
of every spelling of an idiom. --opts gives the options when the template has no /* opts: */.

A variant that does not compile scores nothing; the run says how many did not. Before trusting
a search, check that count: a template whose variants mostly fail searched less than its size.
Scores are FUNC's alone: re-run match.py on a hit before using it."""
import itertools, os, random, re, sys, tempfile, threading, time, collections
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import armvariant as V

CHOICE = re.compile(r'\{\{@(.*?)@\}\}|\{\{(.*?)\}\}', re.S)


def parse(text):
    """(fixed parts, [options per choice point])."""
    parts, opts, last = [], [], 0
    for m in CHOICE.finditer(text):
        parts.append(text[last:m.start()])
        opts.append(m.group(1).split('@|@') if m.group(1) is not None else m.group(2).split('||'))
        last = m.end()
    parts.append(text[last:])
    return parts, opts


def render(parts, opts, idx):
    return ''.join(p + (opts[i][idx[i]] if i < len(opts) else '') for i, p in enumerate(parts))


def rank(idx, radix):
    k = 0
    for i, r in zip(idx, radix): k = k * r + i
    return k


def unrank(k, radix):
    out = []
    for r in reversed(radix):
        out.append(k % r); k //= r
    return out[::-1]


def arg(a, name, default=None, conv=str):
    return conv(a[a.index(name) + 1]) if name in a else default


class Jobs:
    """Thread-local Batches: each job its own work directory and, under Wine, its own prefix."""

    def __init__(self, make, out, prefixes):
        self.make, self.out, self.prefixes = make, out, prefixes
        self.local, self.n = threading.local(), itertools.count()

    def get(self):
        if not hasattr(self.local, 'b'):
            n = next(self.n)
            work = tempfile.mkdtemp(prefix=f'job{n}-', dir=self.out)
            prefix = os.path.join(self.prefixes, f'job{n}') if self.prefixes else None
            self.local.b = self.make(work, prefix)
        return self.local.b


def main(argv):
    sys.path.insert(0, os.path.join(V.EXHUME, 'tools'))
    import config
    path, a = config.pop_config(argv)
    if len(a) < 2: sys.exit(__doc__)
    cfg = config.load(path)
    tpl, func = os.path.abspath(a[0]), a[1]
    jobs = arg(a, '--jobs', os.cpu_count(), int)
    batch = arg(a, '--batch', 40, int)
    prefixes = arg(a, '--prefixes', os.environ.get('EXHUME_ARMCC_PREFIXES'))
    if prefixes: prefixes = os.path.abspath(os.path.expanduser(prefixes))
    with open(tpl, encoding='latin1') as f: text = f.read()
    parts, opts = parse(text)
    radix = [len(o) for o in opts]
    total = 1
    for r in radix: total *= r
    out = os.path.join(cfg.build, 'brute', func); os.makedirs(out, exist_ok=True)
    if '--table' in a:
        return table(cfg, a, tpl, func, parts, opts, radix, total, jobs, batch, prefixes, out)
    t = V.Target.from_config(cfg, tpl, func)
    regs = '--regs' in a
    jobs_ = Jobs(lambda w, p: V.Batch(cfg, t, func, w, p), out, prefixes)

    def run(ks):
        """[(k, idx, score or None, source)] for a batch of combinations, one armcc call."""
        srcs = [(k, unrank(k, radix)) for k in ks]
        texts = [render(parts, opts, idx) for _, idx in srcs]
        scores, _ = jobs_.get().scores(texts, regs)
        return [(k, idx, s, src) for (k, idx), s, src in zip(srcs, scores, texts)]

    (r0,), log = jobs_.get().compile([render(parts, opts, [0] * len(opts))])
    if not r0 or not r0[0]:
        print(f'{func}: combination 0 does not compile:\n' + log[-3000:]); return 1
    t.adopt(*r0)
    s0 = t.score(*r0, regs=regs)
    print(f'{func}: combination 0 scores {s0}', flush=True)
    hits_path = os.path.join(out, 'hits.c')
    if '--climb' in a:
        return climb(run, radix, func, jobs, batch, out, hits_path, arg(a, '--restarts', 0, int), arg(a, '--start', 0, int))
    nrand, start = arg(a, '--random', 0, int), arg(a, '--start', 0, int)
    shard = tuple(map(int, arg(a, '--shard', '0/1').split('/')))
    if nrand:
        rng = random.Random(start)
        ks, count = (rng.randrange(total) for _ in range(nrand)), nrand
    else:
        ks = range(start + shard[0], total, shard[1]); count = len(ks)
    print(f'{func}: {len(opts)} choice points, {total} combinations, {count} to run, {jobs} jobs', flush=True)
    best, done, failed, t0 = None, 0, 0, time.time()
    with open(hits_path, 'a') as hits, ThreadPoolExecutor(jobs) as ex:
        it, pending = iter(ks), set()

        def feed():
            while len(pending) < jobs * 2:
                b = list(itertools.islice(it, batch))
                if not b: return
                pending.add(ex.submit(run, b))
        feed()
        while pending:
            fin, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in fin:
                pending.discard(fut)
                for k, idx, s, src in fut.result():
                    done += 1
                    if s is None: failed += 1; continue
                    if best is None or s < best[0]:
                        best = (s, k, idx)
                        with open(os.path.join(out, 'best.txt'), 'a') as f: f.write(f'{s}\t{k}\t{idx}\n')
                        print(f'  [{done}/{count}] score {s} at {k} {idx}', flush=True)
                    if s == 0:
                        hits.write(f'/* brute {func} combination {k} {idx} */\n{src}\n/* ---- */\n'); hits.flush()
                        print(f'  HIT at {k} {idx}', flush=True)
                    if done % 500 == 0:
                        print(f'  {done}/{count} ({done / (time.time() - t0):.1f}/s), best {best[0] if best else None}', flush=True)
            feed()
    print(f'{func}: done {done} ({failed} did not compile); best {best}')
    return 0


def climb(run, radix, func, jobs, batch, out, hits_path, restarts=0, seed=0):
    """Coordinate descent from combination 0, then from `restarts` random combinations."""
    seen, rng, overall = {}, random.Random(seed), None
    for n in range(restarts + 1):
        start = [0] * len(radix) if n == 0 else [rng.randrange(r) for r in radix]
        sc, idx = climb1(run, radix, func, jobs, batch, out, hits_path, start, seen)
        print(f'{func}: climb {n} from {start}: {sc} at {idx}', flush=True)
        if sc is not None and (overall is None or sc < overall[0]): overall = (sc, idx)
        if overall and overall[0] == 0: break
    print(f'{func}: done climbing ({len(seen)} compiled); best {overall}')
    return 0


def climb1(run, radix, func, jobs, batch, out, hits_path, cur, seen):
    k0 = rank(cur, radix)
    best = seen[k0] if k0 in seen else run([k0])[0][2]
    if best is None: return None, cur
    seen[k0] = best
    improved = True
    with ThreadPoolExecutor(jobs) as ex:
        while improved and best:
            improved = False
            for i, r in enumerate(radix):
                ks = []
                for o in range(r):
                    idx = list(cur); idx[i] = o; k = rank(idx, radix)
                    if k not in seen: ks.append(k)
                    elif seen[k] is not None and seen[k] < best:          # found by an earlier climb
                        best, cur, improved = seen[k], idx, True
                for res in ex.map(run, [ks[j:j + batch] for j in range(0, len(ks), batch)]):
                    for k, idx, sc, src in res:
                        seen[k] = sc
                        if sc is not None and sc < best:
                            best, cur, improved = sc, idx, True
                            print(f'  point {i}: score {sc} at {k} {idx}', flush=True)
                            with open(os.path.join(out, 'best.txt'), 'a') as f: f.write(f'{sc}\t{k}\t{idx}\n')
                        if sc == 0:
                            with open(hits_path, 'a') as f: f.write(f'/* brute {func} combination {k} {idx} */\n{src}\n/* ---- */\n')
                            print(f'  HIT at {k} {idx}', flush=True)
                if best == 0: break
    return best, cur


def signature(lst_text, func, mode):
    """A combination's key in a table: its normalised listing, structural listing or pool order."""
    if mode == 'pool':
        loads, pool = V.pool_order(lst_text, func)
        return 'loads ' + ' '.join(loads) + '  pool ' + ' '.join(f'[{p}]' for p in pool)
    got, pool = V.func_bytes(lst_text, func)
    if not got: return None
    lst = V.listing(got, pool)
    if mode == 'regs': lst = V.structural(lst)
    return ' ; '.join(lst)


def group(rows):
    """[(key, [labels])] largest group first; rows are (label, key or None)."""
    table = collections.defaultdict(list)
    for label, key in rows: table[key if key is not None else '(does not compile)'].append(label)
    return sorted(table.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def table(cfg, a, tpl, func, parts, opts, radix, total, jobs, batch, prefixes, out):
    mode = 'pool' if '--pool' in a else 'regs' if '--regs' in a else 'listing'
    _, o = cfg.directives(tpl)
    copts = arg(a, '--opts', o or cfg.c_opts)
    jobs_ = Jobs(lambda w, p: (V.Runner(cfg, p), w), out, prefixes)

    def run(ks):
        runner, work = jobs_.get()
        idxs = [unrank(k, radix) for k in ks]
        lst, _ = V.compile_batch(runner, [render(parts, opts, i) for i in idxs], copts, work)
        return [(str(i), signature(x, func, mode) if x else None) for i, x in zip(idxs, lst)]

    print(f'{func}: {total} combinations, grouped by {mode}', flush=True)
    rows = []
    with ThreadPoolExecutor(jobs) as ex:
        for res in ex.map(run, [range(k, min(k + batch, total)) for k in range(0, total, batch)]):
            rows += res
    for key, labels in group(rows):
        print(f'{len(labels):6d}  e.g. {labels[0]}\n        {key[:400]}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
