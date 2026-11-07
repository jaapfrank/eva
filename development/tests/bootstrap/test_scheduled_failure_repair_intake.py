"""Bounds and identity of the existing scheduled repair service tick."""
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eva.bootstrap.self_repair import _ScheduledFailureRepair, _recovery_marker_matches
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec, DelegatedInitiativeDigitalWorkSpec


def original_marker():
    spec = DelegatedDigitalWorkSpec.model_construct(
        contract="DelegatedDigitalWork/v4", schema_version=4, generation=1,
        audience="private", owner_principal_id="synthetic-owner",
        work_id="work:synthetic", execution_id="work:synthetic",
    )
    thread = f"scheduled-work:{spec.owner_principal_id}:provider-recovery:" + hashlib.sha256(
        spec.execution_id.encode()
    ).hexdigest()
    checkpoint = thread + ":blocked"
    payload = {"checkpoint": {"checkpoint_id": checkpoint, "revision": 1,
                              "phase": "blocked", "status": "needs_help"},
               "data": {"operation": "resume", "work_id": spec.work_id, "run_at": "synthetic",
                        "reason": "synthetic", "blocker_code": "synthetic", "automatic_retry": False}}
    marker = {"thread_id": thread, "checkpoint_id": checkpoint, "work_id": spec.work_id,
              "spec_sha256": bytes.fromhex(spec.sha256), "payload": payload,
              "payload_sha256": bytes.fromhex(canonical_sha256(payload))}
    work = SimpleNamespace(spec=spec, state="ambiguous", error_code="provider_unavailable",
                           stop_requested_at=None, lease_owner=None, lease_expires_at=None,
                           call_id="synthetic-call", call_started_at=datetime(2026, 1, 1, tzinfo=UTC), result=None)
    return marker, work


@pytest.mark.parametrize("change", ["corrupt_hash", "wrong_owner", "wrong_execution", "automatic_retry", "native_work"])
def test_only_exact_private_provider_recovery_marker_is_eligible(change):
    marker, work = original_marker()
    assert _recovery_marker_matches(marker, work)
    if change == "corrupt_hash":
        marker["payload_sha256"] = b"\x00" * 32
    elif change == "wrong_owner":
        marker["thread_id"] = marker["thread_id"].replace("synthetic-owner", "other-owner")
    elif change == "wrong_execution":
        marker["work_id"] = "work:unbound"
    elif change == "automatic_retry":
        marker["payload"]["data"]["automatic_retry"] = True
        marker["payload_sha256"] = bytes.fromhex(canonical_sha256(marker["payload"]))
    else:
        work.spec = DelegatedInitiativeDigitalWorkSpec.model_construct(**work.spec.model_dump())
    assert not _recovery_marker_matches(marker, work)


def test_existing_queue_runs_before_bounded_scheduled_observation():
    repair = SimpleNamespace(service=SimpleNamespace(store=object()), observe=Mock(), run_once=Mock(
        side_effect=["waiting", "admitted", "busy", "idle"]))
    intake = _ScheduledFailureRepair(repair)
    intake._observe_one = Mock()
    assert intake.observe is repair.observe
    for state in ("waiting", "admitted", "busy"):
        assert intake.run_once() == state
    intake._observe_one.assert_not_called()
    assert intake.run_once() == "idle"
    intake._observe_one.assert_called_once_with()
