"""Real PostgreSQL transaction fences around the existing repair host adapter."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.error import HTTPError

import pytest

from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, external_timeline_scope, CheckpointConflict
from eva.adapters.postgres.component_improvement_actions import REPAIR
from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.bootstrap.native_initiative_work import admit_native_initiative
from tests.application.test_runtime_repair_request_journal import adapter, NOW
from tests.postgres.test_runtime_repair_host import database, postgres_engine, native_digital, costless_native, setup
from tests.postgres.test_native_initiative_digital_work import started


def bound(native_digital, tmp_path):
    f, _, _, _, _, builder = setup(native_digital, tmp_path)
    admitted = admit_native_initiative(f.auth, builder, f.service)
    work = f.store.load(admitted.work_id, **f.service._owner)
    started(f, work)
    guard, source = f.auth.bind_work(work.spec)
    timeline = PostgresOperationalTimeline(f.store._engine, external_timeline_scope(
        source.tenant_id, source.installation_id, "repair request test"))
    component, calls = adapter(timeline=timeline, source=source, spec=work.spec)
    return f, component, calls


@pytest.mark.parametrize("failure", [False, True])
def test_committed_request_survives_adapter_reconstruction_without_redispatch(native_digital, tmp_path, failure):
    _, component, calls = bound(native_digital, tmp_path)
    post = component.evaluator.runtime_repair
    def result(**request):
        post(**request)
        if failure:
            raise HTTPError("https://private-host/secret", 400, "PRIVATE_BODY", {}, None)
        return {"state": "waiting_for_builder", "installed": False}
    component.evaluator.runtime_repair = result
    call = ConversationActionCall("repair-call", REPAIR, {"action": "submit"})
    if failure:
        with pytest.raises(HTTPError):
            component.execute(component.source, call, step_index=3)
    else:
        receipt = component.execute(component.source, call, step_index=3)
    events = component.timeline.events(component._thread(3))
    assert events[0].event_type == "RUNTIME_REPAIR_REQUEST_ADMITTED"
    assert events[0].payload["data"]["request"] == calls[0]
    state = component.timeline.load(component._thread(3))
    assert state.status == ("active" if failure else "completed")
    assert state.revision == 2
    assert "PRIVATE_BODY" not in repr(events)
    restored, new_calls = adapter(timeline=component.timeline, source=component.source, spec=component.spec)
    if failure:
        with pytest.raises(ConversationConflict, match="requires reconciliation"):
            restored.pending(restored.source, step_index=3)
        with pytest.raises(ConversationConflict, match="requires reconciliation"):
            restored.execute(restored.source, call, step_index=3)
    else:
        assert restored.pending(restored.source, step_index=3) == receipt
        assert restored.execute(restored.source, call, step_index=3) == receipt
    assert not new_calls and len(calls) == 1


def test_concurrent_requests_commit_one_fence_before_exactly_one_post(native_digital, tmp_path):
    _, component, calls = bound(native_digital, tmp_path)
    competitor, competitor_calls = adapter(timeline=component.timeline, source=component.source, spec=component.spec)
    barrier = Barrier(2)
    for candidate in (component, competitor):
        pending = candidate.pending
        def synchronized(turn, *, step_index, original=pending):
            result = original(turn, step_index=step_index)
            barrier.wait(timeout=5)
            return result
        candidate.pending = synchronized
    call = ConversationActionCall("repair-call", REPAIR, {"action": "submit"})
    def run(candidate):
        try:
            return candidate.execute(candidate.source, call, step_index=3)
        except CheckpointConflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(run, (component, competitor)))
    assert sum(result is not None for result in results) == 1
    assert len(calls) + len(competitor_calls) == 1
    assert len(component.timeline.events(component._thread(3))) == 2
