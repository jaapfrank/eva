from __future__ import annotations

from collections.abc import Mapping

import pytest

from eva.adapters.postgres.portable_export import (
    PostgresPortableSnapshot,
    SNAPSHOT_BEGIN_SQL,
    validate_portable_v1_attention_rows,
)
from eva.application.portable_archive import PortableArchiveError


class _Result:
    def __init__(
        self,
        *,
        one: object | None = None,
        rows: list[Mapping[str, object]] | None = None,
    ) -> None:
        self._one = one
        self._rows = rows or []

    def fetchone(self) -> object:
        return self._one

    def fetchall(self) -> list[Mapping[str, object]]:
        return self._rows


class _Connection:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object | None]] = []
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql: str, params: object | None = None) -> _Result:
        self.calls.append((sql, params))
        normalized = " ".join(sql.split())
        if normalized == "SELECT pg_catalog.pg_export_snapshot()":
            return _Result(one=("00000003-0000001B-1",))
        if "current_user" in normalized:
            return _Result(one=("eva_backup", "on"))
        if "COUNT(*)" in normalized:
            return _Result(one=(1,))
        if normalized.startswith("SELECT tenant_id"):
            return _Result(rows=[{"tenant_id": "66a4aad5-0550-5211-8b36-398eabe5b7be"}])
        return _Result(one=(None, None))

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class _EmptyConnection(_Connection):
    def execute(self, sql: str, params: object | None = None) -> _Result:
        self.calls.append((sql, params))
        normalized = " ".join(sql.split())
        if normalized == "SELECT pg_catalog.pg_export_snapshot()":
            return _Result(one=("00000003-0000001B-1",))
        if "current_user" in normalized:
            return _Result(one=("eva_backup", "on"))
        if "COUNT(*)" in normalized:
            return _Result(one=(0,))
        if normalized.startswith("SELECT set_config"):
            return _Result(one=(None, None))
        return _Result(rows=[])


def test_all_reads_use_one_deferrable_repeatable_read_snapshot_without_dbos() -> None:
    connection = _Connection()
    specs = (
        {
            "export_ordinal": 1,
            "table": "eva_core.tenants",
            "database_column_order": ("tenant_id",),
            "primary_key": ("tenant_id",),
            "scope": "tenant",
        },
    )
    with PostgresPortableSnapshot(
        connection,
        tenant_id="66a4aad5-0550-5211-8b36-398eabe5b7be",
        installation_id="0a44a85c-4bbb-5330-8803-60fab86e5a94",
        table_specs=specs,
    ) as snapshot:
        assert snapshot.snapshot_id == "00000003-0000001B-1"
        rows = snapshot.read_all()

    normalized = [" ".join(sql.split()) for sql, _ in connection.calls]
    assert normalized[0] == SNAPSHOT_BEGIN_SQL
    assert normalized[1:3] == [
        "SET LOCAL ROLE eva_backup",
        "SET LOCAL row_security = on",
    ]
    attestation_index = next(
        i for i, sql in enumerate(normalized) if "current_user" in sql
    )
    snapshot_index = normalized.index("SELECT pg_catalog.pg_export_snapshot()")
    count_index = next(i for i, sql in enumerate(normalized) if "COUNT(*)" in sql)
    row_index = next(
        i for i, sql in enumerate(normalized) if sql.startswith("SELECT tenant_id")
    )
    assert attestation_index < snapshot_index < count_index < row_index
    assert all("dbos" not in sql.lower() for sql in normalized)
    assert rows["eva_core.tenants"] == [
        {"tenant_id": "66a4aad5-0550-5211-8b36-398eabe5b7be"}
    ]
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_reachable_principal_scope_covers_every_exported_principal_reference() -> None:
    snapshot = PostgresPortableSnapshot(
        _Connection(),
        tenant_id="66a4aad5-0550-5211-8b36-398eabe5b7be",
        installation_id="0a44a85c-4bbb-5330-8803-60fab86e5a94",
        table_specs=(
            {
                "export_ordinal": 1,
                "table": "eva_core.principals",
                "database_column_order": ("tenant_id", "principal_id"),
                "primary_key": ("tenant_id", "principal_id"),
                "scope": "tenant",
            },
        ),
    )

    clause, parameters = snapshot._reachable_principals_clause()

    assert "eva_core.ingress_requests" in clause
    assert "FROM eva_core.principal_bindings AS binding" in clause
    assert "subject_id" not in clause
    assert clause.count("%s") == len(parameters)


def test_relationship_selected_binding_also_selects_its_third_principal() -> None:
    snapshot = PostgresPortableSnapshot(
        _Connection(),
        tenant_id="66a4aad5-0550-5211-8b36-398eabe5b7be",
        installation_id="0a44a85c-4bbb-5330-8803-60fab86e5a94",
        table_specs=(
            {
                "export_ordinal": 1,
                "table": "eva_core.principals",
                "database_column_order": ("tenant_id", "principal_id"),
                "primary_key": ("tenant_id", "principal_id"),
                "scope": "tenant",
            },
        ),
    )

    principal_clause, parameters = snapshot._reachable_principals_clause()
    binding_clause, _ = snapshot._reachable_principal_bindings_clause()
    selected_binding_union = binding_clause.split("principal_binding_id IN (", 1)[1][
        :-1
    ]

    assert (
        "binding.principal_binding_id IN (" + selected_binding_union + ")"
        in principal_clause
    )
    assert "binding.verification_evidence_id=ANY(" in selected_binding_union
    assert principal_clause.count("%s") == len(parameters)


def test_default_catalog_reads_exactly_39_non_dbos_record_sets() -> None:
    connection = _EmptyConnection()
    with PostgresPortableSnapshot(
        connection,
        tenant_id="66a4aad5-0550-5211-8b36-398eabe5b7be",
        installation_id="0a44a85c-4bbb-5330-8803-60fab86e5a94",
    ) as snapshot:
        records = snapshot.read_all()

    statements = [" ".join(sql.split()) for sql, _ in connection.calls]
    assert len(records) == 39
    assert all(rows == [] for rows in records.values())
    assert sum("COUNT(*)" in sql for sql in statements) == 39
    assert (
        sum(sql.startswith("SELECT ") and "COUNT(*)" not in sql for sql in statements)
        == 42
    )
    assert all("dbos" not in sql.lower() for sql in statements)


def test_portable_v1_attention_preflight_accepts_only_the_historical_contract() -> None:
    historical = {
        "kind": "continuation",
        "payload": {
            "contract": "AttentionWake/v1",
            "schema_version": 1,
            "kind": "continuation",
        },
    }

    validate_portable_v1_attention_rows(
        {"eva_core.attention_items": [historical]}
    )

    for changed in (
        {**historical, "kind": "delegated_work"},
        {
            **historical,
            "payload": {
                **historical["payload"],
                "contract": "AttentionWake/v2",
                "schema_version": 2,
            },
        },
        {
            **historical,
            "payload": {**historical["payload"], "kind": "deadline"},
        },
    ):
        with pytest.raises(PortableArchiveError, match="portable v1 cannot export"):
            validate_portable_v1_attention_rows(
                {"eva_core.attention_items": [changed]}
            )


def test_snapshot_refuses_to_nest_inside_a_caller_transaction() -> None:
    connection = _Connection()
    connection.in_transaction = lambda: True  # type: ignore[attr-defined]

    with pytest.raises(PortableArchiveError, match="outside a transaction"):
        with PostgresPortableSnapshot(
            connection,
            tenant_id="66a4aad5-0550-5211-8b36-398eabe5b7be",
            installation_id="0a44a85c-4bbb-5330-8803-60fab86e5a94",
            table_specs=(
                {
                    "export_ordinal": 1,
                    "table": "eva_core.tenants",
                    "database_column_order": ("tenant_id",),
                    "primary_key": ("tenant_id",),
                    "scope": "tenant",
                },
            ),
        ):
            pass

    assert connection.calls == []
    assert connection.rollbacks == 0
