from __future__ import annotations

import hashlib
import json
import os
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Connection, Engine, make_url

from eva.adapters.postgres.catalog_generated import CATALOG_SPEC
from tests.support.noor_4d_repository import (
    ExactNoor4DRow,
    ExpectedNoor4DRow,
    Noor4DDuplicateConflict,
    Noor4DHashConflict,
    Noor4DInvalidRow,
    Noor4DRevisionConflict,
    Noor4DRowNotFound,
    Noor4DScopeConflict,
    PostgresNoor4DRepository,
)
from eva.application.episode_repository import TransactionRequired
from eva.application.portable_archive import build_portable_record


EVA2_ROOT = Path(__file__).resolve().parents[3]
SCHEDULE_PATH = (
    EVA2_ROOT / "development/design/vertical/noor-4d-concurrency-crash-schedules-v1.json"
)
SCHEDULE_SHA256 = "f492d746779ec5276478893ff22720c84926912371d19175dd7508a18734b91d"

_TABLE_SPECS = {spec["table"]: spec for spec in CATALOG_SPEC["canonical_tables"]}


def _schedule() -> dict[str, Any]:
    raw = SCHEDULE_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SCHEDULE_SHA256
    value = json.loads(raw)
    assert isinstance(value, dict)
    return value


def _facts() -> dict[str, dict[str, Any]]:
    catalog = _schedule()["portableSnapshotAuthority"]["fixtureFactCatalog"]
    return {entry["fact_id"]: entry for entry in catalog["facts"]}


def _column_declarations(table: str) -> dict[str, str]:
    return {
        declaration.split(" ", 1)[0]: declaration.split(" ", 2)[1]
        for declaration in _TABLE_SPECS[table]["columns"]
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
    fact = _facts()[fact_id]
    table = fact["table"]
    portable = fact["portable_record"]
    row = portable["row"]
    declarations = _column_declarations(table)
    if set(row) != set(declarations):
        raise ValueError(f"{fact_id} is a redacted/non-reversible portable row")
    values = {
        name: _native_value(value, declarations[name]) for name, value in row.items()
    }
    return ExactNoor4DRow(
        table=table,
        values=values,
        portable_row_sha256=portable["row_sha256"],
    )


def _row_with_changes(row: ExactNoor4DRow, **changes: Any) -> ExactNoor4DRow:
    values = {**row.values, **changes}
    spec = _TABLE_SPECS[row.table]
    portable = spec["portableExport"]
    record = build_portable_record(
        table=row.table,
        record_contract=portable["recordContract"],
        primary_key_columns=spec["primaryKey"],
        transform_spec=portable["transformSpec"],
        database_row=values,
    )
    return ExactNoor4DRow(
        table=row.table,
        values=values,
        portable_row_sha256=record["row_sha256"],
    )


def _primary_key(row: ExactNoor4DRow) -> dict[str, Any]:
    return {name: row.values[name] for name in _TABLE_SPECS[row.table]["primaryKey"]}


def _scope() -> tuple[str, str]:
    tenant = _exact_fact("static/tenant")
    installation = _exact_fact("static/installation")
    return str(tenant.values["tenant_id"]), str(installation.values["installation_id"])


_BASE_FACTS = (
    "static/tenant",
    "static/installation",
    "static/identity-pack",
    "static/policy-pack",
    "static/principal-owner",
    "development/evidence/evidence:authenticated-http:001",
    "development/evidence/evidence:direct-user-request:001",
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


class _NoTransactionControlConnection:
    def __init__(self, connection: Connection) -> None:
        self._connection = connection
        self.commit_calls = 0
        self.rollback_calls = 0

    def get_transaction(self):
        return self._connection.get_transaction()

    def in_transaction(self) -> bool:
        return self._connection.in_transaction()

    def in_nested_transaction(self) -> bool:
        return self._connection.in_nested_transaction()

    def execute(self, *args: Any, **kwargs: Any):
        return self._connection.execute(*args, **kwargs)

    def commit(self) -> None:
        self.commit_calls += 1
        raise AssertionError("repository must not commit")

    def rollback(self) -> None:
        self.rollback_calls += 1
        raise AssertionError("repository must not roll back")


def _repository(connection: Connection) -> PostgresNoor4DRepository:
    tenant_id, installation_id = _scope()
    connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
    return PostgresNoor4DRepository(connection, tenant_id, installation_id)


def _seed_base(repository: PostgresNoor4DRepository) -> None:
    for fact_id in _BASE_FACTS:
        result = repository.upsert_exact(_exact_fact(fact_id))
        assert result.disposition == "inserted"


@pytest.fixture(scope="module")
def noor_postgres_engine() -> Iterator[Engine]:
    dsn = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.fail("EVA2_TEST_POSTGRES_DSN is required for the Noor repository gate")
    url = make_url(dsn)
    if (
        url.get_backend_name() != "postgresql"
        or not url.database
        or not url.database.startswith("eva2_4b_disposable_")
    ):
        pytest.fail("the Noor repository gate requires a disposable PostgreSQL DSN")

    engine = sa.create_engine(url, pool_pre_ping=False)
    config = Config(str(EVA2_ROOT / "deploy/alembic.ini"))
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    with engine.connect() as connection:
        if int(connection.scalar(sa.text("SHOW server_version_num"))) != 160013:
            pytest.fail("the Noor repository gate requires PostgreSQL 16.13")
        nonempty = connection.scalar(
            sa.text(
                "SELECT EXISTS ("
                "SELECT 1 FROM pg_namespace WHERE nspname LIKE 'eva\\_%' ESCAPE '\\'"
                ")"
            )
        )
        if nonempty:
            pytest.fail("the Noor repository gate requires an empty database")

    try:
        command.upgrade(config, "4b_0000_core_namespace")
        with engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
            config.attributes["connection"] = connection
            try:
                command.upgrade(config, "head")
            finally:
                config.attributes.pop("connection", None)
        yield engine
    finally:
        try:
            command.downgrade(config, "base")
        finally:
            engine.dispose()


def test_schedule_full_cas_rows_match_repository_profiles_and_revision_rules() -> None:
    schedule = _schedule()
    facts = _facts()
    cas = {spec["table"]: spec["cas"] for spec in CATALOG_SPEC["canonical_tables"]}
    profile_counts: dict[str, int] = {}
    unique_edges: set[tuple[str, str, str]] = set()

    for snapshot in schedule["portableSnapshotAuthority"]["snapshotDefinitions"]:
        for replacement in snapshot["delta"]["full_cas_replacements"]:
            before_id = replacement["expected_base_fact_id"]
            after_id = replacement["replacement_fact_id"]
            before = facts[before_id]["portable_record"]["row"]
            after = facts[after_id]["portable_record"]["row"]
            profile = cas[replacement["table"]]
            profile_counts[profile] = profile_counts.get(profile, 0) + 1
            unique_edges.add((replacement["table"], before_id, after_id))
            if profile.startswith("revisionCas"):
                assert type(before.get("revision")) is int
                assert type(after.get("revision")) is int
                assert after["revision"] == before["revision"] + 1
            else:
                assert profile == "currentSwap"

    assert profile_counts == {
        "currentSwap": 5,
        "revisionCas": 95,
        "revisionCas plus ack_claim_revision CAS": 2,
    }
    assert len(unique_edges) == 72


def test_repository_requires_and_remains_in_original_caller_transaction(
    noor_postgres_engine,
) -> None:
    tenant_id, installation_id = _scope()
    with noor_postgres_engine.connect() as connection:
        with pytest.raises(TransactionRequired):
            PostgresNoor4DRepository(connection, tenant_id, installation_id)

        original = connection.begin()
        repository = PostgresNoor4DRepository(connection, tenant_id, installation_id)
        original.rollback()
        replacement_transaction = connection.begin()
        with pytest.raises(TransactionRequired):
            repository.readback("eva_core.tenants", {"tenant_id": UUID(tenant_id)})
        replacement_transaction.rollback()

        transaction = connection.begin()
        repository = PostgresNoor4DRepository(connection, tenant_id, installation_id)
        nested = connection.begin_nested()
        try:
            with pytest.raises(TransactionRequired):
                repository.readback("eva_core.tenants", {"tenant_id": UUID(tenant_id)})
        finally:
            nested.rollback()
            transaction.rollback()


def test_exact_fixture_rows_commit_rollback_scope_and_conflicts(
    noor_postgres_engine,
) -> None:
    tenant_id, installation_id = _scope()
    immutable = _exact_fact("development/evidence/evidence:direct-user-request:001")
    commitment_open = _exact_fact("commitment/open")
    commitment_fulfilled = _exact_fact("commitment/fulfilled")
    generation_current = _exact_fact("admitted/generation-current")
    generation_sealed = _exact_fact("generation/sealed")
    attention_pending = _exact_fact("attention/pending")
    attention_completed = _exact_fact("attention/completed")
    identity_pack = _exact_fact("static/identity-pack")

    with noor_postgres_engine.connect() as connection:
        transaction = connection.begin()
        connection.execute(sa.text("SET CONSTRAINTS ALL DEFERRED"))
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        guarded = _NoTransactionControlConnection(connection)
        repository = PostgresNoor4DRepository(guarded, tenant_id, installation_id)
        _seed_base(repository)
        assert guarded.commit_calls == guarded.rollback_calls == 0
        transaction.commit()

    with noor_postgres_engine.connect() as connection:
        transaction = connection.begin()
        repository = _repository(connection)

        stored = repository.readback(immutable.table, _primary_key(immutable))
        assert stored.portable_row_sha256 == immutable.portable_row_sha256
        assert repository.upsert_exact(immutable).disposition == "already_present"
        with pytest.raises(Noor4DDuplicateConflict):
            repository.insert(immutable)

        altered_immutable = _row_with_changes(
            immutable, producer="fixture:producer:altered"
        )
        with pytest.raises(Noor4DHashConflict):
            repository.upsert_exact(altered_immutable)

        commitment = repository.compare_and_swap(
            ExpectedNoor4DRow(
                revision=1,
                portable_row_sha256=commitment_open.portable_row_sha256,
            ),
            commitment_fulfilled,
        )
        generation = repository.compare_and_swap(
            ExpectedNoor4DRow(
                revision=1,
                portable_row_sha256=generation_current.portable_row_sha256,
            ),
            generation_sealed,
        )
        assert commitment.disposition == generation.disposition == "updated"
        assert (
            repository.compare_and_swap(
                ExpectedNoor4DRow(
                    revision=1,
                    portable_row_sha256=commitment_open.portable_row_sha256,
                ),
                commitment_fulfilled,
            ).disposition
            == "already_present"
        )

        with pytest.raises(Noor4DRevisionConflict):
            repository.compare_and_swap(
                ExpectedNoor4DRow(
                    revision=0,
                    portable_row_sha256=attention_pending.portable_row_sha256,
                ),
                attention_completed,
            )
        with pytest.raises(Noor4DHashConflict):
            repository.compare_and_swap(
                ExpectedNoor4DRow(revision=1, portable_row_sha256="0" * 64),
                attention_completed,
            )
        with pytest.raises(Noor4DInvalidRow):
            repository.compare_and_swap(
                ExpectedNoor4DRow(
                    revision=0,
                    portable_row_sha256=identity_pack.portable_row_sha256,
                ),
                identity_pack,
            )
        transaction.commit()

    with noor_postgres_engine.connect() as connection:
        transaction = connection.begin()
        repository = _repository(connection)
        assert (
            repository.readback(
                commitment_fulfilled.table, _primary_key(commitment_fulfilled)
            ).portable_row_sha256
            == commitment_fulfilled.portable_row_sha256
        )
        assert (
            repository.readback(
                generation_sealed.table, _primary_key(generation_sealed)
            ).portable_row_sha256
            == generation_sealed.portable_row_sha256
        )
        repository.compare_and_swap(
            ExpectedNoor4DRow(
                revision=1,
                portable_row_sha256=attention_pending.portable_row_sha256,
            ),
            attention_completed,
        )
        transaction.rollback()

    with noor_postgres_engine.connect() as connection:
        transaction = connection.begin()
        repository = _repository(connection)
        assert (
            repository.readback(
                attention_pending.table, _primary_key(attention_pending)
            ).portable_row_sha256
            == attention_pending.portable_row_sha256
        )

        wrong_scope = _row_with_changes(
            immutable,
            tenant_id=uuid4(),
        )
        with pytest.raises(Noor4DScopeConflict):
            repository.upsert_exact(wrong_scope)

        missing_key = {
            **_primary_key(immutable),
            "evidence_id": uuid4(),
        }
        with pytest.raises(Noor4DRowNotFound):
            repository.readback(immutable.table, missing_key)

        redacted_binding = _facts()["binding/calendar"]["portable_record"]
        with pytest.raises(Noor4DInvalidRow):
            repository.upsert_exact(
                ExactNoor4DRow(
                    table="eva_core.connector_bindings",
                    values=redacted_binding["row"],
                    portable_row_sha256=redacted_binding["row_sha256"],
                )
            )
        transaction.rollback()


def test_expected_row_is_immutable_value_object() -> None:
    expected = ExpectedNoor4DRow(revision=1, portable_row_sha256="a" * 64)
    with pytest.raises((AttributeError, TypeError)):
        replace(expected, revision="1")
