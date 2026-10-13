from __future__ import annotations

from datetime import timedelta
from typing import Iterator
from uuid import UUID

import pytest
from sqlalchemy.engine import Engine

from eva.bootstrap.action_chain_effect_controls import (
    DashboardActionChainEffectController,
)
from eva.operations.action_chain_effect_dispatcher import (
    PostgresActionChainEffectDispatcher,
)

from test_action_chain_effect_authorizer import _prepared_effect, _provider_registry
from test_action_chain_effects import action_chain_engine as _shared_action_chain_engine
from test_action_chain_ledger import NOW, _fact_values


@pytest.fixture
def action_chain_engine() -> Iterator[Engine]:
    yield from _shared_action_chain_engine.__wrapped__()


def _controller(engine: Engine, scope) -> DashboardActionChainEffectController:
    principal = _fact_values("static/principal-owner")[1]
    binding = _fact_values("static/principal-binding")[1]
    return DashboardActionChainEffectController(
        engine,
        scope,
        owner_principal_id=principal["principal_id"],
        principal_binding_id=binding["principal_binding_id"],
        clock=lambda: NOW + timedelta(seconds=10),
    )


def test_dashboard_effect_stop_cancels_before_provider_call(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, _catalog, _authority, prepared = _prepared_effect(action_chain_engine)

    result = _controller(action_chain_engine, scope).stop(
        UUID(prepared.intent.effect_intent_id)
    )

    assert result.disposition == "cancelled_before_call"
    assert result.readback_required is False
    assert result.message == "Gestopt voordat Eva iets naar buiten stuurde."


def test_dashboard_effect_stop_records_but_never_overclaims_after_call_start(
    action_chain_engine: Engine,
) -> None:
    scope, _chain, catalog, _authority, prepared = _prepared_effect(action_chain_engine)
    dispatcher = PostgresActionChainEffectDispatcher(
        action_chain_engine,
        scope,
        provider_registry=_provider_registry(action_chain_engine, scope, prepared.intent),
    )
    claimed = dispatcher.claim_provider_call(
        UUID(prepared.intent.effect_intent_id),
        live_catalog=catalog,
        now=NOW + timedelta(seconds=9),
    )
    assert claimed.disposition == "claimed"

    result = _controller(action_chain_engine, scope).stop(
        UUID(prepared.intent.effect_intent_id)
    )

    assert result.disposition == "stop_requested_after_call_start"
    assert result.readback_required is True
    assert "kan al onderweg zijn" in result.message
    assert "ongedaan" not in result.message
