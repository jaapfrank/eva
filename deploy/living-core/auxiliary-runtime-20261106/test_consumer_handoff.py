import asyncio,base64,copy,json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from contextlib import asynccontextmanager
from hindsight_api.engine.providers.codex_llm import CodexLLM
from eva.operations.model_credentials import publish_codex_consumer_auth,publish_unavailable_lease

def token(label='A',expiry=None):
 payload={'exp':int(time.time())+3600 if expiry is None else expiry,'synthetic':label}
 return 'synthetic.'+base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip('=')+'.synthetic'

class Response:
 status=200
 def __init__(self,status=200):self.status=status;self.content=self;self.headers={};self.url='https://synthetic.invalid'
 async def iter_any(self):
  events=[{'type':'response.output_text.delta','delta':'synthetic-result'},{'type':'response.completed','response':{'status':'completed','usage':{'input_tokens':2,'output_tokens':3,'total_tokens':5}}}]
  for event in events:yield ('data: '+json.dumps(event)+'\n\n').encode()
  yield b'data: [DONE]\n\n'
 async def text(self):return 'synthetic HTTP rejection'

class Transport:
 def __init__(self):self.calls=[];self.status=200;self.on_send=None
 def get(self):return self
 @asynccontextmanager
 async def post(self,url,**kwargs):
  self.calls.append(copy.deepcopy(kwargs['headers']))
  status=self.status
  if self.on_send:self.on_send()
  yield Response(status)
 async def close(self):pass

class HandoffTests(unittest.IsolatedAsyncioTestCase):
 async def asyncSetUp(self):
  self.uidpatch=patch('eva.operations.model_credentials.MODEL_LEASE_OWNER_UID',os.geteuid());self.gidpatch=patch('eva.operations.model_credentials.CODEX_LEASE_READER_GID',os.getegid());self.uidpatch.start();self.gidpatch.start()
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.owner=self.root/'owner.json';self.directory=self.root/'consumer';self.directory.mkdir(mode=0o700);self.path=self.directory/'auth.json';self.provider=None;self.transport=Transport();self.refresh=[]
  self.write_owner(token('A'));publish_codex_consumer_auth(self.owner,self.path)
  self.environment=patch.dict(os.environ,{'EVA_HINDSIGHT_CODEX_ACCESS_ONLY':'1'});self.environment.start()
 async def asyncTearDown(self):
  if self.provider is not None:await self.provider.cleanup()
  self.environment.stop();self.tmp.cleanup();self.uidpatch.stop();self.gidpatch.stop()
 def write_owner(self,access,refresh=False):
  tokens={'access_token':access,'account_id':'synthetic-account'}
  if refresh:tokens['refresh_token']='synthetic-must-not-escalate'
  self.owner.write_text(json.dumps({'auth_mode':'chatgpt','tokens':tokens}));self.owner.chmod(0o600)
 def make(self):
  self.provider=CodexLLM('openai-codex','','','synthetic-model',codex_home=str(self.directory));self.provider._session=self.transport
  async def no_refresh(*args,**kwargs):self.refresh.append(True);raise AssertionError('No refresh authority')
  self.provider._auth_manager.refresh_tokens=no_refresh
  return self.provider
 async def call(self):return await self.provider.call([{'role':'user','content':'synthetic request'}],max_retries=0)
 async def rejected(self):
  before=len(self.transport.calls)
  with self.assertRaises(RuntimeError):await self.call()
  self.assertEqual(len(self.transport.calls),before);self.assertEqual(self.refresh,[]);self.assertEqual(self.provider.access_token,'');self.assertEqual(self.provider.account_id,'');self.assertIsNone(self.provider.refresh_token)
 async def test_public_call_reloads_rotation_A_to_B_before_each_request(self):
  self.make();a=self.provider.access_token;await self.call();b=token('B');self.write_owner(b);publish_codex_consumer_auth(self.owner,self.path);await self.call()
  self.assertEqual([h['Authorization'] for h in self.transport.calls],['Bearer '+a,'Bearer '+b]);self.assertEqual(self.refresh,[])
 async def test_atomic_unavailable_publication_revokes_cached_token_without_refresh(self):
  self.make();lock_inode=self.path.with_suffix('.lock').stat().st_ino;publish_unavailable_lease(self.path,lease_format='codex-consumer');await self.rejected();self.assertEqual(self.path.with_suffix('.lock').stat().st_ino,lock_inode)
 async def test_explicit_expiry_revokes_cached_token_before_transport(self):
  self.make();value=json.loads(self.path.read_text());value['tokens']['access_token']=token('expired',int(time.time())-1);self.path.write_text(json.dumps(value));await self.rejected()
 async def test_invalid_file_permissions_revoke_before_transport(self):
  self.make();self.path.chmod(0o644);await self.rejected()
 async def test_symlink_file_is_never_followed(self):
  self.make();other=self.root/'foreign.json';self.path.rename(other);self.path.symlink_to(other);await self.rejected()
 async def test_hardlink_file_is_rejected(self):
  self.make();os.link(self.path,self.root/'other-link');await self.rejected()
 async def test_bad_parent_mode_is_not_repaired(self):
  self.make();self.directory.chmod(0o755);await self.rejected();self.assertEqual(self.directory.stat().st_mode&0o777,0o755)
 async def test_lock_symlink_is_not_followed_or_repaired(self):
  self.make();lock=self.path.with_suffix('.lock');lock.unlink();other=self.root/'other-lock';other.write_bytes(b'');other.chmod(0o600);lock.symlink_to(other);await self.rejected();self.assertTrue(lock.is_symlink());self.assertEqual(other.read_bytes(),b'')
 async def test_lock_nonempty_rejects_cached_auth(self):
  self.make();lock=self.path.with_suffix('.lock');lock.write_bytes(b'tampered');await self.rejected();self.assertEqual(lock.read_bytes(),b'tampered')
 async def test_refresh_authority_cannot_escalate_access_only_instance(self):
  self.make();value=json.loads(self.path.read_text());value['tokens']['refresh_token']='synthetic-forbidden';self.path.write_text(json.dumps(value));await self.rejected()
 async def test_cold_start_refresh_token_is_denied_by_profile_flag(self):
  value=json.loads(self.path.read_text());value['tokens']['refresh_token']='synthetic-forbidden';self.path.write_text(json.dumps(value))
  with self.assertRaises(RuntimeError):self.make()
  self.assertEqual(self.transport.calls,[])
 async def test_rotation_between_read_and_snapshot_check_fails_closed(self):
  self.make();original=os.read;published=False
  def read(fd,size):
   nonlocal published
   result=original(fd,size)
   if result and not published:published=True;publish_unavailable_lease(self.path,lease_format='codex-consumer')
   return result
  with patch('os.read',read):await self.rejected()
 async def test_reactive_401_cannot_invoke_refresh_or_retry(self):
  self.make();self.transport.status=401
  with self.assertRaises(RuntimeError):await self.call()
  self.assertEqual(len(self.transport.calls),1);self.assertEqual(self.refresh,[])
 async def test_lock_wrong_mode_rejects_without_repair(self):
  self.make();lock=self.path.with_suffix('.lock');lock.chmod(0o644);await self.rejected();self.assertEqual(lock.stat().st_mode&0o777,0o644)
 async def test_lock_hardlink_rejects_without_repair(self):
  self.make();lock=self.path.with_suffix('.lock');os.link(lock,self.root/'lock-hardlink');await self.rejected();self.assertEqual(lock.stat().st_nlink,2)
 async def test_auth_foreign_owner_rejects(self):
  if os.geteuid()!=0:self.skipTest('Separate root-only synthetic metadata probe')
  self.make();os.chown(self.path,65534,0);await self.rejected()
 async def test_auth_foreign_group_rejects(self):
  if os.geteuid()!=0:self.skipTest('Separate root-only synthetic metadata probe')
  self.make();os.chown(self.path,0,65534);await self.rejected()
 async def test_lock_foreign_owner_rejects(self):
  if os.geteuid()!=0:self.skipTest('Separate root-only synthetic metadata probe')
  self.make();os.chown(self.path.with_suffix('.lock'),65534,0);await self.rejected()
 async def test_lock_foreign_group_rejects(self):
  if os.geteuid()!=0:self.skipTest('Separate root-only synthetic metadata probe')
  self.make();os.chown(self.path.with_suffix('.lock'),0,65534);await self.rejected()
 async def test_each_physical_retry_rechecks_rotating_lease(self):
  self.make();a=self.provider.access_token;b=token('B');self.transport.status=503
  def rotate():
   self.transport.on_send=None;self.write_owner(b);publish_codex_consumer_auth(self.owner,self.path);self.transport.status=200
  self.transport.on_send=rotate
  await self.provider.call([{'role':'user','content':'synthetic retry probe'}],max_retries=1,initial_backoff=0,max_backoff=0)
  self.assertEqual([h['Authorization'] for h in self.transport.calls],['Bearer '+a,'Bearer '+b]);self.assertEqual(self.refresh,[])
  self.assertTrue(self.transport.calls[0]['session-id']);self.assertEqual({k:v for k,v in self.transport.calls[0].items() if k not in ['Authorization','ChatGPT-Account-ID']},{k:v for k,v in self.transport.calls[1].items() if k not in ['Authorization','ChatGPT-Account-ID']})
 async def test_exclusive_consumer_lock_busy_fails_closed_without_wait_or_refresh(self):
  import fcntl
  self.make();lock=self.path.with_suffix('.lock')
  with lock.open('rb') as holder:
   fcntl.flock(holder,fcntl.LOCK_EX|fcntl.LOCK_NB)
   await self.rejected()
 async def test_symlink_directory_is_not_followed(self):
  self.make();other=self.root/'old-directory';self.directory.rename(other);self.directory.symlink_to(other,target_is_directory=True);await self.rejected()
 async def test_deep_json_rejects_without_retaining_cached_authority(self):
  self.make();self.path.write_text('['*1100+']'*1100);await self.rejected()
 async def test_deep_jwt_rejects_without_retaining_cached_authority(self):
  self.make();value=json.loads(self.path.read_text());payload=base64.urlsafe_b64encode(('['*1100+']'*1100).encode()).decode().rstrip('=');value['tokens']['access_token']='synthetic.'+payload+'.synthetic';self.path.write_text(json.dumps(value));await self.rejected()
 async def test_original_full_oauth_without_flag_keeps_refresh_path(self):
  value=json.loads(self.path.read_text());value['tokens']['refresh_token']='synthetic-original-refresh';self.path.write_text(json.dumps(value))
  with patch.dict(os.environ,{'EVA_HINDSIGHT_CODEX_ACCESS_ONLY':'0'}):self.make()
  self.assertFalse(self.provider._external_access_only);self.assertEqual(self.provider.refresh_token,'synthetic-original-refresh')
  await self.call();self.assertEqual(len(self.transport.calls),1)

if __name__=='__main__':unittest.main()
