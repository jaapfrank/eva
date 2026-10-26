"""Offline checks of status-only metadata at the existing timeline boundary."""
from datetime import UTC, datetime
import hashlib
import json
from types import SimpleNamespace

import pytest

from eva.adapters.model.contracts import ConversationModelError
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.free_will import AutonomousUsageUncertain


NOW = datetime(2026, 10, 26, 8, tzinfo=UTC)
ABSENT = object()


class IntSubclass(int):
    pass


class RecordingTimeline:
    def __init__(self, phase):
        self.phase = phase
        self.transitions = []

    def load(self, thread_id):
        return SimpleNamespace(phase=self.phase)

    def transition(self, **values):
        self.transitions.append(values)


def record_failure(status=ABSENT, *, usage_uncertain=False):
    timeline = RecordingTimeline("will_claimed" if usage_uncertain else "will_model_started")
    store = object.__new__(PostgresSensoryEventStore)
    store._timeline = timeline
    store._household_binding = None
    claim = SimpleNamespace(thread_id="sensory:initiative-review:fixture:1", revision=3,
        operation_id="fixture-operation", retry_count=0,
        event=SimpleNamespace(source_event_id="initiative-review:fixture:1"))
    kind = AutonomousUsageUncertain if usage_uncertain else ConversationModelError
    error = kind("private upstream body and credentials must not be retained")
    if status is not ABSENT:
        error.failure_http_status_code = status
    store.fail(claim, error, now=NOW)
    assert len(timeline.transitions) == 1
    transition = timeline.transitions[0]
    assert transition["phase"] == "will_usage_uncertain"
    assert transition["status"] == "needs_help"
    assert transition["event_type"] == "WILL_USAGE_UNCERTAIN"
    assert transition["thread_id"] == claim.thread_id
    assert transition["expected_revision"] == claim.revision
    assert transition["operation_id"] == claim.operation_id
    assert transition["retry_count"] == 1
    assert transition["last_error_fingerprint"] == hashlib.sha256(
        b"autonomous_provider_usage_unverified").hexdigest()
    data = transition["data"]
    assert data["usage"] is None
    assert data["reason"] == "autonomous_provider_usage_unverified"
    assert data["source_event_id"] == claim.event.source_event_id
    assert data["failure_type"] == kind.__name__
    assert data["failure_fingerprint"] == hashlib.sha256(
        f"{kind.__name__}: {error}".encode()).hexdigest()
    assert data["provider_usage_error"] is usage_uncertain
    assert "private upstream body" not in json.dumps(transition, default=str)
    return transition


@pytest.mark.parametrize("status", [100, 429, 599])
@pytest.mark.parametrize("usage_uncertain", [False, True])
def test_valid_http_status_adds_only_status_to_existing_failure(status, usage_uncertain):
    original = record_failure(usage_uncertain=usage_uncertain)
    enriched = record_failure(status, usage_uncertain=usage_uncertain)
    assert enriched["data"].pop("failure_http_status_code") == status
    assert enriched == original


@pytest.mark.parametrize("status", [ABSENT, None, True, False, "429", 429.0, 99, 600, IntSubclass(429)])
def test_missing_or_invalid_http_status_leaves_existing_failure_unchanged(status):
    original = record_failure()
    actual = record_failure(status)
    assert "failure_http_status_code" not in actual["data"]
    assert actual == original
