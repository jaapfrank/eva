import ast
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import runpy
import threading
import unittest
from unittest.mock import patch

HERE = Path(__file__).parent
H = runpy.run_path(str(HERE/'parallel_rerank_batches.py'))
predict = H['predict_parallel_rerank_batches']


class ParallelBatchTests(unittest.TestCase):
    def test_original_chunks_and_return_order(self):
        calls=[]
        class Model:
            def predict(self, pairs, **kw):
                calls.append((pairs,kw));return [float(len(d)) for q,d in pairs]
        pairs=[('q','x'*i) for i in range(1,20)]
        self.assertEqual(predict(Model(),pairs,8),list(map(float,range(1,20))))
        self.assertCountEqual([x[0] for x in calls],[pairs[:8],pairs[8:16],pairs[16:]])
        self.assertTrue(all(x[1]=={'batch_size':8,'show_progress_bar':False} for x in calls))

    def test_longest_first_submission(self):
        submitted=[]
        class Future:
            def __init__(self,result):self.value=result
            def result(self):return self.value
        class Pool:
            def submit(self,fn,chunk):submitted.append(chunk);return Future(fn(chunk))
        class Model:
            def predict(self,pairs,**kw):return [float(len(d)) for q,d in pairs]
        pairs=[('q','x'*i) for i in range(1,20)]
        with patch.dict(predict.__globals__,{'_shared_rerank_batch_pool':lambda:Pool()}):
            self.assertEqual(predict(Model(),pairs,8),list(map(float,range(1,20))))
        self.assertEqual(submitted,[pairs[16:],pairs[8:16],pairs[:8]])

    def test_two_simultaneous_recalls_share_two_compute_workers(self):
        lock=threading.Lock();both=threading.Event();release=threading.Event();active=0;peak=0
        class Model:
            def predict(self,pairs,**kw):
                nonlocal active,peak
                with lock:
                    active+=1;peak=max(active,peak)
                    if active==2:both.set()
                assert release.wait(2)
                with lock:active-=1
                return [1.]*len(pairs)
        with ThreadPoolExecutor(max_workers=2) as outer:
            jobs=[outer.submit(predict,Model(),[('q','d')]*32,8) for _ in range(2)]
            self.assertTrue(both.wait(1));self.assertEqual(peak,2);release.set()
            self.assertEqual([f.result(2) for f in jobs],[[1.]*32]*2)
        self.assertEqual(peak,2)

    def test_four_workers_remain_shared_and_bounded(self):
        helper = runpy.run_path(str(HERE/'parallel_rerank_batches.py'))
        factory = helper['_shared_rerank_batch_pool']
        with patch.dict(os.environ, {'EVA_HINDSIGHT_RERANK_BATCH_WORKERS': '4'}):
            pool = factory()
            try:
                self.assertIs(factory(), pool)
                lock = threading.Lock()
                full = threading.Event()
                release = threading.Event()
                active = peak = 0
                class Model:
                    def predict(self, pairs, **kw):
                        nonlocal active, peak
                        with lock:
                            active += 1
                            peak = max(peak, active)
                            if active == 4:
                                full.set()
                        assert release.wait(3)
                        with lock:
                            active -= 1
                        return [1.] * len(pairs)
                with ThreadPoolExecutor(max_workers=2) as outer:
                    jobs = [outer.submit(helper['predict_parallel_rerank_batches'],
                                         Model(), [('q', 'd')]*32, 8) for _ in range(2)]
                    try:
                        self.assertTrue(full.wait(2))
                        self.assertEqual(peak, 4)
                    finally:
                        release.set()
                    self.assertEqual([job.result(2) for job in jobs], [[1.]*32]*2)
                self.assertEqual(peak, 4)
            finally:
                pool.shutdown(wait=True)

    def test_worker_configuration_cannot_remove_bound(self):
        for value in ('0', '3', '999', 'bad'):
            helper = runpy.run_path(str(HERE/'parallel_rerank_batches.py'))
            with patch.dict(os.environ, {'EVA_HINDSIGHT_RERANK_BATCH_WORKERS': value}):
                with self.assertRaises(ValueError):
                    helper['_shared_rerank_batch_pool']()

    def test_failure_drains_own_running_batch_and_pool_remains_usable(self):
        entered=threading.Event();release=threading.Event();returned=threading.Event()
        class Model:
            def predict(self,pairs,**kw):
                if pairs[0][1]=='long':entered.set();assert release.wait(2);return [1.]*8
                assert entered.wait(1);raise RuntimeError('inference failed')
        with ThreadPoolExecutor(max_workers=1) as outer:
            def run():
                try:return predict(Model(),[('q','short')]*8+[('q','long')]*8,8)
                finally:returned.set()
            future=outer.submit(run);self.assertTrue(entered.wait(1))
            self.assertFalse(returned.wait(.03));release.set()
            with self.assertRaisesRegex(RuntimeError,'inference failed'):future.result(2)
        class Good:
            def predict(self,pairs,**kw):return [2.]*len(pairs)
        self.assertEqual(predict(Good(),[('q','d')]*16,8),[2.]*16)

    def test_incomplete_scores_rejected(self):
        class Model:
            def predict(self,pairs,**kw):return []
        with self.assertRaisesRegex(ValueError,'incomplete'):predict(Model(),[('q','d')]*16,8)


class PinnedTests(unittest.TestCase):
    def test_source_digest_rejected(self):
        fn=runpy.run_path(str(HERE/'parallel_rerank_patch.py'))['patch']
        with self.assertRaisesRegex(ValueError,'source changed'):fn(b'changed')

    def test_actual_dispatch_and_cleanup(self):
        path=os.environ.get('EVA_PARALLEL_RERANK_SOURCE')
        if not path:self.skipTest('Set EVA_PARALLEL_RERANK_SOURCE to installed cached module')
        fn=runpy.run_path(str(HERE/'parallel_rerank_patch.py'))['patch']
        candidate=fn(Path(path).read_bytes());tree=ast.parse(candidate)
        cls=next(x for x in tree.body if isinstance(x,ast.ClassDef) and x.name=='LocalSTCrossEncoder')
        cls.body=[n for n in cls.body if isinstance(n,ast.FunctionDef) and n.name in {'__init__','_predict_uncached_sync'}]
        cleanup=[];parallel=[]
        def compute(model,pairs,size):parallel.append(pairs);return predict(model,pairs,size)
        ns={'CrossEncoderModel':object,'os':os,'DEFAULT_RERANKER_LOCAL_MODEL':'pinned',
            'DEFAULT_RERANKER_LOCAL_BATCH_SIZE':8,'release_local_inference_memory':lambda d:cleanup.append(d),
            'predict_parallel_rerank_batches':compute}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),'candidate','exec'),ns)
        class Model:
            def predict(self,pairs,**kw):return [float(len(d)) for q,d in pairs]
        pairs=[('q','x'*i) for i in reversed(range(1,20))]
        for enabled,device,bucket,expected in [('0','cpu',True,0),('1','cuda',True,0),('1','cpu',False,0),('1','cpu',True,1)]:
            parallel.clear()
            with patch.dict(os.environ,{'EVA_HINDSIGHT_PARALLEL_RERANK_BATCHES':enabled,'EVA_HINDSIGHT_EXACT_RERANK_CACHE':'0'}):obj=ns['LocalSTCrossEncoder'](bucket_batching=bucket,batch_size=8)
            obj._model=Model();obj._device_type=device
            self.assertEqual(obj._predict_uncached_sync(pairs),list(map(float,reversed(range(1,20)))))
            self.assertEqual(len(parallel),expected)
        self.assertEqual(cleanup,['cpu','cuda','cpu','cpu'])


if __name__=='__main__':unittest.main()
