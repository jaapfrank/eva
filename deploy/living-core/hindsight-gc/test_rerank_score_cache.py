"""Exact numeric reuse preserves inputs, bounds, misses and worker concurrency."""
import concurrent.futures
from pathlib import Path
import runpy
import threading
import unittest

Cache = runpy.run_path(str(Path(__file__).with_name('rerank_score_cache.py')))['ExactRerankScores']


class ScoreCacheTests(unittest.TestCase):
    def test_exact_hit_and_immutable_return(self):
        cache = Cache(); calls = []
        def compute(pairs):
            calls.append(pairs); return [1.25, -2.5]
        pairs = [('query','first'), ('query','second')]
        a = cache.predict(pairs, compute, context=('model',8)); a[0]=99.
        self.assertEqual(cache.predict(pairs, compute, context=('model',8)), [1.25,-2.5])
        self.assertEqual(calls,[pairs])
        for key, (_, scores) in cache._entries.items():
            self.assertIsInstance(key, bytes); self.assertEqual(len(key),32)
            self.assertEqual(scores,(1.25,-2.5))

    def test_changed_query_document_order_and_config_miss(self):
        cache=Cache(); calls=[]
        def compute(pairs): calls.append(pairs); return [float(i) for i in range(len(pairs))]
        inputs=[ [('q','a'),('q','b')], [('Q','a'),('q','b')],
                 [('q','a!'),('q','b')], [('q','b'),('q','a')] ]
        for pairs in inputs: cache.predict(pairs,compute,context=('model',8))
        cache.predict(inputs[0],compute,context=('model',4))
        self.assertEqual(len(calls),5)

    def test_expiry_and_lru_eviction(self):
        now=[0.];cache=Cache(capacity=2,ttl=3,clock=lambda:now[0]);calls=[]
        def compute(pairs):calls.append(pairs);return [1.]
        def read(text):return cache.predict([('q',text)],compute,context=1)
        read('a');read('b');read('a');read('c');read('b')
        self.assertEqual(len(calls),4); self.assertEqual(len(cache._entries),2)
        now[0]=3.;read('b');self.assertEqual(len(calls),5)

    def test_default_lifetime_reuses_scores_after_session_idle_but_expires(self):
        now=[0.];cache=Cache(clock=lambda:now[0]);calls=[]
        def compute(pairs):calls.append(pairs);return [1.25]
        def read():return cache.predict([('q','document')],compute,context='model')
        self.assertEqual(read(),[1.25])
        for elapsed in (121.,3599.):
            now[0]=elapsed
            self.assertEqual(read(),[1.25])
        self.assertEqual(len(calls),1)
        # Hits do not prolong the lifetime of the original calculation.
        now[0]=3600.;self.assertEqual(read(),[1.25])
        self.assertEqual(len(calls),2)

    def test_concurrent_misses_not_serialized_and_context_cannot_poison(self):
        cache=Cache();entered=threading.Event();release=threading.Event()
        def old(pairs):entered.set();release.wait(2);return [1.]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
            pending=workers.submit(cache.predict,[('q','a')],old,context='old')
            self.assertTrue(entered.wait(1))
            fresh=workers.submit(cache.predict,[('q','a')],lambda _: [2.],context='new')
            self.assertEqual(fresh.result(timeout=1),[2.]);release.set();pending.result(1)
        self.assertEqual(cache.predict([('q','a')],lambda _: self.fail('cache poisoned'),context='new'),[2.])

    def test_same_batch_concurrency_returns_independent_scores(self):
        cache=Cache();barrier=threading.Barrier(2)
        def compute(pairs):barrier.wait(1);return [2.]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as workers:
            futures=[workers.submit(cache.predict,[('q','a')],compute,context=1) for _ in range(2)]
            first,second=[f.result(2) for f in futures]
        first[0]=8.;self.assertEqual(second,[2.])
        self.assertEqual(cache.predict([('q','a')],lambda _:self.fail('miss'),context=1),[2.])

    def test_oversize_and_failed_inference_not_cached(self):
        cache=Cache();calls=[]
        def compute(pairs):calls.append(1);return [1.]*len(pairs)
        for pairs in [[('q','x')]*257,[('q','x'*(2*1024*1024))]]:
            cache.predict(pairs,compute,context=1);cache.predict(pairs,compute,context=1)
        self.assertEqual(len(calls),4);self.assertFalse(cache._entries)
        with self.assertRaises(RuntimeError):
            cache.predict([('q','a')],lambda _:(_ for _ in ()).throw(RuntimeError()),context=1)
        self.assertFalse(cache._entries)





class PinnedPatchTests(unittest.TestCase):
    def test_changed_upstream_rejected(self):
        patcher=runpy.run_path(str(Path(__file__).with_name('rerank_cache_patch.py')))
        with self.assertRaisesRegex(ValueError,'source changed'):
            patcher['patch'](b'changed upstream')

    def test_actual_pinned_patch_default_off_and_enabled_dispatch(self):
        import ast
        import os
        import sys
        import types
        from unittest.mock import patch
        source_path=os.environ.get('EVA_RERANK_UPSTREAM_SOURCE')
        if not source_path:
            self.skipTest('Set EVA_RERANK_UPSTREAM_SOURCE to the exact installed source')
        source=Path(source_path).read_bytes()
        patcher=runpy.run_path(str(Path(__file__).with_name('rerank_cache_patch.py')))
        candidate=patcher['patch'](source)
        tree=ast.parse(candidate)
        original=next(n for n in ast.parse(source).body if isinstance(n,ast.ClassDef) and n.name=='LocalSTCrossEncoder')
        target=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='LocalSTCrossEncoder')
        old=next(n for n in original.body if isinstance(n,ast.FunctionDef) and n.name=='_predict_sync')
        uncached=next(n for n in target.body if isinstance(n,ast.FunctionDef) and n.name=='_predict_uncached_sync')
        self.assertEqual([ast.dump(n) for n in old.body],[ast.dump(n) for n in uncached.body])
        target.body=[n for n in target.body if isinstance(n,ast.FunctionDef) and n.name in {'__init__','_predict_sync','_predict_uncached_sync'}]
        module=ast.Module(body=[target],type_ignores=[])
        namespace={'CrossEncoderModel':object,'ExactRerankScores':Cache,'os':os,
                   'DEFAULT_RERANKER_LOCAL_BATCH_SIZE':8,'DEFAULT_RERANKER_LOCAL_MODEL':'pinned-model',
                   'release_local_inference_memory':lambda _:None}
        exec(compile(ast.fix_missing_locations(module),'actual-patched-class','exec'),namespace)
        encoder=namespace['LocalSTCrossEncoder']
        class Model:
            def __init__(self):self.calls=[]
            def predict(self,pairs,**kwargs):
                self.calls.append((pairs,kwargs));return [float(len(q)+len(d)) for q,d in pairs]
        torch=types.SimpleNamespace(get_num_threads=lambda:2,get_num_interop_threads=lambda:1)
        pairs=[('q','first'),('q','second')]
        with patch.dict(sys.modules,{'torch':torch}):
            with patch.dict(os.environ,{'EVA_HINDSIGHT_EXACT_RERANK_CACHE':'0'}):
                plain=encoder(force_cpu=True,bucket_batching=True,batch_size=8)
            plain._model=Model()
            expected=plain._predict_sync(pairs);plain._predict_sync(pairs)
            self.assertEqual(len(plain._model.calls),2)
            with patch.dict(os.environ,{'EVA_HINDSIGHT_EXACT_RERANK_CACHE':'1'}):
                cached=encoder(force_cpu=True,bucket_batching=True,batch_size=8)
            cached._model=Model()
            self.assertEqual(cached._predict_sync(pairs),expected)
            self.assertEqual(cached._predict_sync(pairs),expected)
            self.assertEqual(len(cached._model.calls),1)
            cached._predict_sync(list(reversed(pairs)))
            cached._predict_sync([('new query','first'),('q','second')])
            self.assertEqual(len(cached._model.calls),3)
            cached.batch_size=4;cached._predict_sync(pairs)
            self.assertEqual(len(cached._model.calls),4)


if __name__=='__main__':unittest.main()
