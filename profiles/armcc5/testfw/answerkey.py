"""Build Exhume's armcc5 test firmware and write what matching needs: the target image
(target/testfw.hex), the map with placeholder names (map/functions.tsv, map/data.tsv) and the
answers (real name -> address and source file), all from armlink's own listing.

    EXHUME_ARMCC_HOME=... python3 answerkey.py ROOT    (ROOT gets src/, build/, target/, map/)"""
import os, re, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import armtc, armmap
from armmap import Row

OPTS = '--cpu Cortex-M3 -O1 -Otime --split_sections --library_type=microlib'
# MicroLIB's startup wants the stack top a startup file would define
SP_SYMDEFS = '#<SYMDEFS># ARM Linker, 5060960: Last Updated: Thu Jan  1 00:00:00 1970\n0x20002000 D __initial_sp\n'
# Arm's C library startup (__main, __scatterload) is linked in, so RW data is compressed
SCATTER = '''LR_IROM 0x08000000 0x8000 {
  ER_IROM 0x08000000 0x8000 { *(InRoot$$Sections) .ANY (+RO) }
  RW_IRAM 0x20000000 0x2000 { .ANY (+RW +ZI) }
}
'''
# armlink --map: "    0x08000000   0x00000018   Code   RO      3    i.main      main.o"
#                "    0x08000000   0x00000008   Code   RO    145  * !!!main     c_w.l(__main.o)"
_MAPLINE = re.compile(r'^\s*0x([0-9a-f]{8})\s+0x([0-9a-f]{8})\s+(Code|Data|Zero|PAD)\s+(RO|RW)\s+\d+\s+(?:\*\s+)?(\S+)\s+(\S+)\s*$')
_OURS = re.compile(r'^(\w+)\.o$')
# armlink --symbols: "    g_tbl     0x20000202   Data   6  x.o(.data)"
_SYMLINE = re.compile(r'^\s{4}(\w+)\s+0x([0-9a-f]{8})\s+(Thumb Code|ARM Code|Data)\s+(\d+)\s+(\w+)\.o\(([^)]+)\)\s*$')


def build(tc, root, srcdir=os.path.join(HERE, 'src')):
    src = os.path.join(root, 'src'); os.makedirs(src, exist_ok=True)
    work = os.path.join(root, 'build', 'answerkey'); os.makedirs(work, exist_ok=True)
    objs = []
    for n in sorted(os.listdir(srcdir)):
        if not n.endswith('.c'): continue
        shutil.copy(os.path.join(srcdir, n), os.path.join(src, n))
        obj = os.path.join(work, n[:-2] + '.o')
        ok, log = tc.compile(os.path.join(src, n), obj, OPTS)
        if not ok: raise RuntimeError(f'{n}: {log}')
        objs.append(obj)
    with open(os.path.join(work, 'fw.sct'), 'w') as f: f.write(SCATTER)
    with open(os.path.join(work, 'sp.sym'), 'w') as f: f.write(SP_SYMDEFS)
    axf = os.path.join(work, 'testfw.axf'); lst = os.path.join(work, 'testfw.lst')
    if os.path.exists(axf): os.remove(axf)
    rc, out = tc.run('armlink', ['--cpu', 'Cortex-M3', '--library_type=microlib', '--scatter', 'fw.sct', '--entry', 'main',
                                 '--keep', 'tables.o(i.tbl)', '--keep', 'tables.o(i.cfg)', '--keep', 'tables.o(i.mixed)',
                                 '--keep', 'tables.o(i.buf)', '--keep', 'tables.o(i.version)', '--keep', 'tables.o(i.lookup)', '--keep', 'tables.o(i.big_switch)', '--keep', 'init.o(i.init)',
                                 '--map', '--symbols', '--list', 'testfw.lst'] +
                     [os.path.basename(o) for o in objs] + ['sp.sym', '-o', 'testfw.axf'], work)
    if rc or not os.path.exists(axf): raise RuntimeError(out)
    os.makedirs(os.path.join(root, 'target'), exist_ok=True)
    rc, out = tc.run('fromelf', ['--i32', 'testfw.axf', '-o', tc.inside(os.path.join(root, 'target', 'testfw.hex'))], work)
    if rc: raise RuntimeError(out)
    with open(lst, encoding='latin1') as f: listing = f.read().splitlines()
    sections, lib_code, table = {}, [], None
    for l in listing:
        m = _MAPLINE.match(l)
        if not m: continue
        addr, size, kind, obj, sec = int(m.group(1), 16), int(m.group(2), 16), m.group(3), m.group(6), m.group(5)
        mine = _OURS.match(obj)
        if mine: sections[(mine.group(1), sec)] = (addr, size)
        elif kind == 'Code' and size: lib_code.append((addr, size))
        if sec == 'Region$$Table': table = addr
    answers, functions, data = {}, [], []
    # library code (Arm's startup and helpers) is in the map too, as kind lib
    for addr, size in lib_code:
        functions.append(Row(addr, size, f'sub_{addr:08X}', 'lib', ''))
    for l in listing:
        m = _SYMLINE.match(l)
        if not m: continue
        name, addr, kind, size, stem, section = m.groups(); addr = int(addr, 16); size = int(size)
        if (stem, section) not in sections: continue        # a library member, not one of our sources
        if 'Code' in kind:
            addr &= ~1
            base, ssize = sections[(stem, section)]
            answers[name] = (addr, stem)
            functions.append(Row(addr, ssize, f'sub_{addr:08X}', 'code', ''))
        else:
            answers[name] = (addr, stem)
            k = 'ro' if addr < 0x20000000 else 'ram'
            data.append(Row(addr, size, f'{"c" if k == "ro" else "g"}_{addr:08X}', k, ''))
    os.makedirs(os.path.join(root, 'map'), exist_ok=True)
    armmap.save(os.path.join(root, 'map', 'functions.tsv'), functions)
    armmap.save(os.path.join(root, 'map', 'data.tsv'), data)
    import ehex, struct
    img = ehex.Image(os.path.join(root, 'target', 'testfw.hex'))
    load, exe, size, fn = struct.unpack_from('<IIII', img.data, table - img.base)
    return {'answers': answers, 'rw': (load, exe, size)}


if __name__ == '__main__':
    home = os.environ['EXHUME_ARMCC_HOME']; root = os.path.abspath(sys.argv[1])
    r = build(armtc.Toolchain(home, root), root)
    for n, (a, s) in sorted(r['answers'].items(), key=lambda x: x[1][0]): print(f'0x{a:08X} {s:8} {n}')
