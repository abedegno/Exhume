"""A synthetic C2000 project for the integration tests: the C in tests/fw, built and linked with
TI's tools into an "original" image, a map made from the link's map file, and an exhume.toml.

The integration tests need a compiler, named by the environment (they skip without one):

    EXHUME_C2000_CGT     a compiler home (bin/cl2000.exe under Wine, or a native bin/cl2000)
    EXHUME_C2000_NATIVE  the bin directory of a native CGT, 18.12 or later (ofd2000, dis2000 ...)
    EXHUME_C2000_WINE    the directory holding wine and wineserver (for a Windows compiler)
    EXHUME_C2000_PREFIX  the Wine prefix the compiler is installed in

Nothing built here is committed: it all goes to a temporary directory."""
import os, re, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.join(os.path.dirname(HERE), 'tools')
sys.path.insert(0, TOOLS)
import c28tc, c28map

FW = os.path.join(HERE, 'fw')
BASE, WORDS = 0x080000, 0x2000


def environment():
    """(cgt, native, wine, prefix), or a reason the integration tests cannot run."""
    e = os.environ
    cgt, native = e.get('EXHUME_C2000_CGT'), e.get('EXHUME_C2000_NATIVE')
    wine, prefix = e.get('EXHUME_C2000_WINE'), e.get('EXHUME_C2000_PREFIX')
    if not cgt or not os.path.isdir(os.path.expanduser(cgt)): return None, 'EXHUME_C2000_CGT names no compiler home'
    if not native or not os.path.exists(os.path.join(os.path.expanduser(native), 'ofd2000')):
        return None, 'EXHUME_C2000_NATIVE names no native CGT bin directory'
    cgt = os.path.expanduser(cgt)
    if not os.path.exists(os.path.join(cgt, 'bin', 'cl2000')):
        if not wine or not os.path.exists(os.path.join(wine, 'wine')): return None, 'a Windows compiler needs EXHUME_C2000_WINE'
        if not prefix or not os.path.isdir(os.path.expanduser(prefix)): return None, 'a Windows compiler needs EXHUME_C2000_PREFIX'
    return (cgt, os.path.expanduser(native), wine, os.path.expanduser(prefix or '')), None


_SEC = re.compile(r'^(\S+)\s+(\d)\s+([0-9a-f]{8})\s+([0-9a-f]{8})\s*(.*)$')
_IN = re.compile(r'^\s+([0-9a-f]{8})\s+([0-9a-f]{8})\s+(.*?)\s*\((\S+)\)\s*$')
_SYM = re.compile(r'^(\d|abs)\s+([0-9a-f]{8})\s+(\S+)\s*$')


def parse_link_map(text):
    """(input sections [(output, load, size, module, member, section, run offset)],
    symbols [(page, address, name)]) from lnk2000's -m file."""
    inputs, syms, out, runoff, lib = [], [], None, 0, None
    part = None
    for line in text.splitlines():
        if line.startswith('SECTION ALLOCATION MAP'): part = 'sec'; continue
        if line.startswith('GLOBAL SYMBOLS: SORTED BY Symbol Address'): part = 'sym'; continue
        if line.startswith(('MODULE SUMMARY', 'GLOBAL DATA SYMBOLS', 'GLOBAL SYMBOLS: SORTED ALPHA')): part = None; continue
        if part == 'sec':
            m = _SEC.match(line)
            if m:
                out = m.group(1); r = re.search(r'RUN ADDR = ([0-9a-f]+)', m.group(5))
                runoff = int(r.group(1), 16) - int(m.group(3), 16) if r else 0
                continue
            m = _IN.match(line)
            if m and out:
                mod = m.group(3)
                if ':' in mod:
                    l, _, mem = mod.partition(':')
                    lib = l.strip() or lib; mem = mem.strip()
                else:
                    lib, mem = None, mod.strip()
                inputs.append((out, int(m.group(1), 16), int(m.group(2), 16), lib, mem, m.group(4), runoff))
        elif part == 'sym':
            m = _SYM.match(line)
            if m: syms.append((m.group(1), int(m.group(2), 16), m.group(3)))
    return inputs, syms


def make_map(inputs, syms):
    """functions.tsv and data.tsv rows from the link map."""
    funcs, data = [], []
    for out, load, size, lib, mem, sec, runoff in inputs:
        if sec.startswith('.cinit'):
            continue
        code = sec.startswith(('.text', 'ramfuncs'))
        lo, hi = load + runoff, load + runoff + size
        page = '0' if code or sec.startswith(('.econst', '.switch')) else '1'
        inside = sorted({(a, n) for p, a, n in syms if p == page and lo <= a < hi and not n.startswith(('.', '___'))
                         and n not in ('etext', 'cinit', '_ram_code_run', '_ram_code_load')})
        if sec.startswith('.switch:'):
            data.append(c28map.Row(load, size, 'sw_' + c28map.cname(sec.split(':', 1)[1]), 'switch')); continue
        byaddr = {}
        for a, n in inside: byaddr.setdefault(a, []).append(n)
        addrs = sorted(byaddr)
        for i, a in enumerate(addrs):
            end = addrs[i + 1] if i + 1 < len(addrs) else hi
            names = sorted(byaddr[a], key=lambda n: (not n.startswith('_'), n))
            name = c28map.cname(names[0])
            notes = []
            if len(names) > 1: notes.append('alias=' + ','.join(names[1:]))
            if code:
                kind = 'rts' if lib else 'ramfunc' if runoff else 'c'
                if runoff: notes.append('run=0x%06X' % a)
                funcs.append(c28map.Row(a - runoff, end - a, name, kind, ' '.join(notes)))
            else:
                data.append(c28map.Row(a, end - a, name, 'const' if page == '0' else 'var', ' '.join(notes)))
    for p, a, n in syms:
        if n in ('_ram_code_load', '_ram_code_run'):
            data.append(c28map.Row(a, 0, c28map.cname(n), 'linker'))
    for p, a, n in syms:
        if n == '_ram_code_size': data.append(c28map.Row(a, 0, c28map.cname(n), 'linker'))
    return sorted(funcs, key=lambda r: r.addr), sorted(data, key=lambda r: (r.addr, r.name))


TOML = '''[project]
name = "synthetic"
[toolchain]
profile = "ti-c2000"
home = "{cgt}"
[binary]
image = "firmware/app.bin"
[c2000]
base = 0x{base:06X}
words = 0x{words:X}
native = "{native}"
wine = "{wine}"
prefix = "{prefix}"
others = "{others}"
jobs = 4
[c2000.gate]
cinit = 0x{cinit:06X}
cinit_order = [{order}]
stack = 0x{stack:X}
stack_size = 0x{stack_size:X}
'''


def build(dest, env, sources=('loopctl.c', 'helpers.c', 'appmain.c')):
    """Build the synthetic project in dest; returns its exhume.toml path."""
    cgt, native, wine, prefix = env
    os.makedirs(os.path.join(dest, 'src'), exist_ok=True)
    for n in sources: shutil.copy(os.path.join(FW, n), os.path.join(dest, 'src', n))
    s = c28tc.Settings(dest, cgt=cgt, cflags='-v28 -ml -mt --float_support=fpu32 -O1',
                       tool_cflags='--gen_func_subsections=on', native=native, wine=wine, prefix=prefix,
                       rtslib='lib/rts2800_fpu32.lib', jobs=4)
    tc = c28tc.Toolchain(s)
    orig = os.path.join(dest, 'orig'); os.makedirs(orig, exist_ok=True)
    objs = [tc.compile(os.path.join(dest, 'src', n), orig) for n in sources]
    p = tc.tool_in(cgt, 'cl2000', ['-z', '-o', 'orig.out', '-m', 'orig.map', '--rom_model', '--stack_size=0x300']
                   + objs + [os.path.join(FW, 'link.cmd'), s.rtslib], orig)
    if p.returncode or not os.path.exists(os.path.join(orig, 'orig.out')):
        raise c28tc.ToolError('the original link failed:\n' + p.stdout + p.stderr)
    os.makedirs(os.path.join(dest, 'firmware'), exist_ok=True)
    with open(os.path.join(orig, 'hex.cmd'), 'w') as f:
        f.write('orig.out\n-b\n-image\n-order MS\n-romwidth 16\n-fill 0xFFFF\n')
        f.write('ROMS\n{\n  FLASH: origin = 0x%X, length = 0x%X, romwidth = 16, files = { %s }\n}\n'
                % (2 * BASE, 2 * WORDS, 'app.bin'))
    tc.run_native('hex2000', ['hex.cmd'], cwd=orig)
    shutil.copy(os.path.join(orig, 'app.bin'), os.path.join(dest, 'firmware', 'app.bin'))
    with open(os.path.join(orig, 'orig.map')) as f: inputs, syms = parse_link_map(f.read())
    funcs, data = make_map(inputs, syms)
    os.makedirs(os.path.join(dest, 'map'), exist_ok=True)
    c28map.write_tsv(os.path.join(dest, 'map', 'functions.tsv'), funcs)
    c28map.write_tsv(os.path.join(dest, 'map', 'data.tsv'), data)
    cin = [x for x in inputs if x[5].startswith('.cinit')]
    order = []
    for out, load, size, lib, mem, sec, runoff in cin:
        e = ('rts:' + mem) if lib else c28tc.stem(mem)
        if e not in order: order.append(e)
    stack = next(a for p, a, n in syms if n == '__stack')
    with open(os.path.join(dest, 'exhume.toml'), 'w') as f:
        f.write(TOML.format(cgt=cgt, base=BASE, words=WORDS, native=native, wine=wine or '', prefix=prefix or '',
                            others=os.path.join(os.path.dirname(cgt), '*', 'bin', 'cl2000*'),
                            cinit=min(x[1] for x in cin), order=', '.join('"%s"' % e for e in order),
                            stack=stack, stack_size=0x300))
    return os.path.join(dest, 'exhume.toml')
