"""The emulator tests' firmware (testfw/emu): system.c, quirks.c and the test firmware's crc.c,
proto.c, tables.c and init.c, with a vector table, built with Arm Compiler 5 and MicroLIB as
an original would be (target/emufw.hex and the linker's ELF, build/emufw/emufw.axf), or the
same C laid out for the open build (sources only: openbuild.py builds it).

    EXHUME_ARMCC_HOME=... python3 emufw.py ROOT"""
import os, shutil, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import armtc

OPTS = '--cpu Cortex-M3 -O1 -Otime --split_sections --library_type=microlib'
FROM_TESTFW = ('crc.c', 'proto.c', 'tables.c', 'init.c')
C_FILES = FROM_TESTFW + ('system.c', 'quirks.c')
SCATTER = '''LR_IROM 0x08000000 0x8000 {
  ER_IROM 0x08000000 0x8000 { boot.o(RESET, +First) *(InRoot$$Sections) .ANY (+RO) }
  RW_IRAM 0x20000000 0x2000 { .ANY (+RW +ZI) }
}
'''
FLASH, RAM = (0x08000000, 0x8000), (0x20000000, 0x2000)


def stage(root):
    """Copy the sources to ROOT/src; returns their paths (C, then the armasm vector table)."""
    src = os.path.join(root, 'src'); os.makedirs(src, exist_ok=True)
    out = []
    for n in C_FILES:
        d = os.path.join(HERE, 'src' if n in FROM_TESTFW else 'emu', n)
        shutil.copy(d, os.path.join(src, n)); out.append(os.path.join(src, n))
    for n in ('boot.s', 'boot_gnu.S'):
        shutil.copy(os.path.join(HERE, 'emu', n), os.path.join(root, n))
    return out


def build_armcc(tc, root):
    """(hex path, axf path) of the armcc build."""
    srcs = stage(root)
    work = os.path.join(root, 'build', 'emufw'); os.makedirs(work, exist_ok=True)
    objs = []
    for p in srcs + [os.path.join(root, 'boot.s')]:
        obj = os.path.join(work, os.path.basename(p).rsplit('.', 1)[0] + '.o')
        ok, log = tc.assemble(p, obj, '--cpu Cortex-M3') if p.endswith('.s') else tc.compile(p, obj, OPTS)
        if not ok: raise RuntimeError(f'{p}: {log}')
        objs.append(obj)
    with open(os.path.join(work, 'emufw.sct'), 'w') as f: f.write(SCATTER)
    axf = os.path.join(work, 'emufw.axf')
    if os.path.exists(axf): os.remove(axf)
    keep = []
    for n in C_FILES: keep += ['--keep', n[:-2] + '.o(i.*)']
    rc, out = tc.run('armlink', ['--cpu', 'Cortex-M3', '--library_type=microlib', '--scatter', 'emufw.sct',
                                 '--entry', 'Reset_Handler', '--keep', 'boot.o(RESET)'] + keep +
                     [os.path.basename(o) for o in objs] + ['-o', 'emufw.axf', '--map', '--list', 'emufw.lst'], work)
    if rc or not os.path.exists(axf): raise RuntimeError(out)
    os.makedirs(os.path.join(root, 'target'), exist_ok=True)
    hexf = os.path.join(root, 'target', 'emufw.hex')
    rc, out = tc.run('fromelf', ['--i32', 'emufw.axf', '-o', tc.inside(hexf)], work)
    if rc: raise RuntimeError(out)
    return hexf, axf


if __name__ == '__main__':
    root = os.path.abspath(sys.argv[1])
    print(build_armcc(armtc.Toolchain(os.environ['EXHUME_ARMCC_HOME'], root), root))
