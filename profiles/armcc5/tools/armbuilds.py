"""Match sources under several compiler builds, to find which build made the original.

    python3 armbuilds.py [--config PATH] HOME... [--src FILE...] [--jobs N]

Each HOME is a toolchain directory as setup-mdk.sh fills it (holding ARM/ARMCC/bin), one per
MDK release. Every source (--src, default every file in matched.txt) is matched under every
HOME, each build in a directory of its own (<build>/builds/<home name>/), and the result printed
as a table: a column per HOME headed by the compiler's own version line, a row per source,
`whole` or the bytes matched. The project's config stays as it is.

What the table means is in compiler.md, "Which compiler build": point releases of armcc 5
compile nearly all code alike, so the whole tree is the test, and one function that only some
builds reproduce settles a range. A source that is whole under every build says nothing; read
the rows that differ."""
import copy, os, sys
from concurrent.futures import ThreadPoolExecutor
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import armtc, armmatch


def version(cfg):
    """armcc's version line for cfg's toolchain ('Component: ARM Compiler 5.06 update 2 (build 183)')."""
    tc = armtc.toolchain(cfg)
    _, out = tc.run('armcc', ['--vsn'], cfg.build if os.path.isdir(cfg.build) else cfg.root)
    for line in out.splitlines():
        if 'Compiler' in line: return line.strip()
    return out.strip().splitlines()[0] if out.strip() else '?'


def for_home(cfg, home):
    """A copy of cfg with another toolchain and a build directory of its own."""
    c = copy.copy(cfg)
    c.home = os.path.abspath(home)
    c.build = os.path.join(cfg.build, 'builds', os.path.basename(os.path.normpath(home)))
    os.makedirs(c.build, exist_ok=True)
    return c


def cell(result):
    if result.whole: return 'whole'
    last = result.lines[-1] if result.lines else ''
    return last.split()[1] if last.startswith('-- ') and 'bytes matched' in last else 'fails'


def table(homes, sources, results, versions):
    """The report: one column per home, one row per source."""
    names = [os.path.basename(os.path.normpath(h)) for h in homes]
    w = max([len(s) for s in sources] + [6])
    out = [f'{n}: {versions[h]}' for n, h in zip(names, homes)]
    out.append(f'{"source":{w}}  ' + '  '.join(f'{n:>12}' for n in names))
    for s in sources:
        out.append(f'{s:{w}}  ' + '  '.join(f'{results[(h, s)]:>12}' for h in homes))
    whole = {h: sum(results[(h, s)] == 'whole' for s in sources) for h in homes}
    out.append(f'{"whole":{w}}  ' + '  '.join(f'{whole[h]:>12}' for h in homes))
    return out


def main(argv):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(HERE))), 'tools'))
    import config
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    jobs = int(a[a.index('--jobs') + 1]) if '--jobs' in a else 4
    if '--jobs' in a: del a[a.index('--jobs'):a.index('--jobs') + 2]
    if '--src' in a:
        k = a.index('--src'); homes, sources = a[:k], a[k + 1:]
    else:
        homes, sources = a, sorted(cfg.matched_set())
    if not homes: sys.exit(__doc__)
    cfgs = {h: for_home(cfg, h) for h in homes}
    versions = {h: version(c) for h, c in cfgs.items()}
    with ThreadPoolExecutor(jobs) as ex:
        futs = {(h, s): ex.submit(armmatch.match, cfgs[h], os.path.join(cfg.root, s)) for h in homes for s in sources}
        results = {k: cell(f.result()) for k, f in futs.items()}
    print('\n'.join(table(homes, sources, results, versions)))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
