import os, sys, tempfile, unittest
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import armmap
from armmap import Row


class ArmmapTest(unittest.TestCase):
    def test_placeholders(self):
        self.assertEqual(armmap.placeholder('sub_08004120'), 0x08004120)
        self.assertEqual(armmap.placeholder('g_20000210'), 0x20000210)
        self.assertEqual(armmap.placeholder('c_08006000'), 0x08006000)
        self.assertIsNone(armmap.placeholder('timer_tick'))
        self.assertIsNone(armmap.placeholder('sub_801896C'))

    def test_annotations(self):
        t = '/* at: timer_tick 0x08004120, g_timers 0x20000210 */\nint x;\n/* at: s_tab 0x08006000 */'
        self.assertEqual(armmap.annotations(t), {'timer_tick': 0x08004120, 'g_timers': 0x20000210, 's_tab': 0x08006000})

    def test_dropped(self):
        t = '/* dropped: s_unused, t_old */\nint x;\n/* dropped: u_dead */'
        self.assertEqual(armmap.dropped(t), {'s_unused', 't_old', 'u_dead'})

    def test_tsv_round_trip(self):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'functions.tsv')
        rows = [Row(0x08004150, 0x18, 'sub_08004150', 'code', ''), Row(0x08004120, 0x30, 'timer_tick', 'code', 'src/t.c')]
        armmap.save(p, rows)
        with open(p) as f: self.assertEqual(f.read().splitlines()[0], 'addr\tsize\tname\tkind\tfile')
        self.assertEqual(armmap.load(p), sorted(rows, key=lambda r: r.addr))

    def test_lookup_and_symdefs(self):
        m = armmap.Map([Row(0x08004120, 0x30, 'timer_tick', 'code', ''), Row(0x08004150, 0x18, 'sub_08004150', 'code', '')],
                       [Row(0x20000210, 8, 'g_20000210', 'ram', '')])
        self.assertEqual(m.address('timer_tick'), 0x08004120)
        self.assertEqual(m.address('sub_08009000'), 0x08009000)       # a placeholder needs no row
        self.assertEqual(m.address('mine', {'mine': 0x20000400}), 0x20000400)
        self.assertIsNone(m.address('unknown'))
        e = m.symdef_entries(exclude={'timer_tick'}, extra={'g_x': 0x20000500})
        self.assertIn((0x08004150, 'T', 'sub_08004150'), e)
        self.assertIn((0x20000210, 'D', 'g_20000210'), e)
        self.assertIn((0x20000500, 'D', 'g_x'), e)
        self.assertNotIn('timer_tick', [n for _, _, n in e])
        text = armmap.symdefs_text([(0x08004150, 'T', 'sub_08004150')])
        self.assertTrue(text.startswith('#<SYMDEFS>#'))
        self.assertIn('0x08004151 T sub_08004150', text)                # Thumb bit on code

    def test_scatter_unaligned_goes_in_one_shared_load_region(self):
        # armlink wants regions on 4-byte boundaries; a .data of halfwords can sit on 2: it goes
        # after a 2-byte pad from exhume_pad.o in a region at the aligned address below
        text, regions = armmap.scatter_text('t.o', [('i.f', 0x08004120), ('.data', 0x2000001A)])

        self.assertIn('ER_1 0x08004120 FIXED { t.o(i.f) }', text)
        self.assertIn('LR_UNALIGNED 0x0E000000 {', text)
        # the pad is the same kind as the section: armlink starts RW after RO on a 4-byte boundary
        self.assertIn('ER_2 0x20000018 { exhume_pad.o(.pad1, +FIRST) t.o(.data) }', text)
        self.assertIn('unsigned char exhume_pad1[2] = { 1 }', armmap.pad_source([('i.f', 0x08004120), ('.data', 0x2000001A)]))
        text2, _ = armmap.scatter_text('t.o', [('.bss', 0x20000101), ('.constdata', 0x08000203)])
        src2 = armmap.pad_source([('.bss', 0x20000101), ('.constdata', 0x08000203)])
        self.assertIn('exhume_pad.o(.pad1, +FIRST) t.o(.bss)', text2)
        self.assertIn('zero_init)) unsigned char exhume_pad1[1]', src2)
        self.assertIn('const unsigned char exhume_pad2[3]', src2)
        self.assertEqual(regions, {'ER_1': 'i.f', 'ER_2': '.data'})

    def test_each_unaligned_section_gets_its_own_pad(self):
        # one pad section can be placed once: two unaligned code sections need two pads
        text, regions = armmap.scatter_text('t.o', [('i.a', 0x08000002), ('i.b', 0x08000012)])
        self.assertIn('exhume_pad.o(.pad1, +FIRST) t.o(i.a)', text)
        self.assertIn('exhume_pad.o(.pad2, +FIRST) t.o(i.b)', text)
        src = armmap.pad_source([('i.a', 0x08000002), ('i.b', 0x08000012)])
        self.assertIn('section(".pad1")', src); self.assertIn('section(".pad2")', src)

    def test_scatter(self):
        text, regions = armmap.scatter_text('t.o', [('i.timer_tick', 0x08004120), ('.data', 0x20000210)])
        self.assertIn('LR_1 0x08004120 {', text)
        self.assertIn('ER_1 0x08004120 FIXED { t.o(i.timer_tick) }', text)
        self.assertEqual(regions, {'ER_1': 'i.timer_tick', 'ER_2': '.data'})


if __name__ == '__main__':
    unittest.main()
