"""Map a small- or compact-model Borland C program without an IDA listing.

    python3 tools/mapsmall.py [--config PATH] [--model s|c]
                                       writes <map>/functions.tsv and <map>/library.tsv

In the small model every C file shares one _TEXT segment, so files are not segments and
there is no listing to read them from. This finds:

- functions: a recursive descent from the entry point, every near and far call target, and
  every standard frame (push bp; mov bp,sp) as a seed, each followed to its last return;
- Borland's runtime library: the start-up module (C0S.OBJ) and every code block of the
  toolchain's CS.LIB and MATHS.LIB modules found byte for byte (fixups as wildcards; the
  memory model's letter from --model or the config's c_opts, -ms by default)
  (library.tsv: address, size, module);
- for each function outside the library: its size, the functions it calls, and the DGROUP
  offsets it reads, writes or takes the address of (functions.tsv), which is what places
  file boundaries: a Borland C file's initialised data and strings follow the previous
  file's in DGROUP, in the same link order as its code in _TEXT.

Reads the config's [binary] exe and dgroup_para and [toolchain] home (its LIB directory).
First used on Dangerous Dave in the Haunted Mansion (Borland C++ 2.0, small model).
"""
import os
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config  # noqa: E402
import omf     # noqa: E402
from iced_x86 import Decoder, FlowControl, OpKind, Register  # noqa: E402


def main(argv):
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    model = a[a.index("--model") + 1] if "--model" in a else \
        (re.search(r"-m([tsmclh])", cfg.c_opts).group(1) if re.search(r"-m([tsmclh])", cfg.c_opts) else "s")
    M = model.upper()
    out_dir = Path(cfg.map)
    exe = cfg.exe
    hdr = struct.unpack_from("<H", exe, 8)[0] * 16
    ds = hdr + cfg.dgroup_para * 16
    nrel, rtab = struct.unpack_from("<H", exe, 6)[0], struct.unpack_from("<H", exe, 0x18)[0]
    relocs = {hdr + s * 16 + o for o, s in (struct.unpack_from("<HH", exe, rtab + 4 * i) for i in range(nrel))}
    # _TEXT: from the image start to the first segment a relocation names above 0
    segs = sorted({struct.unpack_from("<H", exe, r)[0] for r in relocs})
    text_end = hdr + min(s for s in segs if s) * 16
    text = exe[hdr:text_end]

    # ---- the runtime library ----
    lib_dir = Path(cfg.home) / "LIB"
    library = []                                    # (start, size, module, names)
    for libname in (f"C{M}.LIB", f"MATH{M}.LIB"):
        data = (lib_dir / libname).read_bytes()
        for name, segs_, blocks, masks, end in omf.lib_modules(data):
            for k in blocks:
                code, mask = bytes(blocks[k]), masks[k]
                if len(code) < 12 or sum(1 for m in mask if not m) < 10:
                    continue
                pat = re.compile(b"".join(b"." if mask[i] else re.escape(code[i:i + 1]) for i in range(len(code))), re.S)
                hits = [m.start() for m in pat.finditer(exe, hdr, ds)]
                if len(hits) == 1:
                    library.append((hits[0] - hdr, len(code), f"{libname}:{name}"))
    # C0S.OBJ, the start-up code, is linked first (its code equals the toolchain's byte for byte)
    c0 = omf.module_masked((lib_dir / f"C0{M}.OBJ").read_bytes())
    c0_code = next(bytes(v) for v in c0[2].values() if len(v) > 100)
    library.append((0, len(c0_code), f"C0{M}.OBJ"))
    library.sort()
    in_lib = lambda a: any(s <= a < s + n for s, n, _ in library)

    # ---- functions ----
    seeds = {0} | {i for i in range(len(text) - 2) if text[i:i + 3] == b"\x55\x8b\xec"}
    funcs, todo = {}, sorted(seeds)
    while todo:
        start = todo.pop()
        if start in funcs or not 0 <= start < len(text):
            continue
        seen, work, calls, data_refs, end = set(), [start], set(), set(), start
        while work:
            pc = work.pop()
            if pc in seen or not 0 <= pc < len(text):
                continue
            d = Decoder(16, text[pc:], ip=pc)
            for ins in d:
                if ins.ip in seen:
                    break
                seen.add(ins.ip)
                end = max(end, ins.ip + ins.len)
                for k in range(ins.op_count):
                    if ins.op_kind(k) == OpKind.MEMORY and ins.memory_base == Register.NONE \
                            and ins.memory_index == Register.NONE and ins.memory_segment in (Register.DS, Register.NONE):
                        data_refs.add(ins.memory_displacement)
                fc = ins.flow_control
                if fc == FlowControl.CALL and ins.op0_kind == OpKind.NEAR_BRANCH16:
                    calls.add(ins.near_branch16)
                    if ins.near_branch16 not in funcs:
                        todo.append(ins.near_branch16)
                if fc in (FlowControl.CONDITIONAL_BRANCH,):
                    work.append(ins.near_branch16)
                if fc == FlowControl.UNCONDITIONAL_BRANCH:
                    if ins.op0_kind == OpKind.NEAR_BRANCH16:
                        work.append(ins.near_branch16)
                    break
                if fc in (FlowControl.RETURN, FlowControl.INDIRECT_BRANCH, FlowControl.EXCEPTION, FlowControl.INTERRUPT) \
                        and fc != FlowControl.INTERRUPT:
                    break
        funcs[start] = (end - start, sorted(calls), sorted(data_refs))
    # a function's extent ends where the next one starts
    starts = sorted(funcs)
    out = []
    for i, s in enumerate(starts):
        nxt = starts[i + 1] if i + 1 < len(starts) else len(text)
        size, calls, refs = funcs[s]
        lib = next((m for a, n, m in library if a <= s < a + n), "")
        out.append((s, min(size, nxt - s), nxt - s, lib, calls, refs))

    out_dir.mkdir(exist_ok=True)
    with open(out_dir / "functions.tsv", "w") as f:
        f.write("# offset\tcode bytes\tto next\tlibrary module\tcalls\tDGROUP offsets referenced\n")
        for s, size, gap, lib, calls, refs in out:
            f.write(f"{s:04X}\t{size}\t{gap}\t{lib}\t{' '.join(f'{c:04X}' for c in calls)}\t{' '.join(f'{r:04X}' for r in refs)}\n")
    with open(out_dir / "library.tsv", "w") as f:
        f.write("# offset in _TEXT\tsize\tlibrary:module\n")
        for s, n, m in library:
            f.write(f"{s:04X}\t{n}\t{m}\n")
    game = [o for o in out if not o[3]]
    print(f"_TEXT {len(text)} bytes: {len(out)} functions, {len(out) - len(game)} in the runtime library "
          f"({len(library)} library code blocks placed); {len(game)} game functions, "
          f"{sum(o[2] for o in game)} bytes")


if __name__ == "__main__":
    main(sys.argv[1:])
