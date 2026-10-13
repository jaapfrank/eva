"""Admit delegated work to the existing attention queue.

Revision ID: 4e_0002_delegated_work
Revises: 4e_0001_operational_timeline
"""

from __future__ import annotations

from alembic import op

from eva.adapters.postgres.current_catalog import (
    CURRENT_ATTENTION_KIND_CHECK,
    OLD_ATTENTION_KIND_CHECK,
)


revision = "4e_0002_delegated_work"
down_revision = "4e_0001_operational_timeline"
branch_labels = None
depends_on = None


def _replace_kind_check(expression: str) -> None:
    op.drop_constraint(
        "ck_attention_items_kind",
        "attention_items",
        schema="eva_core",
        type_="check",
    )
    op.create_check_constraint(
        "ck_attention_items_kind",
        "attention_items",
        expression,
        schema="eva_core",
    )


def upgrade() -> None:
    _replace_kind_check(CURRENT_ATTENTION_KIND_CHECK)


def downgrade() -> None:
    _replace_kind_check(OLD_ATTENTION_KIND_CHECK)
