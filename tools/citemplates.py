"""Write a project's GitHub Actions CI from Exhume's templates (docs/ci-bundle.md).

    python3 tools/citemplates.py [--config PATH] OUTDIR       with the project's [ci] values
    python3 tools/citemplates.py --vars FILE OUTDIR           with a values file alone
    python3 tools/citemplates.py --list                       the variables and their defaults

It writes OUTDIR/.github/workflows/{accuracy,nightly,port,repocheck,release}.yml (and site.yml
when web_site names a site repository) and
OUTDIR/.github/actions/linux-tools/action.yml and OUTDIR/.github/actions/<assets_action>/action.yml
from tools/templates/ci/. With a [[games]] list in the values (a repository of several games,
each in its own folder, with Exhume as a submodule), repocheck.yml and site.yml are written once and the rest
once per game, with that game's values, as <wf_prefix>accuracy.yml and so on. The values are tools/templates/ci/defaults.toml (a project that uses
Exhume from a checkout in .exhume, with the Makefile template) overridden by the file exhume.toml's
[ci] vars names (UW2's: examples/uw2/ci.toml) and by any other [ci] keys.

The templates began as UW2Decomp's workflows, with what was UW2's as {{name}} variables:
{{name}} in a line is replaced by the value; a line that is only {{name}}, from its first
column, is replaced by the value's lines (which carry their own indentation), or removed when
the value is empty. The lines between a line that is only {{if name}} and one that is only
{{end}}, both from the first column, are kept when name's value is not empty and removed when it
is (an optional step: blackbox_windows); such blocks do not nest. GitHub's own ${{ expr }} has
spaces and is left alone. A variable with no value stops the tool.

    accuracy    the gate, the port build, the quick fuzzing and every session against its golden,
                on pushes to main, by hand, and on pull requests from the repository's own branches
    nightly     the long tier on a schedule: goldens made again from DOS, UBSan, the drivers, the
                deep fuzzing, coverage; fails when a regenerated golden differs, keeping the
                text diff of each changed golden.json (golden-diff.txt)
    port        the port's build on Linux, macOS and Windows, with no game data and no secrets,
                on every push and pull request, forks included
    repocheck   tools/repocheck.py on every push and pull request
    release     the players' packages (tools/package.py) on macOS, Linux and Windows, on a tag
                (release_tags), a schedule (release_schedule) or by hand; each tested on other
                systems (release_test_jobs, tools/pkgcheck.py); for a tag push only, a draft
                release; signed and notarised with Apple's secrets when they are set, ad hoc
                without them
    site        the web build deployed (a repository with Underworld Exhumed's web/deploy.sh): each
                game built from its latest published release's tag, checked, and pushed to the
                gh-pages branch of web_site with the deploy key WEB_DEPLOY_KEY, when a release is
                published; by hand a dry run unless push is set. Only when web_site is not empty
    linux-tools the Ubuntu packages, Python and Node packages, and the tools built from source,
                each cached by the script that pins it; nothing cached touches the game
    assets      the private bundle: cloned with a read-only deploy key, decrypted with an age key
                into $RUNNER_TEMP (docs/ci-bundle.md has the safeguards)
"""
import os, re, sys, tomllib

here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
TEMPLATES = os.path.join(here, 'templates', 'ci')
FILES = [('workflows/accuracy.yml', '.github/workflows/{wf_prefix}accuracy.yml'),
         ('workflows/nightly.yml', '.github/workflows/{wf_prefix}nightly.yml'),
         ('workflows/port.yml', '.github/workflows/{wf_prefix}port.yml'),
         ('workflows/repocheck.yml', '.github/workflows/{wf_prefix}repocheck.yml'),
         ('workflows/release.yml', '.github/workflows/{wf_prefix}release.yml'),
         ('workflows/site.yml', '.github/workflows/{wf_prefix}site.yml'),
         ('actions/linux-tools/action.yml', '.github/actions/{linux_tools_action}/action.yml'),
         ('actions/assets/action.yml', '.github/actions/{assets_action}/action.yml')]
# written once for the repository, not per game
ONCE = ('workflows/repocheck.yml', 'workflows/site.yml')
VAR = re.compile(r'\{\{(\w+)\}\}')


def values(vars_file=None, cfg=None):
    v = tomllib.load(open(os.path.join(TEMPLATES, 'defaults.toml'), 'rb'))
    if cfg is not None:
        # release.yml's names, from the project's [package] and [port] (portcfg)
        import portcfg
        P, K = portcfg.port(cfg), portcfg.package(cfg)
        v.update(pkg_name=K.name, exe=P.exe, icon=K.icon, launcher=K.launcher, env=K.env)
        c = cfg.raw.get('ci', {})
        if c.get('vars'): vars_file = c['vars'] if os.path.isabs(c['vars']) else os.path.join(cfg.root, c['vars'])
        v.update({k: x for k, x in c.items() if k != 'vars'})
    if vars_file: v.update(tomllib.load(open(vars_file, 'rb')))
    return v


def render(text, v, name):
    out = []
    block = None                        # inside {{if k}}: whether its lines are kept
    for line in text.split('\n'):
        c = re.fullmatch(r'\{\{if (\w+)\}\}', line)
        if c:
            if block is not None: sys.exit(f'citemplates.py: {name}: {{{{if}}}} inside {{{{if}}}}')
            if c.group(1) not in v: sys.exit(f'citemplates.py: {name}: no value for {c.group(1)}')
            block = str(v[c.group(1)]) != ''
            continue
        if line == '{{end}}':
            if block is None: sys.exit(f'citemplates.py: {name}: {{{{end}}}} with no {{{{if}}}}')
            block = None
            continue
        if block is False: continue
        m = re.fullmatch(r'\{\{(\w+)\}\}', line)
        if m:
            k = m.group(1)
            if k not in v: sys.exit(f'citemplates.py: {name}: no value for {k}')
            val = str(v[k])
            if val != '': out.extend(val.rstrip('\n').split('\n'))
            continue
        def sub(mm):
            if mm.group(1) not in v: sys.exit(f'citemplates.py: {name}: no value for {mm.group(1)}')
            return str(v[mm.group(1)])
        out.append(VAR.sub(sub, line))
    if block is not None: sys.exit(f'citemplates.py: {name}: {{{{if}}}} with no {{{{end}}}}')
    return '\n'.join(out)


def main(argv):
    vars_file = None
    if '--list' in argv:
        for k, x in values().items():
            first = str(x).split('\n')[0]
            print(f'{k:24} {first[:90]}')
        return 0
    if '--vars' in argv:
        i = argv.index('--vars'); vars_file = argv[i + 1]; argv = argv[:i] + argv[i + 2:]
        v = values(vars_file)
    else:
        import config
        path, argv = config.pop_config(argv)
        v = values(None, config.load(path))
    if len(argv) != 1: sys.exit(__doc__)
    out = argv[0]
    games = v.pop('games', None)
    # site.yml only for a repository that names its site
    files = [f for f in FILES if f[0] != 'workflows/site.yml' or v.get('web_site')]
    if not games:
        write(out, v, files)
        return 0
    # a repository of several games: repocheck and site once for the repository, the rest per game
    write(out, v, [f for f in files if f[0] in ONCE])
    for g in games:
        gv = dict(v); gv.update(g)
        write(out, gv, [f for f in files if f[0] not in ONCE])
    return 0


def write(out, v, files):
    for src, dst in files:
        dst = os.path.join(out, dst.format(**v))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        text = render(open(os.path.join(TEMPLATES, src)).read(), v, src)
        open(dst, 'w').write(text)
        print('wrote', os.path.relpath(dst, out))


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
