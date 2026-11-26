#!/usr/bin/env python3
"""gonative.py: build the profile's Go version for the machine's own CPU.

The toolchain image is linux/amd64. On an arm64 host (Apple Silicon) it runs under emulation, and
the compiler is where a search spends its time. Go's compiler is a cross compiler: the same Go
version built for the host writes the same amd64 objects. This builds one from the image's own
source tree, inside a container of the host's architecture:

    gonative.py build NAME BOOTSTRAP_IMAGE [--from CONTAINER] [--config PATH]

NAME is the native container to create or reuse (project mounted at /work, like the toolchain
container); BOOTSTRAP_IMAGE is any Go image that exists for the host's architecture and can build
the wanted version (for Go 1.7/1.8: `golang:1.8`, which has an arm64 variant; the exact-version
tags `golang:1.8.0` and `golang:1.7.5` are amd64 only). --from is the container whose GOROOT
source is copied (default: the profile's toolchain container; give a second toolchain's container
for godual.py). The result is NAME:/opt/GOVERSION. Use it as

    EXHUME_GO_NATIVE=NAME:/opt/go1.8          gofast.py, gorandperm.py --fast, gotrace.py, goordergoal.py
    --second NAME:/opt/go1.7.5                godual.py

Nothing is trusted on the strength of this build: gofast compiles the unchanged file with both
compilers at every start and refuses the native one unless the two archives are byte-identical.
A whole program linked by a native toolchain has the same first-party code; some standard library
functions differ from the image's (the image's std was built by its own make.bash), so score first
party functions only. GOROOT_FINAL is set to the source container's GOROOT, and godual passes it
again at build time: without it the file table holds the native path and line tables change size.

Measured on an Apple Silicon host, Go 1.8.0, one package compile and dump: 0.54 s native against
1.32 s emulated (a 60-file package), 0.18 s against 0.77 s (a 12-file one); a whole Go 1.7.5 build
of a 20-package program 1.4 s against 8.5 s.
"""
import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gotc


def sh(container, script, timeout=3600):
    return subprocess.run(['docker', 'exec', container, 'sh', '-c', script], capture_output=True, text=True, timeout=timeout)


def build(name, bootstrap_image, src_container, root):
    r = subprocess.run(['docker', 'inspect', '-f', '{{.State.Running}}', name], capture_output=True, text=True)
    if r.stdout.strip() != 'true':
        subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        r = subprocess.run(['docker', 'run', '-d', '--name', name, '-v', f'{root}:/work', '-w', '/work',
                            bootstrap_image, 'sleep', 'infinity'], capture_output=True, text=True)
        if r.returncode:
            raise gotc.ToolError(f'cannot start {name} from {bootstrap_image}: {r.stderr[-300:]}')
    f = sh(src_container, 'go env GOROOT; go version').stdout.split()
    if len(f) < 4:
        raise gotc.ToolError(f'no Go toolchain in {src_container}')
    src_root, version = f[0], f[3]
    dest = f'/opt/{version}'
    host = sh(name, 'go env GOHOSTOS GOHOSTARCH GOROOT').stdout.split()
    if len(host) < 3:
        raise gotc.ToolError(f'no Go toolchain in {name} to bootstrap with')
    p1 = subprocess.Popen(['docker', 'exec', src_container, 'tar', 'cf', '-', '--exclude=./pkg', '--exclude=./bin',
                           '-C', src_root, '.'], stdout=subprocess.PIPE)
    p2 = subprocess.run(['docker', 'exec', '-i', name, 'sh', '-c', f'rm -rf {dest} && mkdir -p {dest} && tar xf - -C {dest}'],
                        stdin=p1.stdout, capture_output=True)
    p1.wait()
    if p1.returncode or p2.returncode:
        raise gotc.ToolError(f'copying {src_root} from {src_container} failed: {p2.stderr[-300:]}')
    r = sh(name, f'cd {dest}/src && GOROOT_BOOTSTRAP={host[2]} GOROOT_FINAL={src_root} GOOS={host[0]} GOARCH={host[1]} '
                 f'CGO_ENABLED=0 ./make.bash >/tmp/make-{version}.log 2>&1 || {{ tail -30 /tmp/make-{version}.log; exit 1; }}; '
                 f'GOROOT={dest} {dest}/bin/go version')
    if r.returncode:
        raise gotc.ToolError(f'make.bash failed:\n{r.stdout[-1500:]}{r.stderr[-500:]}')
    return f'{name}:{dest}', r.stdout.strip().split('\n')[-1]


def main(argv=None):
    ap = argparse.ArgumentParser(description='Build the profile\'s Go version hosted on this machine\'s architecture')
    sub = ap.add_subparsers(dest='cmd', required=True)
    b = sub.add_parser('build')
    b.add_argument('name')
    b.add_argument('bootstrap_image')
    b.add_argument('--from', dest='src', help='container whose GOROOT source to build (default: the toolchain container)')
    b.add_argument('--config')
    a = ap.parse_args(argv)
    s = gotc.settings(a.config)
    src = a.src or gotc.Toolchain(s).ensure_daemon()
    spec, version = build(a.name, a.bootstrap_image, src, s.root)
    print(version)
    print(f'EXHUME_GO_NATIVE={spec}    (or: godual.py --second {spec})')
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except gotc.ToolError as e:
        sys.exit(f'gonative: {e}')
