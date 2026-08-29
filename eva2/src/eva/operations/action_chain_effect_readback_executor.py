"""Execute one claimed effect readback through the sealed process boundary."""

from __future__ import annotations
from eva.contracts.timestamps import format_utc_timestamp as _format_timestamp
from eva.contracts.timestamps import parse_utc_timestamp as _parse_timestamp
from eva.contracts.timestamps import require_utc_second as _require_utc_second

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
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
        self._clock = clock or _utc_now_second

    def execute(
        self,
        read_intent_id: UUID,
        *,
        now: datetime,
        timeout_seconds: float,
    ) -> ExecutedActionChainEffectReadback:
        """Call once after the durable marker and atomically retain the outcome."""

        _require_utc_second(now, "now")
        _require_timeout(timeout_seconds)
        existing = self._results.load_terminal(read_intent_id)
        if existing is not None:
            return ExecutedActionChainEffectReadback(
                "already_started", existing.attempt, existing
            )
        try:
            claim = self._dispatcher.claim_provider_call(
                read_intent_id,
                now=now,
            )
        except ActionChainEffectReadbackConflict:
            raced_result = self._results.load_terminal(read_intent_id)
            if raced_result is None:
                raise
            return ExecutedActionChainEffectReadback(
                "already_started",
                raced_result.attempt,
                raced_result,
            )
        if claim.disposition == "already_started":
            return ExecutedActionChainEffectReadback(
                "already_started", claim.attempt, None
            )
        if (
            claim.process_spec is None
            or claim.connector_manifest is None
            or claim.initialize is None
            or claim.invoke is None
        ):
            raise RuntimeError("first effect readback claim lacks call material")

        deadline = _parse_timestamp(claim.attempt.deadline)
        remaining_seconds = (deadline - now).total_seconds()
        if remaining_seconds <= 0:
            raise RuntimeError("claimed effect readback has no remaining deadline")
        result = run_connector_process(
            spec=claim.process_spec,
            manifest=claim.connector_manifest,
            initialize=claim.initialize,
            invoke=claim.invoke,
            observed_at=_format_timestamp(now),
            timeout_seconds=min(timeout_seconds, remaining_seconds),
        )
        recorded_at = self._clock()
        _require_utc_second(recorded_at, "clock result")
        if recorded_at < now:
            raise RuntimeError("effect readback recording precedes the call")
        stored = self._results.record(
            claim,
            result=result,
            observed_at=now,
            recorded_at=recorded_at,
        )
        return ExecutedActionChainEffectReadback("executed", stored.attempt, stored)


def _utc_now_second() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _require_timeout(value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError("connector timeout must be numeric")
    if value <= 0 or value > 30:
        raise ValueError("connector timeout must be between 0 and 30 seconds")








__all__ = [
    "ExecutedActionChainEffectReadback",
    "PostgresActionChainEffectReadbackExecutor",
]
