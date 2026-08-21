from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from dbos import DBOSPortableJSONSerializer, SQLAlchemyDatasource
from dbos.cli.migration import run_dbos_database_migrations
from sqlalchemy.engine import Engine, URL, make_url

from eva.adapters.postgres.schema import (
    episode_events,
    episodes,
    installations,
    tenants,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.lifecycle import EpisodeState
from eva.domain.episode import EpisodeTransitionCommand


EVA2_ROOT = Path(__file__).resolve().parents[2]
REQUIRED_SERVER_VERSION_NUM = 160013
REQUIRED_IMAGE_ID = (
    "sha256:472efd9a66f2b2f1a5aeb18b28de74332e6ef88c2b93a1a5d812fb6db67a5f60"
)
DISPOSABLE_DATABASE_PREFIX = "eva2_4c_disposable_"
LOGIN_ROLE = "eva2_4c_login"
LOGIN_PASSWORD = "eva2_4c_runtime_only"


@dataclass(frozen=True, slots=True)
class Postgres4CGate:
    admin_engine: Engine
    admin_dsn: str
    runtime_dsn: str
    container_name: str


@dataclass(frozen=True, slots=True)
class Seeded4CEpisode:
    tenant_id: str
    installation_id: str
    episode_id: str
    initial_state: EpisodeState
    command: EpisodeTransitionCommand
    expected_result: dict[str, object]


def _runtime_url(admin_url: URL) -> str:
    return (
        admin_url.set(
            username=LOGIN_ROLE,
            password=LOGIN_PASSWORD,
            drivername="postgresql+psycopg",
        )
        .update_query_dict({"options": "-c role=eva_dbos"})
        .render_as_string(hide_password=False)
    )


def _alembic_config(dsn: str) -> Config:
    config = Config(str(EVA2_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    return config


def _attest_container(name: str) -> None:
    raw = subprocess.run(
        ["docker", "inspect", name],
        check=True,
        text=True,
        capture_output=True,
        timeout=10,
    ).stdout
    inspection = json.loads(raw)
    if len(inspection) != 1:
        pytest.fail("the 4C gate requires exactly one named disposable container")
    item = inspection[0]
    if item["Image"] != REQUIRED_IMAGE_ID:
        pytest.fail(f"wrong PostgreSQL image ID: {item['Image']}")
    if item["Config"]["Labels"].get("eva2.scope") != "4c-validation":
        pytest.fail("the disposable container lacks eva2.scope=4c-validation")
    if not item["State"]["Running"]:
        pytest.fail("the disposable PostgreSQL container is not running")


@pytest.fixture(scope="session")
def postgres_4c_gate() -> Iterator[Postgres4CGate]:
    dsn = os.environ.get("EVA2_4C_ADMIN_DSN", "").strip()
    container_name = os.environ.get("EVA2_4C_CONTAINER_NAME", "").strip()
    if not dsn or not container_name:
        pytest.fail(
            "EVA2_4C_ADMIN_DSN and EVA2_4C_CONTAINER_NAME are required; "
            "the real 4C gate must not skip"
        )
    _attest_container(container_name)
    url = make_url(dsn)
    if url.get_backend_name() != "postgresql":
        pytest.fail("EVA2_4C_ADMIN_DSN must select PostgreSQL")
    if not url.database or not url.database.startswith(DISPOSABLE_DATABASE_PREFIX):
        pytest.fail("the database name must start with eva2_4c_disposable_")

    engine = sa.create_engine(url, pool_pre_ping=False)
    config = _alembic_config(dsn)
    with engine.connect() as connection:
        version = int(connection.scalar(sa.text("SHOW server_version_num")))
        if version != REQUIRED_SERVER_VERSION_NUM:
            pytest.fail(
                f"PostgreSQL 16.13 ({REQUIRED_SERVER_VERSION_NUM}) required, got {version}"
            )
        role = connection.execute(
            sa.text(
                "SELECT rolsuper, rolcreaterole FROM pg_roles "
                "WHERE rolname = current_user"
            )
        ).one()
        if not role.rolsuper or not role.rolcreaterole:
            pytest.fail("4C provisioning requires an administrative disposable role")
        schemas = set(
            connection.scalars(
                sa.text(
                    "SELECT nspname FROM pg_namespace WHERE nspname !~ '^pg_' "
                    "AND nspname NOT IN ('information_schema', 'public')"
                )
            )
        )
        public_relations = set(
            connection.scalars(
                sa.text(
                    "SELECT c.relname FROM pg_class c JOIN pg_namespace n "
                    "ON n.oid=c.relnamespace WHERE n.nspname='public' "
                    "AND c.relkind IN ('r','p','v','m','S','f')"
                )
            )
        )
        if schemas or public_relations:
            pytest.fail(
                "4C DSN must point to an empty disposable database; "
                f"schemas={sorted(schemas)}, relations={sorted(public_relations)}"
            )

    command.upgrade(config, "4b_0000_core_namespace")
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        config.attributes["connection"] = connection
        try:
            command.upgrade(config, "head")
        finally:
            config.attributes.pop("connection", None)

    with engine.begin() as connection:
        connection.execute(
            sa.text(
                f"DO $role$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE "
                f"rolname='{LOGIN_ROLE}') THEN CREATE ROLE {LOGIN_ROLE} LOGIN "
                f"PASSWORD '{LOGIN_PASSWORD}' NOSUPERUSER NOCREATEDB NOCREATEROLE "
                "NOINHERIT NOREPLICATION NOBYPASSRLS; END IF; END $role$"
            )
        )
        connection.execute(
            sa.text(f"ALTER ROLE {LOGIN_ROLE} PASSWORD '{LOGIN_PASSWORD}'")
        )
        connection.execute(sa.text(f"GRANT eva_dbos TO {LOGIN_ROLE}"))
        connection.execute(
            sa.text(f'GRANT CREATE ON DATABASE "{url.database}" TO eva_dbos')
        )

    runtime_dsn = _runtime_url(url)
    try:
        run_dbos_database_migrations(runtime_dsn, schema="dbos")
        datasource = SQLAlchemyDatasource.create(
            runtime_dsn,
            schema="dbos",
            serializer=DBOSPortableJSONSerializer(),
            run_migrations=True,
            max_transaction_attempts=3,
        )
        datasource.engine.dispose()
    finally:
        with engine.begin() as connection:
            connection.execute(
                sa.text(f'REVOKE CREATE ON DATABASE "{url.database}" FROM eva_dbos')
            )

    try:
        yield Postgres4CGate(
            admin_engine=engine,
            admin_dsn=dsn,
            runtime_dsn=runtime_dsn,
            container_name=container_name,
        )
    finally:
        engine.dispose()


@pytest.fixture
def seed_4c_episode(
    postgres_4c_gate: Postgres4CGate,
) -> Callable[[str], Seeded4CEpisode]:
    def seed(label: str) -> Seeded4CEpisode:
        def identifier(name: str) -> str:
            return str(uuid5(NAMESPACE_URL, f"eva2-4c-{label}-{name}"))

        ids = {
            name: identifier(name)
            for name in (
                "tenant",
                "installation",
                "episode",
                "principal",
                "conversation",
                "ingress",
                "initial-event",
                "initial-command",
                "transition-event",
                "transition-command",
            )
        }
        initial = EpisodeState.model_validate(
            {
                "schema_version": 1,
                "contract": "EpisodeState/v1",
                "episode_id": ids["episode"],
                "tenant_id": ids["tenant"],
                "principal_id": ids["principal"],
                "conversation_id": ids["conversation"],
                "ingress_request_id": ids["ingress"],
                "state": "accepted",
                "current_generation": 1,
                "supersedes_generation": None,
                "last_event_id": ids["initial-event"],
                "updated_at": "2026-08-21T04:00:00Z",
            }
        )
        occurred_at = "2026-08-21T04:00:01Z"
        command_value = EpisodeTransitionCommand(
            tenant_id=ids["tenant"],
            installation_id=ids["installation"],
            episode_id=ids["episode"],
            command_id=ids["transition-command"],
            event_id=ids["transition-event"],
            expected_revision=1,
            expected_state_sha256=canonical_sha256(initial),
            occurred_at=occurred_at,
        )
        after = EpisodeState.model_validate(
            {
                **initial.model_dump(mode="json"),
                "state": "planning",
                "last_event_id": ids["transition-event"],
                "updated_at": occurred_at,
            }
        )
        at = datetime(2026, 8, 21, 4, 0, tzinfo=timezone.utc)
        with postgres_4c_gate.admin_engine.begin() as connection:
            connection.execute(
                tenants.insert().values(tenant_id=ids["tenant"], created_at=at)
            )
            connection.execute(
                installations.insert().values(
                    tenant_id=ids["tenant"],
                    installation_id=ids["installation"],
                    created_at=at,
                )
            )
            connection.execute(
                episodes.insert().values(
                    tenant_id=ids["tenant"],
                    installation_id=ids["installation"],
                    episode_id=ids["episode"],
                    principal_id=ids["principal"],
                    conversation_id=ids["conversation"],
                    ingress_request_id=ids["ingress"],
                    state="accepted",
                    current_generation=1,
                    supersedes_generation=None,
                    revision=1,
                    last_event_id=ids["initial-event"],
                    state_contract="EpisodeState/v1",
                    state_schema_version=1,
                    state_payload_sha256=bytes.fromhex(canonical_sha256(initial)),
                    created_at=at,
                    updated_at=at,
                )
            )
            connection.execute(
                episode_events.insert().values(
                    tenant_id=ids["tenant"],
                    installation_id=ids["installation"],
                    episode_id=ids["episode"],
                    event_id=ids["initial-event"],
                    command_id=ids["initial-command"],
                    command_sha256=bytes.fromhex(
                        canonical_sha256({"seed": ids["initial-command"]})
                    ),
                    event_kind="initialized",
                    before_revision=0,
                    after_revision=1,
                    before_state=None,
                    after_state="accepted",
                    before_generation=None,
                    after_generation=1,
                    before_episode_state=None,
                    after_episode_state=initial.model_dump(mode="json"),
                    before_state_sha256=None,
                    after_state_sha256=bytes.fromhex(canonical_sha256(initial)),
                    occurred_at=at,
                )
            )
        return Seeded4CEpisode(
            tenant_id=ids["tenant"],
            installation_id=ids["installation"],
            episode_id=ids["episode"],
            initial_state=initial,
            command=command_value,
            expected_result={"revision": 2, "state": after.model_dump(mode="json")},
        )

    return seed
