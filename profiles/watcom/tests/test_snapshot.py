from pathlib import Path
from unittest.mock import patch
import importlib,sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[3];sys.path.insert(0,str(ROOT/'tools'))
class SnapshotTests(unittest.TestCase):
 def api(self):return importlib.import_module('verified_snapshot')
 def test_retained_raw_is_parsed_and_copy_rejects_later_change(self):
  p=self.api()
  with tempfile.TemporaryDirectory()as d:
   source=Path(d)/'model.json';source.write_text('{"value":17}');s=p.capture(source);source.write_text('{"value":18}')
   self.assertEqual(s.json(),{'value':17})
   with self.assertRaises(ValueError):p.copy_verified(s,Path(d)/'copy.json')
 def test_expected_digest_rejects_wrong_initial_bytes(self):
  with tempfile.TemporaryDirectory()as d:
   path=Path(d)/'input';path.write_bytes(b'own fixture')
   with self.assertRaises(ValueError):self.api().capture(path,'0'*64)
 def test_namespace_extra_file_rejects(self):
  p=self.api()
  with tempfile.TemporaryDirectory()as d:
   root=Path(d);(root/'a').write_bytes(b'a');snap=p.capture_tree(root);(root/'b').write_bytes(b'b')
   with self.assertRaises(ValueError):p.check_tree(root,snap)
 def test_output_mutation_during_publication_removes_receipt(self):
  p=self.api()
  with tempfile.TemporaryDirectory()as d:
   root=Path(d);out=root/'output';out.write_bytes(b'validated');snap=p.capture(out);receipt=root/'receipt.json';original=p.os.replace
   def race(a,b):
    original(a,b)
    if Path(b)==receipt:out.write_bytes(b'changed')
   with patch.object(p.os,'replace',side_effect=race),self.assertRaises(ValueError):p.publish_json(receipt,{'ok':True},[snap])
   self.assertFalse(receipt.exists())
 def test_receipt_cannot_replace_validated_input(self):
  p=self.api()
  with tempfile.TemporaryDirectory()as d:
   source=Path(d)/'source.json';source.write_bytes(b'original');snapshot=p.capture(source)
   with self.assertRaisesRegex(ValueError,'overlaps'):p.publish_json(source,{'ok':True},[snapshot])
   self.assertEqual(source.read_bytes(),b'original')
 def test_successful_publication_and_snapshot_are_same_bytes(self):
  p=self.api()
  with tempfile.TemporaryDirectory()as d:
   root=Path(d);out=root/'a';out.write_bytes(b'validated');receipt=root/'r.json';s=p.publish_json(receipt,{'ok':True},[p.capture(out)])
   self.assertEqual(s.raw,receipt.read_bytes());self.assertEqual(s.json(),{'ok':True})
if __name__=='__main__':unittest.main()
