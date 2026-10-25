"""A slow connector shutdown must not create a second ingestion listener."""

from threading import Event, Lock, Thread
from types import SimpleNamespace

from eva.application.sensory import SensoryIngestionWorker


class BlockingConnector:
    def __init__(self):
        self.entered = [Event() for _ in range(3)]
        self.release = [Event() for _ in range(3)]
        self.threads = []
        self.lock = Lock()

    def listen(self, stop, handler):
        with self.lock:
            index = len(self.threads)
            self.threads.append(__import__("threading").current_thread())
        self.entered[index].set()
        # Connector I/O can outlast the worker's bounded join. No source event
        # or external connection is created by this controlled blocked listener.
        self.release[index].wait()

    def finish(self):
        for release in self.release:
            release.set()
        for thread in self.threads:
            Thread.join(thread, timeout=2)
            assert not thread.is_alive()


def test_stop_timeout_cannot_reactivate_a_live_listener_and_open_another(monkeypatch):
    source = BlockingConnector()
    worker = SensoryIngestionWorker(source, SimpleNamespace(retain=lambda event: None))
    try:
        worker.start()
        assert source.entered[0].wait(timeout=2)
        first = source.threads[0]
        # Deterministically reach the join-timeout boundary without a five-second
        # sleep; the genuine listener thread remains blocked and alive.
        monkeypatch.setattr(first, "join", lambda timeout=None: None)
        worker.stop()
        assert first.is_alive()
        worker.start()
        assert not source.entered[1].wait(timeout=0.1), "restart opened a second live connector"
        assert worker._stop.is_set(), "restart revoked a live listener's stop request"
    finally:
        source.finish()


def test_a_new_listener_can_start_once_the_previous_listener_has_really_exited(monkeypatch):
    source = BlockingConnector()
    worker = SensoryIngestionWorker(source, SimpleNamespace(retain=lambda event: None))
    try:
        worker.start()
        assert source.entered[0].wait(timeout=2)
        first = source.threads[0]
        monkeypatch.setattr(first, "join", lambda timeout=None: None)
        worker.stop()
        source.release[0].set()
        Thread.join(first, timeout=2)
        assert not first.is_alive()
        worker.start()
        assert source.entered[1].wait(timeout=2)
        assert not worker._stop.is_set()
        worker.start()
        assert not source.entered[2].wait(timeout=0.1)
    finally:
        source.finish()
