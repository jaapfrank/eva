import asyncio,base64,json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch,AsyncMock
from hindsight_api.engine.providers.codex_llm import CodexLLM
import hindsight_api.engine.providers.codex_llm as module
class ColdStart(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.root.chmod(0o700);self.auth=self.root/'auth.json';self.lock=self.root/'auth.lock';self.data={'auth_mode':'chatgpt','tokens':{'access_token':'synthetic-A','account_id':'synthetic-sensitive-account'}};self.write();self.lock.write_text('');self.lock.chmod(0o600)
 def tearDown(self):self.tmp.cleanup()
 def write(self):self.auth.write_text(json.dumps(self.data));self.auth.chmod(0o600)
 def construct(self):return CodexLLM('openai-codex','','','synthetic-model',codex_home=str(self.root))
 def forced(self,rejected=False):
  with patch.dict(os.environ,{'EVA_HINDSIGHT_CODEX_ACCESS_ONLY':'1'}),patch.object(CodexLLM,'_load_codex_auth',side_effect=AssertionError('legacy credential read forbidden'))as legacy,patch.object(CodexLLM,'_load_codex_refresh_token',side_effect=AssertionError('legacy refresh read forbidden'))as refresh,patch.object(module.logger,'info')as log,patch.object(module.CodexAuthManager,'refresh_tokens',new_callable=AsyncMock)as delegation:
   if rejected:
    with self.assertRaises(module.ProviderUnavailableError):self.construct()
   else:
    instance=self.construct();self.assertEqual(instance._auth_manager.access_token,'synthetic-A');self.assertEqual(instance._auth_manager.account_id,'synthetic-sensitive-account');self.assertIsNone(instance._auth_manager.refresh_token);asyncio.run(instance.cleanup())
   legacy.assert_not_called();refresh.assert_not_called();log.assert_not_called();delegation.assert_not_called()
 def test_forced_valid_cold_start_uses_only_owned_snapshot(self):self.forced()
 def test_unsafe_mode_never_legacy_reads_or_logs(self):self.auth.chmod(0o644);self.forced(True)
 def test_unavailable_marker_never_legacy_reads_or_logs(self):self.auth.write_text(json.dumps({'contract':'EvaModelAccessLeaseUnavailable/v1','state':'source_unavailable'}));self.forced(True)
 def test_missing_auth_never_legacy_reads_or_logs(self):self.auth.unlink();self.forced(True)
 def test_symlink_never_legacy_reads_or_logs(self):other=self.root/'other';self.auth.rename(other);self.auth.symlink_to(other);self.forced(True)
 def test_hardlink_never_legacy_reads_or_logs(self):os.link(self.auth,self.root/'other');self.forced(True)
 def test_unsafe_parent_never_legacy_reads_or_logs(self):self.root.chmod(0o750);self.forced(True)
 def test_unsafe_lock_never_legacy_reads_or_logs(self):self.lock.chmod(0o644);self.forced(True)
 def test_oversize_never_legacy_reads_or_logs(self):self.auth.write_bytes(b'x'*65537);self.forced(True)
 def test_deep_json_never_legacy_reads_or_logs(self):self.auth.write_text('['*2000+']'*2000);self.forced(True)
 def test_refresh_escalation_never_legacy_reads_or_logs(self):self.data['tokens']['refresh_token']='synthetic-forbidden';self.write();self.forced(True)
 def test_expired_jwt_never_legacy_reads_or_logs(self):self.data['tokens']['access_token']='synthetic.'+base64.urlsafe_b64encode(json.dumps({'exp':int(time.time())-1}).encode()).decode().rstrip('=')+'.synthetic';self.write();self.forced(True)
 def test_full_oauth_flag_zero_preserves_legacy_loading(self):
  self.data['tokens']['refresh_token']='synthetic-original-refresh';self.write();calls=[];original=CodexLLM._load_codex_auth;original_refresh=CodexLLM._load_codex_refresh_token
  def load(instance):calls.append('access');return original(instance)
  def refresh(instance):calls.append('refresh');return original_refresh(instance)
  with patch.dict(os.environ,{'EVA_HINDSIGHT_CODEX_ACCESS_ONLY':'0'}),patch.object(CodexLLM,'_load_codex_auth',load),patch.object(CodexLLM,'_load_codex_refresh_token',refresh),patch.object(module.logger,'info')as log:
   instance=self.construct();self.assertEqual(calls,['access','refresh']);self.assertFalse(instance._external_access_only);self.assertEqual(instance._auth_manager.access_token,'synthetic-A');self.assertEqual(instance._auth_manager.refresh_token,'synthetic-original-refresh');self.assertTrue(log.called);asyncio.run(instance.cleanup())
if __name__=='__main__':unittest.main()
