"""SQLAlchemy Core mirror of the sealed tranche 4D PostgreSQL catalog.

Alembic remains the only DDL writer.  The generated resource contains no
runtime discovery path and is pinned to one reviewed design-freeze digest.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from eva.adapters.postgres import schema as schema_4b
from eva.adapters.postgres.catalog_generated import CATALOG_SPEC


SCHEMA_FREEZE_SHA256 = (
    "f70eb25013ff84e464e4ef2a448b2e63fe23a8c11d91d4d57a54d3e431dc9d38"
)
if CATALOG_SPEC["schema_freeze_sha256"] != SCHEMA_FREEZE_SHA256:
    raise RuntimeError("generated PostgreSQL catalog is bound to another freeze")


metadata = sa.MetaData()
CANONICAL_TABLE_SPECS: tuple[dict[str, Any], ...] = tuple(
    CATALOG_SPEC["canonical_tables"]
)
NONCANONICAL_TABLE_SPECS: tuple[dict[str, Any], ...] = tuple(
    CATALOG_SPEC["noncanonical_tables"]
)
ALL_TABLE_SPECS = CANONICAL_TABLE_SPECS + NONCANONICAL_TABLE_SPECS
CANONICAL_TABLE_NAMES = tuple(item["table"] for item in CANONICAL_TABLE_SPECS)
NONCANONICAL_TABLE_NAMES = tuple(item["table"] for item in NONCANONICAL_TABLE_SPECS)

_COLUMN_TYPES: dict[str, sa.types.TypeEngine[Any]] = {
    "bigint": sa.BigInteger(),
    "boolean": sa.Boolean(),
    "bytea": postgresql.BYTEA(),
    "integer": sa.Integer(),
    "jsonb": postgresql.JSONB(none_as_null=True),
    "smallint": sa.SmallInteger(),
    "text": sa.Text(collation="C"),
    "text[]": postgresql.ARRAY(sa.Text(collation="C")),
    "timestamptz": postgresql.TIMESTAMP(timezone=True),
    "timestamptz(0)": postgresql.TIMESTAMP(timezone=True, precision=0),
    "tsvector": postgresql.TSVECTOR(),
    "uuid": postgresql.UUID(as_uuid=True),
    "uuid[]": postgresql.ARRAY(postgresql.UUID(as_uuid=True)),
    "varchar(64)": sa.String(64, collation="C"),
}

_FOREIGN_KEY = re.compile(
    r"^(?P<name>[a-z][a-z0-9_]*): \((?P<local>[^)]+)\) -> "
    r"(?P<remote_table>[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*)"
    r"\((?P<remote>[^)]+)\) ON DELETE (?P<ondelete>RESTRICT|CASCADE|NO ACTION)"
    r"(?P<deferred> DEFERRABLE INITIALLY DEFERRED)?$"
)
_UNIQUE = re.compile(
    r"^(?P<name>[a-z][a-z0-9_]*): UNIQUE \((?P<columns>[^)]+)\)"
    r"(?: WHERE (?P<where>.+))?$"
)
_CHECK = re.compile(
    r"^(?P<name>[a-z][a-z0-9_]*): CHECK \((?P<expression>.*)\)"
    r"(?: \[preserved exactly from [^]]+\])?$"
)
_INDEX = re.compile(
    r"^(?P<name>[a-z][a-z0-9_]*): (?P<unique>UNIQUE )?"
    r"(?P<method>BTREE|GIN) \((?P<columns>[^)]+)\)"
    r"(?: WHERE (?P<where>.+))?$"
)


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",")]


def _column(declaration: str) -> sa.Column[Any]:
    if declaration.endswith(" NOT NULL"):
        head = declaration.removesuffix(" NOT NULL")
        nullable = False
    elif declaration.endswith(" NULL"):
        head = declaration.removesuffix(" NULL")
        nullable = True
    else:
        raise RuntimeError(f"unsupported sealed column declaration: {declaration}")
    name, type_name = head.split(" ", 1)
    if not re.fullmatch(r"[a-z][a-z0-9_]*", name) or type_name not in _COLUMN_TYPES:
        raise RuntimeError(f"unsupported sealed column declaration: {declaration}")
    return sa.Column(
        name,
        _COLUMN_TYPES[type_name].copy(),
        nullable=nullable,
    )


def _table_for_spec(spec: dict[str, Any]) -> sa.Table:
    schema, name = spec["table"].split(".", 1)
    return sa.Table(
        name,
        metadata,
        *(_column(item) for item in spec["columns"]),
        schema=schema,
        info={
            "classification": spec["classification"],
            "scope": spec["scope"],
            "enable_rls": True,
            "force_rls": True,
            "cas": spec.get("cas"),
            "portable_export": spec.get("portableExport"),
            "reference_validations": tuple(spec.get("referenceValidations", ())),
        },
    )


for _spec in ALL_TABLE_SPECS:
    _table_for_spec(_spec)


ROLE_GRANT_MANIFEST: dict[str, Any] = CATALOG_SPEC["role_grant_manifest"]
ROLE_GRANT_ENTRIES: tuple[dict[str, Any], ...] = tuple(ROLE_GRANT_MANIFEST["entries"])
COLLATION_MANIFEST: dict[str, Any] = CATALOG_SPEC["collation_manifest"]
COLLATION_ENTRIES: tuple[dict[str, Any], ...] = tuple(COLLATION_MANIFEST["entries"])
_PRESERVED_4B_COLLATION_COLUMNS = {
    ("eva_core.episodes", "state"),
    ("eva_core.episodes", "state_contract"),
    ("eva_core.episode_events", "event_kind"),
    ("eva_core.episode_events", "before_state"),
    ("eva_core.episode_events", "after_state"),
}


def _canonical_array_sha256(entries: Iterable[dict[str, Any]]) -> str:
    data = json.dumps(
        list(entries),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _validate_role_grant_manifest() -> None:
    expected: list[dict[str, Any]] = []
    for spec in ALL_TABLE_SPECS:
        cas = spec["cas"]
        update_enabled = any(
            cas.startswith(prefix)
            for prefix in ("revisionCas", "currentSwap", "projectionUpsert")
        )
        column_names = [item.split(" ", 1)[0] for item in spec["columns"]]
        retention = spec["retention"]
        delete_owner = spec["owner"]["delete"]
        if isinstance(retention, dict):
            if retention.get("deleteOwner") != delete_owner:
                raise RuntimeError("grant manifest delete authority is inconsistent")
            retention_profile = retention["profile"]
        else:
            retention_profile = retention
        if not delete_owner or not retention_profile:
            raise RuntimeError("grant manifest delete authority is missing")
        expected.append(
            {
                "table": spec["table"],
                "roles": ["eva_runtime", "eva_dbos"],
                "tablePrivileges": ["SELECT", "INSERT", "DELETE"],
                "updateColumns": (
                    [name for name in column_names if name not in spec["primaryKey"]]
                    if update_enabled
                    else []
                ),
                "sourceCasProfile": cas,
                "sourceDeleteOwner": delete_owner,
                "sourceRetentionProfile": retention_profile,
            }
        )
    if (
        list(ROLE_GRANT_ENTRIES) != expected
        or ROLE_GRANT_MANIFEST.get("roleOrder") != ["eva_runtime", "eva_dbos"]
        or ROLE_GRANT_MANIFEST.get("entryCount") != 44
        or ROLE_GRANT_MANIFEST.get("updateTableCount") != 31
        or ROLE_GRANT_MANIFEST.get("deleteTableCount") != 44
        or ROLE_GRANT_MANIFEST.get("orderedEntriesCanonicalSha256")
        != _canonical_array_sha256(ROLE_GRANT_ENTRIES)
    ):
        raise RuntimeError("role/table/column grant manifest mismatch")


def _validate_collation_manifest() -> None:
    expected: list[dict[str, Any]] = []
    for spec in ALL_TABLE_SPECS:
        for declaration in spec["columns"]:
            head = declaration.removesuffix(" NOT NULL").removesuffix(" NULL")
            name, type_name = head.split(" ", 1)
            if type_name not in {"text", "text[]", "varchar(64)"}:
                continue
            expected.append(
                {
                    "table": spec["table"],
                    "column": name,
                    "declaredType": type_name,
                    "physicalAction": (
                        "alter_preserved_4b"
                        if (spec["table"], name) in _PRESERVED_4B_COLLATION_COLUMNS
                        else "create_or_add"
                    ),
                }
            )
    if (
        list(COLLATION_ENTRIES) != expected
        or COLLATION_MANIFEST.get("collation") != "C"
        or COLLATION_MANIFEST.get("entryCount") != 201
        or COLLATION_MANIFEST.get("preserved4BAlterCount") != 5
        or COLLATION_MANIFEST.get("orderedEntriesCanonicalSha256")
        != _canonical_array_sha256(COLLATION_ENTRIES)
    ):
        raise RuntimeError("collation manifest mismatch")
    for entry in COLLATION_ENTRIES:
        column_type = metadata.tables[entry["table"]].c[entry["column"]].type
        collation = (
            column_type.item_type.collation
            if isinstance(column_type, sa.ARRAY)
            else column_type.collation
        )
        if collation != "C":
            raise RuntimeError("collatable catalog column does not use C")


_validate_role_grant_manifest()
_validate_collation_manifest()

_ROLE_GRANT_BY_TABLE = {entry["table"]: entry for entry in ROLE_GRANT_ENTRIES}
_TABLE_SPEC_BY_TABLE = {spec["table"]: spec for spec in ALL_TABLE_SPECS}
PRESERVED_4B_COLLATION_ALTERS: tuple[dict[str, Any], ...] = tuple(
    entry
    for entry in COLLATION_ENTRIES
    if entry["physicalAction"] == "alter_preserved_4b"
)


def application_role_revoke_statements(full_name: str) -> tuple[str, str]:
    """Remove every table- and column-level application-role privilege."""

    try:
        entry = _ROLE_GRANT_BY_TABLE[full_name]
        spec = _TABLE_SPEC_BY_TABLE[full_name]
    except KeyError as error:
        raise RuntimeError(
            f"table has no sealed application-role grants: {full_name}"
        ) from error
    roles = ", ".join(entry["roles"])
    columns = ", ".join(item.split(" ", 1)[0] for item in spec["columns"])
    return (
        f"REVOKE ALL PRIVILEGES ON TABLE {full_name} FROM {roles}",
        f"REVOKE ALL ({columns}) ON TABLE {full_name} FROM {roles}",
    )


def application_role_grant_statements(full_name: str) -> tuple[str, ...]:
    """Return the exact fail-closed ACL statements sealed for one table."""

    try:
        entry = _ROLE_GRANT_BY_TABLE[full_name]
    except KeyError as error:
        raise RuntimeError(
            f"table has no sealed application-role grants: {full_name}"
        ) from error
    roles = ", ".join(entry["roles"])
    privileges = ", ".join(entry["tablePrivileges"])
    statements = [
        *application_role_revoke_statements(full_name),
        f"GRANT {privileges} ON TABLE {full_name} TO {roles}",
    ]
    if entry["updateColumns"]:
        columns = ", ".join(entry["updateColumns"])
        statements.append(f"GRANT UPDATE ({columns}) ON TABLE {full_name} TO {roles}")
    return tuple(statements)


def _append_constraints(spec: dict[str, Any]) -> None:
    table = metadata.tables[spec["table"]]
    table.append_constraint(
        sa.PrimaryKeyConstraint(*spec["primaryKey"], name=f"pk_{table.name}")
    )
    for declaration in spec.get("foreignKeys", ()):
        declaration = declaration.strip()
        match = _FOREIGN_KEY.fullmatch(declaration)
        if match is None:
            raise RuntimeError(f"unsupported sealed foreign key: {declaration}")
        remote = _split_csv(match["remote"])
        table.append_constraint(
            sa.ForeignKeyConstraint(
                _split_csv(match["local"]),
                [f"{match['remote_table']}.{column}" for column in remote],
                name=match["name"],
                ondelete=match["ondelete"],
                deferrable=bool(match["deferred"]),
                initially="DEFERRED" if match["deferred"] else None,
            )
        )
    for declaration in spec.get("uniques", ()):
        declaration = declaration.strip()
        match = _UNIQUE.fullmatch(declaration)
        if match is None:
            raise RuntimeError(f"unsupported sealed unique: {declaration}")
        columns = [table.c[name] for name in _split_csv(match["columns"])]
        if match["where"] is None:
            table.append_constraint(sa.UniqueConstraint(*columns, name=match["name"]))
        else:
            sa.Index(
                match["name"],
                *columns,
                unique=True,
                postgresql_where=sa.text(match["where"]),
                postgresql_using="btree",
            )
    for declaration in spec.get("checks", ()):
        declaration = declaration.strip()
        match = _CHECK.fullmatch(declaration)
        if match is None:
            raise RuntimeError(f"unsupported sealed check: {declaration}")
        expression: Any = match["expression"]
        if spec["table"] == "eva_core.episode_events" and match["name"] in {
            "ck_episode_events_after_snapshot",
            "ck_episode_events_before_snapshot",
        }:
            expression = next(
                constraint.sqltext
                for constraint in schema_4b.episode_events.constraints
                if constraint.name == match["name"]
            )
        table.append_constraint(sa.CheckConstraint(expression, name=match["name"]))


def _index_expressions(table: sa.Table, raw: str) -> list[Any]:
    expressions: list[Any] = []
    for declaration in _split_csv(raw):
        match = re.fullmatch(r"([a-z][a-z0-9_]*)(?: (ASC|DESC))?", declaration)
        if match is None:
            raise RuntimeError(f"unsupported sealed index expression: {declaration}")
        expression: Any = table.c[match[1]]
        if match[2] == "DESC":
            expression = expression.desc()
        elif match[2] == "ASC":
            expression = expression.asc()
        expressions.append(expression)
    return expressions


def _append_explicit_indexes(spec: dict[str, Any]) -> None:
    table = metadata.tables[spec["table"]]
    for declaration in (*spec.get("additionalIndexes", ()), *spec.get("indexes", ())):
        declaration = declaration.strip()
        match = _INDEX.fullmatch(declaration)
        if match is None:
            raise RuntimeError(f"unsupported sealed index: {declaration}")
        sa.Index(
            match["name"],
            *_index_expressions(table, match["columns"]),
            unique=bool(match["unique"]),
            postgresql_using=match["method"].lower(),
            postgresql_where=(
                sa.text(match["where"]) if match["where"] is not None else None
            ),
        )


def _key_prefixes(spec: dict[str, Any]) -> Iterable[list[str]]:
    yield list(spec["primaryKey"])
    for declaration in spec.get("uniques", ()):
        declaration = declaration.strip()
        match = _UNIQUE.fullmatch(declaration)
        if match is None:
            raise RuntimeError(f"unsupported sealed unique: {declaration}")
        yield _split_csv(match["columns"])


AUTOMATIC_FK_INDEX_ENTRIES: tuple[dict[str, Any], ...] = tuple(
    CATALOG_SPEC["automatic_fk_index_entries"]
)


def _expected_automatic_fk_index_entries() -> tuple[dict[str, Any], ...]:
    entries: list[dict[str, Any]] = []
    for spec in ALL_TABLE_SPECS:
        table_name = spec["table"]
        unqualified = table_name.split(".", 1)[1]
        prefixes = tuple(_key_prefixes(spec))
        for declaration in spec.get("foreignKeys", ()):
            declaration = declaration.strip()
            match = _FOREIGN_KEY.fullmatch(declaration)
            if match is None:
                raise RuntimeError(f"unsupported sealed foreign key: {declaration}")
            columns = _split_csv(match["local"])
            if any(prefix[: len(columns)] == columns for prefix in prefixes):
                continue
            natural = f"ix_{unqualified}__fk__{'__'.join(columns)}"
            if (
                not natural.isascii()
                or re.fullmatch(r"[a-z][a-z0-9_]*", natural) is None
            ):
                raise RuntimeError(
                    "automatic foreign-key index natural name is invalid"
                )
            digest = hashlib.sha256(natural.encode("utf-8")).hexdigest()
            selected = (
                natural
                if len(natural.encode("utf-8")) <= 63
                else f"{natural[:45]}__{digest[:16]}"
            )
            entries.append(
                {
                    "table": table_name,
                    "localColumns": columns,
                    "naturalName": natural,
                    "selectedName": selected,
                    "naturalNameSha256": digest,
                }
            )
    return tuple(entries)


def _validate_and_append_automatic_fk_indexes() -> None:
    expected = _expected_automatic_fk_index_entries()
    if AUTOMATIC_FK_INDEX_ENTRIES != expected:
        raise RuntimeError(
            "automatic foreign-key index manifest does not match catalog"
        )
    canonical = json.dumps(
        list(AUTOMATIC_FK_INDEX_ENTRIES),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if (
        hashlib.sha256(canonical).hexdigest()
        != CATALOG_SPEC["automatic_fk_index_entries_sha256"]
    ):
        raise RuntimeError("automatic foreign-key index manifest hash mismatch")

    explicit_names = {
        constraint.name
        for table in metadata.tables.values()
        for constraint in table.constraints
    } | {index.name for table in metadata.tables.values() for index in table.indexes}
    selected_names = [item["selectedName"] for item in AUTOMATIC_FK_INDEX_ENTRIES]
    if (
        len(AUTOMATIC_FK_INDEX_ENTRIES) != 79
        or sum(
            item["naturalName"] != item["selectedName"]
            for item in AUTOMATIC_FK_INDEX_ENTRIES
        )
        != 50
        or len(set(selected_names)) != len(selected_names)
        or any(len(name.encode("utf-8")) > 63 for name in selected_names)
        or set(selected_names) & explicit_names
    ):
        raise RuntimeError(
            "automatic foreign-key index manifest fails collision checks"
        )

    for item in AUTOMATIC_FK_INDEX_ENTRIES:
        table = metadata.tables[item["table"]]
        sa.Index(
            item["selectedName"],
            *(table.c[column] for column in item["localColumns"]),
            postgresql_using="btree",
        )


for _spec in ALL_TABLE_SPECS:
    _append_constraints(_spec)
for _spec in ALL_TABLE_SPECS:
    _append_explicit_indexes(_spec)
_validate_and_append_automatic_fk_indexes()


def table_specs_for_schema(schema: str) -> tuple[dict[str, Any], ...]:
    """Return generated specs in their sealed creation/export order."""

    return tuple(
        item for item in ALL_TABLE_SPECS if item["table"].startswith(f"{schema}.")
    )
