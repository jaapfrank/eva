"""Owner-bound dashboard control for one durable effect-chain stop request.

The controller deliberately distinguishes a cancellation before provider I/O
from a request made after the irreversible call-start fence.  The latter does
not claim to kill a browser process or undo a website action.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy.engine import Engine

from eva.adapters.postgres.action_chain_effect_cancellations import (
    ActionChainEffectCancellationConflict,
    PostgresActionChainEffectCanceller,
)
from eva.adapters.postgres.action_chain_effect_stop_requests import (
    ActionChainEffectStopRequestConflict,
    PostgresActionChainEffectStopRequester,
)
from eva.adapters.postgres.operational_timeline import TimelineScope


@dataclass(frozen=True, slots=True)
class DashboardActionChainEffectStop:
    """A field-minimized, honest outcome for one owner's stop request."""

    disposition: Literal[
        "cancelled_before_call",
        "already_cancelled_before_call",
        "stop_requested_after_call_start",
        "already_requested_after_call_start",
        "already_terminal",
        "not_stoppable",
    ]
    effect_intent_id: UUID
    message: str
    readback_required: bool


class DashboardActionChainEffectController:
    """Give the configured dashboard owner one bounded effect-stop control."""

    def __init__(
        self,
        engine: Engine,
        scope: TimelineScope,
        *,
        owner_principal_id: UUID,
        principal_binding_id: UUID,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._scope = scope
        self._owner_principal_id = owner_principal_id
        self._principal_binding_id = principal_binding_id
        self._clock = clock or (lambda: datetime.now(tz=UTC).replace(microsecond=0))
        self._canceller = PostgresActionChainEffectCanceller(engine, scope)
        self._requester = PostgresActionChainEffectStopRequester(engine, scope)

    def stop(self, effect_intent_id: UUID) -> DashboardActionChainEffectStop:
        """Stop only where the durable action-chain state makes that truthful."""

        now = self._now()
        try:
            cancelled = self._canceller.cancel_pre_dispatch(
                effect_intent_id,
                owner_principal_id=self._owner_principal_id,
                principal_binding_id=self._principal_binding_id,
                now=now,
            )
        except ActionChainEffectCancellationConflict as error:
            raise RuntimeError("effect stop control is unavailable") from error
        if cancelled.disposition == "cancelled":
            return DashboardActionChainEffectStop(
                "cancelled_before_call",
                effect_intent_id,
                "Gestopt voordat Eva iets naar buiten stuurde.",
                False,
            )
        if cancelled.disposition == "already_cancelled":
            return DashboardActionChainEffectStop(
                "already_cancelled_before_call",
                effect_intent_id,
                "Deze actie was al gestopt voordat Eva iets naar buiten stuurde.",
                False,
            )

        try:
            requested = self._requester.request_stop(
                effect_intent_id,
                owner_principal_id=self._owner_principal_id,
                principal_binding_id=self._principal_binding_id,
                now=now,
            )
        except ActionChainEffectStopRequestConflict:
            return DashboardActionChainEffectStop(
                "not_stoppable",
                effect_intent_id,
                "Deze actie kan niet meer veilig worden gestopt. Eva start niets opnieuw; controle van de uitkomst is nodig.",
                True,
            )
        if requested.disposition == "stop_requested":
            return DashboardActionChainEffectStop(
                "stop_requested_after_call_start",
                effect_intent_id,
                "Je stopopdracht is bewaard. De actie kan al onderweg zijn; Eva start niets nieuws en de uitkomst moet worden gecontroleerd.",
                True,
            )
        if requested.disposition == "already_requested":
            return DashboardActionChainEffectStop(
                "already_requested_after_call_start",
                effect_intent_id,
                "De stopopdracht was al bewaard. Eva start niets nieuws en de uitkomst moet worden gecontroleerd.",
                True,
            )
        return DashboardActionChainEffectStop(
            "already_terminal",
            effect_intent_id,
            "Deze actie was al afgerond; controleer de geregistreerde uitkomst.",
            True,
        )

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None or now.utcoffset() is None or now.microsecond:
            raise RuntimeError("effect stop clock must use aware whole seconds")
        return now.astimezone(UTC)


__all__ = ["DashboardActionChainEffectController", "DashboardActionChainEffectStop"]
