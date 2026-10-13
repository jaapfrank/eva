from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine, make_url

from eva.adapters.postgres.current_catalog import metadata
from eva.adapters.postgres.delegated_work import (
    DelegatedWorkConflict,
    DelegatedWorkReleaseSelector,
    PostgresDelegatedWorkStore,
)
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.contracts.delegated_work import (
    DelegatedWorkGrant,
    DelegatedWorkResult,
    DelegatedWorkSpec,
    DelegatedWorkUsage,
)
from eva.operations.install_schema import install_schema


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[3]


def _id(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"eva2-private-delegated-test:{label}")


@pytest.fixture(scope="module")
def engine() -> Engine:
    dsn = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.fail("EVA2_TEST_POSTGRES_DSN is required")
    url = make_url(dsn)
    if not url.database or not url.database.startswith("eva2_4b_disposable_"):
        pytest.fail("delegated work requires an explicit disposable database")
    value = sa.create_engine(url)
    with value.connect() as connection:
        version_table_exists = connection.scalar(
            sa.text("SELECT to_regclass('public.eva_alembic_version') IS NOT NULL")
        )
        if version_table_exists:
            assert connection.scalar(
                sa.text("SELECT version_num FROM public.eva_alembic_version")
            ) is None
    receipt = install_schema(dsn, alembic_ini=ROOT / "deploy/alembic.ini")
    assert receipt.final_revision == "4e_0007_delegated_private"
    try:
        yield value
    finally:
        with value.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE eva_core.delegated_work_runs NO FORCE ROW LEVEL SECURITY"
            )
            connection.execute(sa.text("DELETE FROM eva_core.delegated_work_runs"))
            connection.execute(
                sa.text(
                    "DELETE FROM eva_core.principal_bindings "
                    "WHERE tenant_id=:tenant_id"
                ),
                {"tenant_id": _id("tenant")},
            )
            connection.execute(
                sa.text("DELETE FROM eva_core.principals WHERE tenant_id=:tenant_id"),
                {"tenant_id": _id("tenant")},
            )
        config = Config(str(ROOT / "deploy/alembic.ini"))
        config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
        command.downgrade(config, "base")
        value.dispose()


@pytest.fixture(scope="module")
def authority(engine: Engine) -> tuple[TimelineScope, UUID, UUID]:
    tenant = _id("tenant")
    installation = _id("installation")
    principal = _id("principal")
    binding = _id("binding")
    until = NOW + timedelta(days=30)
    principals = metadata.tables["eva_core.principals"]
    bindings = metadata.tables["eva_core.principal_bindings"]
    with engine.begin() as connection:
        for table in (principals, bindings):
            connection.exec_driver_sql(
                f"ALTER TABLE {table.schema}.{table.name} DISABLE TRIGGER ALL"
            )
        try:
            connection.execute(
                principals.insert().values(
                    tenant_id=tenant,
                    principal_id=principal,
                    principal_type="human",
                    status="active",
                    display_name=None,
                    privacy_class="restricted",
                    revision=1,
                    retention_policy_id="retention:test",
                    retention_until=until,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
            connection.execute(
                bindings.insert().values(
                    tenant_id=tenant,
                    principal_binding_id=binding,
                    principal_id=principal,
                    provider_id="telegram",
                    channel_id="private",
                    external_subject_sha256=bytes.fromhex("11" * 32),
                    verification_evidence_id=_id("verification"),
                    status="verified",
                    verified_at=NOW,
                    revision=1,
                    retention_policy_id="retention:test",
                    retention_until=until,
                    created_at=NOW,
                    updated_at=NOW,
                )
            )
        finally:
            for table in (bindings, principals):
                connection.exec_driver_sql(
                    f"ALTER TABLE {table.schema}.{table.name} ENABLE TRIGGER ALL"
                )
    return TimelineScope(tenant, installation), principal, binding


def _spec(label: str, principal: UUID, binding: UUID) -> DelegatedWorkSpec:
    return DelegatedWorkSpec(
        contract="DelegatedWork/v1",
        schema_version=1,
        work_id=f"work:private:{label}",
        episode_id=str(_id(f"conversation-context:{label}")),
        generation=1,
        parent_task_id=f"chat:{label}",
        owner_principal_id=str(principal),
        principal_binding_id=str(binding),
        audience="private",
        worker_name="onderzoeker",
        worker_profile_id="worker:research:v1",
        worker_profile_release_sha256="22" * 32,
        task="Onderzoek dit zonder tools of toegang tot Eva's privédata.",
        model_profile_id="model:codex:v1",
        model_profile_release_sha256="33" * 32,
        worker_program_sha256="44" * 32,
        engine_release_sha256="55" * 32,
        output_schema_sha256="66" * 32,
        grant=DelegatedWorkGrant(
            contract="DelegatedWorkGrant/v1",
            schema_version=1,
            context_refs=[],
            capability_ids=[],
            workspace_ref=None,
            child_network_access=False,
            maximum_input_tokens=1_000,
            maximum_output_tokens=500,
            maximum_total_tokens=1_500,
            maximum_cost_usd_micros=10_000,
            timeout_seconds=30,
            maximum_claims_before_call_start=2,
            delegation_depth=1,
        ).model_dump(mode="json"),
        created_at="2026-08-30T12:00:00Z",
        deadline="2026-08-30T12:10:00Z",
    )


def _usage() -> DelegatedWorkUsage:
    return DelegatedWorkUsage(
        contract="DelegatedWorkUsage/v1",
        schema_version=1,
        measurement="measured",
        completed_model_responses=1,
        tool_calls=0,
        input_tokens=20,
        output_tokens=8,
        total_tokens=28,
        cost_usd_micros=14,
    )


def _admit(store: PostgresDelegatedWorkStore, spec: DelegatedWorkSpec) -> None:
    store.admit(
        spec=spec,
        title="Privéonderzoek",
        summary="Een afgeschermde onderzoeker werkt aan de opdracht.",
        worker_role="Onderzoek",
        retention_policy_id="retention:test",
        retention_until=NOW + timedelta(days=7),
        occurred_at=NOW,
    )


def test_private_context_claim_crash_fence_stop_and_success(
    engine: Engine, authority: tuple[TimelineScope, UUID, UUID]
) -> None:
    scope, principal, binding = authority
    store = PostgresDelegatedWorkStore(engine, scope)
    store.validate_admission_authority(
        owner_principal_id=principal, principal_binding_id=binding
    )

    stopped_spec = _spec("stopped", principal, binding)
    _admit(store, stopped_spec)
    release = DelegatedWorkReleaseSelector.from_spec(stopped_spec)
    first = store.claim_next(worker_id="worker:first", release=release, now=NOW)
    assert first is not None
    assert store.claim_next(worker_id="worker:other", release=release, now=NOW) is None
    reclaimed = store.claim_next(
        worker_id="worker:restart", release=release, now=NOW + timedelta(seconds=31)
    )
    assert reclaimed is not None
    with pytest.raises(DelegatedWorkConflict, match="stale"):
        store.start_call(first, now=NOW + timedelta(seconds=32))
    call = store.start_call(reclaimed, now=NOW + timedelta(seconds=32))
    assert call is not None
    assert PostgresDelegatedWorkStore(engine, scope).claim_next(
        worker_id="worker:new-process",
        release=release,
        now=NOW + timedelta(seconds=40),
    ) is None
    requested = store.request_stop(
        stopped_spec.work_id,
        owner_principal_id=principal,
        principal_binding_id=binding,
        now=NOW + timedelta(seconds=41),
    )
    assert requested.stop_requested_at == NOW + timedelta(seconds=41)
    assert requested.lease_owner is None
    assert store.stop_requested(call)
    stopped = store.record_stopped(
        call, usage=_usage(), now=NOW + timedelta(seconds=42)
    )
    assert stopped.state == "stopped"

    success_spec = _spec("success", principal, binding)
    _admit(store, success_spec)
    success_claim = store.claim_next(
        worker_id="worker:success",
        release=DelegatedWorkReleaseSelector.from_spec(success_spec),
        now=NOW,
    )
    assert success_claim is not None
    success_call = store.start_call(success_claim, now=NOW + timedelta(seconds=1))
    assert success_call is not None
    usage = _usage()
    result = DelegatedWorkResult(
        contract="DelegatedWorkResult/v1",
        schema_version=1,
        work_id=success_spec.work_id,
        call_id=success_call.call_id,
        output="Het afgeschermde onderzoek is afgerond.",
        output_sha256=(
            "a44fb649aca06dff58b6f16d3cd2dae88480b785af2c69879211761e241bf43d"
        ),
        spec_sha256=success_spec.sha256,
        usage=usage.model_dump(mode="json"),
        completed_at="2026-08-30T12:00:02Z",
    )
    recorded = store.record_success(
        success_call, result, now=NOW + timedelta(seconds=2)
    )
    assert recorded.state == "succeeded"
    assert recorded.lease_owner is None
    assert store.record_success(
        success_call, result, now=NOW + timedelta(seconds=2)
    ) == recorded

    with engine.connect() as connection:
        delegated_attention_count = connection.scalar(
            sa.text(
                "SELECT count(*) FROM eva_core.attention_items "
                "WHERE kind='delegated_work'"
            )
        )
    assert delegated_attention_count == 0
