"""Current Alembic metadata projected over the sealed historical 4D catalog."""

from __future__ import annotations

import sqlalchemy as sa

from eva.adapters.postgres.catalog import metadata as sealed_metadata


OLD_ATTENTION_KIND_CHECK = (
    "kind IN ('ingress','continuation','deadline','external_signal',"
    "'delivery_recovery')"
)
CURRENT_ATTENTION_KIND_CHECK = (
    "kind IN ('ingress','continuation','deadline','external_signal',"
    "'delivery_recovery','delegated_work')"
)

metadata = sa.MetaData()
for _table in sealed_metadata.tables.values():
    _table.to_metadata(metadata)

_attention = metadata.tables["eva_core.attention_items"]
_kind_constraints = [
    constraint
    for constraint in _attention.constraints
    if isinstance(constraint, sa.CheckConstraint)
    and constraint.name == "ck_attention_items_kind"
]
if len(_kind_constraints) != 1 or str(_kind_constraints[0].sqltext) != (
    OLD_ATTENTION_KIND_CHECK
):
    raise RuntimeError("sealed attention kind constraint differs")
_attention.constraints.remove(_kind_constraints[0])
_attention.append_constraint(
    sa.CheckConstraint(
        CURRENT_ATTENTION_KIND_CHECK,
        name="ck_attention_items_kind",
    )
)

del _attention, _kind_constraints, _table


__all__ = [
    "CURRENT_ATTENTION_KIND_CHECK",
    "OLD_ATTENTION_KIND_CHECK",
    "metadata",
]
