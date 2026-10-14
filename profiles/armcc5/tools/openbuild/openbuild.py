"""The firmware's source built with an open toolchain: clang for Thumb and GNU ld, with the
original's RAM layout. Not byte for byte (the matcher and the exact link prove that); a second
build of the same C, checked against the original with the differential tests and run in the
system emulator (docs/firmware-testing.md, "The open-toolchain build").

    python3 profiles/armcc5/tools/openbuild/openbuild.py [--config PATH] [--opt -O2] [--no-ram-layout]

Reads [openbuild] (docs/config.md). Writes OUT/app.elf and OUT/app.hex (OUT is [openbuild] out,
default build/open), with OUT/tree the rewritten copy of the sources and OUT/obj the objects.

What it does to the sources, none of it in the project's own tree:
- copies them (and every header under [project] src and the include directories) to OUT/tree,
  applying the rewrites: `__packed struct` and `__packed union` become clang's packed attribute
  after the keyword (clang ignores it before), then the project's [openbuild] rewrite pairs;
- force-includes compat.h (armcc's keywords and intrinsics);
- compiles each with -fshort-enums (armcc gives an enum the smallest type that holds it; without
  this every struct with an enum field changes layout), -fno-common, -ffunction-sections and
  -fdata-sections (one section per variable, for ramlayout.py), -ffreestanding -fno-builtin;
- takes each source's -D and -I from its `/* opts: */` line, as the armcc build does;
- links the profile's runtime.c and runtime.S (memset, memcpy, 64-bit division and shifts) and
  [openbuild] extra (a startup file, the project's own library stand-ins), with link.ld and a
  ram.ld from ramlayout.py that puts every RAM symbol at the original's address."""
import argparse, glob, os, re, shlex, shutil, subprocess, sys
HERE = os.path.dirname(os.path.abspath(__file__))
EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE))))
sys.path[:0] = [HERE, os.path.join(os.path.dirname(HERE), 'emu'), os.path.join(EXHUME, 'tools')]
import ramlayout

DEFAULT_REWRITES = [
    (r'__packed(\s+)(struct|union)\b', r'\2 __attribute__((packed))'),
]


class BuildError(Exception):
    pass


def settings(cfg, over=None):
    o = dict(cfg.raw.get('openbuild', {}) if cfg else {}); o.update(over or {})
    root = cfg.root if cfg else os.getcwd()
    rel = lambda p: os.path.normpath(p if os.path.isabs(p) else os.path.join(root, p))
    s = {
        'root': root,
        'out': rel(o.get('out', 'build/open')),
        'src': rel(o['src']) if 'src' in o else (cfg.src if cfg else rel('src')),
        'sources': o.get('sources'),
        'exclude': o.get('exclude', []),
        'include': [rel(p) for p in o.get('include', [])] + ([cfg.include] if cfg and os.path.isdir(cfg.include) else []),
        'extra': [rel(p) for p in o.get('extra', [])],
        'cc': o.get('cc', 'clang'), 'target': o.get('target', 'thumbv7m-none-eabi'), 'cpu': o.get('cpu', 'cortex-m3'),
        'opt': o.get('opt', '-Os'), 'cflags': o.get('cflags', ''), 'defines': o.get('defines', []),
        'short_enums': o.get('short_enums', True),
        'rewrite': ([] if o.get('default_rewrites', True) is False else DEFAULT_REWRITES) + [tuple(x) for x in o.get('rewrite', [])],
        'flash': tuple(o.get('flash', (0x08000000, 0x80000))), 'ram': tuple(o.get('ram', (0x20000000, 0x10000))),
        'ram_layout': o.get('ram_layout', True),
        'ram_syms': rel(o['ram_syms']) if 'ram_syms' in o else (cfg.map if cfg else None),
        'entry': o.get('entry', 'main'), 'runtime': o.get('runtime', True), 'libs': o.get('libs', []),
        'ld': o.get('ld', 'arm-none-eabi-ld'), 'objcopy': o.get('objcopy', 'arm-none-eabi-objcopy'),
        'objdump': o.get('objdump', 'arm-none-eabi-objdump'),
        'docker_root': o.get('docker_root', '/work'),
    }
    return s


def sources(s):
    pats = s['sources'] or [os.path.join(os.path.relpath(s['src'], s['root']), '**', '*.c')]
    out = []
    for p in pats:
        out += glob.glob(os.path.join(s['root'], p), recursive=True)
    ex = set()
    for p in s['exclude']: ex |= set(glob.glob(os.path.join(s['root'], p), recursive=True))
    return sorted(set(os.path.normpath(x) for x in out) - set(os.path.normpath(x) for x in ex))


def rewrite(text, rules):
    for pat, rep in rules: text = re.sub(pat, rep, text)
    return text


def copy_tree(s, srcs):
    """The sources and headers, rewritten, under OUT/tree at their paths relative to the root."""
    tree = os.path.join(s['out'], 'tree')
    shutil.rmtree(tree, ignore_errors=True)
    files = set(srcs)
    for d in [s['src']] + s['include']:
        files |= set(glob.glob(os.path.join(d, '**', '*.h'), recursive=True))
    for f in files:
        dst = os.path.join(tree, os.path.relpath(f, s['root']))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        with open(f, encoding='latin1') as fh: text = fh.read()
        with open(dst, 'w', encoding='latin1') as fh: fh.write(rewrite(text, s['rewrite']))
    return tree


def opts_flags(path, s):
    """-D and -I from a source's /* opts: */ line; a path under the toolchain container's project
    mount (docker_root) is mapped back to the copy of the tree."""
    with open(path, encoding='latin1') as f: text = f.read(4000)
    m = re.search(r'/\*\s*opts:\s*([^*]+?)\s*\*/', text)
    if not m: return []
    out = []
    toks = shlex.split(m.group(1))
    for i, t in enumerate(toks):
        if t.startswith('-D'): out.append(t if len(t) > 2 else '-D' + toks[i + 1])
        elif t.startswith('-I'):
            p = t[2:] or toks[i + 1]
            if p.startswith(s['docker_root'] + '/'): p = os.path.join(s['out'], 'tree', p[len(s['docker_root']) + 1:])
            out.append('-I' + p)
    return out


def cc_base(s):
    c = [s['cc'], '-target', s['target'], f'-mcpu={s["cpu"]}', '-mthumb', '-mfloat-abi=soft', '-std=gnu90', s['opt'],
         '-ffreestanding', '-fno-builtin', '-fno-common', '-ffunction-sections', '-fdata-sections', '-w']
    if s['short_enums']: c.append('-fshort-enums')
    return c + shlex.split(s['cflags']) + ['-D' + d for d in s['defines']]


def run(cmd, cwd=None):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if r.returncode: raise BuildError(' '.join(cmd) + '\n' + r.stdout + r.stderr)
    return r.stdout


def build(cfg=None, over=None, log=print):
    """Build; returns {'elf', 'hex', 'notes', 'objects'}. Raises BuildError with the tool's output."""
    s = settings(cfg, over)
    srcs = sources(s)
    if not srcs: raise BuildError('no sources (check [openbuild] sources and [project] src)')
    obj_dir = os.path.join(s['out'], 'obj')
    shutil.rmtree(obj_dir, ignore_errors=True); os.makedirs(obj_dir)
    tree = copy_tree(s, srcs)
    incs = ['-I' + os.path.join(tree, os.path.relpath(d, s['root'])) for d in s['include']]
    base = cc_base(s) + ['-include', os.path.join(HERE, 'compat.h')]
    objs = []
    for f in srcs:
        rel = os.path.relpath(f, s['root']); copy = os.path.join(tree, rel)
        obj = os.path.join(obj_dir, rel.replace(os.sep, '_')[:-2] + '.o')
        run(base + opts_flags(copy, s) + incs + ['-c', os.path.basename(copy), '-o', obj], cwd=os.path.dirname(copy))
        objs.append(obj)
    extra = list(s['extra'])
    if s['runtime']: extra += [os.path.join(HERE, 'runtime.c'), os.path.join(HERE, 'runtime.S')]
    for f in extra:
        obj = os.path.join(obj_dir, 'x_' + os.path.basename(f).replace('.', '_') + '.o')
        flags = cc_base(s) if f.endswith('.c') else [s['cc'], '-target', s['target'], f'-mcpu={s["cpu"]}', '-mthumb']
        run(flags + incs + ['-c', f, '-o', obj]); objs.append(obj)
    notes = []
    if s['ram_layout'] and s['ram_syms'] and not os.path.exists(s['ram_syms']):
        if 'ram_syms' in (over or {}) or (cfg and 'ram_syms' in cfg.raw.get('openbuild', {})):
            raise BuildError(f'{s["ram_syms"]}: no such symbol file ([openbuild] ram_syms)')
        notes.append(f'no map at {s["ram_syms"]}: RAM in the linker\'s own order'); s['ram_layout'] = False
    if s['ram_layout'] and s['ram_syms']:
        text, notes = ramlayout.place(objs, ramlayout.ram_symbols(s['ram_syms'], s['ram']), s['ram'],
                                      s['objdump'], s['objcopy'])
    else:
        text, _ = ramlayout.place([], {}, s['ram'])
    with open(os.path.join(s['out'], 'ram.ld'), 'w') as f: f.write(text)
    with open(os.path.join(HERE, 'link.ld')) as f: ld = f.read()
    for k, v in (('FLASH_ORIGIN', s['flash'][0]), ('FLASH_LENGTH', s['flash'][1]), ('RAM_ORIGIN', s['ram'][0]),
                 ('RAM_LENGTH', s['ram'][1])):
        ld = ld.replace(f'@{k}@', f'0x{v:X}')
    with open(os.path.join(s['out'], 'link.ld'), 'w') as f: f.write(ld)
    elf, hexf = os.path.join(s['out'], 'app.elf'), os.path.join(s['out'], 'app.hex')
    run([s['ld'], '-L', s['out'], '-T', os.path.join(s['out'], 'link.ld'), '-e', s['entry'],
         '-o', elf] + objs + s['libs'], cwd=s['out'])
    run([s['objcopy'], '-O', 'ihex', elf, hexf])
    for n in notes: log('note: ' + n)
    log(f'{len(srcs)} sources compiled: {elf}')
    return {'elf': elf, 'hex': hexf, 'notes': notes, 'objects': objs}


def main(argv):
    import config
    cpath, argv = config.pop_config(argv)
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--opt'); ap.add_argument('--no-ram-layout', action='store_true')
    o = ap.parse_args(argv)
    over = {}
    if o.opt: over['opt'] = o.opt
    if o.no_ram_layout: over['ram_layout'] = False
    try:
        build(config.load(cpath), over)
    except BuildError as e:
        print(e, file=sys.stderr); return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
