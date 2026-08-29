"""Execute one claimed action-chain read through the sealed process boundary."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.engine import Engine

from eva.adapters.connectors.process import run_connector_process
from eva.adapters.postgres.action_chain_read_results import (
    PostgresActionChainReadResultStore,
    StoredActionChainReadResult,
)
from eva.adapters.postgres.action_chain_reads import ActionChainReadConflict
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.application.action_chain import ActionToolCatalog
from eva.contracts.execution import ReadAttempt
from eva.operations.action_chain_dispatch_common import (
    execute_connector_once,
    utc_now_second,
)
from eva.operations.action_chain_provider_registry import SealedReadProviderRegistry
from eva.operations.action_chain_read_dispatcher import (
    PostgresActionChainReadDispatcher,
)


@dataclass(frozen=True, slots=True)
class ExecutedActionChainRead:
    """A first execution has a durable result; replay exposes no call material."""

    disposition: Literal["executed", "already_started"]
    attempt: ReadAttempt
    stored_result: StoredActionChainReadResult | None


class PostgresActionChainReadExecutor:
    """Own the only provider-process call for a validated action-chain read."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        provider_registry: SealedReadProviderRegistry,
        role: Literal["eva_runtime", "eva_dbos"] = "eva_runtime",
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._dispatcher = PostgresActionChainReadDispatcher(
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
        live_catalog: ActionToolCatalog,
        now: datetime,
        timeout_seconds: float,
    ) -> ExecutedActionChainRead:
        """Call once after a durable marker and atomically retain the outcome."""

        return ExecutedActionChainRead(
            *execute_connector_once(
                read_intent_id,
                results=self._results,
                claim_provider=lambda: self._dispatcher.claim_provider_call(
                    read_intent_id,
                    live_catalog=live_catalog,
                    now=now,
                ),
                conflict=ActionChainReadConflict,
                now=now,
                timeout_seconds=timeout_seconds,
                clock=self._clock,
                claim_label="action-chain read",
                recording_subject="provider result",
                runner=run_connector_process,
            )
        )


__all__ = ["ExecutedActionChainRead", "PostgresActionChainReadExecutor"]
