import unittest,hashlib,ssl,json,os
from pathlib import Path
from bundle_filter import remove_blacklisted_roots,CERT
HERE=Path(os.environ['EVA_RUNTIME_NATIVE_INPUTS'])
class BundleFilterTests(unittest.TestCase):
 def setUp(self):
  proof=json.loads((HERE/'ca-blacklist-proof.json').read_text());self.blacklist={r['derSha256'] for r in proof['removedCertificates']};old=HERE/'extracted/ca-certificates-old/data/usr/share/ca-certificates/mozilla';self.removed=[(old/r['name']).read_bytes() for r in proof['removedCertificates']];self.keep=(old/'ACCVRAIZ1.crt').read_bytes()
 def test_removes_exact_two_and_preserves_unknown_and_order_bytes(self):
  bundle=self.keep+b'\n'+self.removed[0]+self.keep+b'\n'+self.removed[1]+b'\n';result,removed=remove_blacklisted_roots(bundle,self.blacklist);self.assertEqual(result,self.keep+b'\n'+self.keep+b'\n'+b'\n');self.assertEqual(set(removed),self.blacklist)
 def test_missing_root_refused(self):
  with self.assertRaises(ValueError):remove_blacklisted_roots(self.keep+self.removed[0],self.blacklist)
 def test_duplicate_target_refused(self):
  with self.assertRaises(ValueError):remove_blacklisted_roots(b''.join(self.removed)*2,self.blacklist)
 def test_malformed_content_refused(self):
  with self.assertRaises(ValueError):remove_blacklisted_roots(b'not certificate'+b''.join(self.removed),self.blacklist)
if __name__=='__main__':unittest.main()
