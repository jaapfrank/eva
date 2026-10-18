"""The existing service-loop transport rechecks current foreground state before sending."""
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from eva.bootstrap import native_initiative_work
from eva.application.free_will import ProactiveExpressionClaim


def test_new_foreground_activity_stops_private_contact_before_transport(monkeypatch):
    now = datetime(2026, 10, 18, tzinfo=UTC)
    monkeypatch.setattr(native_initiative_work, "datetime", SimpleNamespace(now=lambda tz: now))
    claim = ProactiveExpressionClaim("thread", "operation", 3, 0, "source", "A private question?", 1)
    store = Mock()
    store.claim_next_expression.return_value = claim
    delivery = Mock()
    run = native_initiative_work.private_contact_delivery(store, delivery, can_send=lambda: False)
    assert run() == "failed"
    delivery.deliver.assert_not_called()
    store.authorize_private_expression.assert_not_called()
    store.fail_before_expression_send.assert_called_once()


def test_allowed_contact_uses_current_claim_guard_before_existing_transport(monkeypatch):
    now = datetime(2026, 10, 18, tzinfo=UTC)
    monkeypatch.setattr(native_initiative_work, "datetime", SimpleNamespace(now=lambda tz: now))
    claim = ProactiveExpressionClaim("thread", "operation", 3, 0, "source", "A private question?", 1)
    calls = []
    store = Mock()
    store.claim_next_expression.return_value = claim
    store.authorize_private_expression.side_effect = lambda *args, **kwargs: calls.append("guard")
    delivery = SimpleNamespace(deliver=lambda value: calls.append("send") or "telegram:42:1")
    run = native_initiative_work.private_contact_delivery(store, delivery, can_send=lambda: True)
    assert run() == "delivered" and calls == ["guard", "send"]
    store.authorize_private_expression.assert_called_once_with(claim, now=now)
    store.acknowledge_expression.assert_called_once()


def test_foreground_starting_during_authority_read_blocks_transport(monkeypatch):
    now = datetime(2026, 10, 18, tzinfo=UTC)
    monkeypatch.setattr(native_initiative_work, "datetime", SimpleNamespace(now=lambda tz: now))
    active = {"value": False}
    claim = ProactiveExpressionClaim("thread", "operation", 3, 0, "source", "A private question?", 1)
    store = Mock()
    store.claim_next_expression.return_value = claim
    store.authorize_private_expression.side_effect = lambda *args, **kwargs: active.update(value=True)
    delivery = Mock()
    run = native_initiative_work.private_contact_delivery(store, delivery, can_send=lambda: not active["value"])
    assert run() == "failed"
    delivery.deliver.assert_not_called()
    store.fail_before_expression_send.assert_called_once()
