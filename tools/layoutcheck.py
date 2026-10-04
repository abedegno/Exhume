"""Compare every struct and union of the shared headers as the original compiler lays it out
(in DOS) and as the port lays it out (clang on the host), byte for byte (docs/port.md, "Struct
layout and bitfields").

    python3 tools/layoutcheck.py [--config PATH]            build both probes, run them, compare
    python3 tools/layoutcheck.py [--config PATH] --host     the host side only (no DOS run): sizes and fields
    python3 tools/layoutcheck.py [--config PATH] --show TAG print one record's fields from both sides
    python3 tools/layoutcheck.py [--config PATH] --reuse    compare the last DOS output again

The record list comes from clang's AST of one file that includes every header in [project]
include (with [port] compat, as the port compiles). One generated C program,
<build>/layout/LPROBE.C, then sets each field of each record in turn to all ones in a zeroed
buffer (a bitfield is assigned -1, any other field is filled with FFh bytes) and prints which
bytes of the record changed, with their values, and the record's size. The same program is
compiled with the original compiler ([port.layout] probe, a DOS batch line; the default is
Turbo C++ 1.01's in the medium model, as the game sources are compiled, with no -a), linked
and run in headless DOS (tools/dosrun.mjs, with [toolchain] stage), and with the host compiler
and the port's flags. The two outputs are compared line by line.

A record whose layouts differ is listed with its first difference. A record with pointer
fields cannot match, since a host pointer is 8 bytes; such a record must stay in memory. The
file records ([port.layout] file_records: the ones read from or written to the game's files
and saves, or laid over bytes read from them, each with why) must come out identical, and the
tool exits with status 1 if one does not, or if a record with a pointer field is a file
record. A source line that moves a pointer-holding record as bytes (one of [port.layout]
io_calls, by default the C library's read and write calls, memcpy and movedata) also fails.
"""
import os, re, sys, json, argparse, subprocess, shutil

here = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, here)
import portcfg, portcheck, sources

CFG, ARGV = portcfg.cli()
root = CFG.root
OUT = os.path.join(CFG.build, 'layout')
INC = CFG.include
LAYOUT = portcfg.port(CFG).layout
PROBE = LAYOUT.get('probe', 'TCC -mm -1 -f- -w- -IC:\\ -LC:\\ LPROBE.C > BUILD.TXT')
IO_CALLS = LAYOUT.get('io_calls', ['fread', 'fwrite', 'read', 'write', 'movedata', 'memcpy', 'memmove', '_fmemcpy'])
SKIP_HEADERS = portcfg.port(CFG).size_probe_skip

# The records the game reads from or writes to its files, or lays over bytes read from them
# ([port.layout] file_records, {tag: why}). Each must have the same layout in the port as in DOS.
FILE_RECORDS = dict(LAYOUT.get('file_records', {}))

# a record whose name is in this list need not match: the comment says why
MEMORY_ONLY_OK = dict(LAYOUT.get('memory_only_ok', {}))


def ast_records():
    """[(kind, tag, [(path, is_bitfield, qualtype)])] for each record defined in src/include."""
    os.makedirs(OUT, exist_ok=True)
    tu = os.path.join(OUT, 'allheaders.c')
    hs = [h for h in sorted(os.listdir(INC)) if h.endswith('.h') and h not in SKIP_HEADERS]
    open(tu, 'w').write(''.join(f'#include "{h}"\n' for h in hs))
    flags = [f for f in portcheck.FLAGS if not f.startswith('-W') and f not in ('-ferror-limit=0',)]
    r = subprocess.run([os.environ.get('CC', 'cc')] + flags + ['-fsyntax-only', '-Wno-comment',
                        '-Xclang', '-ast-dump=json', tu], capture_output=True, text=True, cwd=root)
    if r.returncode: raise SystemExit('layoutcheck.py: clang failed on the headers\n' + r.stderr[-3000:])
    ast = json.loads(r.stdout)
    cur = {'file': ''}

    def note(loc):
        if not isinstance(loc, dict): return
        for k in ('spellingLoc', 'expansionLoc'):
            if k in loc: note(loc[k])
        if 'file' in loc: cur['file'] = loc['file']

    def locs(node):
        note(node.get('loc'))
        rg = node.get('range') or {}
        note(rg.get('begin')); note(rg.get('end'))

    byid = {}
    def index(node):
        if isinstance(node, dict):
            if 'id' in node: byid[node['id']] = node
            for c in node.get('inner', []) or []: index(c)
    index(ast)

    def fields(rec, prefix):
        out = []
        for c in rec.get('inner', []) or []:
            if c.get('kind') != 'FieldDecl': continue
            name = c.get('name')
            qt = c['type'].get('qualType', '')
            if not name: continue
            path = prefix + name
            # a field of an unnamed struct or union type: check its members through the path
            m = re.match(r'(struct|union) \(unnamed (struct|union) at ', qt)
            anon = None
            if m:
                for sib in rec.get('inner', []) or []:
                    if sib.get('kind') == 'RecordDecl' and not sib.get('name') and sib.get('completeDefinition'):
                        if sib.get('id') == c.get('type', {}).get('typeAliasDeclId'):
                            anon = sib
                # match the unnamed record defined just before this field
                prev = None
                for sib in rec.get('inner', []) or []:
                    if sib is c: break
                    if sib.get('kind') == 'RecordDecl' and sib.get('completeDefinition'): prev = sib
                anon = anon or prev
            out.append((path, bool(c.get('isBitfield')), qt))
            if anon is not None and '[' not in qt:
                out += fields(anon, path + '.')
        return out

    recs = []; seen = set()
    for node in ast.get('inner', []):
        locs(node)
        if node.get('kind') != 'RecordDecl' or not node.get('completeDefinition'): continue
        if not node.get('name') or not cur['file'].startswith(INC): continue
        tag = node['name']
        if tag in seen: continue
        seen.add(tag)
        recs.append((node['tagUsed'], tag, os.path.basename(cur['file']), fields(node, '')))
    return recs, hs


def write_probe(recs, hs):
    big = 0
    lines = ['/* generated by tools/layoutcheck.py: do not edit */',
             '#include <stdio.h>', '#include <string.h>']
    lines += [f'#include "{h}"' for h in hs]
    lines += ['#ifdef __TURBOC__', 'static unsigned char buf[0x7F00];', '#else',
              'static unsigned char buf[0x40000];', '#endif',
              'static void dump(char *tag, char *path, unsigned size)',
              '{',
              '    unsigned i, first = 0xFFFF, last = 0;',
              '    for (i = 0; i < size; i++) if (buf[i]) { if (first == 0xFFFF) first = i; last = i; }',
              '    printf("F %s %s", tag, path);',
              '    if (first == 0xFFFF) { printf(" none\\n"); return; }',
              '    printf(" %X-%X", first, last);',
              '    for (i = first; i <= last && i < first + 8; i++) printf(" %02X", buf[i]);',
              '    if (last - first >= 8) { for (; i <= last && buf[i] == 0xFF; i++); printf(i > last ? " ..FF" : " ..mixed"); }',
              '    printf("\\n");',
              '}']
    for n, (kind, tag, hdr, flds) in enumerate(recs):
        ty = f'{kind} {tag}'
        lines.append(f'static void probe{n}(void)')
        lines.append('{')
        lines.append(f'    #define P (({ty} *)buf)')
        lines.append(f'    printf("R {tag} %X\\n", (unsigned)sizeof({ty}));')
        for path, bit, qt in flds:
            lines.append(f'    memset(buf, 0, sizeof({ty}));')
            if bit: lines.append(f'    P->{path} = -1;')
            else: lines.append(f'    memset(&P->{path}, 0xFF, sizeof(P->{path}));')
            lines.append(f'    dump("{tag}", "{path}", sizeof({ty}));')
        lines.append('    #undef P')
        lines.append('}')
    # compat.h renames the game's main for the port; the probe is its own program
    lines += ['#ifdef main', '#undef main', '#endif']
    lines.append('int main(void)')
    lines.append('{')
    lines += [f'    probe{n}();' for n in range(len(recs))]
    lines.append('    return 0;')
    lines.append('}')
    p = os.path.join(OUT, 'LPROBE.C')
    open(p, 'w').write('\r\n'.join(lines) + '\r\n')
    return p


def run_host(probe):
    exe = os.path.join(OUT, 'lprobe_host')
    flags = [f for f in portcheck.FLAGS if not f.startswith('-W') and f != '-ferror-limit=0']
    r = subprocess.run([os.environ.get('CC', 'cc')] + flags + ['-w', '-o', exe, probe], capture_output=True,
                       text=True, cwd=root)
    if r.returncode: raise SystemExit('layoutcheck.py: the host probe does not build\n' + r.stderr[-4000:])
    out = subprocess.run([exe], capture_output=True, text=True).stdout
    open(os.path.join(OUT, 'host.txt'), 'w').write(out)
    return out


def run_dos(probe, hs):
    dosout = os.path.join(OUT, 'dos')
    if os.path.isdir(dosout): shutil.rmtree(dosout)
    args = ['node', os.path.join(here, 'dosrun.mjs'), dosout, '-f', probe + '=LPROBE.C']
    for st in CFG.stage: args += ['--stage', st]
    import build      # the shared headers as the DOS build stages them: the runtime's portable.h too
    for h, dosname in build.shared_headers(CFG):
        if h.lower().endswith('.h'): args += ['-f', h + '=' + dosname]
    args += ['-c', PROBE,
             '-c', 'LPROBE > OUT.TXT', '-o', 'OUT.TXT', '-o', 'BUILD.TXT', '-t', '400']
    r = subprocess.run(args, capture_output=True, text=True, cwd=root)
    p = os.path.join(dosout, 'OUT.TXT')
    if not os.path.exists(p) or os.path.getsize(p) == 0:
        b = os.path.join(dosout, 'BUILD.TXT')
        raise SystemExit('layoutcheck.py: the DOS probe produced no output\n' + r.stdout[-2000:] + r.stderr[-2000:]
                         + (open(b, encoding='latin1').read()[-3000:] if os.path.exists(b) else ''))
    out = open(p, encoding='latin1').read().replace('\r\n', '\n')
    open(os.path.join(OUT, 'dos.txt'), 'w').write(out)
    return out


def parse(out):
    recs = {}
    for l in out.split('\n'):
        f = l.split(' ')
        if f[0] == 'R': recs[f[1]] = {'size': int(f[2], 16), 'fields': {}}
        elif f[0] == 'F' and f[1] in recs: recs[f[1]]['fields'][f[2]] = ' '.join(f[3:])
    return recs


def main(argv):
    ap = argparse.ArgumentParser(description='Compare struct layouts between Turbo C and the port.')
    ap.add_argument('--host', action='store_true', help='host side only')
    ap.add_argument('--show', metavar='TAG')
    ap.add_argument('--reuse', action='store_true', help='compare the last DOS output again')
    a = ap.parse_args(argv)
    recs, hs = ast_records()
    probe = write_probe(recs, hs)
    host = parse(run_host(probe))
    pointers = {tag for k, tag, h, fl in recs if any('*' in qt for p, b, qt in fl)}
    if a.host:
        for k, tag, h, fl in recs:
            print(f"{tag:20} {h:10} size {host[tag]['size']:#x}{'  (pointer fields)' if tag in pointers else ''}")
        return 0
    if a.reuse and os.path.exists(os.path.join(OUT, 'dos.txt')):
        dos = parse(open(os.path.join(OUT, 'dos.txt')).read())
    else:
        dos = parse(run_dos(probe, hs))
    if a.show:
        for side, d in (('DOS', dos), ('host', host)):
            r = d.get(a.show)
            if not r: print(f'{side}: no record {a.show}'); continue
            print(f"{side}: size {r['size']:#x}")
            for p, v in r['fields'].items(): print(f'  {p:24} {v}')
        return 0
    same, diff, bad = [], [], []
    for k, tag, h, fl in recs:
        d, o = dos.get(tag), host.get(tag)
        if d is None or o is None:
            diff.append((tag, h, 'missing from the ' + ('DOS' if d is None else 'host') + ' output'))
            if tag in FILE_RECORDS: bad.append(tag)
            continue
        why = None
        if d['size'] != o['size']: why = f"size {d['size']:#x} in DOS, {o['size']:#x} on the host"
        for p in d['fields']:
            if why: break
            if d['fields'][p] != o['fields'].get(p):
                why = f"{p}: DOS {d['fields'][p]}, host {o['fields'].get(p)}"
        if why is None: same.append(tag)
        else:
            diff.append((tag, h, why))
            if tag in FILE_RECORDS: bad.append(tag)
    print(f'{len(recs)} records in {os.path.relpath(INC, root)}: {len(same)} identical, {len(diff)} differ')
    for tag, h, why in diff:
        mark = 'FILE RECORD ' if tag in FILE_RECORDS else ''
        ptr = ' (pointer fields: memory only)' if tag in pointers else ''
        print(f'  {mark}{tag} ({h}){ptr}: {why}')
    # a record with pointer fields must never be copied as bytes to or from a file or buffer
    io = re.compile(r'\b(' + '|'.join(IO_CALLS) + r')\s*\(')
    for p in sorted(sources.all_sources(CFG)):
        if not p.upper().endswith('.C'): continue
        for n, l in enumerate(open(p, encoding='latin1'), 1):
            if not io.search(l): continue
            for t in sorted(pointers):
                if re.search(r'\b(struct|union)\s+' + t + r'\b', l):
                    print(f'  {os.path.relpath(p, root)}:{n}: {t} (pointer fields) copied as bytes: {l.strip()}')
                    bad.append(t)
    filebad = [t for t in FILE_RECORDS if t in pointers]
    for t in filebad: print(f'  FILE RECORD {t} has a pointer field')
    missing = [t for t in FILE_RECORDS if t not in host]
    if missing: print(f'  file records not found in {os.path.relpath(INC, root)}: ' + ' '.join(missing))
    print('file records: ' + ('all identical' if not bad and not filebad else 'NOT identical: ' + ' '.join(bad + filebad)))
    return 1 if bad or filebad else 0


if __name__ == '__main__':
    sys.exit(main(ARGV))
