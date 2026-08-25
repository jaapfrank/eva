from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
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
    action_chain_thread_id,
)
from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.planning import ModelDecision, ValidatedPlan


EVA2_ROOT = Path(__file__).resolve().parents[2]
SCHEDULE_PATH = (
    EVA2_ROOT / "design/vertical/noor-4d-concurrency-crash-schedules-v1.json"
)
SCHEDULE_SHA256 = "f492d746779ec5276478893ff22720c84926912371d19175dd7508a18734b91d"
NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)
RETENTION_UNTIL = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)
H = "a" * 64

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


@pytest.fixture(scope="module")
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
                policy_scope_sha256="d" * 64,
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
                policy_scope_sha256="d" * 64,
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
        policy_sha256="d" * 64,
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
                .values(state="waiting")
            )
        with pytest.raises(ActionChainCorrupt, match="attention binding"):
            restarted.load(loaded.plan_id)
        with restarted_engine.begin() as connection:
            connection.execute(
                sa.update(attention_items)
                .where(attention_items.c.attention_id == attention_id)
                .values(state="pending")
            )

        root_thread_id = action_chain_thread_id(loaded.plan)
        with restarted_engine.begin() as connection:
            connection.execute(
                sa.update(operational_checkpoints)
                .where(operational_checkpoints.c.thread_id == root_thread_id)
                .values(status="failed")
            )
        with pytest.raises(ActionChainCorrupt, match="timeline origin"):
            restarted.load(loaded.plan_id)
    finally:
        restarted_engine.dispose()
