"""Unit tests for gotypes.py (type declarations from runtime type information) and goskel.py (the
skeleton source tree).

The fixture module `shop` is built with the host's Go for linux/amd64 (with -trimpath, so file
names read shop/cart/cart.go) into a temporary directory, as the command cmd/kiosk, which uses
every package of the fixture. The skeleton is written to another temporary directory, given a
go.mod, and built there with the host's Go.
"""
import contextlib
import io
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.dirname(TESTS_DIR)
FIXTURE = os.path.join(TESTS_DIR, 'fixtures', 'shop')
TOOLS = os.path.join(PROFILE, 'tools')
sys.path.insert(0, TOOLS)
sys.path.insert(0, TESTS_DIR)

import gobin
import goskel
import gotypes
from test_gobin import go_env


def slurp(path):
    with open(path) as f:
        return f.read()


class Skeleton(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('go'):
            raise unittest.SkipTest('go is not on the PATH')
        cls.tmp = tempfile.mkdtemp(prefix='goskel-test-')
        cls.ref = os.path.join(cls.tmp, 'kiosk')
        r = subprocess.run(['go', 'build', '-trimpath', '-buildvcs=false', '-o', cls.ref,
                            './cmd/kiosk'], cwd=FIXTURE, env=go_env(CGO_ENABLED='0'),
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError('go build failed:\n' + r.stdout + r.stderr)
        cls.bin = gobin.load(cls.ref)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.out = tempfile.mkdtemp(prefix='out-', dir=self.tmp)
        with open(os.path.join(self.out, 'go.mod'), 'w') as f:
            f.write('module shop\n\ngo 1.21\n')

    # Helpers

    def go_build(self, out):
        return subprocess.run(['go', 'build', '-buildvcs=false', '-o', os.devnull, './...'],
                              cwd=out, env=go_env(CGO_ENABLED='0'), capture_output=True,
                              text=True)

    def read(self, out, rel):
        with open(os.path.join(out, rel)) as f:
            return f.read()

    def files(self, out):
        for d, _, names in os.walk(out):
            for n in sorted(names):
                if n.endswith('.go'):
                    yield os.path.join(d, n)

    def line_of(self, out, text):
        """The line, in whichever generated file holds it, that starts with text."""
        for path in self.files(out):
            with open(path) as f:
                for n, line in enumerate(f, 1):
                    if line.startswith(text):
                        return n
        self.fail(text + ' is not in the skeleton')

    def ref_start_line(self, name):
        return self.bin.func(name).start_line

    def put_body(self, out, func, body):
        for path in self.files(out):
            with open(path) as f:
                lines = f.read().split('\n')
            for n, line in enumerate(lines):
                if line.startswith('func %s(' % func):
                    # Claiming a stub: write the body and drop the generator's marker.
                    lines[n] = line.replace('panic("todo")', body).split(' // skel:stub')[0]
                    with open(path, 'w') as f:
                        f.write('\n'.join(lines))
                    return
        self.fail('func %s is not in the skeleton' % func)

    # The brief's tests

    def test_skeleton_builds(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertEqual(self.go_build(self.out).returncode, 0)

    def test_functions_sit_on_their_lines(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertEqual(self.line_of(self.out, 'func Total'), self.ref_start_line('shop/cart.Total'))

    def test_struct_layout_is_kept(self):
        self.assertIn('type Item struct {\n\tName  string\n\tPence int', gotypes.declare(self.ref, r'^shop/cart$'))

    def test_existing_bodies_survive(self):
        goskel.write(self.ref, self.out, prefix='shop/'); self.put_body(self.out, 'Total', 'return 1')
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertIn('return 1', self.read(self.out, 'cart/cart.go'))

    # Types

    def test_embedded_fields_and_tags(self):
        decl = gotypes.declare(self.ref, r'^shop/shelf$')
        shelf = decl[decl.index('type Shelf struct {'):]
        shelf = shelf[:shelf.index('\n}\n')]
        lines = [l.strip() for l in shelf.split('\n')[1:]]
        self.assertEqual(lines[:2], ['Base', 'sync.Mutex'])
        self.assertEqual(re.sub(r'\s+', ' ', lines[2]), 'Items []cart.Item `json:"items"`')
        self.assertEqual(re.sub(r'\s+', ' ', lines[3]), 'labels Labels `json:"-"`')
        self.assertIn('byName map[string]*cart.Item', [re.sub(r'\s+', ' ', l) for l in lines])
        self.assertIn('Done chan struct{}', [re.sub(r'\s+', ' ', l) for l in lines])
        self.assertIn('OnAdd func(cart.Item) error', [re.sub(r'\s+', ' ', l) for l in lines])

    def test_interface_and_named_types(self):
        decl = gotypes.declare(self.ref, r'^shop/shelf$')
        self.assertIn('type Stocker interface {\n\tLabel() string\n\tStock(string) (int, bool)\n}',
                      decl)
        self.assertIn('type Bins []int\n', decl)
        self.assertIn('type Labels map[string]string\n', decl)

    def test_field_offsets(self):
        t = gotypes.load(self.bin).named('shop/cart', 'Item')
        self.assertEqual([(f.name, f.offset) for f in t.fields],
                         [('Name', 0), ('Pence', 16), ('Qty', 24)])

    # The skeleton

    def test_method_signatures_from_method_tables(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        src = self.read(self.out, 'shelf/shelf.go').split('\n')
        line = self.ref_start_line('shop/shelf.(*Shelf).Stock')
        self.assertEqual(src[line - 1], 'func (*Shelf) Stock(string) (int, bool) { panic("todo") } // skel:stub')
        line = self.ref_start_line('shop/shelf.Base.Label')
        self.assertEqual(src[line - 1], 'func (Base) Label() string { panic("todo") } // skel:stub')

    def test_parameters_guessed_from_the_argument_layout(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        src = self.read(self.out, 'shelf/shelf.go').split('\n')
        self.assertEqual(src[self.ref_start_line('shop/shelf.NewShelf') - 1],
                         'func NewShelf(int, string) { panic("todo") } // skel:stub signature guessed')
        self.assertEqual(src[self.ref_start_line('shop/shelf.Report') - 1],
                         'func Report(any) (int, int) { panic("todo") } // skel:stub signature '
                         'guessed')
        self.assertIn('shop/shelf.Report\tshelf/shelf.go:', self.read(self.out, 'UNKNOWN.txt'))

    def test_results_recovered_from_call_sites(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        src = self.read(self.out, 'cart/shapes.go').split('\n')
        line = lambda name: src[self.ref_start_line('shop/cart.' + name) - 1]
        self.assertEqual(line('Parse'), 'func Parse(string) (int, int, int) { panic("todo") } '
                         '// skel:stub signature guessed')
        self.assertEqual(line('Square'), 'func Square(int) int { panic("todo") } '
                         '// skel:stub signature guessed')
        self.assertEqual(line('Tick'), 'func Tick() { panic("todo") } '
                         '// skel:stub signature guessed')
        self.assertEqual(line('Triple'), 'func Triple(int) { panic("todo") } '
                         '// skel:stub signature guessed')
        unknown = self.read(self.out, 'UNKNOWN.txt')
        self.assertRegex(unknown, r'shop/cart\.Parse\tcart/shapes\.go:\d+\tsignature guessed: '
                         r'parameters from the argument layout, results from 1 call sites\n')
        self.assertRegex(unknown, r'shop/cart\.Triple\tcart/shapes\.go:\d+\tsignature guessed: '
                         r'parameters from the argument layout, results unknown\n')
        self.assertEqual(self.go_build(self.out).returncode, 0)

    def test_compiler_generated_functions_are_not_emitted(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        src = self.read(self.out, 'shelf/shelf.go')
        self.assertEqual(src.count('func First['), 1)
        first = next(f for f in self.bin.funcs() if f.name.startswith('shop/shelf.First['))
        self.assertTrue(src.split('\n')[first.start_line - 1].startswith('func First[T1, T2 any]('))
        self.assertNotIn('func1', src)
        self.assertNotIn('deferwrap', src)

    def test_types_go_in_types_recovered(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        types = self.read(self.out, 'shelf/types_recovered.go')
        self.assertIn('type Shelf struct {', types)
        self.assertNotIn('type Shelf ', self.read(self.out, 'shelf/shelf.go'))

    def test_unknown_signatures_are_marked_and_listed(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        unknown = self.read(self.out, 'UNKNOWN.txt')
        self.assertIn('shop/shelf.First', unknown)
        src = self.read(self.out, 'shelf/shelf.go')
        first = next(l for l in src.split('\n') if l.startswith('func First['))
        self.assertTrue(first.endswith('// skel:stub signature unknown'))

    def test_existing_declarations_are_not_repeated(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        with open(os.path.join(self.out, 'shelf', 'extra.go'), 'w') as f:
            f.write('package shelf\n\ntype Bins []int\n')
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertNotIn('type Bins ', self.read(self.out, 'shelf/types_recovered.go'))
        self.assertEqual(self.go_build(self.out).returncode, 0)

    # Keep files

    def build_with(self, out, tags=''):
        exe = os.path.join(out, 'kiosk.bin')
        cmd = ['go', 'build', '-trimpath', '-buildvcs=false', '-o', exe]
        if tags:
            cmd.append('-tags=' + tags)
        r = subprocess.run(cmd + ['./cmd/kiosk'], cwd=out, env=go_env(CGO_ENABLED='0'),
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return gobin.load(exe)

    def keep_files(self, out):
        return [p for p in self.files(out) if os.path.basename(p).startswith('zz_keep_')]

    def test_keep_files_build_with_and_without_the_tag(self):
        goskel.write(self.ref, self.out, prefix='shop/', keep='keepall')
        names = {os.path.relpath(p, self.out) for p in self.keep_files(self.out)}
        self.assertEqual(names, {'cart/zz_keep_cart.go', 'shelf/zz_keep_shelf.go',
                                 'tally/zz_keep_tally.go', 'cmd/kiosk/zz_keep_main.go'})
        self.assertTrue(self.read(self.out, 'cart/zz_keep_cart.go').startswith(
            goskel.KEEP_NOTE + '\n\n//go:build keepall\n'))
        shelf = self.read(self.out, 'shelf/zz_keep_shelf.go')
        for ref in ('NewShelf,', '(*Shelf).Stock,', 'Base.Label,', 'Count,'):
            self.assertIn('\t' + ref + '\n', shelf)
        self.assertNotIn('First', shelf)
        self.assertIn('shop/shelf.First', self.read(self.out, 'UNKNOWN.txt'))
        self.build_with(self.out)
        self.build_with(self.out, 'keepall')

    def test_keep_tag_links_functions_nothing_calls(self):
        goskel.write(self.ref, self.out, prefix='shop/', keep='keepall')
        self.put_body(self.out, 'Total', 'println()')
        goskel.write(self.ref, self.out, prefix='shop/', keep='keepall')
        self.assertIn('\tTotal,\n', self.read(self.out, 'cart/zz_keep_cart.go'))
        without = {f.name for f in self.build_with(self.out).funcs()}
        self.assertNotIn('shop/cart.Total', without)
        linked = {f.name for f in self.build_with(self.out, 'keepall').funcs()}
        self.assertTrue({'shop/cart.Total', 'shop/shelf.(*Shelf).Add',
                         'shop/shelf.Base.Label'} <= linked)

    def test_no_keep_files_without_the_option(self):
        goskel.write(self.ref, self.out, prefix='shop/', keep='keepall')
        before = self.read(self.out, 'shelf/shelf.go')
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertEqual(self.keep_files(self.out), [])
        self.assertEqual(self.read(self.out, 'shelf/shelf.go'), before)

    # Ownership: only marker lines are the generator's

    def lines_of(self, rel):
        return self.read(self.out, rel).split('\n')

    def put(self, rel, text):
        with open(os.path.join(self.out, rel), 'w') as f:
            f.write(text)

    def test_unmarked_stub_is_hand_written(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        lines = self.lines_of('cart/cart.go')
        n = self.ref_start_line('shop/cart.Total')
        lines[n - 2] = '// Total is mine now.'
        lines[n - 1] = 'func Total(items []Item) int { panic("todo") }'
        self.put('cart/cart.go', '\n'.join(lines))
        before = self.read(self.out, 'cart/cart.go')
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertEqual(self.read(self.out, 'cart/cart.go'), before)

    def test_existing_file_is_edited_in_place(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        lines = self.lines_of('shelf/shelf.go')
        n = self.ref_start_line('shop/shelf.Report')      # the lines after it are free
        lines[n] = '// a loose note'
        lines[n + 2] = 'func helper() int { return 2 } // not in the binary'
        lines[n + 4] = 'func Gone() { panic("todo") } // skel:stub'
        lines[-1:] = ['', '', '// a note at the end', '']
        self.put('shelf/shelf.go', '\n'.join(lines))
        goskel.write(self.ref, self.out, prefix='shop/')
        lines[n + 4] = ''                   # the only change: a stub the binary does not have
        self.assertEqual(self.lines_of('shelf/shelf.go'), lines)

    def test_types_file_with_a_hand_written_declaration_stops_the_run(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        with open(os.path.join(self.out, 'shelf', 'types_recovered.go'), 'a') as f:
            f.write('\ntype Extra int\n')
        os.remove(os.path.join(self.out, 'cart', 'cart.go'))
        with self.assertRaises(goskel.Refused) as cm:
            goskel.write(self.ref, self.out, prefix='shop/')
        self.assertIn('Extra', str(cm.exception))
        self.assertFalse(os.path.exists(os.path.join(self.out, 'cart', 'cart.go')))
        self.assertIn('type Extra int', self.read(self.out, 'shelf/types_recovered.go'))

    def test_keep_files_respect_build_constraints(self):
        goskel.write(self.ref, self.out, prefix='shop/', keep='keepall')
        self.put('shelf/only_linux.go', 'package shelf\n\nfunc onLinux() int { return 1 }\n')
        self.put('shelf/tagged.go', '//go:build sometag\n\npackage shelf\n\n'
                                    'func tagged() int { return 2 }\n')
        goskel.write(self.ref, self.out, prefix='shop/', keep='keepall')
        keeps = {os.path.basename(p): slurp(p) for p in self.keep_files(self.out)}
        linux = [n for n, text in keeps.items() if '\tonLinux,' in text]
        self.assertEqual(len(linux), 1)
        self.assertTrue(linux[0].endswith('_linux.go'))
        tagged = [text for text in keeps.values() if '\ttagged,' in text]
        self.assertEqual(len(tagged), 1)
        self.assertIn('//go:build keepall && (sometag)\n', tagged[0])
        for tags in ('', 'keepall', 'keepall,sometag'):
            self.build_with(self.out, tags)

    def test_hand_written_keep_file_is_left_alone(self):
        self.put('go.mod', 'module shop\n\ngo 1.21\n')
        os.makedirs(os.path.join(self.out, 'shelf'))
        self.put('shelf/zz_keep_mine.go', 'package shelf\n\nvar mine = 1\n')
        goskel.write(self.ref, self.out, prefix='shop/', keep='keepall')
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertEqual(self.read(self.out, 'shelf/zz_keep_mine.go'),
                         'package shelf\n\nvar mine = 1\n')

    def test_hand_declared_generic_type_suppresses_the_placeholder(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        self.put('shelf/pair.go', 'package shelf\n\ntype Pair[K comparable, V any] struct {\n'
                                  '\tKey K\n\tVal V\n}\n')
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertNotIn('type Pair[', self.read(self.out, 'shelf/types_recovered.go'))
        self.assertEqual(self.go_build(self.out).returncode, 0)

    def claim_add(self, body):
        """Claim shelf's one stub that uses the cart package, writing body in its place."""
        lines = self.lines_of('shelf/shelf.go')
        k = [n for n, l in enumerate(lines) if l.startswith('func (*Shelf) Add(')][0]
        lines[k] = 'func (*Shelf) Add(it cart.Item) error { %s }' % body
        self.put('shelf/shelf.go', '\n'.join(lines))

    def test_import_used_by_claimed_code_survives(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        before = self.lines_of('shelf/shelf.go')[1]
        self.assertEqual(before, 'import "shop/cart" // skel:import')
        self.claim_add('var c cart.Item; _ = c; return nil')
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertEqual(self.lines_of('shelf/shelf.go')[1], before)
        r = self.go_build(self.out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_one_line_import_has_no_name(self):
        src = goskel.Source('package shelf\nimport "shop/cart" // skel:import\nimport c2 "shop/cart"\n')
        self.assertEqual(src.imports(), [(None, 'shop/cart'), ('c2', 'shop/cart')])

    def test_import_nothing_uses_is_removed(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        self.claim_add('return nil')
        self.put('shelf/shelf.go', self.read(self.out, 'shelf/shelf.go').replace(
            'func (*Shelf) Add(it cart.Item)', 'func (*Shelf) Add(it int)'))
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertNotIn('skel:import', self.read(self.out, 'shelf/shelf.go'))

    def test_adopting_unmarked_stubs_of_an_earlier_run(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        fresh = {p: slurp(p) for p in self.files(self.out)}
        for p, text in fresh.items():       # as written before the markers existed
            text = re.sub(r' // skel:(stub|import)[^\n]*', '', text)
            with open(p, 'w') as f:
                f.write(text.replace('// skel:type\n', ''))
        self.put('cart/hand.go', 'package cart\n\n// Hand is mine.\nfunc Hand() int { return 3 }\n')
        with self.assertRaises(goskel.Refused):
            goskel.write(self.ref, self.out, prefix='shop/')
        goskel.write(self.ref, self.out, prefix='shop/', adopt=True)
        self.assertEqual({p: slurp(p) for p in self.files(self.out) if p in fresh}, fresh)
        self.assertEqual(self.read(self.out, 'cart/hand.go'),
                         'package cart\n\n// Hand is mine.\nfunc Hand() int { return 3 }\n')

    def raw(self, rel):
        with open(os.path.join(self.out, rel), newline='') as f:
            return f.read()

    def put_raw(self, rel, text):
        with open(os.path.join(self.out, rel), 'w', newline='') as f:
            f.write(text)

    def test_line_endings_are_kept(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        lines = self.lines_of('cart/cart.go')
        n = self.ref_start_line('shop/cart.Total')
        lines[n - 1] = 'func Total() { panic("todo") } // skel:stub'      # out of date
        self.put_raw('cart/cart.go', '\r\n'.join(lines))
        text = self.raw('cart/types_recovered.go')            # an untouched file
        shelf = self.raw('shelf/shelf.go').rstrip('\n')
        self.put_raw('shelf/shelf.go', shelf)                 # no final newline
        goskel.write(self.ref, self.out, prefix='shop/')
        cart = self.raw('cart/cart.go')
        self.assertTrue(cart.endswith('\r\n'))
        self.assertNotIn('\n', cart.replace('\r\n', ''))
        self.assertIn('func Total([]byte) { panic("todo") } // skel:stub signature guessed\r\n',
                      cart)
        self.assertEqual(self.raw('shelf/shelf.go'), shelf)
        self.assertEqual(self.raw('cart/types_recovered.go'), text)

    def test_markers_in_strings_and_comments_are_text(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        notes = ('package shelf\n\nvar doc = `\nfunc Gone() { panic("todo") } // skel:stub\n`\n\n'
                 '/*\nfunc Gone2() { panic("todo") } // skel:stub\n*/\n')
        self.put('shelf/notes.go', notes)
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertEqual(self.read(self.out, 'shelf/notes.go'), notes)
        self.assertEqual(self.go_build(self.out).returncode, 0)

    def test_edited_line_with_a_marker_is_hand_written(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        lines = self.lines_of('cart/cart.go')
        n = self.ref_start_line('shop/cart.Total')
        lines[n - 1] = 'func Total(items []Item) int { return len(items) } // skel:stub'
        self.put('cart/cart.go', '\n'.join(lines))
        self.assertEqual(lines[n - 2], self.NOINLINE)
        before = self.read(self.out, 'cart/cart.go')
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            goskel.write(self.ref, self.out, prefix='shop/')
        # the function is hand-written now, so the stub's directive goes
        self.assertEqual(self.read(self.out, 'cart/cart.go'),
                         before.replace(self.NOINLINE + '\n', '\n'))
        self.assertIn('cart/cart.go:%d: marker left on an edited function' % n, err.getvalue())
        self.assertEqual(self.go_build(self.out).returncode, 0)

    def test_stub_followed_by_a_block_comment_stays(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        lines = self.lines_of('shelf/shelf.go')
        n = self.ref_start_line('shop/shelf.Report')      # the lines after it are free
        stub = lines[n - 1]
        self.assertTrue(stub.endswith('// skel:stub') or ' // skel:stub ' in stub)
        lines[n:n + 3] = ['/* a hand-written', '   note */'] + lines[n:n + 1]
        self.put('shelf/shelf.go', '\n'.join(lines))
        before = self.read(self.out, 'shelf/shelf.go')
        for _ in range(2):
            goskel.write(self.ref, self.out, prefix='shop/')
            self.assertEqual(self.read(self.out, 'shelf/shelf.go'), before)
        after = self.lines_of('shelf/shelf.go')
        self.assertEqual(after[n - 1], stub)
        self.assertEqual(after[n:n + 2], ['/* a hand-written', '   note */'])

    def test_backslash_newline_in_a_string_is_refused(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        bad = 'package shelf\n\nvar s = "abc\\\nd"\n\nfunc x() int { return 1 }\n'
        self.put('shelf/bad.go', bad)
        before = {p: slurp(p) for p in self.files(self.out)}
        with self.assertRaises(goskel.Refused) as cm:
            goskel.write(self.ref, self.out, prefix='shop/')
        self.assertIn('bad.go', str(cm.exception))
        self.assertEqual({p: slurp(p) for p in self.files(self.out)}, before)
        r = subprocess.run([sys.executable, os.path.join(TOOLS, 'goskel.py'), self.ref, self.out,
                            '--prefix', 'shop/'], capture_output=True, text=True)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn('bad.go', r.stderr)
        self.assertNotIn('Traceback', r.stderr)

    def test_unchanged_unknown_list_is_not_rewritten(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        path = os.path.join(self.out, 'UNKNOWN.txt')
        os.utime(path, ns=(1000000000, 1000000000))
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertEqual(os.stat(path).st_mtime_ns, 1000000000)

    # Helpers that exist only inlined

    INLINE = '{ panic("todo") } // skel:stub inline-only signature unknown'

    def fixture_line(self, rel, text):
        """The line of the fixture's source file rel that starts with text."""
        with open(os.path.join(FIXTURE, rel)) as f:
            for n, line in enumerate(f, 1):
                if line.startswith(text):
                    return n
        self.fail(text + ' is not in ' + rel)

    def assert_stub_at(self, rel, head, text=None):
        n = self.fixture_line(rel, head)
        self.assertEqual(self.lines_of(rel)[n - 1], (text or head) + ' ' + self.INLINE)

    def test_inlined_only_helper_gets_a_stub_on_its_line(self):
        self.assertNotIn('shop/cart.price', {f.name for f in self.bin.funcs()})
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assert_stub_at('cart/cart.go', 'func price(', 'func price()')
        unknown = self.read(self.out, 'UNKNOWN.txt')
        self.assertIn('shop/cart.price\tcart/cart.go:%d\tinline-only: signature unknown, '
                      'inlined into 1 functions\n' % self.fixture_line('cart/cart.go', 'func price('),
                      unknown)
        r = self.go_build(self.out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_helper_with_its_own_function_is_not_duplicated(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        shelf = self.read(self.out, 'shelf/shelf.go')
        self.assertEqual(shelf.count('func (Base) Label('), 1)
        self.assertNotIn('Label() string { panic("todo") } // skel:stub inline-only', shelf)

    def test_inlined_only_methods_get_their_receivers(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assert_stub_at('shelf/shelf.go', 'func (s *Shelf) size(', 'func (*Shelf) size()')
        self.assert_stub_at('shelf/shelf.go', 'func (b Base) note(', 'func (Base) note()')
        unknown = self.read(self.out, 'UNKNOWN.txt')
        self.assertRegex(unknown, r'shop/cart\.counter\.twice\tcart/round\.go:\d+\tinline-only '
                         r'skipped: receiver type unknown, inlined into 1 functions\n')
        self.assertNotIn('twice', self.read(self.out, 'cart/round.go'))

    def test_inlined_only_generic_helper_is_written_once(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assert_stub_at('shelf/shelf.go', 'func either[', 'func either[T1 any]()')
        self.assertEqual(self.read(self.out, 'shelf/shelf.go').count('func either['), 1)

    def test_inlined_only_helper_in_a_new_file_and_package(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertEqual(self.lines_of('cart/round.go')[0], 'package cart')
        self.assert_stub_at('cart/round.go', 'func roundUp(', 'func roundUp()')
        self.assertEqual(self.lines_of('tally/tally.go')[0], 'package tally')
        self.assert_stub_at('tally/tally.go', 'func Add(', 'func Add()')
        self.assertIn('shop/tally\ttally\tinline-only: package with no function or type of its '
                      'own, written for helpers that exist only inlined\n',
                      self.read(self.out, 'UNKNOWN.txt'))
        r = self.go_build(self.out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_inlined_only_stubs_are_kept_and_a_second_run_changes_nothing(self):
        goskel.write(self.ref, self.out, prefix='shop/', keep='keepall')
        self.assertIn('\tprice,\n', self.read(self.out, 'cart/zz_keep_cart.go'))
        shelf = self.read(self.out, 'shelf/zz_keep_shelf.go')
        self.assertIn('\t(*Shelf).size,\n', shelf)
        self.assertIn('\tBase.note,\n', shelf)
        self.assertNotIn('either', shelf)
        self.assertIn('\tAdd,\n', self.read(self.out, 'tally/zz_keep_tally.go'))
        before = {p: slurp(p) for p in self.files(self.out)}
        before['UNKNOWN.txt'] = self.read(self.out, 'UNKNOWN.txt')
        goskel.write(self.ref, self.out, prefix='shop/', keep='keepall')
        after = {p: slurp(p) for p in self.files(self.out)}
        after['UNKNOWN.txt'] = self.read(self.out, 'UNKNOWN.txt')
        self.assertEqual(after, before)
        linked = {f.name for f in self.build_with(self.out, 'keepall').funcs()}
        self.assertTrue({'shop/cart.price', 'shop/shelf.(*Shelf).size', 'shop/tally.Add'} <= linked)
        self.build_with(self.out)

    def test_inlined_only_stub_on_a_taken_line_moves(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        n = self.fixture_line('cart/cart.go', 'func price(')
        lines = self.lines_of('cart/cart.go')
        lines[n - 1] = 'var mine = 1'
        self.put('cart/cart.go', '\n'.join(lines))
        goskel.write(self.ref, self.out, prefix='shop/')
        lines = self.lines_of('cart/cart.go')
        self.assertEqual(lines[n - 1], 'var mine = 1')
        self.assertEqual(sum(l.startswith('func price()') for l in lines), 1)
        self.assertIn('shop/cart.price\tcart/cart.go:%d\tline moved to ' % n,
                      self.read(self.out, 'UNKNOWN.txt'))

    # The noinline directive on stubs of functions that the binary never inlines

    NOINLINE = '//go:noinline // skel:noinline'

    def above(self, rel, name, out=None):
        """The line above the stub of the function called name, in the file rel."""
        with open(os.path.join(out or self.out, rel)) as f:
            return f.read().split('\n')[self.ref_start_line(name) - 2]

    def snapshot(self, out=None):
        out = out or self.out
        files = {os.path.relpath(p, out): slurp(p) for p in self.files(out)}
        files['UNKNOWN.txt'] = self.read(out, 'UNKNOWN.txt')
        return files

    def test_never_inlined_stub_gets_the_directive(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        for rel, name in (('cart/shapes.go', 'shop/cart.Square'), ('cart/cart.go', 'shop/cart.Total'),
                          ('cart/shapes.go', 'shop/cart.Lo'),
                          ('shelf/shelf.go', 'shop/shelf.(*Shelf).Stock'),
                          ('shelf/shelf.go', 'shop/shelf.(*Shelf).Add')):
            self.assertEqual(self.above(rel, name), self.NOINLINE, name)
            self.assertTrue(self.lines_of(rel)[self.ref_start_line(name) - 1].startswith('func '))
        r = self.go_build(self.out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_function_inlined_somewhere_gets_no_directive(self):
        self.assertIn('shop/shelf.Base.Label',
                      {i.callee for f in self.bin.funcs() for i in self.bin.inltree(f)})
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertEqual(self.above('shelf/shelf.go', 'shop/shelf.Base.Label'), '')

    def test_inline_only_and_generic_stubs_get_no_directive(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        n = self.fixture_line('cart/cart.go', 'func price(')
        self.assertEqual(self.lines_of('cart/cart.go')[n - 2], '')
        for rel in ('cart/round.go', 'tally/tally.go'):
            self.assertNotIn('go:noinline', self.read(self.out, rel))
        n = self.fixture_line('shelf/shelf.go', 'func First[')
        self.assertEqual(self.lines_of('shelf/shelf.go')[n - 2], '')

    def test_line_above_holding_code_gets_no_directive(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        n = self.ref_start_line('shop/cart.Hi')
        self.assertEqual(self.ref_start_line('shop/cart.Lo'), n - 1)
        self.assertTrue(self.above('cart/shapes.go', 'shop/cart.Hi').startswith('func Lo('))
        self.assertIn('shop/cart.Hi\tcart/shapes.go:%d\tnoinline not written: the line above '
                      'holds code in the binary\n' % n, self.read(self.out, 'UNKNOWN.txt'))

    def test_hand_comment_above_a_stub_stays_and_is_listed(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        n = self.ref_start_line('shop/cart.Square')
        lines = self.lines_of('cart/shapes.go')
        lines[n - 2] = '// Square is next.'
        self.put('cart/shapes.go', '\n'.join(lines))
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertEqual(self.lines_of('cart/shapes.go'), lines)
        self.assertIn('shop/cart.Square\tcart/shapes.go:%d\tnoinline not written: the line above '
                      'is not blank\n' % n, self.read(self.out, 'UNKNOWN.txt'))

    def test_claimed_function_loses_the_directive(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        self.put_body(self.out, 'Square', 'return 0')
        n = self.ref_start_line('shop/cart.Tick')
        lines = self.lines_of('cart/shapes.go')
        self.assertEqual(lines[n - 2], self.NOINLINE)
        lines[n - 2] = '// Tick is mine.'
        lines[n - 1] = 'func Tick() {}'
        self.put('cart/shapes.go', '\n'.join(lines))
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertEqual(self.above('cart/shapes.go', 'shop/cart.Square'), '')
        self.assertEqual(self.above('cart/shapes.go', 'shop/cart.Tick'), '// Tick is mine.')
        self.assertEqual(self.above('cart/shapes.go', 'shop/cart.Parse'), self.NOINLINE)
        r = self.go_build(self.out)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_second_run_with_directives_is_byte_identical(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        self.put_body(self.out, 'Square', 'return 0')
        goskel.write(self.ref, self.out, prefix='shop/')
        before = self.snapshot()
        goskel.write(self.ref, self.out, prefix='shop/')
        self.assertEqual(self.snapshot(), before)

    def test_switching_directives_off_removes_them_and_moves_no_line(self):
        goskel.write(self.ref, self.out, prefix='shop/')
        on = self.snapshot()
        self.assertIn(self.NOINLINE, on['cart/shapes.go'])
        goskel.write(self.ref, self.out, prefix='shop/', noinline=False)
        off = self.snapshot()
        for rel, text in off.items():
            self.assertNotIn('go:noinline', text, rel)
            if rel.endswith('.go'):
                self.assertEqual(text.split('\n'),
                                 ['' if l == self.NOINLINE else l for l in on[rel].split('\n')])
        fresh = tempfile.mkdtemp(prefix='out-', dir=self.tmp)
        r = subprocess.run([sys.executable, os.path.join(TOOLS, 'goskel.py'), self.ref, fresh,
                            '--prefix', 'shop/', '--no-noinline-directives'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn('go:noinline', slurp(os.path.join(fresh, 'cart', 'shapes.go')))

    def test_directive_keeps_a_real_call_in_the_caller(self):
        import gocmpx
        goskel.write(self.ref, self.out, prefix='shop/', keep='keepall')
        lines = self.lines_of('cart/shapes.go')
        n = self.ref_start_line('shop/cart.Hi')
        self.assertTrue(lines[n - 1].startswith('func Hi('))
        lines[n - 1] = 'func Hi(n int) int { return Square(n) + 1 }'
        self.put('cart/shapes.go', '\n'.join(lines))
        goskel.write(self.ref, self.out, prefix='shop/', keep='keepall')
        self.assertEqual(self.above('cart/shapes.go', 'shop/cart.Square'), self.NOINLINE)

        def hi(b):
            f = b.func('shop/cart.Hi')
            return (gocmpx.calls_many(b, ['shop/cart.Hi'])['shop/cart.Hi'],
                    {i.callee for i in b.inltree(f)})
        calls, inlined = hi(self.build_with(self.out, 'keepall'))
        self.assertIn('shop/cart.Square', calls)
        self.assertNotIn('shop/cart.Square', inlined)
        goskel.write(self.ref, self.out, prefix='shop/', keep='keepall', noinline=False)
        calls, inlined = hi(self.build_with(self.out, 'keepall'))     # the panic is inlined
        self.assertNotIn('shop/cart.Square', calls)
        self.assertIn('shop/cart.Square', inlined)

    def test_range_over_func_bodies_are_not_declared(self):
        self.assertEqual(goskel.parse_name('(*List).Add-range1'), 'range-over-func loop body')
        self.assertEqual(goskel.parse_name('Walk-range2.1'), 'range-over-func loop body')
        self.assertEqual(goskel.parse_name('(*List).Add').name, 'Add')

    def test_command_line(self):
        r = subprocess.run([sys.executable, os.path.join(TOOLS, 'goskel.py'), self.ref, self.out,
                            '--prefix', 'shop/'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.exists(os.path.join(self.out, 'cart', 'cart.go')))
        r = subprocess.run([sys.executable, os.path.join(TOOLS, 'gotypes.py'), self.ref,
                            '^shop/cart$'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('type Item struct', r.stdout)


if __name__ == '__main__':
    unittest.main()
