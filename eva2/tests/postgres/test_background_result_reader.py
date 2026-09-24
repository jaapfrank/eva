"""A worker reads retained prior results through the actual private owner store."""
import hashlib
from dataclasses import replace
from datetime import timedelta

import pytest

from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.bootstrap.delegated_work_result_reader import DelegatedWorkResultReader
from eva.contracts.delegated_work import DelegatedWorkResult
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_private_work_return import setup, start
from tests.postgres.test_delegated_delivery_store import NOW, _release, _usage


def test_complete_result_fresh_read_retention_owner_and_cancellation(database):
    service, turn, clock = setup(database, "worker-result", False)
    spec = start(service, turn)
    claim = service.store.claim_next(worker_id="worker:result", release=_release(spec), now=NOW+timedelta(seconds=1))
    call = service.store.start_call(claim, now=NOW+timedelta(seconds=2))
    output = "Retained finding. " * 500 + "FINAL UNIQUE DETAIL"
    assert len(output.encode()) > 6000
    result = DelegatedWorkResult(contract="DelegatedWorkResult/v1", schema_version=1,
        work_id=spec.work_id, call_id=call.call_id, output=output,
        output_sha256=hashlib.sha256(output.encode()).hexdigest(), spec_sha256=spec.sha256,
        usage=_usage().to_payload(), completed_at="2026-08-26T12:00:03Z")
    service.store.record_success(call, result, now=NOW+timedelta(seconds=3))
    clock[0] += timedelta(seconds=4)
    reader = DelegatedWorkResultReader(service)
    request = ConversationActionCall("read:prior", "get_delegated_work", {"work_id": spec.work_id})
    guards = []
    with background_execution(BackgroundExecutionScope(turn, "execution:new", lambda value: guards.append(value))):
        receipt = reader.execute(turn, request)
        assert reader.pending(turn) is None
        assert reader.execute(turn, request) == receipt
    assert len(guards) == 2
    assert receipt.output["operation_result"]["work"]["result"]["output"] == output
    assert reader.action_names == ("get_delegated_work",)
    for name in ("start_digital_work", "stop_delegated_work", "resume_delegated_work"):
        with pytest.raises(ConversationConflict):
            reader.execute(turn, replace(request, name=name))
    other, other_turn, _ = setup(database, "worker-result-other", False)
    assert DelegatedWorkResultReader(other).execute(other_turn, request).output["operation_result"]["status"] == "not_found"
    with pytest.raises((ValueError, RuntimeError)):
        reader.execute(other_turn, request)
    def cancelled(value):
        raise ConversationConflict("work stopped")
    with background_execution(BackgroundExecutionScope(turn, "execution:new", cancelled)):
        with pytest.raises(ConversationConflict, match="stopped"):
            reader.execute(turn, request)
    clock[0] += timedelta(days=8)
    assert reader.execute(turn, request).output["operation_result"]["status"] == "not_found"
