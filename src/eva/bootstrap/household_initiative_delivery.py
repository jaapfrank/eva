"""Offer one household-only initiative through an authorized HA satellite.

An HTTP service acknowledgement proves offered, not heard. Durable claims and
ambiguous failures prevent automatic replay after an uncertain announcement.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import logging
from typing import Callable, Protocol

import httpx2 as httpx

from eva.application.free_will import ProactiveExpressionClaim
from eva.application.household_initiative import HouseholdInitiativeBinding
from eva.bootstrap.native_work_delivery import NativeWorkDelivery
from eva.contracts.canonical_json import canonical_sha256

_LOG = logging.getLogger(__name__)


class HouseholdExpressionStore(Protocol):
    def claim_next_household_expression(self, *, binding: dict[str, str], now: datetime
                                        ) -> ProactiveExpressionClaim | None: ...

    def authorize_household_expression(self, claim: ProactiveExpressionClaim, *,
                                       binding: dict[str, str], now: datetime) -> None: ...

    def acknowledge_household_expression(self, claim: ProactiveExpressionClaim, *,
            binding: dict[str, str], disposition: str, provider_message_id: str | None,
            now: datetime) -> None: ...

    def fail_expression(self, claim: ProactiveExpressionClaim, error: Exception, *,
                        now: datetime) -> None: ...


class HouseholdInitiativeDelivery:
    def __init__(self, store: HouseholdExpressionStore, binding: HouseholdInitiativeBinding,
                 authorize: Callable[[], None], provider, *,
                 delivery_window: Callable[[datetime], bool] | None = None,
                 validate_expression: Callable[[ProactiveExpressionClaim], bool | None] | None = None,
                 admit_expression: Callable[[ProactiveExpressionClaim], dict] | None = None,
                 settle_expression: Callable[..., None] | None = None,
                 clock: Callable[[], datetime] | None = None):
        if not isinstance(binding, HouseholdInitiativeBinding) or not callable(authorize):
            raise ValueError("household initiative delivery requires bound current authority")
        if delivery_window is not None and not callable(delivery_window):
            raise ValueError("household delivery window is invalid")
        if ((admit_expression is None) != (settle_expression is None)
                or (admit_expression is not None and
                    (not callable(admit_expression) or not callable(settle_expression)))):
            raise ValueError("household effect admission requires paired settlement")
        self.store, self.binding, self.authorize, self.provider = store, binding, authorize, provider
        self.validate_expression = validate_expression
        self.admit_expression, self.settle_expression = admit_expression, settle_expression
        self.delivery_window = delivery_window
        self.clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))

    def _now(self) -> datetime:
        now = self.clock()
        if (not isinstance(now, datetime) or now.tzinfo is None
                or now.utcoffset() != timedelta(0) or now.microsecond):
            raise ValueError("household initiative delivery requires whole-second UTC")
        return now

    def _window_open(self, now: datetime) -> bool:
        return self.delivery_window is None or self.delivery_window(now) is True

    def _interrupted(self, claim, payload):
        self.store.acknowledge_household_expression(claim, binding=payload,
            disposition="interrupted", provider_message_id=None, now=self._now())
        return "interrupted"

    def run_once(self) -> str:
        claim = None
        admission = None
        settled = False
        payload = self.binding.to_payload()
        try:
            self.authorize()
            if not self._window_open(self._now()):
                return "idle"
            with httpx.Client(base_url=self.provider.endpoint_url.rstrip("/"),
                    headers={"Authorization": "Bearer " + self.provider.credential},
                    timeout=4, follow_redirects=False, trust_env=False) as client:
                if not NativeWorkDelivery._idle(client, self.binding.satellite_id):
                    return "idle"
                self.authorize()
                if not self._window_open(self._now()):
                    return "idle"
                claim = self.store.claim_next_household_expression(binding=payload, now=self._now())
                if claim is None:
                    return "idle"
                if (not isinstance(claim, ProactiveExpressionClaim)
                        or not isinstance(claim.text, str) or not claim.text.strip()
                        or len(claim.text) > 600 or type(claim.urgency) is not int
                        or not 1 <= claim.urgency <= 3):
                    raise ValueError("household initiative claim is invalid")
                self.store.authorize_household_expression(claim, binding=payload, now=self._now())
                if not NativeWorkDelivery._idle(client, self.binding.satellite_id):
                    return self._interrupted(claim, payload)
                self.authorize()
                if not self._window_open(self._now()):
                    return self._interrupted(claim, payload)
                # Re-read durable provenance and lease immediately before the
                # effect, after the potentially blocking satellite observation.
                self.store.authorize_household_expression(claim, binding=payload, now=self._now())
                # Durable provenance may block while a conversation starts or
                # quiet hours begin. Recheck the current attention window after
                # that read, immediately before offering an unsolicited message.
                if not self._window_open(self._now()):
                    return self._interrupted(claim, payload)
                if self.validate_expression is not None:
                    if self.validate_expression(claim) is False:
                        return self._interrupted(claim, payload)
                elif self.store.household_expression_requires_guard(claim):
                    return self._interrupted(claim, payload)
                self.authorize()
                self.store.authorize_household_expression(claim, binding=payload, now=self._now())
                if not self._window_open(self._now()):
                    return self._interrupted(claim, payload)
                # This is the final admission point, after potentially blocking
                # provenance reads. Pause/STOP serialize with this admission;
                # an already admitted effect may finish, and is never replayed.
                if self.admit_expression is not None:
                    admission = self.admit_expression(claim)
                    if (not isinstance(admission, dict)
                            or type(admission.get("admitted_now")) is not bool
                            or "receipt" not in admission):
                        raise ValueError("household effect admission is invalid")
                    if not admission["admitted_now"]:
                        return self._interrupted(claim, payload)
                    # Admission can block while a conversation or quiet hours
                    # begin. No HTTP effect has started when this gate closes.
                    if not self._window_open(self._now()):
                        self.settle_expression(admission, outcome="not_dispatched",
                            proof_sha256=canonical_sha256({"operation_id": claim.operation_id,
                                "source_event_id": claim.source_event_id,
                                "outcome": "attention closed before household announcement"}))
                        settled = True
                        return self._interrupted(claim, payload)
                response = client.post("/api/services/assist_satellite/announce",
                    json={"entity_id": self.binding.satellite_id, "message": claim.text,
                          "preannounce": False})
                response.raise_for_status()
                receipt = "ha-offered:" + canonical_sha256({
                    "binding": payload, "operation_id": claim.operation_id,
                    "thread_id": claim.thread_id, "revision": claim.revision,
                    "source_event_id": claim.source_event_id,
                    "text_sha256": canonical_sha256(claim.text),
                    "http_status": response.status_code})
                if admission is not None:
                    # The existing receipt proves offered, never heard or adoption.
                    self.settle_expression(admission, outcome="completed",
                        proof_sha256=receipt.removeprefix("ha-offered:"))
                    settled = True
            self.store.acknowledge_household_expression(claim, binding=payload,
                disposition="offered", provider_message_id=receipt, now=self._now())
            return "offered"
        except Exception:
            if isinstance(admission, dict) and admission.get("admitted_now") is True and not settled:
                try:
                    self.settle_expression(admission, outcome="unknown",
                        proof_sha256=canonical_sha256({"operation_id": claim.operation_id,
                            "source_event_id": claim.source_event_id,
                            "outcome": "household announcement unconfirmed"}))
                except Exception:
                    # An unsettled admission remains non-replayable.
                    pass
            # Exceptions can carry bearer credentials or spoken content. Persist
            # only a fixed fingerprint source; never replay an uncertain claim.
            _LOG.warning("Household initiative delivery unavailable")
            if claim is not None:
                try:
                    self.store.fail_expression(claim,
                        RuntimeError("household announcement outcome unavailable"), now=self._now())
                except Exception:
                    # The durable claimed lease must expire as ambiguous, not
                    # return to the send queue if this failure write also fails.
                    pass
                return "ambiguous"
            return "unavailable"

    def __call__(self) -> str:
        return self.run_once()
