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


@pytest.mark.parametrize("transition", ["already_busy", "first_state_read", "second_state_read", "final_provenance", "quiet_hours_begin", "none"])
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
    result = delivery.run_once()
    assert result == ("offered" if transition == "none" else "idle" if transition in {"already_busy", "first_state_read"} else "interrupted")
    assert ("post" in calls) == (transition == "none")
    assert ("claim" in calls) == (transition not in {"already_busy", "first_state_read"})
    if transition == "already_busy": assert calls == []
    if transition in {"second_state_read", "final_provenance", "quiet_hours_begin"}: assert calls[-1] == "interrupted"
