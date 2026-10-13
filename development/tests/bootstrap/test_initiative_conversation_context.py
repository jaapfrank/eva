"""Native self-state reaches the verified private conversation without a tool roundtrip."""
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace as NS
from uuid import UUID

import pytest

from eva.adapters.postgres.delegated_work import DelegatedWorkConflict
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import ConversationTurn
from eva.application.memory_audience import HouseholdConsentReference, MemoryAudience
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService

OWNER = UUID("33333333-3333-4333-8333-333333333333")
BINDING = UUID("44444444-4444-4444-8444-444444444444")
NOW = datetime(2026, 10, 2, 12, tzinfo=UTC)
ALIAS = "telegram:private:owner"


def turn(principal=ALIAS):
    return ConversationTurn.private(tenant_id="home", installation_id="eva",
        principal_id=principal, conversation_id=principal, message_id="message:context",
        text="Waar ben je mee bezig?", occurred_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))


@pytest.fixture
def context_service():
    authority = {"current": True, "alias": ALIAS}
    reads = []
    def authorize(**kwargs):
        if not authority["current"]:
            raise DelegatedWorkConflict("binding revoked")
    store = NS(scope=external_timeline_scope("home", "eva", "scope"),
        validate_admission_authority=authorize)
    service = DelegatedWorkOwnerService(store, owner_principal_id=OWNER,
        principal_binding_id=BINDING, clock=lambda: NOW,
        private_bridge_principal_loader=lambda: authority["alias"])
    cards = [dict(intent_id="personal-interest:" + "a"*64,
        title="Een eigen bijdrage", motivation="Aandacht voor het gezin",
        status="done", phase="initiative_completed", updated_at="2026-09-30T06:16:00Z",
        has_artifact=True, feedback=None, result_preview=("Bewaard resultaat. " * 60)[:500],
        result_truncated=True),
        dict(intent_id="personal-interest:" + "b"*64,
        title="Een oud idee", motivation="Eerdere belangstelling",
        status="stopped", phase="personal_stopped", updated_at="2026-10-01T12:00:00Z",
        has_artifact=False, feedback="Niet verder uitwerken.")]
    def page(*, now, limit, include_result_preview):
        assert include_result_preview is True
        reads.append(("page", limit))
        return {"cards": cards, "truncated": True}
    def artifact(identifier, *, now):
        reads.append(("artifact", identifier))
        return {"content": "Bewaard resultaat. " * 60}
    service._initiative_store = lambda incoming: NS(page=page, artifact=artifact)
    return service, authority, cards, reads


def test_same_native_result_for_owner_and_trusted_private_alias(context_service):
    service, authority, cards, reads = context_service
    owner = service.own_initiative_metadata(turn(str(OWNER)))
    alias = service.own_initiative_metadata(turn())
    assert owner == alias
    assert alias["limited"] is True
    done, stopped = alias["initiatives"]
    assert done["phase"] == "initiative_completed" and done["status"] == "done"
    assert len(done["result_preview"]) == 500 and done["result_truncated"]
    assert done["feedback"] is None
    assert stopped["status"] == "stopped" and "result_preview" not in stopped
    assert stopped["feedback"] == "Niet verder uitwerken."
    assert all(value == 3 for kind, value in reads if kind == "page")


@pytest.mark.parametrize("audience", ["other_person", "household", "revoked_alias", "revoked_binding"])
def test_private_self_state_never_reaches_wrong_or_revoked_audience(context_service, audience):
    service, authority, cards, reads = context_service
    incoming = turn()
    if audience == "other_person":
        incoming = turn("other-person")
    elif audience == "household":
        incoming = replace(incoming,
            conversation_id="household:family:conversation:test",
            message_id="household:family:conversation:test:message",
            audience=MemoryAudience.household(household_id="family", principal_id=ALIAS,
                consent_ref=HouseholdConsentReference(ALIAS, "family", "household-consent:test")))
    elif audience == "revoked_alias":
        authority["alias"] = None
    else:
        authority["current"] = False
    with pytest.raises(DelegatedWorkConflict):
        service.own_initiative_metadata(incoming)
    assert reads == []


def test_alias_revocation_during_read_discards_context(context_service):
    service, authority, cards, reads = context_service
    def page(**kwargs):
        authority["alias"] = None
        return {"cards": [], "truncated": False}
    service._initiative_store = lambda incoming: NS(page=page)
    with pytest.raises(DelegatedWorkConflict):
        service.own_initiative_metadata(turn())


def test_own_result_available_in_social_context_without_active_delegated_work(context_service):
    service, authority, cards, reads = context_service
    service.active_work_metadata = lambda incoming: {"jobs": [], "truncated": False}
    context = PostgresDelegatedWorkActions(None, service).active_context(turn())
    assert len(context) == 1
    assert "Bewaard resultaat." in context[0]
    assert '"phase":"personal_stopped"' in context[0]
    assert '"status":"done"' in context[0]
    assert "niet de uitvoeringsdatum" in context[0]
    assert "geen opdrachten" in context[0]
