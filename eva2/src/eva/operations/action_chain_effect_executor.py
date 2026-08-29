"""Execute one claimed action-chain effect through the sealed process boundary."""

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
from eva.adapters.postgres.action_chain_effect_results import (
    PostgresActionChainEffectResultStore,
    StoredActionChainEffectResult,
)
from eva.adapters.postgres.action_chain_effects import ActionChainEffectConflict
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.application.action_chain import ActionToolCatalog
from eva.contracts.execution import EffectAttempt
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
        self._clock = clock or _utc_now_second

    def execute(
        self,
        effect_intent_id: UUID,
        *,
        live_catalog: ActionToolCatalog,
        now: datetime,
        timeout_seconds: float,
    ) -> ExecutedActionChainEffect:
        """Call once after a durable marker and atomically retain the outcome."""

        _require_utc_second(now, "now")
        _require_timeout(timeout_seconds)
        existing = self._results.load_terminal(effect_intent_id)
        if existing is not None:
            return ExecutedActionChainEffect(
                "already_started", existing.attempt, existing
            )
        try:
            claim = self._dispatcher.claim_provider_call(
                effect_intent_id,
                live_catalog=live_catalog,
                now=now,
            )
        except ActionChainEffectConflict:
            raced_result = self._results.load_terminal(effect_intent_id)
            if raced_result is None:
                raise
            return ExecutedActionChainEffect(
                "already_started",
                raced_result.attempt,
                raced_result,
            )
        if claim.disposition == "already_started":
            return ExecutedActionChainEffect("already_started", claim.attempt, None)
        if (
            claim.process_spec is None
            or claim.connector_manifest is None
            or claim.initialize is None
            or claim.invoke is None
        ):
            raise RuntimeError("first action-chain effect claim lacks call material")

        deadline = _parse_timestamp(claim.attempt.deadline)
        remaining_seconds = (deadline - now).total_seconds()
        if remaining_seconds <= 0:
            raise RuntimeError("claimed action-chain effect has no remaining deadline")
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
            raise RuntimeError("provider result recording precedes the call")
        stored = self._results.record(
            claim,
            result=result,
            observed_at=now,
            recorded_at=recorded_at,
        )
        return ExecutedActionChainEffect("executed", stored.attempt, stored)


def _utc_now_second() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


def _require_timeout(value: float) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise TypeError("connector timeout must be numeric")
    if value <= 0 or value > 30:
        raise ValueError("connector timeout must be between 0 and 30 seconds")


__all__ = ["ExecutedActionChainEffect", "PostgresActionChainEffectExecutor"]
