import hashlib,importlib.util,pathlib,tempfile,unittest
Q=pathlib.Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location('graceful_prepare',Q/'prepare.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m)
class PrepareTests(unittest.TestCase):
 def test_wrong_baseline_rejected_before_patch(self):
  with tempfile.TemporaryDirectory()as t:
   p=pathlib.Path(t)/'x';p.write_text('original\n')
   with self.assertRaises(ValueError):m.patch_file(p,pathlib.Path(t)/'missing.patch','0'*64,'1'*64)
   self.assertEqual(p.read_text(),'original\n')
 def test_patch_result_hash_verified(self):
  with tempfile.TemporaryDirectory()as t:
   p=pathlib.Path(t)/'x';p.write_text('old\n');d=pathlib.Path(t)/'p';d.write_text('--- x\n+++ x\n@@ -1 +1 @@\n-old\n+new\n')
   with self.assertRaises(ValueError):m.patch_file(p,d,m.sha(p),'0'*64)
 def test_exact_patch_succeeds(self):
  with tempfile.TemporaryDirectory()as t:
   p=pathlib.Path(t)/'x';p.write_text('old\n');d=pathlib.Path(t)/'p';d.write_text('--- x\n+++ x\n@@ -1 +1 @@\n-old\n+new\n');m.patch_file(p,d,m.sha(p),hashlib.sha256(b'new\n').hexdigest());self.assertEqual(p.read_bytes(),b'new\n')
 def test_no_service_dispatch(self):
  source=(Q/'prepare.py').read_text();self.assertNotIn("'start'",source);self.assertNotIn("'stop'",source);self.assertNotIn("'kill'",source);self.assertNotIn("'--force'",source)
if __name__=='__main__':unittest.main()
