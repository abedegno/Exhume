"""Public Exhume commands and full module proofs use original synthetic data."""
from pathlib import Path
import sys,subprocess,tempfile,json,hashlib,unittest,os,types
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[3];sys.path[:0]=[str(ROOT/'tools'),str(ROOT/'profiles/watcom/tools')]
import config
from profiles.watcom.tests.synthetic_omf import object_bytes,linked_image
class WatcomDispatch(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);(self.root/'src').mkdir();(self.root/'src/own.c').write_text('int own(int x) { return x+17; }\n');(self.root/'target.le').write_bytes(linked_image());self.cfg_path=self.root/'exhume.toml';self.cfg_path.write_text('[project]\nname="own"\n[toolchain]\nprofile="watcom"\nhome="tc"\n[binary]\nimage="target.le"\n[watcom]\nmodules="modules.json"\n');self.spec={'schema':1,'modules':{'src/own.c':{'placements':{'1':[1,0],'2':[2,0]},'symbols':{'own_external':[2,65552]},'group_frames':{'1':[2,0]}}}};self.write_spec()
 def tearDown(self):self.temp.cleanup()
 def write_spec(self):(self.root/'modules.json').write_text(json.dumps(self.spec))
 def api(self):
  import wproject
  return wproject
 def test_profile_and_distinct_names(self):
  cfg=config.Config(str(self.cfg_path));self.assertEqual(cfg.object_model,'omf32-le');self.assertEqual(cfg.mangle('own'),'own_');self.assertEqual(cfg.mangle('own',kind='data'),'_own');self.assertEqual(cfg.mangle('x'*50),'x'*50+'_')
 def test_borland_default_names_unchanged(self):
  p=self.root/'old.toml';p.write_text('[project]\nname="old"\n');cfg=config.Config(str(p));self.assertEqual(cfg.mangle('x'*40),'_'+'x'*32)
 def test_public_cli_dispatch_help(self):
  for tool,args in [('match.py',['--help']),('verify.py',['--help']),('build.py',['--help']),('merge.py',['--help']),('gate.py',['check','--help'])]:
   result=subprocess.run([sys.executable,str(ROOT/'tools'/tool),'--config',str(self.cfg_path),*args],capture_output=True,text=True)
   with self.subTest(tool=tool):self.assertEqual(result.returncode,0,result.stdout+result.stderr);self.assertIn('Watcom',result.stdout+result.stderr)
 def test_own_complete_module_and_wrong_callee(self):
  p=self.api();cfg=config.Config(str(self.cfg_path));obj=self.root/'own.obj';obj.write_bytes(object_bytes());proof=p.verify_source(cfg,'src/own.c',object_path=obj);self.assertTrue(proof['verified']);self.assertEqual(proof['verified_fixups'],3);self.assertFalse(proof['whole_file_identity'])
  self.spec['modules']['src/own.c']['symbols']['own_external']=[2,65553];self.write_spec()
  with self.assertRaises(ValueError):p.verify_source(cfg,'src/own.c',object_path=obj)
 def test_build_fresh_and_reject_failed_compile_without_stale_object(self):
  p=self.api();cfg=config.Config(str(self.cfg_path))
  def compile_ok(home,source,output,options,**kwargs):Path(output).parent.mkdir(parents=True,exist_ok=True);Path(output).write_bytes(object_bytes());return Path(output)
  with patch.object(p.runner,'compile_source',side_effect=compile_ok):proof=p.verify_source(cfg,'src/own.c',fresh=True)
  self.assertTrue(proof['fresh_source_build']);obj=Path(proof['object']);self.assertTrue(obj.exists())
  with patch.object(p.runner,'compile_source',side_effect=RuntimeError('compiler failed')),self.assertRaises(RuntimeError):p.verify_source(cfg,'src/own.c',fresh=True)
  self.assertFalse(obj.exists())
 def test_manifest_escape_or_duplicate_target_input_rejects(self):
  p=self.api();cfg=config.Config(str(self.cfg_path));self.spec['modules']['../outside.c']=self.spec['modules'].pop('src/own.c');self.write_spec()
  with self.assertRaises(ValueError):p.read_project(cfg)
 def test_gate_rejects_output_changed_after_exact_comparison(self):
  import wcli
  p=self.api();self.spec['link']={'command':['own-link'],'output':'build/linked.le'};self.write_spec();cfg=config.Config(str(self.cfg_path));output=self.root/'build/linked.le'
  def compile_ok(home,source,obj,options,**kwargs):Path(obj).parent.mkdir(parents=True,exist_ok=True);Path(obj).write_bytes(object_bytes());return Path(obj)
  def link_ok(*a,**kw):output.parent.mkdir(parents=True,exist_ok=True);output.write_bytes(linked_image());return types.SimpleNamespace(returncode=0,stdout='',stderr='')
  original=p.compare_file
  def changed(*a,**kw):
   proof=original(*a,**kw);raw=bytearray(output.read_bytes());raw[0x200]^=1;output.write_bytes(raw);return proof
  with patch.object(p.runner,'compile_source',side_effect=compile_ok),patch.object(wcli.subprocess,'run',side_effect=link_ok),patch.object(p,'compare_file',side_effect=changed):self.assertEqual(wcli.main(cfg,['check'],'gate'),1)
  self.assertFalse((self.root/'build/watcom/gate.json').exists())
 def test_failed_rebuild_invalidates_acceptance_and_module_proof(self):
  import wcli
  p=self.api();cfg=config.Config(str(self.cfg_path))
  def compile_ok(home,source,obj,options,**kwargs):Path(obj).write_bytes(object_bytes());return Path(obj)
  with patch.object(p.runner,'compile_source',side_effect=compile_ok):self.assertEqual(wcli.main(cfg,['--all'],'merge'),0)
  accepted=self.root/'build/watcom/accepted.json';proof=p.object_path(cfg,'src/own.c').with_suffix('.proof.json')
  self.assertTrue(accepted.exists());self.assertTrue(proof.exists())
  with patch.object(p.runner,'compile_source',side_effect=RuntimeError('failed')):self.assertEqual(wcli.main(cfg,['--all'],'merge'),1)
  self.assertFalse(accepted.exists());self.assertFalse(proof.exists())
 def test_later_compile_cannot_change_earlier_build_output(self):
  import wcli
  p=self.api();(self.root/'src/second.c').write_text('int second;');self.spec['modules']['src/second.c']=self.spec['modules']['src/own.c'].copy();self.write_spec();cfg=config.Config(str(self.cfg_path))
  def compile_mutates(home,source,obj,options,**kwargs):
   Path(obj).write_bytes(object_bytes())
   if source.name=='second.c':p.object_path(cfg,'src/own.c').write_bytes(b'changed')
   return Path(obj)
  report=self.root/'build/report.json'
  with patch.object(p.runner,'compile_source',side_effect=compile_mutates):self.assertEqual(wcli.main(cfg,['--all','--report',str(report)],'build'),1)
  self.assertFalse(report.exists())
 def test_manifest_requires_typed_schema_and_includes(self):
  p=self.api();cfg=config.Config(str(self.cfg_path))
  self.spec['schema']=True;self.write_spec()
  with self.assertRaises(ValueError):p.read_project(cfg)
  self.spec['schema']=1;self.spec['modules']['src/own.c']['include']='headers';self.write_spec()
  with self.assertRaises(ValueError):p.read_project(cfg)
 def test_added_header_during_compile_rejects_receipt(self):
  p=self.api();headers=self.root/'headers';headers.mkdir();self.spec['modules']['src/own.c']['include']=['headers'];self.write_spec();cfg=config.Config(str(self.cfg_path))
  def changed(home,source,obj,options,**kwargs):
   Path(obj).write_bytes(object_bytes());(headers/'new.h').write_text('int changed;');return Path(obj)
  with patch.object(p.runner,'compile_source',side_effect=changed),self.assertRaisesRegex(ValueError,'namespace changed'):p.build_source(cfg,'src/own.c')
  self.assertFalse(p.object_path(cfg,'src/own.c').with_suffix('.json').exists())
 def test_config_change_after_parse_cannot_be_recaptured(self):
  p=self.api();cfg=config.Config(str(self.cfg_path));self.cfg_path.write_text(self.cfg_path.read_text()+'\n# changed after parse\n')
  with self.assertRaises(ValueError):p.read_project(cfg)
 def test_exact_file_gate_keeps_raw_serialization(self):
  p=self.api();cfg=config.Config(str(self.cfg_path));built=self.root/'rebuilt.le';built.write_bytes(linked_image());proof=p.compare_file(cfg,built);self.assertTrue(proof['whole_file_identity']);raw=bytearray(built.read_bytes());raw[0x200]^=1;built.write_bytes(raw)
  with self.assertRaises(ValueError):p.compare_file(cfg,built)
if __name__=='__main__':unittest.main()
