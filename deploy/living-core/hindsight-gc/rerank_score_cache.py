"""Ephemeral exact-batch arithmetic reuse; never a retrieval or authority cache."""
import hashlib
import math
import os
import threading
import time
from collections import OrderedDict


class ExactRerankScores:
    def __init__(self, *, capacity=64, ttl=30.0, clock=time.monotonic):
        self._capacity, self._ttl, self._clock = capacity, ttl, clock
        self._salt = os.urandom(32)
        self._entries = OrderedDict()
        self._lock = threading.Lock()
        self._context = None
        self._generation = 0

    def predict(self, pairs, compute, *, context):
        # Bound retained scores and hashing work; larger batches stay unchanged.
        if not 1 <= len(pairs) <= 256:
            return compute(pairs)
        digest = hashlib.blake2b(key=self._salt, digest_size=32)
        size = 0
        for pair in pairs:
            if len(pair) != 2 or not all(isinstance(text, str) for text in pair):
                return compute(pairs)
            for text in pair:
                raw = text.encode('utf-8')
                size += len(raw)
                if size > 2 * 1024 * 1024:
                    return compute(pairs)
                digest.update(len(raw).to_bytes(8, 'big'))
                digest.update(raw)
        key = digest.digest()
        now = self._clock()
        with self._lock:
            if self._context != context:
                self._entries.clear()
                self._context = context
                self._generation += 1
            generation = self._generation
            for expired in [k for k, (stamp, _) in self._entries.items() if now - stamp >= self._ttl]:
                del self._entries[expired]
            found = self._entries.get(key)
            if found is not None:
                self._entries.move_to_end(key)
                return list(found[1])
        # Never hold the cache lock over inference: unrelated misses retain the
        # existing bounded worker concurrency. Duplicate misses are harmless.
        result = compute(pairs)
        if (len(result) == len(pairs)
                and all(type(value) is float and math.isfinite(value) for value in result)):
            with self._lock:
                if generation == self._generation:
                    self._entries[key] = (self._clock(), tuple(result))
                    self._entries.move_to_end(key)
                    while len(self._entries) > self._capacity:
                        self._entries.popitem(last=False)
        return result
