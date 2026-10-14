"""Exhume's shared match.py and merge.py hand a linked-image project to the armcc5 tools."""
import os, subprocess, sys, tempfile, textwrap, unittest
EXHUME = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


class DispatchTest(unittest.TestCase):
    def run_tool(self, tool, *args):
        d = tempfile.mkdtemp(); p = os.path.join(d, 'exhume.toml')
        with open(p, 'w') as f:
            f.write(textwrap.dedent('''
                [project]
                name = "t"
                [toolchain]
                profile = "armcc5"
            '''))
        return subprocess.run([sys.executable, os.path.join(EXHUME, 'tools', tool), '--config', p] + list(args),
                              capture_output=True, text=True)

    def test_match_hands_over(self):
        r = self.run_tool('match.py')
        self.assertIn('Match one source against a linked image', r.stdout + r.stderr)

    def test_merge_hands_over(self):
        r = self.run_tool('merge.py')
        self.assertIn('Accept a source for a linked image', r.stdout + r.stderr)

    def test_gate_hands_over(self):
        r = self.run_tool('gate.py', 'check', '--help')
        self.assertIn('The whole-image gate for a linked image', r.stdout + r.stderr)
        r = self.run_tool('gate.py', 'game', '--help')
        self.assertIn('The modded build for a linked image', r.stdout + r.stderr)
        r = self.run_tool('gate.py', 'symbols')
        self.assertIn('not a command for a linked image', r.stdout + r.stderr)


if __name__ == '__main__':
    unittest.main()
