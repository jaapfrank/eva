"""Real PostgreSQL reference-only state on the installed schema, no model/effects."""
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import make_url

from eva.adapters.postgres.housemate_development import PostgresDevelopmentSelection
from eva.adapters.postgres.operational_timeline import operational_events
from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
from eva.application.housemate_development import select_development, resolve_development
from tests.application.test_housemate_development import source
from tests.postgres.test_delegated_work_store import _seed_authority, _id

NOW = datetime(2026,8,26,12,tzinfo=UTC)


@pytest.fixture(scope="module")
def e110_engine():
    dsn = os.environ.get("EVA2_E110_TEST_DSN", "")
    if not dsn:
        pytest.fail("EVA2_E110_TEST_DSN must name an explicitly prepared isolated schema0011 database")
    url = make_url(dsn)
    if url.get_backend_name() != "postgresql" or not url.database.startswith("eva2_4b_disposable_e110_"):
        pytest.fail("E110 requires its own disposable PostgreSQL database")
    engine = sa.create_engine(url)
    with engine.connect() as conn:
        expected_version = os.environ.get("EVA2_E110_TEST_SERVER_VERSION", "160013")
        assert expected_version in {"160013", "180006"}
        assert conn.scalar(sa.text("SHOW server_version_num")) == expected_version
        assert conn.scalar(sa.text("SELECT version_num FROM public.eva_alembic_version")) == "4e_0011_timeline_type_time"
    yield engine
    engine.dispose()


def setup(engine):
    label = "e110-" + uuid4().hex[:12]
    authority,_,binding = _seed_authority(engine,label)
    principal = _id(label,"principal")
    owner = PostgresOwnerInitiatives(engine,tenant_id="tenant:"+label,installation_id="installation:"+label,
        owner=principal,binding=binding,authority_scope=authority)
    return owner, PostgresDevelopmentSelection(owner)


def test_durable_references_idempotency_and_private_authority(e110_engine):
    owner,store = setup(e110_engine)
    principal = str(owner._owner)
    feedback = source("For research give the recommendation first",principal=principal,at="2026-08-26T11:00:00Z")
    def selection(sources):
        return select_development(owner_id=principal,principals=(principal,),sources=sources,projects=[],
            now=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert not store.save(selection([]),now=NOW)
    assert store.load(now=NOW) is None
    assert store.save(selection([feedback]),now=NOW)
    assert not store.save(selection([feedback]),now=NOW)
    current = store.load(now=NOW)
    assert current["revision"] == 1
    assert "recommendation first" not in str(current)
    with e110_engine.connect() as conn:
        assert conn.scalar(sa.select(sa.func.count()).select_from(operational_events).where(
            operational_events.c.thread_id == store.thread)) == 1
    # A source erased/corrected outside the dream becomes unavailable immediately.
    assert resolve_development(current["selection"],owner_id=principal,principals=(principal,),
        sources=[],projects=[],now=NOW.strftime("%Y-%m-%dT%H:%M:%SZ")) is None
    with pytest.raises(RuntimeError,match="binding"):
        store.load(now=NOW+timedelta(days=8))
    foreign = PostgresOwnerInitiatives(e110_engine,tenant_id=owner._tenant_id,
        installation_id=owner._installation_id,owner=uuid4(),binding=owner._binding,authority_scope=owner._authority)
    with pytest.raises(RuntimeError,match="binding"):
        PostgresDevelopmentSelection(foreign).load(now=NOW)


def test_storage_rejects_copied_state_history(e110_engine):
    owner,store = setup(e110_engine)
    invalid = dict(contract="EvaDevelopmentSelection/v1",owner_id=str(owner._owner),delta_refs=[],project_refs=[],
        inner_state_history=["A copied private conversation"])
    with pytest.raises(ValueError,match="contract"):
        store.save(invalid,now=NOW)
    assert store.load(now=NOW) is None


def test_existing_project_stop_invalidates_the_current_dream_selection(e110_engine):
    from dataclasses import replace
    from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
    from eva.application.free_will import FreeWillDecision, PersonalInitiativeProposal
    from tests.postgres.test_sensory_admission import _event

    owner,store = setup(e110_engine)
    principal = str(owner._owner)
    events = PostgresSensoryEventStore(e110_engine,tenant_id=owner._tenant_id,
        installation_id=owner._installation_id,initiative_principal_id=principal)
    events.retain(replace(_event("development-own-project"),occurred_at=NOW))
    claim = events.mark_model_started(events.claim_next(now=NOW),now=NOW)
    decision = FreeWillDecision("silence","Explore a real chosen line",0,"fixture",
        initiative=PersonalInitiativeProposal("A chosen line","Curiosity","Compare two approaches"))
    events.complete(claim,decision,now=NOW)
    cards = owner.page(now=NOW,limit=2)["cards"]
    assert len(cards) == 1
    selection = select_development(owner_id=principal,principals=(principal,),sources=[],projects=cards,
        now=NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    assert store.save(selection,now=NOW)
    current = store.current_projects(selection["project_refs"],now=NOW)
    assert resolve_development(selection,owner_id=principal,principals=(principal,),sources=[],projects=current,
        now=NOW.strftime("%Y-%m-%dT%H:%M:%SZ")) is not None
    owner.stop(cards[0]["intent_id"],now=NOW)
    stopped = store.current_projects(selection["project_refs"],now=NOW)
    assert stopped[0]["status"] == "stopped"
    assert resolve_development(selection,owner_id=principal,principals=(principal,),sources=[],projects=stopped,
        now=NOW.strftime("%Y-%m-%dT%H:%M:%SZ")) is None
