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
        text, regions = armmap.scatter_text('t.o', [('i.f', 0x08004120, 4), ('.data', 0x2000001A, 8)])

        self.assertIn('ER_1 0x08004120 FIXED { t.o(i.f) }', text)
        self.assertIn('LR_2 0x0E010000 {', text)
        # the pad is the same kind as the section: armlink starts RW after RO on a 4-byte boundary
        self.assertIn('ER_2 0x20000018 { exhume_pad.o(.pad1, +FIRST) t.o(.data) }', text)
        self.assertIn('unsigned char exhume_pad1[2] = { 1 }', armmap.pad_source([('i.f', 0x08004120, 4), ('.data', 0x2000001A, 8)]))
        text2, _ = armmap.scatter_text('t.o', [('.bss', 0x20000101, 4), ('.constdata', 0x08000203, 4)])
        src2 = armmap.pad_source([('.bss', 0x20000101, 4), ('.constdata', 0x08000203, 4)])
        # groups go in address order: the flash constant first, then the RAM .bss
        self.assertIn('exhume_pad.o(.pad1, +FIRST) t.o(.constdata)', text2)
        self.assertIn('exhume_pad.o(.pad2, +FIRST) t.o(.bss)', text2)
        self.assertIn('const unsigned char exhume_pad1[3]', src2)
        self.assertIn('zero_init)) unsigned char exhume_pad2[1]', src2)
        self.assertEqual(regions, {'ER_1': [('i.f', 0x08004120)], 'ER_2': [('.data', 0x2000001A)]})

    def test_a_2_aligned_follower_shares_its_predecessors_region(self):
        # i.b starts 2-aligned straight after i.a: one region holds both, i.a pinned first, so
        # armlink doesn't round i.a's region up over i.b (and doesn't reorder them by name)
        text, regions = armmap.scatter_text('t.o', [('i.a', 0x08000028, 0x26), ('i.b', 0x0800004E, 0x1A), ('i.c', 0x08000100, 8)])
        self.assertIn('ER_1 0x08000028 FIXED { t.o(i.a, +FIRST) t.o(i.b) }', text)
        self.assertEqual(regions['ER_1'], [('i.a', 0x08000028), ('i.b', 0x0800004E)])
        self.assertEqual(regions['ER_2'], [('i.c', 0x08000100)])

    def test_4_aligned_neighbours_keep_their_own_regions(self):
        # adjacent but 4-aligned: separate regions, so names never decide their order
        text, regions = armmap.scatter_text('t.o', [('i.main', 0x08000010, 0x10), ('i.sub_08000020', 0x08000020, 0x10)])
        self.assertEqual(len(regions), 2)

    def test_each_unaligned_group_gets_its_own_load_region(self):
        text, regions = armmap.scatter_text('t.o', [('i.a', 0x0800004E, 0x1A), ('i.b', 0x0800008E, 0x1A)])
        self.assertEqual(text.count('LR_'), 2)

    def test_each_unaligned_section_gets_its_own_pad(self):
        # one pad section can be placed once: two unaligned code sections need two pads
        text, regions = armmap.scatter_text('t.o', [('i.a', 0x08000002, 4), ('i.b', 0x08000012, 4)])
        self.assertIn('exhume_pad.o(.pad1, +FIRST) t.o(i.a)', text)
        self.assertIn('exhume_pad.o(.pad2, +FIRST) t.o(i.b)', text)
        src = armmap.pad_source([('i.a', 0x08000002, 4), ('i.b', 0x08000012, 4)])
        self.assertIn('section(".pad1")', src); self.assertIn('section(".pad2")', src)

    def test_scatter(self):
        text, regions = armmap.scatter_text('t.o', [('i.timer_tick', 0x08004120, 0x30), ('.data', 0x20000210, 8)])
        self.assertIn('LR_1 0x08004120 {', text)
        self.assertIn('ER_1 0x08004120 FIXED { t.o(i.timer_tick) }', text)
        self.assertEqual(regions, {'ER_1': [('i.timer_tick', 0x08004120)], 'ER_2': [('.data', 0x20000210)]})


if __name__ == '__main__':
    unittest.main()
