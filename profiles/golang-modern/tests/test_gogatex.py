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
    def __init__(self, funcs, inl=None):
        self._funcs = funcs
        self._inl = inl or {}

    def funcs(self):
        return self._funcs

    def inltree(self, func):
        return self._inl.get(func.name, [])


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
    def build(cls, name, edit, files=None):
        """Build the fixture with edit (old, new) applied to cart/cart.go and with files, a
        {path: text or (old, new)} dict, written or edited."""
        src = os.path.join(cls.tmp, 'src-' + name)
        shutil.copytree(FIXTURE, src)
        files = dict(files or {})
        if edit:
            files['cart/cart.go'] = edit
        for rel, text in files.items():
            path = os.path.join(src, rel)
            if isinstance(text, tuple):
                with open(path) as f:
                    text = f.read().replace(*text)
            with open(path, 'w') as f:
                f.write(text)
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
        self.assertRegex(out.stdout.splitlines()[-1], r'^matched \d+ of \d+; fitted 0; waiting 0; 0 of the matched call unmatched code$')
        self.assertRegex(out.stdout, r'data operands of the 2 functions scored MATCH: \d+ by '
                         r'content, \d+ by type, \d+ by function, \d+ masked\n')
        self.assertIn(['shop/cart.Total', 'shop/cart/cart.go', 'MATCH', '0', '2026-01-01', ''],
                      read_rows(self.status))

    def test_only_first_party_files_are_compared(self):
        self.gate(self.ref, self.same)
        names = {r[0] for r in read_rows(self.status)}
        self.assertEqual(names, {TOTAL, 'main.main'})      # runtime.* files are elsewhere
        os.unlink(self.status)       # excluding a matched function would be a regression
        out = self.gate(self.ref, self.same, extra=['--exclude', '/cart/'])
        self.assertEqual(out.stdout.splitlines()[-1], 'matched 1 of 1; fitted 0; waiting 0; 0 of the matched call unmatched code')
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
        self.assertEqual(out.stdout.splitlines()[-1], 'matched 1 of 1; fitted 0; waiting 0; 0 of the matched call unmatched code')
        self.assertEqual([r[0] for r in read_rows(self.status)], [TOTAL])

    def test_regression_fails_and_is_named(self):
        self.gate(self.ref, self.same)
        out = self.gate(self.ref, self.changed)
        self.assertEqual(out.returncode, 1)
        self.assertIn('REGRESSED\tshop/cart.Total', out.stdout)
        self.assertEqual(out.stdout.splitlines()[-1],
                         'matched 2 of 2; fitted 0; waiting 0; 0 of the matched call unmatched code (status file unchanged, not written)')
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
        self.assertEqual(lines[-3:], ['scored 1 functions',
                                      'globals: 0 functions likely wrong, 0 bystanders, 0 undecided in 0 '
                                      'conflicts (partial run: unscored functions not checked)',
                                      'matched 2 of 3; fitted 1; waiting 0; 0 of the matched call unmatched code'])
        self.assertIn('MATCH\t' + TOTAL, lines)        # one STATUS<TAB>name line per scored function
        self.assertLess(lines.index('MATCH\t' + TOTAL), len(lines) - 3)
        rows = read_rows(self.status)
        self.assertIn(['main.main', 'shop/cmd/till/main.go', 'DIFF', '1', '2025-05-05', ''], rows)
        self.assertIn(['shop/other.Kept', 'shop/other/o.go', 'MATCH', '0', '2025-01-01', ''], rows)
        self.assertIn([TOTAL, 'shop/cart/cart.go', 'MATCH', '0', '2026-01-01', ''], rows)

    def test_full_gate_prints_no_function_lines(self):
        out = self.gate(self.ref, self.same)
        self.assertNotIn('MATCH\t' + TOTAL, out.stdout.splitlines())

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
                         'matched 1 of 1; fitted 0; waiting 0; 0 of the matched call unmatched code (status file unchanged, not written)')
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
        self.assertIn('; fitted 1;', out.stdout.splitlines()[-1])
        self.assertEqual(read_rows(self.status)[0][3], '1')

    # waits: what a function waits on

    def test_waits_names_an_unmatched_callee(self):
        out = self.gate(self.ref, self.changed)
        self.assertEqual(out.returncode, 0, out.stderr)
        rows = {r[0]: r for r in read_rows(self.status)}
        self.assertEqual(rows['main.main'][2:3] + rows['main.main'][5:], ['MATCH', TOTAL])
        self.assertEqual(rows[TOTAL][2:3] + rows[TOTAL][5:], ['SIZE', ''])
        self.assertEqual(out.stdout.splitlines()[-1],
                         'matched 1 of 2; fitted 0; waiting 0; 1 of the matched call unmatched code')
        out = self.gate(self.ref, self.same, extra=['--accept-regressions'])
        self.assertEqual(read_rows(self.status)[1][5], '')      # Total matches again
        self.assertTrue(out.stdout.splitlines()[-1].endswith('; 0 of the matched call unmatched code'))

    def test_only_refreshes_the_waits_of_unscored_rows(self):
        out = self.gate(self.ref, self.changed)
        self.assertEqual({r[0]: r[5] for r in read_rows(self.status)}['main.main'], TOTAL)
        out = self.gate(self.ref, self.same, extra=['--only', 'cart'])
        self.assertEqual(out.returncode, 0, out.stderr)
        rows = {r[0]: r for r in read_rows(self.status)}
        self.assertEqual(rows['main.main'][5], '')          # not scored, but Total matches now
        self.assertEqual(rows[TOTAL][2], 'MATCH')
        self.assertEqual(gogatex.gocmpx.calls_many(gobin.load(self.ref), ['main.main', TOTAL]),
                         {'main.main': gogatex.gocmpx.compare(
                             gobin.load(self.ref), gobin.load(self.same), 'main.main').calls,
                          TOTAL: ()})

    def test_old_five_column_file_reads_with_empty_waits(self):
        with open(self.status, 'w') as f:
            f.write('name\tfile\tstatus\tfitted\tsince\n'
                    'main.main\tshop/cmd/till/main.go\tMATCH\t0\t2025-01-01\n'
                    'shop/other.Kept\tshop/other/o.go\tMATCH\t0\t2025-01-01\n')
        out = self.gate(self.ref, self.same, extra=['--only', r'^shop/cart\.'])
        self.assertEqual(out.returncode, 0, out.stderr)
        with open(self.status) as f:
            self.assertEqual(f.readline(), 'name\tfile\tstatus\tfitted\tsince\twaits\n')
        rows = read_rows(self.status)
        self.assertTrue(all(len(r) == 6 for r in rows), rows)
        self.assertIn(['shop/other.Kept', 'shop/other/o.go', 'MATCH', '0', '2025-01-01', ''], rows)

    def test_a_six_column_header_with_five_fields_is_a_tool_error(self):
        with open(self.status, 'w') as f:
            f.write('name\tfile\tstatus\tfitted\tsince\twaits\n'
                    'main.main\tshop/cmd/till/main.go\tMATCH\t0\t2025-01-01\n')
        out = self.gate(self.ref, self.same)
        self.assertEqual(out.returncode, 2)
        self.assertIn(':2:', out.stderr)


VARS = """package cart

var countA, countB int

//go:noinline
func BumpA() int { countA++; return countA }

//go:noinline
func BumpB() int { %s++; return %s }
"""
CALLS_BUMPS = ('fmt.Println(cart.Total(items))',
               'fmt.Println(cart.Total(items), cart.BumpA(), cart.BumpB())')
MOVER = """package cart

var aaa1, aaa2, aaa3 int

//go:noinline
func Pad() int { aaa1++; aaa2++; aaa3++; println("words that move the read-only data"); return aaa2 }

func init() { Pad() }
"""


class Globals(Base):
    """References to writable globals, which the comparator masks, are checked for consistency
    across the run."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        main = {'cmd/till/main.go': CALLS_BUMPS}
        cls.vars_ref = cls.build('vars-ref', None, dict(main, **{
            'cart/vars.go': VARS % ('countB', 'countB')}))
        cls.vars_one = cls.build('vars-one', None, dict(main, **{
            'cart/vars.go': VARS % ('countA', 'countA')}))
        cls.vars_moved = cls.build('vars-moved', None, dict(main, **{
            'cart/vars.go': VARS % ('countB', 'countB'), 'cart/aaa.go': MOVER}))

    def test_two_globals_merged_into_one_are_reported(self):
        out = self.gate(self.vars_ref, self.vars_one)
        self.assertEqual(out.returncode, 0, out.stderr)
        rows = {r[0]: r[2] for r in read_rows(self.status)}
        self.assertEqual((rows['shop/cart.BumpA'], rows['shop/cart.BumpB']), ('MATCH', 'MATCH'))
        lines = out.stdout.splitlines()
        flagged = [l.split('\t') for l in lines if l.startswith('GLOBAL\t')]
        self.assertEqual({f[1] for f in flagged}, {'shop/cart.BumpA', 'shop/cart.BumpB'})
        self.assertTrue(all(len(f) == 3 and f[2].startswith('+0x') for f in flagged), flagged)
        detail = next(f[2] for f in flagged if f[1] == 'shop/cart.BumpB')
        self.assertRegex(detail, r'^\+0x[0-9a-f]+: 0x[0-9a-f]+ -> (0x[0-9a-f]+) \(2 operands in 1 '
                                 r'function\) conflicts with 0x[0-9a-f]+ -> \1 \(2 operands in 1 function\)$')
        self.assertNotIn('1 operands', out.stdout)
        self.assertFalse(any(l.startswith('GLOBAL-BYSTANDERS') for l in lines))   # a tie
        self.assertEqual(lines[-2], 'globals: 0 functions likely wrong, 0 bystanders, 2 undecided in 1 conflicts')
        self.assertTrue(lines[-1].startswith('matched '))

    def test_equal_sources_in_a_moved_layout_report_none(self):
        ref, cand = gobin.load(self.vars_ref), gobin.load(self.vars_moved)
        import gocmpx
        r = gocmpx.compare(ref, cand, 'shop/cart.BumpA')
        self.assertEqual(r.status, 'MATCH', r)
        self.assertTrue(all(ra != ca for _, ra, ca in r.pairs), r.pairs)   # the globals moved
        out = self.gate(self.vars_ref, self.vars_moved)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn('GLOBAL\t', out.stdout)
        self.assertIn('globals: 0 functions likely wrong, 0 bystanders, 0 undecided in 0 conflicts\n', out.stdout)


def fake_compare(table):
    """A stand-in for gocmpx.compare_many: table is {name: (status, [callee, ...][, pairs])}."""
    import gocmpx

    def compare_many(ref, cand, names):
        return [gocmpx.Result(table[n][0], n, '', None, tuple(sorted(table[n][1])),
                              tuple(table[n][2]) if len(table[n]) > 2 else ())
                for n in names]
    return compare_many


def fake_calls(table):
    """A stand-in for gocmpx.calls_many: the callees of table (see fake_compare)."""
    def calls_many(ref, names):
        return {n: tuple(sorted(table[n][1])) for n in names if n in table}
    return calls_many


class StubBase(unittest.TestCase):
    """Stub binaries and a stub comparator."""

    def setUp(self):
        self.work = tempfile.mkdtemp(prefix='gogatex-stub-')
        self.status = os.path.join(self.work, 'status.tsv')

    def tearDown(self):
        shutil.rmtree(self.work, ignore_errors=True)

    def write(self, *rows):
        with open(self.status, 'w') as f:
            f.write('name\tfile\tstatus\tfitted\tsince\twaits\n')
            for r in rows:
                f.write('\t'.join(r) + '\n')

    def run_main(self, names, table, extra=(), cand_extra=(), cand_missing=(), starts=None,
                 inl=None, src=None):
        """Run gogatex.main on stub binaries holding names (all selected, file shop/a.go, the
        n-th on line n unless starts {name: line} says otherwise) and returns (exit status,
        stdout lines). inl is {name: [gobin.Inl]}, the reference's inlining trees; src is
        {path: text}, a source tree written under the work directory and passed as --src."""
        import contextlib
        import io
        starts = starts or {}
        funcs = [gobin.Func(n, 2 * i, 2 * i + 1, 'shop/a.go', starts.get(n, i + 1), 0)
                 for i, n in enumerate(names)]
        cand = [f for f in funcs if f.name not in cand_missing] + \
            [gobin.Func(n, 1000 + i, 1001 + i, 'lib/x.go', 1, 0) for i, n in enumerate(cand_extra)]
        bins = {'r': StubRef(funcs, inl), 'c': StubRef(cand)}
        extra = list(extra)
        if src is not None:
            root = os.path.join(self.work, 'src')
            for rel, text in src.items():
                os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
                with open(os.path.join(root, rel), 'w') as f:
                    f.write(text)
            extra += ['--src', root]
        real = gogatex.gobin.load, gogatex.gocmpx.compare_many, gogatex.gocmpx.calls_many
        gogatex.gobin.load = lambda path: bins[path]
        gogatex.gocmpx.compare_many = fake_compare(table)
        gogatex.gocmpx.calls_many = fake_calls(table)
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                code = gogatex.main(['gogatex', 'r', 'c', self.status, '--prefix', 'shop/',
                                     '--today', '2026-01-01'] + extra)
        finally:
            gogatex.gobin.load, gogatex.gocmpx.compare_many, gogatex.gocmpx.calls_many = real
        return code, out.getvalue().splitlines()

    def rows(self):
        return {r[0]: r for r in read_rows(self.status)}


class Stubbed(StubBase):
    """The waits column, with stubs."""

    def test_separator_survives_spaces_commas_and_semicolons(self):
        odd = 'shop/T[go.shape.struct { X int; Y string },go.shape.int].M'
        pct = 'shop/b%c'
        code, lines = self.run_main(['shop/a', odd, pct], {
            'shop/a': ('MATCH', [odd, pct]), odd: ('SIZE', []), pct: ('DIFF', [])})
        self.assertEqual(code, 0, lines)
        waits = self.rows()['shop/a'][5]
        self.assertEqual(waits, 'shop/T[go.shape.struct%20{%20X%20int;%20Y%20string%20},'
                                'go.shape.int].M shop/b%25c')
        self.assertEqual(gogatex.split_waits(waits), [odd, pct])
        self.assertEqual(gogatex.split_waits(''), [])
        self.assertEqual(gogatex.join_waits([]), '')
        for names in ([odd], [odd, pct, 'shop/c'], ['a%20b', 'x y']):
            self.assertEqual(gogatex.split_waits(gogatex.join_waits(names)), sorted(names))
        self.assertEqual(lines[-1], 'matched 1 of 3; fitted 0; waiting 0; 1 of the matched call unmatched code')

    def test_waits_holds_unmatched_first_party_and_missing_callees_only(self):
        code, lines = self.run_main(['shop/a', 'shop/b', 'shop/c'], {
            'shop/a': ('MATCH', ['shop/b', 'shop/c', 'lib.Here', 'lib.Gone']),
            'shop/b': ('MATCH', []), 'shop/c': ('INLINE', ['shop/a'])},
            cand_extra=['lib.Here'])
        self.assertEqual(code, 0, lines)
        rows = self.rows()
        self.assertEqual(rows['shop/a'][5], 'lib.Gone shop/c')
        self.assertEqual(rows['shop/c'][5], '')               # waits on a, which matches
        self.assertEqual(rows['shop/b'][5], '')

    def test_a_dup_callee_is_waited_on(self):
        names = ['shop/a', 'shop/d', 'shop/d']
        code, lines = self.run_main(names, {'shop/a': ('MATCH', ['shop/d'])})
        self.assertEqual(self.rows()['shop/a'][5], 'shop/d')

    def test_only_recomputes_waits_of_every_row(self):
        self.write(('shop/a', 'shop/a.go', 'MATCH', '0', '2025-01-01', 'shop/gone'),
                   ('shop/b', 'shop/a.go', 'SIZE', '0', '2025-01-01', ''),
                   ('shop/c', 'shop/a.go', 'MATCH', '0', '2025-01-01', ''))
        table = {'shop/a': ('MATCH', []), 'shop/b': ('MATCH', []),
                 'shop/c': ('MATCH', ['shop/b'])}
        code, lines = self.run_main(['shop/a', 'shop/b', 'shop/c'], table,
                                    extra=['--only', '^shop/c$'])
        self.assertEqual(code, 0, lines)
        rows = self.rows()
        self.assertEqual(rows['shop/c'][5], 'shop/b')         # b's status taken from the file
        self.assertEqual(rows['shop/a'][5], '')               # unscored, recomputed all the same
        self.assertEqual(rows['shop/a'][2:5], ['MATCH', '0', '2025-01-01'])   # but not scored
        self.assertEqual(lines[-1], 'matched 2 of 3; fitted 0; waiting 0; 1 of the matched call unmatched code')
        code, lines = self.run_main(['shop/a', 'shop/b', 'shop/c'], table,
                                    extra=['--only', '^shop/b$'])
        self.assertEqual(self.rows()['shop/c'][5], '')        # b MATCH now; c not scored
        self.assertEqual(lines[-1], 'matched 3 of 3; fitted 0; waiting 0; 0 of the matched call unmatched code')

    def test_only_recomputes_waits_from_the_reference_calls(self):
        self.write(('shop/a', 'shop/a.go', 'MATCH', '0', '2025-01-01', ''),
                   ('shop/b', 'shop/a.go', 'MATCH', '0', '2025-01-01', ''))
        table = {'shop/a': ('MATCH', ['shop/b', 'lib.Gone']), 'shop/b': ('DIFF', [])}
        code, lines = self.run_main(['shop/a', 'shop/b'], table,
                                    extra=['--only', '^shop/b$', '--accept-regressions'])
        self.assertEqual(code, 0, lines)
        self.assertEqual(self.rows()['shop/a'][5], 'lib.Gone shop/b')

    # the waiting marker

    WAITING_SRC = ('package a\n'
                   '// waits: b and c\n'
                   'func a() {}\n'
                   '\n'
                   'func b() {} // waits: x\n'
                   '// waits: not directly above\n'
                   '\n'
                   'func c() {}\n'
                   '// waits:\n'
                   'func d() {}\n')
    WAITING_STARTS = {'shop/a': 3, 'shop/b': 5, 'shop/c': 8, 'shop/d': 10}
    WAITING_TABLE = {'shop/a': ('SIZE', ['shop/c']), 'shop/b': ('DIFF', []),
                     'shop/c': ('DIFF', []), 'shop/d': ('MATCH', [])}

    def test_waiting_marker_counts_and_prints_rescore_and_wait_done(self):
        code, lines = self.run_main(sorted(self.WAITING_TABLE), self.WAITING_TABLE,
                                    starts=self.WAITING_STARTS,
                                    src={'shop/a.go': self.WAITING_SRC})
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[-1], 'matched 1 of 4; fitted 0; waiting 2; 0 of the matched call '
                                    'unmatched code')
        self.assertIn('RESCORE\tshop/b', lines)               # marked, waits on nothing
        self.assertIn('WAIT-DONE\tshop/d', lines)             # marked and MATCH
        self.assertFalse(any(l.endswith('\tshop/a') or l.endswith('\tshop/c') for l in lines),
                         lines)                               # a still waits on c; c unmarked
        self.assertLess(lines.index('RESCORE\tshop/b'), len(lines) - 2)
        self.assertEqual(self.rows()['shop/a'][5], 'shop/c')

    def test_waiting_marker_needs_src(self):
        code, lines = self.run_main(sorted(self.WAITING_TABLE), self.WAITING_TABLE,
                                    starts=self.WAITING_STARTS)
        self.assertEqual(lines[-1], 'matched 1 of 4; fitted 0; waiting 0; 0 of the matched call '
                                    'unmatched code')
        self.assertFalse(any(l.startswith(('RESCORE', 'WAIT-DONE')) for l in lines), lines)

    def test_waiting_marker_counts_unscored_rows_under_only(self):
        self.write(('shop/b', 'shop/a.go', 'DIFF', '0', '2025-01-01', ''))
        code, lines = self.run_main(sorted(self.WAITING_TABLE), self.WAITING_TABLE,
                                    starts=self.WAITING_STARTS,
                                    src={'shop/a.go': self.WAITING_SRC},
                                    extra=['--only', '^shop/d$'])
        self.assertEqual(code, 0, lines)
        self.assertEqual(lines[-1], 'matched 1 of 2; fitted 0; waiting 1; 0 of the matched call '
                                    'unmatched code')
        self.assertIn('RESCORE\tshop/b', lines)
        self.assertIn('WAIT-DONE\tshop/d', lines)

    def test_waiting_marker_does_not_excuse_a_regression(self):
        self.write(('shop/d', 'shop/a.go', 'MATCH', '0', '2025-01-01', ''))
        table = dict(self.WAITING_TABLE, **{'shop/d': ('DIFF', [])})
        code, lines = self.run_main(sorted(table), table, starts=self.WAITING_STARTS,
                                    src={'shop/a.go': self.WAITING_SRC})
        self.assertEqual(code, 1, lines)
        self.assertIn('REGRESSED\tshop/d', lines)

    # waits names inline-only callees whose source is still a stub

    INLINE_SRC = {
        'shop/a.go': 'package a\nfunc a() {}\nfunc b() {}\n',
        'shop/x/x.go': ('package x\n'
                        '\n'
                        'func helper() { panic("todo") } // skel:stub inline-only signature unknown\n'
                        '\n'
                        'func done() int { return 1 }\n'
                        'func plain() { panic("todo") } // skel:stub\n'),
        'lib/y.go': 'package y\nfunc Lib() { panic("todo") } // skel:stub\n',
    }

    def inlined(self):
        def inl(name, file, line):
            return gobin.Inl(name, 'shop/a.go', 2, -1, [(0, 1)], line, file)
        return {'shop/a': [inl('shop/x.helper', 'shop/x/x.go', 3), inl('shop/x.done', 'shop/x/x.go', 5),
                           inl('shop/x.plain', 'shop/x/x.go', 6), inl('lib/y.Lib', 'lib/y.go', 2),
                           inl('shop/b', 'shop/a.go', 3), inl('shop/x.nofile', 'shop/x/none.go', 1)]}

    def test_waits_names_an_inline_only_stub_callee(self):
        table = {'shop/a': ('MATCH', []), 'shop/b': ('DIFF', [])}
        code, lines = self.run_main(['shop/a', 'shop/b'], table, starts={'shop/a': 2, 'shop/b': 3},
                                    inl=self.inlined(), src=self.INLINE_SRC)
        self.assertEqual(code, 0, lines)
        self.assertEqual(self.rows()['shop/a'][5], 'shop/x.helper shop/x.plain')
        self.assertEqual(lines[-1], 'matched 1 of 2; fitted 0; waiting 0; 1 of the matched call '
                                    'unmatched code')

    def test_inline_only_callees_need_src(self):
        table = {'shop/a': ('MATCH', []), 'shop/b': ('DIFF', [])}
        code, lines = self.run_main(['shop/a', 'shop/b'], table, starts={'shop/a': 2, 'shop/b': 3},
                                    inl=self.inlined())
        self.assertEqual(self.rows()['shop/a'][5], '')

    def test_inline_only_callee_selected_by_name_prefix(self):
        table = {'shop/a': ('MATCH', [])}
        src = dict(self.INLINE_SRC, **{'shop/a.go': 'package a\nfunc a() {}\n'})
        inl = {'shop/a': [gobin.Inl('lib/y.Lib', 'shop/a.go', 2, -1, [(0, 1)], 2, 'lib/y.go')]}
        code, lines = self.run_main(['shop/a'], table, starts={'shop/a': 2}, inl=inl, src=src,
                                    extra=['--name-prefix', 'lib/'])
        self.assertEqual(self.rows()['shop/a'][5], 'lib/y.Lib')

    def globals_run(self, table, extra=()):
        code, lines = self.run_main(sorted(table), table, extra=extra)
        self.assertEqual(code, 0, lines)
        return lines

    def test_globals_blame_a_small_minority_and_summarise_the_bystanders(self):
        good = [(0x10, 0x500, 0x900)]
        table = {'shop/f%02d' % i: ('MATCH', [], good) for i in range(1, 11)}
        table['shop/g'] = ('MATCH', [], [(0x20, 0x600, 0x900), (0x28, 0x700, 0x980)])
        table['shop/h'] = ('MATCH', [], [(0x8, 0x700, 0x980)])          # consistent: no line
        lines = self.globals_run(table)
        glob = [l for l in lines if l.startswith('GLOBAL')]
        self.assertEqual(glob, [
            'GLOBAL\tshop/g\t+0x20: 0x600 -> 0x900 (1 operand in 1 function), but '
            '0x500 -> 0x900 (10 operands in 10 functions)',
            'GLOBAL-BYSTANDERS\t10 functions share the other pairing, first shop/f01+0x10'])
        self.assertEqual(lines[-2], 'globals: 1 functions likely wrong, 10 bystanders, '
                                    '0 undecided in 1 conflicts')

    def test_globals_close_counts_name_no_culprit(self):
        good = [(0x10, 0x500, 0x900)]
        table = {'shop/f%d' % i: ('MATCH', [], good) for i in range(1, 4)}
        table['shop/g'] = ('MATCH', [], [(0x20, 0x600, 0x900)])
        lines = self.globals_run(table)
        glob = [l for l in lines if l.startswith('GLOBAL')]
        mine = '0x500 -> 0x900 (3 operands in 3 functions)'
        theirs = '0x600 -> 0x900 (1 operand in 1 function)'
        self.assertEqual(glob, [
            'GLOBAL\tshop/f1\t+0x10: %s conflicts with %s' % (mine, theirs),
            'GLOBAL\tshop/f2\t+0x10: %s conflicts with %s' % (mine, theirs),
            'GLOBAL\tshop/f3\t+0x10: %s conflicts with %s' % (mine, theirs),
            'GLOBAL\tshop/g\t+0x20: %s conflicts with %s' % (theirs, mine)])
        self.assertEqual(lines[-2], 'globals: 0 functions likely wrong, 0 bystanders, '
                                    '4 undecided in 1 conflicts')
        table = {'shop/f%d' % i: ('MATCH', [], good) for i in range(1, 10)}
        table['shop/g'] = ('MATCH', [], [(0x20, 0x600, 0x900)])         # 9 to 1: still close
        lines = self.globals_run(table)
        self.assertEqual(lines[-2], 'globals: 0 functions likely wrong, 0 bystanders, '
                                    '10 undecided in 1 conflicts')
        self.assertFalse(any(l.startswith('GLOBAL-BYSTANDERS') for l in lines))

    def test_globals_tie_prints_both_sides(self):
        lines = self.globals_run({'shop/a': ('MATCH', [], [(0x4, 0x500, 0x900)]),
                                  'shop/b': ('MATCH', [], [(0x8, 0x600, 0x900)])})
        glob = [l for l in lines if l.startswith('GLOBAL')]
        self.assertEqual([l.split('\t')[1] for l in glob], ['shop/a', 'shop/b'])
        self.assertIn('conflicts with 0x600 -> 0x900 (1 operand in 1 function)', glob[0])
        self.assertEqual(lines[-2], 'globals: 0 functions likely wrong, 0 bystanders, '
                                    '2 undecided in 1 conflicts')

    def test_globals_count_conflicts_and_say_when_partial(self):
        table = {'shop/a': ('MATCH', [], [(0x4, 0x500, 0x900), (0xc, 0x510, 0x910)]),
                 'shop/b': ('MATCH', [], [(0x8, 0x600, 0x900)]),
                 'shop/c': ('MATCH', [], [(0x8, 0x510, 0x920)]),
                 'shop/d': ('MATCH', [], [(0x8, 0x510, 0x910)]),
                 'shop/e': ('MATCH', [], [(0x8, 0x510, 0x910)])}
        lines = self.globals_run(table)
        self.assertEqual(lines[-2], 'globals: 0 functions likely wrong, 0 bystanders, '
                                    '5 undecided in 2 conflicts')
        self.assertIn('GLOBAL\tshop/c\t+0x8: 0x510 -> 0x920 (1 operand in 1 function) conflicts '
                      'with 0x510 -> 0x910 (3 operands in 3 functions)', lines)
        lines = self.globals_run(table, extra=['--only', 'shop/'])
        self.assertEqual(lines[-2], 'globals: 0 functions likely wrong, 0 bystanders, '
                                    '5 undecided in 2 conflicts'
                                    ' (partial run: unscored functions not checked)')


class AcceptFrom(StubBase):
    """--accept-regressions-from NAME: a regression whose old waits named NAME, now MATCH."""

    def old(self, *extra):
        self.write(('shop/a', 'shop/a.go', 'MATCH', '0', '2025-01-01', 'shop/b'),
                   ('shop/b', 'shop/a.go', 'SIZE', '0', '2025-01-01', ''), *extra)
        with open(self.status, 'rb') as f:
            return f.read()

    def unchanged(self, before):
        with open(self.status, 'rb') as f:
            self.assertEqual(f.read(), before)

    def test_accepted_when_the_callee_matches_and_was_waited_on(self):
        self.old()
        code, lines = self.run_main(['shop/a', 'shop/b'], {
            'shop/a': ('SIZE', ['shop/b']), 'shop/b': ('MATCH', [])},
            extra=['--accept-regressions-from', 'shop/b'])
        self.assertEqual(code, 0, lines)
        self.assertIn('ACCEPTED\tshop/a\tcaused by shop/b\tSIZE', lines)
        self.assertFalse(any(l.startswith('REGRESSED') for l in lines), lines)
        rows = self.rows()
        self.assertEqual(rows['shop/a'][2:5], ['SIZE', '0', '2026-01-01'])
        self.assertEqual(rows['shop/b'][2], 'MATCH')
        self.assertTrue(lines[-1].startswith('matched 1 of 2;'), lines)

    def test_refused_when_the_callee_is_not_match(self):
        before = self.old()
        code, lines = self.run_main(['shop/a', 'shop/b'], {
            'shop/a': ('SIZE', ['shop/b']), 'shop/b': ('DIFF', [])},
            extra=['--accept-regressions-from', 'shop/b'])
        self.assertEqual(code, 1)
        self.assertIn('REGRESSED\tshop/a', lines)
        self.assertFalse(any(l.startswith('ACCEPTED') for l in lines), lines)
        self.unchanged(before)

    def test_refused_for_a_function_that_did_not_wait_on_it(self):
        before = self.old(('shop/c', 'shop/a.go', 'MATCH', '0', '2025-01-01', ''),
                          ('shop/d', 'shop/a.go', 'MATCH', '0', '2025-01-01', 'shop/x'))
        table = {'shop/a': ('SIZE', ['shop/b']), 'shop/b': ('MATCH', []),
                 'shop/c': ('DIFF', ['shop/b']), 'shop/d': ('DIFF', ['shop/b'])}
        names = ['shop/a', 'shop/b', 'shop/c', 'shop/d']
        code, lines = self.run_main(names, table, extra=['--accept-regressions-from', 'shop/b'])
        self.assertEqual(code, 1)
        self.assertIn('WOULD-ACCEPT\tshop/a\tcaused by shop/b\tSIZE', lines)   # not applied
        self.assertFalse(any(l.startswith('ACCEPTED') for l in lines), lines)
        self.assertIn('REGRESSED\tshop/c', lines)     # empty old waits
        self.assertIn('REGRESSED\tshop/d', lines)     # waited on another function
        self.assertNotIn('REGRESSED\tshop/a', lines)
        self.assertTrue(lines[-1].endswith('(status file unchanged, not written)'), lines)
        self.unchanged(before)                        # written only when nothing is refused
        code, lines = self.run_main(names, table, extra=['--accept-regressions-from', 'shop/b',
                                                         '--accept-regressions-from', 'shop/x'])
        self.assertEqual(code, 1)                     # shop/x is not MATCH: not in this run at all
        self.assertIn('REGRESSED\tshop/d', lines)

    def test_repeatable_and_names_with_spaces(self):
        odd = 'shop/T[go.shape.struct { X int; Y string }].M'
        self.write(('shop/a', 'shop/a.go', 'MATCH', '0', '2025-01-01', gogatex.join_waits([odd])),
                   ('shop/c', 'shop/a.go', 'MATCH', '0', '2025-01-01', 'shop/b'),
                   ('shop/b', 'shop/a.go', 'SIZE', '0', '2025-01-01', ''),
                   (odd, 'shop/a.go', 'SIZE', '0', '2025-01-01', ''))
        code, lines = self.run_main(['shop/a', 'shop/c', 'shop/b', odd], {
            'shop/a': ('DIFF', [odd]), 'shop/c': ('INLINE', ['shop/b']),
            'shop/b': ('MATCH', []), odd: ('MATCH', [])},
            extra=['--accept-regressions-from', odd, '--accept-regressions-from', 'shop/b'])
        self.assertEqual(code, 0, lines)
        self.assertIn('ACCEPTED\tshop/a\tcaused by %s\tDIFF' % odd, lines)
        self.assertIn('ACCEPTED\tshop/c\tcaused by shop/b\tINLINE', lines)

    def test_only_takes_an_unscored_callee_from_the_file(self):
        self.write(('shop/a', 'shop/a.go', 'MATCH', '0', '2025-01-01', 'shop/b'),
                   ('shop/b', 'shop/a.go', 'MATCH', '0', '2025-01-01', ''))
        table = {'shop/a': ('SIZE', ['shop/b']), 'shop/b': ('SIZE', [])}
        code, lines = self.run_main(['shop/a', 'shop/b'], table,
                                    extra=['--only', '^shop/a$', '--accept-regressions-from', 'shop/b'])
        self.assertEqual(code, 0, lines)
        self.assertIn('ACCEPTED\tshop/a\tcaused by shop/b\tSIZE', lines)
        before = self.old()                           # b SIZE in the file: refused
        code, lines = self.run_main(['shop/a', 'shop/b'], {'shop/a': ('SIZE', ['shop/b']),
                                                           'shop/b': ('MATCH', [])},
                                    extra=['--only', '^shop/a$', '--accept-regressions-from', 'shop/b'])
        self.assertEqual(code, 1)
        self.assertIn('REGRESSED\tshop/a', lines)
        self.unchanged(before)

    def test_missing_or_dup_is_not_caused_by_a_callee(self):
        for now, names in (('MISSING', ['shop/a', 'shop/b']), ('DUP', ['shop/a', 'shop/a', 'shop/b'])):
            before = self.old()
            code, lines = self.run_main(names, {'shop/a': (now, ['shop/b']),
                                                'shop/b': ('MATCH', [])},
                                        extra=['--accept-regressions-from', 'shop/b'])
            self.assertEqual(code, 1, (now, lines))
            self.assertIn('REGRESSED\tshop/a', lines)
            self.assertFalse(any('ACCEPT' in l for l in lines), lines)
            self.unchanged(before)

    def test_only_refuses_a_callee_no_longer_in_the_reference(self):
        self.write(('shop/a', 'shop/a.go', 'MATCH', '0', '2025-01-01', 'shop/b'),
                   ('shop/b', 'shop/a.go', 'MATCH', '0', '2025-01-01', ''))   # a stale row
        with open(self.status, 'rb') as f:
            before = f.read()
        code, lines = self.run_main(['shop/a'], {'shop/a': ('SIZE', ['shop/b'])},
                                    extra=['--only', '^shop/a$', '--accept-regressions-from', 'shop/b'])
        self.assertEqual(code, 1, lines)
        self.assertIn('REGRESSED\tshop/a', lines)
        self.unchanged(before)

    def test_a_row_gone_from_the_result_is_refused(self):
        before = self.old(('shop/gone', 'shop/a.go', 'MATCH', '0', '2025-01-01', 'shop/b'))
        code, lines = self.run_main(['shop/a', 'shop/b'], {
            'shop/a': ('MATCH', ['shop/b']), 'shop/b': ('MATCH', [])},
            extra=['--accept-regressions-from', 'shop/b'])
        self.assertEqual(code, 1)
        self.assertIn('REGRESSED\tshop/gone', lines)
        self.unchanged(before)


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
                                                      'matched 0 of 1; fitted 0; waiting 0; 0 of the matched call unmatched code'])

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
