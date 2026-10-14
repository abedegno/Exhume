"""The readability tools on a small synthetic tree: names and their order, moving files and
functions, struct reconciliation and field names, source groups."""
import contextlib, io, os, sys, tempfile, types, unittest
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), 'tools'))
import armmap, armnames as N, armmovefile as MF, armmovefunc as MV, armstruct as S, armgroups as G
from armmap import Row


def rd(p):
    with open(p) as f: return f.read()


def wr(p, s):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, 'w') as f: f.write(s)


def tree(files, functions=(), data=()):
    """A project in a temporary directory, and a config naming its parts."""
    root = tempfile.mkdtemp()
    for p, s in files.items(): wr(os.path.join(root, p), s)
    cfg = types.SimpleNamespace(root=root, src=os.path.join(root, 'src'), include=os.path.join(root, 'src', 'include'),
                                map=os.path.join(root, 'map'), matched=os.path.join(root, 'matched.txt'),
                                build=os.path.join(root, 'build'), exclude=[os.path.join(root, 'src', 'lib')])
    os.makedirs(cfg.map, exist_ok=True)
    armmap.save(os.path.join(cfg.map, 'functions.tsv'), list(functions))
    armmap.save(os.path.join(cfg.map, 'data.tsv'), list(data))
    return cfg


def quiet(f, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()): return f(*a, **k)


FUNCS = [Row(0x08001000, 0x10, 'Alpha_Init', 'code', 'src/a.c'),
         Row(0x08001010, 0x10, 'sub_08001010', 'code', 'src/a.c'),
         Row(0x08001020, 0x10, 'Beta_Run', 'code', 'src/b.c'),
         Row(0x08001030, 0x10, 'sub_08001030', 'code', 'src/b.c'),
         Row(0x08001040, 0x10, 'main', 'code', 'src/b.c')]


class NamesTest(unittest.TestCase):
    def test_placeholders_sort_after_their_named_neighbour(self):
        ln = [n for _, n in N.link_names(FUNCS)]
        self.assertEqual(ln, ['Alpha_Init', 'Alpha_Init_08001010', 'Beta_Run', 'Beta_Run_08001030', 'main'])
        self.assertEqual(N.order_problems(FUNCS), [])

    def test_a_name_out_of_order_is_found(self):
        bad = [Row(r.addr, r.size, 'Zeta' if r.addr == 0x08001010 else r.name, r.kind, r.file) for r in FUNCS]
        probs = N.order_problems(bad)
        self.assertEqual([(a.name, b.name) for a, b in probs], [('Zeta', 'Beta_Run')])
        self.assertEqual(N.order_problems(bad, 0x08001020), [])          # outside the range checked

    def test_gaps(self):
        g = [(r.name, a, b) for r, a, b in N.gaps(FUNCS)]
        self.assertEqual(g, [('sub_08001010', 'Alpha_Init', 'Beta_Run'), ('sub_08001030', 'Beta_Run', 'main')])

    def test_rename_checks_order_and_rewrites_the_tree(self):
        cfg = tree({'src/a.c': 'void sub_08001010(void) {}\nvoid Alpha_Init(void) { sub_08001010(); }\n',
                    'src/include/a.h': 'void sub_08001010(void);\n'},
                   FUNCS, [Row(0x20000000, 4, 'g_20000000', 'rw', 'src/a.c')])
        self.assertIn('order', N.rename(cfg, [('sub_08001010', 'Gamma_Step', 'descriptive', '')]))
        self.assertIn('basis', N.rename(cfg, [('sub_08001010', 'Alpha_Step', 'guess', '')]))
        self.assertIn('already', N.rename(cfg, [('sub_08001010', 'main', 'likely', '')]))
        self.assertEqual(quiet(N.rename, cfg, [('sub_08001010', 'Alpha_Step', 'likely', 'steps it'),
                                               ('g_20000000', 'counter', 'descriptive', '')]), '')
        self.assertIn('void Alpha_Step(void) {}', rd(os.path.join(cfg.src, 'a.c')))
        self.assertIn('Alpha_Step', rd(os.path.join(cfg.include, 'a.h')))
        m = armmap.Map.from_dir(cfg.map)
        self.assertEqual(m.address('Alpha_Step'), 0x08001010)
        log = rd(os.path.join(cfg.map, 'names.tsv')).splitlines()
        self.assertEqual(log[1], '08001010\tfunction\tsub_08001010\tAlpha_Step\tlikely\tsteps it')
        self.assertEqual(log[2].split('\t')[:2], ['20000000', 'data'])


class MoveFileTest(unittest.TestCase):
    def test_move_keeps_the_records(self):
        cfg = tree({'src/core/u1.c': 'int a;\n', 'src/core/u2.c': 'int b;\n', 'docs/units.md': 'src/core/u1.c and src/core/u10.c\n'},
                   [Row(0x08001000, 4, 'f', 'code', 'src/core/u1.c')])
        wr(cfg.matched, 'src/core/u1.c\nsrc/core/u2.c\n')
        self.assertIn('stem', MF.move(cfg, [('src/core/u1.c', 'src/x/u2.c', '')]))
        self.assertIn('not under', MF.move(cfg, [('src/core/u1.c', 'other/u1.c', '')]))
        self.assertEqual(quiet(MF.move, cfg, [('src/core/u1.c', 'src/drivers/timer.c', 'its job')], ['docs/units.md']), '')
        self.assertTrue(os.path.exists(os.path.join(cfg.src, 'drivers', 'timer.c')))
        self.assertEqual(armmap.load(os.path.join(cfg.map, 'functions.tsv'))[0].file, 'src/drivers/timer.c')
        self.assertEqual(rd(cfg.matched), 'src/drivers/timer.c\nsrc/core/u2.c\n')
        self.assertEqual(rd(os.path.join(cfg.root, 'docs/units.md')), 'src/drivers/timer.c and src/core/u10.c\n')
        self.assertIn('src/core/u1.c\tsrc/drivers/timer.c\tits job', rd(os.path.join(cfg.map, 'filenames.tsv')))


A_C = '''/* opts: --cpu Cortex-M3 -O1 */
#include "types.h"
typedef struct { u8 n; } Ctx;
extern Ctx g_ctx;
u8 g_local = 1;
#define LIMIT 9

u8 helper(u8 x)
{
    return x < LIMIT ? x : LIMIT;
}

void caller(void)
{
    g_ctx.n = helper(g_local);
}
'''
B_C = '''/* opts: --cpu Cortex-M3 -O1 */
#include "types.h"
void Beta(void)
{
}
'''


class MoveFuncTest(unittest.TestCase):
    def setUp(self):
        self.cfg = tree({'src/a.c': A_C, 'src/b.c': B_C, 'src/include/types.h': 'typedef unsigned char u8;\n'},
                        [Row(0x08001000, 0x10, 'Beta', 'code', 'src/b.c'),
                         Row(0x08001010, 0x10, 'caller', 'code', 'src/a.c'),
                         Row(0x08001020, 0x10, 'helper', 'code', 'src/a.c')])

    def test_a_function_moves_with_what_it_needs(self):
        self.assertEqual(quiet(MV.move, self.cfg, ['caller'], 'src/b.c', True), '')
        b, a = rd(os.path.join(self.cfg.src, 'b.c')), rd(os.path.join(self.cfg.src, 'a.c'))
        self.assertIn('void caller(void)', b)
        self.assertIn('extern Ctx g_ctx;', b)
        self.assertIn('typedef struct { u8 n; } Ctx;', b)
        self.assertIn('extern u8 g_local;', b)                  # data stays with its file
        self.assertIn('u8 helper(u8 x);', b)                    # a prototype for the callee left behind
        self.assertNotIn('void caller(void)', a)
        self.assertIn('u8 g_local = 1;', a)
        rows = {r.name: r.file for r in armmap.load(os.path.join(self.cfg.map, 'functions.tsv'))}
        self.assertEqual(rows['caller'], 'src/b.c')

    def test_a_callee_from_another_file_goes_after_its_caller(self):
        quiet(MV.move, self.cfg, ['Beta'], 'src/a.c', True)
        # Beta came from another file and calls nothing; helper and caller keep their order
        a = rd(os.path.join(self.cfg.src, 'a.c'))
        self.assertLess(a.index('u8 helper(u8 x)\n{'), a.index('void caller(void)'))
        self.assertFalse(os.path.exists(os.path.join(self.cfg.src, 'b.c')))      # left empty: removed

    def test_reorder_puts_a_cross_file_callee_after_its_caller(self):
        text = 'void callee(void)\n{\n}\n\nvoid user(void)\n{\n    callee();\n}\n'
        out = MV.reorder(text, [], {'callee': 'src/x.c', 'user': 'src/y.c'})
        self.assertLess(out.index('void user(void)\n{'), out.index('void callee(void)\n{'))
        self.assertIn('void callee(void);', out[:out.index('void user(void)\n{')])
        same = MV.reorder(text, [], {'callee': 'src/x.c', 'user': 'src/x.c'})
        self.assertEqual(same, text)

    def test_options_must_agree(self):
        wr(os.path.join(self.cfg.src, 'b.c'), B_C.replace('-O1', '-O1 --no_data_reorder'))
        self.assertIn('options must agree', quiet(MV.move, self.cfg, ['caller'], 'src/b.c', False))

    def test_a_new_file_takes_the_options_and_includes(self):
        wr(os.path.join(self.cfg.src, 'local.h'), '')
        wr(os.path.join(self.cfg.src, 'a.c'), A_C.replace('#include "types.h"', '#include "local.h"'))
        quiet(MV.move, self.cfg, ['caller'], 'src/new/c.c', True)
        text = rd(os.path.join(self.cfg.src, 'new', 'c.c'))
        self.assertTrue(text.startswith('/* opts: --cpu Cortex-M3 -O1 */\n'))
        self.assertIn('#include "../local.h"', text)

    def test_a_static_is_refused(self):
        wr(os.path.join(self.cfg.src, 'a.c'), A_C.replace('u8 g_local = 1;', 'static u8 g_local = 1;'))
        self.assertIn('static', quiet(MV.move, self.cfg, ['caller'], 'src/b.c', False))


TYPES_H = 'typedef unsigned char u8;\ntypedef unsigned short u16;\ntypedef signed short s16;\ntypedef unsigned int u32;\n'


class StructTest(unittest.TestCase):
    def test_arm_layout(self):
        p = S.Parser(TYPES_H + '''
typedef struct { u8 a; u16 b; u32 c; } Plain;
typedef __packed struct { u8 a; u16 b; u32 c; } Packed;
typedef struct { u8 a; u16 x:3, y:5; u16 z:12; } Bits;
typedef union { u16 w; u8 b[2]; } Word;
''')
        self.assertEqual([(n, o) for n, o, *_ in p.types['Plain'].fields], [('a', 0), ('b', 2), ('c', 4)])
        self.assertEqual(p.types['Plain'].size, 8)
        self.assertEqual([(n, o) for n, o, *_ in p.types['Packed'].fields], [('a', 0), ('b', 1), ('c', 3)])
        self.assertEqual((p.types['Packed'].size, p.types['Packed'].align), (7, 1))
        bits = {n: (o, bp, w) for n, o, _, bp, w in p.types['Bits'].fields}
        self.assertEqual(bits['x'], (0, 8, 3))           # the u16 container at 0 still has room at bit 8
        self.assertEqual(bits['y'], (0, 11, 5))
        self.assertEqual(bits['z'], (2, 0, 12))          # no room left: the next u16 container
        self.assertEqual(p.types['Word'].size, 2)

    def setUp(self):
        self.cfg = tree({
            'src/include/types.h': TYPES_H,
            'src/one.c': '#include "types.h"\ntypedef struct { u8 p0[4]; u16 f04; u8 p6[2]; } V1;\nextern V1 g_state;\n'
                         'void f(void) { g_state.f04 = 1; }\n',
            'src/two.c': '#include "types.h"\ntypedef struct { u8 f00; u8 p1[5]; s16 f06; } V2;\nextern V2 g_state;\n'
                         'void g(void) { g_state.f00 = g_state.f06; }\n',
            'src/three.c': '#include "types.h"\ntypedef struct { u8 f00; u8 p1[3]; u16 len; u8 p6[2]; } V3;\nV3 g_state;\n',
            'src/lib/vendor.c': '#include "types.h"\nextern u8 g_state[8];\n',
        })
        self.t = S.Tree(self.cfg)

    def test_survey_and_report(self):
        self.assertEqual(self.t.survey(), [('g_state', 3)])                # src/lib is excluded
        rep = '\n'.join(S.report(self.t, 'g_state'))
        self.assertIn('0x004 size 2 u: f04, len', rep)
        self.assertIn('0x000 size 1 u: f00', rep)
        self.assertIn('src/three.c: V3 size 8 (definition)', rep)

    def test_draft(self):
        d = S.draft(self.t, 'g_state', 'State')
        self.assertIn('u8 f00;', d); self.assertIn('u16 f04;', d); self.assertIn('s16 f06;', d)
        self.assertIn('} State;   /* 8 bytes */', d)

    def test_convert(self):
        wr(os.path.join(self.cfg.include, 'state.h'), '#include "types.h"\n'
           'typedef struct {\n    u8 mode;\n    u8 p1[3];\n    u16 len;\n    s16 temp;\n} State;\nextern State g_state;\n')
        done, skipped = S.convert(self.t, 'g_state', 'state.h', 'State', write=True)
        self.assertEqual(sorted(f for f, _ in done), ['src/one.c', 'src/two.c'])
        one, two = rd(os.path.join(self.cfg.src, 'one.c')), rd(os.path.join(self.cfg.src, 'two.c'))
        self.assertIn('g_state.len = 1;', one)
        self.assertIn('#include "include/state.h"', one)
        self.assertNotIn('typedef struct', one)
        self.assertIn('g_state.mode = g_state.temp;', two)

    def test_convert_refuses_another_shape(self):
        wr(os.path.join(self.cfg.include, 'state.h'), '#include "types.h"\n'
           'typedef struct { u8 mode; u8 p1[3]; u16 len; u16 temp; } State;\n')
        done, skipped = S.convert(self.t, 'g_state', 'state.h', 'State')
        self.assertIn('src/two.c', [f for f, _ in skipped])                # s16 against u16: other code

    def test_field_names(self):
        wr(os.path.join(self.cfg.include, 'state.h'), '#include "types.h"\n'
           'typedef struct {\n    u8 f00;\n    u8 p1[3];\n    u16 f04;\n} State;\nextern State g_state;\n')
        wr(os.path.join(self.cfg.src, 'four.c'), '#include "include/state.h"\n'
           'void h(State *p) { g_state.f04 = 2; p->f00 = 1; }\n')
        changed, problems = S.rename_fields(self.t, {'State': [('f04', 'length', 'bytes queued'), ('f00', 'mode', '')]}, True)
        self.assertEqual(problems, [])
        self.assertIn('    u16 length;   /* bytes queued */', rd(os.path.join(self.cfg.include, 'state.h')))
        self.assertIn('g_state.length = 2; p->mode = 1;', rd(os.path.join(self.cfg.src, 'four.c')))
        self.assertIn('g_state.f04 = 1', rd(os.path.join(self.cfg.src, 'one.c')))    # its own view: left alone


class GroupsTest(unittest.TestCase):
    def test_shared_definitions_join_files(self):
        texts = {'a.c': 'u8 g_x;\nvoid f(void) {}\n', 'b.c': 'void f(void) {}\nvoid g(void) {}\n',
                 'c.c': 'u8 g_x = 1;\n', 'd.c': 'static void f(void) {}\nvoid h(void) {}\n'}
        gs = G.groups(texts)
        self.assertEqual(gs, [(['a.c', 'b.c', 'c.c'], ['f', 'g_x'])])


if __name__ == '__main__':
    unittest.main()
