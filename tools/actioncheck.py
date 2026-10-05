"""Checks that every GitHub Action the CI templates use (tools/templates/ci) is at its latest
major version. Dependabot reads only a repository's own .github/workflows, so it cannot see the
templates; Exhume's CI runs this instead.

    python3 tools/actioncheck.py [--token TOKEN]

Asks GitHub for each action's latest release (anonymously, or with GITHUB_TOKEN / --token for a
higher rate limit). Exit status 0 when every action is at its latest major version."""
import json, os, re, sys, urllib.request

here = os.path.dirname(os.path.abspath(__file__))
USES = re.compile(r'uses:\s*([\w.-]+/[\w.-]+)@v(\d+)')


def used():
    found = {}
    for d, _, fs in os.walk(os.path.join(here, 'templates', 'ci')):
        for f in fs:
            if f.endswith(('.yml', '.toml')):
                for m in USES.finditer(open(os.path.join(d, f), encoding='utf-8').read()):
                    found.setdefault(m.group(1), set()).add(int(m.group(2)))
    return found


def latest(action, token):
    req = urllib.request.Request(f'https://api.github.com/repos/{action}/releases/latest',
                                 headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'exhume-actioncheck'})
    if token: req.add_header('Authorization', f'Bearer {token}')
    with urllib.request.urlopen(req, timeout=30) as r:
        tag = json.load(r)['tag_name']
    m = re.match(r'v?(\d+)', tag)
    return int(m.group(1)) if m else None, tag


def main(argv):
    token = argv[argv.index('--token') + 1] if '--token' in argv else os.environ.get('GITHUB_TOKEN')
    bad = 0
    for action, majors in sorted(used().items()):
        major, tag = latest(action, token)
        mine = max(majors)
        ok = major is None or (len(majors) == 1 and mine >= major)
        bad += not ok
        print(f"{'ok  ' if ok else 'OLD '} {action}: templates v{', v'.join(map(str, sorted(majors)))}, latest {tag}")
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
