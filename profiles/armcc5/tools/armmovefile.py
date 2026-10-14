"""Move or rename source files of an armcc tree, keeping every record of them in step.

    python3 armmovefile.py [--config PATH] OLD NEW [NOTE] [--record FILE ...] [-n]
    python3 armmovefile.py [--config PATH] --file MOVES.tsv [--record FILE ...] [-n]

Paths are relative to the project root. Each file is moved (`git mv` when the project is a git
work tree, so history follows it) and its path rewritten in the map's file column
(map/functions.tsv, map/data.tsv), in matched.txt and in every --record file (a units list,
notes that name sources), and (old, new, note) is appended to map/filenames.tsv.

A move or rename only: never a split or a join. armcc compiles a call to a function defined
earlier in the same file differently from a call to one it only has a prototype for, and
reaches a file's own globals from one another's addresses, so which functions share a file is
part of the program (compiler.md, "What a file's contents decide"); armmovefunc.py moves
functions between files with that in mind. Object and build directory names come from file
stems, so a stem must stay unique in the tree; the move is refused otherwise. Run the gate
after."""
import os, re, subprocess, sys


def stems(src_dir, root):
    out = {}
    for dirpath, _, files in os.walk(src_dir):
        for f in files:
            if f.endswith(('.c', '.s')):
                out.setdefault(os.path.splitext(f)[0], os.path.relpath(os.path.join(dirpath, f), root))
    return out


def check(root, src_dir, moves):
    """An error message, or '' when every (old, new, note) can go ahead."""
    seen = stems(src_dir, root)
    srcrel = os.path.relpath(src_dir, root)
    for old, new, _ in moves:
        if not os.path.exists(os.path.join(root, old)): return f'{old}: no such file'
        if os.path.exists(os.path.join(root, new)): return f'{new}: exists'
        if os.path.relpath(os.path.join(root, new), os.path.join(root, srcrel)).startswith('..'):
            return f'{new}: not under {srcrel}/'
        if os.path.splitext(new)[1] != os.path.splitext(old)[1]: return f'{new}: another extension than {old}'
        stem = os.path.splitext(os.path.basename(new))[0]
        if stem in seen and seen[stem] != old: return f'{new}: stem {stem} is already {seen[stem]}'
        seen[stem] = new
    return ''


def rewrite(path, moves):
    """Replace each old path with its new one in a text file; True if it changed."""
    if not os.path.exists(path): return False
    with open(path, encoding='latin1') as f: text = f.read()
    new = text
    for old, nw, _ in moves:      # the whole path only: not inside a longer one
        new = re.sub(r'(?<![\w./-])' + re.escape(old) + r'(?![\w./-])', lambda m: nw, new)
    if new == text: return False
    with open(path, 'w', encoding='latin1') as f: f.write(new)
    return True


def move(cfg, moves, records=(), write=True):
    root = os.path.realpath(cfg.root)
    err = check(root, os.path.realpath(cfg.src), moves)
    if err: return err
    if not write:
        for old, new, _ in moves: print(f'would move {old} -> {new}')
        return ''
    git = os.path.isdir(os.path.join(root, '.git'))
    for old, new, _ in moves:
        os.makedirs(os.path.dirname(os.path.join(root, new)), exist_ok=True)
        tracked = git and subprocess.run(['git', 'ls-files', '--error-unmatch', old], cwd=root,
                                         capture_output=True).returncode == 0
        if tracked: subprocess.run(['git', 'mv', old, new], cwd=root, check=True)
        else: os.rename(os.path.join(root, old), os.path.join(root, new))
    recs = [os.path.join(cfg.map, 'functions.tsv'), os.path.join(cfg.map, 'data.tsv'), cfg.matched]
    recs += [os.path.join(root, r) for r in records]
    changed = [os.path.relpath(r, root) for r in recs if rewrite(r, moves)]
    log = os.path.join(cfg.map, 'filenames.tsv')
    fresh = not os.path.exists(log)
    with open(log, 'a') as fh:
        if fresh: fh.write('old\tnew\tnote\n')
        for old, new, note in moves: fh.write(f'{old}\t{new}\t{note}\n')
    print(f'moved {len(moves)} files; records changed: {", ".join(changed) or "none"}')
    return ''


def main(argv):
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), 'tools'))
    import config
    path, a = config.pop_config(argv)
    cfg = config.load(path)
    write = '-n' not in a
    a = [x for x in a if x != '-n']
    records = []
    while '--record' in a:
        k = a.index('--record'); records.append(a[k + 1]); del a[k:k + 2]
    if not a: sys.exit(__doc__)
    if a[0] == '--file':
        moves = []
        with open(a[1]) as f:
            for line in f:
                if not line.strip() or line.startswith('#'): continue
                x = line.rstrip('\n').split('\t') + ['']
                moves.append((x[0], x[1], x[2]))
    elif len(a) >= 2:
        moves = [(a[0], a[1], a[2] if len(a) > 2 else '')]
    else:
        sys.exit(__doc__)
    err = move(cfg, moves, records, write)
    if err: sys.exit('move refused: ' + err)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
