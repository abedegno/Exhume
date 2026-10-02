"""refine2.py PLAN CANDS: move excluded names back into the headers by per-file bisection."""
import json, subprocess, re, sys, os
from collections import defaultdict
H = os.path.dirname(os.path.abspath(__file__)); R = os.path.expanduser('~/UW2Decomp')
planf = sys.argv[1]; plan = json.load(open(planf))
undecided = set(json.load(open(sys.argv[2])))
base_excl = set(plan['exclude']) - undecided
worig = set(open(os.path.join(H, 'w_orig.txt')).read().splitlines())
MAXR = int(sys.argv[3]) if len(sys.argv) > 3 else 14

def run(exclude):
    p = dict(plan); p['exclude'] = sorted(exclude)
    json.dump(p, open(os.path.join(H, 'plan_try.json'), 'w'), indent=1)
    subprocess.run([sys.executable, 'gen.py', 'plan_try.json', '--write'], cwd=H, capture_output=True, check=True)
    r = subprocess.run([sys.executable, os.path.join(H, 'fastcheck.py')], cwd=R, capture_output=True, text=True)
    bad = set(f[:-2].upper() for f in re.findall(r'^FAIL src/(\w+\.C):', r.stdout, re.M))
    bad |= set(re.findall(r'^\s+(\w+): publics listed out of', r.stdout, re.M))
    logs = [os.path.join(R, 'build', os.path.splitext(f)[0], 'BUILD.LOG') for f in os.listdir(os.path.join(R, 'src')) if f.endswith('.C')]
    w = subprocess.run(['sh', 'warns.sh'] + [l for l in logs if os.path.exists(l)], cwd=H, capture_output=True, text=True).stdout.splitlines()
    for l in set(w) - worig: bad.add(l.split('.c:')[0].upper())
    sus = json.load(open(os.path.join(H, 'suspects.json')))
    files = defaultdict(set)
    for f, ns in sus.items():
        for n in ns: files[n].add(f[:-2].upper())
    return bad, files, 'FAST OK' in r.stdout, r.stdout

accepted = set(); rejected = set()
trial = set(undecided)
for rnd in range(MAXR):
    bad, files, ok, out = run(base_excl | (undecided - accepted - trial) | rejected)
    if not ok and not bad:
        print('unexplained failure:', out[-1500:]); break
    good = {n for n in trial if not (files.get(n, set()) & bad)}
    accepted |= good; undecided -= good
    culprits = defaultdict(list)
    for n in sorted(trial - good):
        for f in files.get(n, set()) & bad: culprits[f].append(n)
    for f, ns in culprits.items():
        if len(ns) == 1: rejected.add(ns[0])
    undecided -= rejected
    print(f'round {rnd}: tried {len(trial)}, accepted {len(good)} (total {len(accepted)}), rejected {len(rejected)}, undecided {len(undecided)}, bad {sorted(bad)}', flush=True)
    if not undecided: break
    # next trial: per bad file, the first half of its remaining suspects; names in no bad file go in too
    nxt = set()
    byfile = defaultdict(list)
    for n in sorted(undecided):
        fs = files.get(n, set()) & bad
        if not fs: nxt.add(n)
        else: byfile[min(fs)].append(n)
    for f, ns in byfile.items(): nxt |= set(ns[:max(1, len(ns) // 2)])
    trial = nxt
plan['exclude'] = sorted(base_excl | undecided | rejected)
json.dump(plan, open(planf, 'w'), indent=1)
print('accepted', len(accepted), sorted(accepted))
print('rejected', sorted(rejected))
print('undecided', sorted(undecided))
