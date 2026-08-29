"""Execute one claimed action-chain effect through the sealed process boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.engine import Engine

from eva.adapters.connectors.process import run_connector_process
from eva.adapters.postgres.action_chain_effect_results import (
    PostgresActionChainEffectResultStore,
    StoredActionChainEffectResult,
)
from eva.adapters.postgres.action_chain_effects import ActionChainEffectConflict
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.application.action_chain import ActionToolCatalog
from eva.contracts.execution import EffectAttempt
from eva.operations.action_chain_dispatch_common import (
    execute_connector_once,
    utc_now_second,
)
from eva.operations.action_chain_effect_dispatcher import (
    PostgresActionChainEffectDispatcher,
)
from eva.operations.action_chain_provider_registry import SealedActionProviderRegistry


@dataclass(frozen=True, slots=True)
class ExecutedActionChainEffect:
    """A first execution has a durable result; replay exposes no call material."""

    disposition: Literal["executed", "already_started"]
    attempt: EffectAttempt
    stored_result: StoredActionChainEffectResult | None


class PostgresActionChainEffectExecutor:
    """Own the only provider-process call for a validated action-chain effect."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        provider_registry: SealedActionProviderRegistry,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._dispatcher = PostgresActionChainEffectDispatcher(
            engine,
            scope,
            provider_registry=provider_registry,
            role=role,
        )
        self._results = PostgresActionChainEffectResultStore(
            engine,
            scope,
            role=role,
        )
        self._clock = clock or utc_now_second

    def execute(
        self,
        effect_intent_id: UUID,
        *,
        live_catalog: ActionToolCatalog,
        now: datetime,
        timeout_seconds: float,
    ) -> ExecutedActionChainEffect:
        """Call once after a durable marker and atomically retain the outcome."""

        return ExecutedActionChainEffect(
            *execute_connector_once(
                effect_intent_id,
                results=self._results,
                claim_provider=lambda: self._dispatcher.claim_provider_call(
                    effect_intent_id,
                    live_catalog=live_catalog,
                    now=now,
                ),
                conflict=ActionChainEffectConflict,
                now=now,
                timeout_seconds=timeout_seconds,
                clock=self._clock,
                claim_label="action-chain effect",
                recording_subject="provider result",
                runner=run_connector_process,
            )
        )


__all__ = ["ExecutedActionChainEffect", "PostgresActionChainEffectExecutor"]
