from datetime import UTC, datetime, timedelta
from dataclasses import replace
import pytest
import sqlalchemy as sa
from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.adapters.postgres.conversation_actions import (
    PostgresPersonalInterestStore, PostgresPersonalPrototypeStore,
)
from eva.adapters.postgres.operational_timeline import CheckpointConflict
from eva.adapters.postgres.current_catalog import metadata
from eva.application.free_will import FreeWillDecision, PersonalInitiativeProposal
from eva.application.personal_interests import PersonalInterestExploration, PersonalPrototype
from tests.postgres.test_delegated_work_store import _seed_authority, _id
from tests.postgres.test_sensory_admission import _event

NOW = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)


def test_owner_can_read_result_and_stop_claimed_work_without_resurrection(postgres_engine):
    label = "initiative-owner-control"
    authority, _, binding = _seed_authority(postgres_engine, label)
    owner = _id(label, "principal")
    tenant, installation = "initiative-owner-external", "home"
    source = PostgresSensoryEventStore(postgres_engine, tenant_id=tenant,
        installation_id=installation, initiative_principal_id=str(owner))
    def control(who=owner):
        return PostgresOwnerInitiatives(postgres_engine, tenant_id=tenant,
            installation_id=installation, owner=who, binding=binding, authority_scope=authority)
    decision = FreeWillDecision("silence", "Eigen plan", 0, "fixture",
        initiative=PersonalInitiativeProposal("Gezinsplan", "Voor het gezin", "Maak een gezinsplan"))
    def adopt(name, at):
        source.retain(replace(_event(name), occurred_at=at))
        claim = source.mark_model_started(source.claim_next(now=at), now=at)
        source.complete(claim, decision, now=at)
        return source._timeline.events(claim.thread_id)[-1].payload["data"]["initiative"]["intent_id"]
    intent = adopt("first", NOW)
    assert control().page(now=NOW)["cards"][0]["intent_id"] == intent
    interest = PostgresPersonalInterestStore(postgres_engine)
    claim = interest.claim_next(tenant_id=tenant, installation_id=installation, now=NOW)
    assert claim is not None
    assert control().stop(intent, now=NOW)["status"] == "stopped"
    assert control().stop(intent, now=NOW)["status"] == "stopped"
    with pytest.raises(CheckpointConflict):
        interest.complete(claim, PersonalInterestExploration("Late result", "fixture"), now=NOW)
    assert interest.claim_next(tenant_id=tenant, installation_id=installation, now=NOW) is None
    assert control().page(now=NOW)["cards"][0]["status"] == "stopped"
    at = NOW + timedelta(seconds=1)
    next_intent = adopt("second", at)
    assert next_intent != intent
    claim = interest.claim_next(tenant_id=tenant, installation_id=installation, now=at)
    completed = interest.complete(claim, PersonalInterestExploration("Planinhoud", "fixture"), now=at)
    interest.mark_projected(completed, now=at)
    prototypes = PostgresPersonalPrototypeStore(postgres_engine)
    prototype_claim = prototypes.claim_next(tenant_id=tenant, installation_id=installation, now=at)
    artifact = PersonalPrototype("Gezinsplan", "gezinsplan.md", "text/markdown", "# Plan\nSamen iets doen.", "fixture")
    prototypes.complete(prototype_claim, artifact, now=at)
    assert control().artifact(next_intent, now=at) == {
        "filename": artifact.filename, "content": artifact.content, "sha256": artifact.content_sha256}
    assert any(card["has_artifact"] for card in control().page(now=at)["cards"])
    with pytest.raises(RuntimeError, match="binding is not current"):
        control(_id(label, "other")).artifact(next_intent, now=at)
    with pytest.raises(LookupError):
        control().stop("personal-interest:" + "0"*64, now=at)
    table = metadata.tables["eva_core.principal_bindings"]
    with postgres_engine.begin() as conn:
        conn.execute(sa.update(table).where(table.c.tenant_id == authority.tenant_id,
            table.c.principal_binding_id == binding).values(status="revoked"))
    with pytest.raises(RuntimeError, match="binding is not current"):
        control().stop(next_intent, now=at)
