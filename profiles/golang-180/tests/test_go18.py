"""Unit tests for golang-1.8.0 profile tools (no Docker required)."""
import os
import struct
import subprocess
import sys
import tempfile
import textwrap
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.dirname(TESTS_DIR)
TOOLS = os.path.join(PROFILE, 'tools')
EXHUME = os.path.dirname(os.path.dirname(PROFILE))
sys.path.insert(0, TOOLS)

import gotc
import gomap
import goslotmap
import golinepin
import gopermute
import goclosures
import gopin_vendor


def make_synthetic_go18_elf(version=b'go1.8', nfunc=42):
    """Build a minimal valid ELF64 LE binary with .text, .rodata, .gopclntab, and .shstrtab."""
    shstrtab = b'\x00.shstrtab\x00.text\x00.rodata\x00.gopclntab\x00'
    text = b'\x90' * 16
    rodata = b'runtime.buildVersion\x00' + version + b'\x00runtime.main\x00runtime.morestack\x00'
    pclntab = struct.pack('<IBBBBQ', 0xfffffffb, 0, 0, 1, 8, nfunc) + b'\x00' * 16

    ehdr_size = 64
    off_shstr = ehdr_size
    off_text = off_shstr + len(shstrtab)
    off_rodata = off_text + len(text)
    off_pcln = off_rodata + len(rodata)
    off_shdr = (off_pcln + len(pclntab) + 7) & ~7

    # ELF64 header
    e_ident = b'\x7fELF' + bytes([2, 1, 1, 0]) + b'\x00' * 8
    ehdr = e_ident + struct.pack(
        '<HHIQQQIHHHHHH',
        2,          # ET_EXEC
        62,         # EM_X86_64
        1,          # EV_CURRENT
        0x401000,   # e_entry
        0,          # e_phoff
        off_shdr,   # e_shoff
        0,          # e_flags
        ehdr_size,
        0, 0,       # phentsize, phnum
        64,         # shentsize
        5,          # shnum (null + 4 sections)
        1,          # shstrndx
    )

    def shdr(name_off, sh_type, sh_flags, addr, off, size):
        return struct.pack('<IIQQQQIIQQ', name_off, sh_type, sh_flags, addr, off, size, 0, 0, 8, 0)

    secs = b''.join([
        shdr(0, 0, 0, 0, 0, 0),
        shdr(1, 3, 0, 0, off_shstr, len(shstrtab)),                         # .shstrtab
        shdr(11, 1, 6, 0x401000, off_text, len(text)),                      # .text
        shdr(17, 1, 2, 0x402000, off_rodata, len(rodata)),                  # .rodata
        shdr(25, 1, 2, 0x403000, off_pcln, len(pclntab)),                   # .gopclntab
    ])

    body = ehdr + shstrtab + text + rodata + pclntab
    pad = b'\x00' * (off_shdr - len(body))
    return body + pad + secs


class Go18ProfileTest(unittest.TestCase):
    def test_inspect_elf_go_synthetic(self):
        raw = make_synthetic_go18_elf(b'go1.8', nfunc=1234)
        info = gotc.inspect_elf_go(raw)
        self.assertEqual(info['arch'], 'amd64')
        self.assertFalse(info['dynamic'])
        self.assertTrue(info['stripped_symtab'])
        self.assertTrue(info['stripped_dwarf'])
        self.assertTrue(info['has_gopclntab'])
        self.assertEqual(info['pclntab']['magic'], 0xfffffffb)
        self.assertEqual(info['pclntab']['format'], 'go1.2-1.15')
        self.assertEqual(info['pclntab']['nfunc'], 1234)
        self.assertIn('go1.8', info['go_versions'])

    def test_fingerprint_cli_on_elf(self):
        with tempfile.TemporaryDirectory(prefix='exhume-go18-fp-') as d:
            p = os.path.join(d, 'sample.elf')
            with open(p, 'wb') as f:
                f.write(make_synthetic_go18_elf(b'go1.8', nfunc=77))
            r = subprocess.run(
                [sys.executable, os.path.join(EXHUME, 'tools', 'fingerprint.py'), p],
                capture_output=True, text=True,
            )
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn('ELF64: amd64', r.stdout)
            self.assertIn('nfunc=77', r.stdout)
            self.assertIn('profile golang-180', r.stdout)
            self.assertIn('4/4 signature strings', r.stdout)

    def test_package_of_and_first_party(self):
        self.assertEqual(gomap.package_of('applib/crypto.DeriveKeyA'), 'applib/crypto')
        self.assertEqual(gomap.package_of('store/fs.(*uncachedFile).Read'), 'store/fs')
        self.assertEqual(gomap.package_of('store/fs.uncachedFile.Close'), 'store/fs')
        self.assertEqual(gomap.package_of('main.api_AddItem.func1'), 'main')
        self.assertEqual(gomap.package_of('runtime.mallocgc'), 'runtime')
        self.assertTrue(gomap.is_first_party('applib/crypto'))
        self.assertTrue(gomap.is_first_party('main'))
        self.assertFalse(gomap.is_first_party('runtime'))
        self.assertFalse(gomap.is_first_party('vendor/github.com/pkg/errors'))

    def test_parse_cmp_and_raw(self):
        cmp_out = textwrap.dedent('''\
            MATCH    applib/crypto.DeriveKeyA  (176 vs 176 bytes)
            LINES    applib/crypto.DeriveKeyB  (176 vs 176 bytes)
            DIFF     applib/crypto.hashCheck  (976 vs 960 bytes)
            MISSING  applib/crypto.init

            1/4 functions match (code + line table)
        ''')
        cr = gotc.parse_cmp_output(cmp_out)
        self.assertFalse(cr.ok)
        self.assertEqual(cr.total, 4)
        self.assertEqual(len(cr.matched), 1)
        self.assertEqual(len(cr.lines_only), 1)
        self.assertEqual(len(cr.diffs), 1)
        self.assertEqual(len(cr.missing), 1)

        rr = gotc.parse_raw_output('10432 bytes; 596 differ inside PC-relative displacement fields; 0 differ elsewhere\n')
        self.assertTrue(rr.ok)
        self.assertEqual(rr.total_bytes, 10432)
        self.assertEqual(rr.rel_bytes, 596)
        self.assertEqual(rr.unmasked_diffs, 0)

    def test_skeleton_and_slotmap_parsing(self):
        tsv = (
            '0x401000\t0x4010b0\t176\tapplib/crypto/derive.go\t31\t33\tapplib/crypto.DeriveKeyA\n'
            '0x4010b0\t0x401160\t176\tapplib/crypto/derive.go\t35\t37\tapplib/crypto.DeriveKeyB\n'
        )
        rows = gomap.parse_funcs_tsv(tsv)
        sk = gomap.render_skeleton(rows, 'applib/crypto', 'derive.go')
        self.assertIn('package crypto', sk)
        self.assertIn('31: // START: DeriveKeyA', sk)
        self.assertIn('37: // END: DeriveKeyB', sk)

        listing = textwrap.dedent('''\
            "".foo t=1 size=64
            0x0000 00000 (a.go:10) MOVQ "".salt+32(SP), AX
            0x0005 00005 (a.go:11) MOVQ autotmp_1+48(SP), BX
            "".bar t=1 size=16
        ''')
        slots = goslotmap.parse_listing_slots(listing, 'foo')
        self.assertEqual(slots, {32: 'salt', 48: 'autotmp_1'})
        diff_txt = '   * a.go:10          mov/64 rax, [RSP+0x28]                       | a.go:10          mov/64 rax, [RSP+0x20]\n'
        mapped = goslotmap.map_slots(diff_txt, slots)
        self.assertEqual(mapped, [(0x28, 'salt', 0x20, [0x28])])

    def test_golinepin_and_gopermute_helpers(self):
        diff_txt = (
            '   * f.go:10          mov/64 rax, rbx                              | f.go:8           mov/64 rax, rbx\n'
            '   * f.go:12          ret                                          | f.go:9           ret\n'
        )
        mapping, conflicts = golinepin.extract_line_pairs(diff_txt, 'f.go')
        self.assertEqual(mapping, {8: 10, 9: 12})
        self.assertEqual(conflicts, [])
        src_lines = [f'// l{i}\n' for i in range(1, 8)] + ['func f() {\n', '\treturn\n', '}\n']
        new_lines, ops, unpinned = golinepin.repin_lines(src_lines, mapping)
        self.assertEqual(unpinned, [])
        self.assertEqual(new_lines[9], 'func f() {\n')
        self.assertEqual(new_lines[11], '\treturn\n')

        go_src = 'package p\n\nfunc Foo(xs []int) {\n\tfor i := 0; i < len(xs); i++ {\n\t\tb := &T{}\n\t\t_ = b\n\t}\n}\n'
        i, j = gopermute.find_go_func_span(go_src, 'pkg.Foo')
        body = go_src[i:j]
        muts = dict(gopermute.generate_mutations(body))
        self.assertIn('new(T) for b', muts)
        self.assertIn('range xs', muts)

    def test_goclosures_and_gopin_vendor(self):
        rows = [
            ('src/pkg/a.go', 'type..hash.struct { F uintptr; main.this *main.DB; main.retn *[]uint8 }'),
            ('src/pkg/a.go', 'main.RegularFunc'),
        ]
        found = list(goclosures.extract_closures(rows))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0][1], ['main.this *main.DB', 'main.retn *[]uint8'])

        go_src = 'package p\n\nfunc Foo() {}\nfunc (t *T) Bar(x int) {}\nfunc (T) Baz() {}\n'
        parsed = gopin_vendor.parse_go_funcs(go_src)
        self.assertEqual(parsed['Foo'], [3])
        self.assertEqual(parsed['(*T).Bar'], [4])
        self.assertEqual(parsed['T.Baz'], [5])


if __name__ == '__main__':
    unittest.main()
