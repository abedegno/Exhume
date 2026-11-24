"""First look at a DOS executable: its format, overlay scheme, and which toolchain profile
its runtime strings point to.

    python3 tools/fingerprint.py EXE

Prints validated LE object/page/entry facts, or the MZ facts (header size, load module,
relocations, entry point, stack), a Borland
VROOMM overlay block (FBOV) and its segment table if there is one, counts of telling byte
patterns (standard frames, `enter`, far calls, INT 3Fh overlay thunks, far returns), and for
each profile under profiles/ the signature strings found and the DGROUP paragraph they
suggest. Strings only narrow the field: the proof is compiling a function with the
candidate compiler and getting the same bytes (skills/fingerprint-toolchain)."""
import sys, os, re, struct, glob, tomllib
here = os.path.dirname(os.path.abspath(__file__)); EXHUME = os.path.dirname(here)
if EXHUME not in sys.path:
    sys.path.insert(0, EXHUME)


def _declares_le(data):
    # Recognition only: LEImage performs the container and table validation.
    if data[:2] == b'LE':
        return True
    for match in re.finditer(b'MZ', data):
        origin = match.start()
        if origin + 0x40 <= len(data):
            pointer = struct.unpack_from('<I', data, origin + 0x3c)[0]
            if pointer >= 0x40 and data[origin + pointer:origin + pointer + 2] == b'LE':
                return True
    return False


def _le_facts(data):
    from profiles.watcom.tools.le import LEImage
    image = LEImage.parse(data)
    # Validate raw fixup records too, even though facts do not resolve them.
    image.read_fixups()
    print(f'LE: header at file {image.header_offset:#x}, container origin {image.origin:#x}; '
          f'{len(image.objects)} objects, {len(image.pages)} pages, page size {image.page_size:#x} bytes')
    print(f'    entry {image.entry_object}:{image.entry_offset:#x}; '
          f'stack {image.stack_object}:{image.stack_offset:#x}')
    for number, obj in enumerate(image.objects, 1):
        print(f'    object {number}: base {obj.base:#x}, virtual size {obj.size:#x}, '
              f'flags {obj.flags:#x}, first page {obj.first_page}, {obj.page_count} pages')
    _profile_signatures(data)
    print('Profile signatures only narrow the toolchain; confirm with compilation and exact matching.')


def _elf_facts(data):
    import importlib.util
    gotc_path = os.path.join(EXHUME, 'profiles', 'golang-180', 'tools', 'gotc.py')
    spec = importlib.util.spec_from_file_location('go18_gotc', gotc_path)
    gotc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gotc)
    info = gotc.inspect_elf_go(data)
    link_kind = 'dynamic (CGO/libc)' if info['dynamic'] else 'static (CGO_ENABLED=0)'
    flags = []
    if info['stripped_symtab']:
        flags.append('-s (no .symtab)')
    if info['stripped_dwarf']:
        flags.append('-w (no DWARF)')
    print(f'ELF64: {info["arch"]}, entry {info["entry"]:#x}, {link_kind}, file {info["size"]:#x} bytes')
    print(f'    .text {info["text_size"]:#x} bytes, .rodata {info["rodata_size"]:#x} bytes'
          + (f'; stripped: {", ".join(flags)}' if flags else '; unstripped'))
    if info['pclntab']:
        p = info['pclntab']
        print(f'    .gopclntab: magic {p["magic"]:#x} ({p["format"]}), '
              f'minLC={p["min_lc"]}, ptrSize={p["ptr_size"]}, nfunc={p["nfunc"]}, size={p["size"]:#x}')
    if info['go_versions']:
        print(f'    Go version strings: {", ".join(info["go_versions"][:10])}')
    if info['build_id']:
        print(f'    Go buildid: {info["build_id"]}')
    _profile_signatures(data)
    print('Profile signatures only narrow the toolchain; confirm with compilation and exact matching.')


def main(path):
    with open(path, 'rb') as source:
        d = source.read()
    if d[:4] == b'\x7fELF':
        _elf_facts(d)
        return
    if _declares_le(d):
        _le_facts(d)
        return
    if d[:2] not in (b'MZ', b'ZM'):
        raise ValueError('expected MZ, ELF64, or supported LE')
    if len(d) < 0x20:
        raise ValueError('truncated MZ header')
    w = lambda i: struct.unpack_from('<H', d, i)[0]
    hdr = w(8) * 16; cblp, cp = w(2), w(4); end = (cp - 1) * 512 + cblp if cblp else cp * 512
    if not cp or cblp >= 512 or not 0x20 <= hdr <= end <= len(d):
        raise ValueError('MZ header/load module dimensions')
    if w(6) and (w(0x18) < 0x1c or w(0x18) + w(6) * 4 > hdr):
        raise ValueError('MZ relocations outside header')
    print(f'MZ: header {hdr:#x} bytes, load module {end - hdr:#x} bytes (file {hdr:#x}..{end:#x}), file {len(d):#x} bytes')
    print(f'    {w(6)} relocations at {w(0x18):#x}; entry {w(0x16):04X}:{w(0x14):04X}; stack {w(0x0E):04X}:{w(0x10):04X}; '
          f'min/max alloc {w(0x0A):#x}/{w(0x0C):#x} paragraphs')
    if len(d) > end: print(f'    {len(d) - end:#x} bytes after the load module: {d[end:end + 4]!r}')
    if d[end:end + 4] == b'FBOV':
        if len(d) - end < 16:
            raise ValueError('truncated FBOV header')
        tab, n = struct.unpack_from('<II', d, end + 8)
        if tab > len(d) or n > (len(d) - tab) // 8:
            raise ValueError('FBOV segment table outside file')
        ents = [struct.unpack_from('<4H', d, tab + 8 * i) for i in range(n)]
        stubs = [i for i, e in enumerate(ents) if e[2] == 3]
        print(f'Borland VROOMM overlays (FBOV): segment table at file {tab:#x}, {n} entries of '
              f'(paragraph, end, flags, start); {len(stubs)} overlay stubs (flags 3), entries {stubs[0] if stubs else "-"}..{stubs[-1] if stubs else "-"}')
    # Borland's TLINK leaves FB and its version byte at 1Ch; a packer replaces the header
    if d[0x1C:0x1E] in (b'LZ', ) and d[0x1C:0x20] in (b'LZ09', b'LZ91'):
        print(f'    packed by LZEXE {d[0x1E:0x20].decode()}: unpack it first (tools/unlzexe.py); the runtime strings and '
              'the linker marker are inside')
    elif b'PKLITE' in d[:0x200] or d[0x1E:0x20] == b'PK':
        print('    packed by PKLITE: unpack it first; the runtime strings and the linker marker are inside')
    elif d[0x1E] == 0xFB:
        v = d[0x1F]
        known = {0x20: 'Turbo C 2.x\'s TLINK (measured: Turbo C 2.01)',
                 0x30: 'Borland C++ 2.0\'s TLINK 4.0 (measured: its HELLO.EXE and BCC.EXE)',
                 0x50: 'TLINK 5.x of Borland C++ 3.0/3.1 (measured: Turbo Pascal 7\'s TEMC.EXE and MAKE.EXE)',
                 0x61: 'TLINK 6.1 of Borland C++ 4.x (measured: Scorched Earth 1.5)'}
        print(f'    linker marker FB {v:02X} at 1Eh: {known.get(v, "a Borland TLINK, version byte not yet measured")}')
    img = d[hdr:end]
    pats = {'push bp; mov bp,sp (55 8B EC)': b'\x55\x8b\xec', 'enter (C8 xx xx 00)': None,
            'far call (9A)': b'\x9a', 'INT 3Fh overlay thunk (CD 3F)': b'\xcd\x3f', 'far return (CB)': b'\xcb'}
    for k, p in pats.items():
        n = len(re.findall(rb'\xc8..\x00', img, re.S)) if p is None else img.count(p)
        print(f'    {n:6}  {k}')
    _profile_signatures(d, hdr)


def _profile_signatures(d, hdr=None):
    for prof in sorted(glob.glob(os.path.join(EXHUME, 'profiles', '*', 'profile.toml'))):
        with open(prof, 'rb') as profile:
            P = tomllib.load(profile)
        fp = P.get('fingerprint', {})
        found = [(s, d.find(s.encode('latin1'))) for s in fp.get('strings', [])]
        hits = [(s, o) for s, o in found if o >= 0]
        print(f'profile {os.path.basename(os.path.dirname(prof))} ({P.get("name", "")}): {len(hits)}/{len(found)} signature strings')
        for s, o in hits: print(f'    {o:#08x}  {s!r}')
        a = fp.get('dgroup_anchor')
        if hdr is not None and a and d.find(a.encode('latin1')) >= 0:
            data = d.find(a.encode('latin1')) - fp.get('dgroup_anchor_offset', 0)
            para = (data - hdr) // 16
            print(f'    DGROUP: C0\'s _DATA at file {data:#x}; paragraph {para:#06x} (DS:0 at file {hdr + para * 16:#x}) '
                  f'if nothing precedes _DATA by more than {data - hdr - para * 16} bytes; confirm with a string the code uses')

if __name__ == '__main__':
    if len(sys.argv) != 2: sys.exit(__doc__)
    try:
        main(sys.argv[1])
    except (OSError, ValueError) as error:
        sys.exit(f'invalid executable: {error}')
