"""Unit tests for gosig.py (result registers of functions, from their callers and return paths).

The fixture module `shop` is built with the host's Go for linux/amd64 as the command cmd/kiosk.
shop/cart/shapes.go holds functions whose results have known shapes; kiosk calls each of them
and uses the results, except Triple, which it calls only through a function value.
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
TOOLS = os.path.join(PROFILE, 'tools')
sys.path.insert(0, TOOLS)
sys.path.insert(0, TESTS_DIR)

import gobin
import gosig
import gotypes
from test_gobin import go_env


class Instructions(unittest.TestCase):
    def use(self, text):
        m, ops = gosig.parse(text)
        return gosig.uses(m, ops)

    def test_a_move_writes_its_destination_and_reads_its_source(self):
        self.assertEqual(self.use('mov\trax, rbx'), (0b10, 0b1, 0))

    def test_a_nil_check_reads_only_the_address(self):
        self.assertEqual(self.use('test\tbyte ptr [rcx], al'), (0b100, 0, 0))

    def test_zeroing_idioms_read_nothing(self):
        self.assertEqual(self.use('xor\teax, eax'), (0, 0b1, 0))
        self.assertEqual(self.use('sbb\tr10, r10'), (0, 1 << 7, 0))

    def test_a_store_reads_its_registers(self):
        self.assertEqual(self.use('mov\tqword ptr [rsp + 0x8], rbx'), (0b10, 0, 0))

    def test_narrow_writes_are_partial(self):
        self.assertEqual(self.use('sete\tal'), (0, 0b1, 0b1))
        x1 = 1 << (gosig.NINT + 1)
        self.assertEqual(self.use('cvtsi2sd\txmm1, rcx'), (0b100, x1, x1))

    def test_an_instruction_it_does_not_know(self):
        self.assertIsNone(self.use('cpuid'))


class Results(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('go'):
            raise unittest.SkipTest('go is not on the PATH')
        cls.tmp = tempfile.mkdtemp(prefix='gosig-test-')
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

    def test_no_result(self):
        self.assertEqual(gosig.results(self.bin, 'shop/cart.Tick'), [])

    def test_one_int(self):
        self.assertEqual(gosig.results(self.bin, 'shop/cart.Square'), ['int'])

    def test_int_and_error(self):
        self.assertEqual(gosig.results(self.bin, 'shop/cart.Parse'), ['int', 'int', 'int'])

    def test_string(self):
        self.assertEqual(gosig.results(self.bin, 'shop/cart.Label'), ['int', 'int'])

    def test_struct_in_registers(self):
        self.assertEqual(gosig.results(self.bin, 'shop/cart.Bounds'), ['int', 'int'])

    def test_function_nobody_calls(self):
        self.assertIsNone(gosig.results(self.bin, 'shop/cart.Triple'))
        self.assertEqual(gosig.evidence(self.bin, 'shop/cart.Triple')['sites'], 0)

    def test_call_sites_are_counted(self):
        self.assertEqual(gosig.evidence(self.bin, 'shop/cart.Parse')['sites'], 1)

    def test_unknown_function(self):
        with self.assertRaises(KeyError):
            gosig.results(self.bin, 'shop/cart.NoSuchThing')

    def test_abi_classes_of_known_results(self):
        t = gotypes.load(self.bin).named('shop/shelf', 'Shelf').ptr_to_this
        stock = next(m for m in t.methods if m.name == 'Stock')
        self.assertEqual(gosig.abi_classes(stock.type.outs), ['int', 'int'])
        self.assertEqual(gosig.classes_mask(['int', 'int', 'float']), 0b11 | 1 << gosig.NINT)

    def test_known_methods_agree_with_the_recovery(self):
        a = gosig.analysis(self.bin, known={})       # nothing known in advance
        for name, mask in gosig.known_methods(self.bin).items():
            got = a.results(a.by_name[name])
            if got is not None:
                self.assertEqual(gosig.classes_mask(got), mask, name)

    def test_command_line(self):
        r = subprocess.run([sys.executable, os.path.join(TOOLS, 'gosig.py'), self.ref,
                            r'^shop/cart\.(Parse|Triple)$'], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, 'shop/cart.Parse\tint int int\t1\nshop/cart.Triple\t?\t0\n')


if __name__ == '__main__':
    unittest.main()
