from __future__ import annotations

import ast
import inspect
import runpy
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from tests.support.episode_repository import PostgresEpisodeRepository
from eva.adapters.postgres.schema import episode_events, metadata


EVA2_ROOT = Path(__file__).resolve().parents[3]
SOURCE_ROOT = EVA2_ROOT / "src/eva"


def test_4b_has_exactly_three_linear_alembic_revisions() -> None:
    revision_root = EVA2_ROOT / "deploy/migrations/versions"
    revisions: dict[str, str | None] = {}
    for path in sorted(revision_root.glob("4b_*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        assignments = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in module.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"revision", "down_revision"}
        }
        revisions[assignments["revision"]] = assignments["down_revision"]

    assert revisions == {
        "4b_0000_core_namespace": None,
        "4b_0001_tenant_scope": "4b_0000_core_namespace",
        "4b_0002_episode_atomicity": "4b_0001_tenant_scope",
    }


def test_runtime_source_has_no_ddl_or_migration_entrypoint() -> None:
    forbidden = (
        "create_all(",
        "drop_all(",
        "alembic.command",
        "CREATE TABLE",
        "CREATE SCHEMA",
        "ALTER TABLE",
    )
    offenders: list[tuple[str, str]] = []
    for path in sorted(SOURCE_ROOT.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for marker in forbidden:
            if marker in source:
                offenders.append((path.relative_to(EVA2_ROOT).as_posix(), marker))

    assert offenders == []


def test_repository_requires_an_injected_sqlalchemy_connection() -> None:
    parameters = inspect.signature(PostgresEpisodeRepository).parameters

    assert list(parameters) == ["connection", "tenant_id", "installation_id"]
    assert parameters["connection"].annotation in {Connection, "Connection"}


def test_postgres_adapter_does_not_import_dbos_or_alembic() -> None:
    offenders: list[tuple[str, str]] = []
    for path in sorted((SOURCE_ROOT / "adapters/postgres").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if (
                    name == "dbos"
                    or name.startswith("dbos.")
                    or name == "alembic"
                    or name.startswith("alembic.")
                ):
                    offenders.append((path.relative_to(EVA2_ROOT).as_posix(), name))

    assert offenders == []


def test_repository_contains_no_transaction_control() -> None:
    source = (EVA2_ROOT / "development/tests/support/episode_repository.py").read_text(
        encoding="utf-8"
    )

    assert all(
        marker not in source
        for marker in (".begin(", ".commit(", ".rollback(", ".begin_nested(")
    )


def test_metadata_contains_only_the_four_4b_tables_and_named_constraints() -> None:
    assert set(metadata.tables) == {
        "eva_core.episode_events",
        "eva_core.episodes",
        "eva_core.installations",
        "eva_core.tenants",
    }
    actual = {
        constraint.name
        for table in metadata.tables.values()
        for constraint in table.constraints
    }
    assert None not in actual
    assert actual == {
        "ck_episode_events_after_snapshot",
        "ck_episode_events_before_snapshot",
        "ck_episode_events_generation",
        "ck_episode_events_hashes",
        "ck_episode_events_kind",
        "ck_episode_events_occurred_at_seconds",
        "ck_episode_events_revision_step",
        "ck_episode_events_shape",
        "ck_episode_events_states",
        "ck_episodes_contract",
        "ck_episodes_generation_chain",
        "ck_episodes_revision",
        "ck_episodes_state",
        "ck_episodes_state_hash",
        "ck_episodes_timestamps",
        "ck_installations_created_at_seconds",
        "ck_tenants_created_at_seconds",
        "fk_episode_events_episode",
        "fk_episodes_installation",
        "fk_episodes_last_event",
        "fk_installations_tenant",
        "pk_episode_events",
        "pk_episodes",
        "pk_installations",
        "pk_tenants",
        "uq_episode_events_episode_revision",
        "uq_episode_events_scoped_command",
    }


def test_snapshot_checks_and_circular_fks_match_the_frozen_revision() -> None:
    revision = runpy.run_path(
        str(EVA2_ROOT / "deploy/migrations/versions/4b_0002_episode_atomicity.py")
    )
    snapshot_predicate = revision["episode_state_snapshot_predicate"]
    checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in episode_events.constraints
        if isinstance(constraint, sa.CheckConstraint)
    }

    assert checks["ck_episode_events_after_snapshot"] == snapshot_predicate(
        "after_episode_state",
        state_column="after_state",
        generation_column="after_generation",
        event_column="event_id",
        occurred_at_column="occurred_at",
    )
    assert checks["ck_episode_events_before_snapshot"] == (
        "before_episode_state IS NULL OR ("
        + snapshot_predicate(
            "before_episode_state",
            state_column="before_state",
            generation_column="before_generation",
            event_column=None,
            occurred_at_column=None,
        )
        + ")"
    )

    event_fk = next(
        constraint
        for constraint in episode_events.constraints
        if constraint.name == "fk_episode_events_episode"
    )
    last_event_fk = next(
        constraint
        for constraint in metadata.tables["eva_core.episodes"].constraints
        if constraint.name == "fk_episodes_last_event"
    )
    for constraint in (event_fk, last_event_fk):
        assert constraint.deferrable is True
        assert constraint.initially == "DEFERRED"
        assert constraint.ondelete is None
