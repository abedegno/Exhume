import os, re, sys, tempfile, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import armvariant as V



def rd(p):
    with open(p) as f: return f.read()


def wr(p, s):
    with open(p, 'w') as f: f.write(s)


LISTING = rd(os.path.join(HERE, 'fixtures', 'fromelf_c.txt'))


class FakeRunner:
    """armcc as it behaves: files in order, stopping at the first with an error; fromelf -c
    lists each object (here: the fixture's listing, once per object)."""
    runner = 'docker'

    def __init__(self):
        self.calls = []

    def opts(self, o):
        return o.split()

    def run(self, tool, args, cwd):
        self.calls.append((tool, [a for a in args if a.endswith(('.c', '.o'))]))
        if tool == 'armcc':
            out = ''
            for a in args:
                if not a.endswith('.c'): continue
                text = rd(os.path.join(cwd, a))
                if 'ERROR' in text:
                    return 1, out + f'"{a}", line 1: Error:  #20: identifier "ERROR" is undefined\n'
                wr(os.path.join(cwd, a[:-2] + '.o'), text)
            return 0, out
        return 0, ''.join(f'\n    File Name: {a}\n' + LISTING for a in args if a.endswith('.o'))


class ListingTest(unittest.TestCase):
    def test_bytes_and_pool(self):
        b, pool = V.func_bytes(LISTING, 'put')
        self.assertEqual(len(b), 44)
        self.assertEqual(sorted(pool), [0x20, 0x24, 0x28])
        self.assertEqual(b[:2], bytes.fromhex('0748'))                   # LDR r0,[pc,#28]: 4807 little-endian

    def test_a_thumb2_instruction_is_two_halfwords(self):
        b, _ = V.func_bytes(LISTING, 'tenth')
        self.assertEqual(b[:4], bytes.fromhex('4cf6cd41'))             # f64c41cd: movw r1,#0xcccd

    def test_a_switch_table_is_data_but_not_pool(self):
        b, pool = V.func_bytes(LISTING, 'sw')
        self.assertEqual(pool, set())
        self.assertEqual(b[8:14], bytes.fromhex('030507090b00'))       # DCDU 0x09070503, DCW 11
        lst = V.listing(b, pool)
        self.assertIn('.table 030507090b00', lst)
        self.assertEqual(lst[-1], 'bx lr')

    def test_no_section_gives_nothing(self):
        self.assertEqual(V.func_bytes(LISTING, 'nosuch'), (b'', set()))

    def test_listing_masks_calls_and_pool(self):
        b, pool = V.func_bytes(LISTING, 'callit')
        lst = V.listing(b, pool)
        self.assertIn('bl ?', lst)
        self.assertEqual(lst[-1], '.word')

    def test_the_image_sweep_finds_the_pool(self):
        b, pool = V.func_bytes(LISTING, 'put')
        self.assertEqual(V.image_pool(b), pool)
        b, pool = V.func_bytes(LISTING, 'msg')                          # an adr'd string runs to the end
        self.assertEqual(V.image_pool(b), {4, 8})

    def test_score_counts_an_insertion_once(self):
        a = ['movs r0, #1', 'adds r0, #2', 'bx lr']
        self.assertEqual(V.score(a, a), 0)
        self.assertEqual(V.score(['nop'] + a, a), 1)
        self.assertEqual(V.score(['movs r1, #1', 'adds r0, #2', 'bx lr'], a), 1)

    def test_structural_masks_registers_and_targets(self):
        a = V.structural(['ldr.w r7, [pc, #0x3fc]', 'add.w r0, r7, r0, lsl #1', 'bne #0x20'])
        b = V.structural(['ldr.w lr, [pc, #0x4c8]', 'add.w r0, lr, r0, lsl #1', 'bne #0x24'])
        self.assertEqual(a, b)
        self.assertNotEqual(V.structural(['bic r1, r1, #0x89']), V.structural(['bic r1, r1, #0x88']))

    def test_pool_order(self):
        self.assertEqual(V.pool_order(LISTING, 'put'), (['g_val', 'g_ring', 'g_buf'], ['g_val', 'g_ring', 'g_buf']))
        loads, pool = V.pool_order(LISTING, 'msg')
        self.assertEqual(loads, [])
        self.assertEqual(pool, ['+1819043176', '+111'])

    def test_split_listing(self):
        text = '\n    File Name: v0.o\nA\n    File Name: v1.o\nB\n'
        parts = V.split_listing(text)
        self.assertEqual(sorted(parts), ['v0.o', 'v1.o'])
        self.assertIn('B', parts['v1.o'])

    def test_target_scores_a_compile(self):
        b, pool = V.func_bytes(LISTING, 'put')
        t = V.Target(b, '--cpu Cortex-M3')
        self.assertEqual(t.score(b, pool), 0)
        other = b[:6] + bytes.fromhex('0388') + b[8:]                  # ldrh r3,[r0]: another register
        self.assertEqual(t.score(other, pool), 1)
        self.assertEqual(t.score(other, pool, regs=True), 0)
        self.assertIsNone(t.score(b'', set()))


class BatchTest(unittest.TestCase):
    def test_a_failing_file_does_not_lose_the_ones_after_it(self):
        work = tempfile.mkdtemp()
        r = FakeRunner()
        lst, log = V.compile_batch(r, ['int a;', 'ERROR', 'int c;', 'ERROR', 'int e;'], '--cpu Cortex-M3', work)
        self.assertEqual([x is not None for x in lst], [True, False, True, False, True])
        self.assertIn('Error', log)
        self.assertEqual(len([c for c in r.calls if c[0] == 'armcc']), 3)   # restarted after each failure

    def test_old_objects_are_cleared(self):
        work = tempfile.mkdtemp()
        wr(os.path.join(work, 'v0.o'), 'stale')
        lst, _ = V.compile_batch(FakeRunner(), ['ERROR'], '', work)
        self.assertEqual(lst, [None])

    def test_batch_scores(self):
        b, pool = V.func_bytes(LISTING, 'put')
        t = V.Target(b, '')
        batch = V.Batch(None, t, 'put', tempfile.mkdtemp(), runner=FakeRunner())
        scores, _ = batch.scores(['ok', 'ERROR'])
        self.assertEqual(scores, [0, None])

    def test_wine_options_name_host_paths(self):
        r = V.Runner.__new__(V.Runner)
        r.tc = types.SimpleNamespace(runner='wine'); r.root = '/proj'
        self.assertEqual(r.opts('-I/work/inc -O1'), ['-I/proj/inc', '-O1'])


if __name__ == '__main__':
    unittest.main()
