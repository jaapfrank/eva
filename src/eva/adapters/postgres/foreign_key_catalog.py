"""Exact PostgreSQL foreign-key drift checks for Eva's cyclic catalog.

Alembic's autogenerate check remains useful for ordinary metadata changes, but
its table sorter deliberately omits foreign-key comparisons when a metadata
graph contains cycles.  Eva has intentional cycles, so the installer compares
the complete live foreign-key graph directly against the current catalog too.
"""

from __future__ import annotations

from dataclasses import dataclass

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from eva.adapters.postgres.current_catalog import metadata


_ONDELETE_CODES = {
    "NO ACTION": "a",
    "RESTRICT": "r",
    "CASCADE": "c",
    "SET NULL": "n",
    "SET DEFAULT": "d",
}


@dataclass(frozen=True, order=True, slots=True)
class ForeignKeyShape:
    source_schema: str
    source_table: str
    name: str
    source_columns: tuple[str, ...]
    target_schema: str
    target_table: str
    target_columns: tuple[str, ...]
    ondelete_code: str
    deferrable: bool
    initially_deferred: bool


class ForeignKeyCatalogDrift(RuntimeError):
    """The installed foreign-key graph differs from the sealed current catalog."""


def _expected_shapes() -> tuple[ForeignKeyShape, ...]:
    shapes: list[ForeignKeyShape] = []
    for table in metadata.tables.values():
        if table.schema is None:
            raise RuntimeError("Eva catalog table is missing a schema")
        for constraint in table.foreign_key_constraints:
            if constraint.name is None:
                raise RuntimeError("Eva catalog foreign key is unnamed")
            elements = tuple(constraint.elements)
            if not elements:
                raise RuntimeError(f"Eva catalog foreign key is empty: {constraint.name}")
            target_table = elements[0].column.table
            if target_table.schema is None or any(
                element.column.table is not target_table for element in elements
            ):
                raise RuntimeError(
                    f"Eva catalog foreign key is not one target table: {constraint.name}"
                )
            ondelete = (constraint.ondelete or "NO ACTION").upper()
            try:
                ondelete_code = _ONDELETE_CODES[ondelete]
            except KeyError as error:
                raise RuntimeError(
                    f"Eva catalog foreign key has unsupported delete rule: {constraint.name}"
                ) from error
            shapes.append(
                ForeignKeyShape(
                    source_schema=table.schema,
                    source_table=table.name,
                    name=constraint.name,
                    source_columns=tuple(constraint.columns.keys()),
                    target_schema=target_table.schema,
                    target_table=target_table.name,
                    target_columns=tuple(element.column.name for element in elements),
                    ondelete_code=ondelete_code,
                    deferrable=bool(constraint.deferrable),
                    initially_deferred=constraint.initially == "DEFERRED",
                )
            )
    if len({(shape.source_schema, shape.source_table, shape.name) for shape in shapes}) != len(
        shapes
    ):
        raise RuntimeError("Eva catalog has duplicate foreign-key names per table")
    return tuple(sorted(shapes))


_EXPECTED_SHAPES = _expected_shapes()
_EVA_SCHEMAS = tuple(sorted({shape.source_schema for shape in _EXPECTED_SHAPES}))
_ACTUAL_FOREIGN_KEYS = sa.text(
    """
    SELECT source_namespace.nspname AS source_schema,
           source_table.relname AS source_table,
           constraint_row.conname AS name,
           array_agg(source_column.attname ORDER BY source_key.ordinality) AS source_columns,
           target_namespace.nspname AS target_schema,
           target_table.relname AS target_table,
           array_agg(target_column.attname ORDER BY target_key.ordinality) AS target_columns,
           constraint_row.confdeltype AS ondelete_code,
           constraint_row.condeferrable AS deferrable,
           constraint_row.condeferred AS initially_deferred
      FROM pg_catalog.pg_constraint AS constraint_row
      JOIN pg_catalog.pg_class AS source_table
        ON source_table.oid = constraint_row.conrelid
      JOIN pg_catalog.pg_namespace AS source_namespace
        ON source_namespace.oid = source_table.relnamespace
      JOIN pg_catalog.pg_class AS target_table
        ON target_table.oid = constraint_row.confrelid
      JOIN pg_catalog.pg_namespace AS target_namespace
        ON target_namespace.oid = target_table.relnamespace
      JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY
        AS source_key(attnum, ordinality) ON TRUE
      JOIN pg_catalog.pg_attribute AS source_column
        ON source_column.attrelid = constraint_row.conrelid
       AND source_column.attnum = source_key.attnum
      JOIN LATERAL unnest(constraint_row.confkey) WITH ORDINALITY
        AS target_key(attnum, ordinality)
        ON target_key.ordinality = source_key.ordinality
      JOIN pg_catalog.pg_attribute AS target_column
        ON target_column.attrelid = constraint_row.confrelid
       AND target_column.attnum = target_key.attnum
     WHERE constraint_row.contype = 'f'
       AND source_namespace.nspname = ANY(:schemas)
     GROUP BY source_namespace.nspname, source_table.relname,
              constraint_row.conname, target_namespace.nspname,
              target_table.relname, constraint_row.confdeltype,
              constraint_row.condeferrable, constraint_row.condeferred
     ORDER BY source_namespace.nspname, source_table.relname, constraint_row.conname
    """
).bindparams(sa.bindparam("schemas", type_=sa.ARRAY(sa.Text())))


def expected_foreign_key_shapes() -> tuple[ForeignKeyShape, ...]:
    """Return the immutable FK graph expected at the current schema head."""

    return _EXPECTED_SHAPES


def actual_foreign_key_shapes(connection: Connection) -> tuple[ForeignKeyShape, ...]:
    """Read Eva-owned foreign keys from PostgreSQL without changing the database."""

    rows = connection.execute(_ACTUAL_FOREIGN_KEYS, {"schemas": list(_EVA_SCHEMAS)})
    shapes = tuple(
        sorted(
            ForeignKeyShape(
                source_schema=str(row["source_schema"]),
                source_table=str(row["source_table"]),
                name=str(row["name"]),
                source_columns=tuple(str(item) for item in row["source_columns"]),
                target_schema=str(row["target_schema"]),
                target_table=str(row["target_table"]),
                target_columns=tuple(str(item) for item in row["target_columns"]),
                ondelete_code=str(row["ondelete_code"]),
                deferrable=bool(row["deferrable"]),
                initially_deferred=bool(row["initially_deferred"]),
            )
            for row in rows.mappings()
        )
    )
    return shapes


def require_current_foreign_key_catalog(connection: Connection) -> None:
    """Fail closed unless every Eva foreign key is exactly at the current head."""

    expected = set(_EXPECTED_SHAPES)
    actual = set(actual_foreign_key_shapes(connection))
    if actual == expected:
        return
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    raise ForeignKeyCatalogDrift(
        "foreign_key_catalog_drift "
        f"missing={[shape.name for shape in missing]} "
        f"unexpected={[shape.name for shape in unexpected]}"
    )


__all__ = [
    "ForeignKeyCatalogDrift",
    "ForeignKeyShape",
    "actual_foreign_key_shapes",
    "expected_foreign_key_shapes",
    "require_current_foreign_key_catalog",
]
