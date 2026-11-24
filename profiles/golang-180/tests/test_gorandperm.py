"""Unit tests for gorandperm's mutators (no Docker required)."""
import os
import random
import sys
import unittest

TOOLS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools')
sys.path.insert(0, TOOLS)

import gorandperm as g

SRC = '''package main

func cachedSize(name string) (int64, error) {
	now := time.Now()
	props, err := sizeDB.Properties(name)
	if err != nil {
		return measureSize(name)
	}
	if props.Size.MeasureCompleted < now.Unix()+10 {
		return measureSize(name)
	}
	t := `literal ` + name + ` more
!insertmacro MUI_PAGE_INSTFILES
end`
	return props.Size.Size, nil
}
'''


class MutatorTest(unittest.TestCase):
    def test_atoms_skip_assignment_targets_and_strings(self):
        line = '\tprops, err := sizeDB.Properties(name)'
        spans = [line[s:e] for s, e in g.atoms(line)]
        self.assertIn('sizeDB.Properties(name)', spans)
        self.assertNotIn('props', spans)
        self.assertNotIn('err', spans)
        line = '\tlog.Printf("x %s y", v)'
        self.assertEqual([line[s:e] for s, e in g.atoms(line)], ['v'])  # the statement's own call is never wrapped

    def test_pad_and_unpad_roundtrip(self):
        line = '\tif len(x) > 0 {'
        rng = random.Random(0)
        for _ in range(20):
            padded = g.m_pad(line, rng)
            self.assertIn(padded, ('\tif (len(x)+0) > 0 {', '\tif len((x+0)) > 0 {'))
            self.assertEqual(g.m_unpad(padded, rng), line)

    def test_swapcmp_does_not_split_arithmetic(self):
        rng = random.Random(0)
        self.assertIsNone(g.m_swapcmp('\tif a < now.Unix()+10 {', rng))
        self.assertEqual(g.m_swapcmp('\tif err != nil {', rng), '\tif nil != err {')
        self.assertEqual(g.m_swapcmp('\tif a < b {', rng), '\tif b > a {')

    def test_ifinit_and_rettmp(self):
        rng = random.Random(1)
        out = g.m_ifinit('\tif props.Size.MeasureCompleted == 0 {', rng)
        self.assertRegex(out, r'^\tif (v|x|n|tmp) := props\.Size\.MeasureCompleted; \1 == 0 \{$')
        out = g.m_rettmp('\t\treturn measureSize(name)', rng)
        self.assertRegex(out, r'^\t\tr0(, r1)*(, r2)* := measureSize\(name\); return r0')

    def test_raw_string_lines_are_never_mutated(self):
        lines = SRC.split('\n')
        raw = g.raw_string_lines(lines)
        self.assertEqual({lines[i] for i in raw},
                         {'\tt := `literal ` + name + ` more', '!insertmacro MUI_PAGE_INSTFILES', 'end`'})
        rng = random.Random(3)
        for _ in range(300):
            out = g.mutate(SRC, 1, SRC.count('\n') + 1, rng, 2).split('\n')
            for i in raw:
                self.assertEqual(out[i], lines[i])
            self.assertEqual(len(out), len(lines))

    def test_func_lines(self):
        lo, hi = g.func_lines(SRC, r'^main\.cachedSize$')
        self.assertEqual((lo, hi), (3, 16))
        self.assertEqual(g.func_lines(SRC, r'^main\.\(\*T\)\.cachedSize$'), (3, 16))

    def test_atoms_never_inside_literals_or_comments(self):
        cases = {
            '\t\treturn "!error \\"Couldn\'t access x: " + quote(err.Error()) + "\\""': ['quote(err.Error())', 'err.Error()'],
            '\tu := "http://x/" + path // note: y': ['path'],
            "\tif c == '\"' || c == '`' {": ['c', 'c'],
            '\tx := `raw ` + v + ` more`': ['v'],
        }
        for line, want in cases.items():
            self.assertEqual([line[s:e] for s, e in g.atoms(line)], want, line)
        rng = random.Random(5)
        line = '\t\treturn "!error \\"Couldn\'t access x: " + quote(err.Error()) + "\\""'
        lit = '"!error \\"Couldn\'t access x: "'
        for _ in range(200):
            for fn, _w in g.MUTATORS:
                out = fn(line, rng)
                if out:
                    self.assertIn(lit, out)

    def test_blank_never_duplicates_a_call(self):
        rng = random.Random(2)
        for _ in range(100):
            out = g.m_blank('\terr = json.Unmarshal(data, &sizes)', rng)
            self.assertTrue(out is None or "; _ = json" not in out, out)

    def test_edits(self):
        self.assertEqual(g.edits('a\nb\nc', 'a\nB\nc'), [(1, 'B')])


if __name__ == '__main__':
    unittest.main()
