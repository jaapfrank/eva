"""Execute one claimed action-chain effect through the sealed process boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


from eva.adapters.connectors.process import run_connector_process
from eva.adapters.postgres.action_chain_effect_results import (
    PostgresActionChainEffectResultStore,
    StoredActionChainEffectResult,
)
from eva.adapters.postgres.action_chain_effects import ActionChainEffectConflict
from eva.application.action_chain import ActionToolCatalog
from eva.contracts.execution import EffectAttempt
from eva.operations.action_chain_dispatch_common import (
    PostgresActionChainConnectorExecutor,
)
from eva.operations.action_chain_effect_dispatcher import (
    PostgresActionChainEffectDispatcher,
)


@dataclass(frozen=True, slots=True)
class ExecutedActionChainEffect:
    """A first execution has a durable result; replay exposes no call material."""

    disposition: Literal["executed", "already_started"]
    attempt: EffectAttempt
    stored_result: StoredActionChainEffectResult | None


class PostgresActionChainEffectExecutor(PostgresActionChainConnectorExecutor):
    """Own the only provider-process call for a validated action-chain effect."""

    _dispatcher_factory = PostgresActionChainEffectDispatcher
    _result_store_factory = PostgresActionChainEffectResultStore
    _conflict = ActionChainEffectConflict
    _claim_label = "action-chain effect"
    _recording_subject = "provider result"

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
            *self._execute_connector_once(
                effect_intent_id,
                claim_provider=lambda: self._dispatcher.claim_provider_call(
                    effect_intent_id,
                    live_catalog=live_catalog,
                    now=now,
                ),
                now=now,
                timeout_seconds=timeout_seconds,
                runner=run_connector_process,
            )
        )


__all__ = ["ExecutedActionChainEffect", "PostgresActionChainEffectExecutor"]
