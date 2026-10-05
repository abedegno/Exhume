"""Write a project's GitHub Actions CI from Exhume's templates (docs/ci-bundle.md).

    python3 tools/citemplates.py [--config PATH] OUTDIR       with the project's [ci] values
    python3 tools/citemplates.py --vars FILE OUTDIR           with a values file alone
    python3 tools/citemplates.py --list                       the variables and their defaults

It writes OUTDIR/.github/workflows/{accuracy,nightly,port,repocheck,release}.yml and
OUTDIR/.github/actions/linux-tools/action.yml and OUTDIR/.github/actions/<assets_action>/action.yml
from tools/templates/ci/. The values are tools/templates/ci/defaults.toml (a project that uses
Exhume from a checkout in .exhume, with the Makefile template) overridden by the file exhume.toml's
[ci] vars names (UW2's: examples/uw2/ci.toml) and by any other [ci] keys.

The templates are the workflows UW2Decomp runs, with what is UW2's as {{name}} variables:
{{name}} in a line is replaced by the value; a line that is only {{name}}, from its first
column, is replaced by the value's lines (which carry their own indentation), or removed when
the value is empty. GitHub's own ${{ expr }} has spaces
and is left alone. A variable with no value stops the tool.

    accuracy    the gate, the port build, the quick fuzzing and every session against its golden,
                on pushes to main, by hand, and on pull requests from the repository's own branches
    nightly     the long tier on a schedule: goldens made again from DOS, UBSan, the drivers, the
                deep fuzzing, coverage; fails when a regenerated golden differs
    port        the port's build on Linux, macOS and Windows, with no game data and no secrets,
                on every push and pull request, forks included
    repocheck   tools/repocheck.py on every push and pull request
    release     the players' packages on a tag v* (tools/package.py), on macOS, Linux and
                Windows, into a draft release; signed and notarised with Apple's secrets when
                they are set, ad hoc without them
    linux-tools the Ubuntu packages, Python and Node packages, and the tools built from source,
                each cached by the script that pins it; nothing cached touches the game
    assets      the private bundle: cloned with a read-only deploy key, decrypted with an age key
                into $RUNNER_TEMP (docs/ci-bundle.md has the safeguards)
"""
import os, re, sys, tomllib

here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
TEMPLATES = os.path.join(here, 'templates', 'ci')
FILES = [('workflows/accuracy.yml', '.github/workflows/accuracy.yml'),
         ('workflows/nightly.yml', '.github/workflows/nightly.yml'),
         ('workflows/port.yml', '.github/workflows/port.yml'),
         ('workflows/repocheck.yml', '.github/workflows/repocheck.yml'),
         ('workflows/release.yml', '.github/workflows/release.yml'),
         ('actions/linux-tools/action.yml', '.github/actions/linux-tools/action.yml'),
         ('actions/assets/action.yml', '.github/actions/{assets_action}/action.yml')]
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
    for line in text.split('\n'):
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
    for src, dst in FILES:
        dst = os.path.join(out, dst.format(**v))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        text = render(open(os.path.join(TEMPLATES, src)).read(), v, src)
        open(dst, 'w').write(text)
        print('wrote', os.path.relpath(dst, out))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
