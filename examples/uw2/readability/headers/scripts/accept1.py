"""One trial with every candidate in; accept the names whose files all pass; verify."""
import json, subprocess, sys, os, re
from collections import defaultdict
H = os.path.dirname(os.path.abspath(__file__)); R = os.path.expanduser('~/UW2Decomp')
plan = json.load(open('plan3.json')); cands = set(json.load(open('cands4.json')))
worig = set(open('w_orig.txt').read().splitlines())
def trial(excl):
    p = dict(plan); p['exclude'] = sorted(excl)
    json.dump(p, open('plan_try.json', 'w'), indent=1)
    subprocess.run([sys.executable, 'gen.py', 'plan_try.json', '--write'], cwd=H, capture_output=True, check=True)
    r = subprocess.run([sys.executable, 'fastcheck.py'], cwd=H, capture_output=True, text=True)
    bad = set(f.upper() for f in re.findall(r'^FAIL src/(\w+)\.C', r.stdout, re.M))
    logs = [os.path.join(R, 'build', os.path.splitext(f)[0], 'BUILD.LOG') for f in os.listdir(os.path.join(R, 'src')) if f.endswith('.C')]
    w = subprocess.run(['sh', 'warns.sh'] + [l for l in logs if os.path.exists(l)], cwd=H, capture_output=True, text=True).stdout.splitlines()
    for l in set(w) - worig: bad.add(l.split('.c:')[0].upper())
    return bad
base = set(plan['exclude']) - cands
bad = trial(base)
sus = json.load(open('suspects.json'))
files = defaultdict(set)
for f, ns in sus.items():
    for n in ns: files[n].add(f[:-2].upper())
good = {n for n in cands if not (files.get(n, set()) & bad)}
print('bad files', len(bad), 'accepted', len(good), sorted(good))
plan['exclude'] = sorted(set(plan['exclude']) - good)
json.dump(plan, open('plan6.json', 'w'), indent=1)
