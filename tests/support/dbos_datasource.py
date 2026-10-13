"""Historical DBOS datasource retained for exact database regressions."""

from __future__ import annotations

import inspect
import hashlib
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from dbos import DBOSPortableJSONSerializer, SQLAlchemyDatasource
import dbos._datasource as dbos_datasource_module

from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn


PATCHED_DBOS_VERSION = "2.30.0+eva.1"
DBOS_SCHEMA = "dbos"
DBOS_OWNER = "eva_dbos"
MAX_TRANSACTION_ATTEMPTS = 3
PATCHED_DATASOURCE_SHA256 = (
    "af7032940190792f98a4a923f1c3632b245518068d98680bac7206940f4f8b5f"
)

_EXPECTED_COLUMNS = [
    (1, "workflow_id", "text", "pg_catalog", "default", True, "", "", None),
    (2, "step_id", "integer", None, None, True, "", "", None),
    (3, "output", "text", "pg_catalog", "default", False, "", "", None),
    (4, "error", "text", "pg_catalog", "default", False, "", "", None),
    (5, "serialization", "text", "pg_catalog", "default", False, "", "", None),
    (
        6,
        "created_at",
        "bigint",
        None,
        None,
        True,
        "",
        "",
        "((EXTRACT(epoch FROM now()) * (1000)::numeric))::bigint",
    ),
]
_PROVENANCE = object()


class DBOSDatasourceAttestationError(RuntimeError):
    """The installed fork or pre-provisioned DBOS schema is not exact."""


class EvaDBOSDatasource:
    """Opaque provenance wrapper for the one attested sync datasource."""

    __slots__ = ("__closed", "__database_identity", "__datasource")

    def __init__(
        self,
        datasource: SQLAlchemyDatasource,
        database_identity: tuple[str, str, int],
        provenance: object,
    ) -> None:
        if provenance is not _PROVENANCE:
            raise DBOSDatasourceAttestationError(
                "EvaDBOSDatasource can only be constructed by the attested opener"
            )
        self.__datasource = datasource
        self.__database_identity = database_identity
        self.__closed = False

    def _require_open(self) -> None:
        if self.__closed:
            raise RuntimeError("4C DBOS datasource is closed")

    def transaction(self, *, name: str) -> Any:
        self._require_open()
        return self.__datasource.transaction(name=name)

    def connection(self) -> Any:
        self._require_open()
        return self.__datasource.sql_session().connection()

    def attest_system_database_url(self, system_database_url: str) -> None:
        """Require DBOS system state to use the same durable PostgreSQL database."""

        self._require_open()
        try:
            candidate = dbos_datasource_module.sa.make_url(system_database_url)
        except (TypeError, ValueError) as exc:
            raise DBOSDatasourceAttestationError(
                "4C system_database_url is not a SQLAlchemy URL"
            ) from exc
        if candidate.get_backend_name() != "postgresql":
            raise DBOSDatasourceAttestationError(
                "4C DBOS system state must use PostgreSQL"
            )
        engine = dbos_datasource_module.sa.create_engine(
            sqlalchemy_postgres_dsn(
                candidate.render_as_string(hide_password=False)
            ),
            pool_pre_ping=False,
        )
        try:
            candidate_identity = _postgres_database_identity(engine)
        except BaseException as exc:
            raise DBOSDatasourceAttestationError(
                "4C could not attest the DBOS system PostgreSQL database"
            ) from exc
        finally:
            engine.dispose()
        if candidate_identity != self.__database_identity:
            raise DBOSDatasourceAttestationError(
                "4C datasource and DBOS system state must use the same PostgreSQL database"
            )

    def close(self) -> None:
        if self.__closed:
            return
        self.__closed = True
        if self.__datasource.created_engine:
            self.__datasource.engine.dispose()


def attest_patched_sync_dbos() -> None:
    try:
        installed = version("dbos")
    except PackageNotFoundError as exc:
        raise DBOSDatasourceAttestationError("DBOS is not installed") from exc
    if installed != PATCHED_DBOS_VERSION:
        raise DBOSDatasourceAttestationError(
            f"Eva requires DBOS {PATCHED_DBOS_VERSION}, found {installed}"
        )
    parameters = inspect.signature(SQLAlchemyDatasource.create).parameters
    if parameters.get("run_migrations") is None or (
        parameters.get("max_transaction_attempts") is None
    ):
        raise DBOSDatasourceAttestationError(
            "installed DBOS lacks the bounded no-DDL synchronous datasource API"
        )
    if parameters["run_migrations"].default is not True or (
        parameters["max_transaction_attempts"].default != MAX_TRANSACTION_ATTEMPTS
    ):
        raise DBOSDatasourceAttestationError("installed DBOS patch defaults differ")
    source = Path(dbos_datasource_module.__file__).read_bytes()
    if hashlib.sha256(source).hexdigest() != PATCHED_DATASOURCE_SHA256:
        raise DBOSDatasourceAttestationError(
            "installed DBOS _datasource.py bytes differ from the pinned Eva patch"
        )


def _postgres_database_identity(engine: Any) -> tuple[str, str, int]:
    with engine.connect() as connection:
        row = connection.exec_driver_sql(
            "SELECT current_database(), inet_server_addr()::text, inet_server_port()"
        ).one()
    if row[0] is None or row[1] is None or row[2] is None:
        raise DBOSDatasourceAttestationError(
            "4C requires a TCP PostgreSQL database with an attestable server identity"
        )
    return str(row[0]), str(row[1]), int(row[2])


def _attest_postgres_catalog(datasource: SQLAlchemyDatasource) -> None:
    ownership_sql = """
        SELECT schema_owner.rolname, table_owner.rolname, table_class.relkind,
               table_class.relpersistence, table_class.relrowsecurity,
               table_class.relforcerowsecurity
          FROM pg_catalog.pg_namespace AS namespace
          JOIN pg_catalog.pg_roles AS schema_owner
            ON schema_owner.oid = namespace.nspowner
          JOIN pg_catalog.pg_class AS table_class
            ON table_class.relnamespace = namespace.oid
          JOIN pg_catalog.pg_roles AS table_owner
            ON table_owner.oid = table_class.relowner
         WHERE namespace.nspname = 'dbos'
           AND table_class.relname = 'datasource_outputs'
    """
    columns_sql = """
        SELECT attribute.attnum,
               attribute.attname,
               pg_catalog.format_type(attribute.atttypid, attribute.atttypmod),
               collation_namespace.nspname,
               collation_row.collname,
               attribute.attnotnull,
               attribute.attidentity,
               attribute.attgenerated,
               pg_catalog.pg_get_expr(default_value.adbin, default_value.adrelid)
          FROM pg_catalog.pg_attribute AS attribute
          JOIN pg_catalog.pg_class AS table_class
            ON table_class.oid = attribute.attrelid
          JOIN pg_catalog.pg_namespace AS namespace
            ON namespace.oid = table_class.relnamespace
          LEFT JOIN pg_catalog.pg_attrdef AS default_value
            ON default_value.adrelid = attribute.attrelid
           AND default_value.adnum = attribute.attnum
          LEFT JOIN pg_catalog.pg_collation AS collation_row
            ON collation_row.oid = attribute.attcollation
          LEFT JOIN pg_catalog.pg_namespace AS collation_namespace
            ON collation_namespace.oid = collation_row.collnamespace
         WHERE namespace.nspname = 'dbos'
           AND table_class.relname = 'datasource_outputs'
           AND attribute.attnum > 0
           AND NOT attribute.attisdropped
         ORDER BY attribute.attnum
    """
    primary_key_sql = """
        SELECT constraint_row.conname,
               ARRAY(
                   SELECT attribute.attname
                     FROM unnest(constraint_row.conkey)
                       WITH ORDINALITY AS key_column(attnum, ordinality)
                     JOIN pg_catalog.pg_attribute AS attribute
                       ON attribute.attrelid = table_class.oid
                      AND attribute.attnum = key_column.attnum
                    ORDER BY key_column.ordinality
               ),
               constraint_row.condeferrable,
               constraint_row.condeferred,
               constraint_row.convalidated,
               index_row.indisprimary,
               index_row.indisunique,
               index_row.indisvalid,
               index_row.indisready,
               index_row.indislive,
               pg_catalog.pg_get_expr(index_row.indpred, index_row.indrelid),
               access_method.amname,
               pg_catalog.pg_get_indexdef(index_row.indexrelid)
          FROM pg_catalog.pg_constraint AS constraint_row
          JOIN pg_catalog.pg_class AS table_class
            ON table_class.oid = constraint_row.conrelid
          JOIN pg_catalog.pg_namespace AS namespace
            ON namespace.oid = table_class.relnamespace
          JOIN pg_catalog.pg_index AS index_row
            ON index_row.indexrelid = constraint_row.conindid
          JOIN pg_catalog.pg_class AS index_class
            ON index_class.oid = index_row.indexrelid
          JOIN pg_catalog.pg_am AS access_method
            ON access_method.oid = index_class.relam
         WHERE namespace.nspname = 'dbos'
           AND table_class.relname = 'datasource_outputs'
           AND constraint_row.contype = 'p'
    """
    constraints_sql = """
        SELECT constraint_row.conname, constraint_row.contype
          FROM pg_catalog.pg_constraint AS constraint_row
         WHERE constraint_row.conrelid = 'dbos.datasource_outputs'::regclass
         ORDER BY constraint_row.conname
    """
    triggers_sql = """
        SELECT trigger_row.tgname, trigger_row.tgenabled,
               trigger_row.tgisinternal,
               pg_catalog.pg_get_triggerdef(trigger_row.oid, true)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE trigger_row.tgrelid = 'dbos.datasource_outputs'::regclass
         ORDER BY trigger_row.tgname
    """
    indexes_sql = """
        SELECT index_class.relname
          FROM pg_catalog.pg_index AS index_row
          JOIN pg_catalog.pg_class AS table_class
            ON table_class.oid = index_row.indrelid
          JOIN pg_catalog.pg_namespace AS namespace
            ON namespace.oid = table_class.relnamespace
          JOIN pg_catalog.pg_class AS index_class
            ON index_class.oid = index_row.indexrelid
         WHERE namespace.nspname = 'dbos'
           AND table_class.relname = 'datasource_outputs'
         ORDER BY index_class.relname
    """
    rules_sql = """
        SELECT rewrite_row.rulename
          FROM pg_catalog.pg_rewrite AS rewrite_row
         WHERE rewrite_row.ev_class = 'dbos.datasource_outputs'::regclass
         ORDER BY rewrite_row.rulename
    """
    inheritance_sql = """
        SELECT parent_namespace.nspname, parent_class.relname,
               child_namespace.nspname, child_class.relname
          FROM pg_catalog.pg_inherits AS inheritance_row
          JOIN pg_catalog.pg_class AS parent_class
            ON parent_class.oid = inheritance_row.inhparent
          JOIN pg_catalog.pg_namespace AS parent_namespace
            ON parent_namespace.oid = parent_class.relnamespace
          JOIN pg_catalog.pg_class AS child_class
            ON child_class.oid = inheritance_row.inhrelid
          JOIN pg_catalog.pg_namespace AS child_namespace
            ON child_namespace.oid = child_class.relnamespace
         WHERE inheritance_row.inhparent = 'dbos.datasource_outputs'::regclass
            OR inheritance_row.inhrelid = 'dbos.datasource_outputs'::regclass
         ORDER BY parent_namespace.nspname, parent_class.relname,
                  child_namespace.nspname, child_class.relname
    """

    with datasource.engine.connect() as connection:
        ownership = connection.exec_driver_sql(ownership_sql).one_or_none()
        columns = [tuple(row) for row in connection.exec_driver_sql(columns_sql)]
        primary_keys = [
            (row[0], list(row[1]), *tuple(row[2:]))
            for row in connection.exec_driver_sql(primary_key_sql)
        ]
        constraints = [
            tuple(row) for row in connection.exec_driver_sql(constraints_sql)
        ]
        triggers = [tuple(row) for row in connection.exec_driver_sql(triggers_sql)]
        indexes = [tuple(row) for row in connection.exec_driver_sql(indexes_sql)]
        rules = [tuple(row) for row in connection.exec_driver_sql(rules_sql)]
        inheritance = [
            tuple(row) for row in connection.exec_driver_sql(inheritance_sql)
        ]

    if ownership is None or tuple(ownership) != (
        DBOS_OWNER,
        DBOS_OWNER,
        "r",
        "p",
        False,
        False,
    ):
        raise DBOSDatasourceAttestationError(
            "dbos schema and datasource_outputs must be owned by eva_dbos"
        )
    if columns != _EXPECTED_COLUMNS:
        raise DBOSDatasourceAttestationError(
            "dbos.datasource_outputs columns differ from DBOS 2.30.0"
        )
    expected_primary_key = (
        "datasource_outputs_pkey",
        ["workflow_id", "step_id"],
        False,
        False,
        True,
        True,
        True,
        True,
        True,
        True,
        None,
        "btree",
        "CREATE UNIQUE INDEX datasource_outputs_pkey ON dbos.datasource_outputs "
        "USING btree (workflow_id, step_id)",
    )
    if primary_keys != [expected_primary_key]:
        raise DBOSDatasourceAttestationError(
            "dbos.datasource_outputs primary-key index is not exact, valid, "
            "ready, unique, and non-deferrable"
        )
    if constraints != [("datasource_outputs_pkey", "p")]:
        raise DBOSDatasourceAttestationError(
            "dbos.datasource_outputs has unexpected constraints"
        )
    if triggers:
        raise DBOSDatasourceAttestationError(
            "dbos.datasource_outputs has unexpected triggers"
        )
    if indexes != [("datasource_outputs_pkey",)]:
        raise DBOSDatasourceAttestationError(
            "dbos.datasource_outputs has unexpected indexes"
        )
    if rules:
        raise DBOSDatasourceAttestationError(
            "dbos.datasource_outputs has unexpected rewrite rules"
        )
    if inheritance:
        raise DBOSDatasourceAttestationError(
            "dbos.datasource_outputs participates in table inheritance"
        )


def open_runtime_datasource(
    database_url: str,
    *,
    engine_kwargs: dict[str, Any] | None = None,
) -> EvaDBOSDatasource:
    """Open a pre-migrated sync datasource without issuing runtime DDL."""

    attest_patched_sync_dbos()
    try:
        parsed_url = dbos_datasource_module.sa.make_url(database_url)
    except (TypeError, ValueError) as exc:
        raise DBOSDatasourceAttestationError(
            "4C datasource URL is not a SQLAlchemy URL"
        ) from exc
    if parsed_url.get_backend_name() != "postgresql":
        raise DBOSDatasourceAttestationError(
            "Eva 4C supports only a synchronous PostgreSQL datasource"
        )
    datasource = SQLAlchemyDatasource.create(
        database_url,
        engine_kwargs=engine_kwargs,
        schema=DBOS_SCHEMA,
        serializer=DBOSPortableJSONSerializer(),
        run_migrations=False,
        max_transaction_attempts=MAX_TRANSACTION_ATTEMPTS,
    )
    try:
        if datasource.engine.url != parsed_url:
            raise DBOSDatasourceAttestationError(
                "DBOS datasource engine URL differs from the attested PostgreSQL URL"
            )
        _attest_postgres_catalog(datasource)
        database_identity = _postgres_database_identity(datasource.engine)
    except BaseException:
        if datasource.created_engine:
            datasource.engine.dispose()
        raise
    return EvaDBOSDatasource(
        datasource,
        database_identity,
        _PROVENANCE,
    )
