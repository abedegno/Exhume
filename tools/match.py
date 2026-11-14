"""Compile a source file and compare every function with the original executable.

    python3 tools/match.py SRC [--dis NAME] [--no-build] [--object PATH] [--config PATH]

The target table is <targets>/<segment>.tsv, named by a `/* target: SEG */` line in the
source; switches come from its `/* opts: ... */` line. Each public function found in both
the object and the table is reported as MATCH, or how many bytes differ and where, or its
size against the table's. --dis NAME adds an instruction diff for one function (jump and
call targets hidden, so a length difference shows as the instruction that caused it).
--no-build re-compares the last build.

--object PATH reads an existing proof object instead of the canonical build object and
never compiles, including when --no-build is omitted.

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

# The floating-point emulator's fixups: absolute values the linker adds to an 8087
# instruction's bytes, turning WAIT/ESC (9B D8..DF) into INT 34h-3Dh (Borland and Microsoft
# libraries alike). FI* fixups add to a word, FJ* to the byte after a segment override.
EMULATOR = {'FIDRQQ': 0x5C32, 'FIWRQQ': 0xA23D, 'FIERQQ': 0x1632, 'FICRQQ': 0x0E32,
            'FISRQQ': 0x0632, 'FIARQQ': 0xFE32, 'FJCRQQ': 0xC000, 'FJSRQQ': 0x8000,
            'FJARQQ': 0x4000}


def apply_emulator(obj, ci, code, mask):
    """Add the emulator fixups' values into code segment ci's bytes and unmask them, so that
    floating-point code compares (and disassembles) as the linked program has it."""
    from fixups import fixups
    o = fixups(obj)
    for f in o['fixups']:
        if f['seg'] != ci or f['target'][0] != 2: continue
        v = EMULATOR.get(o['ext'][f['target'][1]])
        if v is None: continue
        at = f['off']
        if f['loc'] in (1, 5):
            w = (code[at] | code[at + 1] << 8) + v + f['disp']
            code[at] = w & 0xFF; code[at + 1] = w >> 8 & 0xFF; mask[at] = mask[at + 1] = 0
        elif f['loc'] in (0, 4):
            code[at] = (code[at] + ((v >> 8) if f['loc'] == 4 else v)) & 0xFF; mask[at] = 0


def compare(b, m, o, rewrite=True):
    """Indices where object bytes b (mask m: 1 = written by a fixup) differ from original o."""
    b = bytearray(b); m = list(m)
    if rewrite:
        for j in range(len(b) - 4):
            if b[j] == 0x9a and all(m[j + 1:j + 5]) and o[j:j + 3] == b'\x90\x0e\xe8':
                b[j:j + 2] = b'\x90\x0e'; m[j + 2] = 0; b[j + 2] = 0xe8; m[j + 1] = 0
    return [k for k in range(min(len(b), len(o))) if not m[k] and b[k] != o[k]]


def dis(buf):
    """Instructions as text; the emulator's INT 34h-3Bh (an 8087 opcode D8-DF follows), INT 3Ch
    (with a segment override) and INT 3Dh (FWAIT) read as the instructions they stand for."""
    from iced_x86 import Decoder, Formatter, FormatterSyntax
    f = Formatter(FormatterSyntax.NASM); r = []; buf = bytes(buf); p = 0
    while p < len(buf):
        b0, b1 = buf[p], buf[p + 1] if p + 1 < len(buf) else 0
        if b0 == 0xCD and 0x34 <= b1 <= 0x3B:
            i = Decoder(16, bytes([0xD8 + b1 - 0x34]) + buf[p + 2:p + 16]).decode(); n = i.len + 1
        elif b0 == 0xCD and b1 == 0x3C and p + 2 < len(buf):
            i = Decoder(16, bytes([0x26, 0xD8 + buf[p + 2] - 0x34]) + buf[p + 3:p + 16]).decode(); n = i.len + 1
        elif b0 == 0xCD and b1 == 0x3D:
            r.append('fwait'); p += 2; continue
        else:
            i = Decoder(16, buf[p:p + 16]).decode(); n = i.len
        s = f.format(i); mn = s.split()[0]
        r.append(mn if mn.startswith('j') or mn in ('call', 'loop') else s)
        p += max(n, 1)
    return r


def code_segment(cfg, segs):
    return [i for i, (sn, cn, ln) in enumerate(segs, 1) if ln and cfg.is_code_class(cn)]


def main(argv):
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    object_path = None
    if '--object' in a:
        i = a.index('--object')
        if i + 1 >= len(a) or a[i + 1].startswith('--'):
            sys.exit('--object requires a path')
        object_path = a[i + 1]
        a = a[:i] + a[i + 2:]
    no_build = object_path is not None or '--no-build' in a
    if cfg.object_model == 'linked-image':
        if object_path is not None:
            sys.exit('--object requires an OMF object profile')
        sys.path.insert(0, os.path.join(cfg.profile_dir, 'tools'))
        import importlib
        return importlib.import_module(cfg.profile.get('link', {}).get('match_tool', 'armmatch')).main(cfg, a)
    if not a or a[0].startswith('-'): sys.exit(__doc__)
    src = a[0]; stem = cfg.stem(src)
    seg, opts = cfg.directives(src)
    if not seg: sys.exit(f'{src}: no /* target: SEG */ line')
    rewrite = cfg.profile.get('link', {}).get('overlay_call_rewrite', False)
    for attempt in range(2):
        if not no_build:
            ok, msgs = build(cfg, [os.path.abspath(src)])[stem]
            for l in msgs: print(l)
            if not ok: return 1
        with open(object_path if object_path is not None else cfg.obj(stem), 'rb') as stream:
            d = stream.read()
        _, segs, data, mask, _ = module_masked(d)
        ci = code_segment(cfg, segs)
        if ci or no_build: break
        # the emulator occasionally returns an object with no code segment; build once more
        print('object has no code segment; rebuilding')
    if not ci: print('object has no code segment'); return 1
    ci = ci[0]; pubs = publics(d)
    code, cm = data[ci], mask[ci]
    apply_emulator(d, ci, code, cm)
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
            # bytes a fixup writes take the original's value, so only real differences show
            mine_b = bytearray(code[o:nxt])
            for k in range(min(len(mine_b), sz)):
                if cm[o + k]: mine_b[k] = exe[base + off + k]
            for t in difflib.unified_diff(dis(exe[base + off:base + off + sz]), dis(mine_b), 'exe', 'obj', lineterm='', n=2):
                print('   ', t)
    if whole and len(code) > size:
        print(f'note: the code runs 0x{len(code) - size:X} bytes past the table (they match); unless that is '
              f'a jump table, the table is too short and the next file starts later')
    print(f'-- {done}/{size} bytes of {seg} matched' + ('; WHOLE SEGMENT MATCHES' if whole else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
