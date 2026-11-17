"""Hand-built LE files: no commercial executable bytes are used here."""
from pathlib import Path
import struct
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from le import LEImage, LEError


def fixture():
    # A bound MZ at 0x40, whose relative new-header pointer reaches 0xc0.
    data = bytearray(0x454)
    data[:2] = b'MZ'
    data[0x40:0x42] = b'MZ'
    struct.pack_into('<HH',data,0x40+2,0x80,1)  # 128-byte DOS load image
    struct.pack_into('<H',data,0x40+8,4)  # 64-byte DOS header
    struct.pack_into('<I', data, 0x40 + 0x3c, 0x80)
    h = 0xc0
    data[h:h+4] = b'LE\0\0'
    struct.pack_into('<HH', data, h+8, 2, 1)
    for off, value in {20:2, 24:1, 28:0, 32:1, 36:24, 40:16, 44:4,
                       48:12, 64:0xc4, 68:1, 72:0xdc,
                       104:0xe4, 108:0xf0, 112:0xf0, 128:0x400}.items():
        struct.pack_into('<I', data, h+off, value)
    struct.pack_into('<6I', data, h+0xc4, 40, 0x10000, 0x2005, 1, 2, 0)
    data[h+0xdc:h+0xe4] = b'\0\0\1\0\0\0\2\0'
    data[0x440:0x454] = b'abcdefghijklmnopqrst'
    return data


def with_fixups(records, second_page=False):
    data = fixture()
    h = 0xc0
    start = 0 if second_page else len(records)
    struct.pack_into('<3I', data, h+0xe4, 0, start, len(records))
    struct.pack_into('<I', data, h+48, 12+len(records))
    struct.pack_into('<I', data, h+112, 0xf0+len(records))
    data[h+0xf0:h+0xf0+len(records)] = records
    return bytes(data)


class LEReaderTests(unittest.TestCase):
    def test_bound_origin_last_page_and_virtual_zero_fill(self):
        image = LEImage.parse(bytes(fixture()))
        self.assertEqual((image.header_offset, image.origin), (0xc0, 0x40))
        self.assertEqual(image.object_bytes(1), b'abcdefghijklmnopqrst' + bytes(20))
        self.assertEqual(image.file_offset(1, 17), 0x451)
        self.assertIsNone(image.file_offset(1, 21))
        self.assertEqual(image.objects[0].base, 0x10000)

    def test_false_le_signature_is_ignored(self):
        data = fixture()
        data[8:12] = b'LE\0\0'
        self.assertEqual(LEImage.parse(bytes(data)).header_offset, 0xc0)

    def test_ambiguous_images_rejected(self):
        data = fixture()
        data.extend(fixture())
        with self.assertRaisesRegex(LEError, 'ambiguous'):
            LEImage.parse(bytes(data))

    def test_truncated_page_rejected(self):
        with self.assertRaises(LEError):
            LEImage.parse(bytes(fixture()[:-1]))

    def test_huge_object_count_rejected(self):
        data = fixture()
        struct.pack_into('<I', data, 0xc0+68, 0xffffffff)
        with self.assertRaises(LEError):
            LEImage.parse(bytes(data))

    def test_plausible_le_pointer_with_impossible_mz_header_rejected(self):
        data=fixture()
        struct.pack_into('<H',data,0x40+8,0xffff)
        with self.assertRaisesRegex(LEError,'MZ'):
            LEImage.parse(bytes(data))

    def test_mz_relocations_must_fit_header(self):
        data=fixture()
        struct.pack_into('<H',data,0x40+6,100)
        struct.pack_into('<H',data,0x40+0x18,0x1c)
        with self.assertRaisesRegex(LEError,'MZ'):
            LEImage.parse(bytes(data))

    def test_overlapping_object_page_ownership_rejected(self):
        data = fixture()
        struct.pack_into('<I', data, 0xc0+68, 2)
        with self.assertRaises(LEError):
            LEImage.parse(bytes(data))

    def test_zeroed_page_has_no_file_backing(self):
        data = fixture()
        data[0xc0+0xdc:0xc0+0xe0] = b'\0\0\0\3'
        image = LEImage.parse(bytes(data))
        self.assertEqual(image.object_bytes(1), bytes(16) + b'qrst' + bytes(20))
        self.assertIsNone(image.file_offset(1, 1))

    def test_iterated_page_is_explicitly_unsupported(self):
        data = fixture()
        data[0xc0+0xdc+3] = 1
        with self.assertRaisesRegex(LEError, 'page'):
            LEImage.parse(bytes(data))

    def test_out_of_range_object_access_rejected(self):
        image = LEImage.parse(bytes(fixture()))
        for obj, offset in [(0,0), (2,0), (1,-1), (1,40)]:
            with self.assertRaises(LEError):
                image.file_offset(obj, offset)

    def test_internal_32_bit_target_and_signed_cross_page_source(self):
        image = LEImage.parse(with_fixups(b'\x07\x10\xfe\xff\x01\x17\x00\x00\x00', True))
        fixups = image.read_fixups()
        self.assertEqual(len(fixups), 1)
        f = fixups[0]
        self.assertEqual((f.page, f.source_object, f.source_offset, f.width), (1,1,14,4))
        self.assertEqual((f.target_object, f.target_offset, f.target_address), (1,23,0x10017))

    def test_source_list_expands_without_losing_targets(self):
        image = LEImage.parse(with_fixups(b'\x27\x00\x02\x01\x05\x00\x00\x00\x04\x00'))
        fs = image.read_fixups()
        self.assertEqual([f.source_offset for f in fs], [0,4])
        self.assertEqual([f.target_address for f in fs], [0x10005,0x10005])

    def test_16_bit_object_ordinal(self):
        fs = LEImage.parse(with_fixups(b'\x07\x40\x00\x00\x01\x00\x09\x00')).read_fixups()
        self.assertEqual(fs[0].target_address, 0x10009)

    def test_external_fixup_rejected(self):
        image = LEImage.parse(with_fixups(b'\x07\x01\x00\x00\x01\x05\x00'))
        with self.assertRaisesRegex(LEError, 'unsupported'):
            image.read_fixups()

    def test_fixup_record_cannot_read_into_next_page(self):
        image = LEImage.parse(with_fixups(b'\x07\x10\x00\x00\x01\x05'))
        with self.assertRaisesRegex(LEError, 'truncated'):
            image.read_fixups()

    def test_fixup_source_cannot_escape_object(self):
        image = LEImage.parse(with_fixups(b'\x07\x00\xff\xff\x01\x05\x00'))
        with self.assertRaisesRegex(LEError, 'source'):
            image.read_fixups()


if __name__ == '__main__':
    unittest.main()
