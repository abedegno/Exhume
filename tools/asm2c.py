"""A static recompiler for hand-written 16-bit x86 assembly modules: translate each module to C for
a native port, instruction for instruction (docs/port.md, "The static recompiler").

    python3 tools/asm2c.py [--config PATH]             write every module of the spec and the module table
    python3 tools/asm2c.py [--config PATH] --check     exit 1 if a written file is out of date
    python3 tools/asm2c.py [--config PATH] --list M    print module M's instructions with their addresses
    python3 tools/asm2c.py [--config PATH] --only M    write module M alone (the table still sees every module)

What to translate is a project file ([asm2c] spec, a Python file; UW2's is
examples/uw2/port/asm2c_spec.py): MODULES, (source, output, C name) in segment order; MODTAB,
the module table's path; CODESEGS, {target prefix: (EXE paragraph, C name of the host block
holding the segment's bytes, or None)}; OVERRIDES, {(module, address): (C, why)}; HANDWRITTEN,
{module: [(lo, hi, where)]}, the ranges the port has as hand-written C; PATCH_OVERRIDDEN, the
(paragraph, address) of patched instructions an override handles; and EXTERNS, {module:
[declaration lines]} for the C names overrides use. The overrides are C for the game's own
instructions, so a project whose spec is published apart from its decompilation keeps them in
its own repository and names that file as [asm2c] overrides (a Python file with OVERRIDES and,
optionally, PATCH_OVERRIDDEN, merged over the spec's; UW2: Underworld Exhumed's uw2/tools/asm2c.py).

Each module is read from its matched .ASM source and from the bytes the gate proves that source
assembles to (the user's EXE, at the module's place in its target table, as tools/match.py reads
it): the source gives the labels, the procs, the data and the comments; the bytes, decoded with
iced_x86, give each instruction's address, length and operands exactly. A source line and its
decoded instruction must agree on the mnemonic, or the tool stops. TASM's padding nop after a
forward jump it sized for the worst case is recognised, and so are instructions the source
writes as bytes (a far jump TASM would shorten, a db that runs as a test).

The C is one function per module, `uint32_t asm_mod_NAME(uint16_t entry)`, which starts at the
entry's label and runs the instructions in order on the machine of runtime/port/x86/asmrt.h:
the registers and flags are the 386's, memory is reached through the segment registers, the
stack is the emulated one in DOS memory. Every instruction is a label, since code jumps into
unrolled loops through tables. A flag is computed only where some instruction that can see it
reads it (a backward liveness pass over the module's flow graph, every exit counting as a
reader). Calls go through asm_call and asm_callf, jumps out of the module and returns go back
to the caller with the code asm_exec expects (asmrt.h).

Self-modifying code becomes data, with no JIT: an instruction whose immediate or displacement
another instruction writes through cs: reads it from the code block at run time (the code
segments are host memory holding the EXE's bytes); a conditional jump whose opcode is written
tests the condition the written opcode names. The tool refuses an instruction with a patched
byte it does not read. What the generic rules cannot express is in OVERRIDES: C written by hand
for one instruction, with the reason (UW2: a routine body copied over another, a patched add
or sub, a patched ret). Code generated at run time is interpreted, not executed: an override
calls the project's interpreter of the few instructions the generator writes (UW2: SCALEBM's
sprite scalers, gfx/scalebm_code.c). A DGROUP offset or segment (the [binary] dgroup_para)
stops the tool: the port's DGROUP is C objects, so each needs an override.

HANDWRITTEN ranges are not translated: a jump or fall-through into one leaves the module
(ASM_JMP), and the runtime sends any call or jump there to the project's glue, which does the
routine's work on the emulated registers. The module table (MODTAB) lists the modules, the
ranges, and every place in them the remaining translation calls or jumps to (asm_hand_calls),
which the port checks at start-up has C ("One implementation per routine" in docs/port.md).

The EXE is the user's own copy ([binary] exe); the generated C holds no byte of it that is not
also in the committed .ASM sources, so it may be committed beside them.
"""
import os, re, sys, argparse
import iced_x86 as I

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
import portcfg

CFG, ARGV = portcfg.cli() if __name__ == '__main__' else (None, None)
root = None
EXE = None
MODULES, MODTAB, CODESEGS, OVERRIDES, HANDWRITTEN, PATCH_OVERRIDDEN, EXTERNS = [], None, {}, {}, {}, set(), {}
DGROUP_PARA = None


def configure(cfg):
    """Read the project's spec ([asm2c] spec) into this module's tables."""
    global root, EXE, MODULES, MODTAB, CODESEGS, OVERRIDES, HANDWRITTEN, PATCH_OVERRIDDEN, EXTERNS, DGROUP_PARA, TARGETS
    a = portcfg.asm2c(cfg)
    if not a.spec: sys.exit('asm2c.py: [asm2c] spec is not set')
    m = portcfg.load_module(a.spec, 'asm2c_spec')
    root = cfg.root; EXE = cfg.exe_path; TARGETS = cfg.targets
    MODULES = m.MODULES; MODTAB = m.MODTAB; CODESEGS = m.CODESEGS; OVERRIDES = m.OVERRIDES
    HANDWRITTEN = m.HANDWRITTEN; PATCH_OVERRIDDEN = set(getattr(m, 'PATCH_OVERRIDDEN', ()))
    EXTERNS = getattr(m, 'EXTERNS', {})
    if a.overrides:             # [asm2c] overrides: the project's own file of them
        if not os.path.exists(a.overrides): sys.exit(f'asm2c.py: [asm2c] overrides: no {a.overrides}')
        o = portcfg.load_module(a.overrides, 'asm2c_overrides')
        OVERRIDES = {**OVERRIDES, **o.OVERRIDES}
        PATCH_OVERRIDDEN |= set(getattr(o, 'PATCH_OVERRIDDEN', ()))
    DGROUP_PARA = cfg.dgroup_para


TARGETS = None

R = {getattr(I.Register, n): n for n in dir(I.Register) if n.isupper()}
MN = {getattr(I.Mnemonic, n): n.lower() for n in dir(I.Mnemonic) if n.isupper()}
OK_ = I.OpKind
CC = I.ConditionCode
FC = I.FlowControl
FMT = I.Formatter(I.FormatterSyntax.MASM)
FMT.uppercase_hex = True

CF_, ZF_, SF_, OF_ = 1, 2, 4, 8
ALLF = 15
CCOND = {CC.O: ('OF', OF_), CC.NO: ('!OF', OF_), CC.B: ('CF', CF_), CC.AE: ('!CF', CF_), CC.E: ('ZF', ZF_),
         CC.NE: ('!ZF', ZF_), CC.BE: ('CF || ZF', CF_ | ZF_), CC.A: ('!CF && !ZF', CF_ | ZF_),
         CC.S: ('SF', SF_), CC.NS: ('!SF', SF_), CC.L: ('SF != OF', SF_ | OF_), CC.GE: ('SF == OF', SF_ | OF_),
         CC.LE: ('ZF || SF != OF', ZF_ | SF_ | OF_), CC.G: ('!ZF && SF == OF', ZF_ | SF_ | OF_)}

SYN = {'retn': 'ret', 'xlatb': 'xlat', 'sal': 'shl', 'jc': 'jb', 'jnc': 'jae', 'jnb': 'jae', 'jz': 'je', 'jnz': 'jne',
       'jna': 'jbe', 'jnbe': 'ja', 'jnae': 'jb', 'jpe': 'jp', 'jpo': 'jnp', 'jnge': 'jl', 'jnl': 'jge', 'jng': 'jle',
       'jnle': 'jg', 'loopz': 'loope', 'loopnz': 'loopne', 'int3': 'int'}


def die(msg):
    raise SystemExit('asm2c.py: ' + msg)


def load_exe():
    exe = open(EXE, 'rb').read()
    hdr = (exe[8] | exe[9] << 8) * 16
    n = exe[6] | exe[7] << 8; at = exe[0x18] | exe[0x19] << 8
    rel = set()
    for i in range(n):
        o = exe[at + 4 * i] | exe[at + 4 * i + 1] << 8; s = exe[at + 4 * i + 2] | exe[at + 4 * i + 3] << 8
        rel.add(hdr + s * 16 + o)
    return exe, hdr, rel


def load_target(name):
    for l in open(os.path.join(TARGETS, name + '.tsv')):
        m = re.match(r'# segment \S+ base 0x([0-9A-Fa-f]+) size 0x([0-9A-Fa-f]+)(?: org 0x([0-9A-Fa-f]+))?', l)
        if m: return int(m.group(1), 16), int(m.group(2), 16), int(m.group(3) or '0', 16)
    die('no segment line in targets/%s.tsv' % name)


def split_items(s):
    """Split a db/dw/dd operand list at top-level commas."""
    out, cur, q, depth = [], '', None, 0
    for ch in s:
        if q:
            cur += ch
            if ch == q: q = None
        elif ch in "'\"": q = ch; cur += ch
        elif ch == '(': depth += 1; cur += ch
        elif ch == ')': depth -= 1; cur += ch
        elif ch == ',' and depth == 0: out.append(cur.strip()); cur = ''
        else: cur += ch
    if cur.strip(): out.append(cur.strip())
    return out


def num(t):
    t = t.strip()
    m = re.fullmatch(r'([0-9][0-9A-Fa-f]*)[hH]', t)
    if m: return int(m.group(1), 16)
    if re.fullmatch(r'[0-9]+', t): return int(t)
    return None


def data_size(kind, ops):
    unit = {'db': 1, 'dw': 2, 'dd': 4}[kind]
    n = 0
    for it in split_items(ops):
        m = re.fullmatch(r'(\S+)\s+dup\s*\((.*)\)', it, re.I)
        if m:
            k = num(m.group(1))
            if k is None: die('dup count ' + it)
            n += k * data_size(kind, m.group(2))
        elif it[0] in "'\"" and kind == 'db': n += len(it) - 2
        else: n += unit
    return n


class Item:
    def __init__(s, kind, line, src, comment=''):
        s.kind = kind; s.line = line; s.src = src; s.comment = comment
        s.addr = None; s.len = 0; s.ins = None; s.labels = []; s.data = None; s.co = None


DGROUP_NAMES = {}


def parse(path):
    """The items of the module's code segment: labels, instructions and data, with the comment
    block before each proc."""
    lines = open(os.path.join(root, path), encoding='latin1').read().split('\n')
    text = '\n'.join(lines)
    target = re.search(r'/\*\s*target:\s*(\w+)\s*\*/', text).group(1)
    items = []; incode = False; pending = []; block = []
    # names declared in DGROUP's data (an extrn inside _DATA or .data): C objects in the port,
    # which an operand naming one cannot reach through a segment register (emit)
    indata = False; dgroup = set()
    for no, raw in enumerate(lines, 1):
        code, _, comment = raw.partition(';')
        if "'" in code and ';' in raw:
            # a ; inside a quoted string is not a comment
            m = re.match(r"^([^;']*'[^']*'[^;]*)(;.*)?$", raw)
            if m: code = m.group(1); comment = (m.group(2) or ';')[1:]
        s = code.strip()
        if re.match(r'_DATA\s+segment\b|\.data\b', s, re.I): indata = True
        elif re.match(r'_DATA\s+ends\b|\.code\b|\w+\s+segment\b', s, re.I): indata = False
        if indata:
            for n in re.findall(r'\bextrn\s+(.*)', s, re.I):
                dgroup.update(x.split(':')[0].strip() for x in n.split(','))
        if not incode:
            # a code segment by name, or TASM's simplified .code
            if re.match(r'\w+_TEXT\s+segment\b', s, re.I) or re.match(r'\.code\b', s, re.I): incode = True
            continue
        if re.match(r'\w+_TEXT\s+ends\b', s, re.I) or re.match(r'\.(data|data\?|fardata|fardata\?|const|stack)\b', s, re.I):
            incode = False; continue
        if not s:
            if comment.strip() or raw.strip().startswith(';'): block.append(comment.rstrip())
            else: block = []
            continue
        if re.match(r'(assume|public|extrn|\.386|\.186|end)\b', s, re.I): block = []; continue
        m = re.match(r'(\w+)\s+endp\b', s, re.I)
        if m: block = []; continue
        m = re.match(r'(\w+)\s+proc\b', s, re.I)
        if m:
            it = Item('label', no, s); it.name = m.group(1); it.block = block; items.append(it); block = []; continue
        m = re.match(r'(\w+)\s+equ\s+\$\+(\d+)$', s, re.I)
        if m:
            it = Item('label', no, s); it.name = m.group(1); it.plus = int(m.group(2)); it.block = []; items.append(it); continue
        m = re.match(r'(\w+)\s+label\b', s, re.I)
        if m:
            it = Item('label', no, s); it.name = m.group(1); it.block = block; items.append(it); block = []; continue
        m = re.match(r'(\w+):\s*(.*)$', s)
        if m:
            it = Item('label', no, s); it.name = m.group(1); it.block = block; items.append(it); block = []
            s = m.group(2).strip()
            if not s: continue
        m = re.match(r'(?:(\w+)\s+)?(db|dw|dd)\s+(.*)$', s, re.I)
        if m:
            if m.group(1):
                it = Item('label', no, s); it.name = m.group(1); it.block = block; items.append(it); block = []
            it = Item('data', no, s, comment.strip()); it.dkind = m.group(2).lower(); it.ops = m.group(3)
            it.len = data_size(it.dkind, it.ops); items.append(it); block = []
            continue
        if re.match(r'even\b', s, re.I):
            items.append(Item('even', no, s)); continue
        it = Item('ins', no, s, comment.strip()); items.append(it); block = []
    DGROUP_NAMES[path] = dgroup
    return target, items


def norm_mn(s):
    t = s.split()
    if t[0].lower() in ('rep', 'repe', 'repne', 'repz', 'repnz'): t = t[1:]
    m = t[0].lower()
    if m == 'movs' or m == 'lods' or m == 'stos' or m == 'scas' or m == 'cmps':
        sz = 'b' if 'byte' in s else 'w' if 'word' in s and 'dword' not in s else 'd'
        m = m + sz
    return SYN.get(m, m)


def layout(path, exe, hdr):
    target, items = parse(path)
    base, size, org = load_target(target)
    pref = [k for k in CODESEGS if target.startswith(k)]
    if not pref: die('unknown code segment ' + target + ' (the spec\'s CODESEGS)')
    seg, cs_name = CODESEGS[max(pref, key=len)]
    addr = org
    out = []
    pend = []
    k = 0
    while k < len(items):
        it = items[k]
        if it.kind == 'label':
            it.addr = addr + getattr(it, 'plus', 0)
            pend.append(it); k += 1; continue
        even = it.kind == 'even'
        if even and not addr & 1:
            k += 1; continue
        # bytes the source writes as data that run as an instruction: a far jmp TASM would
        # shorten, and do_goursurfv's `db 0A8h` (test al, swallowing the next lodsw)
        execd = it.kind == 'data' and it.dkind == 'db' and (
            re.search(r'\bjmp\b', it.comment) is not None or 'test al,' in it.comment)
        # an instruction the source writes as bytes to keep the original's encoding, which its
        # comment names ("match: mov bx,word ptr [bp+8]: disp16, ..."): run it when the bytes
        # decode to that instruction, all of them
        if not execd and it.kind == 'data' and it.dkind == 'db':
            mm = re.match(r'\s*match:\s*(\w+)\b', it.comment)
            if mm:
                pos = base + (addr - org)
                ins = I.Decoder(16, exe[pos:pos + 16], ip=addr).decode()
                got = SYN.get(MN[ins.mnemonic], MN[ins.mnemonic]) if not ins.is_invalid else None
                execd = got is not None and got == SYN.get(mm.group(1).lower(), mm.group(1).lower()) and ins.len == it.len
        if it.kind in ('ins', 'even') or execd:
            pos = base + (addr - org)
            dec = I.Decoder(16, exe[pos:pos + 16], ip=addr)
            ins = dec.decode()
            if ins.is_invalid: die(f'{path}:{it.line}: cannot decode {exe[pos:pos+8].hex()} at {addr:04X}')
            own = it.len if execd else (1 if even else ins.len)
            it.ins = ins; it.co = dec.get_constant_offsets(ins); it.len = ins.len; it.addr = addr
            it.pos = pos
            if it.kind == 'ins':
                want = norm_mn(it.src); got = MN[ins.mnemonic]
                got = SYN.get(got, got)
                if want == 'ret' and got == 'retf': want = 'retf'      # ret in a far proc
                if got == 'nop' and want != 'nop':
                    # TASM's padding after a forward jump it sized for the worst case: a nop that
                    # runs, then the source's instruction
                    pad = Item('ins', it.line, 'nop', 'padding TASM put after a forward jump')
                    pad.ins = ins; pad.co = it.co; pad.len = 1; pad.addr = addr; pad.pos = pos
                    pad.labels = [p for p in pend if p.addr == addr]
                    pend = [p for p in pend if p.addr != addr]
                    out.append(pad)
                    addr += 1
                    it.ins = None
                    continue
                if want != got and not (want == 'nop' and got == 'xchg'):
                    die(f'{path}:{it.line}: source says {want}, the bytes at {addr:04X} are {FMT.format(ins)}')
            it.kind = 'ins'
            it.labels = [p for p in pend if p.addr == addr]
            out.append(it)
            pend = [p for p in pend if p.addr != addr]
            # the items after an instruction written as data keep their own addresses (the far
            # jmp's dw operands are data; do_goursurfv's test runs into do_goursurf's lodsw)
            addr += own
            k += 1
            continue
        if it.kind == 'data':
            it.addr = addr
            it.labels = [p for p in pend if p.addr == addr]
            pend = [p for p in pend if p.addr != addr]
            out.append(it)
            addr += it.len; k += 1; continue
        die('item kind ' + it.kind)
    # A jump into the middle of an instruction (`L5EE9 equ $+1` after a `test ax,0FBD1h` whose
    # immediate runs as `sar bx,1`): the bytes from the target on are decoded as instructions of
    # their own until they meet the module's instruction stream again, and translated as well.
    starts = {x.addr for x in out if x.kind == 'ins'}
    spans = [(x.addr, x.addr + x.len) for x in out if x.kind == 'ins']
    targets = {x.ins.near_branch16 for x in out if x.kind == 'ins' and x.ins is not None
               and x.ins.op_count and x.ins.op_kind(0) == OK_.NEAR_BRANCH16}
    for lab in [x for x in items if x.kind == 'label' and x.addr is not None]:
        a = lab.addr
        if a in starts or a not in targets or not any(lo < a < hi for lo, hi in spans): continue
        first = True
        while a not in starts:
            pos = base + (a - org)
            dec = I.Decoder(16, exe[pos:pos + 16], ip=a)
            ins = dec.decode()
            if ins.is_invalid: die(f'{path}:{lab.line}: cannot decode the bytes {lab.name} jumps into')
            syn = Item('ins', lab.line, '', f'the bytes from {lab.name} on, run as instructions of their own')
            syn.ins = ins; syn.co = dec.get_constant_offsets(ins); syn.len = ins.len; syn.addr = a; syn.pos = pos
            syn.labels = [lab] if first else []
            out.append(syn); starts.add(a)
            first = False
            a += ins.len
            if ins.flow_control not in (FC.NEXT, FC.CALL, FC.CONDITIONAL_BRANCH): break
    labels = {}
    for it in items:
        if it.kind == 'label':
            if it.addr is None: die('label without address ' + it.name)
            labels[it.name] = it.addr
    return dict(path=path, target=target, base=base, size=size, org=org, end=addr, seg=seg, cs=cs_name,
                items=out, labels=labels, all_items=items)


# ---- operands -------------------------------------------------------------------------------

SEGREG = {I.Register.ES: 'es', I.Register.CS: 'cs', I.Register.SS: 'ss', I.Register.DS: 'ds', I.Register.FS: 'fs',
          I.Register.GS: 'gs'}


def rsize(r):
    n = R[r]
    if n in ('AL', 'AH', 'BL', 'BH', 'CL', 'CH', 'DL', 'DH'): return 8
    if n.startswith('E') and len(n) == 3: return 32
    return 16


CUR_SEG = [0]


def rname(r):
    if r == I.Register.CS: return f'(uint16_t)(0x{CUR_SEG[0]:04X} + PORT_LOAD_SEG)'
    if r in SEGREG: return 'asm_' + SEGREG[r]
    return R[r]


CT = {8: 'uint8_t', 16: 'uint16_t', 32: 'uint32_t'}
ST = {8: 'int8_t', 16: 'int16_t', 32: 'int32_t'}
RD = {8: 'rb', 16: 'rw', 32: 'rd'}
WR = {8: 'wb', 16: 'ww', 32: 'wd'}
MASK = {8: 0xFF, 16: 0xFFFF, 32: 0xFFFFFFFF}


class Ctx:
    def __init__(s, mod, it, patched):
        s.mod = mod; s.it = it; s.ins = it.ins; s.patched = patched; s.consumed = set()

    def count(s, i):
        """a shift's count operand: CL, or its immediate (from the code block when patched)"""
        if s.ins.op_kind(i) == OK_.REGISTER: return 'CL', False
        co = s.it.co
        if co.immediate_size:
            d = s.dyn(co.immediate_offset, co.immediate_size)
            if d: return d, False
        return str(s.ins.immediate(i) & 0xFF), True

    def segptr(s, segreg):
        n = SEGREG[segreg]
        if n == 'cs':
            if not s.mod['cs']: die(f"{s.mod['path']}:{s.it.line}: cs: data in a module without a code block")
            return s.mod['cs']
        return 'p' + n.upper()

    def dyn(s, off, size):
        """the code block's bytes at this instruction's offset off, when another instruction
        writes them; else None"""
        a = s.it.addr + off
        hit = [b for b in range(a, a + size) if (s.mod['seg'], b) in s.patched]
        if not hit: return None
        s.consumed.update(range(a, a + size))
        if len(hit) != size: die(f"{s.mod['path']}:{s.it.line}: a patch writes part of an operand")
        return f"{RD[size * 8]}({s.mod['cs']}, 0x{a:04X})"

    def ea(s):
        ins = s.ins
        parts = []
        a32 = False
        for r in (ins.memory_base, ins.memory_index):
            if r != I.Register.NONE and rsize(r) == 32: a32 = True
        if ins.memory_base != I.Register.NONE: parts.append(R[ins.memory_base])
        if ins.memory_index != I.Register.NONE:
            sc = ins.memory_index_scale
            parts.append(R[ins.memory_index] + (f' * {sc}' if sc > 1 else ''))
        disp = ins.memory_displacement & (0xFFFFFFFF if a32 else 0xFFFF)
        dyn = s.dyn(s.it.co.displacement_offset, s.it.co.displacement_size) if s.it.co.displacement_size else None
        if dyn: parts.append(dyn)
        elif disp or not parts: parts.append(f'0x{disp:X}')
        return ' + '.join(parts)

    def msize(s):
        return I.MemorySizeExt.size(s.ins.memory_size) * 8

    def opsize(s, i):
        k = s.ins.op_kind(i)
        if k == OK_.REGISTER: return rsize(s.ins.op_register(i))
        if k in (OK_.MEMORY, OK_.MEMORY_SEG_SI, OK_.MEMORY_ESDI, OK_.MEMORY_SEG_DI): return s.msize()
        return None

    def imm(s, i, size):
        ins = s.ins
        v = ins.immediate(i) & MASK[size]
        co = s.it.co
        if co.immediate_size:
            dyn = s.dyn(co.immediate_offset, co.immediate_size)
            if dyn:
                if co.immediate_size * 8 != size:
                    k = co.immediate_size * 8
                    return f'({CT[size]})({ST[k]}){dyn}'
                return dyn
            p = s.it.pos + co.immediate_offset
            if p in s.mod['rel']:
                if co.immediate_size != 2: die(f"{s.mod['path']}:{s.it.line}: a relocated immediate not a word")
                if v == DGROUP_PARA: die(f"{s.mod['path']}:{s.it.line}: DGROUP's segment: the port has no DGROUP segment (OVERRIDES)")
                return f'(uint16_t)(0x{v:04X} + PORT_LOAD_SEG)'
        return f'0x{v:X}'

    def rd(s, i, size=None):
        ins = s.ins; k = ins.op_kind(i)
        if k == OK_.REGISTER: return rname(ins.op_register(i))
        if k == OK_.MEMORY:
            return f'{RD[s.msize()]}({s.segptr(ins.memory_segment)}, {s.ea()})'
        if k in (OK_.IMMEDIATE8, OK_.IMMEDIATE16, OK_.IMMEDIATE32, OK_.IMMEDIATE8TO16, OK_.IMMEDIATE8TO32,
                 OK_.IMMEDIATE8_2ND):
            return s.imm(i, size or {OK_.IMMEDIATE8: 8, OK_.IMMEDIATE16: 16, OK_.IMMEDIATE32: 32,
                                     OK_.IMMEDIATE8TO16: 16, OK_.IMMEDIATE8TO32: 32, OK_.IMMEDIATE8_2ND: 8}[k])
        die(f"{s.mod['path']}:{s.it.line}: operand kind {k}")

    def wr(s, i, val):
        ins = s.ins; k = ins.op_kind(i)
        if k == OK_.REGISTER:
            r = ins.op_register(i)
            if r in SEGREG:
                n = SEGREG[r]
                if n == 'cs': die('a write of cs')
                return f'SET_{n.upper()}({val});'
            return f'{R[r]} = {val};'
        if k == OK_.MEMORY:
            return f'{WR[s.msize()]}({s.segptr(ins.memory_segment)}, {s.ea()}, {val});'
        die(f"{s.mod['path']}:{s.it.line}: write to operand kind {k}")


# ---- flags ----------------------------------------------------------------------------------

def flag_effect(it):
    """(uses, defs) of the four tracked flags."""
    ins = it.ins; m = MN[ins.mnemonic]
    if m in ('add', 'sub', 'cmp', 'neg', 'and', 'or', 'xor', 'test', 'shld', 'shrd'):
        if m in ('shld', 'shrd') and ins.op_kind(2) == OK_.REGISTER: return ALLF, ALLF
        return 0, ALLF
    if m in ('adc', 'sbb'): return CF_, ALLF
    if m in ('inc', 'dec'): return 0, ZF_ | SF_ | OF_
    if m in ('shl', 'shr', 'sar', 'rol', 'ror'):
        d = ALLF if m in ('shl', 'shr', 'sar') else CF_ | OF_
        if ins.op_kind(1) == OK_.REGISTER: return d, d
        if (ins.immediate(1) & 31) == 0: return 0, 0
        return 0, d
    if m in ('rcl', 'rcr'):
        if ins.op_kind(1) == OK_.REGISTER: return CF_ | OF_, CF_ | OF_
        return CF_, CF_ | OF_
    if m in ('imul', 'mul'): return 0, CF_ | OF_
    if m == 'bsf': return 0, ZF_
    if m in ('scasb', 'scasw', 'scasd', 'cmpsb', 'cmpsw'):
        if ins.has_rep_prefix or ins.has_repne_prefix or ins.has_repe_prefix: return ALLF, ALLF
        return 0, ALLF
    if m in ('clc', 'stc'): return 0, CF_
    if m == 'cmc': return CF_, CF_
    if m == 'sahf': return 0, CF_ | ZF_ | SF_
    if m == 'lahf': return CF_ | ZF_ | SF_, 0
    if m == 'pushf': return ALLF, 0
    if m == 'popf': return 0, ALLF
    if m in ('loope', 'loopne'): return ZF_, 0
    if ins.flow_control == FC.CONDITIONAL_BRANCH and ins.condition_code != CC.NONE:
        return CCOND[ins.condition_code][1], 0
    if ins.flow_control in (FC.CALL, FC.INDIRECT_CALL): return 0, ALLF
    if m == 'int': return 0, ALLF
    return 0, 0


def successors(mod, idx, byaddr):
    it = mod['items'][idx]; ins = it.ins; fc = ins.flow_control
    nxt = byaddr.get(it.addr + it.len)
    out = []; exit_ = False
    if fc in (FC.NEXT, FC.CALL, FC.INDIRECT_CALL, FC.INTERRUPT, FC.EXCEPTION):
        if MN[ins.mnemonic] == 'int' and ins.immediate(0) in (2, 3):
            exit_ = True
        if nxt is not None: out.append(nxt)
        elif not exit_: exit_ = True
    elif fc == FC.CONDITIONAL_BRANCH:
        t = ins.near_branch16
        if t in byaddr: out.append(byaddr[t])
        else: exit_ = True
        if nxt is not None: out.append(nxt)
        else: exit_ = True
    elif fc == FC.UNCONDITIONAL_BRANCH:
        if ins.op_kind(0) == OK_.NEAR_BRANCH16 and ins.near_branch16 in byaddr: out.append(byaddr[ins.near_branch16])
        else: exit_ = True
    else:
        exit_ = True
    return out, exit_


def liveness(mod):
    items = [x for x in mod['items'] if x.kind == 'ins']
    mod['items'] = items
    byaddr = {x.addr: i for i, x in enumerate(items)}
    succ = []; ex = []; ue = []
    for i in range(len(items)):
        s, e = successors(mod, i, byaddr); succ.append(s); ex.append(e); ue.append(flag_effect(items[i]))
    live_out = [0] * len(items)
    live_in = [0] * len(items)
    changed = True
    while changed:
        changed = False
        for i in range(len(items) - 1, -1, -1):
            o = ALLF if ex[i] else 0
            for j in succ[i]: o |= live_in[j]
            u, d = ue[i]
            n = u | (o & ~d)
            if o != live_out[i] or n != live_in[i]:
                live_out[i] = o; live_in[i] = n; changed = True
    for i, x in enumerate(items):
        x.live = live_out[i]; x.succ = succ[i]
    mod['byaddr'] = byaddr


# ---- emission -------------------------------------------------------------------------------

def jtarget(mod, t):
    if t in mod['byaddr']: return f'goto L{t:04X};'
    note_hand(mod['seg'], t)
    return f'return ASM_JMP(0x{mod["seg"]:04X}, 0x{t:04X});'


HAND_RANGES = []        # (segment, lo, hi, where), from HANDWRITTEN and the modules' segments
HAND_CALLS = set()      # (segment, offset): the static targets in them the translation reaches


def hand_range(mod, a):
    for lo, hi, where in HANDWRITTEN.get(mod['name'], ()):
        if lo <= a < hi: return (lo, hi, where)
    return None


def note_hand(seg, a):
    for sg, lo, hi, where in HAND_RANGES:
        if sg == seg and lo <= a < hi: HAND_CALLS.add((seg, a))


def omit_handwritten(mods, patched):
    """Drop the instructions of each module's HANDWRITTEN ranges; refuse when a byte the
    translation still runs is patched by an instruction that is not translated any more."""
    for mod in mods:
        for lo, hi, where in HANDWRITTEN.get(mod['name'], ()): HAND_RANGES.append((mod['seg'], lo, hi, where))
    for mod in mods:
        if mod['name'] not in HANDWRITTEN: continue
        keep = [it for it in mod['items'] if not hand_range(mod, it.addr)]
        gone = [it for it in mod['items'] if hand_range(mod, it.addr)]
        for it in gone:
            ins = it.ins
            if ins is None or ins.op_count == 0 or ins.op_kind(0) != OK_.MEMORY: continue
            if ins.memory_segment != I.Register.CS: continue
            a = ins.memory_displacement & 0xFFFF
            for k in keep:
                if k.kind == 'ins' and k.ins is not None and k.addr <= a < k.addr + k.len:
                    die(f"{mod['path']}:{it.line}: hand-written range writes the code at {a:04X}, which is still translated")
        mod['items'] = keep


def emit(mod, it, patched, entries):
    ins = it.ins; m = MN[ins.mnemonic]; c = Ctx(mod, it, patched)
    LAST[0] = c
    m = {'xlatb': 'xlat', 'retnw': 'ret', 'retfw': 'retf'}.get(m, m)
    seg = mod['seg']; nxt = (it.addr + it.len) & 0xFFFF
    if m == 'int3':
        return [f'asm_halt_at(0x{seg:04X}, 0x{it.addr:04X}, "int 3, the debugger break");']
    key = (mod['name'], it.addr)
    if key in OVERRIDES: return OVERRIDES[key][0].split('\n')
    if 'DGROUP:' in it.src: die(f"{mod['path']}:{it.line}: a DGROUP offset: the port's DGROUP is C objects (OVERRIDES)")
    for n in DGROUP_NAMES.get(mod['path'], ()):
        if re.search(r'(?<![\w@$?])' + re.escape(n) + r'(?![\w@$?])', it.src):
            die(f"{mod['path']}:{it.line}: {n} is DGROUP data: the port's DGROUP is C objects (OVERRIDES)")
    # a patched opcode byte
    if (seg, it.addr) in patched and (seg, it.addr) not in PATCH_OVERRIDDEN:
        if ins.flow_control == FC.CONDITIONAL_BRANCH and it.len == 2 and 0x70 <= ins.code_size + 0x70:
            return [f'if (asm_jcc({mod["cs"]}[0x{it.addr:04X}])) {jtarget(mod, ins.near_branch16)}']
        # add r16,r/m16 (03h) and sub r16,r/m16 (2Bh) share their ModRM: code that turns one
        # into the other writes the opcode byte, so the translation tests it
        if ins.code in (I.Code.ADD_R16_RM16, I.Code.SUB_R16_RM16) and ins.segment_prefix == I.Register.NONE:
            c2 = Ctx(mod, it, patched); LAST[0] = c2
            a = c2.rd(0); b = c2.rd(1, 16); fl = it.live
            def one(op):
                if fl & ALLF: return c2.wr(0, f'{op}16({a}, {b}, 0)')
                return c2.wr(0, f'(uint16_t)({a} {"+" if op == "add" else "-"} {b})')
            return [f'if ({mod["cs"]}[0x{it.addr:04X}] == 0x2B) {{ {one("sub")} }} else {{ {one("add")} }}']
        die(f"{mod['path']}:{it.line}: an instruction whose opcode another writes, with no override")
    fl = it.live
    L = []
    size = c.opsize(0) if ins.op_count else None

    def arith(op, cin):
        a = c.rd(0); b = c.rd(1, size)
        if fl & ALLF:
            return c.wr(0, f'{op}{size}({a}, {b}, {cin})') if op != 'cmp' else f'sub{size}({a}, {b}, 0);'
        if op == 'cmp': return ''
        sign = '+' if op == 'add' else '-'
        extra = f' {sign} CF' if cin == 'CF' else ''
        return c.wr(0, f'({CT[size]})({a} {sign} {b}{extra})')

    if m == 'mov':
        L.append(c.wr(0, c.rd(1, size)))
    elif m in ('movsx', 'movzx'):
        s1 = c.opsize(1)
        v = c.rd(1)
        L.append(c.wr(0, f'({CT[size]})({ST[s1]}){v}' if m == 'movsx' else f'({CT[size]}){v}'))
    elif m == 'lea':
        L.append(c.wr(0, f'({CT[size]})({c.ea()})'))
    elif m in ('lds', 'les', 'lfs', 'lgs', 'lss'):
        # a far pointer loaded from memory: the register gets the word at the operand, the
        # segment register the word after it (both read before either is written, since the
        # register may be part of the address)
        if size != 16: die(f"{mod['path']}:{it.line}: {m} with a 32-bit offset")
        p = c.segptr(ins.memory_segment); e = c.ea()
        L.append(f'{{ uint16_t o_ = rw({p}, (uint16_t)({e})), s_ = rw({p}, (uint16_t)({e} + 2));')
        L.append(c.wr(0, 'o_'))
        L.append(f'SET_{m[1:].upper()}(s_); }}')
    elif m == 'xchg':
        if ins.op_kind(0) == OK_.REGISTER and ins.op_kind(1) == OK_.REGISTER and ins.op_register(0) == ins.op_register(1):
            pass
        else:
            L.append(f'{{ {CT[size]} t_ = {c.rd(0)};')
            L.append(c.wr(0, c.rd(1)))
            L.append(c.wr(1, 't_') + ' }')
    elif m in ('add', 'sub', 'cmp'):
        L.append(arith(m, '0'))
    elif m in ('adc', 'sbb'):
        L.append(arith('add' if m == 'adc' else 'sub', 'CF'))
    elif m in ('and', 'or', 'xor', 'test'):
        o = {'and': '&', 'or': '|', 'xor': '^', 'test': '&'}[m]
        v = f'({CT[size]})({c.rd(0)} {o} {c.rd(1, size)})'
        if m == 'test':
            if fl: L.append(f'logic{size}({v});')
        else:
            L.append(c.wr(0, f'logic{size}({v})' if fl else v))
    elif m in ('inc', 'dec'):
        if fl: L.append(c.wr(0, f'{m}{size}({c.rd(0)})'))
        else: L.append(c.wr(0, f'({CT[size]})({c.rd(0)} {"+" if m == "inc" else "-"} 1)'))
    elif m == 'neg':
        L.append(c.wr(0, f'neg{size}({c.rd(0)})' if fl else f'({CT[size]})-{c.rd(0)}'))
    elif m == 'not':
        L.append(c.wr(0, f'({CT[size]})~{c.rd(0)}'))
    elif m in ('shl', 'shr', 'sar', 'rol', 'ror', 'rcl', 'rcr'):
        n, const = c.count(1)
        a = c.rd(0)
        if fl or m in ('rol', 'ror', 'rcl', 'rcr') or not const:
            if not fl and m in ('shl', 'shr', 'sar'):
                nn = f'({n} & 31)'
                if m == 'shl': L.append(c.wr(0, f'({CT[size]})({a} << {nn})'))
                elif m == 'shr': L.append(c.wr(0, f'({CT[size]})({a} >> {nn})'))
                else: L.append(c.wr(0, f'({CT[size]})(({ST[size]}){a} >> ({nn} >= {size} ? {size - 1} : {nn}))'))
            else:
                L.append(c.wr(0, f'{m}{size}({a}, {n})'))
        else:
            k = int(n) & 31
            if k == 0: pass
            elif m == 'shl': L.append(c.wr(0, f'({CT[size]})({"0" if k >= size else f"{a} << {k}"})'))
            elif m == 'shr': L.append(c.wr(0, f'({CT[size]})({"0" if k >= size else f"{a} >> {k}"})'))
            else: L.append(c.wr(0, f'({CT[size]})(({ST[size]}){a} >> {min(k, size - 1)})'))
    elif m in ('shld', 'shrd'):
        n, _ = c.count(2)
        L.append(c.wr(0, f'{m}{size}({c.rd(0)}, {c.rd(1)}, {n})'))
    elif m == 'imul':
        if ins.op_count == 1: L.append(f'imul{size}({c.rd(0)});')
        elif ins.op_count == 2: L.append(c.wr(0, f'imul{size}x({c.rd(0)}, {c.rd(1, size)})'))
        else: L.append(c.wr(0, f'imul{size}x({c.rd(1)}, {c.rd(2, size)})'))
    elif m == 'mul':
        L.append(f'mul{size}({c.rd(0)});')
    elif m in ('div', 'idiv'):
        L.append(f'if (asm_{m}{size}({c.rd(0)}) && (c = asm_divfault(0x{seg:04X}, 0x{it.addr:04X}, {it.len})) != 0) return c;')
    elif m == 'cbw': L.append('AX = (uint16_t)(int8_t)AL;')
    elif m == 'cwde': L.append('EAX = (uint32_t)(int16_t)AX;')
    elif m == 'cwd': L.append('DX = (int16_t)AX < 0 ? 0xFFFF : 0;')
    elif m == 'cdq': L.append('EDX = (int32_t)EAX < 0 ? 0xFFFFFFFFu : 0;')
    elif m in ('lodsb', 'lodsw', 'lodsd', 'stosb', 'stosw', 'stosd', 'movsb', 'movsw', 'movsd', 'scasb', 'scasw',
               'cmpsb', 'cmpsw'):
        n = {'b': 1, 'w': 2, 'd': 4}[m[-1]]; bits = n * 8
        acc = {1: 'AL', 2: 'AX', 4: 'EAX'}[n]
        src = None
        if m.startswith(('lods', 'movs', 'cmps')):
            src = c.segptr(ins.memory_segment)
        if m.startswith('lods'): body = f'{acc} = {RD[bits]}({src}, SI); SI = (uint16_t)(SI + STEP({n}));'
        elif m.startswith('stos'): body = f'{WR[bits]}(pES, DI, {acc}); DI = (uint16_t)(DI + STEP({n}));'
        elif m.startswith('movs'): body = f'{WR[bits]}(pES, DI, {RD[bits]}({src}, SI)); SI = (uint16_t)(SI + STEP({n})); DI = (uint16_t)(DI + STEP({n}));'
        elif m.startswith('cmps'): body = f'sub{bits}({RD[bits]}({src}, SI), {RD[bits]}(pES, DI), 0); SI = (uint16_t)(SI + STEP({n})); DI = (uint16_t)(DI + STEP({n}));'
        else: body = f'sub{bits}({acc}, {RD[bits]}(pES, DI), 0); DI = (uint16_t)(DI + STEP({n}));'
        if ins.has_repne_prefix:
            L.append(f'while (CX) {{ {body} CX--; if (ZF) break; }}')
        elif ins.has_rep_prefix or ins.has_repe_prefix:
            if m.startswith(('scas', 'cmps')): L.append(f'while (CX) {{ {body} CX--; if (!ZF) break; }}')
            else: L.append(f'while (CX) {{ {body} CX--; }}')
        else:
            L.append(body)
    elif m in ('outsb', 'insb'):
        if m == 'outsb': body = f'asm_out8(DX, rb({c.segptr(ins.memory_segment)}, SI)); SI = (uint16_t)(SI + STEP(1));'
        else: body = 'wb(pES, DI, asm_in8(DX)); DI = (uint16_t)(DI + STEP(1));'
        L.append(f'while (CX) {{ {body} CX--; }}' if ins.has_rep_prefix else body)
    elif m == 'xlat':
        L.append(f'AL = rb({c.segptr(ins.memory_segment)}, BX + AL);')
    elif m == 'push':
        k = ins.op_kind(0)
        if k == OK_.REGISTER and ins.op_register(0) == I.Register.SP: L.append('push16(SP);')
        elif k == OK_.REGISTER: L.append(f'push{rsize(ins.op_register(0)) if ins.op_register(0) not in SEGREG else 16}({c.rd(0)});')
        elif k == OK_.MEMORY: L.append(f'push{c.msize()}({c.rd(0)});')
        else:
            sz = 32 if ins.code_size == 32 or I.MemorySizeExt.size(ins.memory_size) == 4 else 16
            L.append(f'push16({c.rd(0, 16)});')
    elif m == 'pop':
        k = ins.op_kind(0)
        if k == OK_.REGISTER:
            r = ins.op_register(0)
            if r in SEGREG: L.append(c.wr(0, 'pop16()'))
            else: L.append(c.wr(0, f'pop{rsize(r)}()'))
        else: L.append(f'{{ uint{c.msize()}_t t_ = pop{c.msize()}(); {c.wr(0, "t_")} }}')
    elif m == 'pusha':
        L.append('{ uint16_t t_ = SP; push16(AX); push16(CX); push16(DX); push16(BX); push16(t_); push16(BP); push16(SI); push16(DI); }')
    elif m == 'popa':
        L.append('DI = pop16(); SI = pop16(); BP = pop16(); SP = (uint16_t)(SP + 2); BX = pop16(); DX = pop16(); CX = pop16(); AX = pop16();')
    elif m == 'pushf': L.append('push16(asm_flags());')
    elif m == 'popf': L.append('asm_set_flags(pop16());')
    elif m == 'call':
        k = ins.op_kind(0)
        if k == OK_.NEAR_BRANCH16:
            note_hand(seg, ins.near_branch16)
            L.append(f'if ((c = asm_call(ASM_JMP(0x{seg:04X}, 0x{ins.near_branch16:04X}), 0x{nxt:04X})) != 0) return c;')
        elif k == OK_.FAR_BRANCH16:
            note_hand(ins.far_branch_selector, ins.far_branch16)
            L.append(f'if ((c = asm_callf(ASM_JMP(0x{ins.far_branch_selector:04X}, 0x{ins.far_branch16:04X}), 0x{seg:04X} + PORT_LOAD_SEG, 0x{nxt:04X})) != 0) return c;')
        elif ins.is_call_far_indirect and k == OK_.MEMORY:
            # a far pointer in memory, whose segment is a loaded one (the EXE's paragraph plus
            # the load segment, as DOS relocated it)
            p = c.segptr(ins.memory_segment); e = c.ea()
            L.append(f'{{ uint16_t o_ = rw({p}, (uint16_t)({e})), s_ = rw({p}, (uint16_t)({e} + 2));')
            L.append(f'  if ((c = asm_callf(ASM_JMP((uint16_t)(s_ - PORT_LOAD_SEG), o_), 0x{seg:04X} + PORT_LOAD_SEG, 0x{nxt:04X})) != 0) return c; }}')
        elif ins.is_call_far_indirect:
            die(f"{mod['path']}:{it.line}: an indirect far call")
        else:
            L.append(f'if ((c = asm_call(ASM_JMP(0x{seg:04X}, {c.rd(0)}), 0x{nxt:04X})) != 0) return c;')
    elif m == 'jmp':
        k = ins.op_kind(0)
        if k == OK_.NEAR_BRANCH16: L.append(jtarget(mod, ins.near_branch16))
        elif k == OK_.FAR_BRANCH16:
            note_hand(ins.far_branch_selector, ins.far_branch16)
            L.append(f'return ASM_JMP(0x{ins.far_branch_selector:04X}, 0x{ins.far_branch16:04X});')
        else: L.append(f'return ASM_JMP(0x{seg:04X}, {c.rd(0)});')
    elif ins.flow_control == FC.CONDITIONAL_BRANCH:
        t = ins.near_branch16
        if m == 'loop': L.append(f'if (--CX) {jtarget(mod, t)}')
        elif m == 'loope': L.append(f'if (--CX && ZF) {jtarget(mod, t)}')
        elif m == 'loopne': L.append(f'if (--CX && !ZF) {jtarget(mod, t)}')
        elif m == 'jcxz': L.append(f'if (!CX) {jtarget(mod, t)}')
        elif m == 'jecxz': L.append(f'if (!ECX) {jtarget(mod, t)}')
        else: L.append(f'if ({CCOND[ins.condition_code][0]}) {jtarget(mod, t)}')
    elif m == 'ret':
        n = ins.immediate(0) if ins.op_count else 0
        L.append(f'SP = (uint16_t)(SP + {2 + n}); return ASM_RET;' if not n else f'SP = (uint16_t)(SP + {2 + n}); return ASM_RET | {n}u << 8;')
    elif m == 'retf':
        n = ins.immediate(0) if ins.op_count else 0
        L.append(f'SP = (uint16_t)(SP + {4 + n}); return ASM_RETF{" | " + str(n) + "u << 8" if n else ""};')
    elif m == 'iret':
        L.append('SP = (uint16_t)(SP + 4); asm_set_flags(pop16()); return ASM_IRET;')
    elif m == 'int':
        n = ins.immediate(0)
        if n in (2, 3):
            L.append(f'asm_halt_at(0x{seg:04X}, 0x{it.addr:04X}, "int {n}h, the debugger break");')
        else:
            L.append(f'asm_int(0x{n:02X});')
    elif m == 'in':
        L.append(f'AL = asm_in8({c.rd(1) if ins.op_kind(1) == OK_.REGISTER else c.rd(1, 16)});')
    elif m == 'out':
        p = c.rd(0) if ins.op_kind(0) == OK_.REGISTER else c.rd(0, 16)
        L.append(f'asm_out{c.opsize(1)}({p}, {c.rd(1)});')
    elif m in ('cli', 'sti', 'nop'): L.append(';')
    elif m == 'cld': L.append('DF = 0;')
    elif m == 'std': L.append('DF = 1;')
    elif m == 'clc': L.append('CF = 0;')
    elif m == 'stc': L.append('CF = 1;')
    elif m == 'cmc': L.append('CF = !CF;')
    elif m == 'lahf': L.append('AH = (uint8_t)(SF << 7 | ZF << 6 | 2 | CF);')
    elif m == 'sahf': L.append('CF = AH & 1; ZF = AH >> 6 & 1; SF = AH >> 7 & 1;')
    elif m == 'bsf':
        L.append(f'{{ {CT[size]} t_ = {c.rd(1)}; if (t_) {{ {c.wr(0, "(" + CT[size] + ")__builtin_ctz(t_)")} ZF = 0; }} else ZF = 1; }}')
    else:
        die(f"{mod['path']}:{it.line}: no translation for {FMT.format(ins)}")
    return [x for x in L if x]


LAST = [None]


def emit_checked(mod, it, patched, entries):
    """emit, then make sure every byte of the instruction another instruction writes was
    read from the code block (or the instruction is overridden)"""
    key = (mod['name'], it.addr)
    lines = emit(mod, it, patched, entries)
    if key in OVERRIDES or (mod['seg'], it.addr) in patched: return lines
    hit = {b for b in range(it.addr, it.addr + it.len) if (mod['seg'], b) in patched}
    left = hit - LAST[0].consumed
    if left:
        die(f"{mod['path']}:{it.line}: bytes {sorted('%04X' % b for b in left)} of this instruction are written by "
            f"another and the translation does not read them")
    return lines


def find_patches(mods):
    """The bytes of code a cs: write with a constant address changes: (segment, offset)."""
    out = set()
    for mod in mods:
        for it in mod['items']:
            ins = it.ins
            if ins is None or ins.op_count == 0: continue
            if ins.op_kind(0) != OK_.MEMORY or ins.memory_segment != I.Register.CS: continue
            if ins.memory_base != I.Register.NONE or ins.memory_index != I.Register.NONE: continue
            m = MN[ins.mnemonic]
            if m in ('cmp', 'test', 'push', 'jmp', 'call'): continue
            a = ins.memory_displacement & 0xFFFF
            for b in range(a, a + I.MemorySizeExt.size(ins.memory_size)): out.add((mod['seg'], b))
    return out


def cname_comment(s):
    return s.replace('*/', '* /')


def write_module(mod, patched, check):
    name = mod['name']
    CUR_SEG[0] = mod['seg']
    items = mod['items']
    byaddr = mod['byaddr']
    # every instruction can be entered: jumps through tables of addresses land anywhere in an
    # unrolled loop (GRLIBI.ASM's 49C4, GRLIBH.ASM's 498A), the divide faults' handlers start
    # where no label is (do_lnres's 11ABh and 11ADh), and the dispatch reaches every handler
    lab = {}
    for it in items:
        for l in it.labels: lab.setdefault(it.addr, []).append(l.name)
    entries = set(byaddr)
    labels_needed = entries
    rel = os.path.relpath(mod['path'], root) if os.path.isabs(mod['path']) else mod['path']
    o = []
    o.append(f'/* {os.path.basename(mod["out"])}: replaces {rel} ({mod["target"]}, {mod["org"]:04X}..{mod["end"]:04X} of its')
    o.append(f'   segment). Written by tools/asm2c.py from the assembly and the bytes it assembles to; do not')
    o.append(f'   edit by hand: change the tool, or its OVERRIDES, and run it again. Each instruction is the')
    o.append(f'   C below its source line; the comments before a routine are the .ASM file\'s. */')
    o.append('#include "x86/asmrt.h"')
    o.append('')
    o.extend(EXTERNS.get(name, []))
    o.append('static int asm_jcc(uint8_t op)')
    o.append('{')
    o.append('    switch (op & 0x0F) {')
    o.append('    case 0x0: return OF; case 0x1: return !OF; case 0x2: return CF; case 0x3: return !CF;')
    o.append('    case 0x4: return ZF; case 0x5: return !ZF; case 0x6: return CF || ZF; case 0x7: return !CF && !ZF;')
    o.append('    case 0x8: return SF; case 0x9: return !SF; case 0xC: return SF != OF; case 0xD: return SF == OF;')
    o.append('    case 0xE: return ZF || SF != OF; case 0xF: return !ZF && SF == OF;')
    o.append('    default: port_halt("a patched jump on the parity flag");')
    o.append('    }')
    o.append('}')
    o.append('')
    o.append(f'uint32_t asm_mod_{name}(uint16_t entry)')
    o.append('{')
    o.append('    uint32_t c;')
    o.append('    (void)c;')
    o.append('    switch (entry) {')
    for a in sorted(entries):
        o.append(f'    case 0x{a:04X}: goto L{a:04X};')
    o.append(f'    default: asm_bad_entry("{os.path.basename(mod["path"])}", entry);')
    o.append('    }')
    for i, it in enumerate(items):
        for l in it.labels:
            blk = getattr(l, 'block', None)
            if blk:
                o.append('')
                body = [cname_comment(x[1:] if x.startswith(' ') else x).rstrip() for x in blk]
                o.append('    /* ' + body[0])
                for x in body[1:]: o.append('       ' + x if x else '')
                o[-1] += ' */'
        if it.addr in labels_needed:
            names = ', '.join(lab.get(it.addr, []))
            o.append(f'L{it.addr:04X}:' + (f' /* {names} */' if names else ''))
        src = it.src if it.src else FMT.format(it.ins)
        o.append(f'    /* {it.addr:04X}  {cname_comment(src)} */')
        if (name, it.addr) in OVERRIDES:
            o.append(f'    /* by hand: {cname_comment(OVERRIDES[(name, it.addr)][1])} */')
        for line in emit_checked(mod, it, patched, entries):
            o.append('    ' + line)
        # fall through to the next instruction, which may not be the next item
        fc = it.ins.flow_control
        falls = fc in (FC.NEXT, FC.CALL, FC.INDIRECT_CALL, FC.INTERRUPT, FC.CONDITIONAL_BRANCH, FC.EXCEPTION)
        if falls:
            na = it.addr + it.len
            if i + 1 < len(items) and items[i + 1].addr == na: continue
            if na in byaddr:
                o.append(f'    goto L{na:04X};')
            elif hand_range(mod, na):
                note_hand(mod['seg'], na)
                o.append(f'    return ASM_JMP(0x{mod["seg"]:04X}, 0x{na:04X});   /* into hand-written C */')
            else:
                o.append(f'    asm_halt_at(0x{mod["seg"]:04X}, 0x{na:04X}, "ran off the code into data");')
    o.append('}')
    return '\n'.join(o) + '\n'


def build(check=False, only=None):
    exe, hdr, rel = load_exe()
    mods = []
    for src, out, name in MODULES:
        mod = layout(src, exe, hdr)
        mod['out'] = out; mod['name'] = name; mod['rel'] = rel
        mods.append(mod)
    patched = find_patches(mods)
    bad = 0
    omit_handwritten(mods, patched)
    for mod in mods:
        liveness(mod)
    for mod in mods:
        text = write_module(mod, patched, check)        # every module, for HAND_CALLS
        if only and mod['name'] != only: continue
        path = os.path.join(root, mod['out'])
        old = open(path).read() if os.path.exists(path) else None
        if old != text:
            if check: print('out of date: ' + mod['out']); bad = 1
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                open(path, 'w').write(text)
    # the module table
    t = ['/* modtab.c: replaces nothing. The translated modules (tools/asm2c.py writes this file and',
         '   them): the code segment, the range of offsets and the function of each. */',
         '#include "x86/asmrt.h"', '']
    for mod in mods: t.append(f'uint32_t asm_mod_{mod["name"]}(uint16_t entry);')
    t.append('')
    t.append('const struct asm_module asm_modules[] = {')
    for mod in mods:
        if not mod['items']: die(f"{mod['path']}: no instructions (a module of data only, which needs no translation)")
        lo = min(x.addr for x in mod['items']); hi = max(x.addr + x.len for x in mod['items'])
        t.append(f'    {{ 0x{mod["seg"]:04X}, 0x{lo:04X}, 0x{hi:04X}, asm_mod_{mod["name"]}, "{os.path.basename(mod["path"])}" }},')
    t.append('};')
    t.append(f'const int asm_nmodules = {len(mods)};')
    t.append('')
    t.append('/* the ranges of the modules that are hand-written C (asm2c.py\'s HANDWRITTEN) */')
    t.append('const struct asm_hand asm_hand_ranges[] = {')
    for sg, lo, hi, where in HAND_RANGES:
        t.append(f'    {{ 0x{sg:04X}, 0x{lo:04X}, 0x{hi:04X}, "{where}" }},')
    t.append('    { 0, 0, 0, 0 }')
    t.append('};')
    t.append(f'const int asm_nhand_ranges = {len(HAND_RANGES)};')
    t.append('/* the places in them the translated code calls or jumps to: each needs its C (checked at start-up) */')
    t.append('const struct asm_hand_call asm_hand_calls[] = {')
    for sg, a in sorted(HAND_CALLS):
        t.append(f'    {{ 0x{sg:04X}, 0x{a:04X} }},')
    t.append('    { 0, 0 }')
    t.append('};')
    t.append(f'const int asm_nhand_calls = {len(HAND_CALLS)};')
    text = '\n'.join(t) + '\n'
    path = os.path.join(root, MODTAB)
    old = open(path).read() if os.path.exists(path) else None
    if old != text:
        if check: print('out of date: ' + MODTAB); bad = 1
        else: open(path, 'w').write(text)
    return bad


def main(argv):
    configure(CFG)
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--check', action='store_true')
    ap.add_argument('--list')
    ap.add_argument('--only')
    a = ap.parse_args(argv)
    if a.list:
        exe, hdr, rel = load_exe()
        for src, out, name in MODULES:
            if name != a.list: continue
            mod = layout(src, exe, hdr)
            for it in mod['items']:
                if it.kind == 'ins':
                    print(f'{it.addr:04X} {it.len} {FMT.format(it.ins):40} {it.src}')
        return 0
    return build(a.check, a.only)


if __name__ == '__main__':
    sys.exit(main(ARGV))
