"""Fail-closed admission for the one empty-only 4D catalog expansion."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from sqlalchemy.engine import Connection


@dataclass(frozen=True)
class AdmissionStatement:
    name: str
    sql: str


INITIAL_ADMISSION_STATEMENTS = (
    AdmissionStatement(
        "set_transaction_isolation",
        "SET TRANSACTION ISOLATION LEVEL READ COMMITTED;",
    ),
    AdmissionStatement("restore_external_session_authority", "RESET ROLE;"),
    AdmissionStatement(
        "acquire_existing_migration_advisory_lock",
        "SELECT pg_advisory_xact_lock(1164409906, 4);",
    ),
    AdmissionStatement(
        "attest_external_global_read_authority",
        "SELECT\n"
        "  session_user::text AS session_user_name,\n"
        "  current_user::text AS current_user_name,\n"
        "  (r.rolsuper OR r.rolbypassrls) AS unrestricted_rls_read,\n"
        "  (r.rolsuper OR pg_has_role(session_user, 'eva_migrator', 'MEMBER')) AS may_set_migrator\n"
        "FROM pg_catalog.pg_roles AS r\n"
        "WHERE r.rolname = session_user;",
    ),
    AdmissionStatement(
        "attest_source_revision",
        "SELECT version_num\nFROM public.eva_alembic_version;",
    ),
    AdmissionStatement(
        "lock_all_preserved_tables_against_writes",
        "LOCK TABLE\n"
        "  eva_core.tenants,\n"
        "  eva_core.installations,\n"
        "  eva_core.episodes,\n"
        "  eva_core.episode_events\n"
        "IN ACCESS EXCLUSIVE MODE;",
    ),
    AdmissionStatement(
        "count_all_preserved_rows_without_rls_filtering",
        "SELECT\n"
        "  (SELECT count(*)::bigint FROM eva_core.tenants) AS tenants_row_count,\n"
        "  (SELECT count(*)::bigint FROM eva_core.installations) AS installations_row_count,\n"
        "  (SELECT count(*)::bigint FROM eva_core.episodes) AS episodes_row_count,\n"
        "  (SELECT count(*)::bigint FROM eva_core.episode_events) AS episode_events_row_count;",
    ),
    AdmissionStatement(
        "switch_to_ddl_owner_after_admission",
        "SET LOCAL ROLE eva_migrator;",
    ),
    AdmissionStatement(
        "attest_ddl_owner",
        "SELECT\n"
        "  session_user::text AS session_user_name,\n"
        "  current_user::text AS current_user_name;",
    ),
)

_COUNT_KEYS = (
    "tenants_row_count",
    "installations_row_count",
    "episodes_row_count",
    "episode_events_row_count",
)


@dataclass(frozen=True)
class InitialCatalogAdmissionDecision:
    decision: str
    row_counts: dict[str, int]


class InitialCatalogAdmissionError(RuntimeError):
    """A typed, fail-closed 4D admission rejection."""

    def __init__(
        self,
        code: str,
        *,
        row_counts: dict[str, object] | None = None,
    ) -> None:
        super().__init__(code)
        self.code = code
        self.row_counts = row_counts


def _one_mapping(result: Any) -> dict[str, object]:
    row = result.mappings().one()
    return dict(row)


def validate_external_authority_row(row: dict[str, object]) -> None:
    expected_keys = {
        "session_user_name",
        "current_user_name",
        "unrestricted_rls_read",
        "may_set_migrator",
    }
    if (
        set(row) != expected_keys
        or not isinstance(row["session_user_name"], str)
        or row["session_user_name"] != row["current_user_name"]
        or row["session_user_name"] == "eva_migrator"
        or row["unrestricted_rls_read"] is not True
        or row["may_set_migrator"] is not True
    ):
        raise InitialCatalogAdmissionError("eva4d_initial_admission_unprovable")


def _validate_source_revision(row: dict[str, object]) -> None:
    if row != {"version_num": "4b_0002_episode_atomicity"}:
        raise InitialCatalogAdmissionError("eva4d_initial_admission_unprovable")


def _validate_counts(row: dict[str, object]) -> dict[str, int]:
    if set(row) != set(_COUNT_KEYS) or any(
        type(row[key]) is not int or row[key] < 0 for key in _COUNT_KEYS
    ):
        raise InitialCatalogAdmissionError("eva4d_initial_admission_unprovable")
    return {key: int(row[key]) for key in _COUNT_KEYS}


def validate_ddl_owner_row(row: dict[str, object]) -> None:
    if (
        set(row) != {"session_user_name", "current_user_name"}
        or not isinstance(row["session_user_name"], str)
        or row["session_user_name"] == row["current_user_name"]
        or row["current_user_name"] != "eva_migrator"
    ):
        raise InitialCatalogAdmissionError("eva4d_initial_admission_unprovable")


def run_initial_catalog_admission(
    connection: Connection,
) -> InitialCatalogAdmissionDecision:
    """Execute the exact nine-statement admission before the first 4D DDL."""

    counts: dict[str, int] | None = None
    try:
        for ordinal, statement in enumerate(INITIAL_ADMISSION_STATEMENTS, start=1):
            result = connection.exec_driver_sql(statement.sql)
            if ordinal == 4:
                validate_external_authority_row(_one_mapping(result))
            elif ordinal == 5:
                _validate_source_revision(_one_mapping(result))
            elif ordinal == 7:
                raw_counts = _one_mapping(result)
                counts = _validate_counts(raw_counts)
                if any(counts.values()):
                    raise InitialCatalogAdmissionError(
                        "eva4d_initial_admission_nonempty",
                        row_counts=raw_counts,
                    )
            elif ordinal == 9:
                validate_ddl_owner_row(_one_mapping(result))
    except InitialCatalogAdmissionError:
        raise
    except Exception as error:
        raise InitialCatalogAdmissionError(
            "eva4d_initial_admission_unprovable"
        ) from error
    if counts is None:  # pragma: no cover - the sealed loop cannot omit statement 7
        raise InitialCatalogAdmissionError("eva4d_initial_admission_unprovable")
    # Do not expose a mutable object shared with the database-driver row.
    return InitialCatalogAdmissionDecision(
        decision="admitted_empty",
        row_counts=dict(MappingProxyType(counts)),
    )
