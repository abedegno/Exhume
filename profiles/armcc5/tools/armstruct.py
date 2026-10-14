"""Reconcile the per-file copies of a global's struct into one definition, and name its fields.

    python3 armstruct.py [--config PATH] survey
    python3 armstruct.py [--config PATH] report GLOBAL
    python3 armstruct.py [--config PATH] draft GLOBAL TYPENAME
    python3 armstruct.py [--config PATH] convert GLOBAL HEADER TYPENAME [FILE ...] [--write]
                         [--loose-sign] [--with-def]
    python3 armstruct.py [--config PATH] fields FIELDS.tsv [--write]

Each matched source declares the globals it uses with a struct of its own, naming the fields it
touches (f75 is the field at 0x75) and padding the rest. The copies differ in which fields they
name and sometimes in a field's type, and the type is what the code compiled from: a halfword
at 0x90 loads with LDRH as unsigned and LDRSH as signed, a __packed struct's fields are
reloaded where a plain one's stay in registers. This lays every copy out by Arm's rules (AAPCS:
natural alignment, a bitfield in the first container of its declared type with room for it;
__packed: alignment 1 and bitfields packed bit after bit) and lines the fields up by offset, so
one definition can take each field from the copies that use it, and conflicts show before the
gate does. Types come from the file and the headers it includes ("..." includes found next to
it or in [project] include); sources under [project] exclude are left out.

  survey   every global declared at file scope in more than one source, with the count
  report   every copy of GLOBAL, field by field: offset, size, sign or bits, the names files
           give it, and !! where copies disagree on the shape at one offset
  draft    a starting typedef: the majority shape at each offset, padding between, arrays where
           a file indexes, __packed if any copy is
  convert  rewrite every source declaring GLOBAL with a local struct to use TYPENAME from HEADER
           (a path under [project] include): each field the file uses maps to the canonical
           field with the same offset, size, sign and bits (or an element of a canonical array);
           the accesses are renamed, the local typedef deleted and the include added. A file
           whose field has no match is left alone and reported. --loose-sign maps a field across
           signedness and casts its reads back to the file's type; --with-def converts the
           defining file too. Only the named FILEs if given.
  fields   rename fields of shared typedefs: FIELDS.tsv rows are TYPE, old field, new field,
           comment. The field is renamed in the typedef in [project] include (its line gets the
           comment), and in every source including that header, through the type's globals and
           through pointers declared with the type. Files that declare the global with a local
           struct of their own (a view kept for the bytes) are left alone.

Without --write, convert and fields report only. Field names compile to nothing; a field whose
type changed does not, so run the gate after each global."""
import collections, glob, os, re, sys

PAD = r'(^|\.)(p|pad|pad_?[0-9a-fA-F]+|p[0-9a-f]+|reserved\w*|unused\w*)$'   # padding members

SCALARS = {
    'char': (1, 'u'), 'unsigned char': (1, 'u'), 'signed char': (1, 's'),
    'short': (2, 's'), 'unsigned short': (2, 'u'), 'signed short': (2, 's'),
    'short int': (2, 's'), 'unsigned short int': (2, 'u'),
    'int': (4, 's'), 'unsigned': (4, 'u'), 'unsigned int': (4, 'u'), 'signed int': (4, 's'),
    'signed': (4, 's'), 'long': (4, 's'), 'unsigned long': (4, 'u'), 'signed long': (4, 's'),
    'long long': (8, 's'), 'unsigned long long': (8, 'u'), 'signed long long': (8, 's'),
    'float': (4, 'f'), 'double': (8, 'f'),
    'uint8_t': (1, 'u'), 'int8_t': (1, 's'), 'uint16_t': (2, 'u'), 'int16_t': (2, 's'),
    'uint32_t': (4, 'u'), 'int32_t': (4, 's'), 'uint64_t': (8, 'u'), 'int64_t': (8, 's'),
}
QUALS = ('volatile', 'const', '__IO', '__I', '__O', 'register', 'static')
C_NAMES = {(1, 'u'): 'unsigned char', (1, 's'): 'signed char', (2, 'u'): 'unsigned short', (2, 's'): 'short',
           (4, 'u'): 'unsigned int', (4, 's'): 'int'}


class Type:
    """size, align, kind ('scalar' | 'struct' | 'union' | 'array' | 'ptr'), sign, fields, elem, n, packed"""
    def __init__(self, **kw):
        self.packed = False
        self.__dict__.update(kw)


def strip_comments(s):
    s = re.sub(r'/\*.*?\*/', lambda m: re.sub(r'[^\n]', ' ', m.group(0)), s, flags=re.S)
    return re.sub(r'//[^\n]*', ' ', s)


def cval(expr, consts):
    e = expr.strip()
    for k, v in consts.items(): e = re.sub(r'\b%s\b' % re.escape(k), str(v), e)
    e = re.sub(r'(0x[0-9A-Fa-f]+|\d+)[uUlL]+\b', r'\1', e)
    if not re.fullmatch(r'[\s\d+\-*/()xa-fA-F<>]*', e): raise ValueError(expr)
    return int(eval(e, {'__builtins__': {}}, {}))


class Parser:
    """The struct, union and scalar typedefs of some C text, laid out by Arm's rules."""

    def __init__(self, text):
        self.types, self.texts, self.consts = {}, {}, {}
        self.text = strip_comments(text)
        for m in re.finditer(r'(?m)^\s*#define\s+(\w+)\s+\(?\s*(0x[0-9A-Fa-f]+|\d+)[uUlL]*\s*\)?\s*$', self.text):
            self.consts[m.group(1)] = int(m.group(2), 0)
        self.scan()

    def resolve(self, words):
        key = ' '.join(words)
        if key in SCALARS:
            size, sign = SCALARS[key]
            return Type(size=size, align=size, kind='scalar', sign=sign, base=key)
        if len(words) == 1 and words[0] in self.types: return self.types[words[0]]
        if len(words) == 2 and words[0] in ('struct', 'union', 'enum') and words[1] in self.types:
            return self.types[words[1]]
        if words and words[0] == 'enum':        # armcc's default: the smallest type that holds the values
            return Type(size=1, align=1, kind='scalar', sign='u', base='enum')
        raise KeyError(key)

    @staticmethod
    def split_members(body):
        out, depth, cur = [], 0, ''
        for ch in body:
            if ch == '{': depth += 1
            elif ch == '}': depth -= 1
            if ch == ';' and depth == 0:
                if cur.strip(): out.append(cur.strip())
                cur = ''
            else: cur += ch
        if cur.strip(): out.append(cur.strip())
        return out

    @staticmethod
    def declarators(s):
        out, depth, cur = [], 0, ''
        for ch in s:
            if ch in '([': depth += 1
            elif ch in ')]': depth -= 1
            if ch == ',' and depth == 0: out.append(cur.strip()); cur = ''
            else: cur += ch
        if cur.strip(): out.append(cur.strip())
        return out

    def apply_decl(self, base, d):
        """(name, Type, bits) of one declarator over base."""
        bits = None
        m = re.match(r'^(.*?):\s*(.+)$', d)
        if m: d, bits = m.group(1).strip(), cval(m.group(2), self.consts)
        ptr = d.count('*'); d = d.replace('*', ' ').replace('const', ' ').replace('volatile', ' ').strip()
        m = re.match(r'^(\w*)\s*((?:\[[^\]]*\]\s*)*)$', d)
        if not m: raise ValueError('declarator ' + d)
        name, dims = m.group(1), re.findall(r'\[([^\]]*)\]', m.group(2))
        t = Type(size=4, align=4, kind='ptr', sign='u', base='ptr') if ptr else base
        for n in reversed(dims):
            n = cval(n, self.consts) if n.strip() else 0
            t = Type(size=t.size * n, align=t.align, kind='array', elem=t, n=n)
        return name, t, bits

    def parse_body(self, kind, body, packed):
        fields = []   # (name, offset, Type, bitpos, bits)
        off = size = 0; align = 1; bitcur = None   # bitcur: (container offset, size, used bits)
        for mem in self.split_members(body):
            mpacked = packed
            m = re.match(r'^(__packed\s+)?(struct|union)\s*(\w+)?\s*\{(.*)\}\s*(.*)$', mem, re.S)
            if m:
                sub = self.parse_body(m.group(2), m.group(4), packed or bool(m.group(1)))
                decls = self.declarators(m.group(5)) if m.group(5).strip() else ['']
                pairs = [self.apply_decl(sub, d) if d else ('', sub, None) for d in decls]
            else:
                words = mem.replace('*', ' * ').split()
                vol = any(w in ('volatile', '__IO', '__I', '__O') for w in words)
                q = [w for w in words if w not in QUALS]
                if q and q[0] == '__packed': mpacked = True; q = q[1:]
                for k in range(len(q), 0, -1):          # the longest prefix that names a type
                    try: base = self.resolve(q[:k]); break
                    except KeyError: continue
                else: raise KeyError('member ' + mem)
                if vol and base.kind == 'scalar':
                    base = Type(size=base.size, align=base.align, kind='scalar', sign=base.sign + 'v', base=base.base)
                pairs = [self.apply_decl(base, d) for d in self.declarators(' '.join(q[k:]))]
            for name, t, bits in pairs:
                a = 1 if mpacked else t.align
                if bits is not None and kind == 'struct':
                    csize = t.size
                    if bits == 0: bitcur = None; continue
                    if bitcur and bitcur[1] == csize and bitcur[2] + bits <= csize * 8:
                        coff, _, used = bitcur
                    elif mpacked:
                        coff = off if not bitcur else bitcur[0] + (bitcur[2] + 7) // 8
                        used = 0
                    else:
                        # AAPCS: the first container of the field's type, at its alignment, that
                        # still has room at the next free bit
                        nxt = (bitcur[0] * 8 + bitcur[2]) if bitcur else off * 8
                        coff = (nxt // 8) // csize * csize
                        used = nxt - coff * 8
                        if used + bits > csize * 8: coff += csize; used = 0
                    fields.append((name, coff, t, used, bits))
                    bitcur = (coff, csize, used + bits)
                    off = max(off, coff + (used + bits + 7) // 8)
                    align = max(align, a); size = max(size, off)
                    continue
                if bits is not None:   # a bitfield in a union
                    fields.append((name, 0, t, 0, bits)); size = max(size, t.size); align = max(align, a); continue
                bitcur = None
                if kind == 'union':
                    fields.append((name, 0, t, None, None)); size = max(size, t.size)
                else:
                    off = (off + a - 1) // a * a
                    fields.append((name, off, t, None, None)); off += t.size; size = off
                align = max(align, a)
        if packed: align = 1
        size = (size + align - 1) // align * align
        return Type(size=size, align=align, kind=kind, fields=fields, packed=packed)

    def scan(self):
        t = self.text
        for m in re.finditer(r'(?m)^\s*typedef\b', t):
            st = m.end(); semi = t.find(';', st); br = t.find('{', st)
            if br != -1 and br < semi:
                d, k = 0, br
                while k < len(t):
                    if t[k] == '{': d += 1
                    elif t[k] == '}':
                        d -= 1
                        if d == 0: break
                    k += 1
                end = t.find(';', k)
                head, body, names = t[st:br], t[br + 1:k], t[k + 1:end]
                hm = re.match(r'^\s*(__packed\s+)?(struct|union)\s*(\w+)?\s*$', head)
                if not hm: continue
                try:
                    ty = self.parse_body(hm.group(2), body, bool(hm.group(1)))
                except (KeyError, ValueError, SyntaxError, NameError):
                    continue
                if hm.group(3): self.types[hm.group(3)] = ty
                for d in self.declarators(names.replace('__packed', '')):
                    try: n, tt, _ = self.apply_decl(ty, d)
                    except (ValueError, SyntaxError, NameError): continue
                    self.types[n] = tt; self.texts[n] = t[m.start():end + 1]
            else:
                txt = t[st:semi].split()
                if not txt or '(' in t[st:semi]: continue
                try:
                    q = [w for w in txt[:-1] if w not in ('volatile', 'const')]
                    self.types[txt[-1].strip('*')] = (Type(size=4, align=4, kind='ptr', sign='u', base='ptr')
                                                      if '*' in t[st:semi] else self.resolve(q))
                except KeyError: pass


def flatten(t, off=0, path='', esize=False):
    """Leaf fields: (offset, size, sign or kind, bitpos, bits[, element size], path). An array of
    scalars is one leaf ('a' and the element's sign); padding members are left out."""
    if t.kind in ('struct', 'union'):
        for name, o, ft, bp, bits in t.fields:
            p = (path + '.' + name) if name else path
            if bits is not None:
                yield (off + o, ft.size, ft.sign if ft.kind == 'scalar' else '?', bp, bits) + ((None,) if esize else ()) + (p.lstrip('.'),)
            else: yield from flatten(ft, off + o, p, esize)
    elif t.kind == 'array':
        if t.elem.kind == 'scalar' and re.search(PAD, path): return
        sign = 'a' + (t.elem.sign if t.elem.kind == 'scalar' else str(t.elem.size))
        yield (off, t.size, sign, None, None) + ((t.elem.size,) if esize else ()) + (path.lstrip('.'),)
    else:
        if re.search(PAD, path): return
        yield (off, t.size, t.sign, None, None) + ((None,) if esize else ()) + (path.lstrip('.'),)


def canon_leaves(t, off=0, path=''):
    """Canonical leaves with element sizes and C paths, every union member included."""
    if t.kind in ('struct', 'union'):
        for name, o, ft, bp, bits in t.fields:
            p = (path + '.' + name) if name else path
            if bits is not None: yield (off + o, ft.size, ft.sign if ft.kind == 'scalar' else '?', bp, bits, None, p.lstrip('.'))
            else: yield from canon_leaves(ft, off + o, p)
    elif t.kind == 'array':
        if t.elem.kind == 'scalar':
            if re.search(PAD, path): return
            yield (off, t.size, 'a' + t.elem.sign, None, None, t.elem.size, path.lstrip('.'))
        else:
            yield (off, t.size, 'a' + str(t.elem.size), None, None, t.elem.size, path.lstrip('.'))
    else:
        if re.search(PAD, path): return
        yield (off, t.size, t.sign, None, None, None, path.lstrip('.'))


def overlaps(a, b):
    if a[3] is not None and b[3] is not None and a[0] == b[0] and a[1] == b[1]:
        return not (a[3] + a[4] <= b[3] or b[3] + b[4] <= a[3])
    return a[0] < b[0] + b[1] and b[0] < a[0] + a[1]


CKEYWORDS = {'else', 'return', 'case', 'goto', 'do', 'if', 'while', 'for', 'sizeof', 'typedef', 'extern'}
DECL = re.compile(r'(?m)^(extern\s+)?((?:(?:const|volatile|__packed|struct|union|static)\s+)*)(\w+)\s*(\*?)\s*'
                  r'([A-Za-z_]\w*)\s*((?:\[[^\]]*\])*)\s*(?:__attribute__\s*\(\(.*?\)\)\s*)?(?:=|;)')


class Tree:
    """The project's sources, each parsed with the headers it includes."""

    def __init__(self, cfg):
        self.root = os.path.realpath(cfg.root)
        self.src, self.include = cfg.src, getattr(cfg, 'include', None) or cfg.src
        self.exclude = [os.path.realpath(x) for x in (getattr(cfg, 'exclude', None) or [])]
        self._parsers = {}

    def sources(self):
        out = []
        for f in sorted(glob.glob(os.path.join(self.src, '**', '*.c'), recursive=True)):
            rf = os.path.realpath(f)
            if any(rf.startswith(x + os.sep) for x in self.exclude): continue
            out.append(f)
        return out

    def rel(self, f):
        return os.path.relpath(os.path.realpath(f), self.root)

    def with_includes(self, path, seen=None):
        """path's text after the text of every quoted include it reaches, recursively."""
        seen = set() if seen is None else seen
        p = os.path.realpath(path)
        if p in seen or not os.path.isfile(p): return ''
        seen.add(p)
        with open(p, encoding='latin1') as f: text = f.read()
        pre = ''
        for inc in re.findall(r'^\s*#\s*include\s+"([^"]+)"', text, re.M):
            for d in (os.path.dirname(p), self.include):
                q = os.path.join(d, inc)
                if os.path.isfile(q): pre += self.with_includes(q, seen) + '\n'; break
        return pre + text

    def parser(self, path):
        if path not in self._parsers: self._parsers[path] = Parser(self.with_includes(path))
        return self._parsers[path]

    def views(self, name):
        """[(file, type name, Type or None, is a definition)] for a global."""
        out = []
        for f in self.sources():
            with open(f, encoding='latin1') as fh: s = fh.read()
            if name not in s: continue
            for m in DECL.finditer(strip_comments(s)):
                if m.group(5) != name or m.group(3) in CKEYWORDS: continue
                p = self.parser(f)
                words = ([m.group(2).split()[-1]] if m.group(2).split() and m.group(2).split()[-1] in ('struct', 'union') else []) + [m.group(3)]
                try:
                    base = p.resolve(words)
                except KeyError:
                    out.append((self.rel(f), m.group(3), None, not m.group(1))); continue
                _, t, _ = p.apply_decl(base, m.group(4) + 'x' + m.group(6))
                out.append((self.rel(f), m.group(3), t, not m.group(1)))
        return out

    def survey(self):
        names = collections.Counter()
        for f in self.sources():
            with open(f, encoding='latin1') as fh:
                found = {m.group(5) for m in DECL.finditer(strip_comments(fh.read())) if m.group(3) not in CKEYWORDS}
            names.update(found)
        return [(n, c) for n, c in names.most_common() if c > 1]


def elem(t):
    return t.elem if t.kind == 'array' else t


def report(tree, name):
    vs = tree.views(name)
    out, byoff = [], collections.defaultdict(lambda: collections.defaultdict(list))
    for f, tn, t, d in vs:
        out.append(f'{f}: {tn} size {t.size if t else "?"}{" (definition)" if d else ""}')
        if not t: continue
        for off, size, sign, bp, bits, path in flatten(elem(t)):
            byoff[off][(off, size, sign, bp, bits)].append((os.path.basename(f), path))
    out.append('')
    conflicts = 0
    for off in sorted(byoff):
        keys = byoff[off]
        flag = '  ' if len(keys) == 1 else '!!'
        conflicts += len(keys) > 1
        for k, uses in keys.items():
            names = sorted({p for _, p in uses})
            out.append(f'{flag} 0x{off:03x} size {k[1]} {k[2]}{"" if k[3] is None else f" bits {k[3]}+{k[4]}"}: '
                       f'{", ".join(names)[:60]}  ({len(uses)} files)')
    out.append(f'\n{len(vs)} views, {len(byoff)} offsets, {conflicts} with more than one shape')
    return out


def arraycanon(vs):
    """The canonical leaves by majority shape, arrays carrying their element size."""
    count, esize = collections.Counter(), {}
    for f, tn, t, d in vs:
        if not t: continue
        for l in flatten(elem(t), esize=True):
            count[l[:5]] += 1
            if l[5]: esize[l[:5]] = l[5]
    canon = []
    for k, c in sorted(count.items(), key=lambda kc: (-kc[1], kc[0][0])):
        if not any(overlaps(k, x[:5]) for x in canon): canon.append(k + ((esize.get(k),) if k in esize else ()))
    canon.sort(key=lambda c: (c[0], -1 if c[3] is None else c[3]))
    return canon, count


def type_name(parser, size, sign):
    """The project's name for an integer type (a scalar typedef in view), else C's."""
    for n, t in parser.types.items():
        if getattr(t, 'kind', '') == 'scalar' and (t.size, t.sign) == (size, sign) and n not in SCALARS: return n
    return C_NAMES.get((size, sign), 'unsigned char')


def draft(tree, name, tname):
    vs = [v for v in tree.views(name) if v[2]]
    if not vs: return f'/* {name}: no copy with a known type */'
    canon, count = arraycanon(vs)
    defs = [t for _, _, t, d in vs if d]
    size = (elem(defs[0]).size if defs else max(elem(t).size for _, _, t, _ in vs))
    packed = any(elem(t).packed for _, _, t, _ in vs)
    bitbytes = {k[0] for k in count if k[3] is not None and k[1] == 1}
    p = tree.parser(os.path.join(tree.root, vs[0][0]))
    ty = lambda sz, sg: ('volatile ' if sg.endswith('v') else '') + type_name(p, sz, sg.rstrip('v'))
    lines, off, done = [], 0, set()
    for c in canon:
        o, sz, sign = c[:3]
        if o in done or o < off: continue
        if o > off: lines.append(f'{ty(1, "u")} p{off:02x}[{o - off}];'); off = o
        if o in bitbytes:
            lines.append(f'{ty(1, "u")} f{o:02x};   /* also read as bitfields */'); done.add(o); off = o + 1; continue
        if sign.startswith('a'):
            es = c[5] if len(c) > 5 and c[5] else 1
            if sign[1:] in ('u', 's', 'uv', 'sv'): lines.append(f'{ty(es, sign[1:])} f{o:02x}[{sz // es}];')
            else: lines.append(f'{ty(1, "u")} f{o:02x}[{sz}];   /* an array of {sign[1:]}-byte elements */')
        elif c[3] is None:
            lines.append(f'{ty(sz, sign)} f{o:02x};')
        else:
            lines.append(f'{ty(sz, sign)} f{o:02x};   /* bitfields */')
        off = o + sz
    if off < size: lines.append(f'{ty(1, "u")} p{off:02x}[{size - off}];')
    body = '\n'.join('    ' + l for l in lines)
    return f'typedef {"__packed " if packed else ""}struct {{\n{body}\n}} {tname};   /* {size} bytes */'


LOOSE = {}   # old path -> the type the file read it as, for fields mapped across signedness


def mapping(fleaves, cleaves, loose=False, parser=None):
    """{old path: new path} for each file leaf, or (None, the first leaf that fits nothing)."""
    out = {}
    for l in fleaves:
        off, size, sign, bp, bits = l[:5]; old = l[-1]
        new = None
        for c in cleaves:
            if c[:5] == (off, size, sign, bp, bits): new = c[-1]; break
        if new is None:
            for c in cleaves:
                coff, csize, csign, cbp, cbits, es = c[:6]
                if not csign.startswith('a') or not es or not (coff <= off < coff + csize): continue
                if not sign.startswith('a') and bp is None and csign[1:] == sign and size == es and (off - coff) % es == 0:
                    new = f'{c[-1]}[{(off - coff) // es}]'; break
                if sign == csign and off == coff and size <= csize: new = c[-1]; break
        if new is None and loose and not sign.startswith('a') and bp is None:
            for c in cleaves:
                if c[:2] == (off, size) and c[3] is None and not c[2].startswith('a') and c[2].endswith('v') == sign.endswith('v'):
                    new = c[-1]
                    LOOSE[old] = type_name(parser, size, sign.rstrip('v')) if parser else C_NAMES[(size, sign.rstrip('v'))]
                    break
        if new is None: return None, l
        out[old] = new
    return out, None


def typedef_span(s, tn):
    """(start, end) of the typedef that defines TN alone, with its line end, in raw text."""
    for m in re.finditer(r'(?m)^typedef\b', s):
        st = m.start(); semi = s.find(';', st); br = s.find('{', st)
        if br == -1 or br > semi: continue
        d, k = 0, br
        while k < len(s):
            if s[k] == '{': d += 1
            elif s[k] == '}':
                d -= 1
                if d == 0: break
            k += 1
        end = s.find(';', k)
        if re.fullmatch(r'\s*' + re.escape(tn) + r'\s*', s[k + 1:end]):
            end += 1
            if s[end:end + 1] == '\n': end += 1
            return st, end
    return None


def convert(tree, name, header, tname, only=None, write=False, loose=False, with_def=False):
    """Rewrite every file declaring GLOBAL with a local struct to use TNAME from HEADER."""
    hpath = os.path.realpath(header if os.path.isabs(header) else os.path.join(tree.include, header))
    hp = Parser(tree.with_includes(hpath))
    if tname not in hp.types: return [], [(header, f'{tname} is not defined there')]
    ct = hp.types[tname]
    cl = list(canon_leaves(ct))
    done, skipped = [], []
    for f, tn, t, d in tree.views(name):
        if only and os.path.basename(f) not in only and f not in only: continue
        if not t or tn == tname: continue
        tt = elem(t)
        if d:
            if not with_def: continue
            if tt.kind not in ('struct', 'union') or tt.size != ct.size or tt.align != ct.align:
                skipped.append((f, f'definition: {tn} is not the same size and alignment as {tname}')); continue
        path = os.path.join(tree.root, f)
        with open(path, encoding='latin1') as fh: s = fh.read()
        p = tree.parser(path)
        if tt.kind not in ('struct', 'union'): skipped.append((f, f'declared as {tn}, not a struct')); continue
        LOOSE.clear()
        mp, bad = mapping(list(flatten(tt, esize=True)), cl, loose, p)
        if bad: skipped.append((f, f'no canonical field for {bad[-1]} at 0x{bad[0]:x} ({bad[1]} bytes {bad[2]})')); continue
        others = [m.group(5) for m in DECL.finditer(strip_comments(s)) if m.group(3) == tn and m.group(5) != name]
        if others: skipped.append((f, f'{tn} also types {others}')); continue
        theirs = set()
        for on, ot in p.types.items():
            if on == tn or ot is tt or getattr(ot, 'kind', '') not in ('struct', 'union'): continue
            if any(ft is tt for _, _, ft, _, _ in ot.fields): continue
            theirs |= {n for n, *_ in ot.fields if n}
        changes = {o: n for o, n in mp.items() if o != n}
        # rewrite through the global itself when the local type serves nothing else (exact);
        # otherwise every .field of the view, unless another struct in view shares a field name
        direct = len(re.findall(r'\b' + re.escape(tn) + r'\b', strip_comments(s))) == 2
        typed = set()
        if not direct and ({o.split('.')[0] for o in set(changes) | set(LOOSE)} & theirs):
            typed = set(re.findall(r'\b' + re.escape(tn) + r'\s*\*?\s*(?:const\s+)?(\w+)\s*[,;)=\[]', strip_comments(s))) - {name}
            direct = True
        span = typedef_span(s, tn)
        if not span: skipped.append((f, f'typedef {tn} not found in the file')); continue
        s = s[:span[0]] + s[span[1]:]
        for o in sorted(set(changes) | set(LOOSE), key=len, reverse=True):
            changes.setdefault(o, mp[o])
            field = r'\s*\.\s*'.join(re.escape(x) for x in o.split('.')) + r'\b'
            if direct:
                who = '|'.join(re.escape(v) for v in sorted({name} | typed))
                pat = r'(\b(?:' + who + r')\s*(?:\[[^\]]*\]\s*)?(?:\.|->))(\s*)' + field
            else:
                pat = r'(\.|->)(\s*)' + field
            s = re.sub(pat, lambda x: x.group(1) + changes[o], s)
            if o in LOOSE:     # reads keep the file's type: a cast at each read through the global
                rd = (r'(?<![&\w.])(' + re.escape(name) + r'\s*\.\s*' + re.escape(changes[o]) +
                      r')\b(?!\s*(?:[-+*/%&|^]|<<|>>)?=(?!=)|\s*\+\+|\s*--)')
                s = re.sub(rd, lambda x: '((' + LOOSE[o] + ')' + x.group(1) + ')', s)
        if LOOSE and re.search(r'(\+\+|--)\s*' + re.escape(name) + r'\s*\.', s):
            skipped.append((f, 'pre-increment of a sign-cast field')); continue
        s = re.sub(r'\b' + re.escape(tn) + r'\b', tname, s)
        s = re.sub(r'(?m)^extern\s+' + re.escape(tname) + r'\s+' + re.escape(name) + r'\s*;[ \t]*\n', '', s)
        inc = f'#include "{os.path.relpath(hpath, os.path.dirname(path))}"\n'
        if inc not in s:
            k = s.find('#include "')
            k = s.find('\n', k) + 1 if k >= 0 else 0
            s = s[:k] + inc + s[k:]
        if write:
            with open(path, 'w', encoding='latin1') as fh: fh.write(s)
        done.append((f, len(changes)))
    return done, skipped


def rename_fields(tree, rows, write=False):
    """rows: {type: [(old, new, comment)]}. Returns ({path: new text} changed, [problems])."""
    headers, problems, edits = {}, [], {}
    for h in sorted(glob.glob(os.path.join(tree.include, '**', '*.h'), recursive=True)):
        with open(h, encoding='latin1') as f: s = f.read()
        for t in rows:
            if re.search(r'\}\s*' + re.escape(t) + r'\s*;', s): headers[t] = h

    def text(p):
        if p not in edits:
            with open(p, encoding='latin1') as f: edits[p] = f.read()
        return edits[p]
    for t, fields in rows.items():
        h = headers.get(t)
        if not h: problems.append(f'{t}: no header defines it'); continue
        s = text(h)
        m = re.search(r'(?ms)^typedef\b[^;{]*\{.*?\}\s*' + re.escape(t) + r'\s*;', s)
        body = nb = m.group(0)
        for old, new, c in fields:
            pat = re.compile(r'(?m)^(\s*[\w ]+?\s)' + re.escape(old) + r'\b([^;\n]*;)[ \t]*(/\*.*?\*/)?[ \t]*$')
            if not pat.search(nb): problems.append(f'{t}.{old}: not a field on a line of its own'); continue
            nb = pat.sub(lambda x: f'{x.group(1)}{new}{x.group(2)}' + (f'   /* {c} */' if c else ''), nb, count=1)
        edits[h] = s.replace(body, nb)
        globs = re.findall(r'extern\s+(?:volatile\s+)?' + re.escape(t) + r'\s+(\w+)', edits[h])
        inc = os.path.basename(h)
        for f in tree.sources() + sorted(glob.glob(os.path.join(tree.src, '**', '*.h'), recursive=True)):
            if os.path.realpath(f) == os.path.realpath(h): continue
            src = text(f)
            if not re.search(r'#include\s+"[^"]*\b' + re.escape(inc) + '"', src): continue
            ptrs = re.findall(r'\b' + re.escape(t) + r'\s*\*\s*(?:const\s+)?(\w+)', src)
            for old, new, c in fields:
                for g in globs:
                    src = re.sub(r'(\b' + re.escape(g) + r'\s*(?:\[[^\]]*\]\s*)?\.\s*)' + re.escape(old) + r'\b',
                                 lambda x: x.group(1) + new, src)
                for p in ptrs:
                    src = re.sub(r'(\b' + re.escape(p) + r'\s*->\s*)' + re.escape(old) + r'\b', lambda x: x.group(1) + new, src)
            edits[f] = src
    changed = {}
    for p, s in edits.items():
        with open(p, encoding='latin1') as f:
            if f.read() != s: changed[p] = s
    if write:
        for p, s in changed.items():
            with open(p, 'w', encoding='latin1') as f: f.write(s)
    return changed, problems


def main(argv):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'tools'))
    import config
    path, a = config.pop_config(argv)
    if not a: sys.exit(__doc__)
    tree = Tree(config.load(path))
    write = '--write' in a
    if a[0] == 'survey':
        for n, c in tree.survey(): print(f'{n}\t{c}')
    elif a[0] == 'report' and len(a) > 1:
        print('\n'.join(report(tree, a[1])))
    elif a[0] == 'draft' and len(a) > 2:
        print(draft(tree, a[1], a[2]))
    elif a[0] == 'convert' and len(a) > 3:
        only = [x for x in a[4:] if not x.startswith('-')] or None
        done, skipped = convert(tree, a[1], a[2], a[3], only, write, '--loose-sign' in a, '--with-def' in a)
        for f, n in done: print(f'{"converted" if write else "would convert"} {f} ({n} field paths renamed)')
        for f, why in skipped: print(f'left      {f}: {why}')
    elif a[0] == 'fields' and len(a) > 1:
        rows = collections.defaultdict(list)
        with open(a[1], encoding='latin1') as f:
            for line in f:
                if not line.strip() or line.startswith('#'): continue
                t, old, new, *c = line.rstrip('\n').split('\t')
                rows[t].append((old, new, c[0] if c else ''))
        changed, problems = rename_fields(tree, rows, write)
        for p in problems: print(p)
        for p in changed: print(f'{"edited" if write else "would edit"} {tree.rel(p)}')
    else:
        sys.exit(__doc__)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
