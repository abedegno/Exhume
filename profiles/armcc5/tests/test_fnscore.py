"""fnscore.py: every function of an object scored against the image, on objplace's fixtures.

No toolchain is needed: the objects are objplace's test objects rebuilt from their fromelf
listings (test_objplace.py), and the image is armlink's bytes for them. Needs capstone."""
import importlib.util, os, sys, tempfile, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
sys.path.insert(0, HERE)
import armmap, ehex
from armmap import Row

CAPSTONE = importlib.util.find_spec('capstone') is not None
if CAPSTONE:
    import fnscore
    import test_objplace as T


def image(text):
    """An Image with text at T.TEXT (and the unit fixture's .constdata after it)."""
    img = bytearray(b'\xff' * 0x100) + bytearray(text)
    img += b'\xff' * (0x100 - len(text)) + T.ARMLINK_CONST
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, 'fw.hex')
        with open(path, 'w') as f: f.write(ehex.write(0x08000000, bytes(img)))
        return ehex.Image(path)


@unittest.skipUnless(CAPSTONE, 'needs capstone')
class ScoreTest(unittest.TestCase):
    def repeat_map(self, maybe=('maybe', 0x1A, 0xE)):
        rows = [Row(T.TEXT, 0xA, 'bump', 'code', ''), Row(T.TEXT + 0xA, 0x10, 'step', 'code', '')]
        if maybe: rows.append(Row(T.TEXT + maybe[1], maybe[2], maybe[0], 'code', ''))
        return armmap.Map(rows, [])

    def test_every_function_matches(self):
        lines, total, unmapped = fnscore.score(T.repeat_obj(), self.repeat_map(), image(T.R_ARMLINK_TEXT))
        self.assertEqual((total, unmapped), (0, []))
        self.assertEqual([l.split() for l in lines], [['bump', '='], ['step', '='], ['maybe', '='], ['TOTAL', '0']])

    def test_a_literal_pool_and_a_call_are_not_differences(self):
        m = armmap.Map([Row(T.TEXT, 0x12, 'helper', 'code', ''), Row(T.TEXT + 0x12, 0x1E, 'caller', 'code', '')], [])
        lines, total, _ = fnscore.score(T.obj(), m, image(T.ARMLINK_TEXT))
        self.assertEqual(total, 0, lines)

    def test_a_changed_instruction_is_one_difference(self):
        changed = bytearray(T.R_ARMLINK_TEXT); changed[0x12] = 0x81      # step's ldr r1, [r0, #4] -> [r0, #8] in the image
        lines, total, _ = fnscore.score(T.repeat_obj(), self.repeat_map(), image(bytes(changed)), want={'step'})
        self.assertEqual(total, 1)
        self.assertIn('step                 1', lines)
        self.assertTrue(any(l.startswith('    - ') for l in lines) and any(l.startswith('    + ') for l in lines), lines)

    def test_pool_values_name_a_section_symbol_by_the_map(self):
        m = armmap.Map(self.repeat_map().functions, [Row(0x20000000, 4, 'state', 'rw', '')])
        lines, _, _ = fnscore.score(T.repeat_obj(), m, image(T.R_ARMLINK_TEXT), want={'bump'}, show_pool=True)
        self.assertEqual(lines[:2], ['bump                 =', '      r0   20000000        r0   20000000'])

    def test_size_entry_and_unmapped_code_are_reported(self):
        lines, _, unmapped = fnscore.score(T.repeat_obj(), self.repeat_map(None), image(T.R_ARMLINK_TEXT))
        self.assertEqual(unmapped, ['maybe'])
        self.assertEqual(lines[0], 'UNMAPPED_CODE maybe')
        lines, _, _ = fnscore.score(T.repeat_obj(), self.repeat_map(('maybe', 0x1C, 0xC)), image(T.R_ARMLINK_TEXT))
        self.assertIn('size 0xe/0xc entry 0x1a/0x1c', next(l for l in lines if l.startswith('maybe')))


@unittest.skipUnless(CAPSTONE, 'needs capstone')
class JumpTableTest(unittest.TestCase):
    """A bounded tbb/tbh table is data, its length from the bound check before it."""

    def check_table(self, guard, table, count, expected_bytes):
        code = bytes.fromhex('0528' + guard + table + '00' * expected_bytes + '01207047')
        found = set()
        rows = fnscore.decode(code, 0x1000, found=found)
        self.assertEqual(len(found), expected_bytes)
        self.assertEqual(len(rows), count)
        self.assertEqual(rows[-2][1], 'movs r0, #1')

    def test_tbb_unsigned_exclusive_bound_and_padding(self):
        self.check_table('00d2', 'dfe800f0', 5, 6)          # cmp r0, #5; bhs; tbb: 5 entries, padded to 6

    def test_tbh_unsigned_inclusive_bound(self):
        self.check_table('00d8', 'dfe810f0', 5, 12)         # cmp r0, #5; bhi; tbh: 6 entries

    def test_branch_island_before_table(self):
        code = bytes.fromhex('032800d200e000e0dfe800f00000000001207047')
        found = set()
        rows = fnscore.decode(code, 0x1000, found=found)
        self.assertEqual(found, set(range(0x100C, 0x1010)))
        self.assertEqual(rows[-1][1], 'bx lr')

    def test_unknown_guard_is_not_a_table_bound(self):
        code = bytes.fromhex('032800d1dfe800f00000000001207047')
        found = set()
        fnscore.decode(code, 0x1000, found=found)
        self.assertEqual(found, set())


@unittest.skipUnless(CAPSTONE, 'needs capstone')
class CompareTest(unittest.TestCase):
    def test_a_branch_to_a_different_instruction_counts(self):
        exp = ['cmp r0, #0', 'beq L3', 'adds r0, #1', 'bx lr']
        self.assertEqual(fnscore.compare(exp, list(exp)), [])
        self.assertEqual(fnscore.compare(exp, ['cmp r0, #0', 'beq L2', 'adds r0, #1', 'bx lr']),
                         [('replace', 1, 2, 1, 2)])

    def test_an_inserted_instruction_moves_the_labels_without_other_differences(self):
        exp = fnscore.normalise([(0, 'cmp r0, #0'), (2, 'beq #0x8'), (4, 'adds r0, #1'), (6, 'nop'), (8, 'bx lr')])
        got = fnscore.normalise([(0, 'cmp r0, #0'), (2, 'beq #0xa'), (4, 'adds r0, #1'), (6, 'nop'), (8, 'nop'),
                                 (10, 'bx lr')])
        self.assertEqual(fnscore.compare(exp, got), [('insert', 4, 4, 4, 5)])


if __name__ == '__main__':
    unittest.main()
