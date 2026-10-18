"""Ordinary private contact contracts; no provider or transport calls."""
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID
import json

import pytest

from eva.adapters.postgres.initiative_execution import PostgresInitiativeAuthority, InitiativeMandateDenied
from eva.application.free_will import ProactiveExpressionClaim, ProactiveExpressionWorker

NOW = datetime(2026, 10, 18, tzinfo=UTC)
OWNER = "33333333-3333-3333-8333-333333333333"


def _authority(contact):
    data = {"contract": "EvaCostlessInitiativeMandate/v1", "mandate_id": "fixture",
        "owner_principal_id": OWNER, "enabled": True, "authorization_source": "fixture",
        "tenant_scope": OWNER, "installation_scope": OWNER, "capabilities": []}
    if contact != "missing":
        data["private_contact"] = contact
    owner = SimpleNamespace(_engine=object(), _owner=UUID(OWNER),
        _scope=SimpleNamespace(tenant_id=UUID(OWNER), installation_id=UUID(OWNER)))
    authority = object.__new__(PostgresInitiativeAuthority)
    authority.owner = owner
    authority._read_mandate = lambda: json.dumps(data)
    return authority


@pytest.mark.parametrize("enabled", [True, False])
def test_private_contact_contract_is_strict_and_optional(enabled):
    assert "private_contact" not in _authority("missing")._read()
    contact = {"enabled": enabled, "channel": "telegram", "owner_principal_id": OWNER}
    assert _authority(contact)._read()["private_contact"] == contact


@pytest.mark.parametrize("contact", [None, {},
    {"enabled": 1, "channel": "telegram", "owner_principal_id": OWNER},
    {"enabled": True, "channel": "email", "owner_principal_id": OWNER},
    {"enabled": True, "channel": "telegram", "owner_principal_id": "other"},
    {"enabled": True, "channel": "telegram", "owner_principal_id": OWNER, "extra": True}])
def test_private_contact_rejects_broadened_or_borrowed_authority(contact):
    with pytest.raises(InitiativeMandateDenied):
        _authority(contact)._read()


@pytest.mark.parametrize("guard_fails", [False, True])
def test_guard_runs_after_claim_before_transport(guard_fails):
    claim = ProactiveExpressionClaim("private:one", "one", 2, 0, "one", "Private question", 0)
    order = []
    def guard(actual):
        assert actual == claim
        order.append("guard")
        if guard_fails:
            raise ValueError("permission revoked")
    def reject(actual, error, **kw):
        assert actual == claim and isinstance(error, ValueError)
        order.append("known_not_sent")
    store = SimpleNamespace(claim_next_expression=lambda **kw: (order.append("claim") or claim),
        fail_before_expression_send=reject,
        fail_expression=lambda *a, **kw: pytest.fail("pre-send failure is not unknown transport"),
        acknowledge_expression=lambda *a, **kw: order.append("receipt"))
    delivery = SimpleNamespace(deliver=lambda actual: (order.append("transport") or "receipt"))
    worker = ProactiveExpressionWorker(store, delivery, channel="test", before_send=guard)
    assert worker.run_once(now=NOW) == ("failed" if guard_fails else "delivered")
    assert order == (["claim", "guard", "known_not_sent"] if guard_fails else
                     ["claim", "guard", "transport", "receipt"])
