"""Execute one claimed effect readback through the sealed process boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


from eva.adapters.connectors.process import run_connector_process
from eva.adapters.postgres.action_chain_effect_readbacks import (
    ActionChainEffectReadbackConflict,
)
from eva.adapters.postgres.action_chain_read_results import (
    PostgresActionChainReadResultStore,
    StoredActionChainReadResult,
)
from eva.contracts.execution import ReadAttempt
from eva.operations.action_chain_dispatch_common import (
    PostgresActionChainConnectorExecutor,
)
from eva.operations.action_chain_effect_readback_dispatcher import (
    PostgresActionChainEffectReadbackDispatcher,
)


@dataclass(frozen=True, slots=True)
class ExecutedActionChainEffectReadback:
    """A first execution has a durable result; replay has no call material."""

    disposition: Literal["executed", "already_started"]
    attempt: ReadAttempt
    stored_result: StoredActionChainReadResult | None


class PostgresActionChainEffectReadbackExecutor(
    PostgresActionChainConnectorExecutor
):
    """Own the only provider-process call for a claimed effect verification."""

    _dispatcher_factory = PostgresActionChainEffectReadbackDispatcher
    _result_store_factory = PostgresActionChainReadResultStore
    _conflict = ActionChainEffectReadbackConflict
    _claim_label = "effect readback"
    _recording_subject = "effect readback"

    def execute(
        self,
        read_intent_id: UUID,
        *,
        now: datetime,
        timeout_seconds: float,
    ) -> ExecutedActionChainEffectReadback:
        """Call once after the durable marker and atomically retain the outcome."""

        return ExecutedActionChainEffectReadback(
            *self._execute_connector_once(
                read_intent_id,
                claim_provider=lambda: self._dispatcher.claim_provider_call(
                    read_intent_id,
                    now=now,
                ),
                now=now,
                timeout_seconds=timeout_seconds,
                runner=run_connector_process,
            )
        )


__all__ = [
    "ExecutedActionChainEffectReadback",
    "PostgresActionChainEffectReadbackExecutor",
]
