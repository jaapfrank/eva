import os,tempfile,unittest,importlib.util,types
from pathlib import Path
from unittest.mock import patch
P=Path(__file__).resolve().parents[1]/'activation_entrypoint.py'
spec=importlib.util.spec_from_file_location('activation_entrypoint',P);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
def dist(name,version):return types.SimpleNamespace(metadata={'Name':name},version=version)
class ProfileTests(unittest.TestCase):
 def setUp(self):self.expected=[dist('hindsight-api-slim','1.2.0'),dist('torch','2.13.0+cpu')]
 def apply(self,values=None):
  with patch('importlib.metadata.distributions',return_value=self.expected) as called:
   m._apply_120_runtime_profile();called.assert_called_once_with(path=[m._VENV_SITE])
 def test_all_known_profiles_normalize_and_keep_ld_library_path(self):
  for value in m._KNOWN_PYTHONPATHS:
   with patch.dict(os.environ,{'PYTHONPATH':value,'LD_LIBRARY_PATH':'/retained/pg/lib'},clear=True):
    self.apply();self.assertEqual(os.environ['PYTHONPATH'],m._VENV_SITE);self.assertEqual(os.environ['LD_LIBRARY_PATH'],'/retained/pg/lib');self.assertEqual(os.environ['HINDSIGHT_API_RERANKER_LOCAL_MODEL'],'cross-encoder/ms-marco-MiniLM-L-6-v2');self.assertEqual(os.environ['HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL'],'BAAI/bge-small-en-v1.5');self.assertEqual(os.environ['EVA_HINDSIGHT_PARALLEL_RERANK_BATCHES'],'0')
 def test_unknown_and_missing_pythonpath_fail_before_mutation(self):
  for value in [None,'/unreviewed',m._VENV_SITE+':/opt/eva-cuda',' /opt/eva-cuda']:
   env={} if value is None else {'PYTHONPATH':value}
   with patch.dict(os.environ,env,clear=True):
    with self.assertRaisesRegex(RuntimeError,'not a reviewed profile'):self.apply()
    self.assertEqual(dict(os.environ),env)
 def test_missing_or_wrong_or_duplicate_distribution_rejected(self):
  for versions in [[],[dist('hindsight-api-slim','0.9.1'),dist('torch','2.13.0+cpu')],self.expected+[dist('torch','2.13.0+cpu')]]:
   with patch.dict(os.environ,{'PYTHONPATH':'/opt/eva-cuda'},clear=True),patch('importlib.metadata.distributions',return_value=versions):
    with self.assertRaises(RuntimeError):m._apply_120_runtime_profile()
    self.assertEqual(os.environ['PYTHONPATH'],'/opt/eva-cuda')
 def test_explicit_model_configuration_is_preserved(self):
  with patch.dict(os.environ,{'PYTHONPATH':'/opt/eva-cuda','HINDSIGHT_API_RERANKER_LOCAL_MODEL':'explicit-owner-model','HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL':'explicit-owner-embedding'},clear=True):
   self.apply();self.assertEqual(os.environ['HINDSIGHT_API_RERANKER_LOCAL_MODEL'],'explicit-owner-model');self.assertEqual(os.environ['HINDSIGHT_API_EMBEDDINGS_LOCAL_MODEL'],'explicit-owner-embedding')
 def secret(self,d):
  p=Path(d)/'dsn';p.write_text('postgresql://synthetic@synthetic/synthetic');p.chmod(0o600);return p
 def test_original_regular_private_file_accepted(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.secret(d)
   with patch.dict(os.environ,{m.FILE_ENV:str(p)},clear=True):self.assertEqual(m._database_url_from_private_file(),'postgresql://synthetic@synthetic/synthetic')
 def test_original_inline_dsn_forbidden(self):
  with patch.dict(os.environ,{m.VALUE_ENV:'postgresql://synthetic@synthetic/synthetic'},clear=True):
   with self.assertRaisesRegex(RuntimeError,'inline.*forbidden'):m._database_url_from_private_file()
 def test_original_mode_symlink_and_multiple_links_forbidden(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.secret(d);link=Path(d)/'symlink';link.symlink_to(p)
   with patch.dict(os.environ,{m.FILE_ENV:str(link)},clear=True):
    with self.assertRaisesRegex(RuntimeError,'regular file'):m._database_url_from_private_file()
   p.chmod(0o644)
   with patch.dict(os.environ,{m.FILE_ENV:str(p)},clear=True):
    with self.assertRaisesRegex(RuntimeError,'ownership or mode'):m._database_url_from_private_file()
   p.chmod(0o600);os.link(p,Path(d)/'hardlink')
   with patch.dict(os.environ,{m.FILE_ENV:str(p)},clear=True):
    with self.assertRaisesRegex(RuntimeError,'link count'):m._database_url_from_private_file()
 def test_main_preserves_secret_read_before_exact_cli_exec(self):
  with tempfile.TemporaryDirectory() as d:
   p=self.secret(d)
   with patch.dict(os.environ,{'PYTHONPATH':'/opt/eva-cuda',m.FILE_ENV:str(p)},clear=True),patch('importlib.metadata.distributions',return_value=self.expected),patch.object(m.os,'execv') as execute:
    m.main();execute.assert_called_once_with('/app/api/.venv/bin/hindsight-api',['/app/api/.venv/bin/hindsight-api']);self.assertEqual(os.environ[m.VALUE_ENV],'postgresql://synthetic@synthetic/synthetic');self.assertEqual(os.environ['PYTHONPATH'],m._VENV_SITE)
if __name__=='__main__':unittest.main()
