"""Checks a player's package of the port as a player gets it (CI's package-windows job): unpacks
the zip into a fresh directory and starts the program there with a fresh home directory. On
Windows only Windows's own folders are on its PATH, so it starts only if every DLL it needs is in
the package (a missing one exits 0xC0000135). It must exit with status 0 and leave a screenshot.
Prints the unpacked program's path last, for the replays that follow (EXHUME_PORT names it to
replay.py verify; the DLL search for a program starts in its own directory, so they use the
package's DLLs too).

    python3 tools/pkgcheck.py ZIP --exe NAME --data DIR [--unpack DIR]

NAME is the program's name without .exe; DIR the game's folder. --unpack keeps the unpacked
package there (default: a new temporary directory)."""
import os, sys, shutil, zipfile, tempfile, subprocess, argparse


def winpath(p):
    """A path as Windows writes it (MSYS2's /d/a/... to D:\\a\\...)."""
    if os.name == 'nt' and shutil.which('cygpath'):
        return subprocess.run(['cygpath', '-w', p], capture_output=True, text=True).stdout.strip()
    return p


def main():
    ap = argparse.ArgumentParser(description='Check a package of the port starts as a player gets it.')
    ap.add_argument('zip'); ap.add_argument('--exe', required=True); ap.add_argument('--data', required=True)
    ap.add_argument('--unpack')
    a = ap.parse_args()
    work = a.unpack or tempfile.mkdtemp(prefix='pkgcheck-')
    shutil.rmtree(work, ignore_errors=True); os.makedirs(work)
    with zipfile.ZipFile(a.zip) as z:
        for i in z.infolist():
            p = z.extract(i, work)
            mode = i.external_attr >> 16           # the Unix mode a macOS or Linux zip keeps
            if mode & 0o111: os.chmod(p, mode & 0o777)
    exe = None
    for d, _, fs in os.walk(work):
        for f in fs:
            if f.lower() in (a.exe.lower(), a.exe.lower() + '.exe'): exe = os.path.join(d, f)
    if not exe: sys.exit(f'pkgcheck.py: no {a.exe} in {a.zip}')
    print(f'pkgcheck: {os.path.relpath(exe, work)} from {os.path.basename(a.zip)}', file=sys.stderr)

    home = os.path.join(work, 'home'); os.makedirs(home)
    shot = os.path.join(work, 'start.png')
    env = dict(os.environ)
    where = ''
    if os.name == 'nt':
        sysroot = env.get('SYSTEMROOT', r'C:\Windows')
        env['PATH'] = os.pathsep.join([os.path.join(sysroot, 'System32'), sysroot])
        where = " with only Windows's folders on the PATH"
    cmd = [exe, '--data', winpath(a.data), '--home', winpath(home), '--no-recording',
           '--screenshot-after', '4000', '--screenshot', winpath(shot), '--exit-after', '6000']
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, errors='replace', timeout=120)
    if r.returncode != 0 or not os.path.exists(shot):
        sys.stderr.write(r.stdout[-4000:]); sys.stderr.write(r.stderr[-4000:])
        sys.exit(f'pkgcheck: the packaged program did not start cleanly{where} '
                 f'(exit {r.returncode & 0xFFFFFFFF:#x}{", no screenshot" if not os.path.exists(shot) else ""}; '
                 f'0xc0000135 is a missing DLL)')
    print(f'pkgcheck: started{where}, screenshot {os.path.getsize(shot)} bytes, exit 0', file=sys.stderr)
    print(exe)
    return 0


if __name__ == '__main__':
    sys.exit(main())
