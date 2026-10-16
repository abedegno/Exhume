import os, sys, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import armtc, armmatch, armmap
from armmap import Row


class PlaceTest(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(HERE, 'fixtures', 'symtab.txt')) as f:
            self.info = armtc.parse_symbols(f.read())

    def test_all_known(self):
        m = armmap.Map([Row(0x08018000, 0x24, 'f', 'code', ''), Row(0x08018100, 8, 'name', 'code', '')],
                       [Row(0x08018200, 0x15, 's_name', 'ro', ''), Row(0x20000202, 6, 'g_tbl', 'rw', '')])
        placements, defined, errors, unplaced = armmatch.place(self.info, m, {})
        self.assertEqual((errors, unplaced), ([], []))
        self.assertEqual(dict(placements), {'i.f': 0x08018000, 'i.name': 0x08018100, '.constdata': 0x08018200, '.data': 0x20000200})
        self.assertEqual(defined['g_flag'], 0x20000200)

    def test_unknown_function_is_an_error(self):
        m = armmap.Map([Row(0x08018000, 0x24, 'f', 'code', '')], [])
        _, _, errors, _ = armmatch.place(self.info, m, {})
        self.assertTrue(any('i.name' in e for e in errors))

    def test_unknown_data_is_left_unplaced(self):
        # armlink drops unreferenced data, so the original may simply not have it
        m = armmap.Map([Row(0x08018000, 0x24, 'f', 'code', ''), Row(0x08018100, 8, 'name', 'code', '')], [])
        placements, _, errors, unplaced = armmatch.place(self.info, m, {})
        self.assertEqual(errors, [])
        self.assertEqual(sorted(unplaced), ['.constdata', '.data'])
        self.assertNotIn('.data', dict(placements))

    def test_disagreeing_members_is_an_error(self):
        m = armmap.Map([Row(0x08018000, 0x24, 'f', 'code', ''), Row(0x08018100, 8, 'name', 'code', '')],
                       [Row(0x08018200, 0x15, 's_name', 'ro', '')])
        _, _, errors, _ = armmatch.place(self.info, m, {'g_flag': 0x20000200, 'g_tbl': 0x20000300})
        self.assertTrue(any('disagree' in e for e in errors))

    def test_region_bytes_are_trimmed_to_the_section(self):
        # fromelf --bin pads a region to its alignment; the section's own size is what counts
        self.assertEqual(armmatch.section_bytes(b'\x01\x02\x00\x00', 2), b'\x01\x02')
        self.assertEqual(armmatch.section_bytes(b'\x01\x02', None), b'\x01\x02')

    def test_a_section_needing_more_alignment_than_its_address_is_an_error(self):
        info = armtc.ObjectInfo({1: ('.data', 4)}, [armtc.Sym('a', 0, 'Gb', 1, 'Data', 4)], {1: 4})
        m = armmap.Map([], [Row(0x20000002, 4, 'a', 'rw', '')])
        _, _, errors, _ = armmatch.place(info, m, {})
        self.assertTrue(any('alignment' in e for e in errors), errors)

    def test_overridden_weak_symbols_do_not_place_their_section(self):
        # a startup .text: Reset_Handler strong at 0, SysTick_Handler weak at 8; the map puts
        # SysTick_Handler elsewhere (the C one that overrode it): place by the strong symbol, and
        # leave the weak name to the map's definition
        info = armtc.ObjectInfo({1: ('.text', 0x1C)}, [armtc.Sym('Reset_Handler', 1, 'Gb', 1, 'Code', 8),
                                                       armtc.Sym('SysTick_Handler', 9, 'Wk', 1, 'Code', 2)],
                                {1: 4}, {1: 'SHF_ALLOC + SHF_EXECINSTR'}, {1: 'SHT_PROGBITS'})
        m = armmap.Map([Row(0x08008164, 0x1C, 'Reset_Handler', 'asm', ''), Row(0x08014850, 0x10, 'SysTick_Handler', 'code', '')], [])
        placements, defined, errors, _ = armmatch.place(info, m, {})
        self.assertEqual(errors, [])
        self.assertEqual(dict(placements), {'.text': 0x08008164})
        self.assertNotIn('SysTick_Handler', defined)

    def test_all_weak_startup_placed_by_its_annotation(self):
        # ST marks every handler [WEAK], Reset_Handler too: the at: line places the section
        info = armtc.ObjectInfo({1: ('.text', 0x1C)}, [armtc.Sym('Reset_Handler', 1, 'Wk', 1, 'Code', 8),
                                                       armtc.Sym('SysTick_Handler', 9, 'Wk', 1, 'Code', 2)],
                                {1: 4}, {1: 'SHF_ALLOC + SHF_EXECINSTR'}, {1: 'SHT_PROGBITS'})
        m = armmap.Map([Row(0x08008164, 0x1C, 'Reset_Handler', 'asm', ''), Row(0x08014850, 0x10, 'SysTick_Handler', 'code', '')], [])
        placements, defined, errors, _ = armmatch.place(info, m, {'Reset_Handler': 0x08008164})
        self.assertEqual(errors, [])
        self.assertEqual(dict(placements), {'.text': 0x08008164})
        self.assertNotIn('SysTick_Handler', defined)

    def test_weak_symbols_an_at_line_puts_elsewhere_are_overridden(self):
        # the startup source names the application's real handler on an at: line (its map row
        # keeps a placeholder name): the strong Reset_Handler places the section, and the weak
        # default is left to symdefs at the real handler's address
        info = armtc.ObjectInfo({1: ('.text', 0x1C)}, [armtc.Sym('Reset_Handler', 1, 'Gb', 1, 'Code', 8),
                                                       armtc.Sym('NMI_Handler', 9, 'Wk', 1, 'Code', 2)],
                                {1: 4}, {1: 'SHF_ALLOC + SHF_EXECINSTR'}, {1: 'SHT_PROGBITS'})
        m = armmap.Map([Row(0x08008164, 0x1C, 'Reset_Handler', 'asm', ''), Row(0x08004200, 0x10, 'sub_08004200', 'code', '')], [])
        extra = {'Reset_Handler': 0x08008164, 'NMI_Handler': 0x08004200}
        placements, defined, errors, _ = armmatch.place(info, m, extra)
        self.assertEqual(errors, [])
        self.assertEqual(dict(placements), {'.text': 0x08008164})
        self.assertNotIn('NMI_Handler', defined)
        self.assertIn((0x08004200, 'T', 'NMI_Handler'), m.symdef_entries(set(defined), extra))

    def test_weak_symbols_alone_that_disagree_are_an_error(self):
        info = armtc.ObjectInfo({1: ('.text', 0x1C)}, [armtc.Sym('Reset_Handler', 1, 'Wk', 1, 'Code', 8),
                                                       armtc.Sym('NMI_Handler', 9, 'Wk', 1, 'Code', 2)],
                                {1: 4}, {1: 'SHF_ALLOC + SHF_EXECINSTR'}, {1: 'SHT_PROGBITS'})
        _, _, errors, _ = armmatch.place(info, armmap.Map([], []), {'Reset_Handler': 0x08008164, 'NMI_Handler': 0x08004200})
        self.assertTrue(any('disagree' in e for e in errors), errors)

    def test_symbol_size_check_accepts_a_label_whose_row_is_the_whole_section(self):
        self.assertTrue(armmatch.size_fits(sym_size=4, sym_offset=0, row_size=0x150, section_len=0x150))
        self.assertFalse(armmatch.size_fits(sym_size=4, sym_offset=4, row_size=0x150, section_len=0x150))
        self.assertTrue(armmatch.size_fits(sym_size=8, sym_offset=4, row_size=8, section_len=0x150))

    def test_padding(self):
        self.assertTrue(armmatch.padding_ok(b'\x01\x02', b'\x01\x02\x00\x00', 0x08000020))
        self.assertFalse(armmatch.padding_ok(b'\x01\x02', b'\x01\x02\x00\x01', 0x08000020))
        self.assertFalse(armmatch.padding_ok(b'\x01', b'\x01\x00\x00\x00\x00', 0x08000000))
        self.assertFalse(armmatch.padding_ok(b'\x01\x02', b'\x01\x02\x00\x00', 0x08000022))   # not ending on 4

    def test_compare(self):
        self.assertEqual(armmatch.compare(b'\x01\x02\x03', b'\x01\x09\x03'), [1])

    def test_compare_fails_on_a_length_difference(self):
        # a build that equals a prefix of the original is not a match
        self.assertEqual(armmatch.compare(b'\x01\x02', b'\x01\x02\x03'), [2])
        self.assertEqual(armmatch.compare(b'', b'\x01'), [0])

    def test_odd_data_offsets_keep_their_low_bit(self):
        info = armtc.ObjectInfo({1: ('.constdata', 2)}, [armtc.Sym('a', 0, 'Gb', 1, 'Data', 1), armtc.Sym('b', 1, 'Gb', 1, 'Data', 1)])
        m = armmap.Map([], [Row(0x08000101, 1, 'b', 'ro', '')])
        placements, defined, errors, _ = armmatch.place(info, m, {})
        self.assertEqual(errors, [])
        self.assertEqual(dict(placements), {'.constdata': 0x08000100})
        self.assertEqual(defined, {'a': 0x08000100, 'b': 0x08000101})


if __name__ == '__main__':
    unittest.main()
