"""Exact-row PostgreSQL primitives for the sealed Noor 4D fixture.

The repository deliberately owns neither a transaction nor a workflow.  It
accepts native database rows, verifies their sealed portable-row digest, and
performs only scoped insert, exact replay, compare-and-swap, and readback
operations inside the caller's original root transaction.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import insert as postgres_insert

from eva.adapters.postgres.catalog import (
    CANONICAL_TABLE_NAMES,
    CANONICAL_TABLE_SPECS,
    metadata,
)
from eva.adapters.postgres.portable_export import CANONICAL_EXPORT_SPECS
from eva.adapters.postgres.scope import set_local_scope
from eva.application.episode_repository import (
    TransactionRequired,
    TransactionScopeMismatch,
)
from eva.application.portable_archive import (
    PortableArchiveError,
    build_portable_record,
)


_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_TABLE_SPECS = {spec["table"]: spec for spec in CANONICAL_TABLE_SPECS}
_EXPORT_SPECS = {spec["table"]: spec for spec in CANONICAL_EXPORT_SPECS}


class Noor4DRepositoryError(RuntimeError):
    """Base class for typed fixture-row repository failures."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class Noor4DInvalidRow(Noor4DRepositoryError):
    def __init__(self, message: str) -> None:
        super().__init__("invalid_noor_4d_row", message)


class Noor4DScopeConflict(Noor4DRepositoryError):
    def __init__(self) -> None:
        super().__init__(
            "noor_4d_scope_conflict",
            "the row or caller transaction belongs to another scope",
        )


class Noor4DRowNotFound(Noor4DRepositoryError):
    def __init__(self) -> None:
        super().__init__(
            "noor_4d_row_not_found", "the scoped canonical row does not exist"
        )


class Noor4DDuplicateConflict(Noor4DRepositoryError):
    def __init__(self) -> None:
        super().__init__(
            "noor_4d_duplicate_conflict",
            "the insert conflicts with an existing canonical row",
        )


class Noor4DRevisionConflict(Noor4DRepositoryError):
    def __init__(self) -> None:
        super().__init__(
            "noor_4d_revision_conflict",
            "the expected or replacement revision is not current",
        )


class Noor4DHashConflict(Noor4DRepositoryError):
    def __init__(self) -> None:
        super().__init__(
            "noor_4d_hash_conflict",
            "the supplied portable-row hash does not match exact row bytes",
        )


def _validated_sha256(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("portable_row_sha256 must be text")
    if not _SHA256.fullmatch(value):
        raise ValueError("portable_row_sha256 must be lowercase SHA-256 hex")
    return value


def _frozen_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("row values must be a mapping")
    return MappingProxyType(copy.deepcopy(dict(value)))


@dataclass(frozen=True, slots=True)
class ExactNoor4DRow:
    table: str
    values: Mapping[str, Any]
    portable_row_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.table, str) or not self.table:
            raise TypeError("table must be non-empty text")
        object.__setattr__(self, "values", _frozen_mapping(self.values))
        object.__setattr__(
            self,
            "portable_row_sha256",
            _validated_sha256(self.portable_row_sha256),
        )


@dataclass(frozen=True, slots=True)
class ExpectedNoor4DRow:
    revision: int
    portable_row_sha256: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.revision, int)
            or isinstance(self.revision, bool)
            or self.revision < 0
        ):
            raise TypeError("expected revision must be a non-negative integer")
        object.__setattr__(
            self,
            "portable_row_sha256",
            _validated_sha256(self.portable_row_sha256),
        )


@dataclass(frozen=True, slots=True)
class StoredNoor4DRow:
    table: str
    primary_key: Mapping[str, Any]
    values: Mapping[str, Any]
    portable_row_sha256: str


@dataclass(frozen=True, slots=True)
class Noor4DWriteResult:
    disposition: Literal["inserted", "already_present", "updated"]
    row: StoredNoor4DRow

    @property
    def portable_row_sha256(self) -> str:
        return self.row.portable_row_sha256


def _canonical_scope_uuid(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("scope IDs must be text")
    try:
        canonical = str(UUID(value))
    except (AttributeError, TypeError, ValueError) as error:
        raise ValueError("scope IDs must be UUIDs") from error
    if value != canonical:
        raise ValueError("scope IDs must use canonical lowercase UUID text")
    return canonical


class PostgresNoor4DRepository:
    """Scoped exact-row operations in one caller-owned root transaction."""

    def __init__(
        self,
        connection: Any,
        tenant_id: str,
        installation_id: str,
    ) -> None:
        self._connection = connection
        self._tenant_id = _canonical_scope_uuid(tenant_id)
        self._installation_id = _canonical_scope_uuid(installation_id)
        transaction = connection.get_transaction()
        if (
            transaction is None
            or not connection.in_transaction()
            or connection.in_nested_transaction()
        ):
            raise TransactionRequired()
        self._transaction = transaction

    def _require_original_transaction(self) -> None:
        if (
            not self._connection.in_transaction()
            or self._connection.in_nested_transaction()
            or self._connection.get_transaction() is not self._transaction
        ):
            raise TransactionRequired()
        try:
            set_local_scope(
                self._connection,
                tenant_id=self._tenant_id,
                installation_id=self._installation_id,
            )
        except TransactionScopeMismatch as error:
            raise Noor4DScopeConflict() from error

    @staticmethod
    def _spec(table_name: str) -> tuple[dict[str, Any], dict[str, Any], sa.Table]:
        if table_name not in CANONICAL_TABLE_NAMES:
            raise Noor4DInvalidRow("table is not in the 39-table canonical catalog")
        return (
            _TABLE_SPECS[table_name],
            _EXPORT_SPECS[table_name],
            metadata.tables[table_name],
        )

    def _validate_scope(self, values: Mapping[str, Any]) -> None:
        if values.get("tenant_id") != UUID(self._tenant_id):
            raise Noor4DScopeConflict()
        if "installation_id" in values and values["installation_id"] != UUID(
            self._installation_id
        ):
            raise Noor4DScopeConflict()

    def _validate_row(
        self, row: ExactNoor4DRow, *, for_insert: bool
    ) -> tuple[dict[str, Any], dict[str, Any], sa.Table]:
        spec, export, table = self._spec(row.table)
        column_order = tuple(export["database_column_order"])
        if set(row.values) != set(column_order):
            raise Noor4DInvalidRow(
                "row must contain the exact native database column set"
            )
        self._validate_scope(row.values)
        if (
            for_insert
            and (
                str(spec["cas"]).startswith("revisionCas")
                or spec["cas"] == "currentSwap"
            )
            and "revision" in table.c
        ):
            revision = row.values.get("revision")
            if (
                not isinstance(revision, int)
                or isinstance(revision, bool)
                or revision != 1
            ):
                raise Noor4DRevisionConflict()
        try:
            portable = build_portable_record(
                table=row.table,
                record_contract=export["record_contract"],
                primary_key_columns=export["primary_key"],
                transform_spec=export["transform_spec"],
                database_row=row.values,
            )
        except PortableArchiveError as error:
            raise Noor4DInvalidRow(str(error)) from error
        if portable["row_sha256"] != row.portable_row_sha256:
            raise Noor4DHashConflict()
        return spec, export, table

    def _validate_primary_key(
        self, table_name: str, primary_key: Mapping[str, Any]
    ) -> tuple[dict[str, Any], dict[str, Any], sa.Table]:
        if not isinstance(primary_key, Mapping):
            raise Noor4DInvalidRow("primary key must be a mapping")
        spec, export, table = self._spec(table_name)
        expected = tuple(spec["primaryKey"])
        if set(primary_key) != set(expected):
            raise Noor4DInvalidRow("primary key differs from the sealed catalog")
        self._validate_scope(primary_key)
        return spec, export, table

    @staticmethod
    def _primary_key(
        spec: Mapping[str, Any], values: Mapping[str, Any]
    ) -> dict[str, Any]:
        return {name: values[name] for name in spec["primaryKey"]}

    @staticmethod
    def _predicate(table: sa.Table, primary_key: Mapping[str, Any]) -> Any:
        return sa.and_(*(table.c[name] == value for name, value in primary_key.items()))

    def _stored(
        self,
        spec: Mapping[str, Any],
        export: Mapping[str, Any],
        values: Mapping[str, Any],
    ) -> StoredNoor4DRow:
        try:
            portable = build_portable_record(
                table=spec["table"],
                record_contract=export["record_contract"],
                primary_key_columns=export["primary_key"],
                transform_spec=export["transform_spec"],
                database_row=values,
            )
        except PortableArchiveError as error:
            raise Noor4DInvalidRow(
                f"stored row violates its portable contract: {error}"
            ) from error
        return StoredNoor4DRow(
            table=spec["table"],
            primary_key=_frozen_mapping(self._primary_key(spec, values)),
            values=_frozen_mapping(values),
            portable_row_sha256=portable["row_sha256"],
        )

    def _read(
        self,
        table_name: str,
        primary_key: Mapping[str, Any],
        *,
        for_update: bool = False,
    ) -> StoredNoor4DRow | None:
        spec, export, table = self._validate_primary_key(table_name, primary_key)
        statement = sa.select(table).where(self._predicate(table, primary_key))
        if for_update:
            statement = statement.with_for_update()
        result = self._connection.execute(statement).mappings().one_or_none()
        if result is None:
            return None
        values = dict(result)
        self._validate_scope(values)
        return self._stored(spec, export, values)

    def readback(
        self, table_name: str, primary_key: Mapping[str, Any]
    ) -> StoredNoor4DRow:
        self._require_original_transaction()
        stored = self._read(table_name, primary_key)
        if stored is None:
            raise Noor4DRowNotFound()
        return stored

    def insert(self, row: ExactNoor4DRow) -> Noor4DWriteResult:
        self._require_original_transaction()
        spec, _export, table = self._validate_row(row, for_insert=True)
        primary_key = self._primary_key(spec, row.values)
        statement = (
            postgres_insert(table)
            .values(**dict(row.values))
            .on_conflict_do_nothing()
            .returning(*[table.c[name] for name in spec["primaryKey"]])
        )
        inserted = self._connection.execute(statement).first()
        if inserted is None:
            raise Noor4DDuplicateConflict()
        stored = self._read(row.table, primary_key)
        if stored is None or stored.portable_row_sha256 != row.portable_row_sha256:
            raise Noor4DHashConflict()
        return Noor4DWriteResult("inserted", stored)

    def upsert_exact(self, row: ExactNoor4DRow) -> Noor4DWriteResult:
        self._require_original_transaction()
        spec, _export, table = self._validate_row(row, for_insert=True)
        primary_key = self._primary_key(spec, row.values)
        statement = (
            postgres_insert(table)
            .values(**dict(row.values))
            .on_conflict_do_nothing()
            .returning(*[table.c[name] for name in spec["primaryKey"]])
        )
        inserted = self._connection.execute(statement).first()
        stored = self._read(row.table, primary_key)
        if inserted is not None:
            if stored is None or stored.portable_row_sha256 != row.portable_row_sha256:
                raise Noor4DHashConflict()
            return Noor4DWriteResult("inserted", stored)
        if stored is None:
            raise Noor4DDuplicateConflict()
        if stored.portable_row_sha256 != row.portable_row_sha256:
            raise Noor4DHashConflict()
        return Noor4DWriteResult("already_present", stored)

    def compare_and_swap(
        self,
        expected: ExpectedNoor4DRow,
        replacement: ExactNoor4DRow,
    ) -> Noor4DWriteResult:
        self._require_original_transaction()
        spec, _export, table = self._validate_row(replacement, for_insert=False)
        cas = str(spec["cas"])
        if not (
            (cas.startswith("revisionCas") or cas == "currentSwap")
            and "revision" in table.c
        ):
            raise Noor4DInvalidRow("table does not use a revision CAS profile")
        target_revision = replacement.values.get("revision")
        if (
            not isinstance(target_revision, int)
            or isinstance(target_revision, bool)
            or target_revision != expected.revision + 1
        ):
            raise Noor4DRevisionConflict()

        primary_key = self._primary_key(spec, replacement.values)
        current = self._read(replacement.table, primary_key, for_update=True)
        if current is None:
            raise Noor4DRowNotFound()
        if current.portable_row_sha256 == replacement.portable_row_sha256:
            return Noor4DWriteResult("already_present", current)

        current_revision = current.values.get("revision")
        if current_revision != expected.revision:
            raise Noor4DRevisionConflict()
        if current.portable_row_sha256 != expected.portable_row_sha256:
            raise Noor4DHashConflict()

        values = {
            name: value
            for name, value in replacement.values.items()
            if name not in spec["primaryKey"]
        }
        statement = (
            sa.update(table)
            .where(self._predicate(table, primary_key))
            .where(table.c.revision == expected.revision)
            .values(**values)
        )
        result = self._connection.execute(statement)
        if result.rowcount != 1:
            raise Noor4DRevisionConflict()
        stored = self._read(replacement.table, primary_key)
        if stored is None:
            raise Noor4DRowNotFound()
        if stored.portable_row_sha256 != replacement.portable_row_sha256:
            raise Noor4DHashConflict()
        return Noor4DWriteResult("updated", stored)


__all__ = [
    "ExactNoor4DRow",
    "ExpectedNoor4DRow",
    "Noor4DDuplicateConflict",
    "Noor4DHashConflict",
    "Noor4DInvalidRow",
    "Noor4DRepositoryError",
    "Noor4DRevisionConflict",
    "Noor4DRowNotFound",
    "Noor4DScopeConflict",
    "Noor4DWriteResult",
    "PostgresNoor4DRepository",
    "StoredNoor4DRow",
]
