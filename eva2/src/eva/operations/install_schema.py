"""Online, fail-closed schema installation for a new Eva database.

The historical 4B revisions intentionally require the migration role while the
4D admission must run as the unrestricted external installation session.  A
plain ``alembic upgrade head`` cannot satisfy both authorities, so this module
owns the one supported fresh/forward route used by the full-stack installer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path

import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.adapters.postgres.foreign_key_catalog import require_current_foreign_key_catalog
from eva.bootstrap.secrets import read_runtime_secret_file

_REVISION_4B0 = "4b_0000_core_namespace"
_REVISION_4B1 = "4b_0001_tenant_scope"
_REVISION_4B2 = "4b_0002_episode_atomicity"
_KNOWN_FORWARD_REVISIONS = {
    _REVISION_4B0,
    _REVISION_4B1,
    _REVISION_4B2,
    "4d_0003_core_continuity",
    "4d_0004_effect_authority",
    "4d_0005_evidence_objects",
    "4d_0006_memory",
    "4d_0007_projection_telemetry",
    "4e_0001_operational_timeline",
    "4e_0002_delegated_work",
    "4e_0003_delegated_work_runs",
    "4e_0004_delegated_work_stop",
}
_HEAD_REVISION = "4e_0004_delegated_work_stop"


@dataclass(frozen=True, slots=True)
class SchemaInstallationReceipt:
    source_revision: str | None
    final_revision: str
    route: tuple[str, ...]
    drift_check: str


def _canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _secret(*, value_name: str, file_name: str) -> str:
    value = os.environ.get(value_name, "").strip()
    if value:
        raise RuntimeError(f"{value_name} is forbidden; use {file_name}")
    raw_path = os.environ.get(file_name, "").strip()
    if not raw_path:
        raise RuntimeError(f"{file_name} is required")
    return read_runtime_secret_file(raw_path, setting_name=file_name)


def _current_revision(engine: sa.Engine) -> str | None:
    with engine.connect() as connection:
        ledger = bool(
            connection.scalar(
                sa.text("SELECT to_regclass('public.eva_alembic_version') IS NOT NULL")
            )
        )
        if not ledger:
            return None
        value = connection.scalar(
            sa.text("SELECT version_num FROM public.eva_alembic_version")
        )
        if value is None:
            return None
        if not isinstance(value, str) or value not in _KNOWN_FORWARD_REVISIONS:
            raise RuntimeError("database has an unknown Eva schema revision")
        return value


def install_schema(
    database_dsn: str, *, alembic_ini: Path
) -> SchemaInstallationReceipt:
    try:
        sqlalchemy_dsn = sqlalchemy_postgres_dsn(database_dsn)
    except ValueError as error:
        raise RuntimeError("Eva schema installation requires PostgreSQL") from error
    if alembic_ini.is_symlink() or not alembic_ini.is_file():
        raise RuntimeError("Alembic configuration is unavailable")
    config = Config(str(alembic_ini))
    config.set_main_option("sqlalchemy.url", sqlalchemy_dsn.replace("%", "%%"))
    engine = sa.create_engine(sqlalchemy_dsn)
    route: list[str] = []
    try:
        source_revision = _current_revision(engine)
        current = source_revision
        if current is None:
            command.upgrade(config, _REVISION_4B0)
            route.append("base_to_4b_0000_core_namespace")
            current = _REVISION_4B0
        if current in {_REVISION_4B0, _REVISION_4B1}:
            with engine.begin() as connection:
                connection.exec_driver_sql("SET LOCAL ROLE eva_migrator")
                config.attributes["connection"] = connection
                try:
                    command.upgrade(config, _REVISION_4B2)
                finally:
                    config.attributes.pop("connection", None)
            route.append("migrator_role_to_4b_0002_episode_atomicity")
            current = _REVISION_4B2
        if current != _HEAD_REVISION:
            command.upgrade(config, "head")
            route.append("online_empty_admission_or_forward_upgrade_to_head")
        final_revision = _current_revision(engine)
        if final_revision != _HEAD_REVISION:
            raise RuntimeError("Eva schema installation did not reach head")
        # Alembic covers pending ordinary metadata operations.  Its own table
        # sorter warns, and omits FK comparison, for Eva's intentional cycles;
        # the direct catalog check immediately below closes that exact gap.
        with warnings.catch_warnings():
            warnings.filterwarnings(
                "ignore",
                message="Cannot correctly sort tables; there are unresolvable cycles.*",
                category=sa.exc.SAWarning,
            )
            command.check(config)
        with engine.connect() as connection:
            require_current_foreign_key_catalog(connection)
        return SchemaInstallationReceipt(
            source_revision,
            final_revision,
            tuple(route),
            "no_new_upgrade_operations_and_exact_foreign_keys",
        )
    finally:
        engine.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="eva-install-schema")
    parser.add_argument(
        "--alembic-ini", type=Path, default=Path("/opt/eva/alembic.ini")
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    try:
        receipt = install_schema(
            _secret(value_name="EVA_DATABASE_DSN", file_name="EVA_DATABASE_DSN_FILE"),
            alembic_ini=arguments.alembic_ini,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(_canonical({"error": str(error), "status": "failed"}))
        return 2
    print(
        _canonical(
            {
                "contract": "EvaSchemaInstallationReceipt/v1",
                "drift_check": receipt.drift_check,
                "final_revision": receipt.final_revision,
                "route": list(receipt.route),
                "source_revision": receipt.source_revision,
                "status": "passed",
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["SchemaInstallationReceipt", "install_schema", "main"]
