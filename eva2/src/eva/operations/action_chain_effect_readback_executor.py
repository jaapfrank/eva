"""Execute one claimed effect readback through the sealed process boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.engine import Engine

from eva.adapters.connectors.process import run_connector_process
from eva.adapters.postgres.action_chain_effect_readbacks import (
    ActionChainEffectReadbackConflict,
)
from eva.adapters.postgres.action_chain_read_results import (
    PostgresActionChainReadResultStore,
    StoredActionChainReadResult,
)
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.contracts.execution import ReadAttempt
from eva.operations.action_chain_dispatch_common import (
    execute_connector_once,
    utc_now_second,
)
from eva.operations.action_chain_effect_readback_dispatcher import (
    PostgresActionChainEffectReadbackDispatcher,
)
from eva.operations.action_chain_provider_registry import SealedReadProviderRegistry


@dataclass(frozen=True, slots=True)
class ExecutedActionChainEffectReadback:
    """A first execution has a durable result; replay has no call material."""

    disposition: Literal["executed", "already_started"]
    attempt: ReadAttempt
    stored_result: StoredActionChainReadResult | None


class PostgresActionChainEffectReadbackExecutor:
    """Own the only provider-process call for a claimed effect verification."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        provider_registry: SealedReadProviderRegistry,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._dispatcher = PostgresActionChainEffectReadbackDispatcher(
            engine,
            scope,
            provider_registry=provider_registry,
            role=role,
        )
        self._results = PostgresActionChainReadResultStore(
            engine,
            scope,
            role=role,
        )
        self._clock = clock or utc_now_second

    def execute(
        self,
        read_intent_id: UUID,
        *,
        now: datetime,
        timeout_seconds: float,
    ) -> ExecutedActionChainEffectReadback:
        """Call once after the durable marker and atomically retain the outcome."""

        return ExecutedActionChainEffectReadback(
            *execute_connector_once(
                read_intent_id,
                results=self._results,
                claim_provider=lambda: self._dispatcher.claim_provider_call(
                    read_intent_id,
                    now=now,
                ),
                conflict=ActionChainEffectReadbackConflict,
                now=now,
                timeout_seconds=timeout_seconds,
                clock=self._clock,
                claim_label="effect readback",
                recording_subject="effect readback",
                runner=run_connector_process,
            )
        )


__all__ = [
    "ExecutedActionChainEffectReadback",
    "PostgresActionChainEffectReadbackExecutor",
]
