"""Optional original-source smoke tests; select locally installed DOS tools via env.

EXHUME_WATCOM_HOME (or WATCOM_HOME), EXHUME_WATCOM_105_HOME,
EXHUME_TASM_HOME and EXHUME_MASM_HOME enable their tests independently.
EXHUME_DOSBOX_X selects the emulator; otherwise dosbox-x must be on PATH.
"""
import importlib.util
import os
from pathlib import Path
import shutil
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('native_runner_smoke',Path(__file__).resolve().parents[1]/'tools/runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def selected(variable, fallback=None):
    value=os.environ.get(variable) or (os.environ.get(fallback) if fallback else None)
    if not value or not Path(value).is_dir():
        return None
    if not (os.environ.get('EXHUME_DOSBOX_X') or shutil.which('dosbox-x')):
        return None
    return Path(value)


class NativeToolsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.work=Path(self.temp.name)

    def compiler(self, variable, fallback=None):
        home=selected(variable,fallback)
        if home is None:
            self.skipTest(f'set {variable} and install DOSBox-X')
        source=self.work/'leaf.c'; output=self.work/'leaf.obj'
        headers=self.work/'headers';headers.mkdir()
        (headers/'factor.h').write_text('#define FACTOR 2\n')
        source.write_text('#include "factor.h"\nint twice(int x) { return x*FACTOR; }\n')
        runner.compile_source(home,source,output,['/5r','/s','/otexan'],include_dirs=[headers],module_name='TWICE')
        original=output.read_bytes()
        self.assertEqual(original[0],0x80)
        self.assertIn(b'twice_',original)
        self.assertIn(b'\x01\xc0\xc3',original)
        source.write_text('int broken( {\n')
        with self.assertRaises(runner.CompilerError):
            runner.compile_source(home,source,output,['/5r'])
        self.assertEqual(output.read_bytes(),original)
        source.write_text('int unused;\n')
        with self.assertRaises(runner.CompilerError):
            runner.compile_source(home,source,output,['/?'])
        self.assertEqual(output.read_bytes(),original)

    def test_watcom_ga(self):
        self.compiler('EXHUME_WATCOM_HOME','WATCOM_HOME')

    def test_watcom_105(self):
        self.compiler('EXHUME_WATCOM_105_HOME')

    def assembly(self, variable, producer, text, options):
        home=selected(variable)
        if home is None:
            self.skipTest(f'set {variable} and install DOSBox-X')
        source=self.work/'leaf.asm';output=self.work/'leaf.obj'
        headers=self.work/'headers';headers.mkdir()
        (headers/'values.inc').write_text('RETURN_VALUE EQU 7\n')
        text='INCLUDE values.inc\n'+text.replace('call other_', 'mov eax, RETURN_VALUE\ncall other_')
        source.write_text(text)
        runner.assemble_source(home,source,output,options,producer=producer,source_name='leaf.asm',include_dirs=[headers])
        original=output.read_bytes()
        self.assertEqual(original[0],0x80)
        self.assertIn(b'sample_',original)
        self.assertIn(b'other_',original)
        self.assertIn(b'\xb8\x07\x00\x00\x00',original)
        source.write_text('THIS IS INVALID ASSEMBLY\nEND\n')
        with self.assertRaises(runner.CompilerError):
            runner.assemble_source(home,source,output,options,producer=producer)
        self.assertEqual(output.read_bytes(),original)

    def test_wasm(self):
        self.assembly('EXHUME_WATCOM_HOME','wasm',
            ".386\n_TEXT SEGMENT PUBLIC USE32 'CODE'\nPUBLIC sample_\nEXTRN other_:NEAR\nsample_: call other_\nret\n_TEXT ENDS\nEND\n",[])

    def test_tasm(self):
        self.assembly('EXHUME_TASM_HOME','tasm',
            'IDEAL\nP386\nMODEL FLAT\nCODESEG\nMASM\nPUBLIC sample_\nEXTRN other_:PROC\nsample_ PROC NEAR\ncall other_\nret\nsample_ ENDP\nEND\n',
            ['/t','/p','/mx','/w2'])

    def test_masm(self):
        self.assembly('EXHUME_MASM_HOME','masm',
            ".386P\nCODE SEGMENT PUBLIC USE32 'CODE'\nASSUME CS:CODE\nPUBLIC sample_\nEXTRN other_:NEAR\nsample_: call other_\nret\nCODE ENDS\nEND\n",['/MX'])

    def test_real_batch_timeout(self):
        home=selected('EXHUME_WATCOM_HOME','WATCOM_HOME')
        if home is None:
            self.skipTest('set EXHUME_WATCOM_HOME and install DOSBox-X')
        with self.assertRaises(TimeoutError):
            runner.run_batch(home,self.work,'@echo off\r\n:loop\r\ngoto loop\r\n',0.5)

if __name__ == '__main__':
    unittest.main()
