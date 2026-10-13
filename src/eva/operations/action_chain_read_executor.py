"""Execute one claimed action-chain read through the sealed process boundary."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal
from uuid import UUID


from eva.adapters.connectors.process import run_connector_process
from eva.adapters.postgres.action_chain_read_results import (
    PostgresActionChainReadResultStore,
    StoredActionChainReadResult,
)
from eva.adapters.postgres.action_chain_reads import ActionChainReadConflict
from eva.application.action_chain import ActionToolCatalog
from eva.contracts.execution import ReadAttempt
from eva.operations.action_chain_dispatch_common import (
    PostgresActionChainConnectorExecutor,
)
from eva.operations.action_chain_read_dispatcher import (
    PostgresActionChainReadDispatcher,
)


@dataclass(frozen=True, slots=True)
class ExecutedActionChainRead:
    """A first execution has a durable result; replay exposes no call material."""

    disposition: Literal["executed", "already_started"]
    attempt: ReadAttempt
    stored_result: StoredActionChainReadResult | None


class PostgresActionChainReadExecutor(PostgresActionChainConnectorExecutor):
    """Own the only provider-process call for a validated action-chain read."""

    _dispatcher_factory = PostgresActionChainReadDispatcher
    _result_store_factory = PostgresActionChainReadResultStore
    _conflict = ActionChainReadConflict
    _claim_label = "action-chain read"
    _recording_subject = "provider result"

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
            *self._execute_catalog_connector_once(
                read_intent_id,
                live_catalog=live_catalog,
                now=now,
                timeout_seconds=timeout_seconds,
                runner=run_connector_process,
            )
        )


__all__ = ["ExecutedActionChainRead", "PostgresActionChainReadExecutor"]
