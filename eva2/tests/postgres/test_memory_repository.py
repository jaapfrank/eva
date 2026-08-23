from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy.engine import Engine, make_url

from eva.adapters.postgres.catalog_generated import CATALOG_SPEC
from eva.adapters.postgres.memory import PostgresMemoryRepository
from eva.adapters.postgres.noor_4d_repository import (
    ExactNoor4DRow,
    PostgresNoor4DRepository,
)
from eva.application.memory import (
    ExpectedCurrentFact,
    MemoryAuthority,
    PresenceMemory,
    RecallCurrentFacts,
    RememberFact,
)
from eva.application.presence import PresenceTurn, build_presence_context
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.memory import ExperienceSourceEvent, MemoryFact
from eva.contracts.planning import (
    CurrentIdentityPack,
    CurrentUtterance,
    EpisodeGoal,
    PresenceContext,
)


EVA2_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_PATH = (
    EVA2_ROOT / "design/vertical/noor-4d-concurrency-crash-schedules-v1.json"
)
SCHEDULE_SHA256 = "f492d746779ec5276478893ff22720c84926912371d19175dd7508a18734b91d"
NOW = "2026-08-23T10:00:00Z"
LATER = "2026-08-23T10:01:00Z"
RETENTION = "2027-08-23T10:00:00Z"
SOURCE_1 = "34ffb415-bd66-5d29-8a79-6c3ed8523848"
SOURCE_2 = "bd8b4729-f2b3-5c6e-bf8e-d4e0ebfcdd35"
FACT_1 = "43ecfc54-af4b-5848-95ae-2fb38f1b0c42"
FACT_2 = "308563a5-7f06-5066-9fd8-825927cfed36"
CURRENT_ID = "877ea196-f816-54ba-9d4e-3c483607d58c"

TABLE_SPECS = {spec["table"]: spec for spec in CATALOG_SPEC["canonical_tables"]}


def _facts() -> dict[str, dict[str, Any]]:
    raw = SCHEDULE_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SCHEDULE_SHA256
    schedule = json.loads(raw)
    catalog = schedule["portableSnapshotAuthority"]["fixtureFactCatalog"]
    return {entry["fact_id"]: entry for entry in catalog["facts"]}


FACTS = _facts()


def _column_declarations(table: str) -> dict[str, str]:
    return {
        declaration.split(" ", 1)[0]: declaration.split(" ", 2)[1]
        for declaration in TABLE_SPECS[table]["columns"]
    }


def _native_value(value: Any, declaration: str) -> Any:
    if value is None:
        return None
    if declaration == "uuid":
        return UUID(value)
    if declaration == "uuid[]":
        return [UUID(item) for item in value]
    if declaration == "bytea":
        return bytes.fromhex(value)
    if declaration in {"timestamptz", "timestamptz(0)"}:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    return value


def _exact_fact(fact_id: str) -> ExactNoor4DRow:
    fact = FACTS[fact_id]
    table = fact["table"]
    portable = fact["portable_record"]
    declarations = _column_declarations(table)
    values = {
        name: _native_value(value, declarations[name])
        for name, value in portable["row"].items()
    }
    return ExactNoor4DRow(table, values, portable["row_sha256"])


BASE_FACTS = (
    "static/tenant",
    "static/installation",
    "static/identity-pack",
    "static/policy-pack",
    "static/principal-owner",
    "evidence/evidence:authenticated-http:001",
    "evidence/evidence:direct-user-request:001",
    "static/principal-binding",
    "admitted/conversation",
    "admitted/message-input",
    "admitted/ingress",
    "episode/accepted",
    "admitted/generation-current",
    "episode-event/initialized",
    "commitment/open",
    "attention/pending",
)


@pytest.fixture(scope="module")
def memory_engine() -> Iterator[Engine]:
    dsn = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.fail("EVA2_TEST_POSTGRES_DSN is required")
    url = make_url(dsn)
    if (
        url.get_backend_name() != "postgresql"
        or not url.database
        or not url.database.startswith("eva2_4b_disposable_")
    ):
        pytest.fail("memory integration requires a disposable PostgreSQL database")
    engine = sa.create_engine(url, pool_pre_ping=False)
    config = Config(str(EVA2_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    try:
        alembic_command.upgrade(config, "4b_0000_core_namespace")
        with engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
            config.attributes["connection"] = connection
            try:
                alembic_command.upgrade(config, "head")
            finally:
                config.attributes.pop("connection", None)
        yield engine
    finally:
        try:
            alembic_command.downgrade(config, "base")
        finally:
            engine.dispose()


def _scope() -> tuple[str, str, str, str]:
    tenant = _exact_fact("static/tenant").values["tenant_id"]
    installation = _exact_fact("static/installation").values["installation_id"]
    principal = _exact_fact("static/principal-owner").values["principal_id"]
    evidence = _exact_fact("evidence/evidence:direct-user-request:001").values[
        "evidence_id"
    ]
    return str(tenant), str(installation), str(principal), str(evidence)


def _source(
    *,
    source_id: str,
    principal_id: str,
    evidence_id: str,
    at: str,
    correction_of: str | None = None,
) -> ExperienceSourceEvent:
    return ExperienceSourceEvent.model_validate(
        {
            "schema_version": 1,
            "contract": "ExperienceSourceEvent/v1",
            "source_event_id": source_id,
            "tenant_id": _scope()[0],
            "principal_id": principal_id,
            "subject_id": principal_id,
            "event_type": (
                "preference_corrected" if correction_of else "preference_stated"
            ),
            "source_kind": (
                "direct_user_correction" if correction_of else "direct_user_input"
            ),
            "source_ref": f"telegram:message:{source_id[:8]}",
            "content_sha256": hashlib.sha256(source_id.encode()).hexdigest(),
            "provenance_refs": [evidence_id],
            "observed_at": at,
            "valid_from": at,
            "valid_until": None,
            "ingested_at": at,
            "trust": "confirmed_direct_user",
            "privacy_class": "internal",
            "purpose": "conversation_continuity",
            "retention_until": RETENTION,
            "delete_sla_at": RETENTION,
            "corrects_source_event_id": correction_of,
            "supersedes_source_event_id": correction_of,
            "retracts_source_event_id": None,
            "erasure_request_id": None,
        }
    )


def _fact(
    *, fact_id: str, source_id: str, principal_id: str, value: str, at: str
) -> MemoryFact:
    memory_value = {"preference": value, "context": "overkapping"}
    return MemoryFact.model_validate(
        {
            "schema_version": 1,
            "contract": "MemoryFact/v1",
            "fact_id": fact_id,
            "tenant_id": _scope()[0],
            "subject_id": principal_id,
            "predicate": "preference.outdoor_temperature",
            "value": memory_value,
            "source_event_ids": [source_id],
            "observed_at": at,
            "valid_from": at,
            "valid_until": None,
            "trust": "confirmed_direct_user",
            "content_sha256": canonical_sha256(memory_value),
            "assertion_status": "active",
            "superseded_by_fact_id": None,
            "retraction_source_event_id": None,
        }
    )


def _recall_query(
    tenant_id: str, installation_id: str, principal_id: str, *, text: str
) -> RecallCurrentFacts:
    return RecallCurrentFacts(
        recall_id=f"recall:postgres:{text}",
        installation_id=installation_id,
        tenant_id=tenant_id,
        principal_id=principal_id,
        conversation_id="conversation:telegram:jaap",
        project_id=None,
        query_purpose="reply",
        requested_valid_at=LATER,
        scope={"kind": "person", "principal_id": principal_id},
        subject_ids=(principal_id,),
        query_text=text,
    )


def _identity() -> CurrentIdentityPack:
    return CurrentIdentityPack.model_validate(
        {
            "schema_version": 1,
            "contract": "IdentityPack/v1",
            "identity_id": "identity:eva:memory-test",
            "pack_version": 1,
            "state": "current",
            "display_name": "Eva",
            "pronouns": ["zij", "haar"],
            "language": "nl-NL",
            "voice_style": {
                "name": "warm",
                "qualities": ["menselijk"],
                "preferred_wording": [],
                "forbidden_wording": [],
                "maximum_paragraphs": 3,
            },
            "values": ["eerlijk"],
            "temperament": ["nieuwsgierig"],
            "humour": "licht",
            "conversation_boundaries": ["geen verzonnen zekerheden"],
            "channel_presentations": [
                {
                    "channel_class": "telegram",
                    "greeting": None,
                    "signoff": None,
                    "concise_by_default": True,
                }
            ],
            "privacy_preferences": [],
            "consent_preferences": [],
            "initiative_preferences": ["gebruik actuele herinneringen"],
            "unknown_relation_preferences": [],
            "issuer": "eva:host",
            "source_sha256": "1" * 64,
            "signature_algorithm": "Ed25519",
            "signature": "A" * 86,
            "issued_at": NOW,
        }
    )


def _presence(
    memory: PresenceMemory, tenant_id: str, principal_id: str
) -> PresenceContext:
    return build_presence_context(
        PresenceTurn(
            tenant_id=tenant_id,
            principal_id=principal_id,
            conversation_id="conversation:telegram:jaap",
            episode_id="episode:telegram:memory-test",
            generation=1,
            identity_pack=_identity(),
            relationships=(),
            current_utterance=CurrentUtterance(
                turn_ref="telegram:update:memory-test",
                message_id="telegram:message:memory-test",
                text="Wat weet je nog over de overkapping?",
                received_at=LATER,
            ),
            episode_goal=EpisodeGoal(
                goal_id="goal:telegram:memory-test",
                source_message_ref="telegram:message:memory-test",
                description="Gebruik alleen actuele toegelaten herinneringen.",
            ),
            memory=memory,
        ),
        purpose="reply",
    )


def test_real_postgres_remember_correct_recall_and_restart(
    memory_engine: Engine,
) -> None:
    tenant_id, installation_id, principal_id, evidence_id = _scope()
    scope = {"kind": "person", "principal_id": principal_id}
    first_source = _source(
        source_id=SOURCE_1,
        principal_id=principal_id,
        evidence_id=evidence_id,
        at=NOW,
    )
    first_fact = _fact(
        fact_id=FACT_1,
        source_id=SOURCE_1,
        principal_id=principal_id,
        value="koel",
        at=NOW,
    )

    with memory_engine.begin() as connection:
        connection.execute(sa.text("SET CONSTRAINTS ALL DEFERRED"))
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        rows = PostgresNoor4DRepository(connection, tenant_id, installation_id)
        for fact_id in BASE_FACTS:
            assert rows.upsert_exact(_exact_fact(fact_id)).disposition == "inserted"
        memory = MemoryAuthority(
            PostgresMemoryRepository(connection, tenant_id, installation_id)
        )
        receipt = memory.remember(
            RememberFact(
                installation_id=installation_id,
                current_fact_id=CURRENT_ID,
                source_event=first_source,
                fact=first_fact,
                scope=scope,
                retention_policy_id="resident-memory-v1",
                committed_at=NOW,
            )
        )
        assert receipt.current_revision == 1
        assert (
            memory.recall_for_presence(
                _recall_query(tenant_id, installation_id, principal_id, text="koel")
            )
            .confirmed_facts[0]
            .value
            == first_fact.value
        )

    with memory_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        memory = MemoryAuthority(
            PostgresMemoryRepository(connection, tenant_id, installation_id)
        )
        restarted = memory.recall_for_presence(
            _recall_query(tenant_id, installation_id, principal_id, text="koel")
        )
        assert restarted.confirmed_facts[0].fact_id == FACT_1
        before_correction = _presence(restarted, tenant_id, principal_id)
        assert before_correction.confirmed_facts[0].value["preference"] == "koel"

        corrected_source = _source(
            source_id=SOURCE_2,
            principal_id=principal_id,
            evidence_id=evidence_id,
            at=LATER,
            correction_of=SOURCE_1,
        )
        corrected_fact = _fact(
            fact_id=FACT_2,
            source_id=SOURCE_2,
            principal_id=principal_id,
            value="warm",
            at=LATER,
        )
        corrected = memory.remember(
            RememberFact(
                installation_id=installation_id,
                current_fact_id=CURRENT_ID,
                source_event=corrected_source,
                fact=corrected_fact,
                scope=scope,
                retention_policy_id="resident-memory-v1",
                committed_at=LATER,
                expected_current=ExpectedCurrentFact(FACT_1, 1),
            )
        )
        assert corrected.current_revision == 2

    with memory_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        memory = MemoryAuthority(
            PostgresMemoryRepository(connection, tenant_id, installation_id)
        )
        recalled = memory.recall_for_presence(
            _recall_query(tenant_id, installation_id, principal_id, text="warm")
        )
        assert [fact.fact_id for fact in recalled.confirmed_facts] == [FACT_2]
        assert recalled.confirmed_facts[0].value["preference"] == "warm"
        next_turn = _presence(recalled, tenant_id, principal_id)
        assert [fact.fact_id for fact in next_turn.confirmed_facts] == [FACT_2]
        assert next_turn.confirmed_facts[0].value["preference"] == "warm"
        statuses = connection.execute(
            sa.text(
                "SELECT fact_id::text, assertion_status, revision "
                "FROM eva_memory.fact_assertions ORDER BY fact_id"
            )
        ).all()
        assert set(statuses) == {
            (FACT_1, "superseded", 2),
            (FACT_2, "active", 1),
        }
