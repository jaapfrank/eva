from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
from threading import Event
from typing import Any, Iterator
from uuid import UUID, uuid5

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine, make_url

from eva.adapters.postgres.action_chain_ledger import (
    ActionChainConflict,
    ActionChainCorrupt,
    ActionChainNotFound,
    PostgresActionChainLedger,
    attention_items,
    plan_steps,
    plans,
)
from eva.adapters.postgres.action_chain_leases import (
    ActionChainLeaseConflict,
    PostgresActionChainLeases,
)
from eva.adapters.postgres.action_chain_reads import (
    ActionChainReadConflict,
    PostgresActionChainReadPreparer,
    ReadStepAuthority,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.catalog_generated import CATALOG_SPEC
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    operational_checkpoints,
    operational_events,
)
from eva.adapters.postgres.scope import set_local_scope
from eva.application.action_chain import (
    ActionToolCatalog,
    ActionToolDescriptor,
    action_chain_step_operation_id,
    action_chain_thread_id,
)
from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.execution import CapabilityManifest, ConnectorBinding, ResourceScope
from eva.contracts.planning import ModelDecision, ValidatedPlan


EVA2_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_PATH = (
    EVA2_ROOT / "design/vertical/noor-4d-concurrency-crash-schedules-v1.json"
)
SCHEDULE_SHA256 = "f492d746779ec5276478893ff22720c84926912371d19175dd7508a18734b91d"
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
RETENTION_UNTIL = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
H = "a" * 64
POLICY_SHA256 = "858d19fa815fb15b6f78cdd755081264dbfeea9af01f4e488ece73fe0d381bcb"

_TABLE_SPECS = {spec["table"]: spec for spec in CATALOG_SPEC["canonical_tables"]}
_BASE_FACTS = (
    "static/tenant",
    "static/installation",
    "static/identity-pack",
    "static/policy-pack",
    "static/principal-owner",
    "evidence/evidence:authenticated-http:001",
    "evidence/evidence:direct-user-request:001",
    "static/principal-binding",
    "admitted/conversation",
    "admitted/message-input",
    "admitted/ingress",
    "episode/accepted",
    "admitted/generation-current",
    "episode-event/initialized",
    "commitment/open",
    "attention/pending",
)


def _schedule_facts() -> dict[str, dict[str, Any]]:
    raw = SCHEDULE_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == SCHEDULE_SHA256
    schedule = json.loads(raw)
    facts = schedule["portableSnapshotAuthority"]["fixtureFactCatalog"]["facts"]
    return {fact["fact_id"]: fact for fact in facts}


def _column_declarations(table: str) -> dict[str, str]:
    return {
        declaration.split(" ", 1)[0]: declaration.split(" ", 2)[1]
        for declaration in _TABLE_SPECS[table]["columns"]
    }


def _native_value(value: Any, declaration: str) -> Any:
    if value is None:
        return None
    if declaration == "uuid":
        return UUID(value)
    if declaration == "uuid[]":
        return [UUID(item) for item in value]
    if declaration == "bytea":
        return bytes.fromhex(value)
    if declaration in {"timestamptz", "timestamptz(0)"}:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    return value


def _fact_values(fact_id: str) -> tuple[sa.Table, dict[str, Any]]:
    fact = _schedule_facts()[fact_id]
    table_name = fact["table"]
    table = metadata.tables[table_name]
    declarations = _column_declarations(table_name)
    row = fact["portable_record"]["row"]
    values = {
        name: _native_value(value, declarations[name])
        for name, value in row.items()
        if name in table.c
    }
    assert set(values) == set(table.c.keys())
    return table, values


def _scope_and_episode() -> tuple[TimelineScope, UUID]:
    facts = _schedule_facts()
    tenant = facts["static/tenant"]["portable_record"]["row"]["tenant_id"]
    installation = facts["static/installation"]["portable_record"]["row"]
    episode = facts["episode/accepted"]["portable_record"]["row"]["episode_id"]
    return (
        TimelineScope(UUID(tenant), UUID(installation["installation_id"])),
        UUID(episode),
    )


def _upgrade(engine: Engine, config: Config) -> None:
    command.upgrade(config, "4b_0000_core_namespace")
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        config.attributes["connection"] = connection
        try:
            command.upgrade(config, "4b_0002_episode_atomicity")
        finally:
            config.attributes.pop("connection", None)
    command.upgrade(config, "head")


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    dsn = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        pytest.fail("EVA2_TEST_POSTGRES_DSN is required")
    url = make_url(dsn)
    if (
        url.get_backend_name() != "postgresql"
        or not url.database
        or not url.database.startswith("eva2_action_chain_disposable_")
    ):
        pytest.fail("action-chain gate requires an explicit disposable PostgreSQL DB")
    engine = sa.create_engine(url, pool_pre_ping=False)
    config = Config(str(EVA2_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
    try:
        with engine.connect() as connection:
            assert int(connection.scalar(sa.text("SHOW server_version_num"))) == 160013
            assert (
                connection.scalar(
                    sa.text(
                        "SELECT count(*) FROM pg_namespace WHERE nspname LIKE 'eva_%'"
                    )
                )
                == 0
            )
        _upgrade(engine, config)
        _seed_fixture(engine)
        yield engine
    finally:
        try:
            command.downgrade(config, "base")
        finally:
            engine.dispose()


def _seed_fixture(engine: Engine) -> None:
    scope, _ = _scope_and_episode()
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        for fact_id in _BASE_FACTS:
            table, values = _fact_values(fact_id)
            connection.execute(sa.insert(table).values(**values))
        _seed_read_authority(connection, scope)


def _read_authority(scope: TimelineScope) -> ReadStepAuthority:
    resource_scope = ResourceScope(
        resource_type="home-assistant-entity",
        resource_id="overkapping",
        scope_sha256=canonical_sha256(
            {
                "resource_type": "home-assistant-entity",
                "resource_id": "overkapping",
            }
        ),
    )
    return ReadStepAuthority(
        capability_activation_id=uuid5(scope.installation_id, "mc03a:activation"),
        connector_binding_id=uuid5(scope.installation_id, "mc03a:binding"),
        resource_scope=resource_scope,
    )


def _seed_read_authority(
    connection: sa.Connection, scope: TimelineScope
) -> ReadStepAuthority:
    authority = _read_authority(scope)
    principal = _fact_values("static/principal-owner")[1]
    evidence = _fact_values("evidence/evidence:authenticated-http:001")[1]
    policy = _fact_values("static/policy-pack")[1]
    binding = ConnectorBinding(
        schema_version=1,
        contract="ConnectorBinding/v1",
        binding_id=str(authority.connector_binding_id),
        installation_id=str(scope.installation_id),
        tenant_id=str(scope.tenant_id),
        owner_principal_id=str(principal["principal_id"]),
        provider_id="home-assistant",
        provider_account_id="fixture-home-assistant",
        provider_resource_id="fixture-home",
        confirmed_operations=["ha.state.read"],
        confirmed_scopes=[authority.resource_scope.resource_type, "home_read"],
        credential_reference="secret-descriptor:mc03a:home-assistant",
        verification_evidence_ref=str(evidence["evidence_id"]),
        verified_at="2026-08-20T12:00:00Z",
        status="verified",
    )
    bindings = metadata.tables["eva_core.connector_bindings"]
    connection.execute(
        sa.insert(bindings).values(
            tenant_id=scope.tenant_id,
            installation_id=scope.installation_id,
            binding_id=authority.connector_binding_id,
            owner_principal_id=principal["principal_id"],
            provider_id=binding.provider_id,
            provider_account_id=binding.provider_account_id,
            provider_resource_id=binding.provider_resource_id,
            confirmed_operations=binding.confirmed_operations,
            confirmed_scopes=binding.confirmed_scopes,
            credential_reference=binding.credential_reference,
            verification_evidence_id=evidence["evidence_id"],
            verified_at=NOW,
            status="verified",
            payload=binding.model_dump(mode="json"),
            payload_sha256=bytes.fromhex(canonical_sha256(binding)),
            revision=1,
            retention_policy_id="retention:noor-document:30d",
            retention_until=RETENTION_UNTIL,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    manifest_id = uuid5(scope.installation_id, "mc03a:manifest")
    manifest = CapabilityManifest(
        schema_version=1,
        contract="CapabilityManifest/v1",
        capability_id="capability:ha-state-read:test",
        connector_id=binding.provider_id,
        operation="ha.state.read",
        input_schema_sha256=H,
        output_schema_sha256="b" * 64,
        effect_class="read",
        required_provider_scopes=["home_read"],
        proof_floor="fresh provider observation",
        maximum_freshness_ms=300_000,
        executable_sha256="6" * 64,
        sandbox_profile_sha256="7" * 64,
    )
    manifests = metadata.tables["eva_core.capability_manifests"]
    connection.execute(
        sa.insert(manifests).values(
            tenant_id=scope.tenant_id,
            installation_id=scope.installation_id,
            capability_manifest_id=manifest_id,
            capability_id=manifest.capability_id,
            connector_id=manifest.connector_id,
            operation=manifest.operation,
            input_schema_sha256=bytes.fromhex(manifest.input_schema_sha256),
            output_schema_sha256=bytes.fromhex(manifest.output_schema_sha256),
            effect_class=manifest.effect_class,
            required_provider_scopes=manifest.required_provider_scopes,
            proof_floor=manifest.proof_floor,
            maximum_freshness_ms=manifest.maximum_freshness_ms,
            executable_sha256=bytes.fromhex(manifest.executable_sha256),
            sandbox_profile_sha256=bytes.fromhex(manifest.sandbox_profile_sha256),
            release_signature_sha256=bytes.fromhex("8" * 64),
            state="active",
            payload=manifest.model_dump(mode="json"),
            payload_sha256=bytes.fromhex(canonical_sha256(manifest)),
            retention_policy_id="retention:noor-document:30d",
            retention_until=RETENTION_UNTIL,
            created_at=NOW,
        )
    )
    activations = metadata.tables["eva_core.capability_activations"]
    scope_payload = authority.resource_scope.model_dump(mode="json")
    connection.execute(
        sa.insert(activations).values(
            tenant_id=scope.tenant_id,
            installation_id=scope.installation_id,
            activation_id=authority.capability_activation_id,
            capability_manifest_id=manifest_id,
            connector_binding_id=authority.connector_binding_id,
            capability_id=manifest.capability_id,
            operation=manifest.operation,
            scope=scope_payload,
            scope_sha256=bytes.fromhex(authority.resource_scope.scope_sha256),
            policy_id=policy["policy_id"],
            policy_pack_version=policy["policy_version"],
            policy_pack_sha256=bytes.fromhex(POLICY_SHA256),
            executable_sha256=bytes.fromhex(manifest.executable_sha256),
            sandbox_profile_sha256=bytes.fromhex(manifest.sandbox_profile_sha256),
            state="active",
            revision=1,
            retention_policy_id="retention:noor-document:30d",
            retention_until=RETENTION_UNTIL,
            activated_at=NOW,
            updated_at=NOW,
        )
    )
    return authority


def _decision_and_plan() -> tuple[ModelDecision, ValidatedPlan, ActionToolCatalog]:
    decision = ModelDecision(
        schema_version=1,
        contract="ModelDecision/v1",
        episode_id="fixture:episode:noor:001",
        generation=1,
        purpose="plan",
        decision="propose_effect",
        actions=[
            {
                "action_id": "step:state",
                "kind": "request_read",
                "operation": "ha.state.read",
                "resource": "overkapping",
                "arguments": {"target": "overkapping"},
                "depends_on": [],
                "uncertainty_basis_points": 100,
            },
            {
                "action_id": "step:test-effect",
                "kind": "propose_effect",
                "operation": "ha.test_target.set",
                "resource": "input_boolean.eva_test",
                "arguments": {
                    "state": "on",
                    "target": "input_boolean.eva_test",
                },
                "depends_on": ["step:state"],
                "uncertainty_basis_points": 200,
            },
        ],
        clarification_question=None,
        wait_reason=None,
        uncertainty_basis_points=200,
    )

    def validate_read(resource: str, arguments: dict[str, JsonValue]) -> None:
        assert resource == "overkapping"
        assert arguments == {"target": "overkapping"}

    def validate_effect(resource: str, arguments: dict[str, JsonValue]) -> None:
        assert resource == "input_boolean.eva_test"
        assert arguments == {
            "state": "on",
            "target": "input_boolean.eva_test",
        }

    catalog = ActionToolCatalog(
        (
            ActionToolDescriptor(
                operation="ha.state.read",
                effect_class="read",
                permission_class="home_read",
                input_schema_sha256=H,
                output_schema_sha256="b" * 64,
                validator_sha256="c" * 64,
                policy_scope_sha256=POLICY_SHA256,
                proof_floor="fresh provider observation",
                enabled=True,
                validate_request=validate_read,
            ),
            ActionToolDescriptor(
                operation="ha.test_target.set",
                effect_class="effect",
                permission_class="home_effect",
                input_schema_sha256="e" * 64,
                output_schema_sha256="f" * 64,
                validator_sha256="1" * 64,
                policy_scope_sha256=POLICY_SHA256,
                proof_floor="provider receipt plus independent readback",
                enabled=True,
                validate_request=validate_effect,
            ),
        )
    )
    plan = ValidatedPlan(
        schema_version=1,
        contract="ValidatedPlan/v1",
        episode_id=decision.episode_id,
        generation=decision.generation,
        model_decision_sha256=canonical_sha256(decision),
        policy_sha256=POLICY_SHA256,
        steps=[
            {
                "step_id": "step:state",
                "kind": "read",
                "operation": "ha.state.read",
                "resource": "overkapping",
                "input_schema_sha256": H,
                "output_schema_sha256": "b" * 64,
                "depends_on": [],
                "capability_manifest_id": catalog.plan_capability_manifest_id(
                    "ha.state.read"
                ),
                "proof_floor": "fresh provider observation",
            },
            {
                "step_id": "step:test-effect",
                "kind": "effect",
                "operation": "ha.test_target.set",
                "resource": "input_boolean.eva_test",
                "input_schema_sha256": "e" * 64,
                "output_schema_sha256": "f" * 64,
                "depends_on": ["step:state"],
                "capability_manifest_id": catalog.plan_capability_manifest_id(
                    "ha.test_target.set"
                ),
                "proof_floor": "provider receipt plus independent readback",
            },
        ],
        maximum_connector_calls=2,
        maximum_effect_attempts=2,
        maximum_visible_deliveries=0,
        deadline="2026-08-20T12:02:00Z",
    )
    return decision, plan, catalog


def _seed_model_attempt(
    engine: Engine, scope: TimelineScope, decision: ModelDecision
) -> UUID:
    table, values = _fact_values("model-attempt/plan-responded")
    attempt_id = uuid5(scope.installation_id, "mc02a:model-attempt:1")
    decision_sha256 = canonical_sha256(decision)
    payload = deepcopy(values["payload"])
    payload.update(
        {
            "attempt_id": "model-attempt:mc02a:plan:1",
            "episode_id": decision.episode_id,
            "response_sha256": decision_sha256,
        }
    )
    values.update(
        {
            "attempt_id": attempt_id,
            "response_sha256": bytes.fromhex(decision_sha256),
            "payload": payload,
            "payload_sha256": bytes.fromhex(canonical_sha256(payload)),
            "provider_reference": "fixture-provider-reference:mc02a:plan:1",
        }
    )
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        connection.execute(sa.insert(table).values(**values))
    return attempt_id


def _install_attention_failure(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_core.reject_mc02a_attention()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                IF NEW.kind = 'continuation' THEN
                    RAISE EXCEPTION 'forced_mc02a_attention_failure'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc02a_attention
            BEFORE INSERT ON eva_core.attention_items
            FOR EACH ROW EXECUTE FUNCTION eva_core.reject_mc02a_attention()
            """
        )


def _remove_attention_failure(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            "DROP TRIGGER reject_mc02a_attention ON eva_core.attention_items"
        )
        connection.exec_driver_sql("DROP FUNCTION eva_core.reject_mc02a_attention()")


def _install_read_step_failure(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_core.reject_mc03a_running_step()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                IF NEW.state = 'running' THEN
                    RAISE EXCEPTION 'forced_mc03a_step_failure'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_mc03a_running_step
            BEFORE UPDATE ON eva_core.plan_steps
            FOR EACH ROW EXECUTE FUNCTION eva_core.reject_mc03a_running_step()
            """
        )


def _remove_read_step_failure(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            "DROP TRIGGER reject_mc03a_running_step ON eva_core.plan_steps"
        )
        connection.exec_driver_sql(
            "DROP FUNCTION eva_core.reject_mc03a_running_step()"
        )


def test_action_chain_admission_is_atomic_restartable_scoped_and_effect_free(
    action_chain_engine: Engine,
) -> None:
    scope, storage_episode_id = _scope_and_episode()
    decision, plan, catalog = _decision_and_plan()
    model_attempt_id = _seed_model_attempt(action_chain_engine, scope, decision)
    ledger = PostgresActionChainLedger(action_chain_engine, scope)
    arguments = {
        "storage_episode_id": storage_episode_id,
        "model_attempt_id": model_attempt_id,
        "decision": decision,
        "plan": plan,
        "catalog": catalog,
        "retention_policy_id": "retention:noor-document:30d",
        "retention_until": RETENTION_UNTIL,
        "occurred_at": NOW,
    }

    altered_decision_payload = decision.model_dump(mode="json")
    altered_decision_payload["actions"][0]["arguments"] = {
        "target": "verkeerde-kamer"
    }
    altered_decision = ModelDecision.model_validate(altered_decision_payload)
    altered_plan = plan.model_copy(
        update={"model_decision_sha256": canonical_sha256(altered_decision)}
    )
    with pytest.raises(ActionChainConflict, match="resource scope"):
        ledger.admit(
            **{
                **arguments,
                "decision": altered_decision,
                "plan": altered_plan,
            }
        )
    with pytest.raises(ActionChainConflict, match="metadata"):
        ledger.admit(
            **{
                **arguments,
                "plan": plan.model_copy(update={"policy_sha256": "9" * 64}),
            }
        )

    other_scope = TimelineScope(
        uuid5(scope.tenant_id, "other-tenant"),
        uuid5(scope.installation_id, "other-installation"),
    )
    with pytest.raises(ActionChainConflict, match="does not exist"):
        PostgresActionChainLedger(action_chain_engine, other_scope).admit(**arguments)

    _install_attention_failure(action_chain_engine)
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_mc02a_attention_failure"):
            ledger.admit(**arguments)
    finally:
        _remove_attention_failure(action_chain_engine)

    with action_chain_engine.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(plans)) == 0
        assert (
            connection.scalar(sa.select(sa.func.count()).select_from(plan_steps)) == 0
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(attention_items)
                .where(attention_items.c.kind == "continuation")
            )
            == 0
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(operational_events)
                .where(operational_events.c.event_type.like("ACTION_CHAIN%"))
            )
            == 0
        )
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(operational_checkpoints)
                .where(operational_checkpoints.c.thread_id.like("action-chain%"))
            )
            == 0
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        concurrent = tuple(pool.map(lambda _: ledger.admit(**arguments), range(2)))
    assert sorted(item.disposition for item in concurrent) == [
        "already_present",
        "inserted",
    ]
    admitted = next(item for item in concurrent if item.disposition == "inserted")
    assert [step.state for step in admitted.chain.steps] == ["ready", "pending"]
    assert admitted.chain.decision == decision
    assert admitted.chain.plan == plan
    assert admitted.chain.attention.state == "pending"

    action_chain_engine.dispose()
    restarted_engine = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        restarted = PostgresActionChainLedger(restarted_engine, scope)
        loaded = restarted.load(admitted.chain.plan_id)
        assert loaded == admitted.chain
        replay = restarted.admit(**arguments)
        assert replay.disposition == "already_present"
        assert replay.chain == loaded

        with pytest.raises(ActionChainNotFound):
            PostgresActionChainLedger(restarted_engine, other_scope).load(
                admitted.chain.plan_id
            )

        effects = metadata.tables["eva_effects.effect_intents"]
        reads = metadata.tables["eva_effects.read_intents"]
        with restarted_engine.connect() as connection:
            assert connection.scalar(sa.select(sa.func.count()).select_from(plans)) == 1
            assert (
                connection.scalar(sa.select(sa.func.count()).select_from(plan_steps))
                == 2
            )
            assert (
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(attention_items)
                    .where(attention_items.c.kind == "continuation")
                )
                == 1
            )
            assert (
                connection.scalar(
                    sa.select(sa.func.count())
                    .select_from(operational_events)
                    .where(operational_events.c.event_type.like("ACTION_CHAIN%"))
                )
                == 3
            )
            assert connection.scalar(sa.select(sa.func.count()).select_from(effects)) == 0
            assert connection.scalar(sa.select(sa.func.count()).select_from(reads)) == 0

        with restarted_engine.connect() as connection:
            plan_before = connection.execute(
                sa.select(plans.c.state, plans.c.revision, plans.c.updated_at).where(
                    plans.c.plan_id == loaded.plan_id
                )
            ).one()
            steps_before = tuple(
                connection.execute(
                    sa.select(
                        plan_steps.c.plan_step_id,
                        plan_steps.c.state,
                        plan_steps.c.revision,
                        plan_steps.c.updated_at,
                    )
                    .where(plan_steps.c.plan_id == loaded.plan_id)
                    .order_by(plan_steps.c.logical_step_key)
                ).all()
            )

        leases = PostgresActionChainLeases(restarted_engine, scope)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = tuple(
                pool.map(
                    lambda worker: leases.claim_next(
                        worker_id=worker,
                        now=NOW + timedelta(seconds=1),
                        lease_seconds=10,
                    ),
                    ("worker:mc02b:a", "worker:mc02b:b"),
                )
            )
        assert sum(claim is not None for claim in claims) == 1
        first_claim = next(claim for claim in claims if claim is not None)
        assert first_claim.logical_step_key == "step:state"
        assert first_claim.plan_step_id == loaded.steps[0].plan_step_id

        with pytest.raises(ActionChainLeaseConflict, match="stale or expired"):
            leases.release(
                replace(first_claim, worker_id="worker:mc02b:intruder"),
                disposition="pending",
                now=NOW + timedelta(seconds=2),
            )
        with pytest.raises(ActionChainLeaseConflict, match="does not exist"):
            PostgresActionChainLeases(restarted_engine, other_scope).release(
                first_claim,
                disposition="pending",
                now=NOW + timedelta(seconds=2),
            )

        leases.release(
            first_claim,
            disposition="pending",
            now=NOW + timedelta(seconds=2),
            not_before=NOW + timedelta(seconds=20),
        )
        with pytest.raises(ActionChainLeaseConflict, match="stale or expired"):
            leases.release(
                first_claim,
                disposition="pending",
                now=NOW + timedelta(seconds=3),
            )
        assert (
            leases.claim_next(
                worker_id="worker:mc02b:future",
                now=NOW + timedelta(seconds=10),
                lease_seconds=10,
            )
            is None
        )

        expiring_claim = leases.claim_next(
            worker_id="worker:mc02b:expiring",
            now=NOW + timedelta(seconds=20),
            lease_seconds=10,
        )
        assert expiring_claim is not None
        restarted_engine.dispose()
        leases = PostgresActionChainLeases(restarted_engine, scope)
        reclaimed = leases.claim_next(
            worker_id="worker:mc02b:reclaimer",
            now=NOW + timedelta(seconds=31),
            lease_seconds=10,
        )
        assert reclaimed is not None
        assert reclaimed.attention_revision > expiring_claim.attention_revision
        with pytest.raises(ActionChainLeaseConflict, match="stale or expired"):
            leases.release(
                expiring_claim,
                disposition="pending",
                now=NOW + timedelta(seconds=32),
            )

        leases.release(
            reclaimed,
            disposition="pending",
            error_text="same bounded worker failure",
            now=NOW + timedelta(seconds=32),
        )
        retry_two = leases.claim_next(
            worker_id="worker:mc02b:retry:2",
            now=NOW + timedelta(seconds=33),
            lease_seconds=10,
        )
        assert retry_two is not None
        leases.release(
            retry_two,
            disposition="pending",
            error_text="same bounded worker failure",
            now=NOW + timedelta(seconds=34),
        )
        retry_three = leases.claim_next(
            worker_id="worker:mc02b:retry:3",
            now=NOW + timedelta(seconds=35),
            lease_seconds=10,
        )
        assert retry_three is not None
        leases.release(
            retry_three,
            disposition="pending",
            error_text="same bounded worker failure",
            now=NOW + timedelta(seconds=36),
        )
        assert (
            leases.claim_next(
                worker_id="worker:mc02b:after-help",
                now=NOW + timedelta(seconds=37),
            )
            is None
        )

        leased_chain = restarted.load(loaded.plan_id)
        assert leased_chain.attention.state == "waiting"
        child_operation_id = action_chain_step_operation_id(
            leased_chain.plan, "step:state"
        )
        with restarted_engine.connect() as connection:
            child_checkpoint = connection.execute(
                sa.select(operational_checkpoints).where(
                    operational_checkpoints.c.thread_id == child_operation_id
                )
            ).one()
            assert child_checkpoint.retry_count == 3
            assert child_checkpoint.status == "needs_help"
            assert (
                connection.execute(
                    sa.select(plans.c.state, plans.c.revision, plans.c.updated_at).where(
                        plans.c.plan_id == loaded.plan_id
                    )
                ).one()
                == plan_before
            )
            assert tuple(
                connection.execute(
                    sa.select(
                        plan_steps.c.plan_step_id,
                        plan_steps.c.state,
                        plan_steps.c.revision,
                        plan_steps.c.updated_at,
                    )
                    .where(plan_steps.c.plan_id == loaded.plan_id)
                    .order_by(plan_steps.c.logical_step_key)
                ).all()
            ) == steps_before
            assert connection.scalar(sa.select(sa.func.count()).select_from(effects)) == 0
            assert connection.scalar(sa.select(sa.func.count()).select_from(reads)) == 0

        with restarted_engine.begin() as connection:
            connection.execute(
                sa.update(plans)
                .where(plans.c.plan_id == loaded.plan_id)
                .values(state="validated")
            )
        with pytest.raises(ActionChainCorrupt, match="stored plan columns"):
            restarted.load(loaded.plan_id)
        with restarted_engine.begin() as connection:
            connection.execute(
                sa.update(plans)
                .where(plans.c.plan_id == loaded.plan_id)
                .values(state="current")
            )

        first_step_id = loaded.steps[0].plan_step_id
        with restarted_engine.begin() as connection:
            connection.execute(
                sa.update(plan_steps)
                .where(plan_steps.c.plan_step_id == first_step_id)
                .values(state="succeeded")
            )
        with pytest.raises(ActionChainCorrupt, match="plan-step binding"):
            restarted.load(loaded.plan_id)
        with restarted_engine.begin() as connection:
            connection.execute(
                sa.update(plan_steps)
                .where(plan_steps.c.plan_step_id == first_step_id)
                .values(state="ready")
            )

        attention_id = UUID(loaded.attention.attention_id)
        with restarted_engine.begin() as connection:
            connection.execute(
                sa.update(attention_items)
                .where(attention_items.c.attention_id == attention_id)
                .values(reason="tampered without payload or timeline")
            )
        with pytest.raises(ActionChainCorrupt, match="attention binding"):
            restarted.load(loaded.plan_id)
        with restarted_engine.begin() as connection:
            connection.execute(
                sa.update(attention_items)
                .where(attention_items.c.attention_id == attention_id)
                .values(reason=leased_chain.attention.reason)
            )

        root_thread_id = action_chain_thread_id(loaded.plan)
        with restarted_engine.begin() as connection:
            connection.execute(
                sa.update(operational_checkpoints)
                .where(operational_checkpoints.c.thread_id == root_thread_id)
                .values(status="failed")
            )
        with pytest.raises(ActionChainCorrupt, match="timeline"):
            restarted.load(loaded.plan_id)
    finally:
        restarted_engine.dispose()


def test_action_chain_read_preparation_is_atomic_idempotent_and_undispatched(
    action_chain_engine: Engine,
) -> None:
    scope, storage_episode_id = _scope_and_episode()
    decision, plan, catalog = _decision_and_plan()
    model_attempt_id = _seed_model_attempt(action_chain_engine, scope, decision)
    ledger = PostgresActionChainLedger(action_chain_engine, scope)
    admitted = ledger.admit(
        storage_episode_id=storage_episode_id,
        model_attempt_id=model_attempt_id,
        decision=decision,
        plan=plan,
        catalog=catalog,
        retention_policy_id="retention:noor-document:30d",
        retention_until=RETENTION_UNTIL,
        occurred_at=NOW,
    ).chain
    leases = PostgresActionChainLeases(action_chain_engine, scope)
    claim = leases.claim_next(
        worker_id="worker:mc03a:read",
        now=NOW + timedelta(seconds=1),
        lease_seconds=10,
    )
    assert claim is not None
    authority = _read_authority(scope)
    preparer = PostgresActionChainReadPreparer(action_chain_engine, scope)

    drifted_catalog = ActionToolCatalog(
        (
            replace(catalog.require("ha.state.read"), enabled=False),
            catalog.require("ha.test_target.set"),
        )
    )
    with pytest.raises(ActionChainReadConflict, match="disabled tool"):
        preparer.prepare_read(
            claim,
            live_catalog=drifted_catalog,
            authority=authority,
            now=NOW + timedelta(seconds=2),
        )
    with pytest.raises(ActionChainReadConflict, match="activation is absent"):
        preparer.prepare_read(
            claim,
            live_catalog=catalog,
            authority=replace(
                authority,
                capability_activation_id=uuid5(
                    scope.installation_id, "mc03a:wrong-activation"
                ),
            ),
            now=NOW + timedelta(seconds=2),
        )
    invalid_scope = authority.resource_scope.model_copy(
        update={"scope_sha256": "0" * 64}
    )
    with pytest.raises(ActionChainReadConflict, match="resource scope"):
        preparer.prepare_read(
            claim,
            live_catalog=catalog,
            authority=replace(authority, resource_scope=invalid_scope),
            now=NOW + timedelta(seconds=2),
        )
    with pytest.raises(ActionChainReadConflict, match="stale or expired"):
        preparer.prepare_read(
            replace(claim, worker_id="worker:mc03a:intruder"),
            live_catalog=catalog,
            authority=authority,
            now=NOW + timedelta(seconds=2),
        )
    with pytest.raises(ActionChainReadConflict, match="stale or expired"):
        preparer.prepare_read(
            claim,
            live_catalog=catalog,
            authority=authority,
            now=NOW + timedelta(seconds=12),
        )

    reads = metadata.tables["eva_effects.read_intents"]
    attempts = metadata.tables["eva_effects.read_attempts"]
    observations = metadata.tables["eva_effects.read_observations"]
    effects = metadata.tables["eva_effects.effect_intents"]
    _install_read_step_failure(action_chain_engine)
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_mc03a_step_failure"):
            preparer.prepare_read(
                claim,
                live_catalog=catalog,
                authority=authority,
                now=NOW + timedelta(seconds=2),
            )
    finally:
        _remove_read_step_failure(action_chain_engine)
    with action_chain_engine.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(reads)) == 0
        assert (
            connection.scalar(sa.select(plan_steps.c.state).where(
                plan_steps.c.plan_step_id == claim.plan_step_id
            ))
            == "ready"
        )
        attention = connection.execute(
            sa.select(attention_items.c.state, attention_items.c.revision).where(
                attention_items.c.attention_id == claim.attention_id
            )
        ).one()
        assert attention.state == "claimed"
        assert attention.revision == claim.attention_revision

    read_insert_entered = Event()
    release_read_insert = Event()

    def hold_after_authority_locks(
        _connection: sa.Connection,
        _cursor: object,
        statement: str,
        _parameters: object,
        _context: object,
        _executemany: bool,
    ) -> None:
        if statement.startswith("INSERT INTO eva_effects.read_intents"):
            read_insert_entered.set()
            if not release_read_insert.wait(timeout=5):
                raise AssertionError("MC03a read-insert barrier timed out")

    sa.event.listen(
        action_chain_engine, "before_cursor_execute", hold_after_authority_locks
    )
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        futures = tuple(
            pool.submit(
                preparer.prepare_read,
                claim,
                live_catalog=catalog,
                authority=authority,
                now=NOW + timedelta(seconds=2),
            )
            for _ in range(2)
        )
        assert read_insert_entered.wait(timeout=5)

        activations = metadata.tables["eva_core.capability_activations"]
        with pytest.raises(sa.exc.DBAPIError, match="statement timeout"):
            with action_chain_engine.begin() as authority_update:
                authority_update.execute(
                    sa.text("SET LOCAL statement_timeout = '250ms'")
                )
                authority_update.execute(
                    sa.update(activations)
                    .where(
                        activations.c.activation_id
                        == authority.capability_activation_id
                    )
                    .values(state="suspended", revision=2)
                )
        release_read_insert.set()
        prepared = tuple(future.result(timeout=5) for future in futures)
    finally:
        release_read_insert.set()
        pool.shutdown(wait=True, cancel_futures=True)
        sa.event.remove(
            action_chain_engine, "before_cursor_execute", hold_after_authority_locks
        )
    assert sorted(item.disposition for item in prepared) == [
        "already_prepared",
        "prepared",
    ]
    selected = next(item for item in prepared if item.disposition == "prepared")
    expected_read_id = uuid5(claim.plan_step_id, "read-intent:v1")
    assert selected.intent.read_intent_id == str(expected_read_id)
    assert selected.intent.query_sha256 == canonical_sha256(
        {"target": "overkapping"}
    )
    assert selected.intent.resource_scope == authority.resource_scope

    loaded = ledger.load(admitted.plan_id)
    assert loaded.steps[0].state == "running"
    assert loaded.steps[0].read_intent_id == expected_read_id
    assert loaded.steps[1].state == "pending"
    assert loaded.steps[1].read_intent_id is None
    assert loaded.attention.state == "waiting"
    with action_chain_engine.connect() as connection:
        assert connection.scalar(sa.select(sa.func.count()).select_from(reads)) == 1
        assert connection.scalar(sa.select(sa.func.count()).select_from(attempts)) == 0
        assert (
            connection.scalar(sa.select(sa.func.count()).select_from(observations))
            == 0
        )
        assert connection.scalar(sa.select(sa.func.count()).select_from(effects)) == 0
        assert (
            connection.scalar(
                sa.select(sa.func.count())
                .select_from(operational_events)
                .where(
                    operational_events.c.event_type
                    == "ACTION_CHAIN_READ_INTENT_PREPARED"
                )
            )
            == 2
        )
        plan_row = connection.execute(
            sa.select(plans.c.state, plans.c.revision, plans.c.created_at, plans.c.updated_at)
            .where(plans.c.plan_id == admitted.plan_id)
        ).one()
        assert plan_row.state == "current"
        assert plan_row.revision == 1
        assert plan_row.created_at == plan_row.updated_at

    action_chain_engine.dispose()
    restarted_engine = sa.create_engine(action_chain_engine.url, pool_pre_ping=False)
    try:
        restarted_ledger = PostgresActionChainLedger(restarted_engine, scope)
        assert restarted_ledger.load(admitted.plan_id) == loaded
        restarted_preparer = PostgresActionChainReadPreparer(restarted_engine, scope)
        replay = restarted_preparer.prepare_read(
            claim,
            live_catalog=catalog,
            authority=authority,
            now=NOW + timedelta(seconds=9),
        )
        assert replay.disposition == "already_prepared"
        assert replay.intent == selected.intent
        with pytest.raises(ActionChainReadConflict, match="another lease"):
            restarted_preparer.prepare_read(
                replace(claim, attention_revision=claim.attention_revision + 1),
                live_catalog=catalog,
                authority=authority,
                now=NOW + timedelta(seconds=9),
            )

        activations = metadata.tables["eva_core.capability_activations"]
        with restarted_engine.begin() as connection:
            connection.execute(
                sa.update(activations)
                .where(
                    activations.c.activation_id
                    == authority.capability_activation_id
                )
                .values(state="suspended", revision=2)
            )
        with pytest.raises(ActionChainCorrupt, match="capability binding"):
            restarted_ledger.load(admitted.plan_id)
        with restarted_engine.begin() as connection:
            connection.execute(
                sa.update(activations)
                .where(
                    activations.c.activation_id
                    == authority.capability_activation_id
                )
                .values(state="active", revision=3)
            )
        assert restarted_ledger.load(admitted.plan_id) == loaded
    finally:
        restarted_engine.dispose()
