"""Exercise real initiative composition and delivery with isolated boundaries."""
from datetime import UTC, datetime
from types import SimpleNamespace
import pytest
from eva.application.free_will import ProactiveExpressionClaim
from eva.bootstrap import household_initiative as composition
from eva.bootstrap import household_initiative_delivery as delivery_module
from tests.bootstrap.test_household_initiative_loop import configured_loop, SAT


@pytest.mark.parametrize("at,expected", [
    (datetime(2026, 10, 16, 19, 59, tzinfo=UTC), True),
    (datetime(2026, 10, 16, 20, 0, tzinfo=UTC), False),
    (datetime(2026, 10, 16, 22, 0, tzinfo=UTC), False),
    (datetime(2026, 10, 17, 5, 59, tzinfo=UTC), False),
    (datetime(2026, 10, 17, 6, 0, tzinfo=UTC), True),
])
def test_actual_composed_window_keeps_amsterdam_quiet_hours(monkeypatch, at, expected):
    monkeypatch.setattr(composition, "PostgresSensoryEventStore", lambda *a, **kw: object())
    monkeypatch.setattr(composition, "SensoryDecisionWorker", lambda *a, **kw: object())
    loop = configured_loop()
    _, _, delivery, window = loop._components_for(loop._selected())
    assert window(at) is expected
    assert delivery.delivery_window is window


@pytest.mark.parametrize("transition", ["already_busy", "first_state_read", "second_state_read", "final_provenance", "quiet_hours_begin", "missing_extension_validator", "extension_busy", "extension_quiet", "none"])
def test_pending_message_respects_late_attention_changes(monkeypatch, transition):
    available = [transition != "already_busy"]
    now = [datetime(2026, 10, 16, 12, tzinfo=UTC)]
    calls = []
    claim = ProactiveExpressionClaim("synthetic-thread", "synthetic-operation", 1, 0,
                                    "synthetic-source", "A synthetic shared message", 1)
    class Store:
        def __init__(self, *args, **kwargs): self.authorizations = 0
        def claim_next_household_expression(self, **kwargs): calls.append("claim"); return claim
        def authorize_household_expression(self, *args, **kwargs):
            self.authorizations += 1
            if self.authorizations == 2:
                if transition == "final_provenance": available[0] = False
                if transition == "quiet_hours_begin": now[0] = now[0].replace(hour=20)
        def household_expression_requires_guard(self, claim): return transition == "missing_extension_validator"
        def acknowledge_household_expression(self, *args, **kwargs): calls.append(kwargs["disposition"])
        def fail_expression(self, *args, **kwargs): raise AssertionError("No ambiguous failure expected")
    class Client:
        def __init__(self, **kwargs): self.reads = 0
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path):
            self.reads += 1; calls.append("read")
            if transition == {1: "first_state_read", 2: "second_state_read"}.get(self.reads):
                available[0] = False
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"entity_id": SAT, "state": "idle"})
        def post(self, path, **kwargs):
            calls.append("post"); return SimpleNamespace(status_code=200, raise_for_status=lambda: None)
    monkeypatch.setattr(composition, "PostgresSensoryEventStore", Store)
    monkeypatch.setattr(composition, "SensoryDecisionWorker", lambda *a, **kw: object())
    monkeypatch.setattr(delivery_module.httpx, "Client", Client)
    loop = configured_loop(can_decide=lambda: available[0])
    loop.provider = SimpleNamespace(endpoint_url="http://isolated.invalid", credential="synthetic")
    _, _, delivery, _ = loop._components_for(loop._selected())
    delivery.clock = lambda: now[0]
    if transition in {"extension_busy", "extension_quiet"}:
        def validate(claim):
            if transition == "extension_busy": available[0] = False
            else: now[0] = now[0].replace(hour=20)
        delivery.validate_expression = validate
    result = delivery.run_once()
    assert result == ("offered" if transition == "none" else "idle" if transition in {"already_busy", "first_state_read"} else "interrupted")
    assert ("post" in calls) == (transition == "none")
    assert ("claim" in calls) == (transition not in {"already_busy", "first_state_read"})
    if transition == "already_busy": assert calls == []
    if transition in {"second_state_read", "final_provenance", "quiet_hours_begin"}: assert calls[-1] == "interrupted"


@pytest.mark.parametrize("transition", [
    "paused_before_validation", "pause_during_final_provenance", "admission_replay",
    "offered", "uncertain_post", "ack_failure_after_offered",
    "attention_closed_during_admission", "quiet_hours_during_admission",
])
def test_provision_admission_orders_pause_and_preserves_uncertain_effects(monkeypatch, transition):
    calls, settlements = [], []
    available = [True]
    now = [datetime(2026, 10, 16, 12, tzinfo=UTC)]
    paused = [transition == "paused_before_validation"]
    claim = ProactiveExpressionClaim("fixture-thread", "fixture-operation", 1, 0,
                                    "fixture-source", "An isolated fixture message", 1)

    class Store:
        def __init__(self, *args, **kwargs): self.authorizations = 0
        def claim_next_household_expression(self, **kwargs): return claim
        def authorize_household_expression(self, *args, **kwargs):
            self.authorizations += 1
            calls.append("provenance")
            if self.authorizations == 3 and transition == "pause_during_final_provenance":
                paused[0] = True
        def household_expression_requires_guard(self, claim): return True
        def acknowledge_household_expression(self, *args, **kwargs):
            if transition == "ack_failure_after_offered":
                raise RuntimeError("isolated acknowledgement failure")
            calls.append(kwargs["disposition"])
        def fail_expression(self, *args, **kwargs): calls.append("ambiguous")

    class Client:
        def __init__(self, **kwargs): pass
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, path):
            return SimpleNamespace(raise_for_status=lambda: None,
                                   json=lambda: {"entity_id": SAT, "state": "idle"})
        def post(self, path, **kwargs):
            calls.append("post")
            if transition == "uncertain_post":
                raise RuntimeError("isolated transport outcome unknown")
            return SimpleNamespace(status_code=200, raise_for_status=lambda: None)

    def admit(value):
        assert value is claim
        assert calls[-1] == "provenance"
        calls.append("admit")
        if transition == "attention_closed_during_admission": available[0] = False
        if transition == "quiet_hours_during_admission": now[0] = now[0].replace(hour=20)
        return {"admitted_now": not paused[0] and transition != "admission_replay",
                "receipt": {"operation_id": value.operation_id}}

    def settle(admission, *, outcome, proof_sha256):
        assert admission["admitted_now"] is True
        assert len(proof_sha256) == 64
        settlements.append(outcome)

    monkeypatch.setattr(composition, "PostgresSensoryEventStore", Store)
    monkeypatch.setattr(composition, "SensoryDecisionWorker", lambda *a, **kw: object())
    monkeypatch.setattr(delivery_module.httpx, "Client", Client)
    loop = configured_loop(can_decide=lambda: available[0])
    loop.provider = SimpleNamespace(endpoint_url="http://isolated.invalid", credential="fixture")
    _, _, delivery, _ = loop._components_for(loop._selected())
    delivery.clock = lambda: now[0]
    delivery.validate_expression = lambda value: not paused[0]
    delivery.admit_expression, delivery.settle_expression = admit, settle

    result = delivery.run_once()
    attention_closed = transition in {"attention_closed_during_admission", "quiet_hours_during_admission"}
    denied = attention_closed or transition in {"paused_before_validation", "pause_during_final_provenance", "admission_replay"}
    assert result == ("interrupted" if denied else "offered" if transition == "offered" else "ambiguous")
    assert ("post" in calls) is not denied
    assert settlements == (["not_dispatched"] if attention_closed else [] if denied else ["unknown"] if transition == "uncertain_post" else ["completed"])
    if "post" in calls:
        assert calls.index("admit") < calls.index("post")
    if transition == "paused_before_validation":
        assert "admit" not in calls
