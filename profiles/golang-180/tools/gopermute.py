"""Go 1.8.0 micro-idiom permuter & coordinate-descent searcher (profile: golang-180).

    python3 gopermute.py UNIT FILE.go FULL_FUNC_NAME [--max N] [--apply] [--config PATH]

Automatically proposes and tests Go 1.8.0-specific source micro-idioms on one function:
  - local variable alphabetical slot swaps (from goslotmap / `-S` listing)
  - `if cond` branch/condition inversions (`!= nil` <-> `== nil`, `len(x) == 0` <-> `x == nil`)
  - composite literal allocation forms (`x := T{}` <-> `var x T`, `&T{}` <-> `new(T)`)
  - `for` loop forms (`for i := 0; i < len(s); i++` <-> `for i := range s`)
  - `switch` `default:` placement & 2-item `case A, B:` ordering
  - `defer` / direct `return callee(...)` vs temp-var return

Scores each candidate by `(raw_unmasked_diffs, cmp_diff_funcs, cmp_lines_only, size_delta)`.
Uses the configured runner (set `runner = "docker-daemon"` in exhume.toml for ~200ms builds).
Always restores the original file unless `--apply` is given and an improvement is found.
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gotc


def find_go_func_span(src, short_name):
    """Return (start_idx, end_idx) of func/method definition for short_name in Go source."""
    # Match `func (r *T) Name(` or `func Name(`
    m_name = short_name.split('.')[-1]
    pat = re.compile(r'(?m)^func\s+(?:\([^)]*\)\s*)?' + re.escape(m_name) + r'\s*\(')
    m = pat.search(src)
    if not m:
        raise gotc.ToolError(f'function {short_name!r} not found in source')
    brace = src.find('{', m.end())
    if brace < 0:
        raise gotc.ToolError(f'no opening brace for {short_name!r}')
    depth = 0
    in_str = None
    esc = False
    for i in range(brace, len(src)):
        ch = src[i]
        if in_str:
            if esc:
                esc = False
            elif ch == '\\' and in_str == '"':
                esc = True
            elif ch == in_str:
                in_str = None
            continue
        if ch in ('"', '`'):
            in_str = ch
        elif ch == '{':
            depth += 1
        elif ch == '}':
            depth -= 1
            if depth == 0:
                return m.start(), i + 1
    raise gotc.ToolError(f'unterminated function body for {short_name!r}')


def generate_mutations(fn_body):
    """Yield (label, mutated_body) for Go 1.8.0 micro-idiom transformations."""
    seen = {fn_body}

    def emit(label, new_body):
        if new_body != fn_body and new_body not in seen:
            seen.add(new_body)
            return (label, new_body)
        return None

    out = []

    # 1. `x := &T{}` <-> `x := new(T)` and `x := T{}` <-> `var x T`
    for m in re.finditer(r'(\b\w+\b)\s*:=\s*&([\w.]+)\{\}', fn_body):
        v, t = m.group(1), m.group(2)
        c = emit(f'new({t}) for {v}', fn_body[:m.start()] + f'{v} := new({t})' + fn_body[m.end():])
        if c:
            out.append(c)
    for m in re.finditer(r'(\b\w+\b)\s*:=\s*new\(([\w.]+)\)', fn_body):
        v, t = m.group(1), m.group(2)
        c = emit(f'&{t}{{}} for {v}', fn_body[:m.start()] + f'{v} := &{t}{{}}' + fn_body[m.end():])
        if c:
            out.append(c)
    for m in re.finditer(r'(\b\w+\b)\s*:=\s*([\w.]+)\{\}', fn_body):
        v, t = m.group(1), m.group(2)
        c = emit(f'var {v} {t}', fn_body[:m.start()] + f'var {v} {t}' + fn_body[m.end():])
        if c:
            out.append(c)
    for m in re.finditer(r'var\s+(\b\w+\b)\s+([\w.]+)\b', fn_body):
        v, t = m.group(1), m.group(2)
        c = emit(f'{v} := {t}{{}}', fn_body[:m.start()] + f'{v} := {t}{{}}' + fn_body[m.end():])
        if c:
            out.append(c)

    # 2. `for i := 0; i < len(x); i++` <-> `for i := range x`
    for m in re.finditer(r'for\s+(\w+)\s*:=\s*0\s*;\s*\1\s*<\s*len\(([^)]+)\)\s*;\s*\1\+\+', fn_body):
        i, xs = m.group(1), m.group(2)
        c = emit(f'range {xs}', fn_body[:m.start()] + f'for {i} := range {xs}' + fn_body[m.end():])
        if c:
            out.append(c)
    for m in re.finditer(r'for\s+(\w+)\s*:=\s*range\s+([^\s{]+)', fn_body):
        i, xs = m.group(1), m.group(2)
        if i != '_':
            c = emit(f'counted loop {xs}', fn_body[:m.start()] + f'for {i} := 0; {i} < len({xs}); {i}++' + fn_body[m.end():])
            if c:
                out.append(c)

    # 3. `case A, B:` order flip
    for m in re.finditer(r'(case\s+)([^,:\n]+),\s*([^,:\n]+)(:)', fn_body):
        a, b = m.group(2).strip(), m.group(3).strip()
        c = emit(f'flip case {a},{b}', fn_body[:m.start()] + f'{m.group(1)}{b}, {a}{m.group(4)}' + fn_body[m.end():])
        if c:
            out.append(c)

    # 4. `len(x) == 0` <-> `x == nil` and `== 0` <-> `< 1`
    for old, new, tag in [
        (r'len\((\w+)\)\s*==\s*0', r'\1 == nil', 'len==0 -> nil'),
        (r'(\w+)\s*==\s*nil', r'len(\1) == 0', 'nil -> len==0'),
        (r'append\(([^,]+),\s*([^)]+)\)', r'append(\1, \2)', None),
    ]:
        if tag is None:
            continue
        for m in re.finditer(old, fn_body):
            rep = re.sub(old, new, m.group(0), count=1)
            c = emit(tag, fn_body[:m.start()] + rep + fn_body[m.end():])
            if c:
                out.append(c)

    # 5. Local variable alphabetical rename probes (for Go 1.8 stack slot ordering)
    locals_found = sorted(set(re.findall(r'(?m)^\s+(\w+)\s*:=', fn_body)))
    for v in locals_found:
        if v in ('_', 'err'):
            continue
        for prefix, desc in [('a_', 'earlier'), ('z_', 'later')]:
            nv = prefix + v
            nb = re.sub(r'\b' + re.escape(v) + r'\b', nv, fn_body)
            c = emit(f'slot-order {v}->{nv} ({desc})', nb)
            if c:
                out.append(c)

    return out


def score_candidate(tc, s, unit, src_path, content, full_func):
    with open(src_path, 'w') as f:
        f.write(content)
    try:
        cand, _ = tc.build(unit)
    except gotc.ToolError as e:
        return (999999, 999, 999, 999999), f'BUILD FAIL'
    rx = '^' + re.escape(full_func) + '$'
    cr = tc.cmp(s.image, cand, rx)
    rr = tc.raw(s.image, cand, rx)
    sz = sum(abs(r.orig_size - r.cand_size) for r in cr.rows)
    sc = (rr.unmasked_diffs, len(cr.diffs) + len(cr.missing), len(cr.lines_only), sz)
    st = cr.rows[0].status if cr.rows else 'NONE'
    return sc, f'{st} raw_bad={rr.unmasked_diffs} sz_delta={sz}'


def main(argv=None):
    ap = argparse.ArgumentParser(description='Go 1.8.0 micro-idiom permuter with greedy hill-climbing.')
    ap.add_argument('unit', help='configured unit name')
    ap.add_argument('file', help='path to .go source file')
    ap.add_argument('func', help='full symbol name (e.g. app/events.(*Hub).Publish)')
    ap.add_argument('--max', type=int, default=40, dest='max_evals', help='max compiler builds (default 40)')
    ap.add_argument('--apply', action='store_true', help='keep best improved source in FILE.go')
    ap.add_argument('--config', help='path to exhume.toml')
    args = ap.parse_args(argv)

    s = gotc.settings(args.config)
    tc = gotc.Toolchain(s)
    unit, _ = s.resolve_unit(args.unit)
    src_path = args.file if os.path.exists(args.file) else os.path.join(unit.src_dir, args.file)
    if not os.path.exists(src_path):
        raise gotc.ToolError(f'source file not found: {args.file}')
    src_path = os.path.abspath(src_path)
    with open(src_path) as f:
        orig_src = f.read()

    best_src = orig_src
    best_sc, msg = score_candidate(tc, s, unit, src_path, orig_src, args.func)
    print(f'baseline: score={best_sc}  ({msg})')
    evals = 1

    try:
        while evals < args.max_evals and best_sc != (0, 0, 0, 0):
            i, j = find_go_func_span(best_src, args.func)
            body = best_src[i:j]
            muts = generate_mutations(body)
            improved = False
            for label, new_body in muts:
                if evals >= args.max_evals:
                    break
                cand_src = best_src[:i] + new_body + best_src[j:]
                sc, m = score_candidate(tc, s, unit, src_path, cand_src, args.func)
                evals += 1
                marker = ' <-- IMPROVED' if sc < best_sc else ''
                print(f'  [{evals:2d}] {label:32s} score={sc} ({m}){marker}')
                if sc < best_sc:
                    best_sc, best_src, improved = sc, cand_src, True
                    if best_sc == (0, 0, 0, 0):
                        break
            if not improved:
                break
    finally:
        final_src = best_src if (args.apply and best_src != orig_src) else orig_src
        with open(src_path, 'w') as f:
            f.write(final_src)

    print(f'\ndone in {evals} builds; best score={best_sc}'
          + (' (applied)' if args.apply and best_src != orig_src else ' (file restored)'))
    return 0 if best_sc == (0, 0, 0, 0) else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except gotc.ToolError as e:
        sys.exit(f'gopermute: {e}')
