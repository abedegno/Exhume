import json, subprocess, re, sys, os
H = os.path.dirname(os.path.abspath(__file__))
plan = json.load(open(sys.argv[1]))
for it in range(12):
    json.dump(plan, open(sys.argv[1], 'w'), indent=1)
    subprocess.run([sys.executable, 'gen.py', sys.argv[1], '--write'], cwd=H, capture_output=True, check=True)
    r = subprocess.run(['make', 'check'], cwd=os.path.expanduser('~/UW2Decomp'), capture_output=True, text=True)
    open(os.path.join(H, 'loop_last.log'), 'w').write(r.stdout + r.stderr)
    fails = re.findall(r'^FAIL src/(\w+\.C):', r.stdout, re.M)
    linkfail = re.findall(r'^\s+(\w+): publics listed out of', r.stdout, re.M)
    print('iter', it, 'fails', fails, 'link', linkfail, 'PASSED' if 'CHECK PASSED' in r.stdout else '', flush=True)
    if 'CHECK PASSED' in r.stdout: break
    sus = json.load(open(os.path.join(H, 'suspects.json')))
    add = set()
    for f in fails + [l + '.C' for l in linkfail]:
        add |= set(sus.get(f, []))
    add -= set(plan['exclude'])
    if not add:
        print('no suspects to exclude for', fails, linkfail); break
    print('excluding', sorted(add), flush=True)
    plan['exclude'] = sorted(set(plan['exclude']) | add)
