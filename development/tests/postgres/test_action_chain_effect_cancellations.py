from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from typing import Iterator
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_effect_cancellations import (
    ActionChainEffectCancellationConflict,
    PostgresActionChainEffectCanceller,
)
from eva.adapters.postgres.action_chain_effects import ActionChainEffectConflict
from eva.adapters.postgres.action_chain_ledger import (
    ActionChainCorrupt,
    PostgresActionChainLedger,
)
from eva.adapters.postgres.catalog import metadata
from eva.adapters.postgres.operational_timeline import operational_events
from eva.application.action_chain import action_chain_thread_id
from eva.contracts.canonical_json import canonical_sha256
from eva.adapters.postgres.scope import set_local_scope
from eva.operations.action_chain_effect_authorizer import (
    PostgresActionChainEffectAuthorizer,
)
from eva.operations.action_chain_effect_dispatcher import (
    PostgresActionChainEffectDispatcher,
)

from test_action_chain_effect_authorizer import (
    _prepared_effect,
    _provider_registry,
)
from test_action_chain_effects import (
    _effect_counts,
    action_chain_engine as _shared_action_chain_engine,
)
from test_action_chain_ledger import NOW, _fact_values


effect_intents = metadata.tables["eva_effects.effect_intents"]
plan_steps = metadata.tables["eva_core.plan_steps"]


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def _owner() -> tuple[UUID, UUID]:
    principal = _fact_values("static/principal-owner")[1]
    binding = _fact_values("static/principal-binding")[1]
    return principal["principal_id"], binding["principal_binding_id"]


def _cancel_prepared(engine: Engine):
    scope, chain, catalog, _authority, prepared = _prepared_effect(engine)
    owner_principal_id, principal_binding_id = _owner()
    canceller = PostgresActionChainEffectCanceller(engine, scope)
    result = canceller.cancel_pre_dispatch(
        UUID(prepared.intent.effect_intent_id),
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        now=NOW + timedelta(seconds=9),
    )
    return scope, chain, catalog, prepared, canceller, result


def test_pre_dispatch_cancellation_is_owner_bound_durable_and_io_free(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, prepared, canceller, result = _cancel_prepared(
        action_chain_engine
    )
    effect_intent_id = UUID(prepared.intent.effect_intent_id)

    assert result.disposition == "cancelled"
    assert result.intent.state == "cancelled"
    assert _effect_counts(action_chain_engine, scope, chain.plan_id)["attempts"] == 0

    reloaded = PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
    assert [step.state for step in reloaded.steps] == ["succeeded", "cancelled"]
    assert reloaded.attention.state == "completed"

    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        stored_intent = connection.execute(
            sa.select(effect_intents).where(
                effect_intents.c.effect_intent_id == effect_intent_id
            )
        ).one()
        stored_step = connection.execute(
            sa.select(plan_steps).where(
                plan_steps.c.effect_intent_id == effect_intent_id
            )
        ).one()
        events = connection.execute(
            sa.select(operational_events.c.event_type).where(
                operational_events.c.event_type.in_(
                    (
                        "ACTION_CHAIN_EFFECT_CANCELLED",
                        "ACTION_CHAIN_EFFECT_DISPATCH_AUTHORIZED",
                        "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED",
                    )
                )
            )
        ).scalars().all()
    assert stored_intent.state == "cancelled"
    assert stored_intent.revision == 2
    assert stored_step.state == "cancelled"
    assert events == ["ACTION_CHAIN_EFFECT_CANCELLED", "ACTION_CHAIN_EFFECT_CANCELLED"]

    owner_principal_id, principal_binding_id = _owner()
    replay = canceller.cancel_pre_dispatch(
        effect_intent_id,
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        now=NOW + timedelta(seconds=10),
    )
    assert replay.disposition == "already_cancelled"
    assert _effect_counts(action_chain_engine, scope, chain.plan_id)["attempts"] == 0

    registry = _provider_registry(action_chain_engine, scope, prepared.intent)
    with pytest.raises(ActionChainEffectConflict, match="no longer running"):
        PostgresActionChainEffectAuthorizer(
            action_chain_engine, scope, provider_registry=registry
        ).authorize_effect(
            effect_intent_id,
            live_catalog=catalog,
            now=NOW + timedelta(seconds=11),
        )


def test_pre_dispatch_cancellation_rejects_another_owner(
    action_chain_engine: Engine,
) -> None:
    scope, chain, _catalog, _authority, prepared = _prepared_effect(action_chain_engine)
    _owner_principal_id, principal_binding_id = _owner()
    with pytest.raises(
        ActionChainEffectCancellationConflict, match="owner authority is not current"
    ):
        PostgresActionChainEffectCanceller(action_chain_engine, scope).cancel_pre_dispatch(
            UUID(prepared.intent.effect_intent_id),
            owner_principal_id=uuid4(),
            principal_binding_id=principal_binding_id,
            now=NOW + timedelta(seconds=9),
        )
    assert _effect_counts(action_chain_engine, scope, chain.plan_id)["attempts"] == 0
    assert PostgresActionChainLedger(action_chain_engine, scope).load(
        chain.plan_id
    ).steps[1].state == "running"


def test_cancellation_wins_after_authorization_before_provider_call(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, _authority, prepared = _prepared_effect(action_chain_engine)
    effect_intent_id = UUID(prepared.intent.effect_intent_id)
    registry = _provider_registry(action_chain_engine, scope, prepared.intent)
    PostgresActionChainEffectAuthorizer(
        action_chain_engine, scope, provider_registry=registry
    ).authorize_effect(
        effect_intent_id,
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
    )
    owner_principal_id, principal_binding_id = _owner()
    result = PostgresActionChainEffectCanceller(
        action_chain_engine, scope
    ).cancel_pre_dispatch(
        effect_intent_id,
        owner_principal_id=owner_principal_id,
        principal_binding_id=principal_binding_id,
        now=NOW + timedelta(seconds=10),
    )
    assert result.disposition == "cancelled"
    assert result.intent.state == "cancelled"
    assert _effect_counts(action_chain_engine, scope, chain.plan_id)["attempts"] == 1
    reloaded = PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
    assert [step.state for step in reloaded.steps] == ["succeeded", "cancelled"]

    with pytest.raises(ActionChainEffectConflict, match="prepared effect step is no longer running"):
        PostgresActionChainEffectDispatcher(
            action_chain_engine, scope, provider_registry=registry
        ).claim_provider_call(
            effect_intent_id,
            live_catalog=catalog,
            now=NOW + timedelta(seconds=11),
        )
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_runtime"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        provider_call_starts = connection.scalar(
            sa.select(sa.func.count())
            .select_from(operational_events)
            .where(
                operational_events.c.event_type
                == "ACTION_CHAIN_EFFECT_PROVIDER_CALL_STARTED"
            )
        )
    assert provider_call_starts == 0


def test_cancellation_can_win_immediately_after_authorization(
    action_chain_engine: Engine,
) -> None:
    scope, chain, catalog, _authority, prepared = _prepared_effect(action_chain_engine)
    effect_intent_id = UUID(prepared.intent.effect_intent_id)
    owner_principal_id, principal_binding_id = _owner()
    registry = _provider_registry(action_chain_engine, scope, prepared.intent)
    start = Barrier(2)

    def cancel() -> str:
        start.wait()
        return PostgresActionChainEffectCanceller(
            action_chain_engine, scope
        ).cancel_pre_dispatch(
            effect_intent_id,
            owner_principal_id=owner_principal_id,
            principal_binding_id=principal_binding_id,
            now=NOW + timedelta(seconds=9),
        ).disposition

    def authorize() -> str:
        start.wait()
        try:
            return PostgresActionChainEffectAuthorizer(
                action_chain_engine, scope, provider_registry=registry
            ).authorize_effect(
                effect_intent_id,
                live_catalog=catalog,
                now=NOW + timedelta(seconds=9),
            ).disposition
        except ActionChainEffectConflict:
            return "rejected_after_cancel"

    with ThreadPoolExecutor(max_workers=2) as workers:
        cancel_result, authorize_result = tuple(
            workers.map(lambda call: call(), (cancel, authorize))
        )

    counts = _effect_counts(action_chain_engine, scope, chain.plan_id)
    assert (cancel_result, authorize_result) in {
        ("cancelled", "rejected_after_cancel"),
        ("cancelled", "authorized"),
    }
    assert counts["attempts"] == (0 if authorize_result == "rejected_after_cancel" else 1)
    if cancel_result == "cancelled":
        assert PostgresActionChainLedger(action_chain_engine, scope).load(
            chain.plan_id
        ).steps[1].state == "cancelled"


def test_cancellation_rejects_tampered_owner_context(
    action_chain_engine: Engine,
) -> None:
    scope, chain, _catalog, prepared, _canceller, _result = _cancel_prepared(
        action_chain_engine
    )
    root_thread_id = action_chain_thread_id(chain.plan)
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        connection.exec_driver_sql(
            "ALTER TABLE eva_core.operational_events DISABLE TRIGGER USER"
        )
        root = connection.execute(
            sa.select(operational_events).where(
                operational_events.c.thread_id == root_thread_id,
                operational_events.c.event_type == "ACTION_CHAIN_EFFECT_CANCELLED",
            )
        ).one()
        payload = dict(root.payload)
        data = dict(payload["data"])
        context = dict(data["transition_context"])
        context["owner_principal_id"] = str(uuid4())
        data["transition_context"] = context
        payload["data"] = data
        connection.execute(
            sa.update(operational_events)
            .where(
                operational_events.c.tenant_id == scope.tenant_id,
                operational_events.c.installation_id == scope.installation_id,
                operational_events.c.thread_id == root_thread_id,
                operational_events.c.sequence == root.sequence,
            )
            .values(
                payload=payload,
                payload_sha256=bytes.fromhex(canonical_sha256(payload)),
            )
        )
        connection.exec_driver_sql(
            "ALTER TABLE eva_core.operational_events ENABLE TRIGGER USER"
        )
    with pytest.raises(ActionChainCorrupt, match="event context|authority or history"):
        PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
    assert prepared.intent.state == "prepared"


def test_cancellation_remains_readable_after_later_binding_revocation(
    action_chain_engine: Engine,
) -> None:
    scope, chain, _catalog, _prepared, _canceller, _result = _cancel_prepared(
        action_chain_engine
    )
    binding = _fact_values("static/principal-binding")[1]
    bindings = metadata.tables["eva_core.principal_bindings"]
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_dbos"))
        set_local_scope(
            connection,
            tenant_id=str(scope.tenant_id),
            installation_id=str(scope.installation_id),
        )
        updated = connection.execute(
            sa.update(bindings)
            .where(
                bindings.c.tenant_id == scope.tenant_id,
                bindings.c.principal_binding_id == binding["principal_binding_id"],
                bindings.c.status == "verified",
                bindings.c.revision == binding["revision"],
            )
            .values(
                status="revoked",
                revision=int(binding["revision"]) + 1,
                updated_at=NOW + timedelta(seconds=10),
            )
        )
    assert updated.rowcount == 1
    reloaded = PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
    assert reloaded.steps[1].state == "cancelled"
    assert _effect_counts(action_chain_engine, scope, chain.plan_id)["attempts"] == 0


def test_cancellation_rolls_back_if_its_timeline_event_is_rejected(
    action_chain_engine: Engine,
) -> None:
    scope, chain, _catalog, _authority, prepared = _prepared_effect(action_chain_engine)
    with action_chain_engine.begin() as connection:
        connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
        connection.exec_driver_sql(
            """
            CREATE FUNCTION eva_core.reject_action_chain_effect_cancelled()
            RETURNS trigger LANGUAGE plpgsql AS $function$
            BEGIN
                IF NEW.event_type = 'ACTION_CHAIN_EFFECT_CANCELLED' THEN
                    RAISE EXCEPTION 'forced_action_chain_effect_cancelled_failure'
                        USING ERRCODE = '55000';
                END IF;
                RETURN NEW;
            END
            $function$
            """
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_action_chain_effect_cancelled
            BEFORE INSERT ON eva_core.operational_events
            FOR EACH ROW EXECUTE FUNCTION eva_core.reject_action_chain_effect_cancelled()
            """
        )
    owner_principal_id, principal_binding_id = _owner()
    try:
        with pytest.raises(sa.exc.DBAPIError, match="forced_action_chain_effect_cancelled"):
            PostgresActionChainEffectCanceller(
                action_chain_engine, scope
            ).cancel_pre_dispatch(
                UUID(prepared.intent.effect_intent_id),
                owner_principal_id=owner_principal_id,
                principal_binding_id=principal_binding_id,
                now=NOW + timedelta(seconds=9),
            )
    finally:
        with action_chain_engine.begin() as connection:
            connection.execute(sa.text("SET LOCAL ROLE eva_migrator"))
            connection.exec_driver_sql(
                "DROP TRIGGER reject_action_chain_effect_cancelled "
                "ON eva_core.operational_events"
            )
            connection.exec_driver_sql(
                "DROP FUNCTION eva_core.reject_action_chain_effect_cancelled()"
            )
    reloaded = PostgresActionChainLedger(action_chain_engine, scope).load(chain.plan_id)
    assert reloaded.steps[1].state == "running"
    assert _effect_counts(action_chain_engine, scope, chain.plan_id)["attempts"] == 0
