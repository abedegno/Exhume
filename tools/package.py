"""Package the release build of a port for players (docs/port.md, "Packages for players"): the
program from <build>/<[port] out>-release (tools/portbuild.py --release), the shared libraries it
loads, the players' README ([package] readme) as README.txt, the project's licence files
([package] texts) as .txt, and the licence texts of the libraries in licenses/. No game data,
no ROM and nothing from the DOS toolchain goes in: the package is built from the sources alone.

    python3 tools/package.py [--config PATH] [--version V] [--out DIR] [--strict] [--appimage]

With NAME the [package] name and EXE the [port] exe:

  macOS    NAME-V-macos.zip: NAME.app (Contents/MacOS/EXE, the libraries in Contents/Frameworks
           with their install names made @rpath ones, the icon in Contents/Resources, an
           Info.plist from [package]) and the text files beside it. Signed from the inside out
           (the libraries, the program, the app) with $<env>_CODESIGN_IDENTITY, a Developer ID
           Application identity in the keychain, with the hardened runtime and a secure
           timestamp, as notarisation needs; without one, ad hoc, since arm64 runs nothing
           unsigned
  Linux    NAME-V-linux-ARCH.tar.gz: ./LAUNCHER (the start script), bin/EXE, lib/ (the libraries
           built into [port] libs and the release build's own; the program's run path is
           $ORIGIN and $ORIGIN/../lib only, checked), LAUNCHER.desktop and LAUNCHER.png, and the
           text files; with --appimage also NAME-V-linux-ARCH.AppImage, the same program and
           libraries in an AppImage, made by appimagetool ($APPIMAGETOOL, else on the PATH;
           $APPIMAGE_RUNTIME, if set, is the runtime it embeds, else it downloads its own)
  Windows  NAME-V-windows-x86_64.zip (MSYS2 CLANG64): EXE.exe (its icon is a resource) and every
           DLL it loads that is not Windows's own (from ldd), and the text files; then checked:
           every DLL the program and those DLLs load comes from the package or from Windows

V defaults to $GITHUB_REF_NAME, else `git describe`. --strict fails when a library's licence
text cannot be found (CI uses it). The package goes to --out (default <build>/dist).
$<env>_NOTARISED=1 (a release workflow that notarises) drops the README's advice for opening
an app that Apple has not notarised (@MACOS_OPEN@). The icon files are tools/icons.py's.
"""
import os, re, sys, shutil, argparse, plistlib, subprocess, tarfile, zipfile, platform

here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import portcfg

CFG, ARGV = portcfg.cli()
P = portcfg.port(CFG)
K = portcfg.package(CFG)
root = CFG.root
REL = portcfg.variant_out(CFG, 'release')
LIBS = P.libs
EXE = P.exe
ICON = K.icon_dir
WINDOWS = os.name == 'nt' or bool(os.environ.get('MSYSTEM'))

# The licence texts each bundled library needs, by the start of its file name, and where to
# look: the runtime's SDL3 backend and its sound chips' emulators. A [[port.vendor]] library's
# is its directory's LICENSE (or COPYING).
LICENCES = [
    ('SDL3', ('libSDL3', 'SDL3'), ['share/licenses/SDL3/LICENSE.txt', 'LICENSE.txt']),
    ('mt32emu', ('libmt32emu',), ['share/doc/munt/libmt32emu/COPYING.LESSER.txt']),
]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, check=True, **kw).stdout


def version(arg):
    v = arg or os.environ.get('GITHUB_REF_NAME')
    if not v:
        match = ['--match', f'{K.tag_prefix}v*'] if K.tag_prefix else []
        try: v = run(['git', 'describe', '--tags', '--always', '--dirty'] + match, cwd=root).strip()
        except (OSError, subprocess.CalledProcessError): v = 'dev'
    # a tag that names the game in a repository of several (uw2-v1.2.1) gives the version alone
    v = re.sub(r'^[A-Za-z0-9]+-(?=v\d)', '', v)
    return re.sub(r'[^A-Za-z0-9._-]', '-', v)


def macos_unnotarised():
    return (f'This build is not notarised by Apple, so the first time, right-click (or Control-click) '
            f'{K.name}.app and choose Open, then Open again. If macOS still refuses, run this in Terminal once: '
            f'xattr -dr com.apple.quarantine /path/to/{K.name}.app')


def text_files(dest, ver):
    """README.txt and the [package] texts as .txt into dest."""
    readme = open(K.readme, encoding='utf-8').read().replace('@VERSION@', ver)
    readme = readme.replace(' @MACOS_OPEN@', '' if os.environ.get(K.env + '_NOTARISED') == '1' else ' ' + macos_unnotarised())
    with open(os.path.join(dest, 'README.txt'), 'w', encoding='utf-8', newline='\r\n' if WINDOWS else '\n') as f: f.write(readme)
    for name in K.texts:
        src = os.path.join(root, name)
        if not os.path.exists(src): raise SystemExit(f'package.py: no {name} ([package] texts)')
        shutil.copy(src, os.path.join(dest, os.path.basename(name) + '.txt'))


def vendor_shared(v):
    base = v.get('shared') or re.sub(r'[^a-z0-9]', '', v['name'].lower())
    return ('lib' + base, base)


def licence_texts(dest, libs, strict, extra=()):
    """licenses/NAME/... for each bundled library, from [port] libs, Homebrew, MSYS2 or the
    vendor's directory; extra is (name, path) pairs found by the caller."""
    out = os.path.join(dest, 'licenses')
    os.makedirs(out, exist_ok=True)
    missing = []
    bases = [LIBS, '/opt/homebrew/opt/sdl3', '/opt/homebrew/opt/mt32emu', '/usr/local/opt/sdl3', '/usr/local/opt/mt32emu']
    if WINDOWS: bases.append(winpath('/clang64'))       # MSYS2's SDL3 package
    table = [(n, pre, [os.path.join(b, r) for b in bases for r in rels]) for n, pre, rels in LICENCES]
    for v in P.vendor:
        table.append((v['name'].replace(' ', '-'), vendor_shared(v), [os.path.join(v['dir'], f) for f in ('LICENSE', 'COPYING', 'LICENSE.txt')]))
    for name, prefixes, cands in table:
        if not any(os.path.basename(l).startswith(prefixes) for l in libs): continue
        found = next((c for c in cands if os.path.isfile(c)), None)
        if not found: missing.append(name); continue
        os.makedirs(os.path.join(out, name), exist_ok=True)
        shutil.copy(found, os.path.join(out, name, os.path.basename(found)))
    for name, path in extra:
        os.makedirs(os.path.join(out, name), exist_ok=True)
        dst = os.path.join(out, name, os.path.basename(path))
        # an MSYS2 package's licence folder can hold folders (libiconv's libcharset)
        if os.path.isdir(path): shutil.copytree(path, dst, dirs_exist_ok=True)
        elif os.path.isfile(path): shutil.copy(path, dst)
        else: print(f'package.py: no licence file {path}')
    for m in missing: print(f'package.py: no licence text found for {m}')
    if missing and strict: raise SystemExit(1)


# macOS

def macho_deps(path):
    lines = run(['otool', '-L', path]).splitlines()
    # a universal binary has a heading line (ending in a colon) before each slice's list
    return list(dict.fromkeys(l.strip().split(' (')[0] for l in lines if l.strip() and not l.rstrip().endswith(':')))


def rpaths(path):
    out, lines = [], run(['otool', '-l', path]).splitlines()
    for i, l in enumerate(lines):
        if 'cmd LC_RPATH' in l:
            out.append(lines[i + 2].strip().split()[1])
    return list(dict.fromkeys(out))      # a universal binary lists each slice's


def resolve_macho(dep, exe_rpaths):
    if dep.startswith('@rpath/'):
        name = dep[len('@rpath/'):]
        for d in [REL] + [p for p in exe_rpaths if not p.startswith('@')] + [os.path.join(LIBS, 'lib')]:
            if os.path.exists(os.path.join(d, name)): return os.path.join(d, name)
        return None
    return dep if os.path.exists(dep) else None


def info_plist(ver):
    v = ver.lstrip('v')
    d = {'CFBundleDevelopmentRegion': 'en', 'CFBundleDisplayName': K.title, 'CFBundleExecutable': EXE,
         'CFBundleIconFile': K.icon, 'CFBundleIdentifier': K.bundle_id, 'CFBundleInfoDictionaryVersion': '6.0',
         'CFBundleName': K.name, 'CFBundlePackageType': 'APPL', 'CFBundleShortVersionString': v,
         'CFBundleVersion': v, 'LSApplicationCategoryType': K.category, 'LSMinimumSystemVersion': K.min_macos,
         'NSHighResolutionCapable': True}
    if K.copyright: d['NSHumanReadableCopyright'] = K.copyright
    return plistlib.dumps(d)


def package_macos(ver, out, strict):
    stage = os.path.join(out, f'{K.name}-{ver}-macos')
    shutil.rmtree(stage, ignore_errors=True)
    appdir = os.path.join(stage, K.name + '.app')
    app = os.path.join(appdir, 'Contents')
    fw = os.path.join(app, 'Frameworks')
    for d in ('MacOS', 'Frameworks', 'Resources'): os.makedirs(os.path.join(app, d))
    exe = os.path.join(app, 'MacOS', EXE)
    shutil.copy(os.path.join(REL, EXE), exe)
    open(os.path.join(app, 'Info.plist'), 'wb').write(info_plist(ver))
    open(os.path.join(app, 'PkgInfo'), 'w').write('APPL????')
    icns = os.path.join(ICON, K.icon + '.icns')
    if not os.path.exists(icns): raise SystemExit(f'package.py: no {os.path.relpath(icns, root)} (tools/icons.py)')
    shutil.copy(icns, os.path.join(app, 'Resources', K.icon + '.icns'))
    exe_rp = rpaths(exe)
    # copy every library outside the system's, following each library's own dependencies
    todo, copied = [exe], {}
    while todo:
        f = todo.pop()
        for dep in macho_deps(f):
            if dep.startswith(('/usr/lib/', '/System/')) or os.path.basename(dep) in copied: continue
            if f != exe and os.path.basename(dep) == os.path.basename(f): continue     # its own id
            src = resolve_macho(dep, exe_rp)
            if not src: raise SystemExit(f'package.py: cannot find {dep}, needed by {f}')
            name = os.path.basename(dep)
            dst = os.path.join(fw, name)
            shutil.copy(os.path.realpath(src), dst)
            os.chmod(dst, 0o755)
            copied[name] = dep
            todo.append(dst)
    for f in [exe] + [os.path.join(fw, n) for n in copied]:
        for dep in macho_deps(f):
            name = os.path.basename(dep)
            if name in copied and dep != '@rpath/' + name and not (f != exe and name == os.path.basename(f)):
                run(['install_name_tool', '-change', dep, '@rpath/' + name, f])
        if f != exe: run(['install_name_tool', '-id', '@rpath/' + os.path.basename(f), f])
    for rp in exe_rp:
        if not rp.startswith('@'): run(['install_name_tool', '-delete_rpath', rp, exe])
    if '@executable_path/../Frameworks' not in rpaths(exe):
        run(['install_name_tool', '-add_rpath', '@executable_path/../Frameworks', exe])
    # inside out: the libraries, the program, then the app, which seals the rest
    ident = os.environ.get(K.env + '_CODESIGN_IDENTITY') or '-'
    sign = ['codesign', '--force', '--sign', ident] + (['--options', 'runtime', '--timestamp'] if ident != '-' else [])
    for f in [os.path.join(fw, n) for n in copied] + [exe, appdir]:
        run(sign + [f])
    print('signed ' + ('ad hoc' if ident == '-' else 'with a Developer ID identity, hardened runtime'))
    for f in [exe] + [os.path.join(fw, n) for n in copied]:
        print(f'{os.path.relpath(f, stage)}: {run(["lipo", "-archs", f]).strip()}')
    text_files(stage, ver)
    licence_texts(stage, list(copied), strict)
    zpath = os.path.join(out, f'{K.name}-{ver}-macos.zip')
    if os.path.exists(zpath): os.remove(zpath)
    run(['ditto', '-c', '-k', '--norsrc', '--noextattr', '--keepParent', stage, zpath])
    return zpath


# Linux

def launcher_script(rel):
    return ('#!/bin/sh\n'
            f'# Starts the port from its package: {rel} finds its libraries by itself.\n'
            'here=$(dirname "$(readlink -f "$0")")\n'
            f'exec "$here/{rel}" "$@"\n')


def desktop_entry(exe, icon):
    return (f'[Desktop Entry]\nType=Application\nName={K.title}\nComment={K.comment}\n'
            f'Exec={exe}\nIcon={icon}\nTerminal=false\nCategories=Game;RolePlaying;\n')


def package_linux(ver, out, strict):
    arch = platform.machine() or 'unknown'
    stage = os.path.join(out, f'{K.name}-{ver}-linux-{arch}')
    shutil.rmtree(stage, ignore_errors=True)
    for d in ('bin', 'lib'): os.makedirs(os.path.join(stage, d))
    exe = os.path.join(stage, 'bin', EXE)
    shutil.copy(os.path.join(REL, EXE), exe)
    launcher = os.path.join(stage, K.launcher)
    open(launcher, 'w').write(launcher_script('bin/' + EXE))
    os.chmod(launcher, 0o755)
    env = dict(os.environ, LD_LIBRARY_PATH=os.pathsep.join([REL, os.path.join(LIBS, 'lib')]))
    ours = (REL + os.sep, os.path.join(LIBS, 'lib') + os.sep)
    libs = []
    for l in run(['ldd', os.path.join(REL, EXE)], env=env).splitlines():
        m = re.match(r'\s*(\S+) => (\S+)', l)
        if not m: continue
        if m.group(2) == 'not': raise SystemExit(f'package.py: {m.group(1)} not found')
        if os.path.realpath(m.group(2)).startswith(ours) or m.group(2).startswith(ours):
            shutil.copy(os.path.realpath(m.group(2)), os.path.join(stage, 'lib', m.group(1)))
            libs.append(m.group(1))
    for l in libs: print('lib/' + l)
    shutil.copy(os.path.join(ICON, K.icon + '.png'), os.path.join(stage, K.launcher + '.png'))
    open(os.path.join(stage, K.launcher + '.desktop'), 'w').write(desktop_entry(K.launcher, K.launcher))
    text_files(stage, ver)
    licence_texts(stage, libs, strict)
    check_linux(stage, [os.path.join('bin', EXE)] + [os.path.join('lib', l) for l in libs], libs)
    tpath = os.path.join(out, f'{K.name}-{ver}-linux-{arch}.tar.gz')
    with tarfile.open(tpath, 'w:gz') as t: t.add(stage, arcname=os.path.basename(stage))
    return tpath, stage, arch


def check_linux(stage, files, libs):
    """Every ELF file's run path is $ORIGIN-relative only, and the program, run from the
    package with no LD_LIBRARY_PATH, loads each bundled library from the package's lib/."""
    bad = []
    for f in files:
        for l in run(['readelf', '-d', os.path.join(stage, f)]).splitlines():
            m = re.search(r'\((RPATH|RUNPATH)\).*\[(.*)\]', l)
            if m:
                for p in m.group(2).split(':'):
                    if not (p == '$ORIGIN' or p.startswith('$ORIGIN/')): bad.append(f'{f}: {m.group(1)} {p}')
    env = {k: v for k, v in os.environ.items() if k != 'LD_LIBRARY_PATH'}
    libdir = os.path.realpath(os.path.join(stage, 'lib'))
    for l in run(['ldd', os.path.join(stage, 'bin', EXE)], env=env).splitlines():
        m = re.match(r'\s*(\S+) => (\S+)', l)
        if not m: continue
        if m.group(2) == 'not': bad.append(f'bin/{EXE}: {m.group(1)} not found')
        elif m.group(1) in libs and os.path.dirname(os.path.realpath(m.group(2))) != libdir:
            bad.append(f'bin/{EXE}: {m.group(1)} loads from {m.group(2)}, not the package')
    if bad: raise SystemExit('package.py: the package does not stand alone:\n  ' + '\n  '.join(bad))
    print('checked: run paths are $ORIGIN only, and every bundled library loads from lib/')


def appimage(stage, out, ver, arch):
    """NAME-V-linux-ARCH.AppImage from the package's files: AppRun starts usr/bin/EXE, whose
    run path finds usr/lib."""
    tool = os.environ.get('APPIMAGETOOL') or shutil.which('appimagetool')
    if not tool: raise SystemExit('package.py: --appimage needs appimagetool ($APPIMAGETOOL or on the PATH)')
    ad = os.path.join(out, 'AppDir')
    shutil.rmtree(ad, ignore_errors=True)
    shutil.copytree(os.path.join(stage, 'bin'), os.path.join(ad, 'usr', 'bin'))
    shutil.copytree(os.path.join(stage, 'lib'), os.path.join(ad, 'usr', 'lib'))
    doc = os.path.join(ad, 'usr', 'share', 'doc', EXE)
    os.makedirs(doc)
    for f in os.listdir(stage):
        if f.endswith('.txt'): shutil.copy(os.path.join(stage, f), doc)
    shutil.copytree(os.path.join(stage, 'licenses'), os.path.join(doc, 'licenses'))
    icons = os.path.join(ad, 'usr', 'share', 'icons', 'hicolor', '256x256', 'apps')
    os.makedirs(icons)
    png = os.path.join(ICON, K.icon + '.png')
    shutil.copy(png, os.path.join(icons, EXE + '.png'))
    shutil.copy(png, os.path.join(ad, EXE + '.png'))
    os.symlink(EXE + '.png', os.path.join(ad, '.DirIcon'))
    open(os.path.join(ad, EXE + '.desktop'), 'w').write(desktop_entry(EXE, EXE))
    with open(os.path.join(ad, 'AppRun'), 'w') as f:
        f.write(launcher_script('usr/bin/' + EXE).replace('# Starts the port from its package', "# The AppImage's entry"))
    os.chmod(os.path.join(ad, 'AppRun'), 0o755)
    path = os.path.join(out, f'{K.name}-{ver}-linux-{arch}.AppImage')
    if os.path.exists(path): os.remove(path)
    cmd = [tool]
    if os.environ.get('APPIMAGE_RUNTIME'): cmd += ['--runtime-file', os.environ['APPIMAGE_RUNTIME']]
    env = dict(os.environ, ARCH=arch, APPIMAGE_EXTRACT_AND_RUN='1')
    r = subprocess.run(cmd + [ad, path], env=env, capture_output=True, text=True)
    if r.returncode or not os.path.exists(path):
        raise SystemExit('package.py: appimagetool failed\n' + (r.stdout + r.stderr)[-3000:])
    os.chmod(path, 0o755)
    shutil.rmtree(ad)
    return path


# Windows (MSYS2)

def winpath(p):
    return run(['cygpath', '-w', p]).strip() if p.startswith('/') else p


def check_windows(stage, dlls):
    """Every DLL that the program and the package's DLLs load comes from the package or from
    Windows (ldd, with only the package and Windows's directories on the PATH)."""
    st = run(['cygpath', '-u', stage]).strip()
    env = dict(os.environ, PATH=os.pathsep.join([st, '/c/Windows/system32', '/c/Windows']) if os.pathsep == ':'
               else ';'.join([stage, r'C:\Windows\system32', r'C:\Windows']))
    bad = []
    for f in [EXE + '.exe'] + dlls:
        for l in run(['ldd', os.path.join(stage, f)], env=env).splitlines():
            m = re.match(r'\s*(\S+) => (.+?) \(0x', l)
            if not m: continue
            name, path = m.group(1), m.group(2)
            if path == 'not found': bad.append(f'{f}: {name} not found'); continue
            if re.match(r'(?i)(/c/windows/|c:[\\/]windows[\\/])', path): continue
            if path.rsplit('/', 1)[0].lower() != st.rstrip('/').lower():      # ldd prints MSYS paths
                bad.append(f'{f}: {name} loads from {path}, not the package')
    if bad: raise SystemExit('package.py: the package does not stand alone:\n  ' + '\n  '.join(bad))
    print('checked: every DLL loads from the package or from Windows')


def package_windows(ver, out, strict):
    stage = os.path.join(out, f'{K.name}-{ver}-windows-x86_64')
    shutil.rmtree(stage, ignore_errors=True)
    os.makedirs(stage)
    exe = os.path.join(REL, EXE + '.exe')
    shutil.copy(exe, stage)
    env = dict(os.environ)
    env['PATH'] = os.pathsep.join([REL, os.path.join(LIBS, 'bin'), env.get('PATH', '')])
    dlls, extra = [], []
    for l in run(['ldd', exe], env=env).splitlines():
        m = re.match(r'\s*(\S+) => (.+?) \(0x', l)
        if not m: continue
        name, path = m.group(1), m.group(2)
        if path == 'not found': raise SystemExit(f'package.py: {name} not found')
        if re.match(r'(?i)(/c/windows/|c:\\windows\\)', path): continue      # Windows's own
        shutil.copy(winpath(path), os.path.join(stage, name))
        dlls.append(name)
        # the licence of an MSYS2 package's DLL (the compiler runtime's), from its package
        if path.startswith('/clang64/'):
            try:
                pkg = run(['pacman', '-Qqo', path]).strip()
                d = '/clang64/share/licenses/' + re.sub(r'^mingw-w64-clang-x86_64-', '', pkg)
                if os.path.isdir(winpath(d)):
                    for f in sorted(os.listdir(winpath(d))):
                        extra.append((re.sub(r'^mingw-w64-clang-x86_64-', '', pkg), os.path.join(winpath(d), f)))
            except (OSError, subprocess.CalledProcessError):
                pass
    for d in dlls: print(d)
    text_files(stage, ver)
    licence_texts(stage, dlls, strict, extra)
    check_windows(stage, dlls)
    zpath = os.path.join(out, f'{K.name}-{ver}-windows-x86_64.zip')
    with zipfile.ZipFile(zpath, 'w', zipfile.ZIP_DEFLATED) as z:
        for d, _, fs in os.walk(stage):
            for f in fs:
                p = os.path.join(d, f)
                z.write(p, os.path.relpath(p, out))
    return zpath


def main(argv):
    ap = argparse.ArgumentParser(description='Package the release build of the port.')
    ap.add_argument('--version')
    ap.add_argument('--out', default=os.path.join(CFG.build, 'dist'))
    ap.add_argument('--strict', action='store_true')
    ap.add_argument('--appimage', action='store_true', help='Linux: an AppImage too (appimagetool)')
    a = ap.parse_args(argv)
    ver = version(a.version)
    if not os.path.exists(os.path.join(REL, EXE + '.exe' if WINDOWS else EXE)):
        print(f'package.py: {os.path.relpath(REL, root)} has no program; build it first (tools/portbuild.py --release)')
        return 1
    os.makedirs(a.out, exist_ok=True)
    if sys.platform == 'darwin': p = package_macos(ver, a.out, a.strict)
    elif WINDOWS: p = package_windows(ver, a.out, a.strict)
    else:
        p, stage, arch = package_linux(ver, a.out, a.strict)
        if a.appimage:
            ai = appimage(stage, a.out, ver, arch)
            print(f'wrote {os.path.relpath(ai, root)} ({os.path.getsize(ai)} bytes)')
    print(f'wrote {os.path.relpath(p, root)} ({os.path.getsize(p)} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main(ARGV))
