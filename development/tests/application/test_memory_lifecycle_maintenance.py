"""Existing idle maintenance expires day text without an extra model request."""
from types import SimpleNamespace
from eva.application.memory_background import HindsightColdPathWorker


def test_lifecycle_uses_existing_idle_stage_and_waits_for_foreground():
    calls = []
    outbox = SimpleNamespace(compact_day_sources=lambda **kw: calls.append(kw))
    worker = HindsightColdPathWorker(object(), outbox=outbox, idle_seconds=1,
        idle_capacity=lambda: True)
    worker._last_activity = 0
    worker._schedule_reflection_if_idle()
    assert len(calls) == 1 and calls[0]["limit"] == 32
    with worker.foreground():
        worker._schedule_reflection_if_idle()
    assert len(calls) == 1


def test_lifecycle_failure_remains_visible_without_triggering_provider_retry():
    def fail(**kw): raise RuntimeError("canonical proof differs")
    worker = HindsightColdPathWorker(object(), outbox=SimpleNamespace(compact_day_sources=fail),
        idle_seconds=1, idle_capacity=lambda: True)
    worker._last_activity = 0; worker._schedule_reflection_if_idle()
    assert "day text lifecycle: RuntimeError: canonical proof differs" in worker.last_error


def test_raw_expiry_does_not_require_paid_reflection_allowance():
    calls = []
    worker = HindsightColdPathWorker(object(),
        outbox=SimpleNamespace(compact_day_sources=lambda **kw: calls.append(kw)),
        idle_seconds=1, idle_capacity=lambda: True)
    worker._reflection_has_capacity = lambda: False
    worker._last_activity = 0; worker._schedule_reflection_if_idle()
    assert len(calls) == 1
