"""Synthetic stage tests: no proprietary compiler or source required."""
import importlib.util
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('watcom_native_runner', Path(__file__).resolve().parents[1]/'tools/runner.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root/'home'; self.home.mkdir()
        (self.home/'BINB').mkdir()
        for tool in ('wcc386.exe', 'wasm.exe'):
            (self.home/'BINB'/tool).write_bytes(b'fixture')
        for tool in ('TASM.EXE', 'MASM.EXE'):
            (self.home/tool).write_bytes(b'fixture')
        self.source = self.root/'input.c'; self.source.write_text('int leaf(int x) { return x+x; }\n')
        self.output = self.root/'leaf.obj'; self.output.write_bytes(b'old')

    def stage(self, status='OK', obj=b'new', check=None):
        def run(home, work, batch, timeout):
            if check:
                check(work, batch)
            if status is not None:
                (work/'STATUS.TXT').write_text(status)
            if obj is not None:
                (work/'OUT.OBJ').write_bytes(obj)
            (work/'COMPILE.LOG').write_bytes(b'diagnostics')
            (work/'ASSEMBLE.LOG').write_bytes(b'diagnostics')
        return patch.object(runner, 'run_batch', side_effect=run)

    def test_compile_stages_headers_and_native_module_name(self):
        inc = self.root/'headers'; inc.mkdir()
        (inc/'api.h').write_text('#define VALUE 7\n'); (inc/'private.c').write_text('no copy')
        def check(work, batch):
            self.assertIn('/nm=LEAF', batch)
            self.assertIn('/i=INC0', batch)
            self.assertEqual((work/'SOURCE.C').read_bytes(), self.source.read_bytes())
            self.assertTrue((work/'INC0/api.h').is_file())
            self.assertFalse((work/'INC0/private.c').exists())
        with self.stage(check=check):
            self.assertEqual(runner.compile_source(self.home,self.source,self.output,['/4r'],include_dirs=[inc],module_name='LEAF'),self.output.resolve())
        self.assertEqual(self.output.read_bytes(), b'new')
        self.assertEqual(self.output.with_suffix('.log').read_bytes(),b'diagnostics')

    def test_missing_failed_or_empty_output_preserves_previous_object(self):
        for status, obj in [('FAILED',b'bad'),(None,b'bad'),('OK',None),('OK',b'')]:
            with self.subTest(status=status,obj=obj), self.stage(status,obj):
                with self.assertRaises(runner.CompilerError):
                    runner.compile_source(self.home,self.source,self.output,[])
                self.assertEqual(self.output.read_bytes(),b'old')

    def test_assemblers_have_independent_commands_and_native_names(self):
        for producer, expected in [('wasm','wasm LEAF.ASM /fo=OUT.OBJ'),('tasm','D:\\TASM.EXE /mx LEAF.ASM,OUT.OBJ'),('masm','D:\\MASM.EXE /mx LEAF.ASM,OUT.OBJ,NUL,NUL;')]:
            def check(work,batch):
                self.assertIn(expected,batch)
                self.assertTrue((work/'LEAF.ASM').is_file())
            with self.subTest(producer=producer),self.stage(check=check):
                runner.assemble_source(self.home,self.source,self.output,['/mx'],source_name='leaf.asm',producer=producer)
        with self.stage(check=lambda work,batch:self.assertIn('wasm SOURCE.ASM',batch)):
            runner.assemble_source(self.home,self.source,self.output,[])

    def test_assembler_stages_explicit_headers_with_producer_switches(self):
        includes=self.root/'includes';includes.mkdir()
        (includes/'values.inc').write_text('VALUE EQU 7\n')
        (includes/'api.h').write_text('header')
        (includes/'ignored.asm').write_text('not staged')
        for producer,switch in [('wasm','/i=INC0'),('tasm','/iINC0'),('masm','/IINC0')]:
            def check(work,batch):
                self.assertIn(switch,batch)
                self.assertTrue((work/'INC0/values.inc').is_file())
                self.assertTrue((work/'INC0/api.h').is_file())
                self.assertFalse((work/'INC0/ignored.asm').exists())
                self.assertTrue((work/'LEAF.ASM').is_file())
            with self.subTest(producer=producer),self.stage(check=check):
                runner.assemble_source(self.home,self.source,self.output,[],producer=producer,source_name='leaf.asm',include_dirs=[includes])

    def test_explicit_include_tree_rejects_linked_headers_and_directories(self):
        includes=self.root/'includes';includes.mkdir()
        outside=self.root/'outside';outside.mkdir()
        (outside/'values.inc').write_text('VALUE EQU 7\n')
        for target in [outside/'values.inc',outside]:
            link=includes/'linked';link.symlink_to(target)
            try:
                for action in (runner.compile_source,runner.assemble_source):
                    with self.subTest(target=target,action=action.__name__),self.assertRaisesRegex(ValueError,'symlink'):
                        action(self.home,self.source,self.output,[],include_dirs=[includes])
            finally:
                link.unlink()
        linked_root=self.root/'linked-root';linked_root.symlink_to(outside)
        with self.assertRaisesRegex(ValueError,'symlink'):
            runner.assemble_source(self.home,self.source,self.output,[],include_dirs=[linked_root])

    def test_assembler_failures_preserve_output(self):
        for producer in ('wasm','tasm','masm'):
            for status,obj in [('FAILED',b'bad'),('OK',None),('OK',b'')]:
                with self.subTest(producer=producer,status=status,obj=obj),self.stage(status,obj):
                    with self.assertRaises(runner.CompilerError):
                        runner.assemble_source(self.home,self.source,self.output,[],producer=producer)
                    self.assertEqual(self.output.read_bytes(),b'old')

    def test_unsafe_names_options_and_output_override_rejected(self):
        for opts in [['/dA=1&echo'],['/dA=%PATH%'],['/fo=D:\\x.obj'],['@args']]:
            with self.subTest(opts=opts),self.assertRaises(ValueError):
                runner.compile_source(self.home,self.source,self.output,opts)
        for name in ['../x.asm','CON.asm','COM1.asm','123.asm','toolongname.asm']:
            with self.subTest(name=name),self.assertRaises(ValueError):
                runner.assemble_source(self.home,self.source,self.output,[],source_name=name)
        for name in ['../x','123','TOOLONGNAME']:
            with self.assertRaises(ValueError):
                runner.compile_source(self.home,self.source,self.output,[],module_name=name)
        with self.assertRaises(ValueError):
            runner.compile_source(self.home,self.source,self.output,['/nm=OTHER'],module_name='LEAF')
        with self.assertRaises(ValueError):
            runner.assemble_source(self.home,self.source,self.output,[],producer='nasm')

    def test_diagnostic_path_cannot_overwrite_requested_object(self):
        output=self.root/'object.log';output.write_bytes(b'keep')
        for action in (runner.compile_source,runner.assemble_source):
            with self.subTest(action=action.__name__),self.stage('FAILED',None):
                with self.assertRaises(ValueError):
                    action(self.home,self.source,output,[])
                self.assertEqual(output.read_bytes(),b'keep')

    def test_binw_layout(self):
        (self.home/'BINB').rename(self.home/'BINW')
        self.assertEqual(runner.compiler_bin(self.home),'BINW')
        with self.stage(check=lambda work,batch:self.assertIn('D:\\BINW',batch)):
            runner.assemble_source(self.home,self.source,self.output,[])

    def test_batch_timeout_and_emulator_error(self):
        with patch.dict('os.environ',{'EXHUME_DOSBOX_X':'fake-dosbox'}):
            with patch.object(runner.subprocess,'run',side_effect=subprocess.TimeoutExpired('fake',1)):
                with self.assertRaises(TimeoutError):
                    runner.run_batch(self.home,self.root,'@echo off\r\n',1)
            with patch.object(runner.subprocess,'run',return_value=subprocess.CompletedProcess([],2,b'',b'broken')):
                with self.assertRaisesRegex(runner.CompilerError,'exit 2.*broken'):
                    runner.run_batch(self.home,self.root,'@echo off\r\n',1)

    def test_timeout_keeps_existing_output(self):
        with patch.object(runner,'run_batch',side_effect=TimeoutError('limit')):
            with self.assertRaises(TimeoutError):
                runner.compile_source(self.home,self.source,self.output,[])
        self.assertEqual(self.output.read_bytes(),b'old')

if __name__ == '__main__':
    unittest.main()
