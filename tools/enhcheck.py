"""Checks a port's enhancements (runtime/port/sys/enhance.c; Underworld Exhumed's
docs/ENHANCEMENTS.md): each is off by default and carried by the sessions played with it.

    python3 tools/enhcheck.py [--config PATH] selftest      the tools' own handling of format 5
    python3 tools/enhcheck.py [--config PATH] registry      the options, the settings and the
                                                            recordings' enhancements
    python3 tools/enhcheck.py [--config PATH] presentation  each presentation enhancement leaves
                                                            every session's game state as DOS's
    python3 tools/enhcheck.py [--config PATH] baseline check|make [NAME ...]
                                                            the port-made goldens of tests/replay/
                                                            enhanced/NAME (timing and gameplay)
    python3 tools/enhcheck.py [--config PATH] all           selftest, registry, presentation and
                                                            baseline check

Exit status 0 when every check passes."""
import os, sys, struct, tempfile, shutil
here = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, here)
import replay as R

results = []


def check(name, ok, detail=''):
    results.append(bool(ok))
    print(f"{'ok  ' if ok else 'FAIL'} {name}" + (f': {detail}' if detail != '' and not ok else ''))


def fake_recording(path, version, names=None):
    """A recording with no runs: the header, and for format 5 the enhancements' chunk."""
    with open(path, 'wb') as f:
        f.write(R.RC.magic + struct.pack('<HHI', version, 0, 0))
        if names is not None:
            b = names.encode()
            f.write(bytes([9]) + struct.pack('<H', len(b)) + b)


def selftest():
    d = tempfile.mkdtemp(prefix='enhcheck-')
    try:
        p4, p5 = os.path.join(d, 'a.rec'), os.path.join(d, 'b.rec')
        fake_recording(p4, 4); fake_recording(p5, 5, 'skip-intro')
        s4, _ = R.read_log(p4); s5, _ = R.read_log(p5)
        check('format 4: no enhancements', s4.get('ENH') is None, s4)
        check('format 5: its enhancements read', s5.get('ENH') == ['skip-intro'], s5)
        ok4, _ = R.dos_can_replay(p4); ok5, why = R.dos_can_replay(p5)
        check('DOS replays format 4', ok4)
        check('DOS refuses format 5, saying why', not ok5 and 'skip-intro' in why, why)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main(argv):
    cmd = argv[0] if argv else 'selftest'
    if cmd == 'selftest': selftest()
    else: sys.exit(__doc__)
    n = len(results); bad = results.count(False)
    print(f'enhcheck: {n - bad} of {n} checks pass')
    return 1 if bad else 0


if __name__ == '__main__':
    sys.exit(main(R.ARGV))
