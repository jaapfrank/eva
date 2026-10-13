"""Verify Stripe checkout webhooks before they become commercial truth."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import hashlib
import hmac
import json
import re

from eva.application.commerce import CommerceError, PaidOrderReceipt


_ADMITTED_EVENTS = {
    "checkout.session.async_payment_succeeded",
    "checkout.session.completed",
}


@dataclass(frozen=True, slots=True)
class StripeWebhookConfig:
    signing_secret: str = field(repr=False)
    live_mode: bool = False
    tolerance_seconds: int = 300

    def __post_init__(self) -> None:
        if (
            not self.signing_secret.startswith("whsec_")
            or len(self.signing_secret) > 4096
        ):
            raise ValueError("Stripe webhook signing secret is invalid")
        if not isinstance(self.live_mode, bool):
            raise ValueError("Stripe webhook mode is invalid")
        if not 30 <= self.tolerance_seconds <= 900:
            raise ValueError("Stripe webhook tolerance is invalid")


class StripeWebhookVerifier:
    def __init__(self, config: StripeWebhookConfig) -> None:
        self._config = config

    def verify(
        self, payload: bytes, signature_header: str, *, now: datetime
    ) -> PaidOrderReceipt:
        if (
            not isinstance(payload, bytes)
            or not payload
            or len(payload) > 1_048_576
        ):
            raise CommerceError("Stripe webhook payload is invalid")
        if now.tzinfo is None or now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("Stripe webhook verification time must be UTC")
        timestamp, signatures = self._signature_parts(signature_header)
        if abs(int(now.timestamp()) - timestamp) > self._config.tolerance_seconds:
            raise CommerceError("Stripe webhook signature timestamp is stale")
        signed = str(timestamp).encode("ascii") + b"." + payload
        expected = hmac.new(
            self._config.signing_secret.encode("utf-8"), signed, hashlib.sha256
        ).hexdigest()
        if not any(hmac.compare_digest(expected, candidate) for candidate in signatures):
            raise CommerceError("Stripe webhook signature is invalid")
        try:
            event = json.loads(payload)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise CommerceError("Stripe webhook is not JSON") from error
        return self._receipt(event, now=now)

    @staticmethod
    def _signature_parts(value: str) -> tuple[int, tuple[str, ...]]:
        timestamp = None
        signatures: list[str] = []
        for item in str(value or "").split(","):
            key, separator, raw = item.strip().partition("=")
            if not separator:
                continue
            if key == "t" and raw.isdigit():
                timestamp = int(raw)
            elif key == "v1" and re.fullmatch(r"[0-9a-f]{64}", raw):
                signatures.append(raw)
        if timestamp is None or not signatures:
            raise CommerceError("Stripe webhook signature header is invalid")
        return timestamp, tuple(signatures)

    def _receipt(self, event: object, *, now: datetime) -> PaidOrderReceipt:
        if not isinstance(event, dict):
            raise CommerceError("Stripe webhook event is invalid")
        event_type = event.get("type")
        event_id = event.get("id")
        created = event.get("created")
        event_live_mode = event.get("livemode")
        data = event.get("data")
        session = data.get("object") if isinstance(data, dict) else None
        if (
            event_type not in _ADMITTED_EVENTS
            or not isinstance(event_id, str)
            or not isinstance(created, int)
            or isinstance(created, bool)
            or not isinstance(event_live_mode, bool)
            or event_live_mode is not self._config.live_mode
            or not isinstance(session, dict)
        ):
            raise CommerceError("Stripe webhook event is not an admitted paid checkout")
        if created > int(now.timestamp()) + self._config.tolerance_seconds:
            raise CommerceError("Stripe webhook event time is in the future")
        metadata = session.get("metadata")
        customer = session.get("customer_details")
        email = customer.get("email") if isinstance(customer, dict) else None
        if email is not None and (
            not isinstance(email, str) or not email.strip() or len(email) > 320
        ):
            raise CommerceError("Stripe checkout customer email is invalid")
        if (
            session.get("object") != "checkout.session"
            or session.get("status") != "complete"
            or session.get("payment_status") != "paid"
            or session.get("currency") != "eur"
            or not isinstance(session.get("amount_total"), int)
            or isinstance(session.get("amount_total"), bool)
            or not isinstance(session.get("id"), str)
            or not isinstance(session.get("payment_link"), str)
            or not isinstance(metadata, dict)
            or not isinstance(metadata.get("eva_plan_sha256"), str)
            or not isinstance(metadata.get("eva_release_sha256"), str)
        ):
            raise CommerceError("Stripe checkout session is not proven paid")
        paid_at = datetime.fromtimestamp(created, tz=UTC).replace(microsecond=0)
        email_hash = (
            None
            if email is None
            else hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
        )
        try:
            return PaidOrderReceipt(
                provider="stripe_checkout",
                provider_event_id=event_id,
                checkout_session_id=session["id"],
                provider_resource_id=session["payment_link"],
                plan_sha256=metadata["eva_plan_sha256"],
                release_sha256=metadata["eva_release_sha256"],
                amount_eur_cents=session["amount_total"],
                live_mode=event_live_mode,
                buyer_email_sha256=email_hash,
                paid_at=paid_at,
            )
        except (TypeError, ValueError) as error:
            raise CommerceError("Stripe paid-order receipt is invalid") from error


__all__ = ["StripeWebhookConfig", "StripeWebhookVerifier"]
