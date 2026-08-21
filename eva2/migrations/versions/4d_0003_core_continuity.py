"""Admit an empty 4B catalog and expand the final eva_core authority."""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

from eva.adapters.postgres.catalog import (
    PRESERVED_4B_COLLATION_ALTERS,
    application_role_grant_statements,
    application_role_revoke_statements,
    metadata,
    table_specs_for_schema,
)
from eva.adapters.postgres.schema import metadata as metadata_4b
from eva.operations.migration_admission import (
    INITIAL_ADMISSION_STATEMENTS,
    InitialCatalogAdmissionError,
    run_initial_catalog_admission,
    validate_ddl_owner_row,
    validate_external_authority_row,
)


revision = "4d_0003_core_continuity"
down_revision = "4b_0002_episode_atomicity"
branch_labels = None
depends_on = None


PRESERVED = (
    "eva_core.tenants",
    "eva_core.installations",
    "eva_core.episodes",
    "eva_core.episode_events",
)
NEW_CORE = tuple(
    spec["table"]
    for spec in table_specs_for_schema("eva_core")
    if spec["table"] not in PRESERVED
)
EXISTING_4B_CONSTRAINTS = {
    constraint.name
    for table in metadata_4b.tables.values()
    for constraint in table.constraints
}
NAMESPACE_BOOTSTRAP_SCHEMAS = (
    "eva_effects",
    "eva_evidence",
    "eva_memory",
    "eva_projection",
    "eva_telemetry",
)
_COLLATION_TYPE_SQL = {
    "text": "text",
    "varchar(64)": "varchar(64)",
}


def _result_mapping(result: object) -> dict[str, object]:
    return dict(result.mappings().one())


def _bootstrap_namespaces() -> None:
    """Run the sealed post-admission namespace exception before table DDL."""

    connection = op.get_bind()
    try:
        connection.exec_driver_sql(INITIAL_ADMISSION_STATEMENTS[1].sql)
        authority = connection.exec_driver_sql(INITIAL_ADMISSION_STATEMENTS[3].sql)
        validate_external_authority_row(_result_mapping(authority))
        for schema in NAMESPACE_BOOTSTRAP_SCHEMAS:
            connection.exec_driver_sql(
                f"CREATE SCHEMA {schema} AUTHORIZATION eva_migrator;"
            )
            connection.exec_driver_sql(f"REVOKE ALL ON SCHEMA {schema} FROM PUBLIC;")
        connection.exec_driver_sql(INITIAL_ADMISSION_STATEMENTS[7].sql)
        owner = connection.exec_driver_sql(INITIAL_ADMISSION_STATEMENTS[8].sql)
        validate_ddl_owner_row(_result_mapping(owner))
    except InitialCatalogAdmissionError:
        raise
    except Exception as error:
        raise InitialCatalogAdmissionError(
            "eva4d_initial_admission_unprovable"
        ) from error


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


def _alter_preserved_collations(*, restore_default: bool) -> None:
    collation_clause = 'COLLATE "default"' if restore_default else 'COLLATE "C"'
    for entry in PRESERVED_4B_COLLATION_ALTERS:
        type_sql = _COLLATION_TYPE_SQL[entry["declaredType"]]
        op.execute(
            f"ALTER TABLE {entry['table']} ALTER COLUMN {entry['column']} "
            f"TYPE {type_sql} {collation_clause}"
        )


def _apply_application_role_grants(full_name: str) -> None:
    for statement in application_role_grant_statements(full_name):
        op.execute(statement)


def _add_preserved_columns_and_objects(full_name: str) -> None:
    table = metadata.tables[full_name]
    historical = metadata_4b.tables[full_name]
    for column in table.c:
        if column.name not in historical.c:
            op.add_column(
                table.name,
                sa.Column(column.name, column.type, nullable=column.nullable),
                schema=table.schema,
            )
    for constraint in sorted(table.constraints, key=lambda item: item.name):
        if constraint.name in EXISTING_4B_CONSTRAINTS:
            continue
        if isinstance(constraint, sa.CheckConstraint):
            op.create_check_constraint(
                constraint.name,
                table.name,
                constraint.sqltext,
                schema=table.schema,
            )
        elif isinstance(constraint, sa.UniqueConstraint):
            op.create_unique_constraint(
                constraint.name,
                table.name,
                [column.name for column in constraint.columns],
                schema=table.schema,
            )
    for index in sorted(table.indexes, key=lambda item: item.name):
        op.execute(sa.schema.CreateIndex(index))


def _configure_new_table(full_name: str) -> None:
    schema, name = full_name.split(".", 1)
    predicate = (
        "tenant_id::text = current_setting('eva.tenant_id', true)"
        if metadata.tables[full_name].info["scope"] == "tenant"
        else "tenant_id::text = current_setting('eva.tenant_id', true) "
        "AND installation_id::text = current_setting('eva.installation_id', true)"
    )
    op.execute(f"ALTER TABLE {full_name} OWNER TO eva_migrator")
    op.execute(f"REVOKE ALL ON TABLE {full_name} FROM PUBLIC")
    _apply_application_role_grants(full_name)
    op.execute(f"ALTER TABLE {full_name} ENABLE ROW LEVEL SECURITY")
    op.execute(f"ALTER TABLE {full_name} FORCE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY {name}_scope ON {full_name} "
        f"USING ({predicate}) WITH CHECK ({predicate})"
    )


def _add_core_foreign_keys() -> None:
    for spec in table_specs_for_schema("eva_core"):
        table = metadata.tables[spec["table"]]
        for constraint in sorted(
            table.foreign_key_constraints, key=lambda item: item.name
        ):
            target_schema = list(constraint.elements)[0].column.table.schema
            if (
                target_schema == "eva_core"
                and constraint.name not in EXISTING_4B_CONSTRAINTS
            ):
                _create_foreign_key(constraint)


def upgrade() -> None:
    context = op.get_context()
    if not context.config.attributes.get("eva4d_initial_admission_complete"):
        run_initial_catalog_admission(op.get_bind())
    _bootstrap_namespaces()
    _alter_preserved_collations(restore_default=False)

    for full_name in PRESERVED:
        _add_preserved_columns_and_objects(full_name)
    for full_name in NEW_CORE:
        _create_table(full_name)
    _add_core_foreign_keys()
    for full_name in PRESERVED:
        _apply_application_role_grants(full_name)
    for full_name in NEW_CORE:
        _configure_new_table(full_name)

    all_core = ", ".join(spec["table"] for spec in table_specs_for_schema("eva_core"))
    op.execute(f"GRANT SELECT ON TABLE {all_core} TO eva_backup")


def downgrade() -> None:
    # This downgrade is admitted only before seed/traffic by the frozen range.
    for full_name in PRESERVED:
        for statement in application_role_revoke_statements(full_name):
            op.execute(statement)
    for spec in reversed(table_specs_for_schema("eva_core")):
        table = metadata.tables[spec["table"]]
        for constraint in sorted(
            table.foreign_key_constraints, key=lambda item: item.name, reverse=True
        ):
            if (
                list(constraint.elements)[0].column.table.schema == "eva_core"
                and constraint.name not in EXISTING_4B_CONSTRAINTS
            ):
                op.drop_constraint(
                    constraint.name,
                    table.name,
                    schema=table.schema,
                    type_="foreignkey",
                )
    for full_name in reversed(NEW_CORE):
        schema, name = full_name.split(".", 1)
        op.drop_table(name, schema=schema)
    for full_name in reversed(PRESERVED):
        table = metadata.tables[full_name]
        historical = metadata_4b.tables[full_name]
        for index in sorted(table.indexes, key=lambda item: item.name, reverse=True):
            op.drop_index(index.name, table_name=table.name, schema=table.schema)
        for constraint in sorted(
            table.constraints, key=lambda item: item.name, reverse=True
        ):
            if constraint.name in EXISTING_4B_CONSTRAINTS:
                continue
            if isinstance(constraint, sa.CheckConstraint):
                op.drop_constraint(
                    constraint.name, table.name, schema=table.schema, type_="check"
                )
            elif isinstance(constraint, sa.UniqueConstraint):
                op.drop_constraint(
                    constraint.name, table.name, schema=table.schema, type_="unique"
                )
        for column in reversed(tuple(table.c)):
            if column.name not in historical.c:
                op.drop_column(table.name, column.name, schema=table.schema)

    op.execute(
        "GRANT SELECT ON TABLE eva_core.tenants, eva_core.installations, "
        "eva_core.episodes, eva_core.episode_events "
        "TO eva_runtime, eva_dbos"
    )
    op.execute(
        "GRANT UPDATE (state, current_generation, supersedes_generation, revision, "
        "last_event_id, state_payload_sha256, updated_at) ON TABLE eva_core.episodes "
        "TO eva_runtime, eva_dbos"
    )
    op.execute("GRANT INSERT ON TABLE eva_core.episode_events TO eva_runtime, eva_dbos")
    _alter_preserved_collations(restore_default=True)
    for schema in reversed(NAMESPACE_BOOTSTRAP_SCHEMAS):
        op.execute(f"DROP SCHEMA {schema}")
