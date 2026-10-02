"""The build driver and gate: one command that proves the whole tree still rebuilds the original.

    python3 tools/gate.py [--config PATH] check [--all] [--fast]   the gate every change must pass
    python3 tools/gate.py [--config PATH] exact                    the exact link, judged by [gate] known_diffs
    python3 tools/gate.py [--config PATH] game                     the modding build; prints the EXE's path
    python3 tools/gate.py [--config PATH] symbols                  step 3 of the gate alone
    python3 tools/gate.py [--config PATH] boot [EXE]               boot the modding build (or EXE) and screenshot it

A project wraps these in a Makefile (tools/templates/Makefile) and runs `check` from a git
pre-push hook (tools/install-hooks.sh), since hosted CI cannot have the toolchain or the
program; CI runs tools/repocheck.py instead.

The gate, `check`:
1. Every source with a `/* target: */` line, in every directory under src, is compiled with
   its own `/* opts: */`, several to a DOS session and several sessions at once (tools/build.py).
   A source is recompiled only when its hash (its text with the shared headers it includes,
   tools/srcdeps.py), its options, its object or the toolchain differ from the last check it
   passed (<build>/check/state.json); --all recompiles everything. A source that fails in a
   batch is built again on its own by match.py before it counts as failed.
2. match.py (--no-build) must say WHOLE SEGMENT MATCHES and verify.py "fixups and data
   verified" for each.
3. symbols.tsv rebuilt from scratch must hold the same names at the same addresses as the
   committed file, in any order: verify.py --update over every source into an empty file,
   overlays first and then by target, trying the ones that fail again after the rest until a
   round adds nothing (a file can need a name another file merges first: a far address it
   refers to by its offset alone, data placed only by its publics). A name the committed
   file marks 'library' by hand may come out 'provisional', since no object carries the mark.
4. The exact link must equal the original except the bytes in [gate] known_diffs, and the
   modding build with no source changed must be byte-identical to it.
Exit status 0 only when everything passes. --fast stops after step 2 and checks only the
sources that changed: the quick loop while editing, never the proof.

exhume.toml, [gate] (every key optional except the link commands, which `exact`, `game` and
steps 4 need). Commands are split like a shell line and may use {python}, {exhume},
{config}, {root} and {build}; they run in the project root.
    link = "{python} {exhume}/examples/uw2/link.py --config {config}"
    mod_link = "{python} {exhume}/examples/uw2/link.py --mod --config {config}"
    exact_exe = "LINK/out/UW2.EXE"       # relative to [project] build
    mod_exe = "MODLINK/out/UW2.EXE"
    known_diffs = [[0x6676C, 0x00, 0x01]]   # file offset, original byte, linked byte
    sessions = 3                          # DOS sessions at once
    batch = 8                             # sources per session
    boot = ["w:5000", "s:title"]          # rungame.mjs steps for `boot` ([run] data, exe_name, skip)
"""
import sys, os, re, json, glob, time, shutil, hashlib, shlex, subprocess, tempfile, io, contextlib
from concurrent.futures import ThreadPoolExecutor
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
EXHUME = os.path.dirname(here)
import config, sources
from srcdeps import source_hash


def sha1(path):
    with open(path, 'rb') as f: return hashlib.sha1(f.read()).hexdigest()


class Gate:
    def __init__(self, cfg):
        self.cfg = cfg
        g = cfg.raw.get('gate', {})
        self.g = g
        self.sessions = int(g.get('sessions', 3))
        self.batch = int(g.get('batch', 8))
        self.known = {int(o): (int(a), int(b)) for o, a, b in g.get('known_diffs', [])}
        self.state_path = os.path.join(cfg.build, 'check', 'state.json')
        py = os.path.join(EXHUME, '.venv', 'bin', 'python')
        self.py = py if os.path.exists(py) else sys.executable

    def rel(self, p): return os.path.relpath(p, self.cfg.root)

    def command(self, key):
        if key not in self.g: sys.exit(f'[gate] {key} is not set in {self.cfg.file}')
        subst = dict(python=self.py, exhume=EXHUME, config=self.cfg.file, root=self.cfg.root, build=self.cfg.build)
        return [x.format(**subst) for x in shlex.split(self.g[key])]

    def exe_path(self, key, default):
        return os.path.join(self.cfg.build, self.g.get(key, default))

    # ---- what to build ------------------------------------------------------------------
    def sources(self):
        """[(stem, path, opts, target)] for every source with a target line."""
        out = []
        for src in sources.all_sources(self.cfg):
            t, opts = self.cfg.directives(src)
            if t: out.append((sources.stem(src), src, opts, t))
        return out

    def toolchain(self):
        """A hash of what turns a source into an object: the toolchain's executables, the
        profile's command lines and the DOS runner. A change rebuilds everything."""
        h = hashlib.sha1()
        files = [os.path.join(here, 'dosrun.mjs'), os.path.join(here, 'build.py'),
                 os.path.join(self.cfg.profile_dir, 'profile.toml')]
        for s in self.cfg.stage:
            if os.path.isdir(s): files += sorted(glob.glob(os.path.join(s, '*.EXE')) + glob.glob(os.path.join(s, '*.exe')))
            else: files.append(s)
        for p in files:
            if os.path.exists(p): h.update(os.path.basename(p).encode() + sha1(p).encode())
        return h.hexdigest()

    # ---- steps 1 and 2 --------------------------------------------------------------------
    def match_verify(self, src, build=False):
        """(ok, why) from match.py and verify.py; build=True lets match.py compile it itself."""
        cfgarg = ['--config', self.cfg.file]
        m = subprocess.run([self.py, os.path.join(here, 'match.py'), src] + cfgarg + ([] if build else ['--no-build']),
                           capture_output=True, text=True, cwd=self.cfg.root)
        mt = m.stdout + m.stderr
        if m.returncode or 'WHOLE SEGMENT MATCHES' not in mt:
            bad = [l for l in mt.splitlines() if l.strip() and not l.rstrip().endswith('MATCH')]
            return False, 'match.py: ' + ('\n    '.join(bad[-12:]) or f'exit {m.returncode}')
        v = subprocess.run([self.py, os.path.join(here, 'verify.py'), src] + cfgarg, capture_output=True, text=True, cwd=self.cfg.root)
        vt = v.stdout + v.stderr
        if v.returncode or 'fixups and data verified' not in vt:
            bad = [l for l in vt.splitlines() if 'PROBLEM' in l or l.startswith('--') or 'Error' in l]
            return False, 'verify.py: ' + ('\n    '.join(bad[-12:]) or f'exit {v.returncode}')
        return True, ''

    def compile_and_match(self, force, fast):
        """Steps 1 and 2. Returns (passed sources, total, failures)."""
        import build
        cfg = self.cfg
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        try: state = json.load(open(self.state_path))
        except (OSError, ValueError): state = {}
        tc = self.toolchain()
        if state.get('toolchain') != tc: state = {'toolchain': tc, 'sources': {}}
        known = state.setdefault('sources', {})
        srcs = self.sources()
        todo = []
        for stem, src, opts, _ in srcs:
            obj = cfg.obj(stem); k = known.get(stem)
            if force or not k or k['src'] != source_hash(src, cfg) or k.get('opts') != opts \
               or not os.path.exists(obj) or k['obj'] != sha1(obj):
                todo.append((stem, src, opts))
        t1 = time.time()
        built = {}
        if todo:
            res = build.build(cfg, [s for _, s, _ in todo], jobs=self.sessions, batch=self.batch)
            built = {stem: (None if ok else '; '.join(msgs) or 'failed') for stem, (ok, msgs) in res.items()}
        print(f'compiled {len(todo)} of {len(srcs)} sources in {time.time() - t1:.0f}s'
              + (' (the rest are unchanged since they last passed)' if len(todo) < len(srcs) else ''))
        check = [x for x in srcs if x[0] in {t[0] for t in todo}] if fast else srcs

        def one(item):
            stem, src, opts, _ = item
            if built.get(stem):
                ok, why = self.match_verify(src, build=True)
                return stem, ok, why if ok else f'{why}\n    (batch build: {built[stem]})'
            ok, why = self.match_verify(src)
            if not ok and stem in built: ok, why = self.match_verify(src, build=True)
            return stem, ok, why
        with ThreadPoolExecutor(self.sessions) as pool:
            per = list(pool.map(one, check))
        fails = []
        for (stem, ok, why), (_, src, opts, _) in zip(per, check):
            if ok: known[stem] = {'src': source_hash(src, cfg), 'opts': opts, 'obj': sha1(cfg.obj(stem))}
            else: known.pop(stem, None); fails.append(f'{self.rel(src)}: {why}')
        json.dump(state, open(self.state_path + '.tmp', 'w'), indent=1)
        os.replace(self.state_path + '.tmp', self.state_path)
        return sum(1 for _, ok, _ in per if ok), len(check), fails

    # ---- step 3 ---------------------------------------------------------------------------
    def rebuild_symbols(self, path, srcs=None, keep=None):
        """verify.py --update over every source into a fresh symbols file at path (starting
        from keep, {name: (address, label)}, if given). Returns the sources that never verify."""
        import verify
        cfg = self.cfg
        srcs = srcs or [s for _, s, _, _ in self.sources()]
        with open(path, 'w') as f:
            f.write('# name\taddress\tname source\n')
            for n, (v, l) in sorted((keep or {}).items()): f.write(f'{n}\t{v}\t{l}\n')
        ovl = lambda t: cfg.overlay_index(t) is not None
        todo = sorted(srcs, key=lambda s: (not ovl(cfg.directives(s)[0]), cfg.directives(s)[0]))
        saved = cfg.symbols; cfg.symbols = path
        failed = []
        try:
            while todo:
                failed = []
                for src in todo:
                    try:
                        with contextlib.redirect_stdout(io.StringIO()):
                            rc = verify.main([src, '--update', '--config', cfg.file])
                    except SystemExit as e: rc = e.code or 1
                    if rc: failed.append(src)
                if len(failed) == len(todo): break
                todo = failed
        finally:
            cfg.symbols = saved
        return failed

    def symbols_from_scratch(self):
        cfg = self.cfg
        d = tempfile.mkdtemp(prefix='syms-', dir=os.path.dirname(self.state_path))
        try:
            path = os.path.join(d, 'symbols.tsv')
            failed = self.rebuild_symbols(path)
            if failed: return False, 'verify --update failed from scratch for ' + ' '.join(self.rel(s) for s in failed)
            rows = lambda p: {f[0]: (f[1], f[2] if len(f) > 2 else '') for f in
                              (l.rstrip('\n').split('\t') for l in open(p) if l.strip() and not l.startswith('#'))}
            new, old = rows(path), rows(cfg.symbols)
        finally:
            shutil.rmtree(d, ignore_errors=True)
        probs = [f'only in the committed file: {n} {old[n][0]}' for n in sorted(set(old) - set(new))]
        probs += [f'only in the rebuild: {n} {new[n][0]}' for n in sorted(set(new) - set(old))]
        for n in sorted(set(old) & set(new)):
            if old[n][0] != new[n][0]: probs.append(f'{n}: committed {old[n][0]}, rebuilt {new[n][0]}')
            elif old[n][1] != new[n][1] and not (old[n][1] == 'library' and new[n][1] == 'provisional'):
                probs.append(f'{n}: committed source {old[n][1]!r}, rebuilt {new[n][1]!r}')
        if probs: return False, '\n    '.join(probs[:30]) + (f'\n    ... {len(probs) - 30} more' if len(probs) > 30 else '')
        return True, f'{len(new)} names'

    # ---- step 4 ---------------------------------------------------------------------------
    def link(self, mod):
        """Run the link from a clean output; returns (EXE path or None, output)."""
        out = self.exe_path('mod_exe' if mod else 'exact_exe', ('MODLINK' if mod else 'LINK') + '/out/' + os.path.basename(self.cfg.exe_path or 'GAME.EXE'))
        if os.path.exists(out): os.remove(out)          # never judge a stale EXE
        r = subprocess.run(self.command('mod_link' if mod else 'link'), capture_output=True, text=True, cwd=self.cfg.root)
        text = r.stdout + r.stderr
        if mod and r.returncode: return None, text
        # the exact link's own exit status is exediff's, nonzero while the known bytes differ:
        # judge it by its bytes
        return (out if os.path.exists(out) else None), text

    def exact_diff(self, path):
        """None when path equals the original but for the known bytes, else what differs."""
        a = self.cfg.exe; b = open(path, 'rb').read()
        if len(a) != len(b): return f'size {len(b):#x}, the original {len(a):#x}'
        diff = [i for i in range(len(a)) if a[i] != b[i]] if a != b else []
        extra = [i for i in diff if self.known.get(i) != (a[i], b[i])]
        if extra: return f'{len(extra)} bytes differ beyond the known {len(self.known)}, first at {extra[0]:#x}'
        if len(diff) != len(self.known):
            return f'expected the {len(self.known)} known bytes to differ, {len(diff)} do (has the linker or the EXE changed?)'
        return None

    def known_text(self):
        return 'equal' if not self.known else 'equal except ' + ', '.join(f'{o:#x}' for o in sorted(self.known))

    # ---- commands -------------------------------------------------------------------------
    def check(self, force, fast):
        t0 = time.time()
        results = {}; fails = []
        nok, n, f12 = self.compile_and_match(force, fast)
        fails += f12
        results['match + verify'] = (nok == n, f'{nok}/{n} sources' + (' (changed only)' if fast else ''))
        if not fast:
            if nok == n:
                ok, why = self.symbols_from_scratch()
                results['symbols.tsv from scratch'] = (ok, why)
                if not ok: fails.append('symbols.tsv: ' + why)
            else:
                results['symbols.tsv from scratch'] = (False, 'skipped: not every source verifies')
            exe, text = self.link(False)
            d = self.exact_diff(exe) if exe else 'no EXE: ' + '\n    '.join(text.strip().splitlines()[-10:])
            results['exact link'] = (not d, d or self.known_text() + ' to the original')
            if d: fails.append('exact link: ' + d)
            if not d and 'mod_link' in self.g:
                mexe, mtext = self.link(True)
                if not mexe: md = 'failed: ' + '\n    '.join(mtext.strip().splitlines()[-10:])
                elif open(mexe, 'rb').read() != open(exe, 'rb').read(): md = f'{self.rel(mexe)} differs from {self.rel(exe)}'
                else: md = None
                results['modding build = exact'] = (not md, md or 'byte-identical')
                if md: fails.append('modding build: ' + md)
            elif 'mod_link' in self.g:
                results['modding build = exact'] = (False, 'skipped: the exact link failed')
        print()
        for f in fails: print('FAIL', f)
        if fails: print()
        for k, (ok, why) in results.items(): print(f'{"pass" if ok else "FAIL"}  {k:26} {why}')
        passed = all(ok for ok, _ in results.values())
        label = 'FAST CHECK' if fast else 'CHECK'
        print(f'\n{label} {"PASSED" if passed else "FAILED"} in {time.time() - t0:.0f}s'
              + (' (no symbols rebuild, no link: not a proof)' if fast and passed else ''))
        return 0 if passed else 1

    def exact(self):
        exe, text = self.link(False)
        print(text.rstrip())
        if not exe: print('FAIL: the exact link produced no EXE'); return 1
        d = self.exact_diff(exe)
        print(f'{self.rel(exe)}: ' + (self.known_text() + ' to the original' if not d else 'FAIL: ' + d))
        return 1 if d else 0

    def game(self):
        exe, text = self.link(True)
        print(text.rstrip())
        if not exe: print('FAIL: the modding build failed'); return 1
        print(f'modding build: {exe}')
        return 0

    def symbols(self):
        nok, n, fails = self.compile_and_match(False, False)
        if nok != n:
            for f in fails: print('FAIL', f)
            return 1
        ok, why = self.symbols_from_scratch()
        print(('pass  ' if ok else 'FAIL  ') + 'symbols.tsv from scratch: ' + why)
        return 0 if ok else 1

    def boot(self, a):
        exe = a[0] if a else self.exe_path('mod_exe', 'MODLINK/out/' + os.path.basename(self.cfg.exe_path or 'GAME.EXE'))
        if not os.path.exists(exe): print(f'{exe} does not exist: run `game` first'); return 1
        d = os.path.join(self.cfg.build, 'boot'); os.makedirs(d, exist_ok=True)
        for p in glob.glob(os.path.join(d, '*.png')): os.remove(p)
        run = self.cfg.run
        cmd = ['node', os.path.join(here, 'rungame.mjs')]
        if run.get('data'): cmd += ['--data', run['data']]
        if run.get('exe_name'): cmd += ['--as', run['exe_name']]
        for s in run.get('skip', []): cmd += ['--skip', s]
        steps = self.g.get('boot', ['w:5000', 's:title'])
        r = subprocess.run(cmd + [exe, os.path.join(d, 'shot-')] + steps, cwd=EXHUME)
        shots = sorted(glob.glob(os.path.join(d, '*.png')))
        for s in shots: print('screenshot:', s)
        return r.returncode or (0 if shots else 1)


def main(argv):
    path, a = config.pop_config(argv)
    if not a or a[0] in ('-h', '--help'): print(__doc__); return 0
    g = Gate(config.load(path))
    c, rest = a[0], a[1:]
    if c == 'check': return g.check('--all' in rest, '--fast' in rest)
    if c == 'exact': return g.exact()
    if c == 'game': return g.game()
    if c == 'symbols': return g.symbols()
    if c == 'boot': return g.boot(rest)
    print(__doc__); return 2


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
