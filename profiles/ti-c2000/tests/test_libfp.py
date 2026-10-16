"""The runtime-library fingerprint on synthetic members and a synthetic image: relocated bits
masked, the anchor, sections too small to place by their words, constants beside their member,
and comparing two libraries. No toolchain needed."""
import unittest
from . import support  # noqa: F401
import c28tc, c28libfp

S, O = c28tc.Section, c28tc.Obj


def sym(name, sec, value=0, sclass='C_EXT'):
    return dict(name=name, kind='defined', section=sec, sclass=sclass, value=value)


def member(name, sections, symbols):
    return O(name, {s.name: s for s in sections}, symbols)


BASE = 0x080000


def build():
    """A library of four members and an image that holds three of them."""
    # div: 8 words, an LCR to `_helper` at +2 (its target bits masked)
    div = member('div.obj', [S('.text', 8, [0x1111, 0x2222, 0x7640, 0x0000, 0x3333, 0x4444, 0x5555, 0x0006],
                               [(2, 'R_REL22', '_helper')], 'text'),
                             S('.econst', 3, [0x0A0A, 0x0B0B, 0x0C0C], [], 'data')],
                 [sym('_div', '.text'), sym('_tbl', '.econst')])
    # helper: 1 word, an LRETR: fits everywhere by its words, placed from div's call
    helper = member('helper.obj', [S('.text', 1, [0x0006], [], 'text')], [sym('_helper', '.text')])
    # copy: 6 words, a data-page load whose page and offset differ in the image
    copy = member('copy.obj', [S('.text', 6, [0x761F, 0x0000, 0x9200, 0x9601, 0xFF00, 0x0006],
                                [(1, 'R_PARTMS16', '.ebss'), (2, 'R_PARTLS6', '.ebss')], 'text')],
                  [sym('_copy', '.text')])
    # unused: not in the image
    unused = member('unused.obj', [S('.text', 5, [0xDEAD, 0xBEEF, 0xDEAD, 0xBEEF, 0x0006], [], 'text')],
                    [sym('_unused', '.text')])
    w = [0xFFFF] * 64
    w[4:12] = [0x1111, 0x2222, 0x7640 | 0x08, 0x0030, 0x3333, 0x4444, 0x5555, 0x0006]   # div at +4, calls 0x080030
    w[0x30] = 0x0006                                                                     # helper
    w[20:26] = [0x761F, 0x0292, 0x9207, 0x9601, 0xFF00, 0x0006]                          # copy, page and offset relocated
    w[40:43] = [0x0A0A, 0x0B0B, 0x0C0C]                                                  # div's constants
    w[50:53] = [0x0A0A, 0x0B0B, 0x0C0C]                                                  # the same words, elsewhere
    return {'div.obj': div, 'helper.obj': helper, 'copy.obj': copy, 'unused.obj': unused}, c28tc.Image(w, BASE)


class SearchTest(unittest.TestCase):
    def test_masked_words_match(self):
        members, img = build()
        sec = members['copy.obj'].sections['.text']
        idx = c28libfp.index_pairs(img.w)
        self.assertEqual(c28libfp.search(sec.data, c28tc.reloc_masks(sec), img, idx), [BASE + 20])
        # without the masks the relocated words do not fit
        self.assertEqual(c28libfp.search(sec.data, [0] * 6, img, idx), [])

    def test_one_word_fits_many_places(self):
        members, img = build()
        idx = c28libfp.index_pairs(img.w)
        self.assertGreater(len(c28libfp.search([0x0006], [0], img, idx)), 2)


class FingerprintTest(unittest.TestCase):
    def test_places(self):
        members, img = build()
        found = c28libfp.fingerprint(members, img)
        got = {(f['member'], f['section']): (f['addr'], f['how']) for f in found}
        self.assertEqual(got[('div.obj', '.text')], (BASE + 4, 'words'))
        self.assertEqual(got[('copy.obj', '.text')], (BASE + 20, 'words'))
        self.assertEqual(got[('helper.obj', '.text')], (BASE + 0x30, 'called from div.obj'))
        self.assertNotIn(('unused.obj', '.text'), got)
        # div's constants fit twice by their words; neither is beside other constants of div
        self.assertNotIn(('div.obj', '.econst'), got)
        self.assertEqual([f['addr'] for f in found], sorted(f['addr'] for f in found))

    def test_constants_need_their_code(self):
        members, img = build()
        w = list(img.w); w[4] = 0x9999                    # div's code no longer fits
        found = c28libfp.fingerprint(members, c28tc.Image(w, BASE))
        self.assertFalse(any(f['member'] == 'div.obj' for f in found))

    def test_comparing_libraries(self):
        # an older library whose copy() differs in one unrelocated word places one section less
        members, img = build()
        older = dict(members)
        c = members['copy.obj'].sections['.text']
        older['copy.obj'] = member('copy.obj', [S('.text', 6, c.data[:3] + [0x9602] + c.data[4:], c.relocs, 'text')],
                                   members['copy.obj'].symbols)
        n_new = len(c28libfp.fingerprint(members, img))
        n_old = len(c28libfp.fingerprint(older, img))
        self.assertEqual(n_new - n_old, 1)


if __name__ == '__main__':
    unittest.main()
