"""Reorder a source's variable definitions so that cl2000 lays out its .ebss as the image has it.

    python3 c28ebss.py SRC [SRC ...]        # check, and solve where needed
    python3 c28ebss.py --check SRC ...      # report only
    python3 c28ebss.py --all                # every source with markers
    (--config PATH, or $EXHUME_CONFIG; SRC is a path, or a file name or stem under src/)

Why. cl2000 does not keep source order in a file's .ebss (compiler.md, ".ebss layout"). It puts
16-bit scalars first, then 32-bit scalars, then structs and arrays, and inside each class the
order is a permutation of the definition order. The permutation is a function of the sequence of
types and of the identifier spellings in the whole file: adding a function, a declaration or a
header, or renaming a callee, can move the variables with no change to any code.

How. The definitions sit between two marker lines, one declaration per item:

    /* ebss-begin */          (or // ebss-begin)
    unsigned int mode;
    float level = 0.25f;
    /* ebss-end */            (or // ebss-end)

Each round compiles the real file with its own compiler and options, reads where each variable
landed, and applies the inverse permutation: the item in the slot that received the k-th
variable by address gets the k-th variable by address. A variable's address is its map/data.tsv
row, or the address its name spells (var_A404). The file is right when every variable's address
minus its offset gives one section base. When plain rounds cycle (the spellings moved under
them), pairs of misplaced items are swapped, keeping each swap that lowers the count.

Comment and blank lines move with the declaration after them. An item with several declarators
(`int a, b;`) moves as one. The file is rewritten only when a solution is found. Exit status 0
when every file given is right at the end."""
import argparse, os, random, re, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import c28tc, c28map

BEGIN = re.compile(r'^\s*(/\*\s*ebss-begin\s*\*/|//\s*ebss-begin)\s*$')
END = re.compile(r'^\s*(/\*\s*ebss-end\s*\*/|//\s*ebss-end)\s*$')


def strip_comments(s):
    return re.sub(r'/\*.*?\*/|//[^\n]*', ' ', s, flags=re.S)


def declared_names(code):
    """The names a declaration defines (`unsigned int a = 1, b[4];` -> ['a', 'b'])."""
    code = re.sub(r'=\s*\{.*?\}\s*(?=[,;])', '', code, flags=re.S)       # initialiser lists
    code = re.sub(r'=[^,;]*', '', code)                                   # scalar initialisers
    code = re.sub(r'\{[^{}]*\}', ' ', code, flags=re.S)                   # struct bodies
    code = re.sub(r'\[[^\]]*\]', '', code)
    code = code.strip().rstrip(';')
    names = []
    for part in code.split(','):
        ws = re.findall(r'[A-Za-z_]\w*', part)
        if ws: names.append(ws[-1])
    return names


def split_items(lines):
    """The block's lines as items [(lines, names)], and the trailing lines no declaration ends."""
    items, cur = [], []
    for line in lines:
        cur.append(line)
        code = strip_comments('\n'.join(cur))
        if not code.strip(): continue
        if code.count('{') == code.count('}') and code.rstrip().endswith(';'):
            items.append((cur, declared_names(code))); cur = []
    return items, cur


def block(text):
    """(lines, begin index, end index, items, tail), or None when the file has no markers."""
    lines = text.split('\n')
    b = next((i for i, l in enumerate(lines) if BEGIN.match(l)), None)
    e = next((i for i, l in enumerate(lines) if b is not None and i > b and END.match(l)), None)
    if b is None or e is None: return None
    items, tail = split_items(lines[b + 1:e])
    return lines, b, e, items, tail


def compose(lines, b, e, items, tail):
    return '\n'.join(lines[:b + 1] + [l for it, _ in items for l in it] + tail + lines[e:])


def misplaced(lay, addr_of):
    """(names whose address does not fit the common base, the base most variables agree on)."""
    bases = {}
    for n, off in lay.items():
        a = addr_of(n)
        if a is not None: bases.setdefault(a - off, []).append(n)
    if not bases: return sorted(lay), None
    base = max(bases, key=lambda x: (len(bases[x]), -x))
    return sorted(n for n in lay if addr_of(n) is None or addr_of(n) - lay[n] != base), base


def permute(items, lay, addr_of):
    """One round: the slots in the order the compiler laid them out take the items in address
    order. Items with several declarators, or names not laid out, stay where they are."""
    singles = [(k, ns[0]) for k, (it, ns) in enumerate(items) if len(ns) == 1 and ns[0] in lay]
    slots = sorted((lay[n], k) for k, n in singles)
    want = sorted(singles, key=lambda kn: (addr_of(kn[1]) is None, addr_of(kn[1]) or 0))
    new = list(items)
    for (off, k), (k2, n) in zip(slots, want): new[k] = items[k2]
    return new


def solve(text, layout, addr_of, rounds=40, swaps=400, say=print, label='file'):
    """(solved, text): text with the block reordered so that layout(text) puts every variable at
    its address. layout(text) -> {name: .ebss offset} compiles the text."""
    lay = layout(text)
    if not lay: say('%s: no .ebss variables' % label); return True, text
    bad, base = misplaced(lay, addr_of)
    if not bad: say('%s: right (%d variables, .ebss at 0x%04X)' % (label, len(lay), base)); return True, text
    say('%s: %d of %d variables misplaced' % (label, len(bad), len(lay)))
    blk = block(text)
    if blk is None: say('  no ebss markers in the file: cannot reorder'); return False, text
    lines, b, e, items, tail = blk
    inblock = {n for _, ns in items for n in ns}
    outside = [n for n in lay if n not in inblock]
    if outside: say('  defined outside the block (left in place): ' + ' '.join(sorted(outside)))
    multi = [ns for _, ns in items if len(ns) > 1]
    if multi: say('  items with several declarators move as one: ' + '; '.join(','.join(x) for x in multi))

    def score(its):
        t = compose(lines, b, e, its, tail)
        l = layout(t)
        return (t, l) + misplaced(l, addr_of)

    seen, best = set(), (len(bad), list(items))
    for rnd in range(rounds):
        key = tuple(tuple(ns) for _, ns in items)
        if key in seen: break
        seen.add(key)
        new = permute(items, lay, addr_of)
        if new == items: break
        items = new
        t, lay, bad, base = score(items)
        say('  round %d: %d misplaced' % (rnd + 1, len(bad)))
        if len(bad) < best[0]: best = (len(bad), list(items))
        if not bad:
            say('%s: solved, .ebss at 0x%04X' % (label, base)); return True, t
    items = list(best[1])
    t, lay, bad, base = score(items)
    rng = random.Random(1)
    for _ in range(swaps):
        if not bad: break
        badk = [k for k, (it, ns) in enumerate(items) if any(n in bad for n in ns)]
        if len(badk) < 2: badk += rng.sample(range(len(items)), min(2, len(items)))
        i, j = rng.sample(badk, 2)
        trial = list(items); trial[i], trial[j] = trial[j], trial[i]
        t2, l2, b2, bs2 = score(trial)
        if len(b2) < len(bad):
            items, t, lay, bad, base = trial, t2, l2, b2, bs2
            say('  swap: %d misplaced' % len(bad))
    if not bad:
        say('%s: solved by swaps, .ebss at 0x%04X' % (label, base)); return True, t
    say('%s: NOT SOLVED (%d misplaced: %s)' % (label, len(bad), ' '.join(bad[:12])))
    return False, text


def compiler_layout(tc, src, work):
    """layout(text) for solve(): the .ebss offsets of the text compiled as src (in src's place, so
    the same includes and file-name-dependent names), with src's own compiler and options."""
    def layout(text):
        shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
        p = os.path.join(work, os.path.basename(src))
        with open(p, 'w', encoding='latin1') as f: f.write(text)
        obj = tc.compile(p, os.path.join(work, 'o'), cgt=tc.home_for(src), extra=['-I' + os.path.dirname(src)])
        o = tc.obj(obj)
        return {c28map.cname(y['name']): y['value'] for y in o.symbols
                if y['kind'] == 'defined' and (y['section'] or '') == '.ebss'
                and not y['name'].startswith(('.', '$')) and y['name'] != y['section']}
    return layout


def run(s, argv, out=print):
    ap = argparse.ArgumentParser(prog='c28ebss.py', description='Solve the .ebss order of C2000 sources.')
    ap.add_argument('src', nargs='*')
    ap.add_argument('--all', action='store_true', help='every source with ebss markers')
    ap.add_argument('--check', action='store_true', help='report only, change nothing')
    a = ap.parse_args(argv)
    tc = c28tc.Toolchain(s)
    m = c28map.Map.from_dir(s.map_dir)
    addr_of = lambda n: m.drows[n].addr if n in m.drows else m.resolve('_' + n)
    srcs = [s.source(x) for x in a.src]
    if a.all:
        for p in s.sources(('.c',)):
            with open(p, encoding='latin1') as f:
                if block(f.read()) is not None and p not in srcs: srcs.append(p)
    ok = True
    for src in srcs:
        with open(src, encoding='latin1') as f: text = f.read()
        layout = compiler_layout(tc, src, os.path.join(s.build, 'ebss', c28tc.stem(src)))
        rel = os.path.relpath(src, s.root)
        if a.check:
            bad, base = misplaced(layout(text), addr_of)
            out('%s: %s' % (rel, 'right' if not bad else '%d misplaced: %s' % (len(bad), ' '.join(bad[:12]))))
            ok &= not bad; continue
        good, new = solve(text, layout, addr_of, say=out, label=rel)
        if good and new != text:
            with open(src, 'w', encoding='latin1') as f: f.write(new)
        ok &= good
    return 0 if ok else 1


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(c28tc.EXHUME, 'tools'))
    import config
    p, rest = config.pop_config(sys.argv[1:])
    r = run(c28tc.settings(p), rest)
    sys.exit(r)
