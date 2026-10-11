import os, sys, tempfile, unittest
from types import SimpleNamespace
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tools'))
import armmerge, armmap, armmatch
from armmap import Row


class MergeTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(); os.makedirs(os.path.join(self.root, 'map')); os.makedirs(os.path.join(self.root, 'src'))
        armmap.save(os.path.join(self.root, 'map', 'functions.tsv'), [Row(0x08004120, 0x30, 'sub_08004120', 'code', '')])
        armmap.save(os.path.join(self.root, 'map', 'data.tsv'), [Row(0x20000210, 8, 'g_20000210', 'ram', '')])
        self.src = os.path.join(self.root, 'src', 't.c'); open(self.src, 'w').close()
        self.cfg = SimpleNamespace(root=self.root, map=os.path.join(self.root, 'map'), matched=os.path.join(self.root, 'matched.txt'))

    def fake(self, whole):
        r = armmatch.Result(whole=whole, lines=['-- x'], defined={'timer_tick': 0x08004120, 'g_timers': 0x20000210})
        return lambda cfg, src: r

    def read(self, p):
        with open(p) as f: return f.read()

    def test_merge_renames_and_records(self):
        ok, _ = armmerge.merge(self.cfg, self.src, matcher=self.fake(True))
        self.assertTrue(ok)
        f = armmap.load(os.path.join(self.root, 'map', 'functions.tsv'))
        d = armmap.load(os.path.join(self.root, 'map', 'data.tsv'))
        self.assertEqual((f[0].name, f[0].file), ('timer_tick', 'src/t.c'))
        self.assertEqual((d[0].name, d[0].file), ('g_timers', 'src/t.c'))
        self.assertEqual(self.read(self.cfg.matched), 'src/t.c\n')
        armmerge.merge(self.cfg, self.src, matcher=self.fake(True))
        self.assertEqual(self.read(self.cfg.matched), 'src/t.c\n')     # once only

    def test_no_merge_without_whole_match(self):
        ok, _ = armmerge.merge(self.cfg, self.src, matcher=self.fake(False))
        self.assertFalse(ok)
        self.assertEqual(armmap.load(os.path.join(self.root, 'map', 'functions.tsv'))[0].name, 'sub_08004120')
        self.assertFalse(os.path.exists(self.cfg.matched))


if __name__ == '__main__':
    unittest.main()
