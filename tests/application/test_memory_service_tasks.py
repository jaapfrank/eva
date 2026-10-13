"""Background service timing without model, provider network or external effects."""
from dataclasses import replace
from threading import Event
import time

from eva.application.memory_background import SemanticMemoryColdPathWorker
from tests.application.test_memory_background import _document


class Memory:
    def __init__(self, *, blocked=False):
        self.entered = Event()
        self.release = Event()
        if not blocked:
            self.release.set()
        self.retained = Event()
        self.maintenance = Event()

    def retain_conversation(self, document):
        self.entered.set()
        assert self.release.wait(3), "fixture retain was not released"
        self.retained.set()
        return None

    def maintain_person_scope(self, *args):
        self.maintenance.set()


def test_services_advance_during_active_conversation_and_blocked_memory_retain():
    memory = Memory(blocked=True)
    service_during_retain = Event()
    idle = Event()
    def service():
        if memory.entered.is_set() and not memory.release.is_set():
            service_during_retain.set()
    worker = SemanticMemoryColdPathWorker(memory, idle_seconds=300,
        idle_tasks=(idle.set,), service_tasks=(service,))
    worker.submit(_document())
    worker.start()
    try:
        assert memory.entered.wait(1)
        original = (worker._thread, worker._service_thread)
        for i in range(3):
            worker.submit(replace(_document(), document_id="conversation:" + str(i + 2)))
            worker.start()
            assert (worker._thread, worker._service_thread) == original
        assert service_during_retain.wait(2)
        assert not memory.retained.is_set() and not idle.is_set() and not memory.maintenance.is_set()
    finally:
        memory.release.set()
        worker.stop()
    assert not worker._thread.is_alive() and not worker._service_thread.is_alive()


def test_timed_out_stop_cannot_duplicate_lane_or_run_next_callback_and_restart_is_clean():
    memory = Memory()
    entered, release, later = Event(), Event(), Event()
    def blocking_service():
        entered.set()
        assert release.wait(3), "fixture service was not released"
    worker = SemanticMemoryColdPathWorker(memory, service_tasks=(blocking_service, later.set))
    worker.start()
    try:
        assert entered.wait(1)
        old_thread = worker._service_thread
        started = time.monotonic()
        worker.stop(timeout_seconds=.03)
        assert time.monotonic() - started < .5
        worker.start()
        assert worker._service_thread is old_thread and worker._stop.is_set()
        release.set()
        old_thread.join(1)
        assert not old_thread.is_alive() and not later.is_set()
        worker.start()
        assert worker._service_thread is not old_thread
        assert later.wait(1)
        # The old memory stop sentinel cannot discard newly retained jobs.
        worker.submit(_document())
        assert memory.retained.wait(1)
    finally:
        release.set()
        worker.stop()


def test_default_without_services_preserves_idle_memory_maintenance():
    memory = Memory()
    idle = Event()
    worker = SemanticMemoryColdPathWorker(memory, idle_seconds=.05, idle_tasks=(idle.set,))
    worker.submit(_document())
    worker.start()
    try:
        assert memory.retained.wait(1)
        assert idle.wait(1) and memory.maintenance.is_set()
        assert worker._service_thread is None
    finally:
        worker.stop()
