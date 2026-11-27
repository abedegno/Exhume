"""Unit tests for gobin.py, the Go ELF binary reader.

The fixture module `shop` is built with the host's Go for linux/amd64 into a temporary directory,
once with internal and once with external linking. The external build needs a C compiler that
targets linux/amd64 (CC_LINUX_AMD64, x86_64-linux-gnu-gcc, x86_64-linux-musl-gcc or zig); without
one that test is skipped.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROFILE = os.path.dirname(TESTS_DIR)
TOOLS = os.path.join(PROFILE, 'tools')
FIXTURE = os.path.join(TESTS_DIR, 'fixtures', 'shop')
sys.path.insert(0, TOOLS)

import gobin


def go_env(**extra):
    env = dict(os.environ, GOOS='linux', GOARCH='amd64', GOWORK='off', GO111MODULE='on',
               GOFLAGS='')
    env.update(extra)
    return env


def go_build(out, ldflags='', cgo='0', cc=None):
    env = go_env(CGO_ENABLED=cgo)
    if cc:
        env['CC'] = cc
    cmd = ['go', 'build', '-buildvcs=false', '-o', out]
    if ldflags:
        cmd.append('-ldflags=' + ldflags)
    cmd.append('./cmd/till')
    r = subprocess.run(cmd, cwd=FIXTURE, env=env, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('go build failed:\n' + r.stdout + r.stderr)


def amd64_c_compiler():
    """A command usable as CC for linux/amd64, or None."""
    if os.environ.get('CC_LINUX_AMD64'):
        return os.environ['CC_LINUX_AMD64']
    for name in ('x86_64-linux-gnu-gcc', 'x86_64-linux-musl-gcc'):
        if shutil.which(name):
            return name
    if shutil.which('zig'):
        return 'zig cc -target x86_64-linux-gnu'
    return None


class GoBinary(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not shutil.which('go'):
            raise unittest.SkipTest('go is not on the PATH')
        cls.tmp = tempfile.mkdtemp(prefix='gobin-test-')
        cls.internal = os.path.join(cls.tmp, 'till-internal')
        go_build(cls.internal)
        cls.stripped = os.path.join(cls.tmp, 'till-stripped')
        go_build(cls.stripped, ldflags='-s -w')
        cls.cc = amd64_c_compiler()
        cls.external = os.path.join(cls.tmp, 'till-external')
        if cls.cc:
            go_build(cls.external, ldflags='-linkmode=external', cgo='1', cc=cls.cc)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        if self._testMethodName == 'test_external_link_addresses' and not self.cc:
            self.skipTest('no C compiler for linux/amd64 (set CC_LINUX_AMD64), '
                          'so no externally linked build')

    def first_byte_from_objdump(self, name):
        if not shutil.which('objdump'):
            self.skipTest('objdump is not on the PATH')
        r = subprocess.run(['objdump', '-d', '--disassemble-symbols=' + name, self.internal],
                           capture_output=True, text=True, check=True)
        for line in r.stdout.splitlines():
            m = re.match(r'\s*[0-9a-f]+:\s+([0-9a-f]{2})\s', line)
            if m:
                return bytes.fromhex(m.group(1))
        self.fail('objdump printed no code for ' + name)

    def line_of(self, text):
        with open(os.path.join(FIXTURE, 'cart', 'cart.go')) as f:
            for n, line in enumerate(f, 1):
                if text in line:
                    return n
        self.fail(text + ' is not in cart.go')

    def test_function_addresses_point_at_code(self):
        b = gobin.load(self.internal)
        f = next(x for x in b.funcs() if x.name == 'shop/cart.Total')
        self.assertEqual(b.code(f)[:1], self.first_byte_from_objdump('shop/cart.Total'))

    def test_external_link_addresses(self):          # Review focus 1
        b = gobin.load(self.external)
        f = next(x for x in b.funcs() if x.name == 'shop/cart.Total')
        self.assertEqual(b.code(f), gobin.load(self.internal).code(
            next(x for x in gobin.load(self.internal).funcs() if x.name == 'shop/cart.Total')))

    def test_lines_name_the_source_file(self):
        b = gobin.load(self.internal)
        f = next(x for x in b.funcs() if x.name == 'shop/cart.Total')
        self.assertTrue(all(file.endswith('cart/cart.go') for _, file, _ in b.lines(f)))

    def test_inlined_call_is_listed_with_its_call_site(self):
        b = gobin.load(self.internal)
        f = next(x for x in b.funcs() if x.name == 'shop/cart.Total')
        self.assertIn(('shop/cart.price', self.line_of('price(it)')),
                      [(i.callee, i.call_line) for i in b.inltree(f)])

    def test_buildinfo(self):
        self.assertEqual(gobin.load(self.internal).buildinfo()['path'], 'shop/cmd/till')

    # Beyond the brief: the target is stripped, and each way of finding the text base must agree.

    def test_stripped_binary_gives_the_same_functions(self):
        full = {(f.name, f.entry, f.end) for f in gobin.load(self.internal).funcs()}
        bare = {(f.name, f.entry, f.end) for f in gobin.load(self.stripped).funcs()}
        self.assertEqual(full, bare)

    def test_every_way_to_the_text_base_agrees(self):
        b = gobin.load(self.internal)
        self.assertEqual(b._text_from_symbol(), b.text)
        self.assertEqual(b._text_from_moduledata(), b.text)
        self.assertEqual(b._text_from_prologues(), b.text)
        self.assertIsNone(gobin.load(self.stripped)._text_from_symbol())

    def test_function_ends_at_its_last_instruction(self):
        b = gobin.load(self.internal)
        f = next(x for x in b.funcs() if x.name == 'shop/cart.Total')
        self.assertEqual(f.start_line, self.line_of('func Total'))
        self.assertTrue(f.file.endswith('cart/cart.go'))
        self.assertEqual(len(b.code(f)), f.end - f.entry)
        self.assertEqual(b.lines(f)[0][0], 0)
        if shutil.which('objdump'):                        # the symbol's size, without padding
            r = subprocess.run(['objdump', '-t', self.internal], capture_output=True, text=True,
                               check=True)
            sym = next(l.split() for l in r.stdout.splitlines()
                       if l.endswith(' shop/cart.Total'))
            self.assertEqual((int(sym[0], 16), int(sym[-2], 16)), (f.entry, f.end - f.entry))


if __name__ == '__main__':
    unittest.main()
