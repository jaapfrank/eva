"""Create read, effect, delivery and circuit-breaker authority."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from eva.adapters.postgres.catalog import (
    application_role_grant_statements,
    metadata,
    table_specs_for_schema,
)


revision = "4d_0004_effect_authority"
down_revision = "4d_0003_core_continuity"
branch_labels = None
depends_on = None


EFFECT_TABLES = tuple(spec["table"] for spec in table_specs_for_schema("eva_effects"))
CORE_EFFECT_FKS = (
    "fk_plan_steps_read_intent",
    "fk_plan_steps_effect_intent",
)


def _create_table(full_name: str) -> None:
    table = metadata.tables[full_name]
    op.execute(sa.schema.CreateTable(table, include_foreign_key_constraints=[]))
    for index in sorted(table.indexes, key=lambda item: item.name):
        op.execute(sa.schema.CreateIndex(index))


def _create_foreign_key(constraint: sa.ForeignKeyConstraint) -> None:
    elements = list(constraint.elements)
    source = constraint.table
    target = elements[0].column.table
    op.create_foreign_key(
        constraint.name,
        source.name,
        target.name,
        [element.parent.name for element in elements],
        [element.column.name for element in elements],
        source_schema=source.schema,
        referent_schema=target.schema,
        ondelete=constraint.ondelete,
        deferrable=constraint.deferrable,
        initially=constraint.initially,
    )


def _configure_table(full_name: str) -> None:
    _, name = full_name.split(".", 1)
    predicate = (
        "tenant_id::text = current_setting('eva.tenant_id', true) "
        "AND installation_id::text = current_setting('eva.installation_id', true)"
    )
    op.execute(f"ALTER TABLE {full_name} OWNER TO eva_migrator")
    op.execute(f"REVOKE ALL ON TABLE {full_name} FROM PUBLIC")
    for statement in application_role_grant_statements(full_name):
        op.execute(statement)
    op.execute(f"GRANT SELECT ON TABLE {full_name} TO eva_backup")
    op.execute(f"ALTER TABLE {full_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {full_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {name}_scope ON {full_name} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def _phase_foreign_keys() -> tuple[sa.ForeignKeyConstraint, ...]:
    selected: list[sa.ForeignKeyConstraint] = []
    for full_name in EFFECT_TABLES:
        for constraint in metadata.tables[full_name].foreign_key_constraints:
            target_schema = list(constraint.elements)[0].column.table.schema
            if target_schema in {"eva_core", "eva_effects"}:
                selected.append(constraint)
    for constraint in metadata.tables["eva_core.plan_steps"].foreign_key_constraints:
        if constraint.name in CORE_EFFECT_FKS:
            selected.append(constraint)
    return tuple(sorted(selected, key=lambda item: item.name))


def upgrade() -> None:
    op.execute("SET LOCAL ROLE eva_migrator")
    op.execute("GRANT USAGE ON SCHEMA eva_effects TO eva_runtime, eva_dbos, eva_backup")
    for full_name in EFFECT_TABLES:
        _create_table(full_name)
    for constraint in _phase_foreign_keys():
        _create_foreign_key(constraint)
    for full_name in EFFECT_TABLES:
        _configure_table(full_name)


def downgrade() -> None:
    for constraint in reversed(_phase_foreign_keys()):
        op.drop_constraint(
            constraint.name,
            constraint.table.name,
            schema=constraint.table.schema,
            type_="foreignkey",
        )
    for full_name in reversed(EFFECT_TABLES):
        schema, name = full_name.split(".", 1)
        op.drop_table(name, schema=schema)
