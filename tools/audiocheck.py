"""Checks a recorded session's audio as a port renders it with the user's MT-32 or CM-32L ROMs: the
whole session replayed hidden, in a fresh home directory, with --audio-wav, and the WAV's SHA-256
compared with a committed digest. A replay is deterministic, so any change to the MT-32 emulation,
the library, the driver or the timing changes the digest. Also: the ROMs the port reports loading
are the ones expected, and the audio is not silent.

    python3 tools/audiocheck.py --exe PORT --data GAME_DIR --rec REC [--cfg CFG] --roms DIR
                                --digests DIR [--name NAME] [--want-ctrl ID] [--update]

The digest is DIR/NAME.PLATFORM.sha256, PLATFORM being the system and machine (darwin-arm64,
linux-x86_64, win32-x86_64): floating point may round differently between them. --update writes
the digest for this platform instead of comparing. Exit status 0 when every check passes."""
import argparse, hashlib, os, platform, re, shutil, subprocess, sys, tempfile, wave, array, math


def plat():
    m = platform.machine().lower()
    m = {'amd64': 'x86_64', 'x64': 'x86_64', 'aarch64': 'arm64'}.get(m, m)
    return f'{sys.platform}-{m}'


def exe_path(p):
    return p + '.exe' if not os.path.exists(p) and os.path.exists(p + '.exe') else p


def main():
    ap = argparse.ArgumentParser()
    for k in ('--exe', '--data', '--rec', '--roms', '--digests'): ap.add_argument(k, required=True)
    ap.add_argument('--cfg'); ap.add_argument('--name', default='soundmt')
    ap.add_argument('--want-ctrl', default='ctrl_cm32l_1_02'); ap.add_argument('--update', action='store_true')
    a = ap.parse_args()
    work = tempfile.mkdtemp(prefix='audiocheck-')
    ok = True
    try:
        home = os.path.join(work, 'home'); os.makedirs(os.path.join(home, 'DATA'))
        if a.cfg: shutil.copy(a.cfg, os.path.join(home, 'DATA', 'UW.CFG'))
        wav = os.path.join(work, 'out.wav')
        env = {k: v for k, v in os.environ.items() if not k.endswith('_MT32_ROMS')}
        env.update(HOME=os.path.join(work, 'user'), XDG_DATA_HOME=os.path.join(work, 'user'), XDG_CONFIG_HOME=os.path.join(work, 'user'),
                   XDG_DATA_DIRS=os.path.join(work, 'none'), LOCALAPPDATA=os.path.join(work, 'user'), SDL_AUDIODRIVER='dummy')
        cmd = [exe_path(a.exe), '--data', a.data, '--home', home, '--hidden', '--exit-on-halt', '--exit-after', '900000',
               '--replay', a.rec, '--audio-wav', wav, '--mt32-roms', a.roms]
        r = subprocess.run(cmd, env=env, capture_output=True, text=True, errors='replace', timeout=1800)
        log = r.stdout + r.stderr
        m = re.search(r'mt32: ROMs \w+ .+? \((\S+), (\S+)\)', log)
        if not m or not m.group(1).startswith(a.want_ctrl):
            print(f'FAIL ROMs: wanted {a.want_ctrl}, the port said: {m.group(0) if m else "no mt32 line"}'); ok = False
        else:
            print(f'ok   ROMs: {m.group(1)}, {m.group(2)}')
        if not os.path.exists(wav) or os.path.getsize(wav) < 1000:
            print(f'FAIL no audio written\n{log[-800:]}'); return 1
        with wave.open(wav, 'rb') as w:
            frames = w.readframes(w.getnframes()); width = w.getsampwidth(); rate = w.getframerate(); ch = w.getnchannels()
        samples = array.array('h', frames) if width == 2 else array.array('f', frames)
        peak = max((abs(x) for x in samples), default=0)
        rms = math.sqrt(sum(x * x for x in samples[::64]) / max(1, len(samples[::64])))
        seconds = len(samples) / ch / rate
        loud = peak > (1000 if width == 2 else 0.03)
        print(f"{'ok  ' if loud else 'FAIL'} not silent: {seconds:.1f} s, peak {peak}, rms {rms:.1f}")
        ok &= loud
        digest = hashlib.sha256(open(wav, 'rb').read()).hexdigest()
        path = os.path.join(a.digests, f'{a.name}.{plat()}.sha256')
        if a.update:
            os.makedirs(a.digests, exist_ok=True)
            open(path, 'w').write(digest + '\n'); print(f'wrote {path}: {digest}')
        elif not os.path.exists(path):
            print(f'FAIL no digest for {plat()} ({path}); this run gives {digest} (--update writes it)'); ok = False
        else:
            want = open(path).read().split()[0]
            same = want == digest
            print(f"{'ok  ' if same else 'FAIL'} digest {plat()}: {digest}" + ('' if same else f' (committed {want})'))
            ok &= same
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
