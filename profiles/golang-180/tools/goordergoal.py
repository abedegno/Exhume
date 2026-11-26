#!/usr/bin/env python3
"""goordergoal.py: search for a wanted LOAD ORDER of one call's result words.

gorandperm's score has no slope for the hardest kind of difference: the words of a multi-word
result (`b, err := f()` is five: ptr, len, cap, itab, data) loaded in another order than the
original's. Every wrong order costs about the same number of bytes, so a random search cannot tell
"nearly" from "nowhere". This search scores what decides the order instead: the value IDs the `dec`
pass gives the words (gotrace.py), counted as the number of word PAIRS in the wrong relative order.

    goordergoal.py UNIT FILE.go SSA_NAME FUNC_REGEX --line L --kinds slice,iface --want 2,1,3,4,0
                   [--lines LO-HI] [--start FILE] [--workers N] [--minutes M] [--out DIR] [--seed N]

--kinds: the multi-word values `dec` splits at that line, in order (string, slice, iface); their
words in order are the indexes 0, 1, 2, ... (slice,iface: 0 ptr, 1 len, 2 cap, 3 itab, 4 data).
--want: the original's emission order as those indexes, read from the disassembly after the call
(the words sit at ascending stack offsets in word order; the example is cap, len, itab, data, ptr).
Mutations are gorandperm's, on --lines (default: the function). Objective, lexicographic:
(wrong pairs, distance in list positions to an alignment that would be right, gofast byte score).
Candidates that change the function's size are dropped. OUT/best.go is the best so far and
OUT/order_right_SCORE.go each candidate with no wrong pair; continue from one with gorandperm
`--start` to clear what else the edits disturbed.

Three rules this tool exists to enforce, each learned from a false result:
  * the words are found by their own op pattern in `dec`'s allocations, never at a fixed position:
    an edit that makes `dec` split something else first moves them;
  * an improvement is measured twice before it is kept;
  * the work directory carries the output directory's name, so two runs cannot share one.
Still confirm a result from the disassembly (and a whole build) before believing it.
"""
import argparse
import hashlib
import os
import queue
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gorandperm as g
import gotc
import gotrace


def measure(T, text, w, line, kinds, want):
    """(wrong pairs, distance, byte score) or None (does not build, other size, group not found)."""
    try:
        rows = T.trace(text, worker=w)
    except gotc.ToolError:
        return None
    sc, _ = T.f.evaluate(w, text)
    if sc is None or sc >= 1_000_000:
        return None
    ids = gotrace.load_group(rows, line, kinds)
    if ids is None:
        return None
    inv = gotrace.inversions(ids, want)
    # how far the list would have to slide for the group to come out right (1000: nowhere)
    st = [int(f[3][1:]) for f in rows if f[0] == 'stack' and f[1] == 'before_dec']
    dec = [int(f[2][1:]) for f in rows if f[0] == 'alloc' and f[1] == 'dec']
    pat = [op for k in kinds for op in gotrace.KINDS[k]]
    offs = [j for j, op in enumerate(pat) if op == 'Load']
    at = dec.index(ids[0])
    st += list(range(10**6, 10**6 + len(pat)))
    best = 1000
    for k in range(len(st) - len(pat)):
        if gotrace.inversions([st[k + o] for o in offs], want) == 0:
            best = min(best, abs(k - at))
    return inv, best, sc


def main(argv=None):
    ap = argparse.ArgumentParser(description='Search for a wanted load order of one call\'s result words')
    ap.add_argument('unit')
    ap.add_argument('file')
    ap.add_argument('ssa_name')
    ap.add_argument('func')
    ap.add_argument('--line', type=int, required=True)
    ap.add_argument('--kinds', required=True)
    ap.add_argument('--want', required=True)
    ap.add_argument('--lines')
    ap.add_argument('--start')
    ap.add_argument('--workers', type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument('--minutes', type=float, default=30)
    ap.add_argument('--out')
    ap.add_argument('--seed', type=int)
    ap.add_argument('--config')
    a = ap.parse_args(argv)
    s = gotc.settings(a.config)
    unit, _ = s.resolve_unit(a.unit)
    kinds = a.kinds.split(',')
    want = [int(x) for x in a.want.split(',')]
    path = os.path.join(unit.src_dir, a.file)
    base = open(a.start or path).read()
    out = a.out or os.path.join(s.build, 'goordergoal', re.sub(r'\W+', '_', a.ssa_name))
    os.makedirs(out, exist_ok=True)
    tag = 'ordergoal_' + re.sub(r'\W+', '_', os.path.basename(os.path.normpath(out)))
    W = a.workers
    T = gotrace.Tracer(s, gotc.Toolchain(s), unit, a.file, a.ssa_name, a.func, tag, workers=W)
    g.set_globals(base, os.path.dirname(path))
    lo, hi = map(int, a.lines.split('-')) if a.lines else g.func_lines(base, a.func)
    rng = random.Random(a.seed)
    q = queue.Queue()
    for i in range(W):
        q.put(i)

    def m(text):
        w = q.get()
        try:
            return measure(T, text, w, a.line, kinds, want)
        finally:
            q.put(w)
    m0 = m(base)
    if m0 is None:
        raise gotc.ToolError('the start does not build, has another size, or has no such group on that line')
    pool, seen, best = [(m0, base)], set(), m0
    print('start', m0, flush=True)

    def job(_):
        parent = rng.choice(pool[-12:])[1]
        text = g.mutate(parent, lo, hi, random.Random(rng.random()), rng.choice([1, 1, 2, 3]))
        h = hashlib.sha1(text.encode()).hexdigest()
        if h in seen or text == parent:
            return None
        seen.add(h)
        return m(text), text
    t0, n, ok = time.time(), 0, 0
    with ThreadPoolExecutor(W) as ex:
        while time.time() - t0 < a.minutes * 60:
            for r in ex.map(job, range(W * 4)):
                if r is None:
                    continue
                n += 1
                if r[0] is None:
                    continue
                ok += 1
                cur, top = r[0], pool[-1][0]
                if cur < top:
                    if m(r[1]) != cur:          # a result must reproduce before it is believed
                        print('NOT REPRODUCED', cur, flush=True)
                        continue
                    pool.append(r)
                    if cur < best:              # the walk's current point may be worse than the best
                        best = cur
                        open(os.path.join(out, 'best.go'), 'w').write(r[1])
                        if cur[0] == 0:
                            open(os.path.join(out, f'order_right_{cur[2]}.go'), 'w').write(r[1])
                        print('BEST', cur, flush=True)
                elif cur[:2] == top[:2] and rng.random() < 0.1:
                    pool.append(r)              # a sideways step keeps the walk moving
            if n % (W * 40) < W * 4:
                print(time.strftime('%H:%M:%S'), n, 'tried', ok, 'usable; best', best,
                      f'{n / (time.time() - t0):.1f}/s', flush=True)
            if best[0] == 0 and best[2] == 0:
                break
    print('DONE', best, 'after', n, 'candidates;', out)
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except gotc.ToolError as e:
        sys.exit(f'goordergoal: {e}')
