"""Exhume's shared match.py, merge.py and gate.py dispatch a golang-1.8.0 project to this profile."""
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest

EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class DispatchTest(unittest.TestCase):
    def run_tool(self, tool, *args):
        d = tempfile.mkdtemp(prefix='exhume-go18-dispatch-')
        p = os.path.join(d, 'exhume.toml')
        with open(p, 'w') as f:
            f.write(textwrap.dedent('''
                [project]
                name = "t"
                [toolchain]
                profile = "golang-180"
            '''))
        return subprocess.run(
            [sys.executable, os.path.join(EXHUME, 'tools', tool), '--config', p] + list(args),
            capture_output=True, text=True,
        )

    def test_match_hands_over(self):
        r = self.run_tool('match.py')
        self.assertEqual(r.returncode, 0)
        self.assertIn('Match one Go 1.8.0 unit/package', r.stdout + r.stderr)

    def test_merge_hands_over(self):
        r = self.run_tool('merge.py')
        self.assertEqual(r.returncode, 0)
        self.assertIn('Accept a matched Go 1.8.0 unit', r.stdout + r.stderr)

    def test_gate_hands_over(self):
        r = self.run_tool('gate.py', 'check', '--help')
        self.assertEqual(r.returncode, 0)
        self.assertIn('The whole-project gate for a Go 1.8.0 binary', r.stdout + r.stderr)
        r = self.run_tool('gate.py', 'game')
        self.assertIn('not a command for a linked image', r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
