"""Portable behavior tests for the generated upstream overlay (stdlib only).

A minimal upstream scaffold isolates GC behavior without importing torch or
requiring the deployment image. Only its test digest replaces the pinned digest.
"""
import hashlib
import importlib.util
import os
from pathlib import Path
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch as mock_patch

_spec = importlib.util.spec_from_file_location("gc_overlay_patch", Path(__file__).with_name("patch.py"))
overlay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(overlay)

SCAFFOLD = b'''import sys
import gc

def _resolve_heap_trim():
    return lambda: None

_heap_trim = _resolve_heap_trim()

def _empty_gpu_cache(device_type):
    pass

def release_local_inference_memory(device_type=None):
    gc.collect()
    _heap_trim()
    _empty_gpu_cache(device_type)
'''


def load_generated(enabled=None):
    with mock_patch.object(overlay, "SOURCE_SHA256", hashlib.sha256(SCAFFOLD).hexdigest()):
        source = overlay.patch(SCAFFOLD)
    namespace = {}
    with mock_patch.dict(os.environ):
        os.environ.pop("EVA_HINDSIGHT_COALESCE_GC", None)
        if enabled is not None:
            os.environ["EVA_HINDSIGHT_COALESCE_GC"] = enabled
        exec(compile(source, "<generated local_device>", "exec"), namespace)
    return namespace


class PatchTests(unittest.TestCase):
    def test_source_mismatch_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "source changed"):
            overlay.patch(SCAFFOLD)
        with mock_patch.object(overlay, "SOURCE_SHA256", hashlib.sha256(SCAFFOLD).hexdigest()):
            with self.assertRaises(ValueError):
                overlay.patch(SCAFFOLD + b"\n")

    def test_default_off_preserves_full_gc_trim_and_gpu_calls(self):
        for setting in (None, "0", "true"):
            with self.subTest(setting=setting):
                ns = load_generated(setting)
                calls = []
                ns["gc"] = SimpleNamespace(collect=lambda *args: calls.append(("gc", args)))
                ns["_heap_trim"] = lambda: calls.append(("trim",))
                ns["_empty_gpu_cache"] = lambda device: calls.append(("gpu", device))
                for _ in range(2):
                    ns["release_local_inference_memory"]("cuda")
                self.assertEqual(calls, [("gc", ()), ("trim",), ("gpu", "cuda")] * 2)

    def test_interval_starts_after_full_collection_finishes(self):
        ns = load_generated("1")
        clock = [0.0]
        calls = []
        def collect(*args):
            calls.append(args)
            if not args:
                clock[0] += 0.25
        ns["gc"] = SimpleNamespace(collect=collect)
        ns["time"] = SimpleNamespace(monotonic=lambda: clock[0])
        cleanup = []
        ns["_heap_trim"] = lambda: cleanup.append("trim")
        ns["_empty_gpu_cache"] = lambda device: cleanup.append(device)
        ns["release_local_inference_memory"]("cuda")
        clock[0] = 1.0  # A second after start, but only .75 after completion.
        ns["release_local_inference_memory"]("cuda")
        clock[0] = 1.25
        ns["release_local_inference_memory"]("cuda")
        self.assertEqual(calls, [(), (0,), ()])
        self.assertEqual(ns["_last_full_gc"], 1.5)
        self.assertEqual(cleanup, ["trim", "cuda"] * 3)

    def test_concurrent_calls_share_one_full_collection(self):
        ns = load_generated("1")
        ns["time"] = SimpleNamespace(monotonic=lambda: 0.0)
        entered, release = threading.Event(), threading.Event()
        calls, failures = [], []
        state_lock = threading.Lock()
        active = [0]
        maximum = [0]
        def collect(*args):
            with state_lock:
                active[0] += 1
                maximum[0] = max(maximum[0], active[0])
                calls.append(args)
            try:
                if not args:
                    entered.set()
                    if not release.wait(3):
                        raise TimeoutError("test did not release collection")
            finally:
                with state_lock:
                    active[0] -= 1
        ns["gc"] = SimpleNamespace(collect=collect)
        def run():
            try:
                ns["_collect_inference_garbage"]()
            except BaseException as error:
                failures.append(error)
        threads = [threading.Thread(target=run, daemon=True) for _ in range(5)]
        threads[0].start()
        try:
            self.assertTrue(entered.wait(3))
            for thread in threads[1:]:
                thread.start()
        finally:
            release.set()
            for thread in threads:
                if thread.ident is not None:
                    thread.join(3)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(failures, [])
        self.assertEqual(maximum[0], 1)
        self.assertEqual(calls, [()] + [(0,)] * 4)

    def test_collection_failure_releases_lock_and_does_not_advance_stamp(self):
        for initial_stamp in (-float("inf"), 0.0):
            with self.subTest(initial_stamp=initial_stamp):
                ns = load_generated("1")
                ns["time"] = SimpleNamespace(monotonic=lambda: 0.0)
                ns["_last_full_gc"] = initial_stamp
                def fail(*args):
                    raise RuntimeError("collection failure")
                ns["gc"] = SimpleNamespace(collect=fail)
                with self.assertRaisesRegex(RuntimeError, "collection failure"):
                    ns["_collect_inference_garbage"]()
                self.assertEqual(ns["_last_full_gc"], initial_stamp)
                acquired = ns["_gc_lock"].acquire(blocking=False)
                self.assertTrue(acquired)
                if acquired:
                    ns["_gc_lock"].release()
                calls = []
                ns["gc"] = SimpleNamespace(collect=lambda *args: calls.append(args))
                ns["_collect_inference_garbage"]()
                self.assertEqual(calls, [()] if initial_stamp < 0 else [(0,)])


if __name__ == "__main__":
    unittest.main()
