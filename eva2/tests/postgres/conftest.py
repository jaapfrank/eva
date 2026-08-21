from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine, make_url

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
DISPOSABLE_DATABASE_PREFIX = "eva2_4b_disposable_"


@dataclass(frozen=True)
class PostgresGate:
    engine: Engine
    alembic_config: Config
    preserved_scope_anchor: bool
    migrated_as_eva_migrator: bool


@dataclass(frozen=True)
class SeededEpisode:
    tenant_id: str
    installation_id: str
    episode_id: str
    initial_state: EpisodeState
    command: EpisodeTransitionCommand


def _alembic_config(dsn: str) -> Config:
    config = Config(str(EVA2_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    return config


def _upgrade_as_eva_migrator(engine: Engine, config: Config, target: str) -> bool:
    """Run a revision with an admin session but Eva's NOLOGIN current role."""

    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        current_role = connection.scalar(
            sa.text(
                "SELECT current_user = 'eva_migrator' AND session_user <> current_user"
            )
        )
        config.attributes["connection"] = connection
        try:
            command.upgrade(config, target)
        finally:
            config.attributes.pop("connection", None)
    return bool(current_role)


@pytest.fixture(scope="session")
def postgres_gate() -> Iterator[PostgresGate]:
    dsn = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.fail(
            "EVA2_TEST_POSTGRES_DSN is required; 4B PostgreSQL evidence must not skip"
        )
    url = make_url(dsn)
    if url.get_backend_name() != "postgresql":
        pytest.fail("EVA2_TEST_POSTGRES_DSN must select PostgreSQL")
    if not url.database or not url.database.startswith(DISPOSABLE_DATABASE_PREFIX):
        pytest.fail("the explicit database name must start with eva2_4b_disposable_")

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
                "SELECT rolsuper, rolcreaterole FROM pg_roles WHERE rolname = current_user"
            )
        ).one()
        if not role.rolsuper or not role.rolcreaterole:
            pytest.fail(
                "the disposable 4B migration gate requires an administrative test role"
            )
        user_schemas = set(
            connection.scalars(
                sa.text(
                    "SELECT nspname FROM pg_namespace "
                    "WHERE nspname !~ '^pg_' "
                    "AND nspname NOT IN ('information_schema', 'public')"
                )
            )
        )
        user_relations = set(
            connection.scalars(
                sa.text(
                    "SELECT c.relname FROM pg_class c "
                    "JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE n.nspname = 'public' "
                    "AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f')"
                )
            )
        )
        if user_schemas or user_relations:
            pytest.fail(
                "EVA2_TEST_POSTGRES_DSN must point at an empty disposable database; "
                f"schemas={sorted(user_schemas)}, relations={sorted(user_relations)}"
            )

    preserved_scope_anchor = False
    migrated_as_eva_migrator = False
    try:
        command.upgrade(config, "4b_0000_core_namespace")
        migrated_as_eva_migrator = _upgrade_as_eva_migrator(
            engine, config, "4b_0001_tenant_scope"
        )
        anchor_tenant = uuid5(NAMESPACE_URL, "eva2-4b-upgrade-tenant")
        anchor_installation = uuid5(NAMESPACE_URL, "eva2-4b-upgrade-installation")
        anchor_time = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        with engine.begin() as connection:
            connection.execute(
                tenants.insert().values(tenant_id=anchor_tenant, created_at=anchor_time)
            )
            connection.execute(
                installations.insert().values(
                    tenant_id=anchor_tenant,
                    installation_id=anchor_installation,
                    created_at=anchor_time,
                )
            )
        migrated_as_eva_migrator = (
            _upgrade_as_eva_migrator(engine, config, "head")
            and migrated_as_eva_migrator
        )
        with engine.connect() as connection:
            preserved_scope_anchor = (
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(installations)
                    .where(
                        installations.c.tenant_id == anchor_tenant,
                        installations.c.installation_id == anchor_installation,
                    )
                )
                == 1
            )
        command.downgrade(config, "base")
        with engine.connect() as connection:
            if connection.scalar(
                sa.text("SELECT to_regnamespace('eva_core') IS NOT NULL")
            ):
                pytest.fail("pre-traffic downgrade left eva_core behind")
        command.upgrade(config, "4b_0000_core_namespace")
        migrated_as_eva_migrator = (
            _upgrade_as_eva_migrator(engine, config, "head")
            and migrated_as_eva_migrator
        )
        yield PostgresGate(
            engine=engine,
            alembic_config=config,
            preserved_scope_anchor=preserved_scope_anchor,
            migrated_as_eva_migrator=migrated_as_eva_migrator,
        )
    finally:
        try:
            command.downgrade(config, "base")
        finally:
            engine.dispose()


@pytest.fixture(scope="session")
def postgres_engine(postgres_gate: PostgresGate) -> Engine:
    return postgres_gate.engine


@pytest.fixture
def seed_episode(
    postgres_engine: Engine,
) -> Callable[[str], SeededEpisode]:
    def seed(label: str) -> SeededEpisode:
        def identifier(name: str) -> str:
            return str(uuid5(NAMESPACE_URL, f"eva2-4b-{label}-{name}"))

        tenant_id = identifier("tenant")
        installation_id = identifier("installation")
        episode_id = identifier("episode")
        principal_id = identifier("principal")
        conversation_id = identifier("conversation")
        ingress_request_id = identifier("ingress")
        initial_event_id = identifier("initial-event")
        initial_command_id = identifier("initial-command")
        transition_event_id = identifier("transition-event")
        transition_command_id = identifier("transition-command")
        state = EpisodeState.model_validate(
            {
                "schema_version": 1,
                "contract": "EpisodeState/v1",
                "episode_id": episode_id,
                "tenant_id": tenant_id,
                "principal_id": principal_id,
                "conversation_id": conversation_id,
                "ingress_request_id": ingress_request_id,
                "state": "accepted",
                "current_generation": 1,
                "supersedes_generation": None,
                "last_event_id": initial_event_id,
                "updated_at": "2026-08-20T12:00:00Z",
            }
        )
        state_hash = canonical_sha256(state)
        at = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        with postgres_engine.begin() as connection:
            connection.execute(
                tenants.insert().values(tenant_id=tenant_id, created_at=at)
            )
            connection.execute(
                installations.insert().values(
                    tenant_id=tenant_id,
                    installation_id=installation_id,
                    created_at=at,
                )
            )
            connection.execute(
                episodes.insert().values(
                    tenant_id=tenant_id,
                    installation_id=installation_id,
                    episode_id=episode_id,
                    principal_id=principal_id,
                    conversation_id=conversation_id,
                    ingress_request_id=ingress_request_id,
                    state="accepted",
                    current_generation=1,
                    supersedes_generation=None,
                    revision=1,
                    last_event_id=initial_event_id,
                    state_contract="EpisodeState/v1",
                    state_schema_version=1,
                    state_payload_sha256=bytes.fromhex(state_hash),
                    created_at=at,
                    updated_at=at,
                )
            )
            connection.execute(
                episode_events.insert().values(
                    tenant_id=tenant_id,
                    installation_id=installation_id,
                    episode_id=episode_id,
                    event_id=initial_event_id,
                    command_id=initial_command_id,
                    command_sha256=bytes.fromhex(
                        canonical_sha256({"seed": initial_command_id})
                    ),
                    event_kind="initialized",
                    before_revision=0,
                    after_revision=1,
                    before_state=None,
                    after_state="accepted",
                    before_generation=None,
                    after_generation=1,
                    before_episode_state=None,
                    after_episode_state=state.model_dump(mode="json"),
                    before_state_sha256=None,
                    after_state_sha256=bytes.fromhex(state_hash),
                    occurred_at=at,
                )
            )
        return SeededEpisode(
            tenant_id=tenant_id,
            installation_id=installation_id,
            episode_id=episode_id,
            initial_state=state,
            command=EpisodeTransitionCommand(
                tenant_id=tenant_id,
                installation_id=installation_id,
                episode_id=episode_id,
                command_id=transition_command_id,
                event_id=transition_event_id,
                expected_revision=1,
                expected_state_sha256=state_hash,
                occurred_at="2026-08-20T12:00:01Z",
            ),
        )

    return seed
