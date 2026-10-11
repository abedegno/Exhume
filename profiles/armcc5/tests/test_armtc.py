import os, sys, shutil, subprocess, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import armtc

HOME = os.environ.get('EXHUME_ARMCC_HOME')
DOCKER = shutil.which('docker') and subprocess.run(['docker', 'info'], capture_output=True).returncode == 0


class ParseTest(unittest.TestCase):
    def setUp(self):
        self.info = armtc.parse_symbols(open(os.path.join(HERE, 'fixtures', 'symtab.txt')).read())

    def test_sections(self):
        names = {n for n, _ in self.info.sections.values()}
        self.assertTrue({'i.f', 'i.name', '.constdata', '.data'} <= names)
        self.assertEqual(self.info.sections[1][0], 'i.f')

    def test_symbols(self):
        by = {s.name: s for s in self.info.symbols}
        self.assertEqual((by['f'].sec, by['f'].type, by['f'].value, by['f'].size), (1, 'Code', 1, 0x18))
        self.assertEqual((by['g_tbl'].sec, by['g_tbl'].value, by['g_tbl'].bind), (6, 2, 'Gb'))
        self.assertEqual((by['s_name'].bind, by['s_name'].sec), ('Lc', 5))
        self.assertEqual(by['g_far'].sec, 'Ref')
        self.assertEqual(by['sub_08009000'].type, 'Code')

    def test_section_flags(self):
        names = {n: i for i, (n, _) in self.info.sections.items()}
        self.assertIn('SHF_ALLOC', self.info.flags[names['i.f']])
        self.assertIn('SHF_EXECINSTR', self.info.flags[names['i.f']])
        self.assertNotIn('SHF_ALLOC', self.info.flags.get(names['.symtab'], ''))

    def test_section_alignment(self):
        self.assertEqual(self.info.align[1], 4)          # i.f: "Size : 36 bytes (alignment 4)"

    def test_noise_lines_are_dropped(self):
        self.assertEqual(armtc.clean('a\nassertion failed [x86_avx_state_ptr->xsave_header.xfeatures == k]: \n(ThreadContextSignals.cpp:414 rt_sigreturn)\nb'), 'a\nb')


@unittest.skipUnless(HOME and DOCKER, 'needs EXHUME_ARMCC_HOME and Docker')
class ToolchainTest(unittest.TestCase):
    def test_compile_symbols_link_bin(self):
        root = tempfile.mkdtemp(dir=os.path.expanduser('~'))
        try:
            open(os.path.join(root, 'd.c'), 'w').write('unsigned char soc(unsigned short x){return x/10;}\n')
            open(os.path.join(root, 'd.sct'), 'w').write('LR 0x08000000 { ER_soc 0x08000000 FIXED { d.o(i.soc) } }\n')
            tc = self.tc = armtc.Toolchain(HOME, root)
            ok, log = tc.compile(os.path.join(root, 'd.c'), os.path.join(root, 'd.o'), '--cpu Cortex-M3 -O1 -Otime --split_sections')
            self.assertTrue(ok, log)
            info = tc.symbols(os.path.join(root, 'd.o'))
            self.assertIn('i.soc', {n for n, _ in info.sections.values()})
            ok, log = tc.link([os.path.join(root, 'd.o')], os.path.join(root, 'd.sct'), os.path.join(root, 'd.axf'))
            self.assertTrue(ok, log)
            bins = tc.tobin(os.path.join(root, 'd.axf'), os.path.join(root, 'bin'))
            self.assertEqual(bins['ER_soc'].hex(' '), '4c f6 cd 41 48 43 c0 f3 c7 40 70 47')
        finally:
            self.tc.stop(); shutil.rmtree(root, ignore_errors=True)

    def test_two_cold_starts_at_once_share_one_container(self):
        import threading
        root = tempfile.mkdtemp(dir=os.path.expanduser('~'))
        tcs = [armtc.Toolchain(HOME, root) for _ in range(3)]
        self.tc = tcs[0]
        try:
            outs = [None] * 3
            def go(i): outs[i] = tcs[i].run('armcc', ['--vsn'], root)
            ts = [threading.Thread(target=go, args=(i,)) for i in range(3)]
            for t in ts: t.start()
            for t in ts: t.join()
            for rc, out in outs:
                self.assertEqual(rc, 0, out); self.assertIn('ARM Compiler 5.06', out)
        finally:
            self.tc.stop(); shutil.rmtree(root, ignore_errors=True)

    def test_compile_error_is_not_retried_and_reported(self):
        root = tempfile.mkdtemp(dir=os.path.expanduser('~'))
        try:
            open(os.path.join(root, 'e.c'), 'w').write('int f(void){ return }\n')
            self.tc = armtc.Toolchain(HOME, root)
            ok, log = self.tc.compile(os.path.join(root, 'e.c'), os.path.join(root, 'e.o'), '--cpu Cortex-M3')
            self.assertFalse(ok); self.assertIn('Error', log)
        finally:
            self.tc.stop(); shutil.rmtree(root, ignore_errors=True)


if __name__ == '__main__':
    unittest.main()
