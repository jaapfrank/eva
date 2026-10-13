from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import json

import pytest

from eva.adapters.connectors.stripe_webhook import (
    StripeWebhookConfig,
    StripeWebhookVerifier,
)
from eva.application.commerce import CommerceError


NOW = datetime(2026, 8, 24, 14, 0, tzinfo=UTC)
SECRET = "whsec_test_signing_secret_12345678"
PLAN = "a" * 64
RELEASE = "b" * 64


def _payload(
    *,
    event_id: str = "evt_12345678",
    event_type: str = "checkout.session.completed",
    amount: int = 499,
    payment_status: str = "paid",
    live_mode: bool = False,
    event_created_offset: int = -2,
    include_email: bool = True,
) -> bytes:
    return json.dumps(
        {
            "id": event_id,
            "type": event_type,
            "created": int(NOW.timestamp()) + event_created_offset,
            "livemode": live_mode,
            "data": {
                "object": {
                    "object": "checkout.session",
                    "id": "cs_test_12345678",
                    "created": int(NOW.timestamp()) - 5,
                    "status": "complete",
                    "payment_status": payment_status,
                    "currency": "eur",
                    "amount_total": amount,
                    "payment_link": "plink_12345678",
                    "metadata": {
                        "eva_plan_sha256": PLAN,
                        "eva_release_sha256": RELEASE,
                    },
                    "customer_details": (
                        {"email": " Buyer@Example.COM "} if include_email else {}
                    ),
                }
            },
        },
        separators=(",", ":"),
    ).encode()


def _signature(payload: bytes, *, when: datetime = NOW, secret: str = SECRET) -> str:
    timestamp = int(when.timestamp())
    digest = hmac.new(
        secret.encode(),
        str(timestamp).encode() + b"." + payload,
        hashlib.sha256,
    ).hexdigest()
    return f"t={timestamp},v1={digest}"


def test_signed_paid_checkout_becomes_privacy_bounded_receipt() -> None:
    payload = _payload()
    receipt = StripeWebhookVerifier(
        StripeWebhookConfig(signing_secret=SECRET)
    ).verify(payload, _signature(payload), now=NOW)

    assert receipt.checkout_session_id == "cs_test_12345678"
    assert receipt.provider_resource_id == "plink_12345678"
    assert receipt.amount_eur_cents == 499
    assert receipt.plan_sha256 == PLAN
    assert receipt.buyer_email_sha256 == hashlib.sha256(
        b"buyer@example.com"
    ).hexdigest()
    assert "Buyer@Example.COM" not in json.dumps(receipt.to_payload())


@pytest.mark.parametrize(
    ("payload", "signature", "message"),
    [
        (
            _payload(),
            f"t={int(NOW.timestamp())},v1=" + "0" * 64,
            "signature is invalid",
        ),
        (
            _payload(),
            _signature(_payload(), when=NOW - timedelta(minutes=10)),
            "timestamp is stale",
        ),
        (_payload(payment_status="unpaid"), None, "not proven paid"),
        (_payload(amount=250), None, "paid-order receipt is invalid"),
        (_payload(live_mode=True), None, "not an admitted paid checkout"),
    ],
)
def test_webhook_fails_closed_without_exact_current_payment_proof(
    payload: bytes, signature: str | None, message: str
) -> None:
    verifier = StripeWebhookVerifier(StripeWebhookConfig(signing_secret=SECRET))
    actual_signature = signature if signature is not None else _signature(payload)
    with pytest.raises(CommerceError, match=message):
        verifier.verify(payload, actual_signature, now=NOW)


def test_second_admitted_event_for_same_session_has_stable_payment_binding() -> None:
    verifier = StripeWebhookVerifier(StripeWebhookConfig(signing_secret=SECRET))
    completed = _payload(event_id="evt_completed1")
    asynchronous = _payload(
        event_id="evt_asyncpaid1",
        event_type="checkout.session.async_payment_succeeded",
        event_created_offset=-1,
        include_email=False,
    )

    first = verifier.verify(completed, _signature(completed), now=NOW)
    second = verifier.verify(asynchronous, _signature(asynchronous), now=NOW)

    assert first.provider_event_id != second.provider_event_id
    assert first.paid_at != second.paid_at
    assert first.checkout_session_id == second.checkout_session_id
