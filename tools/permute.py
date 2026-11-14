"""Permuter for Borland C++ 2.0 code: tries small rewrites of one function's C source until
it compiles to the target bytes, using the bcc20 port's compile servers (tools/bcc20.py's
Pool), several variants at once.

    python3 tools/permute.py FILE.C FUNCTION (--target-obj T.OBJ | --target-bin F.BIN)
        [--opts "-ml -O -G"] [--root DIR] [--headers DIR]... [--jobs N] [--nice N]
        [--iterations N] [--seed S] [--whole-file] [--no-snapshot] [--out BEST.C]

--jobs caps the compiles at once (default: BCC20_WORKERS, else one fewer than the cores) and
--nice lowers their priority.  --headers DIR gives the .H files of DIR to every compile
under their names (a project's own headers); --root stands for C:\\ (the toolchain's).
Variants are compiled from the file cut after the function's closing brace when that gives
the function exactly the code it has in the whole file (BCC compiles one function at a time,
but a near call to a function defined later in the file, or static functions that follow it
before the next public, depend on the rest): the start compiles both and checks.  The
rewrites never add or remove a call, so the check holds for every variant.  In borland-
reborn's bcc20 sources the cut is exact for 654 of 849 public functions.  --whole-file always
compiles the whole file.

Where the host can (POSIX), the file up to the function's line is compiled once in every
server and paused (a snapshot: tools/bcc20.py's Pool.snapshot), and each variant is finished
on a copy of that compile, after a start check that this gives the function its whole-file
code (it did for all 849 public functions of borland-reborn's bcc20 sources): about 1000
variants a second with 11 workers on GENERATE_BITFIELD_STORE, against 420 without.
--no-snapshot compiles each variant from the start.

The target is the function's code: from an object file (the public FUNCTION, or _FUNCTION,
to the next public or the segment's end), or raw bytes cut from the original program.  A
candidate's score counts the bytes of its function's code that differ from the target,
ignoring the bytes fixups fill in (in either), plus the difference in length; 0 is a match.

Rewrites, each keeping the meaning: swapping the operands of a commutative operator
(+ * & | ^ == !=; an operand is a name or number with any subscripts, calls and member
accesses, or a parenthesised expression), swapping two adjacent local declarations or two
names in one declaration, adding or dropping `register` on a local declaration,
`a += b;` <-> `a = a + b;` for a simple target, and `i++;` <-> `++i;`.  A rewrite that scores no worse is kept (so the
search can cross plateaus); the best source so far is written to --out.
"""
import argparse
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bcc20   # noqa: E402
import omf     # noqa: E402

TOKEN = re.compile(r"\s+|/\*.*?\*/|//[^\n]*|[A-Za-z_]\w*|0[xX][0-9A-Fa-f]+[uUlL]*|\d+[uUlL]*|"
                   r"'(?:\\.|[^'])'|\"(?:\\.|[^\"])*\"|->|\+\+|--|<<=|>>=|<<|>>|<=|>=|==|!=|&&|"
                   r"\|\||[-+*/%&|^]=|.", re.S)
COMMUTATIVE = {"+", "*", "&", "|", "^", "==", "!="}
# C's binary operators by precedence (higher binds tighter)
PRECEDENCE = {"*": 13, "/": 13, "%": 13, "+": 12, "-": 12, "<<": 11, ">>": 11, "<": 10, ">": 10,
              "<=": 10, ">=": 10, "==": 9, "!=": 9, "&": 8, "^": 7, "|": 6, "&&": 5, "||": 4}
SIMPLE = re.compile(r"[A-Za-z_]\w*$|0[xX][0-9A-Fa-f]+[uUlL]*$|\d+[uUlL]*$")


OLD_STYLE_DECL = re.compile(r"[A-Za-z_][\w \t\r\n*\[\],]*;")


def function_span(text, name):
    """(start, end) of FUNCTION's body braces in text"""
    return definition(text, name)[1:]


def definition(text, name):
    """(name position, start, end) of FUNCTION's definition in text: the first `NAME (...)`
    followed, after any old-style parameter declarations (`int a;`), by `{` (a plain scan: a
    regular expression for this backtracks without end on some files); start and end are
    its body's braces"""
    for m in re.finditer(r"\b%s\s*\(" % re.escape(name), text):
        i, depth = m.end() - 1, 0
        while i < len(text):                        # the parameter list's closing parenthesis
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            if depth == 0:
                break
            i += 1
        i += 1
        while i < len(text):                        # old-style declarations, then the body
            while i < len(text) and text[i].isspace():
                i += 1
            if text.startswith("/*", i):
                k = text.find("*/", i + 2)
                i = len(text) if k < 0 else k + 2
                continue
            if i < len(text) and text[i] == "{":
                break
            decl = OLD_STYLE_DECL.match(text, i)    # `int a, *b[4];`
            if decl:
                i = decl.end()
                continue
            i = len(text)                           # a call or a prototype, not the definition
        if i >= len(text):
            continue
        start, depth = i, 0
        while i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            if depth == 0:
                return m.start(), start, i + 1
            i += 1
        raise SystemExit(f"permute: {name}'s body does not end")
    raise SystemExit(f"permute: no definition of {name}")


def tokens(body):
    return TOKEN.findall(body)


def significant(toks):
    return [i for i, t in enumerate(toks) if not t.isspace() and not t.startswith(("/*", "//"))]


def operand_end(toks, sig, k):
    """the index in sig of the last token of the operand starting at sig[k] (an identifier
    or number with any [..], (..), . and -> after it, or a parenthesised expression), or
    None"""
    t = toks[sig[k]]
    if t == "(":
        k = closing(toks, sig, k)
        if k is None:
            return None
    elif not SIMPLE.match(t):
        return None
    while k + 1 < len(sig):
        nxt = toks[sig[k + 1]]
        if nxt in ("[", "("):
            k = closing(toks, sig, k + 1)
            if k is None:
                return None
        elif nxt in (".", "->") and k + 2 < len(sig) and SIMPLE.match(toks[sig[k + 2]]):
            k += 2
        else:
            break
    return k


def closing(toks, sig, k):
    pairs = {"(": ")", "[": "]"}
    depth = 0
    for j in range(k, len(sig)):
        t = toks[sig[j]]
        if t in pairs:
            depth += 1
        elif t in (")", "]"):
            depth -= 1
            if depth == 0:
                return j
    return None


def operand_start(toks, sig, k):
    """the index in sig of the first token of the operand ending at sig[k], or None"""
    j = k
    while True:
        t = toks[sig[j]]
        if t in (")", "]"):
            depth = 0
            while j >= 0:
                u = toks[sig[j]]
                if u in (")", "]"):
                    depth += 1
                elif u in ("(", "["):
                    depth -= 1
                    if depth == 0:
                        break
                j -= 1
            if j < 0:
                return None
            if toks[sig[j]] == "(" and (j == 0 or not SIMPLE.match(toks[sig[j - 1]])):
                return j            # a parenthesised expression
            j -= 1
            continue
        if SIMPLE.match(t):
            if j >= 2 and toks[sig[j - 1]] in (".", "->"):
                j -= 2
                continue
            return j
        return None


def swap_commutative(toks, rng):
    sig = significant(toks)
    places = []
    for k in range(1, len(sig) - 1):
        if toks[sig[k]] not in COMMUTATIVE:
            continue
        a = operand_start(toks, sig, k - 1)
        b = operand_end(toks, sig, k + 1)
        if a is None or b is None:
            continue
        prec = PRECEDENCE[toks[sig[k]]]
        before = toks[sig[a - 1]] if a >= 1 else ";"
        after = toks[sig[b + 1]] if b + 1 < len(sig) else ";"
        # what is before the left operand: a binary operator binding as tightly takes it
        # (left to right), a unary operator, cast or sizeof binds it more tightly still
        if before in PRECEDENCE:
            earlier = toks[sig[a - 2]] if a >= 2 else ";"
            binary = SIMPLE.match(earlier) or earlier in (")", "]")
            if not binary or PRECEDENCE[before] >= prec:
                continue
        elif before in ("!", "~", "++", "--", "sizeof", ".", "->"):
            continue
        elif before == ")" :
            continue                # a cast
        # after the right operand: a binary operator binding more tightly takes it
        if after in PRECEDENCE and PRECEDENCE[after] > prec:
            continue
        if after in ("++", "--", "[", "(", ".", "->"):
            continue
        places.append((a, k, b))
    if not places:
        return None
    a, k, b = rng.choice(places)
    left = toks[sig[a]:sig[k - 1] + 1]
    right = toks[sig[k + 1]:sig[b] + 1]
    return toks[:sig[a]] + right + toks[sig[k - 1] + 1:sig[k + 1]] + left + toks[sig[b] + 1:]


DECL = re.compile(r"^[ \t]*(register[ \t]+)?((unsigned|signed|long|short|char|int|float|double|struct[ \t]+\w+)[ \t]*)+[^;(){}=]*(=[^;]*)?;[ \t]*$", re.M)


def declarations(body):
    """the local declaration lines at the top of the body: (start, end) of each"""
    out = []
    pos = body.index("{") + 1
    for m in DECL.finditer(body, pos):
        if body[pos:m.start()].strip():
            break
        out.append((m.start(), m.end()))
        pos = m.end()
    return out


def swap_declarations(body, rng):
    decls = declarations(body)
    if len(decls) < 2:
        return None
    k = rng.randrange(len(decls) - 1)
    (a0, a1), (b0, b1) = decls[k], decls[k + 1]
    return body[:a0] + body[b0:b1] + body[a1:b0] + body[a0:a1] + body[b1:]


def toggle_register(body, rng):
    decls = declarations(body)
    if not decls:
        return None
    a, b = rng.choice(decls)
    line = body[a:b]
    if re.match(r"\s*register\s", line):
        line = re.sub(r"register\s+", "", line, count=1)
    else:
        line = re.sub(r"^(\s*)", r"\1register ", line, count=1)
    return body[:a] + line + body[b:]


def swap_declarators(body, rng):
    """two adjacent names in one declaration line (int i, total; -> int total, i;)"""
    lines = []
    for a, b in declarations(body):
        line = body[a:b]
        m = re.match(r"(\s*(?:register\s+)?(?:(?:unsigned|signed|long|short|char|int|float|double|struct\s+\w+)\s*)+)([^;]*);", line)
        if m and "," in m.group(2) and "=" not in m.group(2) and "(" not in m.group(2):
            lines.append((a, b, m))
    if not lines:
        return None
    a, b, m = rng.choice(lines)
    names = [n.strip() for n in m.group(2).split(",")]
    k = rng.randrange(len(names) - 1)
    names[k], names[k + 1] = names[k + 1], names[k]
    return body[:a] + m.group(1) + ", ".join(names) + ";" + body[a + m.end():b] + body[b:]


def compound(body, rng):
    forms = [(m, 0) for m in re.finditer(r"\b([A-Za-z_]\w*)\s*([-+*&|^])=\s*([^;]+);", body)] + \
            [(m, 1) for m in re.finditer(r"\b([A-Za-z_]\w*)\s*=\s*\1\s*([-+*&|^])\s*([^;]+);", body)]
    if not forms:
        return None
    m, kind = rng.choice(forms)
    name, op, rest = m.group(1), m.group(2), m.group(3)
    if kind == 0:
        new = f"{name} = {name} {op} {rest};" if not re.search(r"[-+*/%&|^<>?]", rest) else f"{name} = {name} {op} ({rest});"
    else:
        new = f"{name} {op}= {rest};"
    return body[:m.start()] + new + body[m.end():]


def increment(body, rng):
    forms = list(re.finditer(r"(?<![\w+-])([A-Za-z_]\w*)(\+\+|--)(?=\s*[;)])|(\+\+|--)([A-Za-z_]\w*)(?=\s*[;)])", body))
    if not forms:
        return None
    m = rng.choice(forms)
    new = (m.group(2) + m.group(1)) if m.group(1) else (m.group(4) + m.group(3))
    return body[:m.start()] + new + body[m.end():]


def mutate(body, rng):
    for _ in range(20):
        kind = rng.randrange(6)
        if kind == 0:
            toks = swap_commutative(tokens(body), rng)
            new = "".join(toks) if toks else None
        else:
            new = (swap_declarations, swap_declarators, toggle_register, compound, increment)[kind - 1](body, rng)
        if new and new != body:
            return new
    return None


def function_code(obj, name):
    """the code bytes of NAME in an object file, and the mask of bytes fixups fill"""
    _, segs, data, masks = omf.module_masked(obj)[:4]
    pubs = omf.publics(obj)
    key = name if name in pubs else "_" + name
    if key not in pubs:
        return None, None
    seg, offset = pubs[key]
    ends = sorted(o for s, o in pubs.values() if s == seg and o > offset)
    end = ends[0] if ends else len(data[seg])
    return bytes(data[seg][offset:end]), list(masks[seg][offset:end])


def score(code, mask, target, target_mask):
    if code is None:
        return 1 << 30
    n = min(len(code), len(target))
    diff = sum(1 for i in range(n) if code[i] != target[i] and not mask[i] and not target_mask[i])
    return diff + abs(len(code) - len(target)) * 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("function")
    ap.add_argument("--target-obj")
    ap.add_argument("--target-bin")
    ap.add_argument("--opts", default="-ml -O -G")
    ap.add_argument("--root", help="directory standing for C:\\ (the toolchain's headers)")
    ap.add_argument("--headers", action="append", default=[], help="a directory of the project's headers")
    ap.add_argument("--jobs", type=int, help="compiles at once (default: one fewer than the cores)")
    ap.add_argument("--nice", type=int, default=0, help="lower the compiles' priority")
    ap.add_argument("--whole-file", action="store_true", help="compile the whole file for each variant")
    ap.add_argument("--no-snapshot", action="store_true", help="compile each variant from the start")
    ap.add_argument("--iterations", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--out")
    args = ap.parse_args()
    if args.target_obj:
        target, target_mask = function_code(Path(args.target_obj).read_bytes(), args.function)
        if target is None:
            raise SystemExit(f"permute: {args.function} is not public in {args.target_obj}")
    elif args.target_bin:
        target = Path(args.target_bin).read_bytes()
        target_mask = [0] * len(target)
    else:
        raise SystemExit("permute: give --target-obj or --target-bin")
    text = Path(args.source).read_text(encoding="latin1")
    named, start, end = definition(text, args.function)
    head, body, tail = text[:start], text[start:end], text[end:]
    line = text.rfind("\n", 0, named) + 1          # the snapshot's #include goes before this line
    compiled_tail = tail
    cmd = ["-c", *args.opts.split(), "T.C"]
    pool = bcc20.Pool(workers=args.jobs, root=args.root, time=1, nice=args.nice)
    headers = {}
    for d in args.headers:
        for h in sorted(Path(d).iterdir()):
            if h.suffix.upper() == ".H":
                headers[h.name.upper()] = h.read_bytes()
    if headers:
        pool.keep(headers)

    def scored(r):
        obj = r.outputs.get("T.OBJ")
        if r.status or not obj:
            return 1 << 30
        code, mask = function_code(obj, args.function)
        return score(code, mask, target, target_mask)

    def same_code(a, b):
        """the same code as the score sees it: same length and fixups, same other bytes"""
        return a is not None and b is not None and len(a[0]) == len(b[0]) and a[1] == b[1] \
            and score(*a, *b) == 0

    def code_of(r):
        obj = r.outputs.get("T.OBJ")
        return function_code(obj, args.function) if obj and not r.status else None

    if not args.whole_file:
        whole, cut = pool.map([(cmd, {"T.C": (head + body + tail).encode("latin1")}),
                               (cmd, {"T.C": (head + body + "\n").encode("latin1")})])
        code = [function_code(r.outputs["T.OBJ"], args.function) if r.outputs.get("T.OBJ") and not r.status
                else None for r in (whole, cut)]
        if same_code(code[1], code[0]):
            compiled_tail = "\n"
            print("compiling the file up to the function only (the same code as the whole file)")
        else:
            print("compiling the whole file (cut after the function, its code differs)")

    snapshot = False
    # the snapshot: the file up to the function's line compiled once in every server, each
    # variant finished on a copy of it (POSIX hosts), if that gives the whole file's code
    if not args.no_snapshot:
        reference = code_of(pool.map([(cmd, {"T.C": (head + body + tail).encode("latin1")})])[0])
        prefix = (text[:line] + '#include "VARIANT.C"\n').encode("latin1")
        if pool.snapshot(cmd, {"T.C": prefix}):
            snapshot = True
            if same_code(code_of(pool.resume({"VARIANT.C": (text[line:start] + body + compiled_tail).encode("latin1")})), reference):
                print("finishing each variant on a copy of the compile paused before the function")
            else:
                pool.release()
                snapshot = False
                print("no snapshot (the function's code differs when it is included)")
        else:
            print("no snapshot on this host")

    def evaluate_all(bodies):
        if snapshot:
            return [scored(r) for r in pool.resume_map(
                [{"VARIANT.C": (text[line:start] + b + compiled_tail).encode("latin1")} for b in bodies])]
        jobs = [(cmd, {"T.C": (head + b + compiled_tail).encode("latin1")}) for b in bodies]
        return [scored(r) for r in pool.map(jobs)]


    # each round: one variant of the current source per worker, compiled at once; the best
    # that scores no worse becomes current (the first of equal ones, so a seed repeats)
    rng = random.Random(args.seed)
    best_body, best = body, evaluate_all([body])[0]
    current, current_score = body, best
    print(f"start: score {best} ({pool.workers} compiles at once)")
    n = 0
    while n < args.iterations and best:
        candidates = [c for c in (mutate(current, rng) for _ in range(pool.workers)) if c is not None]
        if not candidates:
            n += pool.workers
            continue
        n += len(candidates)
        scores = evaluate_all(candidates)
        k = min(range(len(scores)), key=lambda i: scores[i])
        if scores[k] <= current_score:
            current, current_score = candidates[k], scores[k]
            if current_score < best:
                best_body, best = current, current_score
                print(f"iteration {n}: score {best}")
                if args.out:
                    Path(args.out).write_text(head + best_body + tail, encoding="latin1")
    pool.close()
    print("match" if best == 0 else f"best score {best}")
    if args.out and best_body is not body:
        Path(args.out).write_text(head + best_body + tail, encoding="latin1")
    elif not args.out:
        print(head + best_body + tail)
    return 0 if best == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
