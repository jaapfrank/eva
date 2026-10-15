"""Retained source feedback reaches only private decisions, without redispatch."""
from datetime import UTC, datetime
from types import SimpleNamespace
import json
import pytest

from eva.application.free_will import FreeWillDecision, SensoryDecisionClaim, SensoryDecisionWorker
from eva.application.conversation import IdentityProfile, ConversationActionCall, ConversationModelUsage
from eva.application.sensory import SensoryEvent
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.contracts.canonical_json import canonical_sha256
from tests.model.test_conversation_model import _memory

NOW = datetime(2026, 10, 15, 10, tzinfo=UTC)
OUTCOMES = ({"work_id": "work:eva-initiative:" + "a" * 64,
    "spec_sha256":"e"*64,"result_sha256":"f"*64,
    "reported_output": {"text": "Reported interpretation", "semantic_verification": False},
    "verified_reads": [{"observed_at": "2026-10-15T09:50:00Z",
        "observation": {"events": []}, "receipt_sha256": "b" * 64}]},)

@pytest.mark.parametrize("source", ["initiative-review", "home-assistant"])
@pytest.mark.parametrize("foreground_after_read", [False, True])
@pytest.mark.parametrize("triggered",[False,True])
def test_worker_feedback_is_private_and_hashes_exact_context(source, foreground_after_read, triggered):
    event = SensoryEvent(source, "fixture-feedback", "private_opportunity_review" if source == "initiative-review"
        else "state_changed", NOW, {"entity_id": "binary_sensor.synthetic", "old_state": "off", "new_state": "on"})
    if source == "initiative-review" and triggered:
        from dataclasses import replace
        event=replace(event,payload={"native_completion_source":{k:OUTCOMES[0][k] for k in ("work_id","spec_sha256","result_sha256")}})
    claim = SensoryDecisionClaim(event, "sensory:fixture", "fixture", 1, 0)
    calls, completed, reads, deferred = [], [], [], []
    def read(**kw):
        reads.append(kw)
        return OUTCOMES
    class Store:
        def claim_next(self, **kw): return claim
        def mark_model_started(self, actual, **kw): return actual
        def context_review_claim_is_current(self, actual, **kw): return True
        def complete(self, actual, decision, **kw): completed.append(decision)
        def defer_before_dispatch(self, actual, **kw): deferred.append(kw)
        def fail(self, actual, error, **kw): raise error
    class Model:
        def evaluate_sensory_event(self, *args, **kw):
            calls.append(kw)
            return FreeWillDecision("silence", "No currently useful action", 0, "synthetic")
    worker = SensoryDecisionWorker(Store(), Model(), SimpleNamespace(recall_for_presence=lambda q: _memory()),
        IdentityProfile("Eva", "Housemate", ("Honest",), 80), tenant_id="tenant", installation_id="home",
        principal_id="owner", context_review_authorizer=lambda: None,
        context_review_admission=lambda *a, **kw: SimpleNamespace(admitted=True), source_outcome_reader=read,
        dispatch_ready=lambda: not (foreground_after_read and reads))
    outcome = worker.run_once(now=NOW)
    if source == "initiative-review" and foreground_after_read:
        assert outcome == "deferred" and calls == [] and completed == []
        assert deferred == [{"reason": "foreground_active", "now": NOW}]
        return
    assert outcome == "silence"
    assert len(calls) == len(completed) == 1
    if source == "initiative-review":
        assert reads == [{"now": NOW, **({"work_id":OUTCOMES[0]["work_id"]} if triggered else {})}]
        assert calls[0]["initiative_source_outcomes"] == OUTCOMES
        assert completed[0].source_context_sha256 == canonical_sha256(list(OUTCOMES))
    else:
        assert reads == [] and "initiative_source_outcomes" not in calls[0]
        assert completed[0].source_context_sha256 is None


def test_model_includes_original_source_time_and_unconfirmed_report(monkeypatch, tmp_path):
    model = PydanticConversationModel(auth_file=tmp_path / "not-read.json")
    bodies = []
    def request(body, *a, **kw):
        bodies.append(body)
        return (("", ConversationActionCall("choice", "submit_sensory_decision", {
            "disposition": "silence", "expression": None, "reason": "Read source is not an urgent event",
            "urgency": 0, "initiative": None})), ConversationModelUsage.measured(
                model_attempts=1, tool_calls=1, input_tokens=120, output_tokens=20))
    monkeypatch.setattr(model, "_request_using", request)
    event = SensoryEvent("initiative-review", "fixture", "private_opportunity_review", NOW, {})
    result = model.evaluate_sensory_event(event, IdentityProfile("Eva", "Housemate", ("Honest",), 80),
        _memory(), initiative_source_outcomes=OUTCOMES)
    payload = json.loads(bodies[0]["input"][0]["content"])
    assert payload["native_source_outcomes"] == list(OUTCOMES)
    assert result.disposition == "silence"
    assert "onbevestigde interpretatie" in bodies[0]["instructions"]
    assert "oorspronkelijke tijd" in bodies[0]["instructions"]
    assert len(bodies[0]["tools"]) == 1  # No second source-execution route.
    with pytest.raises(ValueError, match="private opportunity"):
        model.evaluate_sensory_event(SensoryEvent("household-initiative", "shared", "household_observation_review", NOW, {}),
            IdentityProfile("Eva", "Housemate", ("Honest",), 80), _memory(), initiative_source_outcomes=OUTCOMES)
    assert len(bodies) == 1
