import os,unittest,threading,asyncio,time,ast
from pathlib import Path
from unittest.mock import patch
from hindsight_api.engine.cross_encoder import LocalSTCrossEncoder,RerankTimeoutError
class Fake:
 def __init__(self,event=None,delay=0):self.calls=0;self.event=event;self.delay=delay
 def predict(self,pairs,**kwargs):
  self.calls+=1
  if self.delay:time.sleep(self.delay)
  if self.event:self.event.set()
  return [float(len(a)+len(b)) for a,b in pairs]
class OverlayTests(unittest.TestCase):
 def make(self,enabled=True,**kwargs):
  with patch.dict(os.environ,{'EVA_HINDSIGHT_EXACT_RERANK_CACHE':'1' if enabled else '0'}):
   x=LocalSTCrossEncoder(model_name='synthetic-local-test',force_cpu=True,batch_size=1,**kwargs)
  x._model=Fake();return x
 def test_default_off_same_inference(self):
  x=self.make(False);self.assertEqual(x._predict_sync([('a','bc')]),[3.]);x._predict_sync([('a','bc')]);self.assertEqual(x._model.calls,2)
 def test_hit_and_changed_query_and_configuration(self):
  x=self.make();self.assertEqual(x._predict_sync([('a','bc')]),[3.]);self.assertEqual(x._predict_sync([('a','bc')]),[3.]);self.assertEqual(x._model.calls,1);x._predict_sync([('b','bc')]);self.assertEqual(x._model.calls,2);x.timeout+=1;x._predict_sync([('a','bc')]);self.assertEqual(x._model.calls,3)
 def test_cancel_before_hit_does_not_return_cached_scores(self):
  x=self.make();x._predict_sync([('a','bc')]);e=threading.Event();e.set()
  with self.assertRaises(asyncio.CancelledError):x._predict_sync([('a','bc')],e)
  self.assertEqual(x._model.calls,1)
 def test_cancel_between_batches_not_cached(self):
  x=self.make();e=threading.Event();x._model=Fake(e)
  with self.assertRaises(asyncio.CancelledError):x._predict_sync([('a','bc'),('a','d')],e)
  self.assertEqual(x._model.calls,1);self.assertEqual(len(x._exact_score_cache._entries),0)
 def test_timeout_between_batches_not_cached(self):
  x=self.make(timeout=0.005);x._model=Fake(delay=0.01)
  with self.assertRaises(RerankTimeoutError):x._predict_sync([('a','bc'),('a','d')])
  self.assertEqual(x._model.calls,1);self.assertEqual(len(x._exact_score_cache._entries),0)
 def test_cancellation_after_cache_compute_propagates(self):
  x=self.make();e=threading.Event();x._model=Fake(e)
  with self.assertRaises(asyncio.CancelledError):x._predict_sync([('a','bc')],e)
 def test_warmup_after_dtype_before_migration(self):
  p=Path(os.environ['EVA_HINDSIGHT_MEMORY_ENGINE_SOURCE']);raw=p.read_text();self.assertLess(raw.index('await asyncio.wait_for(asyncio.gather(*init_tasks)'),raw.index('EVA_HINDSIGHT_WARMUP_RERANKER'));self.assertLess(raw.index('torch_mod.set_default_dtype(torch_mod.float32)'),raw.index('EVA_HINDSIGHT_WARMUP_RERANKER'));self.assertLess(raw.index('EVA_HINDSIGHT_WARMUP_RERANKER'),raw.index('# Run database migrations if enabled'))
if __name__=='__main__':unittest.main()
