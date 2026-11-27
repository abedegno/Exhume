"""Unit tests for gocmpx.py, the function comparator.

The fixture module `shop` is copied to a temporary directory, edited there and built with the
host's Go for linux/amd64 (with -trimpath, so file names do not depend on the directory). The
repository's copy is never touched. The test of padding between an internally and an externally
linked build needs a C compiler that targets linux/amd64 and is skipped without one; the rule
itself is also tested on hand-made bytes.
"""
import itertools
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.dirname(TESTS_DIR)
FIXTURE = os.path.join(TESTS_DIR, 'fixtures', 'shop')
sys.path.insert(0, os.path.join(PROFILE, 'tools'))
sys.path.insert(0, TESTS_DIR)

import gobin
import gocmpx
from test_gobin import amd64_c_compiler, go_env

TOTAL = 'shop/cart.Total'
ORIGINAL = ()


class Edit:
    """A change to the fixture's sources: a name that identifies it, and a function that edits a
    {path: text} dict in place."""

    def __init__(self, key, fn):
        self.key, self.fn = key, fn


def add_blank_line_above(func):
    return Edit(('blank', func), lambda files: files.update({'cart/cart.go': files[
        'cart/cart.go'].replace('// %s is' % func, '\n// %s is' % func)}))


def replace(old, new, path='cart/cart.go'):
    return Edit(('replace', old, new, path), lambda files: files.update({path: files[
        path].replace(old, new)}))


def add_function(source):
    """Add a function that runs at start-up, in a file that sorts before cart.go."""
    return Edit(('add', source), lambda files: files.update({
        'cart/aaa.go': 'package cart\n\n//go:noinline\n%s\n\nfunc init() { Spare() }\n' % source}))


def call_helper(which, as_value=False):
    """Total starts by calling helperA or helperB, which both exist in the source; as a value, it
    passes the function to a sink instead of calling it."""
    return Edit(('helper', which, as_value), lambda files: files.update({
        'cart/helpers.go': 'package cart\n\nimport "fmt"\n\n//go:noinline\nfunc helperA() int { return 1 }\n\n'
                           '//go:noinline\nfunc helperB() int { return 2 }\n\n'
                           '//go:noinline\nfunc sink(f func() int) int { return len(fmt.Sprint(f)) }\n',
        'cart/cart.go': files['cart/cart.go'].replace('total := 0', 'total := %s' % (
            'sink(%s)' % which if as_value else which + '()'))}))


class Stub:
    """Just enough of a gobin.Binary to hold one function, p.g."""

    def __init__(self, code, lines, tree):
        self.fn, self.code_bytes, self.line_table, self.tree = gobin.Func(
            'p.g', 0x1000, 0x1000 + len(code), 'a.go', 1, 0), code, lines, tree

    def funcs(self):
        return [self.fn]

    def code(self, func):
        return self.code_bytes

    def lines(self, func):
        return self.line_table

    def inltree(self, func):
        return self.tree


class Comparator(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('go'):
            raise unittest.SkipTest('go is not on the PATH')
        if not shutil.which('objdump'):
            raise unittest.SkipTest('objdump is not on the PATH')
        cls.tmp = tempfile.mkdtemp(prefix='gocmpx-test-')
        cls.built, cls.counter = {}, itertools.count()
        cls.internal = cls.build(ORIGINAL)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def build(cls, edits, ldflags='', cgo='0', cc=None, tag='internal'):
        """Build the fixture with the edits (functions on a {path: text} dict) applied."""
        key = (tuple(e.key for e in edits), ldflags, cgo, tag)
        if key in cls.built:
            return cls.built[key]
        n = next(cls.counter)
        src = os.path.join(cls.tmp, 'src%d' % n)
        shutil.copytree(FIXTURE, src)
        files = {}
        for rel in ('cart/cart.go', 'cmd/till/main.go'):
            with open(os.path.join(src, rel)) as f:
                files[rel] = f.read()
        for edit in edits:
            edit.fn(files)
        for rel, text in files.items():
            with open(os.path.join(src, rel), 'w') as f:
                f.write(text)
        out = os.path.join(cls.tmp, 'bin%d' % n)
        env = go_env(CGO_ENABLED=cgo)
        if cc:
            env['CC'] = cc
        cmd = ['go', 'build', '-trimpath', '-buildvcs=false', '-o', out]
        if ldflags:
            cmd.append('-ldflags=' + ldflags)
        cmd.append('./cmd/till')
        r = subprocess.run(cmd, cwd=src, env=env, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError('go build failed:\n' + r.stdout + r.stderr)
        cls.built[key] = out
        return out

    def between(self, ref, cand, name=TOTAL):
        return gocmpx.compare(gobin.load(ref), gobin.load(cand), name)

    def cmp_edit(self, *edits, name=TOTAL, base=ORIGINAL):
        return self.between(self.build(base), self.build(edits), name)

    # The brief's tests

    def test_same_source_matches(self):
        self.assertEqual(self.cmp_edit().status, 'MATCH')

    def test_moved_function_is_lines(self):
        self.assertEqual(self.cmp_edit(add_blank_line_above('Total')).status, 'LINES')

    def test_renamed_inlined_callee_is_inline(self):   # Review focus 2
        """Same code, same lines, but the inlined call is to another function."""
        r = self.cmp_edit(replace('price', 'cost'))
        self.assertEqual(r.status, 'INLINE', r)
        self.assertIn('shop/cart.price', r.detail)
        self.assertIn('shop/cart.cost', r.detail)

    def test_moved_call_site_is_inline(self):          # Review focus 2
        """Moving the call onto its own line makes the compiler add a nop (so the code differs),
        and any other way of moving it moves the lines too: this case is built from stubs."""
        code, lines = b'\x55\xc3', [(0, 'a.go', 5)]
        ref = Stub(code, lines, [gobin.Inl('p.f', 'a.go', 7, -1, [(0, 1)])])
        cand = Stub(code, lines, [gobin.Inl('p.f', 'a.go', 8, -1, [(0, 1)])])
        r = gocmpx.compare(ref, cand, 'p.g')
        self.assertEqual(r.status, 'INLINE', r)
        self.assertIn('a.go:7', r.detail)
        self.assertIn('a.go:8', r.detail)
        cand = Stub(code, [(0, 'b.go', 5)], cand.tree)
        self.assertEqual(gocmpx.compare(ref, cand, 'p.g').status, 'LINES')
        self.assertEqual(gocmpx.compare(ref, Stub(code, lines, ref.tree), 'p.g').status, 'MATCH')

    def test_changed_constant_is_diff(self):
        r = self.between(self.build((replace('* i.Qty', '* 2'),)),
                         self.build((replace('* i.Qty', '* 3'),)))
        self.assertEqual(r.status, 'DIFF', r)

    def test_padding_is_ignored(self):                 # Review focus 4
        cc = amd64_c_compiler()
        if not cc:
            self.skipTest('no C compiler for linux/amd64 (set CC_LINUX_AMD64)')
        external = self.build(ORIGINAL, ldflags='-linkmode=external', cgo='1', cc=cc,
                              tag='external')
        self.assertEqual(self.between(self.internal, external).status, 'MATCH')

    def test_other_binary_layout_still_matches(self):  # an unrelated function added
        self.assertEqual(self.cmp_edit(add_function('func Spare() {}')).status, 'MATCH')

    # Beyond the brief

    def test_relocated_calls_still_match_when_layout_moves(self):
        b = gobin.load(self.internal)
        moved = gobin.load(self.build((add_function('func Spare() {}'),)))
        self.assertNotEqual(b.func('main.main').entry, moved.func('main.main').entry)
        self.assertNotEqual(b.code(b.func('main.main')), moved.code(moved.func('main.main')))
        self.assertEqual(gocmpx.compare(b, moved, 'main.main').status, 'MATCH')

    def test_call_to_another_function_is_diff(self):
        r = self.between(self.build((call_helper('helperA'),)),
                         self.build((call_helper('helperB'),)))
        self.assertEqual(r.status, 'DIFF', r)
        self.assertIn('shop/cart.helperA+0x0', r.detail)
        self.assertIn('shop/cart.helperB+0x0', r.detail)

    def test_address_of_another_function_is_diff(self):
        ref = self.build((call_helper('helperA', as_value=True),))
        cand = self.build((call_helper('helperB', as_value=True),))
        r = self.between(ref, cand)
        self.assertEqual(r.status, 'DIFF', r)
        self.assertIn('shop/cart.helperA+0x0', r.detail)
        self.assertIn('shop/cart.helperB+0x0', r.detail)

    def test_address_of_the_same_function_matches_when_it_moves(self):
        ref = self.build((call_helper('helperA', as_value=True),))
        cand = self.build((call_helper('helperA', as_value=True), add_function('func Spare() {}')))
        self.assertEqual(self.between(ref, cand).status, 'MATCH')

    def test_call_to_the_same_function_matches_when_it_moves(self):
        ref = self.build((call_helper('helperA'),))
        cand = self.build((call_helper('helperA'), add_function('func Spare() {}')))
        self.assertEqual(self.between(ref, cand).status, 'MATCH')

    def test_missing_function(self):
        """With the only call to Total gone, the linker leaves Total out."""
        r = self.between(self.internal,
                         self.build((replace('cart.Total(items)', 'len(items)', 'cmd/till/main.go'),)))
        self.assertEqual((r.status, r.name), ('MISSING', TOTAL))
        self.assertIn('candidate', r.detail)
        r = self.between(self.build((replace('cart.Total(items)', 'len(items)', 'cmd/till/main.go'),)),
                         self.internal)
        self.assertEqual((r.status, r.name), ('MISSING', TOTAL))
        self.assertIn('reference', r.detail)

    def test_size_differs(self):
        r = self.cmp_edit(replace('total += price(it)', 'total += price(it)\n\t\ttotal += it.Qty'))
        self.assertEqual(r.status, 'SIZE', r)

    def test_compare_many_agrees_with_compare(self):
        a, b = gobin.load(self.internal), gobin.load(self.build((add_function('func Spare() {}'),)))
        names = [TOTAL, 'main.main', 'shop/cart.price', 'no.such.func']
        names = [n for n in names if n in {f.name for f in a.funcs()}]
        many = gocmpx.compare_many(a, b, names)
        self.assertEqual(many, [gocmpx.compare(a, b, n) for n in names])
        self.assertEqual([r.name for r in many], names)

    def test_command_line(self):
        r = subprocess.run([sys.executable, os.path.join(PROFILE, 'tools', 'gocmpx.py'),
                            self.internal, self.internal, r'^shop/cart\.'],
                           capture_output=True, text=True, check=True)
        rows = [l.split('\t') for l in r.stdout.splitlines()]
        self.assertIn(['MATCH', TOTAL, ''], rows)
        self.assertTrue(all(row[0] == 'MATCH' for row in rows))

    # The --diff listing

    def diff_cli(self, ref, cand, name=TOTAL):
        r = subprocess.run([sys.executable, os.path.join(PROFILE, 'tools', 'gocmpx.py'),
                            ref, cand, '--diff', name], capture_output=True, text=True, check=True)
        return r.stdout.splitlines()

    def test_diff_marks_the_differing_row_only(self):
        out = self.diff_cli(self.build((replace('* i.Qty', '* 2'),)),
                            self.build((replace('* i.Qty', '* 3'),)))
        status, name, rest = out[0].split('\t')
        self.assertEqual((status, name), ('DIFF', TOTAL))
        rows = out[2:]
        marked = [r for r in rows if r.startswith('*')]
        self.assertTrue(marked)
        self.assertIn('%d differing rows' % len(marked), rest)
        self.assertLess(len(marked), len(rows))
        same = [r for r in rows if r.startswith('  ')]
        self.assertTrue(same)
        for r in same:                       # unmarked rows hold the same instruction and line
            left, right = r[2:].split(' | ')  # (offsets may differ when lengths changed)
            self.assertEqual(left.strip().split('  ', 1)[1], right.strip().split('  ', 1)[1])
        self.assertIn('cart.go:', rows[0])
        self.assertTrue(any('[shop/cart.price]' in r for r in rows))

    def test_diff_of_a_size_pair_prints_both_sides(self):
        ref = self.build(ORIGINAL)
        cand = self.build((replace('total += price(it)', 'total += price(it)\n\t\ttotal += it.Qty'),))
        a, b = gobin.load(ref), gobin.load(cand)
        summary, rows = gocmpx.diff(a, b, TOTAL)
        self.assertTrue(summary.startswith('SIZE\t' + TOTAL + '\t'))
        rsize = len(gocmpx.trim_padding(a.code(a.func(TOTAL)), gocmpx.decode([a.code(a.func(TOTAL))])[0]))
        self.assertIn('%d bytes / ' % rsize, summary)
        self.assertTrue(any(l is None or r is None for _, l, r in rows))
        self.assertEqual([l.off for _, l, _ in rows if l],
                         sorted(l.off for _, l, _ in rows if l))
        self.assertTrue(all(m == '*' for m, l, r in rows if l is None or r is None))
        out = self.diff_cli(ref, cand)
        self.assertTrue(out[0].startswith('SIZE'))
        one_sided = [r for r in out[2:] if r.startswith('*') and
                     (r.endswith(' |') or r.split(' | ')[0].strip() == '*')]
        self.assertTrue(one_sided)
        self.assertTrue(any('ret' in r.split(' | ')[0] for r in out[2:]))
        self.assertTrue(any('ret' in r.split(' | ')[1] for r in out[2:] if ' | ' in r))


class Decoding(unittest.TestCase):
    """The pieces that do not need a Go build."""

    def setUp(self):
        if not shutil.which('objdump'):
            self.skipTest('objdump is not on the PATH')

    def trimmed(self, code):
        (insns,) = gocmpx.decode([code])
        return gocmpx.trim_padding(code, insns)

    def test_padding_is_trimmed_from_the_end_only(self):
        self.assertEqual(self.trimmed(b'\x55\xc3\xcc\x90\xcc\xcc'), b'\x55\xc3')
        self.assertEqual(self.trimmed(b'\xcc\x55\xc3'), b'\xcc\x55\xc3')
        self.assertEqual(self.trimmed(b'\xcc\x90'), b'')
        self.assertEqual(self.trimmed(b'\x55\xc3'), b'\x55\xc3')
        self.assertEqual(self.trimmed(b'\xc3\x66\x90\x0f\x1f\x00'), b'\xc3')

    def test_padding_bytes_inside_an_instruction_are_kept(self):
        self.assertEqual(self.trimmed(bytes.fromhex('b801909090')), bytes.fromhex('b801909090'))

    def stub_status(self, a, b):
        ref, cand = Stub(a, [(0, 'a.go', 5)], []), Stub(b, [(0, 'a.go', 5)], [])
        return gocmpx.compare(ref, cand, 'p.g')

    def test_instructions_ending_in_padding_bytes_are_not_equal(self):
        r = self.stub_status(bytes.fromhex('b801909090'), bytes.fromhex('b801cccccc'))
        self.assertEqual(r.status, 'DIFF', r)
        self.assertEqual(self.stub_status(bytes.fromhex('c3cccc'), bytes.fromhex('c390')).status,
                         'MATCH')

    def test_branch_leaving_in_only_one_is_diff(self):
        leaves, stays = bytes.fromhex('e810000000c3'), bytes.fromhex('e800000000c3')
        for a, b in ((leaves, stays), (stays, leaves)):
            r = self.stub_status(a, b)
            self.assertEqual(r.status, 'DIFF', r)
            self.assertIn('only one', r.detail)

    def test_rip_address_of_code_must_agree(self):
        lea = bytes.fromhex('488d05f9ffffff')                  # lea rax, [rip - 7]: itself
        r = self.stub_status(lea + b'\xc3', bytes.fromhex('488d05faffffff') + b'\xc3')
        self.assertEqual(r.status, 'DIFF', r)
        self.assertIn('p.g+0x0', r.detail)
        self.assertEqual(self.stub_status(lea + b'\xc3', lea + b'\xc3').status, 'MATCH')
        far = bytes.fromhex('488d0500100000') + b'\xc3'        # data far away: masked
        self.assertEqual(self.stub_status(far, bytes.fromhex('488d0500200000') + b'\xc3').status,
                         'MATCH')

    def test_inlined_call_ranges_are_compared(self):
        code, lines = b'\x55\x90\xc3', [(0, 'a.go', 5)]
        ref = Stub(code, lines, [gobin.Inl('p.f', 'a.go', 7, -1, [(0, 1)])])
        cand = Stub(code, lines, [gobin.Inl('p.f', 'a.go', 7, -1, [(0, 2)])])
        r = gocmpx.compare(ref, cand, 'p.g')
        self.assertEqual(r.status, 'INLINE', r)
        self.assertIn('ranges', r.detail)

    def masked(self, code):
        (insns,) = gocmpx.decode([code])
        return gocmpx.masked(code, insns)

    def test_call_out_of_the_function_is_masked(self):
        code = bytes.fromhex('55e8 78563412 c3'.replace(' ', ''))
        self.assertEqual(self.masked(code), bytes.fromhex('55e800000000c3'))

    def test_jump_inside_the_function_is_kept(self):
        code = bytes.fromhex('0f84 01000000 90 c3'.replace(' ', ''))
        self.assertEqual(self.masked(code), code)
        code = bytes.fromhex('eb00' 'e9fbffffff'.replace(' ', ''))
        self.assertEqual(self.masked(code), code)

    def test_conditional_jump_out_of_the_function_is_masked(self):
        code = bytes.fromhex('0f84 00010000 c3'.replace(' ', ''))
        self.assertEqual(self.masked(code), bytes.fromhex('0f8400000000c3'))

    def test_rip_relative_operand_is_masked_and_immediate_kept(self):
        lea = bytes.fromhex('488d05 00100000'.replace(' ', ''))
        cmp_imm = bytes.fromhex('833d f0ffffff 07'.replace(' ', ''))     # cmp dword [rip-0x10], 7
        self.assertEqual(self.masked(lea + cmp_imm + b'\xc3'),
                         bytes.fromhex('488d0500000000' '833d00000000' '07' 'c3'))

    def test_other_immediates_are_kept(self):
        code = bytes.fromhex('b8 78563412 c3'.replace(' ', ''))
        self.assertEqual(self.masked(code), code)

    def test_many_functions_in_one_run(self):
        out = gocmpx.decode([b'\x55\xc3', b'\xc3', b'\x90\x90\xc3'])
        self.assertEqual([[i.text for i in f] for f in out],
                         [['push\trbp', 'ret'], ['ret'], ['nop', 'nop', 'ret']])

    def test_differing_code_is_told_apart_from_relocation(self):
        a = bytes.fromhex('e8 01000000 c3'.replace(' ', ''))
        b = bytes.fromhex('e8 02000000 c3'.replace(' ', ''))
        c = bytes.fromhex('e8 02000000 90'.replace(' ', ''))
        da, db, dc = gocmpx.decode([a, b, c])
        self.assertIsNone(gocmpx._code_difference(a, b, da, db))
        self.assertIn('ret', gocmpx._code_difference(a, c, da, dc))


if __name__ == '__main__':
    unittest.main()
