"""Website return eligibility is not a Telegram delivery receipt."""
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from eva.adapters.postgres.private_work_return import private_episode
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import ConversationTurn
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService


def setup():
    service = object.__new__(DelegatedWorkOwnerService)
    service.owner_principal_id, service.principal_binding_id = uuid4(), uuid4()
    service._private_bridge_principal_loader = None
    now = datetime(2026, 9, 23, 12, tzinfo=UTC)
    service._clock = lambda: now
    service._delivery_status_loader = lambda _: "unbound"
    turn = ConversationTurn.private("tenant:test", "installation:test",
        str(service.owner_principal_id), "dashboard:" + str(service.principal_binding_id),
        "message:test", "Do some work", "2026-09-23T12:00:00Z")
    service.conversation_scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "test")
    work = NS(spec=NS(work_id="work:test", audience="private",
        owner_principal_id=str(service.owner_principal_id),
        principal_binding_id=str(service.principal_binding_id), episode_id=private_episode(turn)),
        retention_until=now+timedelta(days=1), stop_requested_at=None, state="succeeded")
    return service, turn, work


@pytest.mark.parametrize("state", ["admitted", "call_started", "succeeded", "verified"])
def test_same_website_episode_reports_route_not_delivery(state):
    service, turn, work = setup()
    work.state = state
    view = service._private_delivery_view(work, turn)
    assert view["state"] == "private_route_available"
    assert "geen aflevering" in view["description"]


@pytest.mark.parametrize("change", ["episode", "owner", "binding", "audience", "expired", "stopped", "failed", "route"])
def test_unavailable_or_other_route_does_not_claim_website_return(change):
    from dataclasses import replace
    service, turn, work = setup()
    if change == "episode": work.spec.episode_id = str(uuid4())
    if change == "owner": work.spec.owner_principal_id = str(uuid4())
    if change == "binding": work.spec.principal_binding_id = str(uuid4())
    if change == "audience": work.spec.audience = "household"
    if change == "expired": work.retention_until = service.now()
    if change == "stopped": work.stop_requested_at = service.now()
    if change == "failed": work.state = "failed"
    if change == "route": turn = replace(turn, conversation_id="telegram:123")
    view = service._private_delivery_view(work, turn)
    assert view["state"] == "unbound"
    assert "Telegram" in view["description"]


@pytest.mark.parametrize("state", ["awaiting_result", "ready", "delivery_acknowledged", "delivery_ambiguous", "blocked"])
def test_existing_telegram_delivery_status_is_preserved(state):
    service, turn, work = setup()
    service._delivery_status_loader = lambda _: state
    assert service._private_delivery_view(work, turn) == service._delivery_view(work.spec.work_id)
