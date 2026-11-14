"""Permuter for Borland C++ 2.0 code: tries small rewrites of one function's C source until
it compiles to the target bytes, using the bcc20 port in-process (tools/bcc20.py), so it
tries thousands of variants a second.

    python3 tools/permute.py FILE.C FUNCTION (--target-obj T.OBJ | --target-bin F.BIN)
        [--opts "-ml -O -G"] [--root DIR] [--iterations N] [--seed S] [--out BEST.C]

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


def function_span(text, name):
    """(start, end) of FUNCTION's body braces in text"""
    m = re.search(r"\b%s\s*\([^;{]*\)\s*(?:[^;{]*;\s*)*\{" % re.escape(name), text)
    if not m:
        raise SystemExit(f"permute: no definition of {name}")
    depth, i = 0, m.end() - 1
    while i < len(text):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return m.end() - 1, i + 1
        i += 1
    raise SystemExit(f"permute: {name}'s body does not end")


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
    start, end = function_span(text, args.function)
    head, body, tail = text[:start], text[start:end], text[end:]
    bcc = bcc20.Compiler()
    cmd = ["-c", *args.opts.split(), "T.C"]

    def evaluate(b):
        r = bcc.compile(cmd, files={"T.C": (head + b + tail).encode("latin1")}, root=args.root, time=1)
        obj = r.outputs.get("T.OBJ")
        if r.status or not obj:
            return 1 << 30
        code, mask = function_code(obj, args.function)
        return score(code, mask, target, target_mask)

    rng = random.Random(args.seed)
    best_body, best = body, evaluate(body)
    current, current_score = body, best
    print(f"start: score {best}")
    for n in range(1, args.iterations + 1):
        if best == 0:
            break
        candidate = mutate(current, rng)
        if candidate is None:
            continue
        s = evaluate(candidate)
        if s <= current_score:
            current, current_score = candidate, s
            if s < best:
                best_body, best = candidate, s
                print(f"iteration {n}: score {best}")
                if args.out:
                    Path(args.out).write_text(head + best_body + tail, encoding="latin1")
    print("match" if best == 0 else f"best score {best}")
    if args.out and best_body is not body:
        Path(args.out).write_text(head + best_body + tail, encoding="latin1")
    elif not args.out:
        print(head + best_body + tail)
    return 0 if best == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
