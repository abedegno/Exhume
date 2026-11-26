r"""Go 1.8.0 random parity permuter (profile: golang-180).

    python3 gorandperm.py UNIT FILE.go FUNC_REGEX [--guard REGEX] [--lines LO-HI]
                          [--workers N] [--minutes M] [--pkg PKG] [--out DIR] [--config PATH]
                          [--start FILE] [--fast] [--sweep | --sweep-only] [--seed N]

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
  - `stmt; if pkg.ErrX == nil {}` on a package-level variable of an imported std package: no
    instruction, but `dec` splits the load and takes 3 recycled IDs there (5 for a slice)
  - `stmt; nop()` for an empty function the package defines (a new block: compiler.md 2i), and
    the length of a dead run `zzN := len(x) + len(x) + ...; _ = zzN`
  - regrouping an `&&` / `||` chain, `a && b && c` <-> `a && (b && c)`; a folded-away operand,
    `a && true && b` or a repeated test; `if c { ...; return }` + rest <-> `... } else { rest }`
    with the closing brace on the line that closes the enclosing block. These three are ordinary
    source, and the real source may use them (compiler.md 2h)

--fast scores from the package's object instead of a whole build (gofast.py); --sweep first tries
every single edit, the best distinct ones in pairs, then a beam (as armcc5's armpermute2 does).
The work directories on the host and in the container carry the --out directory's name, and a
second run with the same name is refused while the first is alive: two runs in one directory
compile each other's files and both report scores for the wrong text.

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
import hashlib
import os
import queue
import random
import re
import shutil
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, FIRST_COMPLETED, wait

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


def m_alias(line, rng):
    """`stmt; nK := atom; _ = nK`: one more *named* value. Go 1.8's `decompose builtin` pass takes
    2 recycled IDs per named string/interface value and 3 per named slice (compiler.md 2b)."""
    code = line.rstrip()
    s = code.strip()
    if not s or code.endswith(("{", ",", "(", "}")) or s.startswith(("case", "default", "return", "}")):
        return None
    a = [(st, en) for st, en in atoms(line) if "(" not in line[st:en]]
    if not a:
        return None
    st, en = rng.choice(a)
    v = "n%d" % rng.randrange(1000, 9999)
    return f"{code}; {v} := {line[st:en]}; _ = {v}"


def m_boolfold(line, rng):
    """`if c {` <-> `if c == true {` / `if c != false {` / `if !!c {`: the comparison is folded away in
    `opt`, which frees one to three values in that block without adding a block or a `dec` pop."""
    m = re.match(r'^(\s*)((?:\}\s*else\s+)?if) (.*) \{\s*$', line)
    if not m or ";" in m.group(3) or "&&" in m.group(3) or "||" in m.group(3):
        return None
    ind, kw, c = m.groups()
    for suf in (" == true", " != false"):
        if c.endswith(suf):
            c0 = c[:-len(suf)]
            return f"{ind}{kw} {c0[1:-1] if c0.startswith('(') and c0.endswith(')') else c0} {{"
    if c.startswith("!!"):
        return f"{ind}{kw} {c[2:]} {{"
    form = rng.choice(["(%s) == true", "(%s) != false", "!!(%s)"])
    return f"{ind}{kw} {form % c} {{"


def m_viatmp(line, rng):
    """`x = f(...)` <-> `tK := f(...); x = tK`: assigning an inlined constructor's result through a
    local changes how many nil checks `opt` discards there (3 -> 1 for `&T{...}`), and adds a local."""
    m = re.match(r'^(\s*)([A-Za-z_][\w.]*) = (t\d{4}) /\*via\*/$', line)
    m2 = re.match(r'^(\s*)(t\d{4}) := (.*); ([A-Za-z_][\w.]*) = \2$', line)
    if m2:
        return f"{m2.group(1)}{m2.group(4)} = {m2.group(3)}"
    m = re.match(r'^(\s*)([A-Za-z_][\w.]*) = ([A-Za-z_][\w.]*\(.*\))\s*$', line)
    if not m or "," in m.group(2):
        return None
    t = "t%d" % rng.randrange(1000, 9999)
    return f"{m.group(1)}{t} := {m.group(3)}; {m.group(2)} = {t}"


def m_nop(line, rng):
    """Toggle a call to an empty function (`func nop() {}`, which the package must define) at the
    end of a line. The call is inlined and emits no code, but its return label leaves a dead block,
    so the phis at the next join survive until `opt` and are freed there: one freed value per live
    variable, at a point the other levers cannot reach."""
    s = line.rstrip()
    for suf in ("; nop()", " nop()"):
        if s.endswith(suf):
            return s[:-len(suf)]
    if s.endswith("nop() }"):
        return s[:-len("nop() }")] + "}"
    t = s.strip()
    if t == "}":
        return s[:-1] + "nop() }"
    if t.startswith("}") or t.startswith("//") or t.startswith("case ") or t.startswith("default"):
        return None
    if t.endswith("{"):
        return s + " nop()"
    if t.endswith(")") or re.match(r'.*[\w"\]]$', t):
        return s + "; nop()"
    return None


def m_deadrun(line, rng):
    """Change the length of a dead-value run `zzK := len(x) + len(x) + ...; _ = zzK`. Each term is
    a value early deadcode frees, so the count sets how many recycled IDs lie under `opt`'s own."""
    m = re.match(r'^(\s*zz\d+ := )(len\([\w.]+\))((?: \+ len\([\w.]+\))*)(; _ = zz\d+.*)$', line)
    if not m:
        return None
    n = 1 + m.group(3).count("+")
    n2 = max(1, n + rng.choice([-3, -2, -1, -1, 1, 1, 2, 3]))
    if n2 == n:
        return None
    return m.group(1) + " + ".join([m.group(2)] * n2) + m.group(4)


# Package-level interface and slice variables of the standard library, by import path. A load of
# one needs no nil check, so `if X == nil {}` emits no instruction, but its condition stays alive
# until the `dec` pass, which splits the load and so takes recycled value IDs (3 for an interface,
# 5 for a slice) at that point of the function: the loads after it get other IDs.
STD_GLOBALS = {
    "strconv": ["strconv.ErrRange == nil", "strconv.ErrSyntax == nil"],
    "io": ["io.EOF == nil", "io.ErrUnexpectedEOF == nil", "io.ErrShortWrite == nil"],
    "os": ["os.ErrNotExist == nil", "os.ErrExist == nil", "os.ErrPermission == nil", "len(os.Args) > 0"],
    "net/http": ["http.ErrNoCookie == nil", "http.ErrHijacked == nil", "http.ErrContentLength == nil",
                 "http.ErrMissingFile == nil", "http.ErrLineTooLong == nil", "http.ErrBodyNotAllowed == nil"],
    "bufio": ["bufio.ErrTooLong == nil", "bufio.ErrBufferFull == nil"],
    "bytes": ["bytes.ErrTooLarge == nil"],
}
GLOBALS = []


def set_globals(text, pkg_dir=None):
    """The conditions m_glob may use: those of the std packages TEXT imports under their own name,
    and the package's own `var X = errors.New(...)` (3 IDs) and `var X = []byte(...)` (5 IDs)."""
    GLOBALS[:] = [c for path, conds in STD_GLOBALS.items()
                  if re.search(r'^\s*(?:import\s+)?"%s"\s*$' % re.escape(path), text, re.M) for c in conds]
    own = []
    for fn in sorted(os.listdir(pkg_dir)) if pkg_dir and os.path.isdir(pkg_dir) else []:
        if not fn.endswith(".go") or fn.endswith("_test.go"):
            continue
        with open(os.path.join(pkg_dir, fn), errors="replace") as f:
            for ln in f:
                m = re.match(r'var (\w+) = (errors\.New\(|\[\]byte\()', ln)
                if m and m.group(1) != "_":
                    own.append(f"{m.group(1)} == nil" if m.group(2).startswith("errors") else f"len({m.group(1)}) > 0")
    GLOBALS.extend(own[:12])


def m_glob(line, rng):
    """Append `; if G == nil {}` for a package-level variable G to a statement, or take one off."""
    s = line.rstrip()
    have = re.findall(r'; if ([\w.() ]+ (?:== nil|> 0)) \{\}', s)
    have = [h for h in have if h in GLOBALS]
    if have and rng.random() < 0.4:
        return s.replace(f"; if {rng.choice(have)} {{}}", "", 1)
    t = s.strip()
    free = [g for g in GLOBALS if g not in have]
    if not free or not t or t.startswith(("}", "//", "case ", "default")) or t.endswith(("{", ",", "(")):
        return None
    if not (t.endswith(")") or t.endswith("{}") or re.match(r'.*[\w"\]]$', t)):
        return None
    if re.match(r'^(return|continue|break|goto)\b', t):
        return None
    return s + f"; if {rng.choice(free)} {{}}"


def _split_top(expr, op):
    """EXPR cut at the top-level occurrences of OP ('&&' or '||'): outside brackets and literals."""
    code, mask = code_mask(expr)
    if len(code) != len(expr):
        return [expr]
    parts, depth, last, i = [], 0, 0, 0
    while i < len(expr):
        c = expr[i]
        if not mask[i]:
            if c in "([{":
                depth += 1
            elif c in ")]}":
                depth -= 1
            elif depth == 0 and expr.startswith(" " + op + " ", i):
                parts.append(expr[last:i])
                last = i + len(op) + 2
                i = last
                continue
        i += 1
    parts.append(expr[last:])
    return parts


def _wrapped(x, op):
    """X is `( ... )` as a whole and its inside is a top-level OP chain."""
    return x.startswith("(") and balanced_end(x, 0, "(", ")") == len(x) and len(_split_top(x[1:-1], op)) > 1


def m_assoc(line, rng):
    """Regroup an `&&` or `||` chain of three or more operands: `a && b && c` <-> `a && (b && c)`.
    Evaluation order and branches are the same, so the code is; but each grouping builds the
    short-circuit blocks and their phis in another order, which moves value and block IDs."""
    for pat in (r'^(\s*(?:\}\s*else\s+)?if )(.*)( \{\s*)$', r'^(\s*return )(.*)()$', r'^(\s*[\w., ]+ :?= )(.*)()$'):
        m = re.match(pat, line.rstrip())
        if m:
            break
    else:
        return None
    head, expr, tail = m.groups()
    if ";" in expr:
        return None
    for op in rng.sample(["&&", "||"], 2):
        outer = _split_top(expr, "||")
        if op == "||":
            parts, rebuild = outer, (lambda ps: " || ".join(ps))
        else:
            cand = [k for k, x in enumerate(outer) if len(_split_top(x, "&&")) > 1]
            if not cand:
                continue
            k = rng.choice(cand)
            parts = _split_top(outer[k], "&&")
            rebuild = (lambda ps, k=k: " || ".join(outer[:k] + [" && ".join(ps)] + outer[k + 1:]))
        grouped = [i for i, x in enumerate(parts) if _wrapped(x, op)]
        if grouped and (len(parts) < 3 or rng.random() < 0.4):
            i = rng.choice(grouped)
            return head + rebuild(parts[:i] + _split_top(parts[i][1:-1], op) + parts[i + 1:]) + tail
        if len(parts) < 3:
            continue
        i = rng.randrange(0, len(parts) - 1)
        j = rng.randrange(i + 1, len(parts))
        if i == 0 and j == len(parts) - 1:
            j -= 1
            if j == i:
                continue
        return head + rebuild(parts[:i] + ["(" + (" " + op + " ").join(parts[i:j + 1]) + ")"] + parts[j + 1:]) + tail
    return None


def else_wrap(lines, lo, hi, rng):
    """`if c { ...; return }` followed by the rest of its block  <->  `if c { ...; return } else { rest }`:
    [(line index, new line), (line index, new line)] or None. The closing brace of the `else` goes
    on the line that closes the enclosing block, so no line moves. The branch and the code after it
    are the same instructions, but the join block of the `if` is created before the `else` body,
    which renumbers the blocks after it and changes which variables get a copy at a block edge."""
    ind = lambda s: len(s) - len(s.lstrip("\t"))
    cand = []
    for i in range(lo, min(hi, len(lines))):
        t = lines[i].strip()
        if t not in ("}", "} else {"):
            continue
        d = ind(lines[i])
        p = i - 1
        while p > lo and not lines[p].strip():
            p -= 1
        if not re.match(r'^return\b', lines[p].strip()) or ind(lines[p]) != d + 1:
            continue
        for j in range(i + 1, min(hi, len(lines))):
            if lines[j].strip() and ind(lines[j]) < d:
                break
        else:
            continue
        if not lines[j].strip().startswith("}"):
            continue
        if t == "}" and any(lines[q].strip() and not lines[q].strip().startswith("//") for q in range(i + 1, j)):
            cand.append([(i, lines[i].rstrip()[:-1] + "} else {"), (j, lines[j][:ind(lines[j])] + "}" + lines[j].lstrip("\t"))])
        elif t == "} else {" and lines[j].strip().startswith("}}"):
            cand.append([(i, lines[i].rstrip()[:-len("} else {")] + "}"), (j, lines[j][:ind(lines[j])] + lines[j].lstrip("\t")[1:])])
    return rng.choice(cand) if cand else None


def m_trueop(line, rng):
    """Add or remove a constant operand in a condition: `a && b` <-> `a && true && b`, `a || b` <->
    `a || false || b`. The operand is folded away (no instruction), but it is one more
    short-circuit block pair while SSA is built: one more set of copies of the live variables."""
    for pat in (r'^(\s*(?:\}\s*else\s+)?if )(.*)( \{\s*)$', r'^(\s*return )(.*)()$', r'^(\s*[\w., ]+ :?= )(.*)()$'):
        m = re.match(pat, line.rstrip())
        if m:
            break
    else:
        return None
    head, expr, tail = m.groups()
    if ";" in expr or not re.search(r'&&|\|\||[<>!=]=|[<>]', expr):
        return None
    outer = _split_top(expr, "||")
    if "false" in outer and rng.random() < 0.5:
        outer.remove("false")
        return head + " || ".join(outer) + tail
    k = rng.randrange(len(outer))
    parts = _split_top(outer[k], "&&")
    dup = [i for i, x in enumerate(parts) if x == "true" or parts.count(x) > 1 or
           (re.search(r' (!= 0|>= 0|>= 1)$', x) and re.sub(r' (!= 0|>= 0|>= 1)$', " > 0", x) in parts)]
    if dup and rng.random() < 0.6:
        del parts[rng.choice(dup)]
    elif len(outer) > 1 and rng.random() < 0.25:
        outer.insert(rng.randrange(len(outer) + 1), "false")
        return head + " || ".join(outer) + tail
    else:
        if len(parts) == 1 and len(outer) == 1 and not re.search(r'[<>!=]=|[<>]|^!|\)$', parts[0]):
            return None
        # besides `true`: a test already in the chain, or one it implies (`x > 0` -> `x != 0`,
        # `x >= 0`); the compiler proves the repeat and drops its branch
        extra = ["true", "true"] + [x for x in parts if x != "true"]
        for x in parts:
            mm = re.match(r'^(.*) > 0$', x)
            if mm:
                extra += [mm.group(1) + " != 0", mm.group(1) + " >= 0", mm.group(1) + " >= 1"]
        parts.insert(rng.randrange(len(parts) + 1), rng.choice(extra))
    outer[k] = " && ".join(parts)
    return head + " || ".join(outer) + tail


MUTATORS = [(m_trueop, 2), (m_assoc, 3), (m_glob, 4), (m_deadrun, 4), (m_nop, 4), (m_viatmp, 1), (m_boolfold, 3), (m_alias, 2), (m_pad, 5), (m_unpad, 3), (m_conv, 2), (m_unconv, 1), (m_swapcmp, 2), (m_incdec, 1),
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


FREE_MARK = "/*gp*/"


def scope_idents(lines, lo, i):
    """Identifiers declared (`:=`, `var`, func parameters) on lines lo..i-1: guesses, the build decides."""
    out = []
    for l in lines[lo:i]:
        l = l.split("//")[0]
        for m in re.finditer(r'((?:[A-Za-z_]\w*\s*,\s*)*[A-Za-z_]\w*)\s*:=', l):
            out += [x.strip() for x in m.group(1).split(",")]
        m = re.match(r'\s*var\s+([A-Za-z_]\w*)', l)
        if m:
            out.append(m.group(1))
        m = re.match(r'\s*func\b.*?\(([^)]*)\)\s*[^(]*\{\s*$', l)
        if m:
            out += [x.strip().split(" ")[0] for x in m.group(1).split(",") if x.strip()]
    return [x for x in out if x and x != "_" and x not in KEYWORDS]


def m_free(lines, i, lo, rng):
    """Fill a blank or lone-`//` line with a statement that emits no instructions, or empty one again.
    An empty branch keeps its condition's load alive until `dec` (5 IDs for a slice, 3 for a string);
    an unused declaration adds an entry to the frame's variable list (compiler.md 2b, 2c)."""
    if FREE_MARK in lines[i]:
        return ""
    ids = scope_idents(lines, lo, i)
    ind = ""
    for j in range(i + 1, min(i + 6, len(lines))):
        if lines[j].strip():
            ind = re.match(r'\s*', lines[j]).group(0)
            break
    k = rng.randrange(1000, 9999)
    forms = [f"var d{k} int; _ = d{k}", f"var d{k} string; _ = d{k}", f"var d{k} error; _ = d{k}",
             f"var d{k} []byte; _ = d{k}", "if false {}", "nop()", "nop()"]
    if ids:
        x = rng.choice(ids)
        forms += [f"if len({x}) > 0 {{}}", f"if len({x}) == 0 {{}}", f"if {x} == nil {{}}", f"if {x} != nil {{}}",
                  f'if {x} == "" {{}}', f"if len(*{x}) > 0 {{}}", f"n{k} := {x}; _ = n{k}", f"if {x} == 0 {{}}"] * 2
        forms += [f"zz{k} := " + " + ".join([f"len({x})"] * rng.randrange(1, 12)) + f"; _ = zz{k}"] * 3
    if GLOBALS:
        forms += ["; ".join(f"if {g} {{}}" for g in rng.sample(GLOBALS, rng.randrange(1, min(4, len(GLOBALS)) + 1)))] * 6
    return ind + rng.choice(forms) + " " + FREE_MARK


def mutate(text, lo, hi, rng, n, bad=frozenset()):
    """Apply n random single-line mutations inside lines lo..hi (1-based, inclusive)."""
    lines = text.split("\n")
    raw = raw_string_lines(lines)
    cand = [i for i in range(lo - 1, min(hi, len(lines)))
            if i not in raw and lines[i].strip() and not lines[i].strip().startswith("//")]
    free = [i for i in range(lo, min(hi - 1, len(lines)))
            if i not in raw and (lines[i].strip() in ("", "//") or FREE_MARK in lines[i])]
    if not cand:
        return text
    done = tries = 0
    while done < n and tries < 50:
        tries += 1
        if rng.random() < 0.04:
            ew = else_wrap(lines, lo - 1, hi, rng)
            if ew and all((i, new) not in bad for i, new in ew):
                for i, new in ew:
                    lines[i] = new
                done += 1
                continue
        if free and rng.random() < 0.3:
            i = rng.choice(free)
            new = m_free(lines, i, lo - 1, rng)
            if new == "" and lines[i].strip() in ("", "//"):
                continue
            if (i, new) not in bad:
                lines[i] = new
                done += 1
            continue
        i = rng.choice(cand)
        fn = rng.choices([m for m, _ in MUTATORS], [w for _, w in MUTATORS])[0]
        new = fn(lines[i], rng)
        if new and new != lines[i] and "\n" not in new and (i, new) not in bad:
            lines[i] = new
            done += 1
    return "\n".join(lines)


def strip_variants(line):
    """Every way to take one zero-instruction construct off a line (the reverse of the probes)."""
    out, s = [], line.rstrip()
    if FREE_MARK in s:
        out.append("")
    for g in GLOBALS:
        if f"; if {g} {{}}" in s:
            out.append(s.replace(f"; if {g} {{}}", "", 1))
    for pat in (r'; nop\(\)', r' nop\(\)(?=\s*$)', r'nop\(\) (?=\})', r'; _ = [\w.]+',
                r'; n\d{4} := [\w.]+; _ = n\d{4}', r'; var d\d{4} [\w\[\]]+; _ = d\d{4}'):
        for m in re.finditer(pat, s):
            out.append(s[:m.start()] + s[m.end():])
    for m in re.finditer(r'\(([\w.\[\]()]+)\+0\)', s):
        out.append(s[:m.start()] + m.group(1) + s[m.end():])
    return [o for o in dict.fromkeys(out) if o != s]


def _norm(new):
    """A line with its generated names made equal, so two draws that differ only in a name count once."""
    return re.sub(r'\b([ndt]|zz)\d{4}\b', r'\1N', new)


def list_singles(text, lo, hi, draws=10, free_draws=40):
    """Every single-line edit of lines lo..hi the mutators can make: [(line index, new line, rule)].
    Each (line, mutator) is drawn with fixed seeds; removals (strip_variants) are listed in full."""
    lines = text.split("\n")
    raw = raw_string_lines(lines)
    out, seen = [], set()

    def add(i, new, rule):
        if new is None or new == lines[i] or "\n" in new or (i, _norm(new)) in seen:
            return
        seen.add((i, _norm(new)))
        out.append((i, new, rule))

    for i in range(lo - 1, min(hi, len(lines))):
        if i in raw:
            continue
        t = lines[i].strip()
        blank = t in ("", "//") or FREE_MARK in lines[i]
        if blank and lo <= i < hi - 1:
            for k in range(free_draws):
                new = m_free(lines, i, lo - 1, random.Random(f"free/{i}/{k}"))
                if not (new == "" and t in ("", "//")):
                    add(i, new, "free")
        if not t or t.startswith("//"):
            continue
        for new in strip_variants(lines[i]):
            add(i, new, "strip")
        for fn, _ in MUTATORS:
            for k in range(draws):
                add(i, fn(lines[i], random.Random(f"{fn.__name__}/{i}/{k}")), fn.__name__[2:])
    return out


def apply_edits(text, eds):
    """TEXT with the (line index, new line, rule) edits applied; None when two touch one line."""
    lines = text.split("\n")
    if len({i for i, _, _ in eds}) != len(eds):
        return None
    for i, new, _ in eds:
        lines[i] = new
    return "\n".join(lines)


def sweep(base, lo, hi, score_many, say, out, pairs=40, beam=10, depth=6, deadline=None):
    """Singles, then the best distinct singles two at a time, then a beam (armpermute2's stage).
    score_many(texts) -> [(score or None, object key)]. Returns [(score, text, edits)] best first."""
    singles = list_singles(base, lo, hi)
    (base_sc, base_key), = score_many([base])
    say(f"sweep: {len(singles)} single edits, start {base_sc}")
    res = score_many([apply_edits(base, [e]) for e in singles])
    rows, by_key = [], {}
    for e, (sc, key) in zip(singles, res):
        rows.append((sc, key, e))
        if sc is not None and key != base_key and (key not in by_key or sc < by_key[key][0]):
            by_key[key] = (sc, [e])
    with open(os.path.join(out, "sweep.tsv"), "w") as f:
        for sc, key, (i, new, rule) in rows:
            f.write(f"{'' if sc is None else sc}\t{(key or '')[:12]}\t{rule}\t{i + 1}\t{new.strip()}\n")
    ok = sum(1 for sc, _, _ in rows if sc is not None)
    say(f"sweep: {ok} compiled, {len(by_key)} distinct objects other than the start's, "
        f"best single {min([v[0] for v in by_key.values()], default=None)}")
    found = dict(by_key)                               # key -> (score, edits)
    top = sorted(by_key.values(), key=lambda v: v[0])[:pairs]

    def level(cands, what):
        texts = [(eds, apply_edits(base, eds)) for eds in cands]
        texts = [(eds, t) for eds, t in texts if t is not None]
        new = []
        for (eds, _), (sc, key) in zip(texts, score_many([t for _, t in texts])):
            if sc is not None and key != base_key and (key not in found or sc < found[key][0]):
                found[key] = (sc, eds)
                new.append((sc, eds))
        new.sort(key=lambda v: v[0])
        say(f"sweep: {what}: {len(texts)} tried, {len(new)} new objects, best {new[0][0] if new else None}")
        return new

    front = level([a[1] + b[1] for n, a in enumerate(top) for b in top[n + 1:]], "pairs")
    last = min([v[0] for v in top], default=base_sc)
    for d in range(3, depth + 1):
        if not front or (deadline and time.time() > deadline) or front[0][0] == 0:
            break
        if front[0][0] >= last and d > 3:
            break
        last = front[0][0]
        front = level([f[1] + t[1] for f in front[:beam] for t in top], f"{d} edits")
    best = sorted(found.values(), key=lambda v: (v[0], len(v[1])))
    return [(sc, apply_edits(base, eds), eds) for sc, eds in best[:pairs]], base_sc


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
    if wdir not in _W.setdefault("synced", set()):  # a copy left by an earlier run may be stale
        _W["synced"].add(wdir)
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
    ap.add_argument("--fast", action="store_true",
                    help="score from the package's object (gofast.py: one compile, no link); a 0 is confirmed by a whole build")
    ap.add_argument("--sweep", action="store_true",
                    help="with --fast: first try every single edit, pairs of the best, then a beam")
    ap.add_argument("--sweep-only", action="store_true", help="stop after the sweep")
    ap.add_argument("--config")
    a = ap.parse_args(argv)

    s = gotc.settings(a.config)
    unit, _ = s.resolve_unit(a.unit)
    path = a.file if os.path.isabs(a.file) else os.path.join(unit.src_dir, a.file)
    rel = os.path.relpath(path, unit.src_dir)
    if rel.startswith(".."):
        raise gotc.ToolError(f"{a.file} is not inside {unit.src_dir}")
    base = open(a.start or path).read()
    set_globals(base, os.path.dirname(path))
    lo, hi = map(int, a.lines.split("-")) if a.lines else func_lines(base, a.func)
    if a.start and base.count("\n") != open(path).read().count("\n"):
        raise gotc.ToolError(f"{a.start} has a different number of lines from {path}")
    rx = a.func if not a.guard else f"(?:{a.func})|(?:{a.guard})"
    tag = re.sub(r'[^A-Za-z0-9]+', '_', a.func).strip('_')[-60:] or "func"
    out = a.out or os.path.join(s.build, "gorandperm", tag)
    os.makedirs(out, exist_ok=True)
    if a.out:
        # the work directories (host and container) are named by the tag: two runs on one function
        # with the same tag would compile into each other's files, so the output directory is part of it
        tag = (tag + "_" + re.sub(r'[^A-Za-z0-9]+', '_', os.path.basename(os.path.normpath(out))).strip('_'))[-60:]
    wroot = os.path.join(s.build, "gorandperm-wk", tag)
    os.makedirs(wroot, exist_ok=True)
    lock = os.path.join(wroot, "run.pid")
    if os.path.exists(lock):
        try:
            other = int(open(lock).read().strip() or 0)
            os.kill(other, 0)
        except (ValueError, ProcessLookupError, PermissionError):
            other = 0
        if other and other != os.getpid():
            raise gotc.ToolError(f"another run (pid {other}) is using {wroot}: give this one its own --out")
    with open(lock, "w") as f:
        f.write(str(os.getpid()))
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
    seen = {hashlib.sha1(base.encode()).digest()}
    say(f"{a.func}: mutating {rel}:{lo}-{hi} with {a.workers} workers for {a.minutes:g} min"
        + (" (fast)" if a.fast else ""))
    fast = None
    if a.fast:
        import gofast
        fast = gofast.Fast(s, gotc.Toolchain(s), unit, rel, rx, tag).prepare(
            a.workers, os.path.join(wroot, "fast"))

        def run(wid, text):
            return text, fast.evaluate(wid, text)[0]
        pool = ThreadPoolExecutor(a.workers)
        wids = queue.Queue()
        for n in range(a.workers):
            wids.put(n)

        def one(text):
            wid = wids.get()
            try:
                return fast.evaluate(wid, text)
            finally:
                wids.put(wid)

        def score_many(texts):
            return list(pool.map(one, texts))
    else:
        def run(wid, text):
            return evaluate(job(wid, text))
        pool = ProcessPoolExecutor(a.workers)
    t0 = time.time()
    with pool as ex:
        if a.fast:
            _, best_score = run(0, base)
            _, whole = evaluate(job(0, base))
            say(f"whole-build score of the start {whole}")
        else:
            _, best_score = ex.submit(evaluate, job(0, base)).result()
        if best_score is None:
            say("the unmodified source does not build or matches no function")
            return 1
        best, plateau = base, [base]
        say(f"baseline score {best_score}")
        if fast and (a.sweep or a.sweep_only):
            ranked, _ = sweep(base, lo, hi, score_many, say, out, deadline=time.time() + a.minutes * 60)
            for n, (sc, text, eds) in enumerate(ranked[:10]):
                say(f"sweep result {n}: {sc}  " + "; ".join(f"L{i + 1} {rule}" for i, _, rule in eds))
            good = [(sc, text) for sc, text, _ in ranked if sc <= best_score]
            if good and good[0][0] == 0:
                _, whole = evaluate(job(0, good[0][1]))
                say(f"object score 0, whole-build score {whole}")
                if whole != 0:
                    good = [g for g in good if g[0] > 0]
            if good:
                if good[0][0] < best_score:
                    best_score, best = good[0]
                    say(f"NEW BEST {best_score} (sweep)")
                plateau = [best] + [t for sc, t in good if sc == best_score and t != best][:39]
            if a.sweep_only:
                with open(os.path.join(out, "best.go"), "w") as f:
                    f.write(best)
                say(f"DONE best {best_score} after the sweep; {os.path.join(out, 'best.go')}")
                return 0 if best_score == 0 else 2
        with open(os.path.join(out, "best.go"), "w") as f:
            f.write(best)
        deadline = time.time() + a.minutes * 60
        evals = fails = 0
        pending, free = {}, list(range(a.workers))

        def submit():
            wid = free.pop()
            parent = rng.choice(plateau)
            for _ in range(20):          # a text already tried is not compiled again
                cand = mutate(parent, lo, hi, rng, rng.choice([1, 1, 1, 1, 2, 2, 3]), bad)
                if cand.count("\n") != nlines:
                    cand = parent
                h = hashlib.sha1(cand.encode()).digest()
                if h not in seen:
                    seen.add(h)
                    break
            if fast:
                pending[ex.submit(run, wid, cand)] = (wid, parent)
            else:
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
                if fast and sc == 0:      # relocated fields are not compared: confirm by a whole build
                    _, sc = evaluate(job(0, text))
                    say(f"object score 0, whole-build score {sc}")
                    if sc != 0:
                        sc = None if sc is None else max(sc, 1)
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
                    say(f"evals {evals}, {fails} did not build, best {best_score}, plateau {len(plateau)}, "
                        f"{evals / max(time.time() - t0, 1e-9):.1f}/s")
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
