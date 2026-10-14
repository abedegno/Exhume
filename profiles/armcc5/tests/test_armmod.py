"""The modded build: the allowed-diff checker on synthetic words, and a mod of the test firmware."""
import os, shutil, sys, tempfile, textwrap, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.dirname(HERE)
EXHUME = os.path.dirname(os.path.dirname(PROFILE))
sys.path.insert(0, os.path.join(PROFILE, 'tools')); sys.path.insert(0, os.path.join(PROFILE, 'testfw'))
sys.path.insert(0, os.path.join(EXHUME, 'tools')); sys.path.insert(0, HERE)
import armmod
from test_armgate import HOME, TOOLCHAIN, make_project, pieces_of

BASE = 0x08000000
SYMS = {'Reset_Handler': (0x08000100, True), 'mod_reset': (0x08010000, True),
        'g_table': (0x20000100, False), 'mod_table': (0x20008000, False)}


def resolve(name): return SYMS[name]


resolve.size = lambda name: 0x40


def words(*ws):
    return b''.join(w.to_bytes(4, 'little') for w in ws)


class CheckTest(unittest.TestCase):
    def test_identical_needs_no_rules(self):
        ok, lines = armmod.check_allowed(words(1, 2, 3), words(1, 2, 3), BASE, [], resolve)
        self.assertTrue(ok, lines)

    def test_a_vector_moved_to_new_code(self):
        rules = [{'kind': 'word', 'from': 'Reset_Handler', 'to': 'mod_reset', 'count': 1}]
        ok, lines = armmod.check_allowed(words(0x20002000, 0x08000101), words(0x20002000, 0x08010001), BASE, rules, resolve)
        self.assertTrue(ok, lines)

    def test_a_count_that_is_not_met_fails(self):
        rules = [{'kind': 'word', 'from': 'Reset_Handler', 'to': 'mod_reset', 'count': 2}]
        ok, lines = armmod.check_allowed(words(0x08000101), words(0x08010001), BASE, rules, resolve)
        self.assertFalse(ok)
        self.assertIn('1 found, want 2', '\n'.join(lines))

    def test_pool_entries_move_to_the_same_offset_in_the_new_table(self):
        rules = [{'kind': 'pool', 'from': 'g_table', 'to': 'mod_table'}]
        ok, lines = armmod.check_allowed(words(0x20000100, 0x20000124), words(0x20008000, 0x20008024), BASE, rules, resolve)
        self.assertTrue(ok, lines)
        ok, lines = armmod.check_allowed(words(0x20000124), words(0x20008020), BASE, rules, resolve)
        self.assertFalse(ok, lines)

    def test_a_thumb_immediate(self):
        rules = [{'kind': 'imm', 'insn': 'cmp', 'old': 7, 'new': 8, 'count': 1}]
        old = (0x2907 | (0xBF00 << 16))              # cmp r1, #7 ; nop
        ok, lines = armmod.check_allowed(words(old), words(old + 1), BASE, rules, resolve)
        self.assertTrue(ok, lines)
        # the same change in another register's compare is still a cmp #7 -> #8
        ok, lines = armmod.check_allowed(words(0x2A07), words(0x2A08), BASE, rules, resolve)
        self.assertTrue(ok, lines)
        # a different instruction is not
        ok, lines = armmod.check_allowed(words(0x2107), words(0x2108), BASE, rules, resolve)
        self.assertFalse(ok, lines)

    def test_an_unexplained_change_fails(self):
        ok, lines = armmod.check_allowed(words(1, 2), words(1, 3), BASE, [], resolve)
        self.assertFalse(ok)
        self.assertIn('0x08000004: 00000002 -> 00000003', '\n'.join(lines))

    def test_a_range_allows_anything_including_a_new_length(self):
        rules = [{'kind': 'range', 'start': '0x08000004', 'end': '0x08000010'}]
        ok, lines = armmod.check_allowed(words(1, 2), words(1, 5, 6), BASE, rules, resolve)
        self.assertTrue(ok, lines)

    def test_replace_rules_check_their_count(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, 'a.c'), 'w') as f: f.write('x x y\n')
        armmod.apply_edits(d, {'replace': [{'file': 'a.c', 'old': 'x', 'new': 'z', 'count': 2}]}, {'root': d}, lambda s: None)
        with open(os.path.join(d, 'a.c')) as f: self.assertEqual(f.read(), 'z z y\n')
        with self.assertRaises(SystemExit):
            armmod.apply_edits(d, {'replace': [{'file': 'a.c', 'old': 'y', 'new': 'w', 'count': 2}]}, {'root': d}, lambda s: None)


MOD_C = '''\
/* a mod: a fault handler in free flash that calls the original's crc16 */
extern unsigned short crc16(const unsigned char *p, unsigned int n);
unsigned short mod_last;
void mod_fault(void)
{
    static const unsigned char m[2] = { 1, 2 };
    mod_last = crc16(m, 2);
    for (;;) ;
}
'''

MOD_TOML = '''
[mod]
sources = ["mod/fault.c"]
code = "0x08001000"
ram = "0x20002000"
[[mod.replace]]
file = "src/vectors.s"
old = "        EXPORT  __Vectors\\n"
new = "        EXPORT  __Vectors\\n        IMPORT  mod_fault\\n"
[[mod.replace]]
file = "src/vectors.s"
old = "DCD     Fault_Handler"
new = "DCD     mod_fault"
count = 2
[[mod.allow]]
kind = "word"
from = "Fault_Handler"
to = "mod_fault"
count = 2
'''


@unittest.skipUnless(TOOLCHAIN, 'needs EXHUME_ARMCC_HOME and Docker (or EXHUME_ARMCC_RUNNER=wine)')
class ModTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import armtc, answerkey, config
        cls.root = tempfile.mkdtemp(dir=os.path.expanduser('~'), prefix='exhume-mod-')
        cls.tc = armtc.Toolchain(HOME, cls.root)
        cls.key = answerkey.build(cls.tc, cls.root)
        make_project(cls.root, cls.key, pieces_of(cls.key), MOD_TOML)
        os.makedirs(os.path.join(cls.root, 'mod'))
        with open(os.path.join(cls.root, 'mod', 'fault.c'), 'w') as f: f.write(MOD_C)
        cls.cfg = config.Config(os.path.join(cls.root, 'exhume.toml'))
        lines = []
        cls.res = armmod.build(cls.cfg, cls.cfg.file, jobs=2, log=lines.append)
        cls.out = '\n'.join(lines)

    @classmethod
    def tearDownClass(cls):
        cls.tc.stop()
        shutil.rmtree(cls.root, ignore_errors=True)

    def test_only_the_vectors_changed(self):
        self.assertTrue(self.res.ok, self.out)
        self.assertIn('2 x word', self.out)

    def test_the_vectors_point_at_the_new_code(self):
        import ehex
        base, data = ehex.read(self.res.out)
        fault = self.res.resolve('mod_fault')[0] | 1
        self.assertEqual(int.from_bytes(data[8:12], 'little'), fault)
        self.assertGreaterEqual(fault, 0x08001000)

    def test_without_the_rule_the_change_is_caught(self):
        ok, lines = armmod.check_allowed(self.res.orig, self.res.new, self.res.base, [], self.res.resolve)
        self.assertFalse(ok)
        self.assertEqual(sum(1 for l in lines if l.startswith('  0x')), 2, lines)


if __name__ == '__main__':
    unittest.main()
