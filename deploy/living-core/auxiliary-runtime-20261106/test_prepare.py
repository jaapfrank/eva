from pathlib import Path
import hashlib,importlib.util,json,tempfile,unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('public_prepare',Path(__file__).with_name('prepare.py'));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Bounds(unittest.TestCase):
 def test_exact_source_patch(self):
  with tempfile.TemporaryDirectory()as d:
   d=Path(d);source=d/'source';source.write_text('before\n');p=d/'change.patch';p.write_text('--- source\n+++ source\n@@ -1 +1 @@\n-before\n+after\n');m.apply_patch(source,p,m.sha(b'before\n'),m.sha(b'after\n'));self.assertEqual(source.read_bytes(),b'after\n')
 def test_wrong_baseline_rejected_before_patch(self):
  with tempfile.TemporaryDirectory()as d:
   d=Path(d);source=d/'source';source.write_text('unexpected');p=d/'change.patch';p.write_text('invalid')
   with self.assertRaisesRegex(ValueError,'baseline source'):m.apply_patch(source,p,'0'*64,'1'*64)
   self.assertEqual(source.read_text(),'unexpected')
 def test_wrong_patch_output_rejected(self):
  with tempfile.TemporaryDirectory()as d:
   d=Path(d);source=d/'source';source.write_text('before\n');p=d/'change.patch';p.write_text('--- source\n+++ source\n@@ -1 +1 @@\n-before\n+after\n')
   with self.assertRaisesRegex(ValueError,'patched source'):m.apply_patch(source,p,m.sha(b'before\n'),'0'*64)
 def test_existing_output_not_overwritten(self):
  with tempfile.TemporaryDirectory()as d:
   out=Path(d);sentinel=out/'receipt';sentinel.write_text('preserve')
   with self.assertRaisesRegex(ValueError,'new output'):m.prepare(out)
   self.assertEqual(sentinel.read_text(),'preserve')
 def test_cached_wrong_wheel_rejected_before_docker(self):
  with tempfile.TemporaryDirectory()as d:
   d=Path(d);here=d/'recipe';here.mkdir();(here/'build-inputs.json').write_text(json.dumps({'roles':{}}));family=here/'bridge-document';family.mkdir();(family/'requirements.lock').write_text('x==1');(family/'wheelhouse-manifest.json').write_text(json.dumps({'wheels':[{'file':'x.whl','sha256':'0'*64,'bytes':5,'officialUrl':'https://files.pythonhosted.org/unused'}]}));cache=d/'cache/bridge-document/wheelhouse';cache.mkdir(parents=True);(cache/'x.whl').write_bytes(b'wrong')
   with patch.object(m,'HERE',here),patch.object(m.subprocess,'check_output',side_effect=AssertionError('Docker must not run')):
    with self.assertRaisesRegex(ValueError,'wheel bytes mismatch'):m.prepare(d/'out',d/'cache')
 def test_changed_external_script_same_manifest_rejected_before_execution(self):
  with tempfile.TemporaryDirectory()as d:
   d=Path(d);entries={}
   for name in m.NATIVE_FILES:
    p=d/name;p.write_bytes(('original '+name).encode());entries[name]=m.sha(p.read_bytes())
   manifest=d/'artifact-manifest.json';manifest.write_text(json.dumps({'files':entries}));pin=m.sha(manifest.read_bytes());(d/'prepare_native.py').write_text('UNREVIEWED MUTATION')
   (d/'build-inputs.json').write_text(json.dumps({'roles':{},'nativeRecipe':'.','nativeRecipeArtifactManifestSha256':pin}))
   with patch.object(m,'HERE',d),patch.object(m.subprocess,'run',side_effect=AssertionError('must not build or execute changed native source')),patch.object(m.subprocess,'check_output',side_effect=AssertionError('must not invoke Docker')):
    with self.assertRaisesRegex(ValueError,'Native recipe source changed'):m.prepare(d/'new-output',build_images=True)

if __name__=='__main__':unittest.main()
