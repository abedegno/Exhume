"""Compile a source file and compare every function with the original executable.

    python3 tools/match.py SRC [--dis NAME] [--no-build] [--config PATH]

The target table is <targets>/<segment>.tsv, named by a `/* target: SEG */` line in the
source; switches come from its `/* opts: ... */` line. Each public function found in both
the object and the table is reported as MATCH, or how many bytes differ and where, or its
size against the table's. --dis NAME adds an instruction diff for one function (jump and
call targets hidden, so a length difference shows as the instruction that caused it).
--no-build re-compares the last build.

Bytes written by fixups (addresses of globals, call targets, segment values) are masked:
names and addresses need not be right yet, only near/far-ness. That is why verify.py must
follow. With the profile's overlay_call_rewrite, the linker's rewrite of a far call into the
same overlay (9A ... -> 90 0E E8 ...) counts as equal.

The last line says WHOLE SEGMENT MATCHES when the object's code equals the EXE's from the
table's base for at least the table's size; code running past the table (a switch jump
table after the last function) counts if those bytes match too."""
import sys, os, re, difflib
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config
from omf import module_masked, publics
from build import build


def compare(b, m, o, rewrite=True):
    """Indices where object bytes b (mask m: 1 = written by a fixup) differ from original o."""
    b = bytearray(b); m = list(m)
    if rewrite:
        for j in range(len(b) - 4):
            if b[j] == 0x9a and all(m[j + 1:j + 5]) and o[j:j + 3] == b'\x90\x0e\xe8':
                b[j:j + 2] = b'\x90\x0e'; m[j + 2] = 0; b[j + 2] = 0xe8; m[j + 1] = 0
    return [k for k in range(min(len(b), len(o))) if not m[k] and b[k] != o[k]]


def dis(buf):
    from iced_x86 import Decoder, Formatter, FormatterSyntax
    f = Formatter(FormatterSyntax.NASM); r = []
    for i in Decoder(16, bytes(buf)):
        s = f.format(i); mn = s.split()[0]
        r.append(mn if mn.startswith('j') or mn in ('call', 'loop') else s)
    return r


def code_segment(cfg, segs):
    return [i for i, (sn, cn, ln) in enumerate(segs, 1) if cfg.is_code_class(cn)]


def main(argv):
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    if not a or a[0].startswith('-'): sys.exit(__doc__)
    src = a[0]; stem = cfg.stem(src)
    seg, opts = cfg.directives(src)
    if not seg: sys.exit(f'{src}: no /* target: SEG */ line')
    rewrite = cfg.profile.get('link', {}).get('overlay_call_rewrite', False)
    for attempt in range(2):
        if '--no-build' not in a:
            ok, msgs = build(cfg, [os.path.abspath(src)])[stem]
            for l in msgs: print(l)
            if not ok: return 1
        d = open(cfg.obj(stem), 'rb').read()
        _, segs, data, mask, _ = module_masked(d)
        ci = code_segment(cfg, segs)
        if ci or '--no-build' in a: break
        # the emulator occasionally returns an object with no code segment; build once more
        print('object has no code segment; rebuilding')
    if not ci: print('object has no code segment'); return 1
    ci = ci[0]; pubs = publics(d)
    code, cm = data[ci], mask[ci]
    base, size, rows, org = cfg.load_targets(seg); exe = cfg.exe
    whole = len(code) >= size and not compare(code, cm, exe[base:base + len(code)], rewrite)
    done = 0
    for c, ida, off, sz in rows:
        p = pubs.get(cfg.mangle(c))
        if p is None: continue
        o = p[1]
        # a function's end in the object: the next public, or the end of the code
        nxt = min([q[1] for q in pubs.values() if q[0] == ci and q[1] > o] + [len(code)])
        mine = nxt - o; last = nxt == len(code)
        bad = compare(code[o:nxt], cm[o:nxt], exe[base + off:base + off + (mine if last and mine > sz else sz)], rewrite)
        ok = (mine == sz or last and mine > sz) and not bad
        if ok: done += sz
        state = 'MATCH' if ok else (f'{len(bad)} bytes differ, first at +0x{bad[0]:X}' if bad else '') + ('' if mine == sz else f'  size 0x{mine:X} want 0x{sz:X}')
        print(f'{c:22} {ida:45} {state}')
        if '--dis' in a and a[a.index('--dis') + 1] == c:
            for t in difflib.unified_diff(dis(exe[base + off:base + off + sz]), dis(code[o:nxt]), 'exe', 'obj', lineterm='', n=2):
                print('   ', t)
    print(f'-- {done}/{size} bytes of {seg} matched' + ('; WHOLE SEGMENT MATCHES' if whole else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
