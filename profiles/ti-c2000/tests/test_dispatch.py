"""Exhume's shared match.py, merge.py and gate.py hand a ti-c2000 project to this profile's tools."""
import os, subprocess, sys, tempfile, textwrap, unittest
from . import support


class DispatchTest(unittest.TestCase):
    def run_tool(self, tool, *args):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'exhume.toml')
        with open(p, 'w') as f:
            f.write(textwrap.dedent('''
                [project]
                name = "t"
                [toolchain]
                profile = "ti-c2000"
                [c2000]
                base = 0x080000
            '''))
        return subprocess.run([sys.executable, os.path.join(support.EXHUME, 'tools', tool), '--config', p] + list(args),
                              capture_output=True, text=True)

    def test_match_hands_over(self):
        r = self.run_tool('match.py')
        self.assertIn('Match one source against a C2000 image', r.stdout + r.stderr)

    def test_gate_hands_over(self):
        r = self.run_tool('gate.py', 'check', '--help')
        self.assertIn('The whole-image gate for a C2000 image', r.stdout + r.stderr)
        r = self.run_tool('gate.py', 'game')
        self.assertIn('not a command for a linked image', r.stdout + r.stderr)

    def test_merge_says_there_is_none(self):
        r = self.run_tool('merge.py', 'src/a.c')
        self.assertIn('no merge step', r.stdout + r.stderr)
        self.assertEqual(r.returncode, 2)


if __name__ == '__main__':
    unittest.main()
