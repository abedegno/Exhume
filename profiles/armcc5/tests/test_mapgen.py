import os, shutil, subprocess, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__)); PROFILE = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(PROFILE, 'tools')); sys.path.insert(0, os.path.join(PROFILE, 'testfw'))
import armtc, answerkey, ehex, rwdata, armmapgen

HOME = os.environ.get('EXHUME_ARMCC_HOME')
DOCKER = shutil.which('docker') and subprocess.run(['docker', 'info'], capture_output=True).returncode == 0
GHIDRA = os.path.exists(armmapgen.ghidra_headless())


@unittest.skipUnless(HOME and DOCKER and GHIDRA, 'needs EXHUME_ARMCC_HOME, Docker and Ghidra')
class MapGen(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = tempfile.mkdtemp(dir=os.path.expanduser('~'), prefix='exhume-map-')
        cls.tc = armtc.Toolchain(HOME, cls.root)
        cls.key = answerkey.build(cls.tc, cls.root)
        cls.img = ehex.Image(os.path.join(cls.root, 'target', 'testfw.hex'))
        cls.frows, cls.drows, cls.cov = armmapgen.generate(cls.img, os.path.join(cls.root, 'ghidra'))

    @classmethod
    def tearDownClass(cls):
        cls.tc.stop(); shutil.rmtree(cls.root, ignore_errors=True)

    def test_every_answer_function_is_a_row(self):
        starts = {r.addr for r in self.frows}
        for name in ('crc16', 'main', 'next_frame', 'soc_percent', 'step', 'timers_tick', 'tbl', 'cfg', 'mixed', 'buf',
                     'version', 'lookup', 'big_switch'):
            self.assertIn(self.key['answers'][name][0], starts, name)

    def test_function_sizes_cover_code_pool_and_padding(self):
        # a row runs to the next row: the answer key's section sizes fit inside, padding aside
        rows = {r.addr: r.size for r in self.frows}
        crc = self.key['answers']['crc16'][0]
        self.assertIn(rows[crc], (0x22, 0x24))

    def test_a_switch_with_trampolines_is_one_function(self):
        # armcc reaches distant cases through b trampolines after the return; none is a function
        start = self.key['answers']['big_switch'][0]
        rows = sorted(r.addr for r in self.frows)
        nxt = rows[rows.index(start) + 1]
        self.assertGreater(nxt - start, 0x200, 'big_switch was split')

    def test_full_coverage(self):
        self.assertEqual(self.cov, [], '\n'.join(self.cov))

    def test_ram_rows(self):
        addrs = {r.addr for r in self.drows}
        for name in ('g_timers', 'g_tbl', 'g_buf', 'g_soc_tenths'):
            self.assertIn(self.key['answers'][name][0], addrs, name)

    def test_ro_rows(self):
        addrs = {r.addr: r for r in self.drows}
        crc_hi = self.key['answers']['crc_hi'][0]
        self.assertEqual(addrs[crc_hi].kind, 'ro')


if __name__ == '__main__':
    unittest.main()


class Trampolines(unittest.TestCase):
    def image(self, halfwords):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'code.bin')
        with open(p, 'wb') as f: f.write(b''.join(h.to_bytes(2, 'little') for h in halfwords))
        return ehex.Image(p)

    def test_b_run_after_a_return(self):
        # pop {r4, pc}; b +8; b +6; b +4; nop... : three stubs after a return, each target is case code
        img = self.image([0xBD10, 0xE002, 0xE001, 0xE000, 0xBF00, 0xBF00, 0xBF00, 0xBF00])
        got = armmapgen.trampolines(img, 0, 16)
        self.assertTrue({2, 4, 6} <= got, got)
        self.assertIn(10, got)                     # 2 + 4 + 2*2

    def test_literal_words_are_not_a_run(self):
        img = self.image([0xBD10, 0xE002, 0xE001, 0xE000, 0xBF00, 0xBF00, 0xBF00, 0xBF00])
        self.assertEqual(armmapgen.trampolines(img, 0, 16, skip={2, 3, 4, 5, 6, 7}), set())

    def test_switch_bound_with_bhs_default_is_the_compare_value(self):
        # cmp r0,#3; bhs default; tbb [pc, r0]: three cases. With bhi it would be four.
        img = self.image([0x2803, 0xD203, 0xE8DF, 0xF000, 0x0201, 0x0003, 0x4770, 0x4770, 0x4770])
        _, _, sw = armmapgen.scan(img, [(0, 18)])
        self.assertEqual(sw[0][2], 3)
        img2 = self.image([0x2803, 0xD803, 0xE8DF, 0xF000, 0x0201, 0x0403, 0x4770, 0x4770, 0x4770])
        _, _, sw2 = armmapgen.scan(img2, [(0, 18)])
        self.assertEqual(sw2[0][2], 4)

    def test_odd_literal_values_are_code_pointers_not_data(self):
        self.assertEqual(armmapgen.data_starts({0x10: 0x41, 0x14: 0x44}, set(), 0x40, 0x80, set()), {0x44})

    def test_a_single_b_after_a_return_is_not_a_run(self):
        img = self.image([0xBD10, 0xE002, 0xBF00, 0xBF00, 0xBF00, 0xBF00])
        self.assertEqual(armmapgen.trampolines(img, 0, 12), set())


class InsideCode(unittest.TestCase):
    def image(self, halfwords):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'code.bin')
        with open(p, 'wb') as f: f.write(b''.join(h.to_bytes(2, 'little') for h in halfwords))
        return ehex.Image(p)

    def test_mid_body_after_an_ordinary_instruction(self):
        # movs r0,#1; adds r0,#2; adds r0,#3; bx lr: 4 is inside, reached by falling through
        img = self.image([0x2001, 0x3002, 0x3003, 0x4770])
        self.assertTrue(armmapgen.inside_code(img, 4, {0: [(0, 8)]}))

    def test_after_a_return_is_not_inside(self):
        # movs r0,#1; bx lr; adds r0,#3; bx lr: 4 follows a return
        img = self.image([0x2001, 0x4770, 0x3003, 0x4770])
        self.assertFalse(armmapgen.inside_code(img, 4, {0: [(0, 8)]}))
