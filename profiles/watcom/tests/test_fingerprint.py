"""Fingerprint facts from original synthetic MZ and LE inputs."""
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_le import fixture as bound_le, with_fixups
from synthetic_omf import linked_image

spec = importlib.util.spec_from_file_location('watcom_fingerprint', ROOT/'tools/fingerprint.py')
fingerprint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fingerprint)


def mz():
    data = bytearray(512)
    data[:2] = b'MZ'
    struct.pack_into('<HH', data, 2, 0, 1)
    struct.pack_into('<H', data, 8, 4)
    data[0x1e:0x20] = b'\xfb\x30'
    data[64:67] = b'\x55\x8b\xec'
    return data


class FingerprintTests(unittest.TestCase):
    def facts(self, data):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'image.exe'
            path.write_bytes(data)
            output = io.StringIO()
            with redirect_stdout(output):
                fingerprint.main(path)
            return output.getvalue()

    def test_raw_le_reports_objects_pages_entry_and_narrow_signatures(self):
        raw = (linked_image() + b'WATCOM C/C++32 Run-Time system'
               + b'Borland C++ - Copyright 1991 Borland Intl.')
        result = self.facts(raw)
        self.assertIn('LE:', result)
        self.assertIn('2 objects', result)
        self.assertIn('34 pages', result)
        self.assertIn('entry 1:0x0', result)
        self.assertIn('object 2:', result)
        self.assertIn('base 0x30000', result)
        self.assertIn('WATCOM C/C++32 Run-Time system', result)
        self.assertIn('signature strings', result)
        self.assertIn('only narrow', result)
        self.assertNotIn('DGROUP:', result)
        self.assertNotIn('linker marker', result)

    def test_nested_dos_bound_le_uses_validated_inner_container(self):
        result = self.facts(bound_le())
        self.assertIn('LE:', result)
        self.assertIn('header at file 0xc0', result)
        self.assertIn('container origin 0x40', result)
        self.assertIn('1 objects', result)
        self.assertIn('2 pages', result)
        self.assertIn('entry 1:0x0', result)

    def test_plain_mz_keeps_borland_marker_pattern_and_overlay_facts(self):
        data = mz()
        banner = b'Borland C++ - Copyright 1991 Borland Intl.'
        data[68:68+len(banner)] = banner
        data.extend(b'FBOV' + bytes(4) + struct.pack('<II', 528, 1))
        data.extend(struct.pack('<4H', 4, 5, 3, 0))
        result = self.facts(data)
        self.assertIn('MZ: header 0x40 bytes, load module 0x1c0 bytes', result)
        self.assertIn('Borland C++ 2.0', result)
        self.assertIn('DGROUP:', result)
        self.assertIn('1 overlay stubs', result)
        self.assertRegex(result, r'1\s+push bp; mov bp,sp')
        self.assertNotIn('LE:', result)

    def test_bad_le_and_mz_inputs_fail_explicitly(self):
        truncated = bytearray(bound_le())
        truncated[0x40 + 8:0x40 + 10] = b'\xff\xff'
        for data in (b'', b'MZ', b'LE', b'LE\0\0', linked_image()[:-1],
                     bound_le()[:-1], truncated, mz()[:100],
                     mz() + b'FBOV', mz() + b'FBOV' + bytes(4) +
                     struct.pack('<II', 0xfffffff0, 0xffffffff)):
            with self.subTest(size=len(data)), self.assertRaises(ValueError):
                self.facts(data)

    def test_lx_is_not_reported_as_supported_le(self):
        data = bytearray(linked_image())
        data[:2] = b'LX'
        with self.assertRaises(ValueError):
            self.facts(data)

    def test_cli_reports_bad_input_without_python_traceback(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'short.exe'
            path.write_bytes(b'MZ')
            result = subprocess.run([sys.executable, str(ROOT/'tools/fingerprint.py'), str(path)],
                                    cwd=temporary, text=True, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn('Traceback', result.stderr)
            self.assertIn('invalid', result.stderr.lower())

    def test_embedded_le_bytes_do_not_change_plain_mz_format(self):
        data = mz()
        data[128:132] = b'LE\0\0'
        result = self.facts(data)
        self.assertIn('MZ:', result)
        self.assertNotIn('LE:', result)

    def test_malformed_or_unsupported_le_fixups_cannot_produce_facts(self):
        for record in (b'\x07\x10\0\0\x01\x05', b'\x07\x01\0\0\x01\x05\0'):
            with self.subTest(record=record), self.assertRaises(ValueError):
                self.facts(with_fixups(record))

    def test_cli_reads_le_when_called_outside_checkout(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'protected.le'
            path.write_bytes(linked_image())
            result = subprocess.run([sys.executable, str(ROOT/'tools/fingerprint.py'), str(path)],
                                    cwd=temporary, text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('LE:', result.stdout)
            self.assertIn('2 objects', result.stdout)
