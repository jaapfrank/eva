from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from eva.adapters.postgres.commerce import (
    PostgresPaidOrderStore,
    PostgresProductPublicationStore,
)
from eva.application.commerce import (
    CommerceError,
    PaidOrderReceipt,
    ProductPublicationApproval,
    ProductPublicationReceipt,
    publication_plan,
)
import pytest
from eva.application.personal_interests import (
    PersonalProductListing,
    PersonalProductRelease,
    PersonalProductReleaseArtifact,
)


NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)


def _plan():  # type: ignore[no-untyped-def]
    artifact = PersonalProductReleaseArtifact(
        "intent:planner:001",
        PersonalProductRelease(
            PersonalProductListing(
                "Slim weekmenu zonder gedoe",
                "Een compacte planner voor avondeten en boodschappen.",
                499,
                ("weekmenu", "planner", "boodschappen"),
                "chatgpt_codex",
            ),
            "weekmenu-bundel.zip",
            b"PK\x03\x04bound-release",
        ),
    )
    return publication_plan(
        artifact,
        tenant_id="tenant:live-test",
        installation_id="installation:live-test",
        principal_id="principal:jaap",
        provider_account_id="acct_12345678",
        live_mode=False,
        created_at=NOW,
    )


class _Timeline:
    def __init__(self) -> None:
        self.items: list[object] = []
        self.state = None

    def events(self, _thread_id: str):  # type: ignore[no-untyped-def]
        return tuple(self.items)

    def load(self, _thread_id: str):  # type: ignore[no-untyped-def]
        return self.state

    def create_thread(self, **values):  # type: ignore[no-untyped-def]
        self.state = SimpleNamespace(
            thread_id=values["thread_id"],
            phase=values["phase"],
            status=values["status"],
            retry_count=0,
            last_error_fingerprint=None,
            revision=1,
        )
        self.items.append(
            SimpleNamespace(
                event_type=values["event_type"], payload={"data": values["data"]}
            )
        )
        return self.state

    def transition(self, **values):  # type: ignore[no-untyped-def]
        assert values["expected_revision"] == self.state.revision
        self.state = SimpleNamespace(
            thread_id=values["thread_id"],
            phase=values["phase"],
            status=values["status"],
            retry_count=values.get("retry_count", self.state.retry_count),
            last_error_fingerprint=values.get(
                "last_error_fingerprint", self.state.last_error_fingerprint
            ),
            revision=self.state.revision + 1,
        )
        self.items.append(
            SimpleNamespace(
                event_type=values["event_type"], payload={"data": values["data"]}
            )
        )
        return self.state


def test_publication_store_fences_call_before_verified_receipt() -> None:
    plan = _plan()
    approval = ProductPublicationApproval(
        "approval:publication:001",
        plan.sha256,
        plan.release_sha256,
        plan.provider_account_id,
        plan.live_mode,
        "principal:jaap",
        NOW,
        NOW + timedelta(minutes=15),
    )
    receipt = ProductPublicationReceipt(
        "stripe_payment_links",
        plan.provider_account_id,
        "plink_12345678",
        "https://buy.stripe.com/test_12345678",
        plan.sha256,
        plan.release_sha256,
        plan.price_eur_cents,
        False,
        True,
        NOW + timedelta(minutes=2),
    )
    timeline = _Timeline()
    store = object.__new__(PostgresProductPublicationStore)
    store._timeline = timeline  # type: ignore[attr-defined]

    assert store.retain_plan(plan) == "planned"
    assert store.retain_plan(plan) == "already_planned"
    assert (
        store.approve(plan, approval, now=NOW + timedelta(minutes=1)) == "approved"
    )
    assert store.open_call(
        plan_sha256=plan.sha256,
        approval_sha256=approval.sha256,
        now=NOW + timedelta(minutes=1),
    ) == "opened"
    assert store.open_call(
        plan_sha256=plan.sha256,
        approval_sha256=approval.sha256,
        now=NOW + timedelta(minutes=1),
    ) == "resume_idempotent"
    assert store.record_verified(
        plan,
        approval,
        receipt,
        now=NOW + timedelta(minutes=2),
    ) == "verified"
    assert store.record_verified(
        plan,
        approval,
        receipt,
        now=NOW + timedelta(minutes=2),
    ) == "already_verified"
    assert store.receipt(plan.sha256) == receipt
    assert [item.event_type for item in timeline.items] == [
        "PRODUCT_PUBLICATION_PLANNED",
        "PRODUCT_PUBLICATION_APPROVED",
        "PRODUCT_PUBLICATION_CALL_OPENED",
        "PRODUCT_PUBLICATION_VERIFIED",
    ]


def test_paid_order_store_records_checkout_session_exactly_once() -> None:
    plan = _plan()
    publication = ProductPublicationReceipt(
        "stripe_payment_links",
        plan.provider_account_id,
        "plink_12345678",
        "https://buy.stripe.com/test_12345678",
        plan.sha256,
        plan.release_sha256,
        499,
        False,
        True,
        NOW,
    )
    first = PaidOrderReceipt(
        "stripe_checkout",
        "evt_completed1",
        "cs_test_12345678",
        publication.provider_resource_id,
        plan.sha256,
        plan.release_sha256,
        499,
        False,
        "c" * 64,
        NOW,
    )
    duplicate = PaidOrderReceipt(
        "stripe_checkout",
        "evt_asyncpaid1",
        first.checkout_session_id,
        first.provider_resource_id,
        first.plan_sha256,
        first.release_sha256,
        first.amount_eur_cents,
        first.live_mode,
        None,
        first.paid_at + timedelta(seconds=1),
    )
    timeline = _Timeline()
    store = object.__new__(PostgresPaidOrderStore)
    store._timeline = timeline  # type: ignore[attr-defined]

    assert store.retain_paid(first, publication) == "recorded"
    assert store.retain_paid(duplicate, publication) == "already_recorded"
    assert store.load(first.checkout_session_id) == first
    assert [item.event_type for item in timeline.items] == ["PAYMENT_VERIFIED"]

    disposition, grant = store.grant_fulfillment(
        checkout_session_id=first.checkout_session_id,
        release_sha256=first.release_sha256,
        filename="weekmenu-bundel.zip",
        byte_length=512,
        now=NOW + timedelta(minutes=1),
    )
    repeated, same_grant = store.grant_fulfillment(
        checkout_session_id=first.checkout_session_id,
        release_sha256=first.release_sha256,
        filename="weekmenu-bundel.zip",
        byte_length=512,
        now=NOW + timedelta(minutes=2),
    )
    assert disposition == "granted"
    assert repeated == "already_granted"
    assert same_grant == grant
    assert [item.event_type for item in timeline.items] == [
        "PAYMENT_VERIFIED",
        "FULFILLMENT_GRANTED",
    ]

    wrong_amount = PaidOrderReceipt(
        "stripe_checkout",
        "evt_wrongamt1",
        "cs_test_87654321",
        first.provider_resource_id,
        first.plan_sha256,
        first.release_sha256,
        599,
        False,
        None,
        NOW,
    )
    with pytest.raises(CommerceError, match="verified publication"):
        store.retain_paid(wrong_amount, publication)
