import ast,asyncio,copy,json,os,time,types,unittest
from pathlib import Path
from unittest.mock import patch
class ReachedOriginalBody(Exception):pass
class Integrity(Exception):pass
class GuardTests(unittest.IsolatedAsyncioTestCase):
 def setUp(self):
  tree=ast.parse(Path(os.environ['EVA_HINDSIGHT_ROLLBACK_SOURCE']).read_text());cls=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='MemoryEngine');method=next(n for n in cls.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_execute_mental_model_refresh');err=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='_RollbackStructuredVersionError');classify=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_is_non_retryable_task_error');h=ast.ClassDef(name='Harness',bases=[],keywords=[],body=[method],decorator_list=[]);module=ast.Module(body=[ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0),err,classify,h],type_ignores=[]);ast.fix_missing_locations(module)
  self.fetches=[];self.entered=0;self.exited=0;self.body=0;self.value=None;self.rowMissing=False;owner=self
  class Conn:
   async def fetchrow(self,sql,*args):
    owner.fetches.append((sql,args));return None if owner.rowMissing else {'structured_content':copy.deepcopy(owner.value)}
   async def execute(self,*args):raise AssertionError('No DB write allowed')
  class Context:
   async def __aenter__(self):owner.entered+=1;return Conn()
   async def __aexit__(self,*args):owner.exited+=1
  def fallthrough(*args):owner.body+=1;raise ReachedOriginalBody()
  self.ns={'os':os,'time':time,'json':json,'acquire_with_retry':lambda backend:Context(),'fq_table':lambda name:'public.'+name,'MENTAL_MODEL_PENDING_CONTENT':'pending','_resolve_refresh_tag_filtering':fallthrough,'asyncpg':types.SimpleNamespace(exceptions=types.SimpleNamespace(IntegrityConstraintViolationError=Integrity)),'_is_oracledb_integrity_error':lambda e:False,'_is_invalid_embedding_dimension_error':lambda e:False};exec(compile(module,'actual_rollback_guard','exec'),self.ns);self.instance=self.ns['Harness']()
  async def backend():return object()
  self.instance._get_backend=backend;self.model={'id':'scoped-model','source_query':'synthetic topic','content':'unchanged faithful body'}
 async def call(self,enabled=True):
  with patch.dict(os.environ,{'EVA_HINDSIGHT_ROLLBACK_PRESERVE_V2':'1' if enabled else '0'}):return await self.instance._execute_mental_model_refresh('scoped-bank',self.model,request_context=object())
 async def test_newer_dict_fails_before_original_body_with_scope_and_connection_release(self):
  self.value={'version':2,'private-test-body':'must never reach exception'};before=copy.deepcopy(self.value)
  with self.assertRaises(self.ns['_RollbackStructuredVersionError']) as caught:await self.call()
  self.assertEqual(str(caught.exception),'Mental model refresh unavailable during rollback: newer or unknown structured version.');self.assertEqual(self.value,before);self.assertEqual(self.body,0);self.assertEqual(self.entered,self.exited);self.assertEqual(self.fetches[0][1],('scoped-bank','scoped-model'));self.assertIn('WHERE bank_id = $1 AND id = $2',self.fetches[0][0])
 async def test_newer_json_string_blocked(self):
  self.value=json.dumps({'version':2,'blocks':[]})
  with self.assertRaises(self.ns['_RollbackStructuredVersionError']):await self.call()
  self.assertEqual(self.body,0);self.assertEqual(self.exited,1)
 async def test_legacy_v1_falls_through_without_write(self):
  self.value={'version':1,'blocks':[]}
  with self.assertRaises(ReachedOriginalBody):await self.call()
  self.assertEqual(self.body,1);self.assertEqual(self.exited,1)
 async def test_null_falls_through_without_write(self):
  with self.assertRaises(ReachedOriginalBody):await self.call()
  self.assertEqual(self.body,1)
 async def test_missing_row_falls_through(self):
  self.rowMissing=True
  with self.assertRaises(ReachedOriginalBody):await self.call()
  self.assertEqual(self.body,1)
 async def test_guard_off_preserves_original_route_without_extra_query(self):
  self.value={'version':2}
  with self.assertRaises(ReachedOriginalBody):await self.call(False)
  self.assertEqual(self.fetches,[]);self.assertEqual(self.body,1)
 async def test_unknown_or_malformed_version_fails_closed(self):
  for value in ['not JSON',{'version':True},{'version':'1'},[],{}]:
   self.value=value
   with self.assertRaises(self.ns['_RollbackStructuredVersionError']):await self.call()
  self.assertEqual(self.body,0);self.assertEqual(self.entered,self.exited)
 def test_only_dedicated_guard_error_added_to_terminal_classifier(self):
  f=self.ns['_is_non_retryable_task_error'];self.assertTrue(f(self.ns['_RollbackStructuredVersionError']('fixed')));self.assertFalse(f(ValueError('other')));self.assertTrue(f(Integrity('original')))
if __name__=='__main__':unittest.main()
