from __future__ import annotations

import sqlalchemy as sa

from eva.adapters.postgres.catalog import metadata as sealed_metadata
from eva.adapters.postgres.current_catalog import (
    CURRENT_ATTENTION_KIND_CHECK,
    OLD_ATTENTION_KIND_CHECK,
    metadata as current_metadata,
)


def _kind_check(metadata: sa.MetaData) -> str:
    table = metadata.tables["eva_core.attention_items"]
    matches = [
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, sa.CheckConstraint)
        and constraint.name == "ck_attention_items_kind"
    ]
    assert len(matches) == 1
    return matches[0]


def test_current_catalog_changes_only_the_attention_kind_constraint() -> None:
    assert _kind_check(sealed_metadata) == OLD_ATTENTION_KIND_CHECK
    assert _kind_check(current_metadata) == CURRENT_ATTENTION_KIND_CHECK
    assert set(current_metadata.tables) == set(sealed_metadata.tables)

    for name, sealed_table in sealed_metadata.tables.items():
        current_table = current_metadata.tables[name]
        assert tuple(sealed_table.c.keys()) == tuple(current_table.c.keys())
        sealed_constraints = {
            constraint.name: str(getattr(constraint, "sqltext", ""))
            for constraint in sealed_table.constraints
            if constraint.name != "ck_attention_items_kind"
        }
        current_constraints = {
            constraint.name: str(getattr(constraint, "sqltext", ""))
            for constraint in current_table.constraints
            if constraint.name != "ck_attention_items_kind"
        }
        assert current_constraints == sealed_constraints
