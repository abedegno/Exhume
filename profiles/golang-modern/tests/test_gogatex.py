"""Unit tests for gogatex.py, the gate that compares a candidate binary with a reference.

The fixture module `shop` is built with the host's Go for linux/amd64 (with -trimpath, so file
names read shop/cart/cart.go) in a temporary directory; the repository's copy is never touched.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.dirname(TESTS_DIR)
FIXTURE = os.path.join(TESTS_DIR, 'fixtures', 'shop')
GATE = os.path.join(PROFILE, 'tools', 'gogatex.py')
sys.path.insert(0, os.path.join(PROFILE, 'tools'))
sys.path.insert(0, TESTS_DIR)

import gobin
import gogatex
from test_gobin import go_env

TOTAL = 'shop/cart.Total'


class StubRef:
    def __init__(self, funcs):
        self._funcs = funcs

    def funcs(self):
        return self._funcs


def read_rows(path):
    with open(path) as f:
        lines = f.read().splitlines()
    return [l.split('\t') for l in lines[1:]]


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('go'):
            raise unittest.SkipTest('go is not on the PATH')
        if not shutil.which('objdump'):
            raise unittest.SkipTest('objdump is not on the PATH')
        cls.tmp = tempfile.mkdtemp(prefix='gogatex-test-')
        cls.ref = cls.build('ref', None)
        cls.same = cls.build('same', None)
        cls.changed = cls.build('changed', ('* i.Qty', '* 3'))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @classmethod
    def build(cls, name, edit):
        src = os.path.join(cls.tmp, 'src-' + name)
        shutil.copytree(FIXTURE, src)
        if edit:
            path = os.path.join(src, 'cart', 'cart.go')
            with open(path) as f:
                text = f.read()
            with open(path, 'w') as f:
                f.write(text.replace(*edit))
        out = os.path.join(cls.tmp, 'bin-' + name)
        r = subprocess.run(['go', 'build', '-trimpath', '-buildvcs=false', '-o', out, './cmd/till'],
                           cwd=src, env=go_env(CGO_ENABLED='0'), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError('go build failed:\n' + r.stdout + r.stderr)
        return out

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='case-', dir=self.tmp)
        self.status = os.path.join(self.work, 'status.tsv')

    def gate(self, ref, cand, source_with_marker=None, extra=(), today='2026-01-01'):
        """Run the gate on shop/ functions; with source_with_marker (a file of the fixture, such
        as cart/cart.go) a source tree is given in which that file holds a `// fitted:` comment."""
        cmd = [sys.executable, GATE, ref, cand, self.status, '--prefix', 'shop/', '--today', today]
        if source_with_marker:
            src = os.path.join(self.work, 'src')
            shutil.rmtree(src, ignore_errors=True)
            shutil.copytree(FIXTURE, os.path.join(src, 'shop'))
            path = os.path.join(src, 'shop', source_with_marker)
            with open(path) as f:
                text = f.read()
            with open(path, 'w') as f:
                f.write(text.replace('total := 0', '// fitted: matches the original\n\ttotal := 0'))
            cmd += ['--src', src]
        return subprocess.run(cmd + list(extra), capture_output=True, text=True)



class Gate(Base):
    def test_counts(self):
        out = self.gate(self.ref, self.same)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertRegex(out.stdout.splitlines()[-1], r'^matched \d+ of \d+; fitted 0$')
        self.assertRegex(out.stdout, r'data operands of the 2 functions scored MATCH: \d+ by '
                         r'content, \d+ by type, \d+ by function, \d+ masked\n')
        self.assertIn(['shop/cart.Total', 'shop/cart/cart.go', 'MATCH', '0', '2026-01-01'],
                      read_rows(self.status))

    def test_only_first_party_files_are_compared(self):
        self.gate(self.ref, self.same)
        names = {r[0] for r in read_rows(self.status)}
        self.assertEqual(names, {TOTAL, 'main.main'})      # runtime.* files are elsewhere
        os.unlink(self.status)       # excluding a matched function would be a regression
        out = self.gate(self.ref, self.same, extra=['--exclude', '/cart/'])
        self.assertEqual(out.stdout.splitlines()[-1], 'matched 1 of 1; fitted 0')
        out = self.gate(self.ref, self.same, extra=['--exclude', '/cart/', '--exclude', '/cmd/'])
        self.assertEqual(out.returncode, 2)               # nothing selected at all

    def test_name_prefix_selects_functions_of_other_files(self):
        funcs = [gobin.Func('shop/cart.Total.Printf.func1', 0, 1, 'fmt/print.go', 1, 0),
                 gobin.Func('fmt.Println', 2, 3, 'fmt/print.go', 5, 0),
                 gobin.Func('shop/cart.Total', 4, 5, 'shop/cart/cart.go', 1, 0)]
        ref = StubRef(funcs)
        names = lambda *a: [f.name for f in gogatex.selected(ref, 'shop/', [], *a)]
        self.assertEqual(names(), ['shop/cart.Total'])
        self.assertEqual(names(['shop/']), ['shop/cart.Total.Printf.func1', 'shop/cart.Total'])

    def test_name_prefix_option(self):
        out = self.gate(self.ref, self.same, extra=['--exclude', '/', '--name-prefix', 'shop/'])
        self.assertEqual(out.stdout.splitlines()[-1], 'matched 1 of 1; fitted 0')
        self.assertEqual([r[0] for r in read_rows(self.status)], [TOTAL])

    def test_regression_fails_and_is_named(self):
        self.gate(self.ref, self.same)
        out = self.gate(self.ref, self.changed)
        self.assertEqual(out.returncode, 1)
        self.assertIn('REGRESSED\tshop/cart.Total', out.stdout)
        self.assertEqual(out.stdout.splitlines()[-1],
                         'matched 2 of 2; fitted 0 (status file unchanged, not written)')
        self.assertNotIn('SIZE', [r[2] for r in read_rows(self.status)])   # not written

    def test_regression_holds_across_runs(self):
        self.gate(self.ref, self.same)
        with open(self.status, 'rb') as f:
            before = f.read()
        for _ in range(2):
            out = self.gate(self.ref, self.changed)
            self.assertEqual(out.returncode, 1)
            self.assertIn('REGRESSED\tshop/cart.Total', out.stdout)
            with open(self.status, 'rb') as f:
                self.assertEqual(f.read(), before)

    def test_accept_regressions_writes_the_file(self):
        self.gate(self.ref, self.same)
        out = self.gate(self.ref, self.changed, extra=['--accept-regressions'])
        self.assertEqual(out.returncode, 0)
        self.assertIn('REGRESSED\tshop/cart.Total', out.stdout)
        self.assertIn('SIZE', [r[2] for r in read_rows(self.status)])
        out = self.gate(self.ref, self.changed)          # accepted: no longer a regression
        self.assertEqual(out.returncode, 0)
        self.assertNotIn('REGRESSED', out.stdout)

    def test_no_regression_without_an_earlier_match(self):
        out = self.gate(self.ref, self.changed)
        self.assertEqual(out.returncode, 0)
        self.assertNotIn('REGRESSED', out.stdout)

    def test_since_keeps_the_date_while_the_status_holds(self):
        self.gate(self.ref, self.same, today='2026-01-01')
        self.gate(self.ref, self.same, today='2026-02-02')
        self.assertEqual(read_rows(self.status)[0][4], '2026-01-01')
        self.gate(self.ref, self.changed, extra=['--accept-regressions'], today='2026-03-03')
        self.assertEqual(read_rows(self.status)[0][4], '2026-03-03')

    def test_rows_gone_from_the_reference_are_dropped(self):
        os.makedirs(self.work, exist_ok=True)
        with open(self.status, 'w') as f:
            f.write('name\tfile\tstatus\tfitted\tsince\n'
                    'shop/cart.Gone\tshop/cart/cart.go\tDIFF\t0\t2025-01-01\n')
        out = self.gate(self.ref, self.same)
        self.assertEqual(out.returncode, 0)
        self.assertNotIn('Gone', open(self.status).read())

    def test_only_scores_a_subset_and_keeps_other_rows(self):
        with open(self.status, 'w') as f:
            f.write('name\tfile\tstatus\tfitted\tsince\n'
                    'main.main\tshop/cmd/till/main.go\tDIFF\t1\t2025-05-05\n'
                    'shop/other.Kept\tshop/other/o.go\tMATCH\t0\t2025-01-01\n')
        out = self.gate(self.ref, self.same, extra=['--only', r'^shop/cart\.'])
        self.assertEqual(out.returncode, 0, out.stderr)
        lines = out.stdout.splitlines()
        self.assertEqual(lines[-2:], ['scored 1 functions', 'matched 2 of 3; fitted 1'])
        rows = read_rows(self.status)
        self.assertIn(['main.main', 'shop/cmd/till/main.go', 'DIFF', '1', '2025-05-05'], rows)
        self.assertIn(['shop/other.Kept', 'shop/other/o.go', 'MATCH', '0', '2025-01-01'], rows)
        self.assertIn([TOTAL, 'shop/cart/cart.go', 'MATCH', '0', '2026-01-01'], rows)

    def test_only_judges_regressions_on_scored_rows(self):
        with open(self.status, 'w') as f:
            f.write('name\tfile\tstatus\tfitted\tsince\n'
                    'shop/cart.Total\tshop/cart/cart.go\tMATCH\t0\t2025-01-01\n'
                    'main.main\tshop/cmd/till/main.go\tMATCH\t0\t2025-01-01\n')
        out = self.gate(self.ref, self.changed, extra=['--only', '^main\\.'])
        self.assertEqual(out.returncode, 0, out.stdout)
        self.assertNotIn('REGRESSED', out.stdout)
        out = self.gate(self.ref, self.changed, extra=['--only', 'cart'])
        self.assertEqual(out.returncode, 1)
        self.assertIn('REGRESSED\tshop/cart.Total', out.stdout)

    def match_status(self, *names):
        with open(self.status, 'w') as f:
            f.write('name\tfile\tstatus\tfitted\tsince\n')
            for n in names:
                f.write('%s\tshop/cart/cart.go\tMATCH\t0\t2025-01-01\n' % n)
        with open(self.status, 'rb') as f:
            return f.read()

    def test_wrong_prefix_after_a_good_run_is_a_tool_error(self):
        self.gate(self.ref, self.same)
        with open(self.status, 'rb') as f:
            before = f.read()
        out = subprocess.run([sys.executable, GATE, self.ref, self.same, self.status,
                              '--prefix', 'typo/'], capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertIn('no function', out.stderr)
        with open(self.status, 'rb') as f:
            self.assertEqual(f.read(), before)

    def test_match_row_gone_from_the_reference_is_a_regression(self):
        old = self.match_status('shop/cart.Gone')
        out = self.gate(self.ref, self.same)
        self.assertEqual(out.returncode, 1)
        self.assertIn('REGRESSED\tshop/cart.Gone', out.stdout)
        self.assertEqual(out.stdout.splitlines()[-1],
                         'matched 1 of 1; fitted 0 (status file unchanged, not written)')
        with open(self.status, 'rb') as f:
            self.assertEqual(f.read(), old)
        out = self.gate(self.ref, self.same, extra=['--accept-regressions'])
        self.assertEqual(out.returncode, 0)
        self.assertNotIn('Gone', open(self.status).read())

    def test_only_does_not_drop_a_matching_unselected_match_row(self):
        old = self.match_status('shop/cart.Gone')
        out = self.gate(self.ref, self.same, extra=['--only', 'Gone'])
        self.assertEqual(out.returncode, 1)
        self.assertIn('REGRESSED\tshop/cart.Gone', out.stdout)
        with open(self.status, 'rb') as f:
            self.assertEqual(f.read(), old)

    def test_only_drops_a_non_match_row_silently(self):
        with open(self.status, 'w') as f:
            f.write('name\tfile\tstatus\tfitted\tsince\n'
                    'shop/cart.Gone\tshop/cart/cart.go\tDIFF\t0\t2025-01-01\n')
        out = self.gate(self.ref, self.same, extra=['--only', 'Gone'])
        self.assertEqual(out.returncode, 0)
        self.assertNotIn('Gone', open(self.status).read())

    def test_bad_only_regex_is_a_tool_error(self):
        out = self.gate(self.ref, self.same, extra=['--only', '('])
        self.assertEqual(out.returncode, 2)
        self.assertFalse(os.path.exists(self.status))

    def test_fitted_marker_is_counted(self):
        out = self.gate(self.ref, self.same, source_with_marker='cart/cart.go')
        self.assertTrue(out.stdout.splitlines()[-1].endswith('fitted 1'))
        self.assertEqual(read_rows(self.status)[0][3], '1')


class Errors(Base):
    """Failures that must not lose a regression, and the stub cases."""

    def stub_gate(self, funcs, cand=None, ref_all=None, **kw):
        ref = StubRef(funcs if ref_all is None else ref_all)
        return gogatex.gate(ref, StubRef(cand or []), self.status, 'shop/', **kw)

    def run_with_status(self, content, **kw):
        if isinstance(content, str):
            content = content.encode()
        with open(self.status, 'wb') as f:
            f.write(content)
        out = self.gate(self.ref, self.same, **kw)
        with open(self.status, 'rb') as f:
            self.assertEqual(f.read(), content)           # untouched
        return out

    def test_malformed_row_is_a_tool_error(self):
        out = self.run_with_status('name\tfile\tstatus\tfitted\tsince\nshop/cart.Total\tMATCH\n')
        self.assertEqual(out.returncode, 2)
        self.assertIn(':2:', out.stderr)

    def test_headerless_file_is_a_tool_error(self):
        out = self.run_with_status('shop/cart.Total\tf\tMATCH\t0\t2026-01-01\n')
        self.assertEqual(out.returncode, 2)
        self.assertIn('header', out.stderr)

    def test_non_utf8_file_is_a_tool_error(self):
        out = self.run_with_status(b'name\tfile\tstatus\tfitted\tsince\n\xff\xfe\n')
        self.assertEqual(out.returncode, 2)
        self.assertIn('UTF-8', out.stderr)

    def test_unreadable_binary_is_a_tool_error(self):
        out = self.gate(os.path.join(self.work, 'nothing'), self.same)
        self.assertEqual(out.returncode, 2)
        self.assertFalse(os.path.exists(self.status))

    def test_tab_in_a_name_aborts_before_writing(self):
        funcs = [gobin.Func('shop/a\tb', 0, 1, 'shop/a.go', 1, 0)]
        with self.assertRaises(gogatex.ToolError):
            self.stub_gate(funcs)
        self.assertFalse(os.path.exists(self.status))

    def test_duplicate_names_are_dup_and_counted(self):
        funcs = [gobin.Func('shop/x', 0, 1, 'shop/a.go', 1, 0),
                 gobin.Func('shop/x', 2, 3, 'shop/a.go', 9, 0)]
        r = self.stub_gate(funcs)
        self.assertEqual((r.regressed, r.dups, r.matched, r.total), ([], 1, 0, 1))
        self.assertEqual(read_rows(self.status)[0][:3], ['shop/x', 'shop/a.go', 'DUP'])

    def test_name_duplicated_in_an_unselected_reference_file_is_dup(self):
        one = gobin.Func('shop/x', 0, 1, 'shop/a.go', 1, 0)
        other = gobin.Func('shop/x', 2, 3, 'vendor/b.go', 1, 0)
        r = self.stub_gate([one], ref_all=[one, other])
        self.assertEqual(r[1], 1)
        self.assertEqual(read_rows(self.status)[0][2], 'DUP')

    def test_name_duplicated_in_the_candidate_is_dup(self):
        one = gobin.Func('shop/x', 0, 1, 'shop/a.go', 1, 0)
        self.stub_gate([one], cand=[one, gobin.Func('shop/x', 2, 3, 'shop/a.go', 9, 0)])
        self.assertEqual(read_rows(self.status)[0][2], 'DUP')

    def test_tab_in_a_file_aborts(self):
        with self.assertRaises(gogatex.ToolError):
            self.stub_gate([gobin.Func('shop/x', 0, 1, 'shop/a\tb.go', 1, 0)])

    def test_status_file_keeps_its_permissions(self):
        with open(self.status, 'w') as f:
            f.write('name\tfile\tstatus\tfitted\tsince\n')
        os.chmod(self.status, 0o640)
        self.stub_gate([gobin.Func('shop/x', 0, 1, 'shop/a.go', 1, 0), gobin.Func(
            'shop/x', 2, 3, 'shop/a.go', 9, 0)])
        self.assertEqual(os.stat(self.status).st_mode & 0o777, 0o640)

    def test_duplicate_line_is_printed_by_main(self):
        import contextlib
        import io
        funcs = [gobin.Func('shop/x', 0, 1, 'shop/a.go', 1, 0),
                 gobin.Func('shop/x', 2, 3, 'shop/a.go', 9, 0)]
        real, gogatex.gobin.load = gogatex.gobin.load, lambda path: StubRef(funcs)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = gogatex.main(['gogatex', 'r', 'c', self.status, '--prefix', 'shop/'])
        finally:
            gogatex.gobin.load = real
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().splitlines(), ['duplicate names: 1',
                                                      'matched 0 of 1; fitted 0'])

    def test_match_turning_dup_is_a_regression(self):
        with open(self.status, 'w') as f:
            f.write('name\tfile\tstatus\tfitted\tsince\nshop/x\tshop/a.go\tMATCH\t0\t2025-01-01\n')
        funcs = [gobin.Func('shop/x', 0, 1, 'shop/a.go', 1, 0),
                 gobin.Func('shop/x', 2, 3, 'shop/a.go', 9, 0)]
        self.assertEqual(self.stub_gate(funcs)[0], ['shop/x'])

    def test_marker_counts_for_the_first_of_two_on_one_line(self):
        src = os.path.join(self.work, 'src')
        os.makedirs(os.path.join(src, 'shop'))
        with open(os.path.join(src, 'shop', 'a.go'), 'w') as f:
            f.write('package a\nfunc f() { g := func() {}; // fitted: x\n}\n')
        funcs = [gobin.Func('shop/f', 0, 1, 'shop/a.go', 2, 0),
                 gobin.Func('shop/f.func1', 2, 3, 'shop/a.go', 2, 0)]
        self.assertEqual(gogatex.fitted_flags(StubRef(funcs), funcs, src), [True, False])

    def test_failed_write_keeps_the_old_file(self):
        with open(self.status, 'w') as f:
            f.write('old')
        real = os.replace
        os.replace = lambda *a: (_ for _ in ()).throw(OSError('boom'))
        try:
            with self.assertRaises(OSError):
                gogatex.write_status(self.status, [('a', 'b', 'MATCH', '0', 'd')])
        finally:
            os.replace = real
        with open(self.status) as f:
            self.assertEqual(f.read(), 'old')
        self.assertEqual(os.listdir(self.work), ['status.tsv'])


if __name__ == '__main__':
    unittest.main()
