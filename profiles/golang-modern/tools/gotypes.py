#!/usr/bin/env python3
"""gotypes.py: recover Go type declarations from a Go binary's runtime type information.

    gotypes.py BINARY PKG_REGEX     declarations of the named types of the matching packages

Prints, for each named type whose import path matches PKG_REGEX, a Go declaration: structs with
their field names, types, embedded fields and tags, interfaces with their methods, and other named
types with their underlying type. Types of other packages are written with their package name as
qualifier.

For binaries built by Go 1.20 and later for linux/amd64, not position independent (pointers in
type descriptors are read as absolute addresses). The layouts are those of internal/abi in Go
1.26. Type descriptors are found from the module's typelinks and itabs, from every reference to
them in the code (rip-relative lea) and in the data sections, and from the types these refer to.
A type that the program never needs at run time is not in the binary.

As a module: load(binary) returns a Types (see its methods), Renderer writes type expressions and
declarations for one package, and declare(binary, regex) returns what the command prints. An
instance of a generic type is written as a placeholder of its size, unless the Renderer is given
a generic_ok callable that accepts its generic type; it is then written with its type arguments.
"""
import re
import struct
import sys

import gobin

BOOL, INT, INT8, INT16, INT32, INT64, UINT, UINT8, UINT16, UINT32, UINT64, UINTPTR = range(1, 13)
FLOAT32, FLOAT64, COMPLEX64, COMPLEX128, ARRAY, CHAN, FUNC, INTERFACE, MAP, POINTER = range(13, 23)
SLICE, STRING, STRUCT, UNSAFE_POINTER = range(23, 27)
KIND_MASK = 0x1F

TFLAG_UNCOMMON, TFLAG_EXTRA_STAR, TFLAG_NAMED = 1, 2, 4

HEADER = 48                         # size of abi.Type
EXTRA = {ARRAY: 24, CHAN: 16, FUNC: 8, INTERFACE: 32, MAP: 64, POINTER: 8, SLICE: 8, STRUCT: 32}

# Word indexes in runtime.moduledata (Go 1.26).
MD_TEXT, MD_TYPES, MD_ETYPES, MD_TYPELINKS, MD_ITABLINKS = 22, 37, 38, 45, 48

LEA_RIP = re.compile(rb'[\x48\x4c]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]', re.S)


class Field:
    __slots__ = ('name', 'type', 'offset', 'embedded', 'tag')

    def __init__(self, name, typ, offset, embedded, tag):
        self.name, self.type, self.offset, self.embedded, self.tag = (name, typ, offset,
                                                                      embedded, tag)


class Method:
    """A method of a named type (type is its signature without the receiver, None when the
    linker dropped it) or of an interface. text is the entry of the code for a direct call,
    or None."""
    __slots__ = ('name', 'type', 'text')

    def __init__(self, name, typ, text=None):
        self.name, self.type, self.text = name, typ, text


class Type:
    """One type descriptor. str is the runtime's string for the type ("cart.Item", "[]int");
    for named types, pkgpath is the import path and name the name without the package."""

    def __init__(self, addr):
        self.addr = addr
        self.kind = self.size = self.align = 0
        self.str = self.name = self.pkgpath = ''
        self.named = False
        self.methods = []           # methods of a named type, or of a pointer to one
        self.elem = self.key = None
        self.len = self.dir = 0
        self.fields = []            # struct fields
        self.imethods = []          # interface methods
        self.ins, self.outs, self.variadic = [], [], False
        self.ptr_to_this = None

    def __repr__(self):
        return 'Type(%#x %s)' % (self.addr, self.str)

    @property
    def generic(self):
        """An instance of a generic type, such as Pair[string,int]."""
        return self.named and '[' in self.name


class Types:
    def __init__(self, b):
        self.b = b
        if b._md is None:
            raise ValueError('no moduledata found, so no runtime type information')
        self.base, self.end = b._md_word(MD_TYPES), b._md_word(MD_ETYPES)
        self.text = b._md_word(MD_TEXT)
        self._raw = {}              # addr -> Type, with children as addresses until linked
        self._types = {}
        self._load()

    # Low-level reading

    def _u(self, fmt, va):
        return struct.unpack_from(fmt, self.b.d, self.b._off(va))

    def _name(self, va):
        """(name, tag, flags) of an abi.Name at va."""
        d, p = self.b.d, self.b._off(va)
        flags = d[p]
        n, q = gobin._varint(d, p + 1)
        name = d[q:q + n].decode('utf-8', 'replace')
        tag = ''
        if flags & 2:
            tn, r = gobin._varint(d, q + n)
            tag = d[r:r + tn].decode('utf-8', 'replace')
        return name, tag, flags

    def _name_off(self, off):
        return self._name(self.base + off)[0] if off else ''

    def _in_types(self, va):
        return self.base <= va < self.end

    def _plausible(self, va):
        """Whether va looks like the start of a type descriptor."""
        if va % 8 or not self._in_types(va) or va + HEADER > self.end:
            return False
        try:
            size, _, _, tflag, align, falign, kind, _, _, str_off, ptr = self._u(
                '<QQIBBBBQQii', va)
        except (KeyError, struct.error):
            return False
        kind &= KIND_MASK
        if not (BOOL <= kind <= UNSAFE_POINTER) or align not in (1, 2, 4, 8) or \
                falign not in (1, 2, 4, 8) or tflag & 0xC0 or size > 1 << 40 or \
                not 0 < str_off < self.end - self.base or not -1 <= ptr < self.end - self.base:
            return False
        try:
            s, _, _ = self._name(self.base + str_off)
        except (KeyError, IndexError):
            return False
        return bool(s) and s.isprintable()

    def _parse(self, va):
        """A Type with its children as addresses, and the list of those addresses."""
        t = Type(va)
        (t.size, _, _, tflag, t.align, _, kind, _, _, str_off,
         ptr) = self._u('<QQIBBBBQQii', va)
        t.kind = kind & KIND_MASK
        t.str = self._name_off(str_off)
        if tflag & TFLAG_EXTRA_STAR and t.str.startswith('*'):
            t.str = t.str[1:]
        t.named = bool(tflag & TFLAG_NAMED)
        kids = []
        if ptr not in (0, -1):
            t.ptr_to_this = self.base + ptr
            kids.append(t.ptr_to_this)
        p = va + HEADER
        k = t.kind
        if k in (POINTER, SLICE):
            t.elem, = self._u('<Q', p)
            kids.append(t.elem)
        elif k == ARRAY:
            t.elem, _, t.len = self._u('<QQQ', p)
            kids.append(t.elem)
        elif k == CHAN:
            t.elem, t.dir = self._u('<Qq', p)
            kids.append(t.elem)
        elif k == MAP:
            t.key, t.elem = self._u('<QQ', p)
            kids += [t.key, t.elem]
        elif k == STRUCT:
            _, data, n, _ = self._u('<QQQQ', p)
            for i in range(n):
                nameptr, typ, off = self._u('<QQQ', data + 24 * i)
                name, tag, flags = self._name(nameptr)
                t.fields.append(Field(name, typ, off, bool(flags & 8), tag))
                kids.append(typ)
        elif k == INTERFACE:
            _, data, n, _ = self._u('<QQQQ', p)
            for i in range(n):
                name_off, typ_off = self._u('<ii', data + 8 * i)
                typ = self.base + typ_off if typ_off not in (0, -1) else None
                t.imethods.append(Method(self._name_off(name_off), typ))
                if typ:
                    kids.append(typ)
        uncommon = p + EXTRA.get(k, 0)
        if k == FUNC:
            nin, nout = self._u('<HH', p)
            t.variadic = bool(nout & 0x8000)
            nout &= 0x7FFF
            params = uncommon + (16 if tflag & TFLAG_UNCOMMON else 0)
            ptrs = self._u('<%dQ' % (nin + nout), params) if nin + nout else ()
            t.ins, t.outs = list(ptrs[:nin]), list(ptrs[nin:])
            kids += ptrs
        if tflag & TFLAG_UNCOMMON:
            pkg_off, mcount, _, moff = self._u('<iHHI', uncommon)
            t.pkgpath = self._name_off(pkg_off)
            for i in range(mcount):
                name_off, mtyp, _, tfn = self._u('<iiii', uncommon + moff + 16 * i)
                typ = self.base + mtyp if mtyp not in (0, -1) else None
                text = self.text + tfn if tfn != -1 else None
                t.methods.append(Method(self._name_off(name_off), typ, text))
                if typ:
                    kids.append(typ)
        if t.named:
            t.name = t.str.split('.', 1)[1] if '.' in t.str.split('[', 1)[0] else t.str
        return t, kids

    # Finding the descriptors

    def _roots(self):
        b = self.b
        data, n, _ = (b._md_word(MD_TYPELINKS + i) for i in range(3))
        roots = [self.base + o for o in self._u('<%di' % n, data)] if n else []
        data, n, _ = (b._md_word(MD_ITABLINKS + i) for i in range(3))
        for itab in (self._u('<%dQ' % n, data) if n else ()):
            roots += self._u('<QQ', itab)
        return roots

    def _references(self):
        """Addresses in the types range named by a rip-relative lea in the code or by an aligned
        word in the data."""
        d, out = self.b.d, set()
        lo, hi = self.base, self.end
        for vaddr, off, filesz, flags in self.b.loads:
            seg = d[off:off + filesz]
            if flags & 1:
                for m in LEA_RIP.finditer(seg):
                    p = m.start()
                    disp, = struct.unpack_from('<i', seg, p + 3)
                    va = vaddr + p + 7 + disp
                    if lo <= va < hi:
                        out.add(va)
            else:
                words = struct.unpack_from('<%dQ' % (filesz // 8), seg)
                out.update(w for w in words if lo <= w < hi)
        return out

    def _load(self):
        todo = [a for a in self._roots() if self._in_types(a)]
        todo += [a for a in self._references() if self._plausible(a)]
        while todo:
            va = todo.pop()
            if va in self._raw or not self._in_types(va):
                continue
            try:
                t, kids = self._parse(va)
            except (KeyError, struct.error, IndexError):
                continue
            self._raw[va] = t
            todo.extend(k for k in kids if k and k not in self._raw)
        self._link()

    def _link(self):
        get = self._raw.get
        for t in self._raw.values():
            t.elem, t.key = get(t.elem), get(t.key)
            t.ptr_to_this = get(t.ptr_to_this)
            for f in t.fields:
                f.type = get(f.type)
            for m in t.methods + t.imethods:
                m.type = get(m.type)
            t.ins = [get(x) for x in t.ins]
            t.outs = [get(x) for x in t.outs]
        # Drop what a bad reference parsed into: anything with a child that is not a type.
        bad = {a for a, t in self._raw.items() if
               (t.kind in (POINTER, SLICE, ARRAY, CHAN, MAP) and t.elem is None) or
               (t.kind == MAP and t.key is None) or any(f.type is None for f in t.fields) or
               None in t.ins or None in t.outs}
        self._types = {a: t for a, t in self._raw.items() if a not in bad}
        for t in self._types.values():
            if t.kind == POINTER and t.elem is not None and t.elem.ptr_to_this is None:
                t.elem.ptr_to_this = t

    # Queries

    def all(self):
        return list(self._types.values())

    def type(self, addr):
        return self._types.get(addr)

    def named_types(self, pkg_regex=None):
        """Named types (of the packages whose import path matches pkg_regex), by package and
        name; generic instances are included."""
        rx = re.compile(pkg_regex) if pkg_regex else None
        return sorted((t for t in self._types.values() if t.named and t.pkgpath and
                       (rx is None or rx.search(t.pkgpath))),
                      key=lambda t: (t.pkgpath, t.name, t.addr))

    def named(self, pkgpath, name):
        """The named type, or None (also when two types of the package share the name, as
        types declared in two functions can)."""
        found = [t for t in self.named_types() if t.pkgpath == pkgpath and t.name == name]
        return pick(found)

    def package_names(self):
        """{import path: package name}, from the strings of named types."""
        out = {}
        for t in self._types.values():
            if t.named and t.pkgpath and '.' in t.str:
                out.setdefault(t.pkgpath, t.str.split('.', 1)[0])
        return out


def pick(same_name):
    """Of the types of one package that share a name, the one declared at package level: the
    only one, or the only one with methods (a type declared in a function has none)."""
    if len(same_name) == 1:
        return same_name[0]
    with_methods = [t for t in same_name if t.methods or
                    (t.ptr_to_this is not None and t.ptr_to_this.methods)]
    return with_methods[0] if len(with_methods) == 1 else None


def load(b):
    if isinstance(b, str):
        b = gobin.load(b)
    return Types(b)


BASIC = {BOOL: 'bool', INT: 'int', INT8: 'int8', INT16: 'int16', INT32: 'int32', INT64: 'int64',
         UINT: 'uint', UINT8: 'uint8', UINT16: 'uint16', UINT32: 'uint32', UINT64: 'uint64',
         UINTPTR: 'uintptr', FLOAT32: 'float32', FLOAT64: 'float64', COMPLEX64: 'complex64',
         COMPLEX128: 'complex128', STRING: 'string'}


def go_quote(s):
    if '`' not in s and s.isprintable():
        return '`' + s + '`'
    return '"' + ''.join(c if c.isprintable() and c not in '"\\' else
                         '\\' + c if c in '"\\' else '\\x%02x' % ord(c) for c in s) + '"'


def opaque(size, align):
    """A type of the given size and alignment with nothing in it worth naming."""
    if size == 0:
        return 'struct{}'
    unit = {8: 'uint64', 4: 'uint32', 2: 'uint16'}.get(align)
    if unit is None or size < align:
        return '[%d]byte' % size
    whole, rest = divmod(size, align)
    if not rest:
        return '[%d]%s' % (whole, unit)
    return 'struct { _ [%d]%s; _ [%d]byte }' % (whole, unit, rest)


def tabular(rows, indent='\t'):
    """Lines of cells aligned the way gofmt aligns struct fields: a column is aligned over a run
    of consecutive lines that have a cell after it."""
    widths = [[0] * len(r) for r in rows]
    for col in range(max((len(r) for r in rows), default=0)):
        run = []
        for i, r in enumerate(rows + [[]]):
            if len(r) > col + 1:
                run.append(i)
                continue
            w = max((len(rows[j][col]) for j in run), default=0)
            for j in run:
                widths[j][col] = w
            run = []
    out = []
    for r, w in zip(rows, widths):
        cells = [c.ljust(w[i] + 1) if i < len(r) - 1 else c for i, c in enumerate(r)]
        out.append(indent + ''.join(cells))
    return out


def split_top(s, sep='.'):
    """s split at each sep outside brackets, parentheses and braces."""
    parts, depth, cur = [], 0, []
    for c in s:
        if c in '([{':
            depth += 1
        elif c in ')]}':
            depth -= 1
        if c == sep and depth == 0:
            parts.append(''.join(cur))
            cur = []
        else:
            cur.append(c)
    parts.append(''.join(cur))
    return parts


def instance_parts(s):
    """('Name', ['arg', ...]) of 'Name[a,b]' (the arguments split at top-level commas), or
    ('Name', []) when s has no type arguments, or None when the brackets do not close at the
    end."""
    if '[' not in s:
        return s, []
    i = s.index('[')
    if not s.endswith(']'):
        return None
    inner = s[i + 1:-1]
    depth = 0
    for c in inner:
        depth += c in '([{'
        depth -= c in ')]}'
        if depth < 0:
            return None
    return s[:i], [a.strip() for a in split_top(inner, ',')]


PREDECLARED = {'bool', 'byte', 'complex64', 'complex128', 'error', 'float32', 'float64', 'int',
               'int8', 'int16', 'int32', 'int64', 'rune', 'string', 'uint', 'uint8', 'uint16',
               'uint32', 'uint64', 'uintptr', 'any'}
IDENT = re.compile(r'^[A-Za-z_]\w*$')


class Renderer:
    """Writes type expressions and declarations as seen from one package.

    local(t) says whether a named type of this package is declared (and so may be named);
    usable(t) whether a named type of another package may be named (its package can be imported
    and the type exists there). A type that may not be named is written as a placeholder of its
    size and alignment, and counted in placeholders. imports collects {import path: name}.
    taken is the set of identifiers of the package, which an import name must not hide.

    An instance of a generic type is written as a placeholder too, unless generic_ok(import
    path, base name) says that the generic type is declared in source. It is then written as
    the base name with its type arguments, which are read from the runtime's name of the
    instance (see type_arg). named_ok(import path, name) says whether a named type that appears
    in such an argument may be named."""

    def __init__(self, pkgpath, pkg_names, local=lambda t: True, usable=lambda t: True,
                 taken=(), generic_ok=None, named_ok=None):
        self.pkg, self.pkg_names = pkgpath, pkg_names
        self.local, self.usable = local, usable
        self.taken = set(taken)
        self.imports = {}
        self.placeholders = []
        self.may_import = lambda path: True     # whether a new import may be added
        self.generic_ok = generic_ok or (lambda path, name: False)
        self.named_ok = named_ok or (lambda path, name: True)

    def qualifier(self, path):
        if path in self.imports:
            return self.imports[path]
        base = self.pkg_names.get(path) or path.rsplit('/', 1)[-1]
        base = re.sub(r'\W', '_', base)
        if not base or base[0].isdigit():
            base = '_' + base
        name, n = base, 1
        used = set(self.imports.values())
        while name in used or name in self.taken:
            n += 1
            name = '%s%d' % (base, n)
        self.imports[path] = name
        return name

    def _placeholder(self, t):
        self.placeholders.append(t)
        return opaque(t.size, t.align)

    def expr(self, t):
        if t is None:
            return 'struct{}'
        k = t.kind
        if k == UNSAFE_POINTER:
            if not self.may_import('unsafe') and 'unsafe' not in self.imports:
                return self._placeholder(t)
            return self.qualifier('unsafe') + '.Pointer'
        if t.named:
            if not t.pkgpath:                       # int, string, error, ...
                return t.name
            if t.pkgpath == 'main' and self.pkg != 'main':
                return self._placeholder(t)
            if t.generic:
                return self.instance(t.pkgpath, t.name) or self._placeholder(t)
            if t.pkgpath == self.pkg:
                return t.name if self.local(t) else self._placeholder(t)
            if not t.name[:1].isupper() or not self.usable(t) or \
                    t.pkgpath not in self.imports and not self.may_import(t.pkgpath):
                return self._placeholder(t)
            return self.qualifier(t.pkgpath) + '.' + t.name
        return self.underlying(t)

    def instance(self, path, name):
        """The source text of the instance name ('Base[arg,...]') of a generic type of package
        path, or None when generic_ok refuses the base or an argument cannot be written. Nothing
        is added to imports when it returns None."""
        saved = dict(self.imports)
        text = self._instance(path, name)
        if text is None:
            self.imports = saved
        return text

    def _named(self, path, name):
        """How a named, non-generic type is written here, or None."""
        if not IDENT.match(name) or not path or not self.named_ok(path, name):
            return None
        if path == self.pkg:
            return name
        if path == 'main' or not name[:1].isupper() or \
                path not in self.imports and not self.may_import(path):
            return None
        return self.qualifier(path) + '.' + name

    def _instance(self, path, name):
        parts = instance_parts(name)
        if parts is None or not parts[1]:
            return None
        base, args = parts
        if not IDENT.match(base) or not self.generic_ok(path, base):
            return None
        head = self._named(path, base) if path != self.pkg else base
        if head is None:
            return None
        texts = [self.type_arg(a) for a in args]
        if None in texts:
            return None
        return '%s[%s]' % (head, ', '.join(texts))

    def type_arg(self, s):
        """The source text of a type argument as the runtime writes it in an instance's name
        (named types as import/path.Name), or None. Accepted: predeclared identifiers, named
        types that may be named here, *X, []X, [N]X, map[K]V, struct {}, interface {}, and
        instances of generic types that generic_ok accepts."""
        s = s.strip()
        if s in PREDECLARED:
            return s
        if s in ('struct {}', 'struct{}'):
            return 'struct{}'
        if s in ('interface {}', 'interface{}'):
            return 'interface{}'
        if s.startswith('*'):
            inner = self.type_arg(s[1:])
            return None if inner is None else '*' + inner
        if s.startswith('[]'):
            inner = self.type_arg(s[2:])
            return None if inner is None else '[]' + inner
        m = re.match(r'^\[(\d+)\](.+)$', s)
        if m:
            inner = self.type_arg(m.group(2))
            return None if inner is None else '[%s]%s' % (m.group(1), inner)
        if s.startswith('map['):
            depth = 0
            for j in range(3, len(s)):
                depth += s[j] in '([{'
                depth -= s[j] in ')]}'
                if depth == 0:
                    break
            else:
                return None
            key, val = self.type_arg(s[4:j]), self.type_arg(s[j + 1:])
            return None if key is None or val is None else 'map[%s]%s' % (key, val)
        head = s.split('[', 1)[0]
        if '.' not in head or ' ' in head or '(' in head:
            return None
        path, base = head.rsplit('.', 1)
        if '[' in s:
            return self._instance(path, base + s[len(head):])
        return self._named(path, base)

    def underlying(self, t):
        k = t.kind
        if k in BASIC:
            return BASIC[k]
        if k == POINTER:
            return '*' + self.expr(t.elem)
        if k == SLICE:
            return '[]' + self.expr(t.elem)
        if k == ARRAY:
            return '[%d]%s' % (t.len, self.expr(t.elem))
        if k == MAP:
            return 'map[%s]%s' % (self.expr(t.key), self.expr(t.elem))
        if k == CHAN:
            elem = self.expr(t.elem)
            if t.dir == 1:
                return '<-chan ' + elem
            if t.dir == 2:
                return 'chan<- ' + elem
            if t.elem is not None and t.elem.kind == CHAN and not t.elem.named and t.elem.dir == 1:
                elem = '(' + elem + ')'
            return 'chan ' + elem
        if k == FUNC:
            return 'func' + self.signature(t)
        if k == INTERFACE:
            if not t.imethods:
                return 'interface{}'
            return 'interface { %s }' % '; '.join(m.name + self.signature(m.type)
                                                  for m in t.imethods)
        if k == STRUCT:
            if not t.fields:
                return 'struct{}'
            return 'struct { %s }' % '; '.join(' '.join(r) for r in self.field_rows(t))
        if k == UNSAFE_POINTER:
            return self.qualifier('unsafe') + '.Pointer'
        return self._placeholder(t)

    def signature(self, f, skip=0):
        """'(params) results' of a func type; skip leaves out leading parameters (a receiver)."""
        if f is None:
            return '()'
        ins = [self.expr(x) for x in f.ins[skip:]]
        if f.variadic and ins:
            last = f.ins[-1]
            ins[-1] = '...' + self.expr(last.elem if last.kind == SLICE else last)
        outs = [self.expr(x) for x in f.outs]
        s = '(' + ', '.join(ins) + ')'
        if len(outs) == 1:
            s += ' ' + outs[0]
        elif outs:
            s += ' (' + ', '.join(outs) + ')'
        return s

    def field_rows(self, t):
        rows = []
        for f in t.fields:
            typ = self.expr(f.type)
            if f.embedded and typ.lstrip('*').split('.')[-1] == f.name:
                row = [typ]
            else:
                row = [f.name, typ]
            if f.tag:
                row.append(go_quote(f.tag))
            rows.append(row)
        return rows

    def decl(self, t):
        """'type Name ...' for a named type."""
        if t.kind == STRUCT and t.fields:
            return 'type %s struct {\n%s\n}' % (t.name, '\n'.join(tabular(self.field_rows(t))))
        if t.kind == INTERFACE and t.imethods:
            lines = ['\t' + m.name + self.signature(m.type) for m in
                     sorted(t.imethods, key=lambda m: m.name)]
            return 'type %s interface {\n%s\n}' % (t.name, '\n'.join(lines))
        return 'type %s %s' % (t.name, self.underlying(t))


def declare(b, pkg_regex):
    """Declarations of the named types of the packages matching pkg_regex, one package after
    another, each headed by a comment naming it."""
    types = b if isinstance(b, Types) else load(b)
    names = types.package_names()
    out, by_pkg = [], {}
    for t in types.named_types(pkg_regex):
        if not t.generic:
            by_pkg.setdefault(t.pkgpath, []).append(t)
    for pkg, ts in sorted(by_pkg.items()):
        groups = {}
        for t in ts:
            groups.setdefault(t.name, []).append(t)
        chosen = {id(pick(g)) for g in groups.values() if pick(g) is not None}
        r = Renderer(pkg, names, local=lambda t: id(t) in chosen)
        out.append('// package %s\n' % pkg)
        for name in sorted(groups):
            t = pick(groups[name])
            out.append((r.decl(t) if t else '// %s: %d types of this name' % (
                name, len(groups[name]))) + '\n')
    return '\n'.join(out)


def main(argv):
    if len(argv) != 3:
        sys.exit(__doc__.split('\n\n')[1])
    sys.stdout.write(declare(argv[1], argv[2]))


if __name__ == '__main__':
    main(sys.argv)
