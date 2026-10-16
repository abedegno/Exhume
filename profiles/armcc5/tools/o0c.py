"""o0c: a deterministic translator from armcc -O0 Thumb-2 code to C drafts that compile back to it.

    python3 o0c.py [--config PATH] gen [--only ADDR,...]    drafts of every code row in build/o0c/
    python3 o0c.py [--config PATH] one ADDR|NAME            translate one function and print it
    python3 o0c.py [--config PATH] unit ADDR|NAME           the draft as a compilable unit (externs, macros, prototypes)
    python3 o0c.py [--config PATH] score [--only ADDR,...] [--jobs N]   compile each draft's unit and score it
    python3 o0c.py [--config PATH] asm ADDR|NAME            the instructions as the translator decodes them
    python3 o0c.py [--config PATH] blocks ADDR|NAME         the basic blocks and their terminators
    python3 o0c.py [--config PATH] files                    source files proposed from literal loads (no --split_sections)
    python3 o0c.py [--config PATH] file ADDR|NAME [--write F.c]   the drafts of that source file as one C file, for match.py
    python3 o0c.py [--config PATH] probe FILE.c             compile a C snippet at the o0c options and list its code

It reads the image ([binary] image) and the map (map/functions.tsv and data.tsv) only. Rows of
kind `lib` are library code and are skipped. Optional `[o0c]` settings in exhume.toml:

    [o0c]
    opts = "--cpu Cortex-M3 -O0"          # what the image was built with (score and probe add --split_sections)
    ram = [[0x20000000, 0x40000000]]      # address ranges that are RAM globals (default: the SRAM region)
    structs = "map/o0c-structs.json"       # stack structures passed to known functions (below)

A structs file names stack structures a function takes the address of, so a draft declares the
structure and stores its fields by name (a vendor peripheral library's *_Init structures):
{"structs": {"T": [size, [[offset, size, "field"], ...]]}, "args": {"func": [argument index, "T"]}}.

armcc -O0 is close to a template compiler: each statement is evaluated into r0-r3 (a pool address
loaded once may be reused in a block), parameters are copied to r4-r7 in the prologue, locals live
in callee-saved registers (allocated by use count, ties by declaration order) or stack slots
(volatile, address taken; first declared highest), and control flow follows the source's layout
(compiler.md, "armcc -O0"). The translator:

1. decode   capstone over the map row; literal pools and tbb/tbh/word-offset tables skipped, pool
            words read as constants or addresses. Without --split_sections a file's pool may lie
            after a later function, so the pool words are those the whole image's code loads.
2. lift     every instruction to micro-ops on expressions (set, flags, store, call, branch, switch).
3. webs     reaching definitions per register over the CFG; defs that reach a common use are one
            web. A web is a parameter (entry value, or its prologue copy), a temporary (one def,
            used in its own block: substituted into the use, so statements rebuild themselves), a
            constant (a literal kept in a scratch register across blocks), a select (r0-r3 set in
            both arms of a diamond and used at the join: `c ? a : b`), or a local variable (named
            after its register, `v4`).
4. eval     each block symbolically: statements (assignments to locals, stores, calls whose value
            is unused) and a terminator (goto, conditional goto on a comparison, switch, return).
5. shape    structured C from the block layout: condition chains reduced to && / ||, if / if-else,
            while / for (`b test; body; test: bcc`), do-while, endless loops, break / continue,
            switch from tbb/tbh tables and compare trees (the dispatch walked with the set of values
            still possible at each block), returns, goto where nothing fits. A function that will
            not translate as laid out is tried again with merged statement tails copied back into
            their predecessors and with selects as locals.
6. emit     C with the types int, uint, short, ushort, sbyte, uchar; a DAT_<addr> token per global
            address and access type; callees with prototypes. `unit` declares each token through the
            literal the image loads (a typed extern when one type reaches the symbol's own address,
            else a typed macro over a byte array), so the access goes through the same pool word.

A draft is a start, not a proof: the score masks call targets and pool words, and only armmatch
on the whole source file (functions in their original order) proves bytes."""
import collections, difflib, json, os, re, sys, time
HERE = os.path.dirname(os.path.abspath(__file__))
EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
for p in (HERE, os.path.join(EXHUME, 'tools')):
    if p not in sys.path: sys.path.insert(0, p)
import armmap, ehex          # noqa: E402

REGN = {f'r{i}': i for i in range(13)}
REGN.update(sb=9, sl=10, fp=11, ip=12, sp=13, lr=14, pc=15)
CCS = {'eq', 'ne', 'cs', 'hs', 'cc', 'lo', 'mi', 'pl', 'vs', 'vc', 'hi', 'ls', 'ge', 'lt', 'gt', 'le', 'al'}
BASES = set('''mov mvn add adc sub sbc rsb neg mul mla mls sdiv udiv and orr orn eor bic lsl lsr asr ror rrx
uxtb uxth sxtb sxth ubfx sbfx bfi bfc cmp cmn tst teq ldr ldrb ldrh ldrsb ldrsh ldrd str strb strh strd ldm stm
ldmia stmia ldmdb stmdb push pop b bl blx bx cbz cbnz tbb tbh nop adr movw movt addw subw clz umull smull umlal
smlal cpsid cpsie dsb isb dmb msr mrs svc wfi wfe bkpt yield sev rev rev16 revsh rbit hint udf ssat usat'''.split())
ITS = re.compile(r'^it[te]{0,3}$')
TSIZE = {'uchar': 1, 'sbyte': 1, 'ushort': 2, 'short': 2, 'int': 4, 'uint': 4}
NARROW = {'uchar', 'sbyte', 'ushort', 'short'}
RANGE = {'uchar': (0, 255), 'sbyte': (-128, 127), 'ushort': (0, 65535), 'short': (-32768, 32767)}
LDT = {'ldr': 'int', 'ldrb': 'uchar', 'ldrh': 'ushort', 'ldrsb': 'sbyte', 'ldrsh': 'short'}
STT = {'str': 'int', 'strb': 'uchar', 'strh': 'ushort'}
INTRINSIC = {'cpsid': '__disable_irq', 'cpsie': '__enable_irq', 'wfi': '__wfi', 'wfe': '__wfe', 'sev': '__sev',
             'dsb': '__dsb', 'isb': '__isb', 'dmb': '__dmb'}
INTRINSICS = set(INTRINSIC.values()) | {'__clz', '__nop', '__disable_fiq', '__enable_fiq'}
DEFAULT_OPTS = '--cpu Cortex-M3 -O0'
DEFAULT_RAM = [(0x20000000, 0x40000000)]
TYPEDEFS = '''typedef unsigned int uint;
typedef unsigned short ushort;
typedef unsigned char uchar;
typedef signed char sbyte;
typedef void code(void);
'''


class Unsupported(Exception):
    pass


class Mem:
    """Bytes at an address: the image, or a compiled function at 0."""

    def __init__(self, base, data):
        self.base, self.data, self.end = base, bytes(data), base + len(data)

    def get(self, a, n):
        p = a - self.base
        if p < 0: return b''
        return self.data[p:p + n]

    def word(self, a):
        p = a - self.base
        if 0 <= p and p + 4 <= len(self.data): return int.from_bytes(self.data[p:p + 4], 'little')
        return None

# ---------------------------------------------------------------- decoding


class Ins:
    __slots__ = ('addr', 'size', 'mn', 'cc', 'sf', 'ops', 'raw', 'tab', 'k')

    def __repr__(self):
        return f'{self.addr:08X} {self.raw}'


def split_mn(mn):
    mn = mn.split('.')[0]
    if mn in BASES or ITS.match(mn): return mn, None, False
    if mn[-2:] in CCS and mn[:-2] in BASES: return mn[:-2], mn[-2:], False
    if mn.endswith('s') and mn[:-1] in BASES: return mn[:-1], None, True
    if mn[-2:] in CCS and mn[-3:-2] == 's' and mn[:-3] in BASES: return mn[:-3], mn[-2:], True
    return mn, None, False


def split_top(s):
    out, depth, cur = [], 0, ''
    for ch in s:
        if ch == ',' and depth == 0: out.append(cur.strip()); cur = ''; continue
        if ch in '[{': depth += 1
        if ch in ']}': depth -= 1
        cur += ch
    if cur.strip(): out.append(cur.strip())
    return out


def num(s):
    return int(s.lstrip('#'), 0)


def parse_ops(s):
    res = []
    for t in split_top(s):
        if t.startswith('{'):
            res.append(('list', [REGN[x.strip()] for x in t.strip('{}').split(',') if x.strip()]))
        elif t.startswith('['):
            inside = t[1:t.index(']')]
            parts = [p.strip() for p in inside.split(',')]
            base = REGN[parts[0]]
            if len(parts) == 1: res.append(('mem', base, None, 0, 0))
            elif parts[1].startswith('#'): res.append(('mem', base, None, 0, num(parts[1])))
            else:
                sh = 0
                if len(parts) > 2:
                    m = re.match(r'lsl #(\d+)', parts[2])
                    if not m: raise Unsupported('mem shift ' + t)
                    sh = int(m.group(1))
                res.append(('mem', base, REGN[parts[1]], sh, 0))
            if t.endswith('!'): raise Unsupported('writeback')
        elif t.startswith('#'):
            res.append(('#', num(t)))
        elif re.match(r'^(lsl|lsr|asr|ror) #\d+$', t):
            k, v = t.split(' #')
            if not res or res[-1][0] != 'r': raise Unsupported('shift ' + t)
            res[-1] = ('rs', res[-1][1], k, int(v))
        elif t == 'rrx':
            raise Unsupported('rrx')
        elif t in REGN:
            res.append(('r', REGN[t]))
        else:
            raise Unsupported('operand ' + t)
    return res


_MD = None


def md():
    global _MD
    if _MD is None:
        import capstone
        _MD = capstone.Cs(capstone.CS_ARCH_ARM, capstone.CS_MODE_THUMB | capstone.CS_MODE_MCLASS)
    return _MD


ADR_DATA = set()      # data addresses some function's adr takes (strings placed among the code)


def decode(img, a, n, pw, data=None):
    """[Ins] of the function at a: pool words (absolute addresses in pw) and the padding before
    them skipped; tbb/tbh/word-offset tables as Ins.tab (absolute targets). Data an adr takes the
    address of (a string armcc placed after the code) is skipped up to the next branch target or
    pool word."""
    if data is None:
        first = decode(img, a, n, pw, data=())
        tg = set()
        adrs = []
        for x in first:
            if x.mn in ('b', 'bl', 'cbz', 'cbnz') and x.ops: tg.add(x.ops[-1][1])
            if x.tab: tg |= set(x.tab)
            if x.mn == 'adr':
                t = ((x.addr + 4) & ~3) + x.ops[1][1]
                if a <= t < a + n: adrs.append(t)
            if x.mn == 'ldr' and x.ops and x.ops[-1][0] == 'mem' and x.ops[-1][1] == 15:
                v = img.word(((x.addr + 4) & ~3) + x.ops[-1][4])
                if v is not None and a <= v < a + n and not v & 1: adrs.append(v)
        adrs += [t for t in ADR_DATA if a <= t < a + n]
        rngs = []
        for t in adrs:
            e = min([w for w in pw if t <= w < a + n] + [g for g in tg if g > t] + [a + n])
            rngs.append((t, e))
        if not rngs: return first
        data = rngs
    buf = img.get(a, n)
    out, i, bound, itq = [], 0, {}, []
    while i < len(buf):
        if a + i in pw: i += 4; continue
        dr = next((e for t, e in data if t <= a + i < e), None)
        if dr is not None: i = dr - a; continue
        ins = next(md().disasm(buf[i:i + 4], a + i), None)
        if ins is None: raise Unsupported(f'undecodable at {a + i:08X}')
        mn, op = ins.mnemonic, ins.op_str
        if mn == 'movs' and op == 'r0, r0' and (a + i + 2 in pw or i + 2 >= len(buf)): i += 2; continue
        x = Ins(); x.addr, x.size, x.raw, x.tab = a + i, ins.size, f'{mn} {op}', None
        x.mn, x.cc, x.sf = split_mn(mn)
        if itq:
            x.cc = itq.pop(0)
        if ITS.match(x.mn):
            c = op.strip()
            inv = {'eq': 'ne', 'ne': 'eq', 'cs': 'cc', 'hs': 'lo', 'cc': 'cs', 'lo': 'hs', 'mi': 'pl', 'pl': 'mi',
                   'hi': 'ls', 'ls': 'hi', 'ge': 'lt', 'lt': 'ge', 'gt': 'le', 'le': 'gt', 'vs': 'vc', 'vc': 'vs'}
            itq = [c] + [c if t == 't' else inv[c] for t in x.mn[2:]]
            x.ops = []; out.append(x); i += ins.size; continue
        if x.mn in ('tbb', 'tbh'):
            reg = re.search(r'\[pc, (\w+)', op).group(1)
            cnt = bound.get(reg, 0)
            nb = cnt * (1 if x.mn == 'tbb' else 2); nb += nb & 1
            tab = buf[i + 4:i + 4 + nb]
            if x.mn == 'tbb': x.tab = [a + i + 4 + 2 * b for b in tab[:cnt]]
            else: x.tab = [a + i + 4 + 2 * int.from_bytes(tab[k:k + 2], 'little') for k in range(0, 2 * cnt, 2)]
            x.ops = [('r', REGN[reg])]
            out.append(x); i += 4 + nb; continue
        m2 = re.match(r'(r\d+|sb|sl|fp|ip), (r\d+|sb|sl|fp|ip), #2$', op)
        if mn.startswith('lsl') and m2: bound['_idx_' + m2.group(1)] = m2.group(2)
        if mn == 'add' and re.match(r'pc, r\d+$', op):
            # armcc's word-offset switch: lsls rX, rI, #2; [nop;] add rX, pc; ldr rX, [rX, #4]; add pc, rX
            reg = op.split(', ')[1]
            idx = bound.get('_idx_' + reg, '')
            k = bound.get(idx, 0)
            x.tab = [a + i + 4 + int.from_bytes(buf[i + 4 + j:i + 8 + j], 'little', signed=True) for j in range(0, 4 * k, 4)]
            x.mn = 'tbw'; x.ops = [('r', REGN[idx])] if idx else []
            for y in out[-4:]:
                if y.mn in ('lsl', 'add', 'ldr', 'nop') and re.match(r'(r\d+|ip)\b', y.raw.split(' ', 1)[1]) and \
                        y.raw.split(' ', 1)[1].split(',')[0] == reg or y.mn == 'nop':
                    y.mn = 'nop'; y.ops = []
            out.append(x); i += 4 + 4 * k; continue
        if mn in ('cmp', 'cmp.w'):
            mm = re.match(r'(\w+), #(0x[0-9a-f]+|\d+)$', op)
            if mm: bound[mm.group(1)] = int(mm.group(2), 0) + 1; bound['_last'] = mm.group(1)
        elif mn.startswith(('bhs', 'bcs')) and bound.get('_last') in bound:
            bound[bound['_last']] -= 1; bound['_last'] = None
        if x.mn in ('cpsid', 'cpsie', 'dsb', 'isb', 'dmb', 'wfi', 'wfe', 'sev', 'nop', 'msr', 'mrs', 'svc', 'bkpt'):
            x.ops = [('sys', op)]; out.append(x); i += ins.size; continue
        x.ops = parse_ops(op) if x.mn not in ('b', 'bl', 'cbz', 'cbnz', 'blx') or '#' not in op else None
        if x.mn in ('b', 'bl', 'blx') and '#' in op: x.ops = [('t', num(op.split('#')[-1]))]
        if x.mn in ('cbz', 'cbnz'): x.ops = [('r', REGN[op.split(',')[0]]), ('t', num(op.split('#')[-1]))]
        out.append(x); i += ins.size
    for k, x in enumerate(out): x.k = k
    return out


def literal_targets(img, addr, size):
    """Absolute addresses of the words a function's code loads pc-relative, and of the blocks it
    takes with adr (('adr', address)), up to the first tbb/tbh (the table is data; extra_pool
    finds the loads after it). pc is aligned from the instruction's own address, which matters for
    functions at addresses of the form 4n+2."""
    out, i = set(), 0
    buf = img.get(addr, size)
    while i < len(buf):
        ins = next(md().disasm(buf[i:i + 4], addr + i), None)
        if ins is None: i += 2; continue
        mn, op = ins.mnemonic, ins.op_str
        if (mn.startswith('ldr') and '[pc' in op) or mn.startswith('adr'):
            mm = re.search(r'#(-?0x[0-9a-f]+|-?\d+)\]?$', op)
            t = ((addr + i + 4) & ~3) + (int(mm.group(1), 0) if mm else 0)
            out.add(t if mn.startswith('ldr') else ('adr', t))
        if mn in ('tbb', 'tbh'): break
        i += ins.size
    return out


def pool_values(img, addr, size):
    """Values of the literal words the function's own code loads (without --split_sections the
    pool may lie far after the function)."""
    vals = []
    for t in sorted(x for x in literal_targets(img, addr, size) if isinstance(x, int)):
        v = img.word(t)
        if v is not None: vals.append(v)
    return vals


def param_count(img, addr, size):
    """Parameters an armcc -O0 prologue shows: after the push (and any stack adjustment), armcc
    copies each parameter in order out of r0..r3 (mov rX, r0; mov rY, r1; ...), used or not, in a
    function that calls out; a leaf copies only what it must."""
    buf = img.get(addr, min(size, 32))
    k = 0
    for ins in md().disasm(buf, addr):
        mn, op = ins.mnemonic, ins.op_str
        if k == 0 and (mn.startswith('push') or (mn.startswith('sub') and op.startswith('sp'))): continue
        m = re.match(r'mov(?:\.w)?$', mn) and re.match(r'(r\d+|sb|sl|fp|ip), r(\d)$', op)
        if m and int(m.group(2)) == k: k += 1; continue
        break
    return k


def symbol_for(addr, pool):
    """(base symbol address, offset) through which an access to addr went: a pool word equal to
    it, else the nearest pool word below it within 4 KB in the same 256 MB region, else itself."""
    if addr in pool: return addr, 0
    below = [p for p in pool if p <= addr < p + 0x1000 and (p >> 28) == (addr >> 28)]
    if below:
        b = max(below)
        return b, addr - b
    return addr, 0

# ---------------------------------------------------------------- expressions


def C(v): return ('c', v & 0xffffffff if v >= 0 else v)


def is_c(e, v=None): return e[0] == 'c' and (v is None or e[1] == v)


def sval(v):
    v &= 0xffffffff
    return v - (1 << 32) if v & 0x80000000 else v


def mk(op, x, y):
    """A binary expression, folded and normalised (shift-and-add constant multiplies as *,
    signed power-of-two division idioms as /)."""
    if is_c(x) and is_c(y) and op in ('+', '-', '*', '&', '|', '^', '<<', '>>u', '>>s') and \
            (op not in ('<<', '>>u', '>>s') or 0 <= y[1] < 32):
        a, b = x[1], y[1]
        v = {'+': lambda: a + b, '-': lambda: a - b, '*': lambda: a * b, '&': lambda: a & b, '|': lambda: a | b,
             '^': lambda: a ^ b, '<<': lambda: a << b, '>>u': lambda: (a & 0xffffffff) >> b,
             '>>s': lambda: sval(a) >> b}[op]()
        return C(v & 0xffffffff)
    if op == '+':
        if is_c(y, 0): return x
        if is_c(x, 0): return y
        if is_c(x) and not is_c(y): x, y = y, x
        if is_c(y) and sval(y[1]) < 0 and sval(y[1]) > -0x100000: return mk('-', x, C(-sval(y[1])))
        # x + x*k = x*(k+1)
        if y[0] == 'b' and y[1] == '*' and y[2] == x and is_c(y[3]): return mk('*', x, C(y[3][1] + 1))
        if x[0] == 'b' and x[1] == '*' and x[2] == y and is_c(x[3]): return mk('*', y, C(x[3][1] + 1))
        if y[0] == 'b' and y[1] == '<<' and y[2] == x and is_c(y[3]): return mk('*', x, C((1 << y[3][1]) + 1))
    if op == '-':
        if is_c(y, 0): return x
        if is_c(y) and x[0] == 'sp': return ('sp', x[1] - y[1])
        if y[0] == 'b' and y[1] == '<<' and y[2] == x and is_c(y[3]): return mk('*', x, C(1 - (1 << y[3][1])))
        # x - (x / 2^k) * 2^k = x % 2^k
        if y[0] == 'b' and y[1] == '*' and is_c(y[3]) and y[2][0] == 'b' and y[2][1] == '/s' and y[2][2] == x and y[2][3] == y[3]:
            return ('b', '%s', x, y[3])
    if op == '+' and x[0] == 'sp' and is_c(y): return ('sp', x[1] + y[1])
    if op == '*':
        if is_c(x) and not is_c(y): x, y = y, x
        if x[0] == 'b' and x[1] == '*' and is_c(x[3]) and is_c(y): return mk('*', x[2], C(x[3][1] * y[1]))
        if is_c(y, 1): return x
    if op == '<<' and is_c(y) and x[0] == 'b' and x[1] == '*' and is_c(x[3]):
        return mk('*', x[2], C(x[3][1] << y[1]))
    if op == '>>s' and is_c(y):
        k = y[1]
        # (x + ((x >> 31) >>u (32 - k))) >> k  ==  x / 2^k
        if x[0] == 'b' and x[1] == '+':
            a, b = x[2], x[3]
            if b[0] == 'b' and b[1] == '>>u' and is_c(b[3], 32 - k) and b[2] == ('b', '>>s', a, C(31)):
                return ('b', '/s', a, C(1 << k))
    if op == '<<' and is_c(y) and x[0] == 'b' and x[1] == '/s' and is_c(x[3], 1 << y[1]):
        return ('b', '*', x, C(1 << y[1]))
    return ('b', op, x, y)


NEG = {'==': '!=', '!=': '==', '<': '>=', '>=': '<', '>': '<=', '<=': '>'}


def neg(c):
    if c[0] == 'cmp': return ('cmp', NEG[c[1]], c[2], c[3], c[4])
    if c[0] == 'and': return ('or', neg(c[1]), neg(c[2]))
    if c[0] == 'or': return ('and', neg(c[1]), neg(c[2]))
    if c[0] == 'not': return c[1]
    return ('not', c)


def cond_of(cc, fl):
    """The condition `cc` after the flags fl = (kind, a, b) as a comparison expression."""
    kind, a, b = fl
    if cc in ('al', None): raise Unsupported('cc')
    if kind in ('tst',):
        x = mk('&', a, b)
        if cc == 'eq': return ('cmp', '==', x, C(0), True)
        if cc == 'ne': return ('cmp', '!=', x, C(0), True)
        if cc == 'mi': return ('cmp', '<', x, C(0), True)
        if cc == 'pl': return ('cmp', '>=', x, C(0), True)
        raise Unsupported('tst ' + cc)
    if kind == 'teq':
        x = mk('^', a, b)
        if cc in ('eq', 'ne'): return ('cmp', '==' if cc == 'eq' else '!=', x, C(0), True)
        raise Unsupported('teq ' + cc)
    if kind == 'cmn':
        b = ('u', '-', b) if not is_c(b) else C(-sval(b[1]))
        kind = 'cmp'
    if kind == 'z':          # flags of a result: compared with 0
        if cc == 'eq': return ('cmp', '==', a, C(0), True)
        if cc == 'ne': return ('cmp', '!=', a, C(0), True)
        if cc == 'mi': return ('cmp', '<', a, C(0), True)
        if cc == 'pl': return ('cmp', '>=', a, C(0), True)
        raise Unsupported('z ' + cc)
    m = {'eq': ('==', True), 'ne': ('!=', True), 'lt': ('<', True), 'ge': ('>=', True), 'gt': ('>', True),
         'le': ('<=', True), 'lo': ('<', False), 'cc': ('<', False), 'hs': ('>=', False), 'cs': ('>=', False),
         'hi': ('>', False), 'ls': ('<=', False)}
    if cc in m: return ('cmp', m[cc][0], a, b, m[cc][1])
    if cc in ('mi', 'pl'): return ('cmp', '<' if cc == 'mi' else '>=', mk('-', a, b), C(0), True)
    raise Unsupported('cc ' + cc)


def subst(e, f):
    """e with every leaf rewritten by f (a function of a leaf returning a new expression or None)."""
    if not isinstance(e, tuple): return e
    r = f(e)
    if r is not None: return r
    t = e[0]
    if t in ('b',): return mk(e[1], subst(e[2], f), subst(e[3], f))
    if t in ('u', 'cast', 'ld'): return (t, e[1], subst(e[2], f))
    if t == 'call': return (t, e[1], tuple(subst(x, f) for x in e[2]), e[3])
    if t == 'cmp': return (t, e[1], subst(e[2], f), subst(e[3], f), e[4])
    if t in ('and', 'or'): return (t, subst(e[1], f), subst(e[2], f))
    if t == 'not': return (t, subst(e[1], f))
    if t in ('sel', 'mls'): return (t, subst(e[1], f), subst(e[2], f), subst(e[3], f))
    return e


def walk(e):
    if not isinstance(e, tuple): return
    yield e
    t = e[0]
    kids = {'b': e[2:4], 'u': e[2:3], 'cast': e[2:3], 'ld': e[2:3], 'cmp': e[2:4], 'and': e[1:3], 'or': e[1:3],
            'not': e[1:2], 'sel': e[1:4], 'mls': e[1:4]}.get(t, ())
    if t == 'call': kids = e[2]
    for k in kids: yield from walk(k)


def regs_in(e):
    return {x[1] for x in walk(e) if x[0] == 'r'}


def pure(e):
    return not any(x[0] in ('ld', 'call', 'post', 'asgx', 'multi') for x in walk(e))

# ---------------------------------------------------------------- the image and the map


def load_structs(path):
    """({struct: (size, [(offset, size, field)])}, {function: (argument index, struct)}) from a
    structs file (the module docstring), or empty tables."""
    if not path or not os.path.exists(path): return {}, {}
    with open(path) as f: j = json.load(f)
    structs = {k: (v[0], [tuple(x) for x in v[1]]) for k, v in j.get('structs', {}).items()}
    args = {k: tuple(v) for k, v in j.get('args', {}).items()}
    return structs, args


def pool_rows(img, rows):
    """Map rows that are nothing but literal words other functions load: a pool the map took for a
    function. They are not translated."""
    words = set()
    for a, n, _ in rows:
        words |= {x for x in literal_targets(img, a, n) if isinstance(x, int)}
    return {a for a, n, _ in rows if n % 4 == 0 and a % 4 == 0 and all(a + o in words for o in range(0, n, 4))}


class Ctx:
    """Facts about the whole image used while translating one function."""

    def __init__(self, img, rows, lib_rows=(), ram=None, structs=None, args=None):
        """img: Mem; rows [(addr, size, name)] of the map's functions; lib_rows: addresses not ours
        to translate (library code)."""
        ADR_DATA.clear()
        self.img = img
        self.rows = rows
        self.ram = list(ram or DEFAULT_RAM)
        self.structs, self.struct_args = structs or {}, args or {}
        self.name = {a: nm for a, n, nm in rows}
        self.by_name = {nm: a for a, n, nm in rows}
        self.size = {a: n for a, n, nm in rows}
        self.lib = set(lib_rows) | pool_rows(img, rows)
        self.pw = {x for a, n, _ in rows for x in literal_targets(img, a, n) if isinstance(x, int)}
        for a, n, nm in rows:
            try:
                for x in decode(img, a, n, self.pw, data=()):
                    if x.mn == 'adr': ADR_DATA.add(((x.addr + 4) & ~3) + x.ops[1][1])
                    if x.mn in ('add', 'sub', 'addw', 'subw') and x.ops and len(x.ops) == 3 and x.ops[1] == ('r', 15) \
                            and x.ops[2][0] == '#':
                        ADR_DATA.add(((x.addr + 4) & ~3) + (x.ops[2][1] if x.mn.startswith('add') else -x.ops[2][1]))
                    if x.mn == 'ldr' and x.ops and x.ops[-1][0] == 'mem' and x.ops[-1][1] == 15:
                        v = img.word(((x.addr + 4) & ~3) + x.ops[-1][4])
                        if v is not None and img.base <= v < img.end and not v & 1 and v not in self.name:
                            ADR_DATA.add(v)
            except Exception:
                pass
        self.pw |= extra_pool(img, rows, self.pw, self.lib)
        self.nparams = {}
        self.retval = {}
        self.clobbers = {}         # function -> the call-clobbered registers (r1-r3, ip) it really writes

    @classmethod
    def from_config(cls, cfg):
        o = cfg.raw.get('o0c', {})
        im = ehex.Image(cfg.image)
        rows = armmap.load(os.path.join(cfg.map, 'functions.tsv'))
        sp = o.get('structs')
        structs, args = load_structs(os.path.join(cfg.root, sp) if sp else None)
        ram = [tuple(r) for r in o['ram']] if o.get('ram') else None
        return cls(Mem(im.base, im.data), [(r.addr, r.size, r.name) for r in rows],
                   {r.addr for r in rows if r.kind == 'lib'}, ram, structs, args)

    def fname(self, a):
        return self.name.get(a, f'sub_{a:08X}')

    def in_ram(self, v):
        return any(lo <= v < hi for lo, hi in self.ram)

    def addr(self, s):
        """An address from hex text or a map name."""
        if s in self.by_name: return self.by_name[s]
        try:
            return int(s, 16)
        except ValueError:
            raise SystemExit(f'{s}: not an address or a map name')


def extra_pool(img, rows, pw, lib=()):
    """Literal words loaded by code that literal_targets does not reach (it stops at a tbb/tbh
    table): found by decoding every function with the words known so far, until no new word
    turns up."""
    pw = set(pw); found = set()
    for _ in range(4):
        new = set()
        for a, n, nm in rows:
            if a in lib: continue
            try: ins = decode(img, a, n, pw)
            except Exception: continue
            for x in ins:
                if x.mn == 'ldr' and x.ops and x.ops[-1][0] == 'mem' and x.ops[-1][1] == 15:
                    t = ((x.addr + 4) & ~3) + x.ops[-1][4]
                    if t not in pw: new.add(t)
        if not new: break
        pw |= new; found |= new
    return found


def literal(ctx, v):
    if ctx.in_ram(v): return ('g', v)
    if ctx.img.base <= v < ctx.img.end:
        if v & 1 and (v - 1) in ctx.name: return ('f', v - 1)
        return ('g', v)
    if 0x40000000 <= v < 0x60000000 or v >= 0xE0000000:
        return ('per', v)          # a peripheral base: armcc keeps base + offset (PERIPH->REG)
    return C(v)


def op2(o):
    if o[0] == '#': return C(o[1])
    if o[0] == 'r': return ('r', o[1])
    if o[0] == 'rs':
        r = ('r', o[1])
        return {'lsl': lambda: mk('<<', r, C(o[3])), 'lsr': lambda: mk('>>u', r, C(o[3])),
                'asr': lambda: mk('>>s', r, C(o[3])), 'ror': lambda: ('b', 'ror', r, C(o[3]))}[o[2]]()
    raise Unsupported(f'op2 {o}')


def memaddr(o):
    _, base, idx, sh, disp = o
    b = ('sp', 0) if base == 13 else ('r', base)
    if idx is not None: b = mk('+', b, mk('*', ('r', idx), C(1 << sh)) if sh else ('r', idx))
    return mk('+', b, C(disp)) if disp else b

# ---------------------------------------------------------------- lifting


def lift(ctx, x, f):
    """Micro-ops of one instruction."""
    mn, o = x.mn, x.ops
    R = lambda k: ('r', o[k][1])
    def dst(): return o[0][1]
    if x.cc and mn not in ('b',):
        if mn in ('mov',) and o[0][0] == 'r':
            return [('pset', x.cc, dst(), op2(o[1]))]
        raise Unsupported(f'predicated {x.raw}')
    if ITS.match(mn): return [('nop',)]
    if mn == 'nop' or mn == 'hint': return [('nop',)]
    if mn in ('mov', 'mvn'):
        if o[0][1] == 13 or (o[1][0] == 'r' and o[1][1] == 13 and mn == 'mov'):
            if o[1][0] == 'r' and o[1][1] == 13: return [('set', dst(), ('sp', 0))]
            raise Unsupported('sp write')
        v = op2(o[1])
        if mn == 'mvn': v = C(~v[1]) if is_c(v) else ('u', '~', v)
        r = [('set', dst(), v)]
        if x.sf: r.append(('flz', dst()))
        return r
    if mn == 'movw': return [('set', dst(), C(o[1][1]))]
    if mn == 'movt': return [('set', dst(), mk('|', mk('&', ('r', dst()), C(0xffff)), C(o[1][1] << 16)))]
    if mn in ('add', 'sub', 'addw', 'subw', 'adc', 'sbc', 'rsb', 'and', 'orr', 'eor', 'bic', 'orn', 'mul',
              'lsl', 'lsr', 'asr', 'ror', 'sdiv', 'udiv', 'neg'):
        base = mn.rstrip('w') if mn in ('addw', 'subw') else mn
        if base in ('adc', 'sbc'): raise Unsupported(mn)
        if base in ('add', 'sub') and len(o) == 3 and o[1] == ('r', 15) and o[2][0] == '#':
            # adr written as add/sub from pc
            t = ((x.addr + 4) & ~3) + (o[2][1] if base == 'add' else -o[2][1])
            y = Ins(); y.addr, y.raw, y.mn, y.ops = x.addr, x.raw, 'adr', [o[0], ('#', t - ((x.addr + 4) & ~3))]
            y.cc, y.sf, y.tab, y.size = None, False, None, x.size
            return lift(ctx, y, f)
        if base == 'neg': a, b = C(0), ('r', o[1][1]); base = 'sub'
        elif len(o) == 2: a, b = ('r', dst()), op2(o[1])
        else:
            a, b = (('sp', 0) if o[1][0] == 'r' and o[1][1] == 13 else op2(o[1])), op2(o[2])
            # `add rd, rn, rd, lsl #k`: armcc gives the shifted operand the result register when the
            # source multiplied (x * 256); x << 8 gets the next register
            if base in ('add', 'sub', 'rsb') and o[2][0] == 'rs' and o[2][2] == 'lsl' and o[2][1] == o[0][1] and o[1][1] != o[0][1]:
                b = mk('*', ('r', o[2][1]), C(1 << o[2][3]))
        if dst() == 13:
            return [('frame', base, b)]
        if base == 'add' and o[1][0] == 'r' and o[1][1] == 15: raise Unsupported('add pc')
        v = {'add': lambda: mk('+', a, b), 'sub': lambda: mk('-', a, b), 'rsb': lambda: mk('-', b, a),
             'and': lambda: mk('&', a, b), 'orr': lambda: mk('|', a, b), 'eor': lambda: mk('^', a, b),
             'bic': lambda: ('b', '&', a, ('u', '~', b)),     # x & ~K: armcc's bic (x & -5 loads -5)
             'orn': lambda: mk('|', a, C(~b[1]) if is_c(b) else ('u', '~', b)),
             'mul': lambda: mk('*', a, b), 'lsl': lambda: mk('<<', a, b), 'lsr': lambda: mk('>>u', a, b),
             'asr': lambda: mk('>>s', a, b), 'ror': lambda: ('b', 'ror', a, b),
             'sdiv': lambda: ('b', '/s', a, b), 'udiv': lambda: ('b', '/u', a, b)}[base]()
        r = [('set', dst(), v)]
        if x.sf: r.append(('flz', dst()))
        return r
    if mn in ('mla', 'mls'):
        if mn == 'mla': v = mk('+', mk('*', op2(o[1]), op2(o[2])), op2(o[3]))
        else: v = ('mls', op2(o[3]), op2(o[1]), op2(o[2]))     # ra - rn * rm: the remainder idiom
        return [('set', dst(), v)]
    if mn in ('uxtb', 'uxth', 'sxtb', 'sxth'):
        if len(o) > 2: raise Unsupported('extend rotate')
        t = {'uxtb': 'uchar', 'uxth': 'ushort', 'sxtb': 'sbyte', 'sxth': 'short'}[mn]
        return [('set', dst(), ('cast', t, op2(o[1])))]
    if mn in ('ubfx', 'sbfx'):
        lsb, w = o[2][1], o[3][1]
        if mn == 'ubfx': v = mk('&', mk('>>u', op2(o[1]), C(lsb)), C((1 << w) - 1)) if lsb else mk('&', op2(o[1]), C((1 << w) - 1))
        else: v = mk('>>s', mk('<<', op2(o[1]), C(32 - lsb - w)), C(32 - w))
        return [('set', dst(), v)]
    if mn == 'bfi':
        lsb, w = o[2][1], o[3][1]; m = (1 << w) - 1
        if lsb + w == 32:
            v = mk('|', mk('&', ('r', dst()), C((1 << lsb) - 1)), mk('<<', op2(o[1]), C(lsb)))
        else:
            v = mk('|', mk('&', ('r', dst()), C(~(m << lsb) & 0xffffffff)), mk('<<', mk('&', op2(o[1]), C(m)), C(lsb)))
        return [('set', dst(), v)]
    if mn == 'bfc':
        lsb, w = o[1][1], o[2][1]; m = (1 << w) - 1
        return [('set', dst(), mk('&', ('r', dst()), C(~(m << lsb) & 0xffffffff)))]
    if mn == 'clz': return [('set', dst(), ('call', '__clz', (op2(o[1]),), 'int'))]
    if mn in ('cmp', 'cmn', 'tst', 'teq'):
        return [('fl', mn, op2(o[0]), op2(o[1]))]
    if mn in LDT or mn == 'ldrd':
        m = o[-1]
        if m[0] != 'mem': raise Unsupported('ldr form')
        if m[1] == 15:
            t = ((x.addr + 4) & ~3) + m[4]
            v = ctx.img.word(t)
            if mn != 'ldr' or v is None: raise Unsupported('pc load')
            return [('set', dst(), literal(ctx, v))]
        ad = memaddr(m)
        if mn == 'ldrd':
            return [('set', o[0][1], ('ld', 'int', ad)), ('set', o[1][1], ('ld', 'int', mk('+', ad, C(4))))]
        return [('set', dst(), ('ld', LDT[mn], ad))]
    if mn in STT or mn == 'strd':
        m = o[-1]
        if m[0] != 'mem' or m[1] == 15: raise Unsupported('str form')
        ad = memaddr(m)
        if mn == 'strd':
            return [('st', 'int', ad, R(0)), ('st', 'int', mk('+', ad, C(4)), R(1))]
        return [('st', STT[mn], ad, R(0))]
    if mn == 'adr':
        t = ((x.addr + 4) & ~3) + o[1][1]
        # a string literal armcc placed after the function's code
        raw = ctx.img.get(t, 256)
        z = raw.find(b'\0')
        if z > 0 and all(32 <= c < 127 or c in (9, 10, 13) for c in raw[:z]):
            return [('set', dst(), ('str', raw[:z].decode('latin1'), t))]
        return [('set', dst(), ('g', t))]
    if mn == 'push': return [('push', o[0][1])]
    if mn == 'pop': return [('pop', o[0][1])]
    if mn == 'b':
        if x.cc: return [('bc', x.cc, o[0][1])]
        return [('b', o[0][1])]
    if mn in ('cbz', 'cbnz'): return [('cbz', o[0][1], mn == 'cbnz', o[1][1])]
    if mn == 'bl': return [('call', o[0][1])]
    if mn == 'blx':
        if o[0][0] == 'r': return [('icall', o[0][1])]
        raise Unsupported('blx imm')
    if mn == 'bx':
        if o[0] == ('r', 14): return [('ret',)]
        raise Unsupported('bx reg')
    if mn in ('tbb', 'tbh', 'tbw'): return [('sw', o[0][1] if o else None, x.tab)]
    if mn in ('cpsid', 'cpsie') and o and o[0][1] == 'f':
        return [('intr', '__disable_fiq' if mn == 'cpsid' else '__enable_fiq', ())]
    if mn in INTRINSIC:
        nm = INTRINSIC[mn]
        args = (C(0xf),) if mn in ('dsb', 'isb', 'dmb') else ()
        return [('intr', nm, args)]
    raise Unsupported(f'instruction {x.raw}')

# ---------------------------------------------------------------- per-function analysis


class Block:
    def __init__(self, k, lo, hi):
        self.k, self.lo, self.hi = k, lo, hi      # instruction index range [lo, hi)
        self.succ, self.pred = [], []
        self.stmts, self.term, self.outs = [], None, {}
        self.ret = False

    def term_kind(self):
        return self.tk


class Func:
    pass


def analyse(ctx, a):
    """Decode, lift, blocks, prologue/epilogue and the parameter copies of the function at a (no
    names yet): the first pass over every function."""
    n = ctx.size[a]
    F = Func(); F.addr, F.size, F.name = a, n, ctx.fname(a)
    ins = decode(ctx.img, a, n, ctx.pw)
    F.ins = ins
    F.ops = [lift(ctx, x, F) for x in ins]
    # prologue: push, sub sp; epilogue: add sp, pop / bx lr
    F.push = []
    F.frame = 0
    k = 0
    if k < len(ins) and F.ops[k][0][0] == 'push':
        F.push = F.ops[k][0][1]; F.ops[k] = [('pro',)]; k += 1
    if k < len(ins) and F.ops[k][0][0] == 'frame' and F.ops[k][0][1] == 'sub' and is_c(F.ops[k][0][2]):
        F.frame = F.ops[k][0][2][1]; F.ops[k] = [('pro',)]; k += 1
    F.body0 = k
    F.lowpush = [r for r in F.push if r < 4]
    F.saved = [r for r in F.push if 4 <= r < 13]
    for i, x in enumerate(ins):
        o = F.ops[i][0]
        if o[0] == 'pop':
            if 15 not in o[1]: raise Unsupported('pop without pc')
            F.ops[i] = [('ret',)]
            o = ('ret',)
        if o[0] == 'ret' and i and F.ops[i - 1][0][0] == 'frame' and F.ops[i - 1][0][1] == 'add':
            F.ops[i - 1] = [('epi',)]
    for i, x in enumerate(ins):
        o = F.ops[i][0]
        if o[0] in ('push', 'pop', 'frame'):
            raise Unsupported(f'stack op in body {x.raw}')
    F.calls = any(o[0][0] in ('call', 'icall') for o in F.ops)
    # a branch over an intermediate literal pool to the next instruction: placement, not source
    for i, ops in enumerate(F.ops):
        if ops[-1][0] == 'b' and i + 1 < len(ins) and ops[-1][1] == ins[i + 1].addr and \
                ins[i + 1].addr != ins[i].addr + ins[i].size:
            F.ops[i] = [('nop',)]
    # branch islands: a conditional branch whose target is out of its range goes to a lone `b X`
    # armcc places in reach (after an unconditional branch, so nothing falls into it): to X
    at0 = {x.addr: x.k for x in ins}
    gotos = {ops[-1][1] for ops in F.ops if ops[-1][0] == 'b'}
    def island(t):
        for _ in range(4):
            j = at0.get(t)
            if j is None or j == 0 or F.ops[j][-1][0] != 'b' or len(F.ops[j]) != 1 or t in gotos: return t
            if F.ops[j - 1][-1][0] not in ('b', 'ret', 'sw'): return t
            t = F.ops[j][-1][1]
        return t
    for i, ops in enumerate(F.ops):
        o = ops[-1]
        if o[0] == 'bc': F.ops[i] = ops[:-1] + [('bc', o[1], island(o[2]))]
        elif o[0] == 'cbz': F.ops[i] = ops[:-1] + [('cbz', o[1], o[2], island(o[3]))]
    # a conditional branch to a conditional branch on the same flags (armcc threads them): to its
    # final target
    INV = {'eq': 'ne', 'ne': 'eq', 'lt': 'ge', 'ge': 'lt', 'gt': 'le', 'le': 'gt', 'lo': 'hs', 'hs': 'lo',
           'hi': 'ls', 'ls': 'hi', 'mi': 'pl', 'pl': 'mi'}
    at = {x.addr: x.k for x in ins}
    for i, ops in enumerate(F.ops):
        o = ops[-1]
        if o[0] != 'bc': continue
        for _ in range(4):
            j = at.get(o[2])
            if j is None or F.ops[j][-1][0] != 'bc': break
            o2 = F.ops[j][-1]
            if o2[1] == o[1]: o = ('bc', o[1], o2[2])
            elif INV.get(o2[1]) == o[1] and j + 1 < len(ins): o = ('bc', o[1], ins[j + 1].addr)
            else: break
        F.ops[i] = ops[:-1] + [o]
    build_blocks(F)
    if getattr(ctx, 'clone', False) and unmerge_tails(F): build_blocks(F); F.cloned = True
    # the prologue's parameter copies: mov rX, rK out of r0-r3, ldr rX, [sp, #n] of stack parameters
    F.argbase = F.frame + 4 * len(F.push)
    F.pcopy = {}
    written = set()
    k = F.body0
    while k < len(ins) and F.ops[k][-1][0] == 'set' and F.bof.get(k) == 0:
        o = F.ops[k][-1]
        if ins[k].mn == 'mov' and o[2][0] == 'r' and o[2][1] < 4 and o[2][1] not in F.pcopy.values() and \
                o[2][1] not in written:
            F.pcopy[k] = o[2][1]
        elif o[2][0] == 'ld' and o[2][1] == 'int' and o[2][2][0] == 'sp' and o[2][2][1] >= F.argbase:
            F.pcopy[k] = 4 + (o[2][2][1] - F.argbase) // 4
        else: break
        written.add(o[1])
        k += 1
    return F


def build_blocks(F):
    ins = F.ins
    starts = {0}
    addr2k = {x.addr: x.k for x in ins}
    F.addr2k = addr2k
    def tk(t):
        if t not in addr2k: raise Unsupported(f'branch into the unknown {t:08X}')
        return addr2k[t]
    for i, ops in enumerate(F.ops):
        o = ops[-1]
        if o[0] in ('b', 'bc', 'cbz'):
            starts.add(tk(o[-1])); starts.add(i + 1)
        elif o[0] == 'sw':
            for t in o[2]: starts.add(tk(t))
            starts.add(i + 1)
        elif o[0] == 'ret':
            starts.add(i + 1)
            j = i
            if j and F.ops[j - 1][0][0] == 'epi': j -= 1
            starts.add(j)
    starts = sorted(s for s in starts if s < len(ins))
    F.blocks = []
    for bi, s in enumerate(starts):
        e = starts[bi + 1] if bi + 1 < len(starts) else len(ins)
        F.blocks.append(Block(bi, s, e))
    F.bof = {}
    for b in F.blocks:
        for i in range(b.lo, b.hi): F.bof[i] = b.k
    for b in F.blocks:
        o = F.ops[b.hi - 1][-1]
        nxt = b.k + 1 if b.k + 1 < len(F.blocks) else None
        b.tk = o[0] if o[0] in ('b', 'bc', 'cbz', 'sw', 'ret') else 'fall'
        if o[0] == 'b': b.succ = [F.bof[tk(o[1])]]
        elif o[0] in ('bc', 'cbz'): b.succ = [F.bof[tk(o[-1])]] + ([nxt] if nxt is not None else [])
        elif o[0] == 'sw': b.succ = sorted({F.bof[tk(t)] for t in o[2]})
        elif o[0] == 'ret': b.succ = []; b.ret = True
        else: b.succ = [nxt] if nxt is not None else []
        b.epi = all(F.ops[i][0][0] in ('epi', 'ret', 'nop') for i in range(b.lo, b.hi)) and b.ret
    for b in F.blocks:
        for s in b.succ: F.blocks[s].pred.append(b.k)


def ins_reads(F, i):
    u, d = set(), set()
    for o in F.ops[i]:
        t = o[0]
        if t == 'set': u |= regs_in(o[2]) - d; d.add(o[1])
        elif t == 'pset': u |= (regs_in(o[3]) | {o[2]}) - d; d.add(o[2])
        elif t in ('fl', 'st'): u |= (regs_in(o[2]) | regs_in(o[3])) - d
        elif t == 'cbz': u.add(o[1])
        elif t == 'sw' and o[1] is not None: u.add(o[1])
        elif t in ('call', 'icall'): d |= {0, 1, 2, 3, 12}
    return u, d


def _copy_ins(src, addr, raw=None, mn=None):
    y = Ins()
    y.addr, y.size, y.raw, y.tab = addr, 0, raw if raw is not None else src.raw, None if src is None else src.tab
    y.mn, y.cc, y.sf, y.ops = (mn, None, False, None) if src is None else (src.mn, src.cc, src.sf, src.ops)
    return y


def unmerge_tails(F):
    """armcc merges identical statement tails (`ldr r1, =g; strb r0, [r1]` ending two paths that
    store to g) into one block reached by `b`: copy such a block back into each predecessor that
    branches to it, so every statement is whole again."""
    B = F.blocks
    clone = {}
    for T in B:
        if len(T.pred) < 2 or T.epi or T.hi - T.lo > 10: continue
        if any(p >= T.k for p in T.pred) or any(q <= T.k for q in T.succ): continue      # loops
        live, d = set(), set()
        for i in range(T.lo, T.hi):
            u, dd = ins_reads(F, i)
            live |= {r for r in u - d if r < 4 or r == 12}
            d |= dd
        if not live: continue
        gp = [p for p in T.pred if F.ops[B[p].hi - 1][-1][0] == 'b' and B[p].succ == [T.k]]
        if not gp: continue
        def side(p):
            return any(o[0] in ('st', 'call', 'icall') for i in range(B[p].lo, B[p].hi) for o in F.ops[i])
        if not any(side(p) for p in T.pred): continue
        for p in gp: clone[B[p].hi - 1] = T
    if not clone: return False
    ins, ops = [], []
    n = 0
    for i, x in enumerate(F.ins):
        if i in clone:
            T = clone[i]
            for j in range(T.lo, T.hi):
                ins.append(_copy_ins(F.ins[j], 0x70000000 + n * 4)); n += 1
                ops.append(list(F.ops[j]))
            last = F.ops[T.hi - 1][-1][0]
            if last in ('bc', 'cbz') or last not in ('b', 'sw', 'ret'):
                if T.hi < len(F.ins):
                    ins.append(_copy_ins(None, 0x70000000 + n * 4, 'b (copy)', 'b')); n += 1
                    ops.append([('b', F.ins[T.hi].addr)])
            continue
        ins.append(x); ops.append(F.ops[i])
    for k, x in enumerate(ins): x.k = k
    F.ins, F.ops = ins, ops
    return True


def ins_uses_defs(F, i, ctx):
    """(uses, defs, clobbers) register sets of instruction i."""
    uses, defs, clob = set(), set(), set()
    for o in F.ops[i]:
        t = o[0]
        if t == 'set':
            uses |= regs_in(o[2]); defs.add(o[1])
        elif t == 'pset':
            uses |= regs_in(o[3]) | {o[2]}; defs.add(o[2])
        elif t == 'fl': uses |= regs_in(o[2]) | regs_in(o[3])
        elif t == 'st': uses |= regs_in(o[2]) | regs_in(o[3])
        elif t == 'cbz': uses.add(o[1])
        elif t == 'sw' and o[1] is not None: uses.add(o[1])
        elif t in ('call', 'icall'):
            n = F.callargs.get(i, 0)
            uses |= set(range(n))
            if t == 'icall': uses.add(o[1])
            defs.add(0); clob |= call_clobbers(ctx, o)
    return uses, defs, clob


SCRATCH = frozenset({1, 2, 3, 12})


def call_clobbers(ctx, o):
    """The registers besides r0 a call op leaves undefined: those of r1-r3 and ip the callee (or
    anything it calls) writes, when the image shows them, else all four. armcc keeps values in
    r1-r3 across a call to a function whose registers it knows (compiler.md), and a caller that
    reads one after the call proves the callee left it alone."""
    if o[0] == 'call': return ctx.clobbers.get(o[1], SCRATCH)
    return SCRATCH


def site_args(F, i):
    """Registers r0-r3 written in the block before the call at i and not read again before it."""
    b = F.blocks[F.bof[i]]
    pending = set()
    for j in range(b.lo, i):
        for o in F.ops[j]:
            if o[0] in ('call', 'icall'): pending = set()
        u, d = set(), set()
        for o in F.ops[j]:
            if o[0] == 'set': u |= regs_in(o[2]); d.add(o[1])
            elif o[0] in ('st', 'fl'): u |= regs_in(o[2]) | regs_in(o[3])
            elif o[0] == 'pset': u |= regs_in(o[3]); d.add(o[2])
        pending -= u
        pending |= {r for r in d if r < 4}
    n = 0
    tgt = F.ops[i][0][1] if F.ops[i][0][0] == 'icall' else None
    while n in pending and n != tgt: n += 1
    return n


def dataflow(F, ctx):
    """Webs: union of the defs that reach a common use."""
    nb = len(F.blocks)
    F.callargs = {}
    F.stackarg, F.callextra = {}, {}
    for i, ops in enumerate(F.ops):
        if ops[0][0] in ('call', 'icall'):
            tgt = ops[0][1] if ops[0][0] == 'call' else None
            n = max(ctx.nparams.get(tgt, 0) if tgt is not None else 0, site_args(F, i))
            F.callargs[i] = min(n, 4)
            # arguments past the fourth: stores to [sp], [sp, #4]... before the call in its block
            if n > 4:
                b = F.blocks[F.bof[i]]
                want = {4 * (k - 4): k for k in range(4, n)}
                for j in range(i - 1, b.lo - 1, -1):
                    if F.ops[j][0][0] in ('call', 'icall'): break
                    for o in F.ops[j]:
                        if o[0] == 'st' and o[2][0] == 'sp' and o[2][1] in want and o[1] == 'int':
                            F.stackarg[(j, o[2][1])] = (i, want.pop(o[2][1]))
                F.callextra[i] = n
    # return uses: r0 at the end of every block that branches or falls into an epilogue
    F.retblocks = set()
    if F.retval:
        for b in F.blocks:
            if b.epi: continue
            if any(F.blocks[s].epi for s in b.succ) and b.term_kind() in ('b', 'fall', 'bc', 'cbz'):
                F.retblocks.add(b.k)
    UD = [ins_uses_defs(F, i, ctx) for i in range(len(F.ins))]
    F.UD = UD
    # reaching definitions
    gen = [dict() for _ in range(nb)]
    for b in F.blocks:
        g = {}
        for i in range(b.lo, b.hi):
            u, d, c = UD[i]
            for r in c: g[r] = frozenset({('clob', i, r)})
            for r in d: g[r] = frozenset({(i, r)})
        gen[b.k] = g
    IN = [dict() for _ in range(nb)]
    OUT = [dict() for _ in range(nb)]
    entry = {r: frozenset({('entry', r)}) for r in range(13)}
    changed = True
    while changed:
        changed = False
        for b in F.blocks:
            inn = dict(entry) if b.k == 0 else {}
            for p in b.pred:
                for r, s in OUT[p].items(): inn[r] = inn.get(r, frozenset()) | s
            out = dict(inn); out.update(gen[b.k])
            if out != OUT[b.k] or inn != IN[b.k]:
                IN[b.k], OUT[b.k] = inn, out; changed = True
    # uses -> defs, union-find
    parent = {}
    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent.setdefault(parent[x], parent[x]); x = parent[x]
        return x
    def union(x, y): parent[find(x)] = find(y)
    F.usedefs = {}
    F.defuses = collections.defaultdict(list)
    for b in F.blocks:
        cur = dict(IN[b.k])
        for i in range(b.lo, b.hi):
            u, d, c = UD[i]
            for r in u:
                ds = cur.get(r, frozenset())
                if not ds: raise Unsupported(f'register r{r} read with no definition at {F.ins[i].addr:08X}')
                if any(x[0] == 'clob' for x in ds):
                    raise Unsupported(f'r{r} read after a call clobbered it at {F.ins[i].addr:08X}')
                F.usedefs[(i, r)] = ds
                ds = list(ds)
                for x in ds:
                    F.defuses[x].append((i, r))
                    find(x)
                for x in ds[1:]: union(ds[0], x)
            for r in c: cur[r] = frozenset({('clob', i, r)})
            for r in d: cur[r] = frozenset({(i, r)})
        if b.k in F.retblocks:
            ds = cur.get(0, frozenset())
            if not ds or any(x[0] == 'clob' for x in ds): raise Unsupported('return value undefined')
            F.usedefs[(('end', b.k), 0)] = ds
            ds = list(ds)
            for x in ds: F.defuses[x].append((('end', b.k), 0)); find(x)
            for x in ds[1:]: union(ds[0], x)
    webs = collections.defaultdict(set)
    for (i, r), ds in F.usedefs.items():
        for x in ds: webs[find(x)].add(x)
    # defs with no use: their own webs
    for i in range(len(F.ins)):
        for r in UD[i][1]:
            x = (i, r)
            if find(x) not in webs: webs[find(x)].add(x)
    F.web = {}
    F.webs = {}
    for root, ds in webs.items():
        F.webs[root] = ds
        for x in ds: F.web[x] = root
    F.IN = IN
    # a value in r1-r3 or ip that lives across a call (the callee is known to leave it alone):
    # the function keeps locals in low registers, and r4 and up serve as scratch
    calls = [i for i, ops in enumerate(F.ops) if ops[0][0] in ('call', 'icall')]
    F.keeps_low = any(r in SCRATCH and isinstance(d[0], int) and any(min(d[0], u) < c < max(d[0], u) for c in calls)
                      for (u, r), ds in F.usedefs.items() if isinstance(u, int) for d in ds)


def _blk(F, u):
    """The block of a use: an instruction index, or ('end', block) for a return value."""
    return F.bof[u[0]] if not isinstance(u[0], tuple) else u[0][1]


def classify(F, ctx):
    """Kind of each web: param, temp, const, select, var or dead."""
    F.kind, F.wname = {}, {}
    F.params = {}
    F.nparams = ctx.nparams.get(F.addr, 0)
    for w, ds in F.webs.items():
        uses = [u for d in ds for u in F.defuses.get(d, [])]
        ent = [d for d in ds if d[0] == 'entry']
        if ent:
            r = ent[0][1]
            if r < 4 and r in F.pcopy.values() and any(d[0] != 'entry' for d in ds):
                F.kind[w] = ('var', r)             # the register reused for a local after the copy
            elif r < 4: F.kind[w] = ('param', r)
            else: F.kind[w] = ('var', r)          # read uninitialised on some path
            continue
        # a literal address kept in a register across blocks (armcc reuses a loaded pool word):
        # the constant at every use
        lits = set()
        for d in ds:
            if not isinstance(d[0], int): lits.add(None); continue
            o = [o for o in F.ops[d[0]] if o[0] == 'set' and o[1] == d[1]]
            lits.add(o[0][2] if o and o[0][2][0] in ('g', 'f', 'per') else None)
        regw = next(iter(ds))[1]
        # not when it is loaded before a loop and used inside it (a pointer local: p = buf; p[i] = 0)
        ublk = {_blk(F, u) for d in ds for u in F.defuses.get(d, [])}
        dblk = {F.bof[d[0]] for d in ds if isinstance(d[0], int)}
        loops = [(s2, b2.k) for b2 in F.blocks for s2 in b2.succ if s2 <= b2.k]
        inloop = any(h <= u <= j and not any(h <= d <= j for d in dblk) for h, j in loops for u in ublk)
        if len(lits) == 1 and None not in lits and (regw < 4 or regw == 12) and not inloop and \
                len(ublk | {F.bof[d[0]] for d in ds}) > 1:
            F.kind[w] = ('const', next(iter(lits))); continue
        pc = [F.pcopy[d[0]] for d in ds if d[0] in F.pcopy and d[1] >= 0]
        if pc:
            F.kind[w] = ('param', pc[0]); continue
        if len(ds) == 1:
            d = next(iter(ds)); i, r = d
            b = F.bof[i]
            same = all((F.bof[u[0]] == b and u[0] > i) if not isinstance(u[0], tuple) else u[0][1] == b for u in uses)
            op = next(o for o in F.ops[i] if o[0] in ('set', 'pset', 'call', 'icall'))
            if same and not uses:
                F.kind[w] = ('dead',); continue
            if same and r < 4 or same and r == 12:
                # a value kept across a statement is a local (int st = 0; ...; return st;), unless it
                # is a literal address armcc reuses or the statement between stores this value
                last = max((u[0] if not isinstance(u[0], tuple) else F.blocks[u[0][1]].hi) for u in uses)
                lit = op[0] == 'set' and op[2][0] in ('g', 'f', 'sp', 'per')
                if not lit and op[0] == 'set' and any(
                        any((o[0] == 'st' and o[3] != ('r', r) and not (o[2][0] == 'sp' and (j, o[2][1]) in F.stackarg))
                            or o[0] in ('call', 'icall') for o in F.ops[j])
                        for j in range(i + 1, last)) and is_c(op[2]) and F.ins[i].mn in ('mov', 'movw', 'mvn') and \
                        not all(not isinstance(u[0], tuple) and any(o[0] == 'st' and o[3] == ('r', r) for o in F.ops[u[0]])
                                for u in uses):
                    F.kind[w] = ('var', r); continue
                F.kind[w] = ('temp',); continue
            if same and r >= 4 and not F.calls and op[0] == 'set' and is_c(op[2]):
                F.kind[w] = ('temp',); continue
            if same and r >= 4 and len(uses) == 1:
                ui = uses[0][0]
                # read-modify-write of the same register (adds r4, r4, #1; uxtb r4, r4), or in a
                # leaf function any computed value used once in its block (armcc takes r4 and up
                # as scratch when r0-r3 run out)
                narrowing = F.ins[ui].mn in ('uxtb', 'uxth', 'sxtb', 'sxth')
                if ((r in F.UD[ui][1] and narrowing) or not F.calls or F.keeps_low) and \
                        not (op[0] == 'set' and op[2][0] in ('r', 'v')) and op[0] != 'call':
                    F.kind[w] = ('temp',); continue
            F.kind[w] = ('var', r); continue
        # several defs
        r = next(iter(ds))[1]
        ublocks = {_blk(F, u) for u in uses}
        dblocks = {F.bof[d[0]] for d in ds}
        if r < 4 and len(ublocks) == 1 and not (ublocks & dblocks) and not getattr(ctx, 'selvar', False):
            F.kind[w] = ('select', r); continue
        F.kind[w] = ('var', r)
    # a leaf returning a value whose result local lives in r0 (int st; ...; st = 0; return st;):
    # its temporaries avoid r0 while r0 is free, or r0 gets a constant nobody reads
    if not F.calls and F.retval and 0 in F.pcopy.values():
        r0defs = [w for w, k in F.kind.items() if k[0] != 'param' and next(iter(F.webs[w]))[1] == 0]
        dead0 = any(F.kind[w][0] == 'dead' and all(isinstance(d[0], int) and
                    any(o[0] == 'set' and o[1] == 0 and is_c(o[2]) for o in F.ops[d[0]]) for d in F.webs[w])
                    for w in r0defs)
        def r0_free_temp():
            # a temporary in r1-r3 computed before the first write of r0 after the copy
            first0 = min([d[0] for w in r0defs for d in F.webs[w] if isinstance(d[0], int)] + [len(F.ins)])
            for w, k in F.kind.items():
                if k[0] != 'temp': continue
                d = next(iter(F.webs[w]))
                if 1 <= d[1] <= 3 and F.body0 + len(F.pcopy) <= d[0] < first0:
                    last = max((u[0] for u in F.defuses.get(d, []) if isinstance(u[0], int)), default=d[0])
                    stores = any(o[0] == 'st' and o[3] == ('r', d[1]) for u in F.defuses.get(d, [])
                                 if isinstance(u[0], int) for o in F.ops[u[0]])
                    if stores and not any(0 in F.UD[j][1] for j in range(d[0], last + 1)): return True
            return False
        if dead0 or r0_free_temp():
            for w in r0defs:
                F.kind[w] = ('var', 0)
    # parameter webs: those reading entry r0-r3 or their prologue copies
    for w, k in F.kind.items():
        if k[0] == 'param': F.params[k[1]] = True
    # names
    byreg = collections.defaultdict(list)
    for w, k in sorted(F.kind.items(), key=lambda kv: min(d[0] if isinstance(d[0], int) else -1 for d in F.webs[kv[0]])):
        if k[0] == 'param': F.wname[w] = f'param_{k[1] + 1}'
        elif k[0] in ('var', 'select', 'bad'):
            byreg[next(iter(F.webs[w]))[1]].append(w)
    preg = {}
    for w, k in F.kind.items():
        if k[0] == 'param':
            for d in F.webs[w]:
                if isinstance(d[0], int) and d[0] in F.pcopy: preg[d[1]] = k[1]
    for r, ws in list(byreg.items()):
        if r in preg:
            for w in ws:
                if F.kind[w][0] == 'var': F.kind[w] = ('param', preg[r]); F.wname[w] = f'param_{preg[r] + 1}'
            del byreg[r]
    for r, ws in byreg.items():
        for w in ws:
            # armcc -O0 gives each local a register of its own: the webs of one register are one local
            F.wname[w] = f'v{r}'

# ---------------------------------------------------------------- evaluation


class Eval:
    def __init__(self, F, ctx):
        self.F, self.ctx = F, ctx
        self.calls = {}            # name -> (nargs, returns value)

    def read(self, i, r, state):
        F = self.F
        ds = F.usedefs.get((i, r))
        if ds is None: raise Unsupported(f'no defs for r{r} at {i}')
        w = F.web[next(iter(ds))]
        k = F.kind[w]
        if k[0] == 'param': return ('v', F.wname[w])
        if k[0] == 'temp':
            if r not in state: raise Unsupported(f'temp r{r} lost at {i}')
            return state[r]
        if k[0] == 'select':
            if r in state: return state[r]
            return ('phi', w)
        if k[0] == 'var': return ('v', F.wname[w])
        if k[0] == 'const': return k[1]
        raise Unsupported(f'web kind {k}')

    def rd(self, i, e, state):
        return subst(e, lambda x: self.read(i, x[1], state) if x[0] == 'r' else None)

    def block(self, b):
        F = self.F
        state = {}
        stmts = []
        fl = None
        fls = {getattr(self.F.blocks[p], 'fl_out', None) for p in b.pred}
        if len(fls) == 1: fl = next(iter(fls))
        flz = None
        b.term = ('fall',)
        b.outs = {}
        pend_args = {}
        defpos = self.defpos = {}
        for i in range(b.lo, b.hi):
            for o in F.ops[i]:
                t = o[0]
                if t == 'nop' and F.ins[i].mn == 'nop' and F.ins[i].raw.startswith('nop') and i > b.lo and i + 1 < b.hi and \
                        F.ops[i + 1][-1][0] not in ('sw',) and not (F.ins[i + 1].mn == 'nop' and not F.ins[i + 1].raw.startswith('nop')) and \
                        F.ops[i + 1][-1][0] not in ('b',) and F.ins[i - 1].mn not in ('nop',):
                    # a nop inside straight-line code is __nop() (the label nops armcc makes sit at
                    # block starts and before the jump into a loop's test)
                    stmts.append(('expr', ('call', '__nop', (), 'void'))); continue
                if t in ('nop', 'pro', 'epi'): continue
                if t == 'set' or t == 'pset':
                    r = o[1] if t == 'set' else o[2]
                    e = self.rd(i, o[2] if t == 'set' else o[3], state)
                    if t == 'pset':
                        old = self.read(i, r, state)
                        e = ('sel', cond_of(o[1], fl), e, old)
                    if e[0] == 'cast' and e[2][0] == 'ld' and e[1] == e[2][1] and e[1] in ('uchar', 'ushort'):
                        # ldrh; uxth: a cast of a wider value whose load armcc narrowed
                        e = ('cast', e[1], ('ld', 'int', e[2][2]))
                    if e[0] == 'mls':
                        ra, rn, rm = e[1], e[2], e[3]
                        if rn[0] == 'b' and rn[1] in ('/s', '/u') and rn[2] == ra and rn[3] == rm:
                            e = ('b', '%' + rn[1][1], ra, rm)
                        elif rm[0] == 'b' and rm[1] in ('/s', '/u') and rm[2] == ra and rm[3] == rn:
                            e = ('b', '%' + rm[1][1], ra, rn)
                        else: e = mk('-', ra, mk('*', rn, rm))
                    self.define(i, r, e, state, stmts)
                elif t == 'fl':
                    fl = (o[1], self.rd(i, o[2], state), self.rd(i, o[3], state)); flz = None
                elif t == 'flz':
                    r = o[1]
                    w = F.web.get((i, r)); k = F.kind.get(w, ('dead',))
                    if k[0] in ('var', 'param'):
                        nm = F.wname[w]
                        fl = ('z', ('v', nm), C(0))
                        flz = len(stmts) - 1 if stmts and stmts[-1][0] == 'asg' and stmts[-1][1] == ('v', nm) else None
                    else:
                        fl = ('z', state.get(r, self.read(i, r, state) if (i, r) in F.usedefs else C(0)), C(0)); flz = None
                elif t == 'st' and o[2][0] == 'sp' and (i, o[2][1]) in F.stackarg:
                    pend_args[F.stackarg[(i, o[2][1])][1]] = self.rd(i, o[3], state)
                elif t == 'st':
                    ad = self.rd(i, o[2], state); v = self.rd(i, o[3], state)
                    if v[0] == 'multi':
                        state[v[2]] = ('asgx', ('ld', o[1], ad), v[1]); continue
                    stmts.append(('asg', ('ld', o[1], ad), v))
                elif t in ('call', 'icall'):
                    n = F.callargs.get(i, 0)
                    args = tuple(self.read(i, r, state) for r in range(n))
                    if i in F.callextra:
                        args += tuple(pend_args.get(k, C(0)) for k in range(4, F.callextra[i]))
                        pend_args.clear()
                    if t == 'call':
                        nm = self.ctx.fname(o[1]); fe = nm
                    else:
                        fe = ('fp', self.read(i, o[1], state)); nm = None
                    for r in call_clobbers(self.ctx, o): state.pop(r, None)
                    e = ('call', fe, args, 'int')
                    if nm: self.calls[nm] = (n, self.calls.get(nm, (0, False))[1])
                    w = F.web.get((i, 0))
                    if w is None or F.kind.get(w, ('dead',))[0] == 'dead':
                        stmts.append(('expr', e))
                        state.pop(0, None)
                    else:
                        if nm: self.calls[nm] = (n, True)
                        self.define(i, 0, e, state, stmts)
                elif t == 'intr':
                    stmts.append(('expr', ('call', o[1], o[2], 'void')))
                elif t == 'b':
                    b.term = ('goto', F.bof[F.addr2k[o[1]]])
                elif t == 'bc':
                    if flz is not None and flz == len(stmts) - 1 and fl[0] == 'z':
                        st = stmts.pop()          # if ((v = e) != 0): the assignment in the condition
                        fl = ('z', ('asgx', st[1], st[2]), C(0)); flz = None
                    b.term = ('cond', cond_of(o[1], fl), F.bof[F.addr2k[o[2]]])
                elif t == 'cbz':
                    v = self.read(i, o[1], state)
                    b.term = ('cond', ('cmp', '!=' if o[2] else '==', v, C(0), True), F.bof[F.addr2k[o[3]]])
                elif t == 'sw':
                    v = self.read(i, o[1], state) if o[1] is not None else None
                    b.term = ('sw', v, [F.bof[F.addr2k[x]] for x in o[2]])
                elif t == 'ret':
                    b.term = ('ret',)
                else:
                    raise Unsupported(f'op {t}')
        b.fl_out = fl
        if b.k in F.retblocks:
            b.retval = self.read(('end', b.k), 0, state)
        b.stmts = stmts
        b.state = state

    def define(self, i, r, e, state, stmts):
        F = self.F
        w = F.web.get((i, r))
        k = F.kind.get(w, ('dead',))
        if k[0] == 'temp':
            uses = F.defuses.get((i, r), [])
            cheap = e[0] in ('c', 'g', 'f', 'sp', 'v', 'per') or \
                (e[0] == 'b' and e[1] in ('+', '-') and e[2][0] in ('v', 'g', 'sp', 'per') and is_c(e[3]))
            if len(uses) > 1 and e[0] not in ('c', 'g', 'f', 'sp', 'v', 'per'):
                first = min((u[0] for u in uses if not isinstance(u[0], tuple)), default=None)
                if first is not None:
                    fo = F.ops[first]
                    if any(o[0] == 'st' and o[3] == ('r', r) for o in fo) or \
                            any(o[0] == 'set' and o[2] == ('r', r) and
                                F.kind.get(F.web.get((first, o[1])), ('',))[0] in ('var', 'param') for o in fo):
                        cheap = False
            if len(uses) > 1 and not cheap:
                # the value of an assignment used again: x = y = e, if ((g = g + 1) > 300)
                first = min((u[0] for u in uses if not isinstance(u[0], tuple)), default=None)
                if first is None: state[r] = e; return
                fo = F.ops[first]
                ok = any(o[0] == 'st' and o[3] == ('r', r) for o in fo) or \
                    any(o[0] == 'set' and o[2] == ('r', r) and
                        F.kind.get(F.web.get((first, o[1])), ('',))[0] in ('var', 'param') for o in fo)
                if ok:
                    state[r] = ('multi', e, r)
                    return
                # else a local of its own (a block-scoped temporary in the source)
                F.kind[w] = ('var', r)
                nm = f'v{r}'
                while nm in F.wname.values(): nm += 'x'
                F.wname[w] = nm
                stmts.append(('asg', ('v', nm), e))
                state.pop(r, None)
                return
            state[r] = e
            self.defpos[r] = i
        elif k[0] in ('var', 'param'):
            nm = F.wname[w]
            if e == ('v', nm): return
            if e[0] == 'multi':
                state[e[2]] = ('asgx', ('v', nm), e[1])
                if r != e[2]: state.pop(r, None)
                return
            if i in F.pcopy and k[0] == 'param': return
            # a pending copy of the old value (mov r0, r6; adds r6, r6, #1; ... r0): v++ in its use
            if e[0] == 'b' and e[1] in ('+', '-') and e[2] == ('v', nm) and is_c(e[3]) or \
                    e[0] == 'cast' and e[2][0] == 'b' and e[2][1] in ('+', '-') and e[2][2] == ('v', nm) and is_c(e[2][3]):
                held = [rr for rr, ev in state.items() if ev == ('v', nm) and rr != r]
                if len(held) == 1:
                    state[held[0]] = ('post', nm, e)
                    state.pop(r, None)
                    return
            stmts.append(('asg', ('v', nm), e))
            state.pop(r, None)
        elif k[0] == 'select':
            state[r] = e
        elif k[0] == 'const':
            state.pop(r, None)
        elif k[0] == 'dead':
            vn = f'v{r}'
            if any(F.kind.get(w2, ('',))[0] == 'var' and F.wname.get(w2) == vn for w2 in F.kind):
                # a value stored to a local and never read (int j = 0; ... for (j = 0; ...)): -O0 keeps it
                stmts.append(('asg', ('v', vn), e)); state.pop(r, None)
            elif not pure(e): stmts.append(('expr', e)); state.pop(r, None)
            else: state[r] = e              # flags of the result may still read it
        else:
            raise Unsupported(f'define {k}')

# ---------------------------------------------------------------- structuring


def inter(dom, rng):
    out = []
    for l, h in dom:
        for l2, h2 in rng:
            lo, hi = max(l, l2), min(h, h2)
            if lo <= hi: out.append((lo, hi))
    return out


def minus(dom, rng):
    out = list(dom)
    for l2, h2 in rng:
        nxt = []
        for l, h in out:
            if h < l2 or l > h2: nxt.append((l, h)); continue
            if l < l2: nxt.append((l, l2 - 1))
            if h > h2: nxt.append((h2 + 1, h))
        out = nxt
    return out


def split(c, dom):
    """(values of dom where c holds, the rest) for c = x OP constant."""
    op, k, sg = c[1], sval(c[3][1]), c[4]
    BIG = (1 << 31) - 1
    if not sg:
        if k < 0: raise Unsupported('unsigned compare with a negative')
        # unsigned: negative values are huge
        r = {'<': [(0, k - 1)], '<=': [(0, k)], '>': [(k + 1, BIG), (-BIG - 1, -1)], '>=': [(k, BIG), (-BIG - 1, -1)],
             '==': [(k, k)], '!=': [(-BIG - 1, k - 1), (k + 1, BIG)]}[op]
    else:
        r = {'<': [(-BIG - 1, k - 1)], '<=': [(-BIG - 1, k)], '>': [(k + 1, BIG)], '>=': [(k, BIG)],
             '==': [(k, k)], '!=': [(-BIG - 1, k - 1), (k + 1, BIG)]}[op]
    return inter(dom, r), minus(dom, r)


class Shape:
    """Structured C from the evaluated blocks, in layout order."""

    def __init__(self, F, ev):
        self.F, self.ev = F, ev
        self.B = F.blocks
        self.phi = {}
        self.want = collections.Counter()      # labels referenced (from the previous pass)

    def pure_cond(self, k):
        b = self.B[k]
        return not b.stmts and b.term[0] == 'cond' and not self.live_out(b)

    def live_out(self, b):
        F = self.F
        return any(F.kind.get(F.web.get((i, r)), ('',))[0] == 'select' for i in range(b.lo, b.hi) for r in F.UD[i][1])

    def chain(self, i, hi):
        """The longest run of blocks from i that reduces to one condition: (cond, target, last)."""
        B = self.B
        if B[i].term[0] != 'cond': return None
        j = i
        while j + 1 < hi and self.pure_cond(j + 1): j += 1
        for last in range(j, i - 1, -1):
            r = self.reduce(i, last)
            if r: return r + (last,)
        return None

    def reduce(self, i, last, head=None):
        """Blocks i..last, each `if (c) goto t` falling into the next, as one `if (C) goto T`
        falling into last + 1, or None."""
        B = self.B
        el = [[k, B[k].term[1], B[k].term[2]] for k in range(i, last + 1)]
        for k in range(i + 1, last + 1):
            if any(p < i or p > last for p in B[k].pred): return None
        def fall(idx): return el[idx + 1][0] if idx + 1 < len(el) else last + 1
        changed = True
        while changed and len(el) > 1:
            changed = False
            for idx in range(len(el) - 1):
                X, Y = el[idx], el[idx + 1]
                if any(e[2] == Y[0] for e in el): continue
                if X[2] == Y[2]:
                    el[idx:idx + 2] = [[X[0], ('or', X[1], Y[1]), Y[2]]]; changed = True; break
                if X[2] == fall(idx + 1):
                    el[idx:idx + 2] = [[X[0], ('and', neg(X[1]), Y[1]), Y[2]]]; changed = True; break
        if len(el) != 1: return None
        if i <= el[0][2] <= last and el[0][2] != head: return None
        return el[0][1], el[0][2]

    def emit(self, ind, s):
        self.lines.append('  ' * ind + s)

    def stmts(self, b, ind):
        for s in b.stmts: self.emit(ind, self.ev.stmt(s))

    def run(self):
        n = len(self.B)
        self.back = collections.defaultdict(list)
        for b in self.B:
            for s in b.succ:
                if s <= b.k: self.back[s].append(b.k)
        for _ in range(2):
            self.lines, self.done, self.labels = [], set(), collections.Counter()
            self.emitted, self.dup, self.placed = set(), 0, set()
            self.phi.clear()
            self.region(0, n, 1, {})
            if set(self.labels) == set(self.want): break
            self.want = collections.Counter({k: 1 for k in self.labels if self.labels[k] > 0})
        return self.lines

    def is_ret(self, k):
        return k is not None and 0 <= k < len(self.B) and self.B[k].epi

    def ret_stmt(self, b):
        v = getattr(b, 'retval', None)
        if self.F.retval and v is not None: return f'return {self.ev.expr(v)};'
        return 'return;'

    def goto(self, t, ind):
        self.labels[t] += 1; self.emit(ind, f'goto L_{t};')

    def jump(self, src, t, ctx, ind, last):
        """A goto from block src to block t as C: break, continue, return, nothing or goto."""
        if last and t == ctx.get('skip'): return
        if last and t == len(self.B) - 1 and self.is_ret(t) and not self.F.retval and ctx.get('hi', 0) >= t and \
                ctx.get('tail', True) and ctx.get('fnend'): return
        if last and t == ctx.get('hi') and ctx.get('tail', True): return
        if t == ctx.get('cont') and src == ctx.get('loopend'): return
        if t == ctx.get('brk'): self.emit(ind, 'break;'); return
        if t == ctx.get('cont'): self.emit(ind, 'continue;'); return
        if self.is_ret(t): self.emit(ind, self.ret_stmt(self.B[src])); return
        if t == src + 1 and not last: return
        self.goto(t, ind)

    def region(self, lo, hi, ind, ctx):
        """Blocks [lo, hi) as statements at indent ind."""
        B = self.B
        ctx = dict(ctx, hi=hi, fnend=hi >= len(B) - 1)
        k = lo
        while k < hi:
            if k in self.emitted:
                # already placed by another region (a layout the shapes here do not cover)
                self.dup += 1
                self.goto(k, ind)
                while k < hi and k in self.emitted: k += 1
                continue
            if self.want.get(k) and k not in self.placed: self.emit(0, f'L_{k}:;'); self.placed.add(k)
            b = B[k]
            if k and not b.pred and not b.stmts and b.term[0] in ('goto', 'fall'):
                self.emitted.add(k); k += 1; continue          # an island no branch uses any more
            if b.epi:
                self.emitted.add(k)
                # an epilogue a region starts with (the then-part of `if (c) return;`)
                if k == lo and k > 0 and k != len(B) - 1 and B[k - 1].term[0] == 'cond':
                    self.emit(ind, self.ret_stmt(B[k - 1]))
                elif k > lo and self.F.retval and B[k - 1].term[0] == 'cond' and getattr(B[k - 1], 'retval', None) is not None:
                    self.emit(ind, self.ret_stmt(B[k - 1]))      # after a loop whose test falls into it
                k += 1; continue
            lp = self.loop_at(k, hi, ctx)
            if lp: k = lp(ind); continue
            self.emitted.add(k)
            if b.term[0] == 'cond':
                sw = self.switch(k, hi, ind, ctx)
                if sw is not None: k = sw; continue
            self.stmts(b, ind)
            t = b.term
            if t[0] in ('fall', 'ret'):
                if k + 1 < len(B) and B[k + 1].epi:
                    if self.F.retval and getattr(b, 'retval', None) is not None: self.emit(ind, self.ret_stmt(b))
                    elif k + 2 < len(B) and not (k + 1 == hi and ctx.get('tail') and ctx.get('fnend')):
                        self.emit(ind, 'return;')
                k += 1; continue
            if t[0] == 'goto':
                self.jump(k, t[1], ctx, ind, k + 1 == hi)
                k += 1; continue
            if t[0] == 'sw':
                raise Unsupported('table switch without a bounds check')
            if t[0] == 'cond':
                k = self.cond(k, hi, ind, ctx); continue
            raise Unsupported(f'term {t}')

    # -- if / else
    def cond(self, k, hi, ind, ctx):
        B = self.B
        ch = self.chain(k, hi)
        if ch is None: c, T, last = B[k].term[1], B[k].term[2], k
        else: c, T, last = ch
        nxt = last + 1
        E = self.ev
        # `if (x) break;` (or continue, return, goto): a conditional branch over a lone jump
        if T == nxt + 1 and nxt < hi and not B[nxt].stmts and B[nxt].term[0] == 'goto' and nxt not in self.want:
            j = B[nxt].term[1]
            if j in (ctx.get('brk'), ctx.get('cont')) or self.is_ret(j):
                self.emitted.add(nxt)
                self.emit(ind, f'if ({E.cond(neg(c))}) {{')
                self.jump(nxt, j, dict(ctx, tail=False), ind + 1, False)
                self.emit(ind, '}')
                return T
        if T == ctx.get('brk'): self.emit(ind, f'if ({E.cond(c)}) break;'); return nxt
        if T == ctx.get('cont') and T == hi and nxt < hi:
            # a branch to the loop's test from the body's last statement: the rest is the then-part
            self.emit(ind, f'if ({E.cond(neg(c))}) {{')
            self.region(nxt, hi, ind + 1, dict(ctx, tail=True))
            self.emit(ind, '}')
            return hi
        if T == ctx.get('cont'): self.emit(ind, f'if ({E.cond(c)}) continue;'); return nxt
        if self.is_ret(T) and not (T == len(B) - 1 and T <= hi and not self.F.retval):
            self.emit(ind, f'if ({E.cond(c)}) {self.ret_stmt(B[last])}'); return nxt
        # a branch to a lone `b epilogue` past the region (a return placed elsewhere)
        if T < len(B) - 1 and not B[T].stmts and B[T].term[0] == 'goto' and self.is_ret(B[T].term[1]) and not self.F.retval and T != nxt:
            self.emit(ind, f'if ({E.cond(c)}) return;'); return nxt
        if T > hi and T == ctx.get('skip') and nxt < hi:
            # a branch to the enclosing join: the rest of this region is the then-part
            self.emit(ind, f'if ({E.cond(neg(c))}) {{')
            self.region(nxt, hi, ind + 1, dict(ctx, tail=True))
            self.emit(ind, '}')
            return hi
        if nxt < T <= hi:
            tb = B[T - 1]
            M = None
            if tb.term[0] == 'goto' and tb.term[1] > T and (tb.term[1] <= hi or tb.term[1] == ctx.get('skip')) and \
                    tb.term[1] not in (ctx.get('brk'), ctx.get('cont')) and \
                    (not self.is_ret(tb.term[1]) or (tb.term[1] == len(B) - 1 and not self.F.retval)):
                M = tb.term[1]
            if M is None and tb.epi:
                # the then-part returns at its end: the join is where its other exits go
                outs = {t for z in range(nxt, T) for t in B[z].succ if T < t <= hi}
                if len(outs) == 1: M = next(iter(outs))
            # the join past the region: the enclosing if-else's (its then-part ends here too)
            Me = M if M is None or M <= hi else (hi if M == ctx.get('skip') else None)
            if Me is not None:
                # a real else: its blocks entered only from inside it (T from the condition)
                for z in range(T + 1, Me):
                    if any(not (T <= p < Me) for p in B[z].pred): M = Me = None; break
            if Me is not None and Me == M and self.select(c, nxt, T, M): return M
            self.emit(ind, f'if ({E.cond(neg(c))}) {{')
            if Me is not None and Me > T:
                self.region(nxt, T, ind + 1, dict(ctx, tail=True, skip=M))
                self.emit(ind, '}')
                self.emit(ind, 'else {')
                self.region(T, Me, ind + 1, dict(ctx, tail=True, skip=M if Me != M else ctx.get('skip')))
                self.emit(ind, '}')
                return Me
            self.region(nxt, T, ind + 1, dict(ctx, tail=True, skip=M if M is not None else ctx.get('skip')))
            self.emit(ind, '}')
            return T
        if T == nxt:
            self.emit(ind, f'if ({E.cond(c)}) {{}}'); return nxt
        self.labels[T] += 1
        self.emit(ind, f'if ({E.cond(c)}) goto L_{T};')
        return nxt

    def select(self, c, nxt, T, M):
        """`if (c) goto T; r = a; goto M; T: r = b; M:` with r a select register: r = !c ? a : b."""
        B = self.B
        if T - nxt != 1 or M - T != 1: return False
        a, b2 = B[nxt], B[T]
        if a.stmts or b2.stmts or b2.term[0] != 'fall': return False
        F = self.F
        def selws(b):
            out = {}
            for i in range(b.lo, b.hi):
                for r in F.UD[i][1]:
                    w = F.web.get((i, r))
                    if F.kind.get(w, ('',))[0] == 'select': out[r] = w
                    else: return None
            return out
        wa, wb = selws(a), selws(b2)
        if not wa or wa != wb: return False
        vals = {}
        for r, w in wa.items():
            ea, eb = a.state.get(r), b2.state.get(r)
            if ea is None or eb is None: return False
            cc = neg(c)
            if is_c(ea, 1) and is_c(eb, 0): v = ('bool', cc)
            elif is_c(ea, 0) and is_c(eb, 1): v = ('bool', c)
            else: v = ('sel', cc, ea, eb)
            vals[w] = v
        self.phi.update(vals)
        return True

    # -- loops
    def loop_at(self, k, hi, ctx):
        B = self.B
        if k in self.done: return None
        latches = [j for j in self.back.get(k, []) if k <= j < hi]
        if not latches: return None
        j = max(latches)
        bj = B[j]
        if bj.term[0] == 'cond' and bj.term[2] == k:
            pre = k - 1
            if pre >= 0 and B[pre].term[0] == 'goto' and k < B[pre].term[1] <= j:
                st = B[pre].term[1]
                if not B[st].stmts:
                    r = self.reduce_to(st, j, k)
                    if r is not None:
                        return lambda ind: self.loop(k, st, j, r, ind, ctx, True)
            cands = [j]
            s = j
            while s - 1 > k and self.pure_cond(s - 1): s -= 1; cands.append(s)
            if s - 1 == k and B[k].term[0] == 'cond': cands.append(k)
            for st in sorted(cands):
                r = self.reduce_to(st, j, k)
                if r is not None:
                    return lambda ind, st=st, r=r: self.loop(k, st, j, r, ind, ctx, False)
            return None
        if bj.term[0] == 'goto' and bj.term[1] == k:
            return lambda ind: self.forever(k, j, ind, ctx)
        return None

    def reduce_to(self, st, j, k):
        if self.B[st].term[0] != 'cond': return None
        for x in range(st + 1, j + 1):
            if not self.pure_cond(x): return None
        r = self.reduce(st, j, k)
        if r is None or r[1] != k: return None
        return r[0]

    def loop(self, k, st, j, c, ind, ctx, is_while):
        B = self.B
        self.done.add(k)
        exitk = j + 1
        lctx = dict(ctx, brk=exitk, cont=st, tail=True, skip=None)
        if is_while:
            # the block before k ended with `goto st` (emitted by the caller): the loop's entry
            if self.lines and self.lines[-1].strip() == f'goto L_{st};':
                self.lines.pop(); self.labels[st] -= 1
            init = self.for_init(k - 1, c)
            if init: self.emit(ind, f'for ({init}; {self.ev.cond(c)}; ) {{')
            else: self.emit(ind, f'while ({self.ev.cond(c)}) {{')
            self.region(k, st, ind + 1, lctx)
            self.emit(ind, '}')
            return exitk
        self.emit(ind, 'do {')
        self.region(k, st, ind + 1, lctx)
        self.stmts(B[st], ind + 1)
        self.emitted |= set(range(st, j + 1))
        self.emit(ind, f'}} while ({self.ev.cond(c)});')
        return exitk

    def for_init(self, pre, c):
        """`v = K` for a while loop whose `b test` follows a nop: armcc drops a for-init that stores
        the value the variable already holds and leaves a nop (for (i = 0; ...) after i = 0)."""
        F, B = self.F, self.B
        j = B[pre].hi - 2
        if j < B[pre].lo or F.ins[j].mn != 'nop': return None
        vs = [x for x in walk(c) if x[0] == 'v']
        if not vs: return None
        v = vs[0]
        for z in range(pre, -1, -1):
            for st in reversed(B[z].stmts):
                if st[0] == 'asg' and st[1] == v:
                    if is_c(st[2]): return f'{v[1]} = {self.ev.expr(st[2])}'
                    return None
        return None

    def forever(self, k, j, ind, ctx):
        self.done.add(k)
        exitk = j + 1
        # entered through `b j` (while (1) compiles to a jump to its closing `b` back)
        if k and self.B[k - 1].term == ('goto', j) and self.lines and self.lines[-1].strip() == f'goto L_{j};':
            self.lines.pop(); self.labels[j] -= 1
        self.emit(ind, 'while (1) {')
        self.region(k, j + 1, ind + 1, dict(ctx, brk=exitk, cont=k, tail=False, loopend=j, skip=None))
        self.emit(ind, '}')
        return exitk

    # -- switches
    def switch(self, k, hi, ind, ctx):
        """A switch: armcc's dispatch (compares of one value against constants, as a chain or a
        binary tree, bounds checks and tbb/tbh/word-offset tables) in the blocks from k, then the
        case bodies laid out after it. The values reaching each body are found by walking the
        dispatch with the set of values still possible."""
        B = self.B
        if B[k].term[0] != 'cond': return None
        x = self.cmp_x(B[k])
        if x is None: return None
        subs = {}
        # the value switched on minus the first case, kept in a local for the table (subs r2, #9)
        pre = {}
        if x[0] == 'v' and B[k].stmts:
            st = B[k].stmts[-1]
            if st[0] == 'asg' and st[1] == x:
                e = st[2]; o = 0
                if e[0] == 'b' and e[1] in ('-', '+') and is_c(e[3]):
                    o = sval(e[3][1]) * (-1 if e[1] == '-' else 1); e = e[2]
                if pure(e) or e[0] == 'ld':
                    pre[x[1]] = o; x = e
        def norm_off(e, sb=None):
            """x + c for e = x, x - c, v - c with v = x + c0 ...; else None. sb: the offsets of
            the locals on this path through the dispatch (one register can hold x - K1 on one
            path and x - K2 on another)."""
            sb = subs if sb is None else sb
            if e[0] == 'v' and e[1] in sb: return sb[e[1]]      # first: x's own register may be re-based
            if e == x: return 0
            if e[0] == 'b' and e[1] in ('-', '+') and is_c(e[3]):
                o = norm_off(e[2], sb)
                if o is None: return None
                return o + (sval(e[3][1]) if e[1] == '+' else -sval(e[3][1]))
            return None
        def offsets(b):
            ok = []
            for st in b.stmts:
                if st[0] == 'asg' and st[1][0] == 'v' and norm_off(st[2]) is not None: ok.append(st)
                else: return None
            return ok
        subs.update(pre)
        for st in (offsets(B[k]) or [st for st in B[k].stmts if st[0] == 'asg' and st[1][0] == 'v' and norm_off(st[2]) is not None]):
            subs[st[1][1]] = norm_off(st[2])
        start = dict(subs)
        allsubs = set(subs)
        m = k
        while m + 1 < hi:
            nb = B[m + 1]
            if (nb.stmts and offsets(nb) is None) or any(p < k or p > m for p in nb.pred): break
            for st in (offsets(nb) or []): subs[st[1][1]] = norm_off(st[2]); allsubs.add(st[1][1])
            if nb.term[0] == 'cond' and self.cmp_x(nb) is not None and norm_off(self.cmp_x(nb)) is not None: m += 1; continue
            if nb.term[0] == 'goto': m += 1; continue
            if nb.term[0] == 'sw': m += 1; continue
            break
        FULL = [(-(1 << 31), (1 << 31) - 1)]
        leaves = collections.defaultdict(list)
        seen = set()
        def unsub(e, sb):
            o = norm_off(e, sb)
            if o is None: return e
            return mk('+', x, C(o)) if o else x
        def walk_d(j, dom, sb):
            if not dom: return
            if not (k <= j <= m):
                leaves[j].extend(dom); return
            key = (j, tuple(dom), tuple(sorted(sb.items())))
            if key in seen: return
            seen.add(key)
            b = B[j]
            if j > k:
                sb = dict(sb)
                for st in (offsets(b) or []): sb[st[1][1]] = norm_off(st[2], sb)
            t = b.term
            if t[0] == 'goto': walk_d(t[1], dom, sb); return
            if t[0] == 'cond':
                c = t[1]
                nxt = j + 1
                while nxt <= m and B[nxt].term[0] == 'goto' and not B[nxt].stmts and B[nxt].term[1] <= m: nxt = B[nxt].term[1]
                if nxt <= m and B[nxt].term[0] == 'sw' and c[0] == 'cmp' and not c[4] and c[1] in ('>=', '>') and is_c(c[3]):
                    n = c[3][1] + (1 if c[1] == '>' else 0)
                    xi = unsub(c[2], sb); off = 0
                    if xi[0] == 'b' and xi[1] == '-' and is_c(xi[3]): off = sval(xi[3][1]); xi = xi[2]
                    if xi[0] == 'b' and xi[1] == '+' and is_c(xi[3]): off = -sval(xi[3][1]); xi = xi[2]
                    if xi != x: raise Unsupported('table index')
                    inside = inter(dom, [(off, off + n - 1)])
                    walk_d(t[2], minus(dom, [(off, off + n - 1)]), sb)
                    for v, tk in enumerate(B[nxt].term[2][:n]):
                        part = inter(inside, [(v + off, v + off)])
                        if part: walk_d(tk, part, sb)
                    return
                if c[0] != 'cmp' or not is_c(c[3]): raise Unsupported('dispatch cond')
                xi, off = unsub(c[2], sb), 0
                if xi[0] == 'b' and xi[1] == '-' and is_c(xi[3]): off = sval(xi[3][1]); xi = xi[2]
                if xi[0] == 'b' and xi[1] == '+' and is_c(xi[3]): off = -sval(xi[3][1]); xi = xi[2]
                if xi != x: raise Unsupported('dispatch value')
                sh = lambda d, o: [(l + o, h + o) for l, h in d]
                tdom, fdom = split(c, sh(dom, -off))
                walk_d(t[2], sh(tdom, off), sb); walk_d(j + 1, sh(fdom, off), sb); return
            raise Unsupported('dispatch term')
        try:
            walk_d(k, FULL, start)
        except Unsupported:
            return None
        size = lambda d: sum(h - l + 1 for l, h in d)
        if len(leaves) < 3: return None
        dflt = max(leaves, key=lambda t: size(leaves[t]))
        cases = {}
        for tk, d in leaves.items():
            if tk == dflt: continue
            if size(d) > 512: return None
            cases[tk] = [v for l, h in d for v in range(l, h + 1)]
        if sum(len(v) for v in cases.values()) < 2: return None
        first = m + 1
        if any(tk < first or tk >= hi for tk in cases) or dflt < first: return None
        for st in B[k].stmts:
            if not (st[0] == 'asg' and st[1][0] == 'v' and st[1][1] in subs): self.emit(ind, self.ev.stmt(st))
        self.ev.hidden |= allsubs
        return self.emit_switch(first, x, cases, dflt, hi, ind, ctx)

    def cmp_x(self, b):
        """The value a dispatch block compares with a constant (or None)."""
        t = b.term
        if t[0] != 'cond' or t[1][0] != 'cmp' or not is_c(t[1][3]): return None
        x = t[1][2]
        if not t[1][4] and x[0] == 'b' and x[1] == '-' and is_c(x[3]): x = x[2]
        return x

    def emit_switch(self, first, x, cases, dflt, hi, ind, ctx):
        """Case bodies from block first; the end is the most common goto target past them."""
        B = self.B
        starts = sorted(set(cases) | ({dflt} if dflt < hi else set()))
        if not starts or starts[0] != first: raise Unsupported('switch bodies not after the dispatch')
        last = max(starts)
        tg = collections.Counter()
        for z in range(first, hi):
            tz = B[z].term
            if tz[0] == 'goto' and tz[1] > last and tz[1] <= hi: tg[tz[1]] += 1
        end = tg.most_common(1)[0][0] if tg else (dflt if dflt > last and dflt <= hi else hi)
        has_default = dflt < end
        self.emit(ind, f'switch ({self.ev.expr(x)}) {{')
        bodies = sorted(z for z in set(cases) | ({dflt} if has_default else set()) if z < end)
        sctx = dict(ctx, brk=end, tail=False, skip=None)
        for n, z in enumerate(bodies):
            for v in sorted(cases.get(z, [])): self.emit(ind, f'case {v}:')
            if z == dflt and has_default: self.emit(ind, 'default:')
            zend = bodies[n + 1] if n + 1 < len(bodies) else end
            self.region(z, zend, ind + 1, sctx)
        self.emit(ind, '}')
        return end

# ---------------------------------------------------------------- rendering


PREC = {'*': 13, '/': 13, '%': 13, '+': 12, '-': 12, '<<': 11, '>>': 11, '<': 10, '<=': 10, '>': 10, '>=': 10,
        '==': 9, '!=': 9, '&': 8, '^': 7, '|': 6, '&&': 5, '||': 4, '?:': 3}


class Render:
    """Expressions and statements as C; collects globals, callees, stack locals and types."""

    def __init__(self, F, ctx):
        self.F, self.ctx = F, ctx
        self.tokens = {}            # (addr, type) -> token
        self.hidden = set()
        self.callmin = {}
        self.calls = {}
        self.phi = {}
        self.vtype = collections.defaultdict(lambda: 'int')

    def token(self, a, t):
        key = (a, t)
        if key in self.tokens: return self.tokens[key]
        same = [k for k in self.tokens if k[0] == a]
        nm = f'DAT_{a:08x}' + ('' if not same else f'_{t}')
        self.tokens[key] = nm
        return nm

    def typeof(self, e):
        t = e[0]
        if t == 'c': return 'int'
        if t == 'v': return self.vtype[e[1]]
        if t == 'ld': return e[1]
        if t == 'cast': return e[1]
        if t == 'call': return 'int'
        if t == 'b':
            if e[1] in ('/u', '%u', '>>u'): return 'uint'
            a, b = self.typeof(e[2]), self.typeof(e[3])
            return 'uint' if 'uint' in (a, b) else 'int'
        return 'int'

    def signed(self, e):
        return self.typeof(e) != 'uint'

    def addr_parts(self, e):
        """(base, [(index expr, scale)], offset) of an address expression."""
        terms = []
        def flat(x, sign=1):
            if x[0] == 'b' and x[1] == '+':
                # the left spine only: a compound right operand is one index (g + (i + 2))
                flat(x[2], sign)
                if x[3][0] == 'b' and x[3][1] in ('+', '-') and not is_c(x[3][3]) or \
                        x[3][0] == 'b' and x[3][1] in ('+', '-') and x[3][2][0] not in ('g', 'sp'):
                    terms.append(x[3])
                else: flat(x[3], sign)
            elif x[0] == 'b' and x[1] == '-' and is_c(x[3]): flat(x[2], sign); terms.append(('c', -sval(x[3][1]) * sign))
            else: terms.append(x if sign == 1 else ('u', '-', x))
        flat(e)
        off = sum(sval(x[1]) for x in terms if x[0] == 'c')
        rest = [x for x in terms if x[0] != 'c']
        base = None
        for x in rest:
            if x[0] in ('g', 'sp', 'per'): base = x; break
        if base is None:
            for x in rest:
                if not (x[0] == 'b' and x[1] == '*' and is_c(x[3])): base = x; break
        idx = []
        for x in rest:
            if x is base: continue
            if x[0] == 'b' and x[1] == '*' and is_c(x[3]): idx.append((x[2], x[3][1]))
            else: idx.append((x, 1))
        if base is not None and base[0] == 'sp': off += base[1]; base = ('sp', 0)
        return base, idx, off

    def mem(self, T, ad, lvalue=False):
        base, idx, off = self.addr_parts(ad)
        sz = TSIZE[T]
        if base is not None and base[0] == 'g':
            if not idx:
                if self.consistent(base[1], off): return self.token(base[1] + off, T)
                return f'*({T} *)(&{self.token(base[1], "uchar")} + {self.num(off)})'
            if len(idx) == 1 and idx[0][1] == sz and self.consistent(base[1], off) and \
                    (off == 0 or (ad[0] == 'b' and ad[1] == '+' and self.addr_parts(ad[2])[1] == [])):
                return f'(&{self.token(base[1] + off, T)})[{self.expr(idx[0][0])}]'
            return f'*({T} *)({self.expr(ad, 2)})'
        if base is not None and base[0] == 'sp':
            return self.stackref(T, off, idx, lvalue)
        vt = f'volatile {"uint" if T == "int" else T}'
        if base is not None and base[0] == 'per' and not idx:
            if off == 0: return f'*({vt} *){self.hexaddr(base[1])}'
            return f'*({vt} *)({self.hexaddr(base[1])} + {self.num(off)})'
        if base is None and not idx: return f'*({vt} *){self.hexaddr(off)}'
        if ad[0] != 'b': return f'*({T} *){self.expr(ad, 15)}'
        return f'*({T} *)({self.expr(ad, 2)})'

    def hexaddr(self, v):
        return f'0x{v & 0xffffffff:x}'

    def consistent(self, b, off):
        s, o = symbol_for(b + off, set(self.F.poolvals))
        return s == b and o == off

    def scaled(self, i, k):
        if k == 1: return self.expr(i, PREC['+'] + 1)
        return f'{self.expr(i, PREC["*"])} * {k}'

    def num(self, v):
        v = sval(v) if isinstance(v, int) else v
        if -10 < v < 10: return str(v)
        return hex(v) if v > 0 else '-' + hex(-v)

    def pslot(self, off):
        p = self.pslot2(off)
        return p[0] if p and p[1] == 0 else None

    def pslot2(self, off):
        """(parameter index, byte offset inside it) of a stack offset in a parameter's home."""
        F = self.F
        if off >= F.argbase: return 4 + (off - F.argbase) // 4, (off - F.argbase) % 4
        if F.frame <= off < F.frame + 4 * len(F.lowpush):
            r = F.lowpush[(off - F.frame) // 4]
            if r < F.nparams and (F.frame or (r not in F.params and r not in F.pcopy.values())): return r, (off - F.frame) % 4
        return None

    def stackref(self, T, off, idx, lvalue):
        p2 = self.pslot2(off)
        if p2 is not None and not idx:
            p, rel = p2
            self.F.usedparams.add(p)
            if rel == 0 and TSIZE[T] == 4: return f'param_{p + 1}'
            return f'*({T} *)((uchar *)&param_{p + 1} + {rel})'
        o = self.F.stackobj.find(off)
        if o is None: raise Unsupported(f'stack offset {off}')
        nm, base, ty, n, kind = o
        rel = off - base
        if kind in ('scalar', 'vscalar') and not idx and rel == 0:
            return nm
        if kind == 'struct':
            for fo, fs, fname in self.ctx.structs[ty][1]:
                if fo == rel and not idx: return f'{nm}.{fname}'
            return f'*({T} *)((uchar *)&{nm} + {rel})'
        es = TSIZE[ty]
        if len(idx) == 1 and idx[0][1] == es == TSIZE[T] and rel % es == 0:
            i = self.expr(idx[0][0])
            if rel: i = f'{i} + {rel // es}'
            return f'{nm}[{i}]'
        if not idx and rel % es == 0 and es == TSIZE[T]:
            return f'{nm}[{rel // es}]'
        s = f'(uchar *){nm}' + ''.join(' + ' + self.scaled(i, k) for i, k in idx) + (f' + {rel}' if rel else '')
        return f'*({T} *)({s})'

    def stackaddr(self, off):
        p = self.pslot(off)
        if p is not None:
            self.F.usedparams.add(p)
            return f'&param_{p + 1}'
        o = self.F.stackobj.find(off)
        if o is None: raise Unsupported(f'stack address {off}')
        nm, base, ty, n, kind = o
        rel = off - base
        if kind in ('struct', 'scalar', 'vscalar'): return f'&{nm}' if rel == 0 else f'(uchar *)&{nm} + {rel}'
        es = TSIZE[ty]
        if rel == 0: return nm
        if rel % es == 0: return f'&{nm}[{rel // es}]'
        return f'(uchar *){nm} + {rel}'

    def expr(self, e, ctxp=0):
        s, p = self._expr(e)
        return f'({s})' if p < ctxp else s

    def _expr(self, e):
        t = e[0]
        if t == 'c':
            v = sval(e[1])
            if v < 0 and v > -0x10000: return str(v), 14
            if e[1] & 0xffffffff >= 0x80000000: return f'0x{e[1] & 0xffffffff:x}', 16
            return (str(v) if v < 10 else hex(v)), 16
        if t == 'v': return e[1], 16
        if t == 'per': return self.hexaddr(e[1]), 16
        if t == 'g':
            a = e[1]
            if a >= 0x40000000: return self.hexaddr(a), 16
            return f'&{self.token(a, "uchar")}', 14
        if t == 'f':
            nm = self.ctx.fname(e[1]); self.calls.setdefault(nm, ('void', 0))
            return f'(int){nm}', 14
        if t == 'sp': return self.stackaddr(e[1]), 14
        if t == 'str':
            esc = e[1].replace('\\', '\\\\').replace('"', '\\"').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
            return f'"{esc}"', 16
        if t == 'ld': return self.mem(e[1], e[2]), 14
        if t == 'cast': return f'({e[1]}){self.expr(e[2], 14)}', 14
        if t == 'u':
            if e[1] == '-': return f'-{self.expr(e[2], 14)}', 14
            if e[1] == '~': return f'~{self.expr(e[2], 14)}', 14
        if t == 'b':
            op = e[1]
            a, b = e[2], e[3]
            if op == 'ror': raise Unsupported('ror')
            sign = None
            if op in ('/s', '%s', '>>s'): sign = True
            if op in ('/u', '%u', '>>u'): sign = False
            cop = op.rstrip('su') if op not in ('>>s', '>>u') else '>>'
            if op in ('/s', '/u', '%s', '%u'): cop = op[0]
            p = PREC[cop]
            la = self.expr(a, p)
            if sign is not None and self.signed(a) != sign:
                la = f'({"int" if sign else "uint"}){self.expr(a, 14)}'
            rb = self.expr(b, p + 1)
            if op == '*' and is_c(b): rb = str(sval(b[1])) if abs(sval(b[1])) < 256 else hex(b[1])
            if op in ('&', '|', '^') and is_c(b): rb = hex(b[1]) if b[1] > 9 else str(b[1])
            return f'{la} {cop} {rb}', p
        if t == 'call':
            f = e[1]
            if isinstance(f, tuple):
                fs = f'((code *){self.expr(f[1], 14)})'
            else:
                fs = f
                if f not in INTRINSICS:
                    old = self.calls.get(f)
                    self.calls[f] = ('int' if self.F.callret.get(f) else 'void', max(len(e[2]), old[1] if old else 0))
                    self.callmin[f] = min(len(e[2]), self.callmin.get(f, 99))
            return f'{fs}(' + ', '.join(self.expr(x, 2) for x in e[2]) + ')', 15
        if t in ('cmp', 'and', 'or', 'not'):
            s = self.cond(e)
            return s, PREC.get({'cmp': e[1] if t == 'cmp' else '', 'and': '&&', 'or': '||'}.get(t, ''), 14)
        if t == 'bool':
            return self.cond(e[1]), 9
        if t == 'sel':
            return f'{self.cond(e[1], 4)} ? {self.expr(e[2], 4)} : {self.expr(e[3], 3)}', 3
        if t == 'phi':
            w = e[1]
            if w in self.phi: return self._expr(self.phi[w])
            return self.F.wname[w], 16
        if t == 'post':
            nm, ex = e[1], e[2]
            inner = ex[2] if ex[0] == 'cast' else ex
            if is_c(inner[3], 1): return f'{nm}{inner[1] * 2}', 15
            return f'({nm} {inner[1]}= {self.num(inner[3][1])}) {"-" if inner[1] == "+" else "+"} {self.num(inner[3][1])}', 12
        if t == 'asgx':
            lhs = e[1]
            l = lhs[1] if lhs[0] == 'v' else self.mem(lhs[1], lhs[2], True)
            return f'{l} = {self.rhs(lhs, e[2]) if lhs[0] == "v" else self.expr(e[2], 2)}', 2
        raise Unsupported(f'expr {t}')

    def cond(self, c, ctxp=0):
        t = c[0]
        if t == 'cmp':
            op, a, b, sg = c[1], c[2], c[3], c[4]
            p = PREC[op]
            la, rb = self.expr(a, p + 1), self.expr(b, p + 1)
            # an address compared: as an integer
            ptr = lambda e: e[0] in ('g', 'f', 'sp', 'str') or (e[0] == 'b' and e[2][0] in ('g', 'sp'))
            if ptr(a): la = f'({"int" if sg else "uint"})({self.expr(a)})'
            if ptr(b): rb = f'({"int" if sg else "uint"})({self.expr(b)})'
            if op not in ('==', '!='):
                if not sg:
                    if self.typeof(a) != 'uint' and not (is_c(a)):
                        la = f'(uint){self.expr(a, 14)}'
                    if is_c(b) and b[1] & 0x80000000: rb = f'0x{b[1]:x}'
                    if is_c(a) and self.typeof(b) != 'uint': rb = f'(uint){self.expr(b, 14)}'
                else:
                    if self.typeof(a) == 'uint': la = f'(int){self.expr(a, 14)}'
                    if self.typeof(b) == 'uint': rb = f'(int){self.expr(b, 14)}'
            s = f'{la} {op} {rb}'
            return f'({s})' if p < ctxp else s
        if t == 'and':
            s = f'{self.cond(c[1], 5)} && {self.cond(c[2], 6)}'
            return f'({s})' if 5 < ctxp else s
        if t == 'or':
            s = f'{self.cond(c[1], 4)} || {self.cond(c[2], 5)}'
            return f'({s})' if 4 < ctxp else s
        if t == 'not':
            return f'!({self.cond(c[1])})'
        return self.expr(c, ctxp)

    def stmt(self, s):
        if s[0] == 'expr': return self.expr(s[1]) + ';'
        lhs, rhs = s[1], s[2]
        if lhs[0] == 'v':
            # i = i + 1 as i++
            r = self.rhs(lhs, rhs)
            if rhs[0] == 'b' and rhs[1] in ('+', '-') and rhs[2] == lhs and is_c(rhs[3], 1):
                return f'{lhs[1]}{rhs[1] * 2};'
            if rhs[0] == 'cast' and rhs[2][0] == 'b' and rhs[2][1] in ('+', '-') and rhs[2][2] == lhs and is_c(rhs[2][3], 1) \
                    and self.vtype[lhs[1]] == rhs[1]:
                return f'{lhs[1]}{rhs[2][1] * 2};'
            return f'{lhs[1]} = {r};'
        if lhs[0] == 'ld':
            return f'{self.mem(lhs[1], lhs[2], True)} = {self.expr(rhs, 2)};'
        raise Unsupported(f'stmt {s}')

    def rhs(self, lhs, e):
        t = self.vtype[lhs[1]] if lhs[0] == 'v' else 'int'
        if e[0] == 'cast' and e[1] == t: e = e[2]
        return self.expr(e, 2)

# ---------------------------------------------------------------- stack objects


class StackObjs:
    def __init__(self, objs, structs):
        self.objs = sorted(objs, key=lambda o: o[1])
        self.structs = structs

    def find(self, off):
        for o in self.objs:
            nm, base, ty, n, kind = o
            size = TSIZE.get(ty, 0) * n if kind != 'struct' else self.structs[ty][0]
            if base <= off < base + max(size, 1): return o
        return None


def stack_layout(F, ev_stmts, frame_top, pslot, structs, struct_args):
    """Stack locals from the accesses: scalars at offsets loaded and stored directly, arrays and
    known structures (structs file) where an address is taken."""
    direct = collections.defaultdict(set)
    taken = set()
    passed = {}
    def scan(e):
        if not isinstance(e, tuple) or not e: return
        if isinstance(e[0], tuple):
            for z in e: scan(z)
            return
        t = e[0]
        if t == 'ld':
            ad = e[2]
            if ad[0] == 'sp': direct[ad[1]].add(e[1]); return
            if ad[0] == 'b' and ad[1] == '+' and ad[2][0] == 'sp':
                taken.add(ad[2][1]); scan(ad[3]); direct[ad[2][1]].add(e[1]); return
            scan(ad); return
        if t == 'sp': taken.add(e[1]); return
        for k in e[1:]:
            if isinstance(k, tuple): scan(k)
            elif isinstance(k, list):
                for z in k: scan(z)
    for s in ev_stmts:
        if s[0] == 'asg':
            scan(s[1]); scan(s[2])
        elif s[0] == 'expr':
            e = s[1]
            if e[0] == 'call':
                for j, a in enumerate(e[2]):
                    if a[0] == 'sp' and isinstance(e[1], str): passed[a[1]] = (e[1], j)
            scan(e)
        elif s[0] == 'cond': scan(s[1])
    # parameters' homes are not locals
    for o in list(direct):
        if pslot(o) is not None: del direct[o]
    taken = {o for o in taken if pslot(o) is None}
    # aggregates: each taken offset starts one
    objs = []
    bases = sorted(taken)
    used = set()
    for b in bases:
        nxt = min([x for x in bases if x > b] + [frame_top])
        fn = passed.get(b)
        st = None
        if fn and fn[0] in struct_args and struct_args[fn[0]][0] == fn[1]:
            st = struct_args[fn[0]][1]
        inside = [o for o in direct if b <= o < nxt]
        ts = set()
        for o in inside: ts |= direct[o]
        used |= set(inside)
        if st and st in structs and structs[st][0] <= nxt - b:
            objs.append((f'local_{b:x}', b, st, 1, 'struct')); continue
        if len(ts) == 1 and (nxt - b) % TSIZE[next(iter(ts))] == 0 and all((o - b) % TSIZE[next(iter(ts))] == 0 for o in inside):
            ty = next(iter(ts))
        elif not ts and (nxt - b) == 4: ty = 'int'
        else: ty = 'uchar'
        n = (nxt - b) // TSIZE[ty]
        if n == 1 and (not inside or set(inside) == {b}): objs.append((f'local_{b:x}', b, ty, 1, 'scalar'))
        else: objs.append((f'local_{b:x}', b, ty, max(n, 1), 'array'))
    for o, ts in sorted(direct.items()):
        if o in used: continue
        ty = sorted(ts, key=lambda t: -TSIZE[t])[0] if len({TSIZE[t] for t in ts}) > 1 else next(iter(ts))
        if len(ts) > 1 and len({TSIZE[t] for t in ts}) == 1: ty = sorted(ts)[0]
        objs.append((f'local_{o:x}', o, ty, 1, 'vscalar'))
    return StackObjs(objs, structs)

# ---------------------------------------------------------------- the translator


def translate(ctx, a):
    """(C text, meta) of the function at a: as laid out, else with merged statement tails copied
    back into their predecessors and selects made locals."""
    err = None
    # strict: a non-leaf function with locals in r0-r3 (armcc keeps them in r4 and up) is taken
    # for merged statement tails first
    for clone, selvar, strict in ((False, False, True), (True, False, True), (False, False, False),
                                  (False, True, False), (True, False, False), (True, True, False)):
        ctx.clone, ctx.selvar, ctx.strict = clone, selvar, strict
        try:
            return translate1(ctx, a)
        except Unsupported as e:
            if err is None or not strict and err.args and err.args[0] == 'low locals': err = e
        finally:
            ctx.clone = ctx.selvar = ctx.strict = False
    raise err


def translate1(ctx, a):
    F = analyse(ctx, a)
    F.retval = ctx.retval.get(a, False)
    F.poolvals = pool_values(ctx.img, a, F.size)
    F.callret = {}
    dataflow(F, ctx)
    classify(F, ctx)
    if getattr(ctx, 'strict', False) and F.calls and not F.keeps_low and any(k[0] == 'var' and k[1] < 4 for k in F.kind.values()):
        raise Unsupported('low locals')
    ev = Eval(F, ctx)
    for b in F.blocks:
        b.retval = None
        ev.block(b)
    if getattr(ctx, 'strict', False) and F.calls and not F.keeps_low and any(k[0] == 'var' and k[1] < 4 for k in F.kind.values()):
        raise Unsupported('low locals')
    R = Render(F, ctx)
    for nm in ev.calls:
        tgt = ctx.by_name.get(nm)
        F.callret[nm] = ctx.retval.get(tgt, False) if tgt is not None else ev.calls[nm][1]
        if ev.calls[nm][1]: F.callret[nm] = True
    np0 = max([k + 1 for k in F.params] + [ctx.nparams.get(a, 0)] + [v + 1 for v in F.pcopy.values()])
    # pushed r0-r3 are the parameters' homes when there is a frame below them (sub sp), else
    # frame space for locals
    frame_top = F.frame + 4 * len(F.lowpush) if not (F.frame and any(r < np0 for r in F.lowpush)) else F.frame
    allst = []
    for b in F.blocks:
        allst += b.stmts
        if b.term[0] == 'cond': allst.append(('cond', b.term[1]))
        if b.term[0] == 'sw' and b.term[1] is not None: allst.append(('cond', b.term[1]))
        for e in b.state.values(): allst.append(('cond', e))
        if getattr(b, 'retval', None) is not None: allst.append(('cond', b.retval))
    F.nparams = max([k + 1 for k in F.params] + [ctx.nparams.get(a, 0)])
    F.usedparams = set()
    F.stackobj = stack_layout(F, allst, frame_top, R.pslot2, ctx.structs, ctx.struct_args)
    for o in F.stackobj.objs:
        if o[1] >= frame_top: raise Unsupported('stack parameters')
    # types of locals: from what is assigned to them
    assigned = collections.defaultdict(list)
    for s in allst:
        if s[0] == 'asg' and s[1][0] == 'v': assigned[s[1][1]].append(s[2])
    pnames = {f'param_{k + 1}' for k in range(4)}
    for _ in range(3):
        for v, es in assigned.items():
            ts = set()
            for e in es:
                if e[0] == 'c': continue
                if e[0] == 'v' and e[1] in pnames: ts.add('int'); continue
                ts.add(R.typeof(e))
            if len(ts) == 1 and next(iter(ts)) in NARROW:
                t = next(iter(ts))
                lo, hi = RANGE[t]
                if all(not is_c(e) or lo <= sval(e[1]) <= hi for e in es): R.vtype[v] = t
            elif ts == {'uint'}: R.vtype[v] = 'uint'
    Sh = Shape(F, R)
    R.phi = Sh.phi
    lines = Sh.run()
    text_body = '\n'.join(lines)
    for lb, c in Sh.labels.items():
        if c > 0 and f'L_{lb}:;' not in text_body: raise Unsupported('label not placed')
    # the function's own parameters and return
    np = max([F.nparams] + [k + 1 for k in F.usedparams])
    ptypes = [f'int param_{k + 1}' for k in range(np)]
    rt = 'int' if F.retval else 'void'
    if F.retval:
        rts = {R.typeof(b.retval) for b in F.blocks if getattr(b, 'retval', None) is not None}
        if len(rts) == 1 and next(iter(rts)) in NARROW: rt = next(iter(rts))
    head = f'{rt} {F.name}({", ".join(ptypes) if ptypes else "void"})'
    # declarations: register locals by register, then stack locals from the top down
    decls = []
    seen = set()
    for w, k in sorted(F.kind.items(), key=lambda kv: (kv[1][1] if len(kv[1]) > 1 and isinstance(kv[1][1], int) else 99, F.wname.get(kv[0], ''))):
        if k[0] not in ('var', 'select'): continue
        nm = F.wname[w]
        if nm in seen: continue
        if k[0] == 'select' and w in Sh.phi: continue
        if not re.search(r'\b' + nm + r'\b', text_body): continue
        seen.add(nm)
        decls.append(f'  {R.vtype[nm]} {nm};')
    for nm, base, ty, n, kind in sorted(F.stackobj.objs, key=lambda o: -o[1]):
        if kind == 'array': decls.append(f'  {ty} {nm}[{n}];')
        elif kind == 'vscalar': decls.append(f'  volatile {ty} {nm};')
        else: decls.append(f'  {ty} {nm};')
    for w, k in F.kind.items():
        if k[0] == 'select' and w not in Sh.phi and re.search(r'\b' + F.wname[w] + r'\b', text_body):
            raise Unsupported('select not resolved')
    text = head + '\n{\n' + '\n'.join(decls) + ('\n' if decls else '') + text_body + '\n}\n'
    text = f'/* draft of {F.name} at 0x{a:08X}, 0x{F.size:X} bytes (o0c) */\n' + text
    gl = {tok: [ga, t, TSIZE[t]] for (ga, t), tok in R.tokens.items()}
    calls = {}
    for nm, (rt2, n) in R.calls.items():
        if R.callmin.get(nm, n) != n:       # call sites disagree: an old-style declaration
            calls[nm] = f'{rt2} {nm}()'
        else:
            calls[nm] = f'{rt2} {nm}(' + (', '.join(f'int param_{i + 1}' for i in range(n)) or 'void') + ')'
    structs = sorted({o[2] for o in F.stackobj.objs if o[4] == 'struct'})
    meta = dict(name=F.name, addr=a, size=F.size, sig=head, calls=calls, globals=gl, pool=F.poolvals,
                structs=structs, source='o0c')
    return text, meta


def global_facts(ctx, rows=None):
    """Parameter counts and returned values of every function (a first pass over the image)."""
    funcs = {}
    for a, n, nm in (rows or ctx.rows):
        try:
            funcs[a] = analyse(ctx, a)
        except Exception:
            pass
    img = ctx.img
    # registers among r1-r3 and ip each function writes, with what its callees write
    own, callees = {}, {}
    for a, F in funcs.items():
        w, cs = set(), set()
        for ops in F.ops:
            for o in ops:
                if o[0] == 'set': w.add(o[1])
                elif o[0] == 'pset': w.add(o[2])
                elif o[0] == 'call': cs.add(o[1])
                elif o[0] == 'icall': w |= SCRATCH
        own[a], callees[a] = w & SCRATCH, cs
    clob = dict(own)
    changed = True
    while changed:
        changed = False
        for a in funcs:
            new = set(clob[a])
            for c in callees[a]: new |= clob.get(c, SCRATCH)
            if new != clob[a]: clob[a] = new; changed = True
    ctx.clobbers = {a: frozenset(v) for a, v in clob.items()}
    # parameter counts: prologue copies (exact when the function calls out), else the entry
    # registers read before written
    for a, F in funcs.items():
        n = param_count(img, a, ctx.size[a])
        if n == 0 or not F.calls:
            # leaf: r0-r3 read before any write, along any path (approximation: in layout order)
            written, read = set(), set()
            for i, ops in enumerate(F.ops):
                for o in ops:
                    u = set()
                    if o[0] == 'set': u = regs_in(o[2])
                    elif o[0] in ('st', 'fl'): u = regs_in(o[2]) | regs_in(o[3])
                    elif o[0] == 'cbz': u = {o[1]}
                    elif o[0] == 'sw' and o[1] is not None: u = {o[1]}
                    elif o[0] == 'pset': u = regs_in(o[3])
                    read |= {r for r in u if r < 4 and r not in written}
                    if o[0] == 'set': written.add(o[1])
                    if o[0] in ('call', 'icall'): written |= {0, 1, 2, 3}
            n = max(n, max([r + 1 for r in read] + [0]))
            # a leaf keeps its (unused) parameters' registers: scratch starts above them
            touched = set(read) | {r for r in written if r < 4}
            if not F.calls and touched:
                n = max(n, min(touched))
        if F.pcopy: n = max(n, max(F.pcopy.values()) + 1)
        if F.frame and F.lowpush: n = max(n, max(F.lowpush) + 1)      # pushed homes under a frame
        # stack parameters read anywhere
        for ops in F.ops:
            for o in ops:
                for e in ([o[2]] if o[0] == 'set' else []):
                    for x in walk(e):
                        if x[0] == 'ld' and x[2][0] == 'sp' and x[2][1] >= F.argbase:
                            n = max(n, 5 + (x[2][1] - F.argbase) // 4)
        ctx.nparams[a] = n
    # returned values: a caller reads r0 after the call before writing it
    def ud(F, j):
        u, d = set(), set()
        for x in F.ops[j]:
            if x[0] == 'set': u |= regs_in(x[2]); d.add(x[1])
            elif x[0] == 'pset': u |= regs_in(x[3]) | {x[2]}; d.add(x[2])
            elif x[0] in ('st', 'fl'): u |= regs_in(x[2]) | regs_in(x[3])
            elif x[0] == 'cbz': u.add(x[1])
            elif x[0] == 'sw' and x[1] is not None: u.add(x[1])
            elif x[0] == 'call':
                if ctx.nparams.get(x[1], 0) >= 1: u.add(0)
                d.add(0)
            elif x[0] == 'icall': u |= {0, x[1]}; d.add(0)
        return u, d
    for a, F in funcs.items():
        for i, ops in enumerate(F.ops):
            if ops[0][0] != 'call': continue
            t = ops[0][1]
            for j in range(i + 1, len(F.ops)):
                u, d = ud(F, j)
                if 0 in u: ctx.retval[t] = True; break
                if 0 in d or F.ops[j][-1][0] in ('b', 'bc', 'cbz', 'sw', 'ret'): break
                if j + 1 < len(F.ops) and F.bof.get(j + 1, -1) != F.bof.get(j, -1): break
    # local evidence: a block ending in (or branching to) the epilogue whose last write of r0 is
    # not a call and is not read after it
    for a, F in funcs.items():
        if a in ctx.retval: continue
        for b in F.blocks:
            if b.epi or not any(F.blocks[s].epi for s in b.succ): continue
            for j in range(b.hi - 1, b.lo - 1, -1):
                u, d = ud(F, j)
                if 0 in u and 0 not in d: break
                if 0 in d:
                    st = [o for o in F.ops[j] if o[0] == 'set' and o[1] == 0]
                    if st and (0 not in u or st[0][2][0] != 'r'):
                        ctx.retval[a] = True
                    break
            if ctx.retval.get(a): break
    # a leaf that copies its first parameter out of r0 in its prologue: it needs r0 for its result
    # (a leaf keeps its parameters where they arrive unless a register is needed)
    for a, F in funcs.items():
        if not F.calls and 0 in F.pcopy.values(): ctx.retval[a] = True
    # a value computed into r0 that reaches an epilogue unused (a leaf's result local in r0)
    for a, F in funcs.items():
        if a in ctx.retval: continue
        try:
            F.retval = False
            dataflow(F, ctx)
        except Exception: continue
        for b in F.blocks:
            if not b.epi: continue
            for d in F.IN[b.k].get(0, ()):
                if d[0] in ('entry', 'clob') or not isinstance(d[0], int): continue
                op = [o for o in F.ops[d[0]] if o[0] == 'set' and o[1] == 0]
                if op and not F.defuses.get(d) and op[0][2][0] not in ('r',):
                    ctx.retval[a] = True
            if ctx.retval.get(a): break
    return funcs

# ---------------------------------------------------------------- units and scores


def unit(ctx, text, meta, data_names=None):
    """The draft as a compilable C file. Each global token goes through the literal the image
    loads: a token alone at its pool symbol's own address becomes a typed extern of that symbol
    (a named variable, as most sources have them: armcc orders operands differently for a named
    variable than for a cast pointer, compiler.md); the others a typed macro over the symbol
    declared as a byte array. Symbols are the map's names where a data row starts at the address,
    else its placeholder (g_ RAM, c_ flash), which the matcher resolves by address. Callees get
    the prototypes the draft saw."""
    data_names = data_names or {}
    pool = set(meta.get('pool', []))
    by_base = collections.defaultdict(list)
    for tok, (ga, t, sz) in sorted(meta['globals'].items()):
        b, off = symbol_for(ga, pool)
        by_base[b].append((off, t, tok))
    out = [TYPEDEFS]
    for s in meta.get('structs', []):
        size, fields = ctx.structs[s]
        body, at, k = [], 0, 0
        for off, fs, fname in sorted(fields):
            if off > at: body.append(f'uchar pad{k}[{off - at}];'); k += 1
            body.append(f'{ {1: "uchar", 2: "ushort", 4: "uint"}.get(fs, "uint") } {fname};'); at = off + fs
        if size > at: body.append(f'uchar pad{k}[{size - at}];')
        out.append(f'typedef struct {{ {" ".join(body)} }} {s};')
    for b, toks in sorted(by_base.items()):
        sym = data_names.get(b) or (('g_' if ctx.in_ram(b) else 'c_') + f'{b:08X}')
        if len(toks) == 1 and toks[0][0] == 0:
            _, t, tok = toks[0]
            out.append(f'extern {t} {sym};')
            out.append(f'#define {tok} {sym}')
            continue
        out.append(f'extern uchar {sym}[];')
        for off, t, tok in toks:
            out.append(f'#define {tok} (*({t} *)({sym} + {off}))')
    for nm, proto in sorted(meta['calls'].items()):
        if nm != meta['name']: out.append(f'extern {proto};')
    out.append('')
    return '\n'.join(out) + '\n' + text


def file_unit(ctx, drafts, data_names=None):
    """One C file from drafts [(text, meta)] of a source file's functions, in address order (the
    order armcc laid them out without --split_sections). Global tokens are renamed to one name per
    address and type across the file, every function of the file is declared first (armcc uses
    what it knows of a callee defined anywhere in the file, before or after its caller), and
    callees outside the file get prototypes (old-style where the drafts disagree)."""
    tokname, glob, pool, structs = {}, {}, set(), set()
    texts = []
    defined = {m['name'] for _, m in drafts}
    calls = collections.defaultdict(set)
    for text, meta in drafts:
        ren = {}
        for tok, (ga, t, sz) in sorted(meta['globals'].items()):
            key = (ga, t)
            if key not in tokname:
                same = any(k[0] == ga for k in tokname)
                tokname[key] = f'DAT_{ga:08x}' + (f'_{t}' if same else '')
            ren[tok] = tokname[key]; glob[tokname[key]] = [ga, t, sz]
        if ren:
            text = re.sub(r'\bDAT_[0-9a-f]{8}(?:_\w+)?\b', lambda m: ren.get(m.group(0), m.group(0)), text)
        texts.append(text)
        pool |= set(meta.get('pool', [])); structs |= set(meta.get('structs', []))
        for nm, proto in meta['calls'].items():
            if nm not in defined: calls[nm].add(proto)
    protos = {nm: next(iter(ps)) if len(ps) == 1 else re.sub(r'\(.*\)$', '()', sorted(ps)[0]) for nm, ps in calls.items()}
    head = '\n'.join(m['sig'] + ';' for _, m in drafts) + '\n\n'
    meta = dict(name=None, globals=glob, pool=sorted(pool), calls=protos, structs=sorted(structs))
    return unit(ctx, head + '\n'.join(texts), meta, data_names)


def norm(ins):
    """Instructions as text for a score: pc-relative offsets masked, branch targets as instruction
    indices, call targets masked, tables as their targets' indices. A `b` over an intermediate
    literal pool to the next instruction is placement, not source, and is left out."""
    at = {x.addr: k for k, x in enumerate(ins)}
    out = []
    for k, x in enumerate(ins):
        mn, _, op = x.raw.partition(' ')
        if x.mn == 'b' and not x.cc and x.ops and k + 1 < len(ins) and x.ops[-1][1] == ins[k + 1].addr and \
                ins[k + 1].addr != x.addr + x.size:
            continue
        if x.tab is not None:
            out.append(f'{x.mn} ' + ','.join(str(at.get(t, '?')) for t in x.tab)); continue
        if x.mn in ('bl', 'blx') and '#' in op: out.append(f'{mn} ?'); continue
        if x.mn in ('b', 'cbz', 'cbnz') and '#' in op:
            t = num(op.split('#')[-1])
            op = re.sub(r'#(0x[0-9a-f]+|\d+)$', f'L{at.get(t, "?")}', op)
        op = re.sub(r'\[pc, #-?(0x[0-9a-f]+|\d+)\]', '[pc]', op)
        if x.mn == 'adr': op = re.sub(r'#-?(0x[0-9a-f]+|\d+)$', '#?', op)
        out.append(f'{mn} {op}'.strip())
    return out


def score_lists(got, want):
    """Instructions that differ between two normalised lists (a diff: an insertion costs its
    length, not everything after it)."""
    sm = difflib.SequenceMatcher(None, got, want, autojunk=False)
    return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != 'equal')


def compiled_norm(got, pool):
    """norm of a compiled function's bytes (armvariant.func_bytes: bytes, pool word offsets)."""
    return norm(decode(Mem(0, got), 0, len(got), set(pool), data=()))


def image_norm(ctx, a):
    return norm(decode(ctx.img, a, ctx.size[a], ctx.pw))

# ---------------------------------------------------------------- source files


def files(ctx):
    """[[(addr, size, name)]] in address order: the source files of an image built without
    --split_sections. A file's functions sit in source order with their literal pools after them
    (one at the end, or an intermediate one where the end is out of reach, even inside a
    function), so a pool does not end a file. A boundary lies between two rows only where no load
    crosses it (no function before it loads a literal or adr block after it, none after it loads
    one before it) and at a 4-byte boundary (an object's .text is 4-aligned when it holds a pool).
    The loads only say where a file may end: a caller that knows its callee's registers shows a
    boundary they do not (compiler.md). Library rows are in no group."""
    tg = {}
    rows = [r for r in ctx.rows]
    for a, n, name in rows:
        tg[a] = {x if isinstance(x, int) else x[1] for x in literal_targets(ctx.img, a, n)}
    out, cur = [], []
    for k, (a, n, name) in enumerate(rows):
        if a in ctx.lib:
            if cur: out.append(cur); cur = []
            continue
        if cur and a % 4 == 0:
            lo = cur[0][0]
            before = any(t >= a for b, _, _ in cur for t in tg[b] if t < a + 0x100000)
            after = any(lo <= t < a for b, _, _ in rows[k:] if b not in ctx.lib for t in tg[b])
            if not before and not after:
                out.append(cur); cur = []
        cur.append((a, n, name))
    if cur: out.append(cur)
    return out

# ---------------------------------------------------------------- command line


def opts_of(cfg):
    return cfg.raw.get('o0c', {}).get('opts', DEFAULT_OPTS)


def data_names(cfg):
    return {r.addr: r.name for r in armmap.load(os.path.join(cfg.map, 'data.tsv'))}


def todo(ctx, only=None):
    return [(a, n, nm) for a, n, nm in ctx.rows if a not in ctx.lib and (not only or a in only)]


def gen(cfg, ctx, only=None, log=print):
    out = os.path.join(cfg.build, 'o0c')
    os.makedirs(out, exist_ok=True)
    if only is None:
        for fn in os.listdir(out):
            if re.match(r'^[0-9A-F]{8}\.(c|json)$', fn): os.remove(os.path.join(out, fn))
    t0 = time.time()
    ok, bad, fails = 0, collections.Counter(), []
    for a, n, nm in todo(ctx, only):
        try:
            text, meta = translate(ctx, a)
        except Unsupported as e:
            bad[re.sub(r'\b(0x)?[0-9A-Fa-f]{8}\b|\d+', 'N', str(e))[:60]] += 1; fails.append((a, str(e))); continue
        except Exception as e:
            bad['crash ' + type(e).__name__ + ' ' + str(e)[:40]] += 1; fails.append((a, 'crash ' + repr(e))); continue
        with open(os.path.join(out, f'{a:08X}.c'), 'w') as f: f.write(text)
        with open(os.path.join(out, f'{a:08X}.json'), 'w') as f: json.dump(meta, f, indent=1)
        ok += 1
    with open(os.path.join(out, 'failures.tsv'), 'w') as f:
        for a, e in fails: f.write(f'{a:08X}\t{e}\n')
    log(f'{ok} drafts in {os.path.relpath(out, cfg.root)}, {sum(bad.values())} not translated ({time.time() - t0:.0f} s)')
    for k, v in bad.most_common(25): log(f'  {v:4d}  {k}')
    return ok, fails


def score(cfg, ctx, only=None, jobs=1, batch=40, log=print):
    """Compile each draft's unit (batches of 40 per armcc call, a Wine prefix per job under
    EXHUME_ARMCC_RUNNER=wine) and score it against the image: {addr: score or None}. Writes
    build/o0c/score.tsv."""
    import armvariant as V
    from concurrent.futures import ThreadPoolExecutor
    out = os.path.join(cfg.build, 'o0c')
    names = data_names(cfg)
    items = []
    for a, n, nm in todo(ctx, only):
        p = os.path.join(out, f'{a:08X}.json')
        if not os.path.exists(p): continue
        with open(p) as f: meta = json.load(f)
        with open(os.path.join(out, f'{a:08X}.c')) as f: text = f.read()
        items.append((meta, unit(ctx, text, meta, names)))
    opts = opts_of(cfg) + ' --split_sections'
    chunks = [items[i:i + batch] for i in range(0, len(items), batch)]
    def one(k):
        # parallel jobs under host Wine each get a prefix of their own (one wineserver serialises
        # its clients); one job uses the caller's
        prefix = os.path.join(cfg.build, 'o0c', 'wine', f'job{k % jobs}') \
            if jobs > 1 and os.environ.get('EXHUME_ARMCC_RUNNER') == 'wine' else None
        runner = V.Runner(cfg, prefix)
        lst, clog = V.compile_batch(runner, [u for _, u in chunks[k]], opts, os.path.join(out, 'work', f'b{k}'))
        res = []
        for (meta, _), l in zip(chunks[k], lst):
            got, pool = V.func_bytes(l, meta['name']) if l else (b'', set())
            # instructions only: where the pool lies is placement (with --split_sections each
            # function has its own; the image's may be shared and far away)
            res.append((meta['addr'], score_lists(compiled_norm(got, pool), image_norm(ctx, meta['addr'])) if got else None))
        return res
    scores = {}
    with ThreadPoolExecutor(max(jobs, 1)) as ex:
        for res in ex.map(one, range(len(chunks))):
            scores.update(res)
    with open(os.path.join(out, 'score.tsv'), 'w') as f:
        f.write('addr\tname\tsize\tscore\n')
        for a in sorted(scores):
            f.write(f'{a:08X}\t{ctx.fname(a)}\t{ctx.size[a]:X}\t{"-" if scores[a] is None else scores[a]}\n')
    exact = sum(1 for s in scores.values() if s == 0)
    log(f'{len(scores)} drafts scored: {exact} exact, {sum(1 for s in scores.values() if s is None)} did not compile '
        f'({os.path.relpath(os.path.join(out, "score.tsv"), cfg.root)})')
    return scores


def probe(cfg, path):
    import armvariant as V
    with open(path) as f: src = TYPEDEFS + f.read()
    lst, log = V.compile_batch(V.Runner(cfg), [src], opts_of(cfg) + ' --split_sections', os.path.join(cfg.build, 'o0c', 'probe'))
    if not lst[0]: print(log); return 1
    for l in lst[0].splitlines():
        m = V.LINE.match(l)
        if l.startswith('** Section') and "'i." in l: print(l.split("'")[1])
        elif m: print(f'   {m.group(1)[-3:]}  {m.group(2):8}  {m.group(3).strip()}')
    return 0


def main(argv):
    import config
    path, a = config.pop_config(argv)
    if not a: sys.exit(__doc__)
    cmd = a[0]
    opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
    cfg = config.load(path)
    if cmd == 'probe': return probe(cfg, a[1])
    ctx = Ctx.from_config(cfg)
    only = {ctx.addr(x) for x in opt('--only').split(',')} if opt('--only') else None
    if cmd in ('gen', 'one', 'unit', 'score', 'blocks', 'file'): global_facts(ctx)
    if cmd == 'gen': gen(cfg, ctx, only); return 0
    if cmd == 'score':
        score(cfg, ctx, only, int(opt('--jobs', 1))); return 0
    if cmd == 'files':
        for g in files(ctx):
            print(f'{g[0][0]:08X}-{g[-1][0] + g[-1][1]:08X}  {len(g):3d} functions  ' + ' '.join(nm for _, _, nm in g[:6]) +
                  (' ...' if len(g) > 6 else ''))
        return 0
    if len(a) < 2: sys.exit(__doc__)
    addr = ctx.addr(a[1])
    if addr not in ctx.size: sys.exit(f'0x{addr:08X}: no map row starts there')
    if cmd == 'asm':
        for x in decode(ctx.img, addr, ctx.size[addr], ctx.pw):
            print(f'{x.addr:08X}  {x.raw}' + (f'  table {[hex(t) for t in x.tab]}' if x.tab else ''))
        return 0
    if cmd == 'blocks':
        F = analyse(ctx, addr); F.retval = ctx.retval.get(addr, False)
        F.poolvals = pool_values(ctx.img, addr, F.size); F.callret = {}
        dataflow(F, ctx); classify(F, ctx)
        ev = Eval(F, ctx)
        for b in F.blocks: b.retval = None; ev.block(b)
        for b in F.blocks:
            t = b.term
            ts = t[0] + ('' if len(t) < 2 else ' ' + ' '.join(str(x) for x in t[1:] if not isinstance(x, tuple)))
            print(f'B{b.k:<3} {F.ins[b.lo].addr:08X} stmts={len(b.stmts)} {ts} pred={b.pred}' + (' epi' if b.epi else ''))
        return 0
    if cmd in ('one', 'unit'):
        try:
            text, meta = translate(ctx, addr)
        except Unsupported as e:
            print('unsupported:', e); return 1
        print(unit(ctx, text, meta, data_names(cfg)) if cmd == 'unit' else text)
        return 0
    if cmd == 'file':
        group = next((g for g in files(ctx) if any(r[0] == addr for r in g)), None)
        if group is None: sys.exit(f'0x{addr:08X}: a library row, in no source file')
        drafts, bad = [], []
        for a2, _, nm in group:
            try: drafts.append(translate(ctx, a2))
            except Unsupported as e: bad.append(f'{nm}: {e}')
        if bad: sys.exit('not translated, so no whole file:\n  ' + '\n  '.join(bad))
        text = f'/* opts: {opts_of(cfg)} */\n' + file_unit(ctx, drafts, data_names(cfg))
        if opt('--write'):
            with open(opt('--write'), 'w') as f: f.write(text)
        else: print(text)
        return 0
    sys.exit(__doc__)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
