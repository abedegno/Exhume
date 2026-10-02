"""Reconcile the many partial copies of a struct into one canonical definition.

    python3 tools/structrec.py [--config PATH] report TAG [union] [--dir SRCDIR]
    python3 tools/structrec.py [--config PATH] fields TAG [union] [--dir SRCDIR]
    python3 tools/structrec.py [--config PATH] spec TAG --header H --out SPECDIR [--dir SRCDIR]
    python3 tools/structrec.py [--config PATH] convert SPEC.py [--specs SPECDIR] [--dir SRCDIR] [--files A.C,B.C] [--write]

Matched sources each declare the structs they touch, as far as they touch them: one file's
`struct Object` names six fields and pads the rest, another's names others at the same
offsets, a third is 8 bytes long because it only ever copies static objects. Step 2 of
skills/readability-pass replaces them with one definition per struct in a shared header,
and the bytes decide every field: a field's offset, width, unit (a char or an int bitfield)
and signedness are what the code compiled from, so two files that agree on those agree on
the field whatever they called it.

  report   every definition of TAG in the sources (--dir, default [project] src), laid out by
           Turbo C's rules (tools/cparse.py), with the fields each file actually uses, grouped
           by bit position: the evidence for the canonical definition. Sizes are listed too:
           a definition by value lays out _BSS with its size, so sizes are evidence.
  fields   the same for every field, used or not.
  spec     a starting spec from the first definition found: SPECDIR/<tag>_s.py.
  convert  rewrite every file defining SPEC's tag to the canonical definition: map each
           field the file uses to the canonical field with the same bits (or an element of a
           canonical array of the same type), rewrite the accesses, and delete the local
           definition (with the comment block directly above it, which the report shows).
           A file with a field that has no canonical match, or several, is left alone and
           reported. Without --write, only reports. Then run the gate: a field that compiles
           differently (unsigned against int, a byte view of a word) fails it.

A spec is a Python file:
    KIND = 'struct'; TAG = 'Tile'; HEADER = 'map.h'
    TEXT = r'''struct Tile { ... };'''      the canonical definition, with its comments
    PRE = '...'                             optional C laid out first (types TEXT uses)
    OVERRIDES = {'FILE.C': {'old.path': 'new.path'}}   forced field mappings per file
    PREFER = ['path']                       canonical fields preferred when several fit
    TYPED = ['name']                        extra variables known to have this type
    SKIP = ['FILE.C']                       files left alone
    RETAG = 'NewTag'                        rename the tag in converted files (8-byte copies
                                            of struct Object becoming struct StaticObj)
    REQUIRES = ['other.h'], ALSO_TAGS = ['Alias'], EMIT = False    for tools/headergen.py
The other specs in --specs (default: the spec's own directory) supply the layouts of nested
canonical types, and their member names make an access ambiguous when they collide.
"""
import os, re, sys, json, glob, importlib.util
from collections import defaultdict
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config, sources, cparse
from cparse import leaves


def load_spec(p):
    sp = importlib.util.spec_from_file_location('spec_' + os.path.basename(p)[:-3], p)
    m = importlib.util.module_from_spec(sp); sp.loader.exec_module(m); return m


def spec_files(d):
    if not d or not os.path.isdir(d): return []
    a = os.path.join(d, 'active.json')
    names = json.load(open(a)) if os.path.exists(a) else sorted(f for f in os.listdir(d) if f.endswith('.py'))
    return [os.path.join(d, f) for f in names]


class Specs:
    def __init__(self, d):
        self.files = spec_files(d)
        self.ctx = cparse.Ctx(); self.members = {}
        for p in self.files:
            t = open(p).read()
            m = re.search(r"TEXT = r'''(.*?)'''", t, re.S)
            if not m: continue
            body = cparse.strip_comments(m.group(1))
            tag = re.search(r"TAG\s*=\s*'(\w+)'", t)
            self.members[tag.group(1) if tag else p] = set(re.findall(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?::\s*\w+\s*)?(?=[;,])', body[body.find('{'):]))
            if 'EMIT = False' in t: continue
            try: cparse.layout_text(m.group(1), self.ctx)
            except Exception as ex: print(f'{p}: cannot lay out: {ex}')


def file_defs(path, tag, kind, header_tags):
    """[(entry, fields)] for each definition of kind TAG in the file, laid out."""
    ctx = cparse.Ctx(dict(header_tags)); out = []
    ents = cparse.analyse(path)
    cparse.MACROS.clear()
    for e in ents:
        if e['kind'] == 'pp_define':
            m = re.match(r'([A-Za-z_]\w*)\s+(.+)$', e['norm'])
            if m and '(' not in e['norm'].split()[0]: cparse.MACROS[m.group(1)] = re.sub(r'/\*.*?\*/', '', m.group(2)).strip()
    for e in ents:
        if e['kind'] in ('struct_def', 'union_def'):
            t = cparse.strip_comments(e['text']); body = t[t.find('{') + 1:t.rfind('}')]; k = e['kind'][:-4]
            try: fl = cparse.layout(body, ctx, k)
            except Exception as ex: print('layout error', os.path.basename(path), e['name'], ex); continue
            if e['name']: ctx.tags['%s %s' % (k, e['name'])] = (cparse.struct_size(fl), fl)
            if e['name'] == tag and k == kind: out.append((e, fl))
    return out


def code_without_structs(path):
    """The file with comments, strings and struct definitions blanked (same length)."""
    raw = open(path, encoding='latin1').read()
    t = cparse.strip_comments(raw)
    t = re.sub(r'"(\\.|[^"\\])*"', lambda m: '"' + ' ' * (len(m.group(0)) - 2) + '"', t)
    for e in cparse.analyse(path):
        if e['kind'] in ('struct_def', 'union_def'):
            t = t[:e['start']] + ' ' * (e['end'] - e['start']) + t[e['end']:]
    return t


def used(code, path, is_container):
    leaf = path.split('.')[-1]
    if is_container: return bool(re.search(r'(->|\.)\s*' + re.escape(leaf) + r'\b(?!\s*\.)', code))
    return bool(re.search(r'(->|\.)\s*' + re.escape(leaf) + r'\b', code))


def tnorm(t):
    t = re.sub(r'\s+', ' ', t).strip()
    t = re.sub(r'\bunsigned (int|short( int)?)\b', 'unsigned', t)
    t = re.sub(r'\b(signed )?short( int)?\b', 'int', t)
    return re.sub(r'\s*\*\s*', '*', t)


def sig(leaf):
    path, ab, nb, unit, typ, size, cont = leaf
    if unit.startswith('bf'): return (ab, nb, unit, not re.search(r'unsigned', typ))
    return (ab, nb, unit, tnorm(typ))


def members_tree(fl):
    t = {}
    for x in fl:
        if not x['name']: continue
        t[x['name']] = members_tree(x['nested']) if (x.get('nested') and not x['bits'] and '[' not in x['type']) else {}
    return t


def other_member_names(path, exclude_tag):
    names = set()
    for e in cparse.analyse(path):
        if e['kind'] in ('struct_def', 'union_def') and e['name'] != exclude_tag:
            t = cparse.strip_comments(e['text']); body = t[t.find('{') + 1:t.rfind('}')]
            names |= set(re.findall(r'([A-Za-z_]\w*)\s*(?:\[[^\]]*\]\s*)*(?::\s*\w+\s*)?(?=[;,])', body))
    return names


def c_files(cfg, d):
    if d: return sorted(p for p in glob.glob(os.path.join(d, '**', '*.[Cc]'), recursive=True)
                        if os.path.commonpath([os.path.abspath(p), os.path.abspath(cfg.include)]) != os.path.abspath(cfg.include))
    return [p for p in sources.all_sources(cfg) if p.upper().endswith('.C')]


def report(cfg, tag, kind, d, specs, all_fields=False):
    table = defaultdict(lambda: defaultdict(list)); sizes = {}
    for p in c_files(cfg, d):
        ds = file_defs(p, tag, kind, specs.ctx.tags)
        if not ds: continue
        code = code_without_structs(p); f = os.path.basename(p)
        for e, fl in ds:
            sizes[f] = cparse.struct_size(fl)
            for path, ab, nb, unit, typ, size, cont in leaves(fl):
                if not all_fields and not used(code, path, cont): continue
                table[(ab, nb, unit)][(path, typ)].append(f)
    print('sizes: ' + ' '.join('%s=%d' % kv for kv in sorted(sizes.items(), key=lambda kv: kv[1])))
    print(f'{len(sizes)} definitions; fields {"declared" if all_fields else "used"}, by byte.bit, width and unit:')
    for (ab, nb, unit) in sorted(table, key=lambda k: (k[0], -k[1])):
        print('%04X.%d w%-3d %-6s' % (ab // 8, ab % 8, nb, unit),
              ' | '.join('%s %s x%d %s' % (p, t, len(fs), ','.join(x.rsplit('.', 1)[0] for x in fs) if len(fs) < 4 else '')
                         for (p, t), fs in sorted(table[(ab, nb, unit)].items(), key=lambda kv: -len(kv[1]))))


def make_spec(cfg, tag, kind, header, out, d):
    for p in c_files(cfg, d):
        raw = open(p, encoding='latin1').read()
        for e in cparse.analyse(p):
            if e['kind'] == kind + '_def' and e['name'] == tag:
                lines = raw[:e['start']].split('\n'); pre = []; j = len(lines) - 2
                if j >= 0 and lines[j].strip().endswith('*/'):
                    k = j
                    while k >= 0 and '/*' not in lines[k]: k -= 1
                    if k >= 0 and lines[k].strip().startswith('/*'): pre = lines[k:j + 1]
                os.makedirs(out, exist_ok=True)
                dst = os.path.join(out, tag.lower() + '_s.py')
                open(dst, 'w').write("KIND = %r; TAG = %r; HEADER = %r\nTEXT = r'''\n%s\n'''\nOVERRIDES = {}\n"
                                     % (kind, tag, header, '\n'.join(pre + [e['text']])))
                print(f'{dst}: from {os.path.relpath(p, cfg.root)}; edit TEXT into the canonical definition')
                return 0
    print(f'no {kind} {tag} defined in the sources'); return 1


def convert(spec, path, write, canon_leaves, specs, out):
    f = os.path.basename(path)
    ds = file_defs(path, spec.TAG, spec.KIND, specs.ctx.tags)
    if not ds: return None
    if len(ds) > 1: out.append(f'{f}: {len(ds)} definitions'); return None
    e, fl = ds[0]
    code = code_without_structs(path)
    over = getattr(spec, 'OVERRIDES', {}).get(f, {})
    csig = {}
    for cl in canon_leaves: csig.setdefault(sig(cl), []).append(cl[0])
    mapping = {}; problems = []
    for lf in leaves(fl):
        p = lf[0]
        if p in over: mapping[p] = over[p]; continue
        if not used(code, p, lf[6]): continue
        cands = csig.get(sig(lf), [])
        if not cands and lf[3] == 'scalar' and '[' not in lf[4]:
            for cl in canon_leaves:       # an element of a canonical array of the same type
                m = re.match(r'(.*?)\[(\d+)\]$', tnorm(cl[4]))
                if cl[3] != 'scalar' or not m or m.group(1) != tnorm(lf[4]): continue
                n = int(m.group(2)); es = cl[5] // n
                k, r = divmod(lf[1] - cl[1], es * 8)
                if 0 <= k < n and r == 0 and lf[2] == es * 8: cands = ['%s[%d]' % (cl[0], k)]; break
        if not cands:
            problems.append(f'{p} {lf[1] // 8:#x}.{lf[1] % 8} w{lf[2]} {lf[3]} {lf[4]}: no canonical field'); continue
        leafname = p.split('.')[-1]
        pref = [c for c in getattr(spec, 'PREFER', []) if c in cands]
        best = [c for c in cands if c == p] or pref[:1] or [c for c in cands if c.split('.')[-1] == leafname] or sorted(cands, key=len)
        if len(best) > 1 and p not in best: problems.append(f'{p}: several canonical fields fit: {best}')
        mapping[p] = best[0]
    changes = {p: q for p, q in mapping.items() if p != q}
    others = other_member_names(path, spec.TAG)
    for t, ms in specs.members.items():
        if t != spec.TAG: others |= ms
    tree = members_tree(fl)
    ambiguous = set(p.split('.')[0] for p in changes if p.split('.')[0] in others)
    typed = set(getattr(spec, 'TYPED', []))
    for m in re.finditer(r'\b%s\s+%s\b(?!\s*\{)' % (spec.KIND, spec.TAG), code):
        rest = re.match(r'[^;(){=]*', code[m.end():]).group(0)
        for dcl in rest.split(','):
            ids = re.findall(r'[A-Za-z_]\w*', re.sub(r'\b(far|near|huge|const)\b', '', re.sub(r'\[[^\]]*\]', '', dcl)))
            if ids: typed.add(ids[0])
    if problems:
        out.append(f'{f}: not converted:' + '\n    '.join([''] + problems)); return None
    raw = open(path, encoding='latin1').read()
    mask = code; res = []; pos = 0; nrew = 0; skipped = []
    for m in re.finditer(r'(->|\.)(\s*)([A-Za-z_]\w*)', mask):
        if m.start() < pos: continue
        first = m.group(3)
        if first not in tree: continue
        k0 = m.start() - 1
        while k0 >= 0 and mask[k0].isspace(): k0 -= 1
        j0 = k0
        while j0 >= 0 and (mask[j0].isalnum() or mask[j0] == '_'): j0 -= 1
        before = mask[max(0, j0 - 1):j0 + 1].strip()
        if k0 > j0 and (before.endswith('.') or before.endswith('->')) and mask[j0 + 1:k0 + 1] not in typed:
            skipped.append(f'{first} after member {mask[j0 + 1:k0 + 1]}'); continue
        if first in ambiguous:
            k = m.start() - 1
            while k >= 0 and mask[k].isspace(): k -= 1
            if k >= 0 and mask[k] == ']':
                d = 0
                while k >= 0:
                    if mask[k] == ']': d += 1
                    elif mask[k] == '[':
                        d -= 1
                        if d == 0: break
                    k -= 1
                k -= 1
                while k >= 0 and mask[k].isspace(): k -= 1
            j = k
            while j >= 0 and (mask[j].isalnum() or mask[j] == '_'): j -= 1
            base = mask[j + 1:k + 1]
            if base not in typed: skipped.append(f'{first} after {base or mask[max(0, k - 10):k + 1]!r}'); continue
        comps = [first]; end = m.end(); sub = tree[first]
        while True:
            mm = re.match(r'\s*\.\s*([A-Za-z_]\w*)', mask[end:])
            if not mm or mm.group(1) not in sub: break
            comps.append(mm.group(1)); sub = sub[mm.group(1)]; end += mm.end()
        for k in range(len(comps), 0, -1):
            pp = '.'.join(comps[:k])
            if pp in changes:
                e2 = m.end()
                for c in comps[1:k]:
                    mm = re.match(r'\s*\.\s*' + re.escape(c) + r'\b', mask[e2:]); e2 += mm.end()
                res.append(raw[pos:m.start()]); res.append(m.group(1) + changes[pp]); pos = e2; nrew += 1
                break
            if pp in mapping: break
    res.append(raw[pos:])
    new = ''.join(res)
    idx = new.find(raw[e['start']:e['end']])
    lines = new[:idx].split('\n')
    a = idx - len(lines[-1]); b = idx + (e['end'] - e['start'])
    nl = new.find('\n', b); b = nl + 1 if nl >= 0 else len(new)
    j = len(lines) - 2
    if j >= 0 and lines[j].strip().endswith('*/'):
        k = j
        while k >= 0 and '/*' not in lines[k]: k -= 1
        if k >= 0 and lines[k].strip().startswith('/*') and all(lines[q].strip() for q in range(k, j + 1)):
            out.append(f'{f}: dropped comment: ' + ' '.join(l.strip() for l in lines[k:j + 1])[:300])
            a = len('\n'.join(lines[:k])) + (1 if k else 0)
    new = re.sub(r'\n{3,}', '\n\n', new[:a] + new[b:])
    out.append(f'{f}: {nrew} accesses rewritten' + (f' {changes}' if changes else ''))
    if skipped: out.append(f'{f}: left alone (not this struct, or the base is not known): ' + '; '.join(skipped))
    if getattr(spec, 'RETAG', None):
        new = re.sub(r'\b%s\s+%s\b' % (spec.KIND, spec.TAG), '%s %s' % (spec.KIND, spec.RETAG), new)
    if write: open(path, 'w', encoding='latin1', newline='').write(new)
    return changes


def main(argv):
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    def take(flag):
        if flag in a: i = a.index(flag); v = a[i + 1]; del a[i:i + 2]; return v
    d = take('--dir'); specdir = take('--specs'); only = take('--files'); header = take('--header'); outdir = take('--out')
    write = '--write' in a
    rest = [x for x in a if not x.startswith('--')]
    if len(rest) < 2: sys.exit(__doc__)
    cmd = rest[0]
    kind = rest[2] if len(rest) > 2 and rest[2] in ('struct', 'union') else 'struct'
    if cmd in ('report', 'fields'):
        report(cfg, rest[1], kind, d, Specs(specdir), cmd == 'fields'); return 0
    if cmd == 'spec':
        if not header or not outdir: sys.exit('spec needs --header H and --out SPECDIR')
        return make_spec(cfg, rest[1], kind, header, outdir, d)
    if cmd == 'convert':
        spec = load_spec(rest[1])
        specs = Specs(specdir or os.path.dirname(os.path.abspath(rest[1])))
        ctx = cparse.Ctx(dict(specs.ctx.tags))
        cparse.MACROS.clear()
        cparse.layout_text(getattr(spec, 'PRE', '') + '\n' + spec.TEXT, ctx)
        canon = ctx.tags['%s %s' % (spec.KIND, spec.TAG)][1]
        cl = list(leaves(canon))
        files = set(only.split(',')) if only else None
        out = []; done = []
        for p in c_files(cfg, d):
            f = os.path.basename(p)
            if f in getattr(spec, 'SKIP', []) or (files and f not in files): continue
            if convert(spec, p, write, cl, specs, out) is not None: done.append(f)
        print('\n'.join(out))
        print(('converted: ' if write else 'would convert: ') + ' '.join(done))
        return 0
    sys.exit(__doc__)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
