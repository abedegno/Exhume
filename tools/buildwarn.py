"""The compiler's warnings for every source, normalised, to compare before and after a change.

    python3 tools/buildwarn.py [--config PATH] [--save FILE] [--diff FILE] [--grep REGEX]

Reads <build>/STEM/BUILD.LOG for every source (as the last build left it: run the gate or
build.py first) and prints one line per distinct warning, `stem: message`, with the line
number dropped so that edits elsewhere in the file do not count as a change. --save writes
them to FILE; --diff FILE prints the warnings that are new or gone since FILE was saved and
exits 1 when any are new.

A readability step that keeps every byte can still change what the compiler thinks of the
code, and the warnings say where: moving a prototype into a header drew 234 "Suspicious
pointer conversion" warnings at UW2's callers of the object-list functions (their arguments
had another pointer type), which retyping the callers cleared. A new warning is not a
failure, since the gate decides that, but it marks a declaration that does not fit its uses.
"Undefined structure" warnings are left out: a header that declares a tag it never defines
draws one in every file that includes it, harmlessly.
"""
import os, re, sys
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import config, sources


def collect(cfg, rx=None):
    out = set()
    for p in sources.all_sources(cfg):
        stem = sources.stem(p)
        log = os.path.join(cfg.build, stem, 'BUILD.LOG')
        if not os.path.exists(log): continue
        for l in open(log, encoding='latin1').read().replace('\r', '').split('\n'):
            m = re.match(r'^Warning\s+(\S+?)\s+(\d+):\s*(.*)$', l)
            if not m or 'Undefined structure' in m.group(3): continue
            msg = f'{stem}: {m.group(3)}'
            if rx and not re.search(rx, msg): continue
            out.add(msg)
    return sorted(out)


def main(argv):
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    def take(flag):
        if flag in a: i = a.index(flag); v = a[i + 1]; del a[i:i + 2]; return v
    save, diff, rx = take('--save'), take('--diff'), take('--grep')
    w = collect(cfg, rx)
    if save: open(save, 'w').write('\n'.join(w) + '\n'); print(f'{len(w)} warnings saved to {save}'); return 0
    if diff:
        old = set(l for l in open(diff).read().split('\n') if l)
        new = [x for x in w if x not in old]; gone = [x for x in sorted(old) if x not in set(w)]
        for x in new: print('new  ', x)
        for x in gone: print('gone ', x)
        print(f'{len(new)} new, {len(gone)} gone, {len(w)} now')
        return 1 if new else 0
    print('\n'.join(w)); print(f'{len(w)} warnings')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
