"""Batch search for one stubborn function: many variants compiled in one cl2000 run.

    python3 c28batch.py SRC FUNC SPEC.json [--random N] [--seed S] [--picks JSON] [--per 40]
                        [-j N] [--top 10] [--climb [START]] [--pairs] [--strict] [--own LO:HI]
    (--config PATH, or $EXHUME_CONFIG)

SPEC.json lists choice points, [[old_text, [alt0, alt1, ...]], ...] (alt0 is usually old_text
itself). A variant picks one alternative per point and replaces the first occurrence of each
old_text in FUNC's body. Without --random, --picks or --climb every combination is tried.

The variants are renamed copies, FUNC_v0, FUNC_v1 ..., put straight after FUNC in a copy of SRC,
so each sees the same earlier functions as FUNC does (cl2000 uses what it knows of a callee
defined above the caller). --per copies go into one file and one cl2000 run, which costs little
more than one compile; a file that does not compile is split in halves until the bad variant is
found. Each copy is then linked alone at FUNC's address with the native lnk2000 (the others
parked) and scored against the image: (0 if every word is the image's else 1, instructions that
differ in an alignment of the two disassemblies, size difference). The copies move the file's
.ebss, so data-page offsets and data addresses are normalised away and call targets too; with
--strict they are kept, except in the file's own .ebss range (--own LO:HI). A score of (1, 0, 0)
means only data addresses differ: rerun match.py on it after putting it in the file (and the
.ebss solver, c28ebss.py). Scores of renamed copies are a guide, not a proof: the same text can
come out with other registers under another name; check the best ones under the real name.

--climb does coordinate descent from START (a JSON pick, default all zeros): every one-point
change of the best pick, then every two-point change of the points that tied or improved with
--pairs, until nothing improves. All scores go to <build>/batch/FUNC.json."""
import argparse, concurrent.futures as cf, difflib, itertools, json, os, random, re, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import c28tc, c28map, c28match, c28disasm


def find_function(text, fn):
    """(start, end) of FUNC's definition in text: from the start of its line to its closing brace."""
    for m in re.finditer(r'(?m)^[^\n;{}#]*\b%s\s*\(' % re.escape(fn), text):
        i = text.index('(', m.end() - 1)
        depth, j = 0, i
        while j < len(text):
            if text[j] == '(': depth += 1
            elif text[j] == ')':
                depth -= 1
                if depth == 0: break
            j += 1
        k = j + 1
        while k < len(text) and text[k] in ' \t\r\n': k += 1
        if k >= len(text) or text[k] != '{': continue           # a declaration, not the definition
        depth = 0
        for e in range(k, len(text)):
            if text[e] == '{': depth += 1
            elif text[e] == '}':
                depth -= 1
                if depth == 0: return m.start(), e + 1
    raise c28tc.ToolError(f'no definition of {fn}')


def variant(body, spec, pick):
    for (old, alts), p in zip(spec, pick):
        if old not in body: raise c28tc.ToolError(f'choice point not in the function: {old!r}')
        body = body.replace(old, alts[p], 1)
    return body


def with_copies(text, fn, bodies):
    """text with renamed copies FUNC_vK of the given function texts straight after FUNC."""
    i, j = find_function(text, fn)
    copies = ''.join('\n\n' + re.sub(r'\b%s\s*\(' % re.escape(fn), '%s_v%d(' % (fn, k), b, count=1)
                     for k, b in enumerate(bodies))
    return text[:j] + copies + text[j:]


def normalise(texts, own=None, strict=False):
    """Instruction texts with what a moved .ebss or a renamed copy changes taken out."""
    out, dp = [], None
    for t in texts:
        t = re.sub(r'(LCR|LC|LB|FFC) 0x[0-9a-f]+', r'\1 CALL', t)
        if not strict:
            t = re.sub(r'MOVW DP, #0x[0-9a-f]+', 'MOVW DP', t)
            t = re.sub(r'@0x[0-9a-f]+', '@D', t)
            t = re.sub(r'#0x00[0-9a-f]{4}\b', '#ADDR', t)
            out.append(t); continue
        m = re.match(r'MOVW DP, #0x([0-9a-f]+)', t)
        if m:
            dp = int(m.group(1), 16) * 64
            out.append('MOVW DP, #OWN' if own and own[0] <= dp < own[1] else t); continue
        if dp is not None:
            def rep(mm):
                a = dp + int(mm.group(1), 16)
                return '@OWN' if own and own[0] <= a < own[1] else '@%04X' % a
            t = re.sub(r'@0x([0-9a-f]+)', rep, t)
        out.append(t)
    return out


def score(ours, want, dis_ours, dis_want):
    sm = difflib.SequenceMatcher(None, dis_ours, dis_want, autojunk=False)
    nd = sum(max(i2 - i1, j2 - j1) for op, i1, i2, j1, j2 in sm.get_opcodes() if op != 'equal')
    return (0 if list(ours) == list(want) else 1, nd, abs(len(ours) - len(want)))


class Batch:
    def __init__(self, s, src, fn, spec, per=40, jobs=4, strict=False, own=None, out=print):
        self.s, self.src, self.fn, self.spec, self.per, self.jobs = s, os.path.abspath(src), fn, spec, per, jobs
        self.strict, self.own, self.out = strict, own, out
        self.tc = c28tc.Toolchain(s)
        self.m = c28map.Map.from_dir(s.map_dir)
        self.img = c28tc.Image.of(s)
        row = self.m.frows.get(fn)
        if row is None: raise c28tc.ToolError(f'{fn}: not in map/functions.tsv')
        self.addr = c28map.run_addr(row)
        self.want = self.img.words(row.addr, row.size)
        self.dis_want = normalise([x[2] for x in c28disasm.disasm(self.tc, self.want, self.addr)], own, strict)
        with open(self.src, encoding='latin1') as f: self.text = f.read()
        i, j = find_function(self.text, fn)
        self.body = self.text[i:j]
        self.seen = {}
        self.dir = os.path.join(s.build, 'batch', fn)

    def chunk(self, tag, picks):
        work = os.path.join(self.dir, tag)
        shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
        p = os.path.join(work, os.path.basename(self.src))
        with open(p, 'w', encoding='latin1') as f:
            f.write(with_copies(self.text, self.fn, [variant(self.body, self.spec, pk) for pk in picks]))
        try:
            obj = self.tc.compile(p, work, cgt=self.tc.home_for(self.src), extra=['-I' + os.path.dirname(self.src)])
        except c28tc.ToolError as e:
            if len(picks) == 1: return [(None, picks[0], str(e)[:200])]
            h = len(picks) // 2
            return self.chunk(tag + 'a', picks[:h]) + self.chunk(tag + 'b', picks[h:])
        o = self.tc.obj(obj)
        placements, symdefs, _, _, _ = c28match.plan(o, self.m, self.img, self.s.park, self.s.park_data)
        res = []
        for k, pick in enumerate(picks):
            vsec = next((n for n in o.sections if n.endswith(':_%s_v%d' % (self.fn, k))), None)
            if vsec is None: res.append((None, pick, 'no section for copy %d' % k)); continue
            park = self.s.park + 0x40000
            pl = []
            for n, b, pg in placements:
                if n == vsec: pl.append((n, self.addr, pg))
                elif o.sections[n].kind == 'text': pl.append((n, park, 0)); park += o.sections[n].size + 16
                else: pl.append((n, b, pg))
            lw = os.path.join(work, 'l%d' % k); os.makedirs(lw, exist_ok=True)
            try:
                outp = c28match.link(self.tc, obj, lw, pl, symdefs)
            except c28tc.ToolError as e:
                res.append((None, pick, 'link: ' + ' '.join(str(e).split())[:150])); continue
            ours = c28match.linked_sections(self.tc, outp).get(vsec, [])
            dis = normalise([x[2] for x in c28disasm.disasm(self.tc, ours, self.addr)], self.own, self.strict) if ours else []
            res.append((score(ours, self.want, dis, self.dis_want), pick, None))
        shutil.rmtree(work, ignore_errors=True)
        return res

    def evaluate(self, picks):
        picks = [tuple(p) for p in picks if tuple(p) not in self.seen]
        chunks = [picks[i:i + self.per] for i in range(0, len(picks), self.per)]
        with cf.ThreadPoolExecutor(max(1, self.jobs)) as ex:
            for res in ex.map(lambda t: self.chunk('c%d' % t[0], t[1]), enumerate(chunks)):
                for sc, pick, err in res:
                    self.seen[tuple(pick)] = sc if sc is not None else (9, 99999, 99999)
                    if err and not getattr(self, '_said', False): self.out('failed: ' + err); self._said = True
        os.makedirs(self.dir, exist_ok=True)
        with open(os.path.join(self.dir + '.json'), 'w') as f:
            json.dump(sorted([list(v), list(k)] for k, v in self.seen.items()), f)

    def climb(self, start, pairs=False):
        sizes = [len(x[1]) for x in self.spec]
        cur = tuple(list(start) + [0] * (len(sizes) - len(start)))
        self.evaluate([cur]); best = self.seen[cur]
        self.out('start %s %s' % (best, list(cur)))
        while best[0] != 0:
            cands = [cur[:i] + (v,) + cur[i + 1:] for i, n in enumerate(sizes) for v in range(n) if v != cur[i]]
            self.evaluate(cands)
            if pairs:
                good = [i for i in range(len(sizes)) if any(self.seen[c][:2] <= best[:2] for c in cands if c[i] != cur[i])]
                pc = [tuple(cur[:i] + (vi,) + cur[i + 1:j] + (vj,) + cur[j + 1:])
                      for i, j in itertools.combinations(good, 2)
                      for vi in range(sizes[i]) for vj in range(sizes[j]) if vi != cur[i] and vj != cur[j]]
                if len(pc) <= 3000: self.evaluate(pc)
            nb = min((v, k) for k, v in self.seen.items())
            if nb[0] < best:
                best, cur = nb
                self.out('better %s %s' % (best, list(cur)))
            else:
                break
        return best, cur


def run(s, argv, out=print):
    ap = argparse.ArgumentParser(prog='c28batch.py', description='Batch search for one function.')
    ap.add_argument('src'); ap.add_argument('fn'); ap.add_argument('spec')
    ap.add_argument('--random', type=int, default=0); ap.add_argument('--seed', type=int, default=1)
    ap.add_argument('--picks'); ap.add_argument('--per', type=int, default=40)
    ap.add_argument('-j', type=int, default=0); ap.add_argument('--top', type=int, default=10)
    ap.add_argument('--climb', nargs='?', const='[]'); ap.add_argument('--pairs', action='store_true')
    ap.add_argument('--strict', action='store_true'); ap.add_argument('--own')
    a = ap.parse_args(argv)
    with open(a.spec) as f: spec = json.load(f)
    own = tuple(int(x, 16) for x in a.own.split(':')) if a.own else None
    b = Batch(s, s.source(a.src), a.fn, spec, a.per, a.j or s.jobs, a.strict, own, out)
    sizes = [len(x[1]) for x in spec]
    if a.climb is not None:
        best, cur = b.climb(json.loads(a.climb), a.pairs)
        out('final %s %s%s' % (best, list(cur), '  EXACT' if best[0] == 0 else ''))
        c28tc.stop_wine(s)
        return 0 if best[0] == 0 else 1
    if a.picks: picks = [tuple(p) for p in json.loads(a.picks)]
    elif a.random:
        rnd = random.Random(a.seed)
        picks = list({tuple(rnd.randrange(n) for n in sizes) for _ in range(a.random)})
    else: picks = list(itertools.product(*[range(n) for n in sizes]))
    b.evaluate(picks)
    ranked = sorted((v, k) for k, v in b.seen.items())
    for sc, pick in ranked[:a.top]:
        out('%s %s%s' % (sc, list(pick), '  EXACT' if sc[0] == 0 else ''))
    failed = sum(1 for v, _ in ranked if v[0] == 9)
    if failed: out('%d variants did not compile or link' % failed)
    c28tc.stop_wine(s)
    return 0 if ranked and ranked[0][0][0] == 0 else 1


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(c28tc.EXHUME, 'tools'))
    import config
    p, rest = config.pop_config(sys.argv[1:])
    sys.exit(run(c28tc.settings(p), rest))
