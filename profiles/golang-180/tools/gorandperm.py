r"""Go 1.8.0 random parity permuter (profile: golang-180).

    python3 gorandperm.py UNIT FILE.go FUNC_REGEX [--guard REGEX] [--lines LO-HI]
                          [--workers N] [--minutes M] [--pkg PKG] [--out DIR] [--config PATH]

For functions that are already the right size and differ only by `(ptr,len)` / `(itab,data)`
load-order swaps, register choice, or one stack-slot pair (compiler.md 2b, 2c): the symptoms of
SSA value-ID recycling, which no structural idiom explains. `gopermute.py` tries structural
idioms one at a time; this tool runs a parallel hill-climb over zero-instruction edits:

  - `(x+0)` on a non-constant integer expression, and removing one again
  - no-op conversions `string(x)` / `int(x)` / `int64(x)` / `bool(x)` / `error(x)`, and removing them
  - `a == b` -> `b == a`, `a < b` -> `b > a`;  `if a != b` <-> `if !(a == b)`
  - `if cond {` -> `if v := atom; cond' {`;   `stmt` -> `stmt; _ = atom`
  - `return f(x)` -> `r0, r1 := f(x); return r0, r1`
  - `x++` <-> `x += 1` <-> `x -= -1`;  `len(s) == 0` <-> `s == ""`

Every edit stays inside one line, so the line table never moves; only lines of FUNC's body (or
--lines) are touched. Each worker builds a private copy of the unit's GOPATH (parallel builds in
one tree race; on macOS bind mounts even an atomic rename is briefly seen as a missing file).

Score (lower is better) = unmasked raw diff bytes + `*` lines of `gocmp -v cmp`
+ 1e6 per function whose size differs. --guard adds other functions to the score so that a fix
does not cost them. A candidate that does not build is dropped, and a single-line edit that
failed is never proposed again. Candidates scoring the same as the best are kept (up to 40) and
mutated in turn, so the search walks plateaus.

Writes OUT/best.go (the whole file) whenever the score improves and logs to OUT/log.txt.
It never changes the unit's own source: diff best.go against it, take what is natural, and
re-verify with gomatch / gogate. Prefer natural source first (compiler.md 2e): these knobs are a
last resort, and stacked knobs hide each other.
"""
import argparse
import os
import random
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, FIRST_COMPLETED, wait

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gotc
import gopermute

KEYWORDS = set(
    "if for return func range var go defer switch case default else select chan map struct "
    "interface type package import break continue goto fallthrough const nil true false "
    "make new len cap append copy delete panic recover string int int64 int32 uint8 bool byte error".split())

# ---------------------------------------------------------------- mutations (one line in, one line out)


def balanced_end(s, i, open_c, close_c):
    """Index just past the bracket matching s[i], skipping string, rune and raw literals; -1 if unbalanced."""
    depth = 0
    j = i
    while j < len(s):
        c = s[j]
        if c in "\"'`":
            j += 1
            while j < len(s) and s[j] != c:
                j += 2 if (s[j] == "\\" and c != "`") else 1
        elif c == open_c:
            depth += 1
        elif c == close_c:
            depth -= 1
            if depth == 0:
                return j + 1
        j += 1
    return -1


def code_mask(line):
    """(code, mask): the line cut at a // comment that is not inside a literal, and for each kept
    character whether it is inside a "string", 'rune' or single-line `raw string` literal (escapes
    honoured). Lines that open or close a multi-line raw string are excluded by raw_string_lines."""
    mask, quote, i = [], None, 0
    while i < len(line):
        c = line[i]
        if quote is None:
            if c == "/" and line[i:i + 2] == "//":
                break
            if c in "\"'`":
                quote = c
                mask.append(True)
            else:
                mask.append(False)
        else:
            mask.append(True)
            if c == "\\" and quote != "`" and i + 1 < len(line):
                mask.append(True)
                i += 1
            elif c == quote:
                quote = None
        i += 1
    return line[:len(mask)], mask


def atoms(line):
    """Spans (start, end) of expressions in a line that can be wrapped without changing its meaning
    grammatically: identifiers, selector chains, calls and index expressions, with an optional *.
    Nothing inside a string, rune or raw-string literal or a comment is ever returned."""
    code, mask = code_mask(line)
    out = []
    for m in re.finditer(r'(\*?)([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)', code):
        st, en = m.start(), m.end()
        if st > 0 and (code[st - 1] == '.' or code[st - 1].isalnum() or code[st - 1] == '_'):
            continue
        name = m.group(2)
        if name in KEYWORDS and name not in ("len", "cap"):
            continue
        if mask[st]:
            continue
        while en < len(code) and code[en] in "([":
            e2 = balanced_end(code, en, code[en], ")" if code[en] == "(" else "]")
            if e2 < 0:
                break
            en = e2
        if name in ("len", "cap") and en == m.end():
            continue
        rest = code[en:]
        if re.match(r'\s*(:=|=[^=]|\+\+|--|[-+*/|&]=|:\s|,[^()]*:=|,[^()]*=[^=])', rest):
            continue  # assignment target or composite-literal key
        before = code[:st].rstrip()
        if before.endswith(("func", "type", "var", "package", "import", "range", "go", "defer")):
            continue
        if before == "" and not rest.strip().startswith(("==", "!=", "<", ">", "+", "-")):
            continue  # an expression statement must stay a call
        out.append((st, en))
    return out


def m_pad(line, rng):
    a = atoms(line)
    if not a:
        return None
    st, en = rng.choice(a)
    return line[:st] + "(" + line[st:en] + "+0)" + line[en:]


def m_unpad(line, rng):
    idx = [m.start() for m in re.finditer(r'\+0\)', line)]
    if not idx:
        return None
    i = rng.choice(idx)
    depth = 0
    for j in range(i - 1, -1, -1):
        if line[j] == ')':
            depth += 1
        elif line[j] == '(':
            if depth == 0:
                return line[:j] + line[j + 1:i] + line[i + 3:]
            depth -= 1
    return None


def m_conv(line, rng):
    a = atoms(line)
    if not a:
        return None
    st, en = rng.choice(a)
    t = rng.choice(["string", "int", "int64", "bool", "error"])
    return line[:st] + t + "(" + line[st:en] + ")" + line[en:]


def m_unconv(line, rng):
    ms = list(re.finditer(r'\b(string|int|int64|bool|error)\(', line))
    if not ms:
        return None
    m = rng.choice(ms)
    e = balanced_end(line, m.end() - 1, "(", ")")
    if e < 0:
        return None
    return line[:m.start()] + line[m.end():e - 1] + line[e:]


_SIDE = r'[\w.*()\[\]"]+'


def m_swapcmp(line, rng):
    # both sides must end at a boundary (space, '{', ';', ')', ',' or end), so `a < b+10` is not split
    ms = list(re.finditer(r'(?<![\w.)\]])(' + _SIDE + r') (==|!=|<|>|<=|>=) (' + _SIDE + r')(?=\s*(?:\{|;|\)|,|&&|\|\||$))', line))
    if not ms:
        return None
    m = rng.choice(ms)
    a, op, b = m.groups()
    if a.count("(") != a.count(")") or b.count("(") != b.count(")"):
        return None
    flip = {"==": "==", "!=": "!=", "<": ">", ">": "<", "<=": ">=", ">=": "<="}[op]
    return line[:m.start()] + f"{b} {flip} {a}" + line[m.end():]


def m_incdec(line, rng):
    for pat, rep in ((r'(\w[\w.]*)\+\+', r'\1 += 1'), (r'(\w[\w.]*) \+= 1\b', r'\1++'),
                     (r'(\w[\w.]*)--', r'\1 -= 1'), (r'(\w[\w.]*) -= 1\b', r'\1 += -1'),
                     (r'(\w[\w.]*) \+= -1\b', r'\1--')):
        if re.search(pat, line):
            return re.sub(pat, rep, line, count=1)
    return None


def m_emptycmp(line, rng):
    for pat, rep in ((r'len\(([\w.*]+)\) == 0', r'\1 == ""'), (r'([\w.*]+) == ""', r'len(\1) == 0'),
                     (r'len\(([\w.*]+)\) > 0', r'\1 != ""'), (r'([\w.*]+) != ""', r'len(\1) > 0'),
                     (r'len\(([\w.*]+)\) != 0', r'len(\1) > 0')):
        if re.search(pat, line):
            return re.sub(pat, rep, line, count=1)
    return None


def m_ifinit(line, rng):
    m = re.match(r'^(\s*)(\}\s*else\s+)?if (.*) \{\s*$', line)
    if not m or ";" in m.group(3):
        return None
    ind, els, cond = m.group(1), m.group(2) or "", m.group(3)
    a = atoms(cond)
    if not a:
        return None
    st, en = rng.choice(a)
    v = rng.choice(["v", "x", "n", "tmp"])
    return f"{ind}{els}if {v} := {cond[st:en]}; {cond[:st]}{v}{cond[en:]} {{"


def m_blank(line, rng):
    code = line.rstrip()
    s = code.strip()
    if not s or code.endswith(("{", ",", "(", "}")) or s.startswith(("case", "default", "return", "}")):
        return None
    a = [(st, en) for st, en in atoms(line) if "(" not in line[st:en]]   # never duplicate a call
    if not a:
        return None
    st, en = rng.choice(a)
    return code + "; _ = " + line[st:en]


def m_rettmp(line, rng):
    m = re.match(r'^(\s*)return ([\w.]+\(.*\))\s*$', line)
    if not m:
        return None
    names = ", ".join(f"r{i}" for i in range(rng.choice([1, 2, 3])))
    return f"{m.group(1)}{names} := {m.group(2)}; return {names}"


def m_neg(line, rng):
    for pat, rep in ((r'if ([\w.*()\[\]]+) != ([\w.*()\[\]"]+) \{', r'if !(\1 == \2) {'),
                     (r'if !\(([\w.*()\[\]]+) == ([\w.*()\[\]"]+)\) \{', r'if \1 != \2 {'),
                     (r'if ([\w.*()\[\]]+) == ([\w.*()\[\]"]+) \{', r'if !(\1 != \2) {')):
        if re.search(pat, line):
            return re.sub(pat, rep, line, count=1)
    return None


MUTATORS = [(m_pad, 5), (m_unpad, 3), (m_conv, 2), (m_unconv, 1), (m_swapcmp, 2), (m_incdec, 1),
            (m_emptycmp, 1), (m_ifinit, 2), (m_blank, 2), (m_rettmp, 1), (m_neg, 1)]


def raw_string_lines(lines):
    """Indexes of lines that are inside, open or close a `raw string` literal. An edit there would
    change string data, which gocmp does not compare (it checks code), so they are never mutated."""
    out, inside = set(), False
    for i, line in enumerate(lines):
        code = line if inside else re.sub(r'"(?:[^"\\]|\\.)*"', '""', line).split("//")[0]
        n = code.count("`")
        if inside or n:
            out.add(i)
        if n % 2:
            inside = not inside
    return out


def mutate(text, lo, hi, rng, n, bad=frozenset()):
    """Apply n random single-line mutations inside lines lo..hi (1-based, inclusive)."""
    lines = text.split("\n")
    raw = raw_string_lines(lines)
    cand = [i for i in range(lo - 1, min(hi, len(lines)))
            if i not in raw and lines[i].strip() and not lines[i].strip().startswith("//")]
    if not cand:
        return text
    done = tries = 0
    while done < n and tries < 50:
        tries += 1
        i = rng.choice(cand)
        fn = rng.choices([m for m, _ in MUTATORS], [w for _, w in MUTATORS])[0]
        new = fn(lines[i], rng)
        if new and new != lines[i] and "\n" not in new and (i, new) not in bad:
            lines[i] = new
            done += 1
    return "\n".join(lines)


def edits(parent, child):
    return [(i, b) for i, (a, b) in enumerate(zip(parent.split("\n"), child.split("\n"))) if a != b]

# ---------------------------------------------------------------- workers

_W = {}


def _copy_tree(src, dst):
    if shutil.which("rsync"):
        subprocess.run(["rsync", "-a", "--delete", src.rstrip("/") + "/", dst.rstrip("/") + "/"], check=True)
    else:
        shutil.rmtree(dst, ignore_errors=True)
        shutil.copytree(src, dst, symlinks=True)


def score(tc, target, binp, rx):
    cmp = tc.cmp(target, binp, rx)
    if not cmp.rows:
        return None
    sc = sum(1_000_000 + abs(r.orig_size - r.cand_size) for r in cmp.rows
             if r.status == 'MISSING' or r.orig_size != r.cand_size)
    sc += tc.raw(target, binp, rx).unmasked_diffs
    v = tc.cmp(target, binp, rx, verbose=True, first_window=10_000_000).raw_stdout
    sc += sum(1 for l in v.splitlines() if l.lstrip().startswith("*"))
    return sc


def evaluate(job):
    """Build one candidate in this worker's private GOPATH and score it. Runs in a pool process."""
    config, unit_name, pkg, rel, wdir, text, rx = job
    if "tc" not in _W:
        s = gotc.settings(config)
        _W["s"], _W["tc"] = s, gotc.Toolchain(s)
        _W["unit"], _ = s.resolve_unit(unit_name)
    s, tc, unit = _W["s"], _W["tc"], _W["unit"]
    gp = os.path.join(wdir, "gopath")
    if not os.path.isdir(os.path.join(gp, "src")):
        os.makedirs(gp, exist_ok=True)
        _copy_tree(unit.src_dir, os.path.join(gp, "src"))
        if os.path.isdir(os.path.join(unit.gopath, "pkg")):
            _copy_tree(os.path.join(unit.gopath, "pkg"), os.path.join(gp, "pkg"))
    with open(os.path.join(gp, "src", rel), "w") as f:
        f.write(text)
    wunit = gotc.Unit(unit.name + "-perm", gp, pkg=pkg or unit.pkg, out="perm.bin",
                      regex=unit.regex, packages=unit.packages)
    try:
        binp, _ = tc.build(wunit, out_rel="perm.bin")
        return text, score(tc, s.image, binp, rx)
    except gotc.ToolError:
        return text, None

# ---------------------------------------------------------------- search


def func_lines(src, func_regex):
    """1-based line range of the function body named by the last component of FUNC_REGEX."""
    name = re.sub(r'[\\^$]', '', func_regex).rstrip('$').split('.')[-1].strip('()*')
    i, j = gopermute.find_go_func_span(src, name)
    return src.count("\n", 0, i) + 1, src.count("\n", 0, j) + 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="Random parallel parity permuter for Go 1.8.0 functions")
    ap.add_argument("unit", help="configured [[go18.unit]] name")
    ap.add_argument("file", help="source file (absolute, or relative to the unit's GOPATH/src)")
    ap.add_argument("func", help="gocmp regex of the target function(s)")
    ap.add_argument("--guard", help="regex of functions that must not get worse (added to the score)")
    ap.add_argument("--lines", help="LO-HI lines to mutate (default: the function's own lines)")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) // 2))
    ap.add_argument("--minutes", type=float, default=30)
    ap.add_argument("--pkg", help="package to build instead of the unit's pkg (e.g. the real main package)")
    ap.add_argument("--out", help="output directory (default BUILD/gorandperm/<func>)")
    ap.add_argument("--seed", type=int)
    ap.add_argument("--start", help="start from this version of the file (e.g. an earlier best.go) instead of the unit's own")
    ap.add_argument("--config")
    a = ap.parse_args(argv)

    s = gotc.settings(a.config)
    unit, _ = s.resolve_unit(a.unit)
    path = a.file if os.path.isabs(a.file) else os.path.join(unit.src_dir, a.file)
    rel = os.path.relpath(path, unit.src_dir)
    if rel.startswith(".."):
        raise gotc.ToolError(f"{a.file} is not inside {unit.src_dir}")
    base = open(a.start or path).read()
    lo, hi = map(int, a.lines.split("-")) if a.lines else func_lines(base, a.func)
    if a.start and base.count("\n") != open(path).read().count("\n"):
        raise gotc.ToolError(f"{a.start} has a different number of lines from {path}")
    rx = a.func if not a.guard else f"(?:{a.func})|(?:{a.guard})"
    tag = re.sub(r'[^A-Za-z0-9]+', '_', a.func).strip('_')[-60:] or "func"
    out = a.out or os.path.join(s.build, "gorandperm", tag)
    os.makedirs(out, exist_ok=True)
    wroot = os.path.join(s.build, "gorandperm-wk", tag)
    log = open(os.path.join(out, "log.txt"), "a")
    rng = random.Random(a.seed)

    def say(msg):
        line = time.strftime("%H:%M:%S ") + msg
        print(line, flush=True)
        log.write(line + "\n")
        log.flush()

    def job(wid, text):
        return (a.config, unit.name, a.pkg, rel, os.path.join(wroot, f"w{wid}"), text, rx)

    nlines = base.count("\n")
    bad = set()
    say(f"{a.func}: mutating {rel}:{lo}-{hi} with {a.workers} workers for {a.minutes:g} min")
    with ProcessPoolExecutor(a.workers) as ex:
        _, best_score = ex.submit(evaluate, job(0, base)).result()
        if best_score is None:
            say("the unmodified source does not build or matches no function")
            return 1
        best, plateau = base, [base]
        say(f"baseline score {best_score}")
        with open(os.path.join(out, "best.go"), "w") as f:
            f.write(best)
        deadline = time.time() + a.minutes * 60
        evals = fails = 0
        pending, free = {}, list(range(a.workers))

        def submit():
            wid = free.pop()
            parent = rng.choice(plateau)
            cand = mutate(parent, lo, hi, rng, rng.choice([1, 1, 1, 1, 2, 2, 3]), bad)
            if cand.count("\n") != nlines:
                cand = parent
            pending[ex.submit(evaluate, job(wid, cand))] = (wid, parent)

        while free:
            submit()
        while pending:
            done, _ = wait(list(pending), return_when=FIRST_COMPLETED)
            for fut in done:
                wid, parent = pending.pop(fut)
                free.append(wid)
                text, sc = fut.result()
                evals += 1
                if sc is None:
                    fails += 1
                    e = edits(parent, text)
                    if len(e) == 1:
                        bad.add(e[0])
                elif sc < best_score:
                    best_score, best, plateau = sc, text, [text]
                    with open(os.path.join(out, "best.go"), "w") as f:
                        f.write(best)
                    say(f"NEW BEST {sc} (eval {evals}, {fails} did not build)")
                elif sc == best_score and text not in plateau:
                    plateau.append(text)
                    if len(plateau) > 40:
                        plateau.pop(1)
                if evals % 50 == 0:
                    say(f"evals {evals}, {fails} did not build, best {best_score}, plateau {len(plateau)}")
            if time.time() < deadline and best_score > 0:
                while free:
                    submit()
    say(f"DONE best {best_score} after {evals} evals; {os.path.join(out, 'best.go')}")
    return 0 if best_score == 0 else 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except gotc.ToolError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
