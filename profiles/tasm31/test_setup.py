"""Setup integrity controls use only small synthetic media fixtures."""
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec=importlib.util.spec_from_file_location('tasm_setup',Path(__file__).with_name('setup.py'))
setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)

class SetupTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.archive=self.root/'media.7z'
        self.archive.write_bytes(b'wrong archive');self.destination=self.root/'installed'

    def test_bad_archive_never_publishes(self):
        with self.assertRaisesRegex(ValueError,'archive identity'):
            setup.prepare(self.archive,self.destination)
        self.assertFalse(self.destination.exists())

    def test_existing_destination_is_preserved(self):
        self.destination.mkdir();(self.destination/'keep').write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            setup.prepare(self.archive,self.destination)
        self.assertEqual((self.destination/'keep').read_bytes(),b'keep')

    def test_part_and_tool_integrity_before_publication(self):
        def extract(archive,destination,members):
            for member in members:
                (destination/Path(member).name).write_bytes(b'12345payload')
        for bad_name,message in [('TASM.CA2','part identity'),('TASM.EXE','executable identity')]:
            def digest(path):
                if path.name==bad_name:return 'wrong'
                if path==self.archive.resolve():return setup.ARCHIVE_SHA256
                for _,name,sha in setup.PARTS:
                    if path.name==name:return sha
                return setup.TOOLS[path.name]
            with self.subTest(bad_name=bad_name),patch.object(setup,'extract',side_effect=extract),patch.object(setup,'digest',side_effect=digest):
                with self.assertRaisesRegex(ValueError,message):
                    setup.prepare(self.archive,self.destination)
                self.assertFalse(self.destination.exists())

if __name__=='__main__':unittest.main()
