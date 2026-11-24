"""The whole-project gate for Go 1.8.0 binaries (profile: golang-180).

    python3 tools/gate.py check [--all] [--no-build] [--unit NAME ...] [--fail-test] [-v]
    (Dispatched from Exhume's tools/gate.py for `check` and `exact`.)

1. Builds each configured `[[go18.unit]]` with `golang:1.8.0` unless `--no-build` is given or the
   unit's source hash + toolchain config match the last passing gate state (pass `--all` to force
   a fresh build of every unit).
2. Runs `gocmp cmp` (normalized disassembly + exact `.gopclntab` line table) AND `gocmp raw`
   (0 unmasked machine-code byte diffs) for every unit.
3. With `--fail-test`, mutates 1 non-PC-relative byte inside a matched function in a temporary
   copy of the candidate binary and verifies that both `cmp` and `raw` catch the mutation.
4. Prints a coverage table of matched functions and bytes against the target binary's first-party
   code (from `map/functions.tsv`).
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gotc
import gomap
import gomatch


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def _elf_text_section(data):
    """Return (sh_addr, sh_offset, sh_size) for .text in an ELF64 little-endian binary."""
    e_shoff, _, _, _, _, e_shentsize, e_shnum, e_shstrndx = struct.unpack_from('<QIHHHHHH', data, 40)
    secs = []
    for i in range(e_shnum):
        off = e_shoff + i * e_shentsize
        sh_name, _, _, sh_addr, sh_offset, sh_size = struct.unpack_from('<IIQQQQ', data, off)
        secs.append((sh_name, sh_addr, sh_offset, sh_size))
    st_off, st_size = secs[e_shstrndx][2], secs[e_shstrndx][3]
    strtab = data[st_off:st_off + st_size]
    for sh_name, sh_addr, sh_offset, sh_size in secs:
        end = strtab.find(b'\x00', sh_name)
        if strtab[sh_name:end] == b'.text':
            return sh_addr, sh_offset, sh_size
    raise gotc.ToolError('ELF binary has no .text section')


def run_fail_test(tc, orig_bin, cand_bin, regex):
    """Mutate 1 byte of the first matched function in a temp copy and prove gocmp catches it."""
    rows = gomap.parse_funcs_tsv(tc.gofuncs(cand_bin))
    rx = re.compile(regex)
    target = next((r for r in rows if rx.search(r.name) and r.size >= 16), None)
    if target is None:
        return False, f'no function >=16 bytes matching {regex!r} in {cand_bin}'
    with open(cand_bin, 'rb') as f:
        data = bytearray(f.read())
    text_addr, text_off, text_size = _elf_text_section(data)
    fn_file_off = text_off + (target.entry - text_addr)
    if not (text_off <= fn_file_off < text_off + text_size):
        return False, f'{target.name} entry {target.entry:#x} outside .text'
    # Flip the very first opcode byte of the function (never a PC-relative displacement field)
    data[fn_file_off] ^= 0xff
    tmp_dir = tempfile.mkdtemp(prefix='exhume-go18-failtest-')
    try:
        mut = os.path.join(tmp_dir, 'mutated.bin')
        with open(mut, 'wb') as f:
            f.write(data)
        c_res = tc.cmp(orig_bin, mut, '^' + re.escape(target.name) + '$')
        r_res = tc.raw(orig_bin, mut, '^' + re.escape(target.name) + '$')
        if c_res.ok or r_res.ok or r_res.unmasked_diffs < 1:
            return False, f'mutation of {target.name}+0 was NOT detected (cmp.ok={c_res.ok}, raw.bad={r_res.unmasked_diffs})'
        return True, f'mutated {target.name}+0 -> detected (cmp DIFF, raw {r_res.unmasked_diffs} unmasked diff)'
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _parser():
    ap = argparse.ArgumentParser(
        prog='gate.py check',
        description='The whole-project gate for a Go 1.8.0 binary.',
    )
    ap.add_argument('--all', action='store_true', help='force fresh build of every unit')
    ap.add_argument('--fast', action='store_true', help='verify cached/existing units only')
    ap.add_argument('--no-build', action='store_true', help='skip builds and verify existing binaries')
    ap.add_argument('--unit', action='append', dest='units', help='check only the named unit(s)')
    ap.add_argument('--include-wip', action='store_true', help='also gate units marked wip = true')
    ap.add_argument('--fail-test', action='store_true', help='mutate 1 byte in a matched function to verify comparator')
    ap.add_argument('-v', '--verbose', action='store_true', help='show diffs on mismatch')
    return ap


def main(cfg, argv, cfg_path=None):
    args = _parser().parse_args(argv)
    t0 = time.time()
    s = gotc.Settings.from_config(cfg)
    tc = gotc.Toolchain(s)
    if not s.image or not os.path.exists(s.image):
        print(f'gogate: target binary not found: {s.image}', file=sys.stderr)
        return 1
    if not s.units:
        print('gogate: no [[go18.unit]] entries configured in exhume.toml', file=sys.stderr)
        return 1

    selected = s.units
    skipped_wip = []
    if args.units:
        wanted = set(args.units)
        selected = [u for u in s.units if u.name in wanted]
        missing = wanted - {u.name for u in selected}
        if missing:
            print(f'gogate: unknown unit(s): {", ".join(sorted(missing))}', file=sys.stderr)
            return 1
    elif not args.include_wip:
        skipped_wip = [u.name for u in s.units if u.wip]
        selected = [u for u in s.units if not u.wip]

    state_path = os.path.join(s.build, 'go18', 'gate-state.json')
    prev_state = {}
    if os.path.exists(state_path):
        try:
            with open(state_path) as f:
                prev_state = json.load(f)
        except Exception:
            prev_state = {}

    tc_sig = hashlib.sha256(
        f'{s.runner}|{s.docker_image}|{s.platform}|{s.goos}|{s.goarch}|{s.cgo_enabled}|{s.gcflags}|{s.ldflags}|{cfg.profile_sha256}'.encode()
    ).hexdigest()

    all_ok = True
    matched_func_names = {}
    first_matched = None
    new_state = dict(prev_state)
    new_state.setdefault('units', {})

    print(f'go18 gate: {len(selected)} unit(s) against {os.path.relpath(s.image, s.root)}'
          + (f' (skipping {len(skipped_wip)} wip: {", ".join(skipped_wip)})' if skipped_wip else ''))
    for unit in selected:
        src_hash = unit.source_hash()
        prev_u = prev_state.get('units', {}).get(unit.name, {})
        cached_ok = (
            not args.all
            and prev_u.get('ok') is True
            and prev_u.get('src_hash') == src_hash
            and prev_u.get('tc_sig') == tc_sig
            and os.path.exists(unit.bin_path)
            and prev_u.get('bin_sha256') == _file_sha256(unit.bin_path)
        )
        skip_build = args.no_build or (cached_ok and args.fast) or (cached_ok and not args.all)
        mode = 'cached' if (skip_build and not args.no_build) else ('no-build' if args.no_build else 'built')
        try:
            ok, cmp_res, raw_res, cand = gomatch.match_unit(
                tc, s, unit,
                no_build=skip_build,
                verbose=args.verbose,
            )
        except gotc.ToolError as e:
            print(f'  FAIL  {unit.name:18s} ({mode})  {e}')
            all_ok = False
            continue

        st = 'PASS' if ok else 'FAIL'
        print(f'  {st}  {unit.name:18s} ({mode:8s})  {len(cmp_res.matched):3d}/{cmp_res.total:<3d} funcs  '
              f'{raw_res.total_bytes:7d} B  ({raw_res.rel_bytes} rel, {raw_res.unmasked_diffs} unmasked)')
        if not ok:
            all_ok = False
            if args.verbose:
                sys.stdout.write(cmp_res.raw_stdout)
                for d in raw_res.details[:20]:
                    print('    ' + d)
        else:
            for r in cmp_res.matched:
                matched_func_names[r.name] = r.orig_size
            if first_matched is None:
                first_matched = (unit, cand)
            new_state['units'][unit.name] = {
                'ok': True,
                'src_hash': src_hash,
                'tc_sig': tc_sig,
                'bin_sha256': _file_sha256(cand),
                'funcs': cmp_res.total,
                'bytes': raw_res.total_bytes,
            }

    if args.fail_test:
        if first_matched is None:
            print('  FAIL  fail-test: no matched unit available to mutate')
            all_ok = False
        else:
            u, cand = first_matched
            ft_ok, ft_msg = run_fail_test(tc, s.image, cand, u.regex)
            print(f'  {"PASS" if ft_ok else "FAIL"}  fail-test: {ft_msg}')
            if not ft_ok:
                all_ok = False

    # Coverage against target map if available
    map_funcs = os.path.join(s.map_dir, 'functions.tsv')
    if os.path.exists(map_funcs):
        all_rows = gomap.read_funcs(map_funcs)
        fp_rows = [r for r in all_rows if gomap.is_first_party(r.package, s.first_party)]
        fp_bytes = sum(r.size for r in fp_rows)
        m_funcs = len(matched_func_names)
        m_bytes = sum(matched_func_names.values())
        pct = (100.0 * m_bytes / fp_bytes) if fp_bytes else 0.0
        print(f'\ncoverage: {m_funcs}/{len(fp_rows)} first-party funcs, '
              f'{m_bytes}/{fp_bytes} bytes ({pct:.2f}%)')

    if all_ok:
        os.makedirs(os.path.dirname(state_path), exist_ok=True)
        with open(state_path, 'w') as f:
            json.dump(new_state, f, indent=2)

    elapsed = time.time() - t0
    print(f'\nCHECK {"PASSED" if all_ok else "FAILED"} in {elapsed:.1f}s'
          + (' (--no-build: verified existing binaries)' if args.no_build and all_ok else ''))
    return 0 if all_ok else 1


if __name__ == '__main__':
    sys.path.insert(0, os.path.join(gotc.EXHUME, 'tools'))
    import config
    p, rest = config.pop_config(sys.argv[1:])
    sys.exit(main(config.load(p), rest, p))
