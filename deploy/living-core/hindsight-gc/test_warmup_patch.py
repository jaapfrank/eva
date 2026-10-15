"""Portable startup barrier/readiness checks; no model or private data needed."""
import asyncio
import hashlib
import os
from pathlib import Path
import runpy
import sys
import threading
import time
import types
import unittest
from unittest.mock import patch as mock_patch

MODULE = runpy.run_path(str(Path(__file__).with_name("warmup_patch.py")))
SCAFFOLD = b'''import logging
import asyncio
class Engine:
    def __init__(self, encoder):
        self._cross_encoder_reranker = type("R", (), {"cross_encoder": encoder})()
        self._initialized = False
        self.events = encoder.events
        self.timeout = 1
    async def initialize(self):
        if self._initialized:
            return
        loop = asyncio.get_running_loop()
        init_timeout = self.timeout
        async def loader(name):
            await asyncio.sleep(0)
            self.events.append(name)
        await asyncio.gather(loader("embeddings_loaded"), loader("reranker_loaded"))
        self.events.append("dtype_normalized")
        # Run database migrations if enabled
        self.events.append("migrations")
        self._initialized = True
'''

class LocalSTCrossEncoder:
    def __init__(self):
        self.events = []
        self.calls = []
        self.fail = False
        self.delay = 0
        self.threads = []
    def _predict_sync(self, pairs):
        self.events.append("warmup")
        self.calls.append(pairs)
        self.threads.append(threading.get_ident())
        time.sleep(self.delay)
        if self.fail:
            raise RuntimeError("synthetic failure")
        return [0.] * len(pairs)

class WarmupPatchTests(unittest.TestCase):
    def setUp(self):
        module = types.ModuleType("warmup_fixture.cross_encoder")
        module.LocalSTCrossEncoder = LocalSTCrossEncoder
        self.modules = mock_patch.dict(sys.modules, {module.__name__: module})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def engine(self, encoder=None):
        fn = MODULE["patch"]
        with mock_patch.dict(fn.__globals__, SOURCE_SHA256=hashlib.sha256(SCAFFOLD).hexdigest()):
            generated = fn(SCAFFOLD)
        namespace = {"__package__": "warmup_fixture"}
        exec(compile(generated, "patched_memory_engine", "exec"), namespace)
        return namespace["Engine"](encoder or LocalSTCrossEncoder())

    def test_source_pin_rejects_unreviewed_code(self):
        with self.assertRaises(ValueError):
            MODULE["patch"](SCAFFOLD)

    def test_default_off_preserves_initialization(self):
        with mock_patch.dict(os.environ, {}, clear=True):
            engine = self.engine()
            asyncio.run(engine.initialize())
            self.assertTrue(engine._initialized)
            self.assertNotIn("warmup", engine.events)

    def test_enabled_warmup_follows_both_loaders_and_dtype_before_readiness(self):
        with mock_patch.dict(os.environ, {"EVA_HINDSIGHT_WARMUP_RERANKER": "1"}):
            engine = self.engine()
            encoder = engine._cross_encoder_reranker.cross_encoder
            asyncio.run(engine.initialize())
            asyncio.run(engine.initialize())
            self.assertEqual(engine.events, ["embeddings_loaded", "reranker_loaded", "dtype_normalized", "warmup", "migrations"])
            self.assertEqual(len(encoder.calls), 1)
            self.assertEqual(len(encoder.calls[0]), 8)
            self.assertNotEqual(encoder.threads[0], threading.get_ident())
            self.assertTrue(engine._initialized)

    def test_failure_stays_unready_and_retry_repeats_warmup(self):
        with mock_patch.dict(os.environ, {"EVA_HINDSIGHT_WARMUP_RERANKER": "1"}):
            engine = self.engine()
            encoder = engine._cross_encoder_reranker.cross_encoder
            encoder.fail = True
            with self.assertRaises(RuntimeError):
                asyncio.run(engine.initialize())
            self.assertFalse(engine._initialized)
            self.assertNotIn("migrations", engine.events)
            encoder.fail = False
            asyncio.run(engine.initialize())
            self.assertEqual(len(encoder.calls), 2)
            self.assertTrue(engine._initialized)

    def test_timeout_stays_unready(self):
        with mock_patch.dict(os.environ, {"EVA_HINDSIGHT_WARMUP_RERANKER": "1"}):
            engine = self.engine()
            engine.timeout = 0.001
            engine._cross_encoder_reranker.cross_encoder.delay = 0.02
            with self.assertRaises(TimeoutError):
                asyncio.run(engine.initialize())
            self.assertFalse(engine._initialized)
            self.assertNotIn("migrations", engine.events)

    def test_remote_provider_is_not_called(self):
        with mock_patch.dict(os.environ, {"EVA_HINDSIGHT_WARMUP_RERANKER": "1"}):
            encoder = types.SimpleNamespace(events=[])
            engine = self.engine(encoder)
            asyncio.run(engine.initialize())
            self.assertTrue(engine._initialized)
            self.assertNotIn("warmup", engine.events)

if __name__ == "__main__":
    unittest.main()
