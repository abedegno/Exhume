"""The map: parsing functions.tsv and data.tsv, resolving names (run addresses for ramfuncs,
names that spell their address, aliases), and the wrong-name trap. No toolchain needed."""
import os, tempfile, unittest
from . import support  # noqa: F401
import c28map

FUNCS = '''addr\tsize\tname\tkind\tnotes
080000\t136\tXF$$OP\trts\t
080088\t86\tlib_entry\trts\t
08016A\t2\tlib_stop\trts\talias=XS$$STOP
080193\t39\tloop_step\tc\t
080279\t15\tfast_sum\tramfunc\trun=0x00B000
080290\t6\tsub_080290\tramfunc\trun=0x00B00F
081FF0\t2\treset_branch\tasm\tsection=resetvec
'''
DATA = '''addr\tsize\tname\tkind\tnotes
# a comment line
00A400\t1\tmode\tvar\t
00A402\t2\tflags\tvar\t
0802C2\t24\tsw_ctl_mode\tswitch\t
'''


class MapTest(unittest.TestCase):
    def setUp(self):
        self.m = c28map.Map(c28map.parse_tsv(FUNCS), c28map.parse_tsv(DATA))

    def test_rows(self):
        self.assertEqual(len(self.m.functions), 7)
        self.assertEqual(len(self.m.data), 3)
        r = self.m.frows['fast_sum']
        self.assertEqual((r.addr, r.size, r.kind), (0x080279, 15, 'ramfunc'))
        self.assertEqual(c28map.note(r, 'run'), '0x00B000')

    def test_resolve(self):
        m = self.m
        self.assertEqual(m.resolve('_loop_step'), 0x080193)
        self.assertEqual(m.resolve('XF$$OP'), 0x080000)
        self.assertEqual(m.resolve('XS$$STOP'), 0x08016A)            # an alias
        self.assertEqual(m.resolve('_mode'), 0x00A400)
        self.assertEqual(m.resolve('_var_A410'), 0x00A410)          # spells its address
        self.assertEqual(m.resolve('_sub_080400'), 0x080400)
        self.assertEqual(m.resolve('0x80'), 0x80)
        self.assertIsNone(m.resolve('_unknown'))

    def test_ramfunc_resolves_to_its_run_address(self):
        self.assertEqual(self.m.resolve('_fast_sum'), 0x00B000)
        self.assertEqual(self.m.resolve('_sub_080290'), 0x00B00F)  # a placeholder that is a map row

    def test_the_wrong_name_trap(self):
        # renamed in the map, but a source still calls the old placeholder: it resolves to the
        # load address it spells, and the call's words come out wrong
        funcs = FUNCS.replace('sub_080290', 'fast_scale')
        m = c28map.Map(c28map.parse_tsv(funcs), [])
        self.assertEqual(m.resolve('_fast_scale'), 0x00B00F)
        self.assertEqual(m.resolve('_sub_080290'), 0x080290)

    def test_section_row_and_owners(self):
        self.assertEqual(self.m.section_row('resetvec').name, 'reset_branch')
        self.assertIsNone(self.m.section_row('nosuch'))
        own = self.m.owners()
        self.assertEqual(own[0x080194].name, 'loop_step')
        self.assertEqual(own[0x00A403].name, 'flags')
        self.assertNotIn(0x00A404, own)

    def test_round_trip(self):
        d = tempfile.mkdtemp()
        c28map.write_tsv(os.path.join(d, 'functions.tsv'), self.m.functions)
        c28map.write_tsv(os.path.join(d, 'data.tsv'), self.m.data)
        m2 = c28map.Map.from_dir(d)
        self.assertEqual([(r.addr, r.size, r.name, r.kind, r.notes) for r in m2.functions],
                         [(r.addr, r.size, r.name, r.kind, r.notes) for r in self.m.functions])


if __name__ == '__main__':
    unittest.main()
