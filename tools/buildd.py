"""Build queue for agents whose sandbox cannot start the emulator (Codex's cannot).

    python3 tools/buildd.py [--config PATH] [--jobs N] [--budget 30]     run OUTSIDE the sandbox

--jobs is how many requests run at once, each its own DOS session; the default follows the DOS
in use (tools/dosbatch.py's sessions(): one per core up to 12 in emu2 or DOSBox, 3 in js-dos).

An agent writes <queue>/<id>.req holding one line, "match SRC [--dis NAME] [--no-build]" or
"verify SRC", and waits for <queue>/<id>.out; tools/remote.sh does both. Only those two
commands, sources inside the project's src directory (or a subdirectory of it) named NAME.C
or NAME.ASM, and those options are accepted: the queue runs with your permissions, so it must not run anything else.

Per-file build budget: <queue>/budget/<NAME.C> holds the number of match builds left
(--budget when the file is first seen). At zero further builds are refused with a message
telling the agent to stop and report. Reset a file's budget by deleting its budget file
before a new attempt. Budgets are the only reliable brake on an agent that loops."""
import os, re, sys, subprocess, time, glob
from concurrent.futures import ThreadPoolExecutor
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config, dosbatch

path, a = config.pop_config(sys.argv[1:]); cfg = config.load(path)
opt = lambda n, d: int(a[a.index(n) + 1]) if n in a else d
JOBS, BUDGET = opt('--jobs', None) or dosbatch.sessions(), opt('--budget', 30)
q = cfg.queue; os.makedirs(os.path.join(q, 'budget'), exist_ok=True)
srcrel = os.path.relpath(cfg.src, cfg.root)
OK = re.compile(r'^(match|verify) ((?:' + re.escape(srcrel) + r'/)?(?:[A-Za-z0-9_]+/)*[A-Za-z0-9_]+\.(?:C|ASM))((?: --dis [A-Za-z_][A-Za-z0-9_]*| --no-build)*)$')

def finish(req, text):
    out = req[:-4] + '.out'
    open(out + '.tmp', 'w').write(text); os.replace(out + '.tmp', out); os.remove(req)

def run(req):
    line = open(req).read().strip()
    m = OK.match(line)
    if not m: return finish(req, f'rejected: {line!r}\n[exit 2]\n')
    cmd, src, opts = m.groups()
    rel = src[len(srcrel) + 1:] if src.startswith(srcrel + '/') else src
    src = os.path.normpath(os.path.join(cfg.src, rel))
    if os.path.commonpath([src, cfg.src]) != cfg.src or not os.path.exists(src):
        import sources     # a bare NAME.C is looked up anywhere in the tree
        hit = sources.by_stem(cfg).get(sources.stem(src))
        if not hit: return finish(req, f'no such source: {line!r}\n[exit 2]\n')
        src = hit
    if cmd == 'match' and '--no-build' not in opts:
        bf = os.path.join(q, 'budget', os.path.basename(src))
        left = int(open(bf).read()) if os.path.exists(bf) else BUDGET
        if left <= 0:
            return finish(req, f'build budget for {os.path.basename(src)} is used up; stop and write your final report\n[exit 3]\n')
        open(bf, 'w').write(str(left - 1))
    if cmd == 'verify': opts = ''
    r = subprocess.run([sys.executable, os.path.join(here, cmd + '.py'), src, '--config', cfg.file] + opts.split(),
                       cwd=cfg.root, capture_output=True, text=True, timeout=900)
    finish(req, r.stdout + r.stderr + f'\n[exit {r.returncode}]\n')

busy = set()
print(f'serving {q} in {dosbatch.backend()}, {JOBS} at a time, budget {BUDGET} builds per file')
with ThreadPoolExecutor(JOBS) as pool:
    while True:
        for req in glob.glob(os.path.join(q, '*.req')):
            if req not in busy:
                busy.add(req); pool.submit(run, req).add_done_callback(lambda f, r=req: busy.discard(r))
        time.sleep(1)
