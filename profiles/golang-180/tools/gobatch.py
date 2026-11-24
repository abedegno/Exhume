"""Batch variant search for a Go 1.8.0 source file (Python VARIANTS or JSON choice-points).

    python3 gobatch.py UNIT FILE.go FUNC_REGEX SPEC.py [--config PATH]
    python3 gobatch.py UNIT FILE.go FUNC_REGEX SPEC.json [--climb] [--max N] [--config PATH]

Supports two spec formats:
  1. Python file defining `VARIANTS = [(name, [(old, new), ...]), ...]` (same format as
     a project's variant scripts, v*.py).
  2. JSON choice-point spec `[[old_text, [alt0, alt1, ...]], ...]` (same format as ti-c2000's
     c28batch.py), with optional `--climb` coordinate descent starting from all-zeros.

Scores each variant by `(raw_unmasked_diffs, cmp_diff_funcs, cmp_lines_only_funcs, size_delta)`.
Always restores the original source file in a `finally:` block.
"""
import argparse
import itertools
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gotc


def _apply_reps(base, reps):
    s = base
    for old, new in reps:
        if old not in s:
            return None, f'pattern not found: {old[:50]!r}'
        s = s.replace(old, new, 1)
    return s, None


def _eval_source(tc, s, unit, src_path, content, regex):
    with open(src_path, 'w') as f:
        f.write(content)
    try:
        cand, _ = tc.build(unit)
    except gotc.ToolError as e:
        return (999999, 999, 999, 999999), f'BUILD FAIL: {str(e).splitlines()[-1][:140]}'
    cmp_res = tc.cmp(s.image, cand, regex)
    raw_res = tc.raw(s.image, cand, regex)
    size_delta = sum(abs(r.orig_size - r.cand_size) for r in cmp_res.rows)
    score = (
        raw_res.unmasked_diffs,
        len(cmp_res.diffs) + len(cmp_res.missing),
        len(cmp_res.lines_only),
        size_delta,
    )
    summary = ' '.join(
        f'{r.status}:{r.name.split(".")[-1]}({r.orig_size}/{r.cand_size})'
        for r in cmp_res.rows
    )
    summary += f'  [raw: {raw_res.unmasked_diffs} unmasked]'
    return score, summary


def _run_py_variants(tc, s, unit, src_path, base, regex, spec_path):
    ns = {}
    with open(spec_path) as f:
        exec(compile(f.read(), spec_path, 'exec'), ns)
    variants = ns.get('VARIANTS', [])
    results = []
    for name, reps in variants:
        content, err = _apply_reps(base, reps)
        if err:
            print(f'{name}: SKIP ({err})')
            continue
        score, summary = _eval_source(tc, s, unit, src_path, content, regex)
        print(f'{name:24s} score={score}  {summary}')
        results.append({'name': name, 'score': list(score), 'summary': summary})
    return results


def _run_json_spec(tc, s, unit, src_path, base, regex, spec_path, climb=False, max_evals=100):
    with open(spec_path) as f:
        spec = json.load(f)
    seen = {}

    def eval_pick(pick):
        pick = tuple(pick)
        if pick in seen:
            return seen[pick]
        reps = [(old, alts[p]) for (old, alts), p in zip(spec, pick)]
        content, err = _apply_reps(base, reps)
        if err:
            sc, sm = (999999, 999, 999, 999999), f'SKIP: {err}'
        else:
            sc, sm = _eval_source(tc, s, unit, src_path, content, regex)
        seen[pick] = (sc, sm)
        print(f'pick={list(pick)}  score={sc}  {sm}')
        return sc, sm

    if climb:
        best = [0] * len(spec)
        best_sc, _ = eval_pick(best)
        while len(seen) < max_evals and best_sc != (0, 0, 0, 0):
            improved = False
            for i, (_old, alts) in enumerate(spec):
                for a in range(len(alts)):
                    if a == best[i] or len(seen) >= max_evals:
                        continue
                    cand = list(best)
                    cand[i] = a
                    sc, _ = eval_pick(cand)
                    if sc < best_sc:
                        best, best_sc, improved = cand, sc, True
            if not improved:
                break
    else:
        for pick in itertools.islice(itertools.product(*(range(len(alts)) for _, alts in spec)), max_evals):
            sc, _ = eval_pick(pick)
            if sc == (0, 0, 0, 0):
                break
    ranked = sorted(((sc, list(pk), sm) for pk, (sc, sm) in seen.items()))
    return [{'pick': pk, 'score': list(sc), 'summary': sm} for sc, pk, sm in ranked]


def main(argv=None):
    ap = argparse.ArgumentParser(description='Search Go 1.8.0 source variants against the target binary.')
    ap.add_argument('unit', help='configured unit name')
    ap.add_argument('file', help='path to .go file (absolute, project-relative, or relative to GOPATH/src)')
    ap.add_argument('regex', help='function regex to score')
    ap.add_argument('spec', help='Python VARIANTS .py file or JSON choice-point .json file')
    ap.add_argument('--climb', action='store_true', help='coordinate descent for JSON choice-point specs')
    ap.add_argument('--max', type=int, default=100, dest='max_evals', help='maximum builds (default 100)')
    ap.add_argument('--config', help='path to exhume.toml')
    args = ap.parse_args(argv)

    s = gotc.settings(args.config)
    tc = gotc.Toolchain(s)
    unit, _ = s.resolve_unit(args.unit)

    src_path = args.file
    if not os.path.exists(src_path):
        alt = os.path.join(unit.src_dir, args.file)
        if os.path.exists(alt):
            src_path = alt
    if not os.path.exists(src_path):
        raise gotc.ToolError(f'source file not found: {args.file}')
    src_path = os.path.abspath(src_path)
    with open(src_path) as f:
        base = f.read()

    try:
        if args.spec.endswith('.json'):
            results = _run_json_spec(tc, s, unit, src_path, base, args.regex, args.spec,
                                     climb=args.climb, max_evals=args.max_evals)
        else:
            results = _run_py_variants(tc, s, unit, src_path, base, args.regex, args.spec)
    finally:
        with open(src_path, 'w') as f:
            f.write(base)

    out_dir = os.path.join(s.build, 'batch')
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, os.path.basename(args.spec) + '.results.json')
    with open(out_file, 'w') as f:
        json.dump(results, f, indent=2)
    print(f'\nresults saved to {out_file}')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except gotc.ToolError as e:
        sys.exit(f'gobatch: {e}')
