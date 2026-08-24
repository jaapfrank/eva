"""Channel-neutral authority and receipts for Eva's commercial publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import re

from eva.application.personal_interests import PersonalProductReleaseArtifact
from eva.contracts.canonical_json import canonical_sha256


_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:._/@+\-=]{0,239}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_STRIPE_ACCOUNT = re.compile(r"^acct_[A-Za-z0-9]{8,64}$")


class CommerceError(RuntimeError):
    """A fail-closed commercial publication error."""


def _require_identifier(value: str, field: str) -> None:
    if not isinstance(value, str) or _IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{field} is invalid")


def _timestamp(value: datetime, field: str) -> str:
    if value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise ValueError(f"{field} must be UTC")
    if value.microsecond:
        raise ValueError(f"{field} must have second precision")
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True, slots=True)
class ProductPublicationPlan:
    operation_id: str
    tenant_id: str
    installation_id: str
    principal_id: str
    release_intent_id: str
    release_sha256: str
    headline: str
    description: str
    price_eur_cents: int
    provider: str
    provider_account_id: str
    live_mode: bool
    created_at: datetime

    def __post_init__(self) -> None:
        for field in (
            "operation_id",
            "tenant_id",
            "installation_id",
            "principal_id",
            "release_intent_id",
        ):
            _require_identifier(getattr(self, field), field)
        if _SHA256.fullmatch(self.release_sha256) is None:
            raise ValueError("release_sha256 is invalid")
        if self.provider != "stripe_payment_links":
            raise ValueError("publication provider is invalid")
        if _STRIPE_ACCOUNT.fullmatch(self.provider_account_id) is None:
            raise ValueError("Stripe account ID is invalid")
        if not isinstance(self.live_mode, bool):
            raise ValueError("publication mode is invalid")
        if (
            not self.headline.strip()
            or len(self.headline) > 120
            or not self.description.strip()
            or len(self.description) > 2_000
            or not 299 <= self.price_eur_cents <= 1_999
        ):
            raise ValueError("publication listing is invalid")
        _timestamp(self.created_at, "created_at")

    def to_payload(self) -> dict[str, object]:
        return {
            "contract": "EvaProductPublicationPlan/v1",
            "created_at": _timestamp(self.created_at, "created_at"),
            "description": self.description,
            "headline": self.headline,
            "installation_id": self.installation_id,
            "live_mode": self.live_mode,
            "operation_id": self.operation_id,
            "price_currency": "EUR",
            "price_minor_units": self.price_eur_cents,
            "principal_id": self.principal_id,
            "provider": self.provider,
            "provider_account_id": self.provider_account_id,
            "release_intent_id": self.release_intent_id,
            "release_sha256": self.release_sha256,
            "tenant_id": self.tenant_id,
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())


@dataclass(frozen=True, slots=True)
class ProductPublicationApproval:
    approval_id: str
    plan_sha256: str
    release_sha256: str
    provider_account_id: str
    live_mode: bool
    approved_by: str
    approved_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        _require_identifier(self.approval_id, "approval_id")
        _require_identifier(self.approved_by, "approved_by")
        if (
            _SHA256.fullmatch(self.plan_sha256) is None
            or _SHA256.fullmatch(self.release_sha256) is None
        ):
            raise ValueError("publication approval hash is invalid")
        if _STRIPE_ACCOUNT.fullmatch(self.provider_account_id) is None:
            raise ValueError("publication approval account is invalid")
        if not isinstance(self.live_mode, bool):
            raise ValueError("publication approval mode is invalid")
        _timestamp(self.approved_at, "approved_at")
        _timestamp(self.expires_at, "expires_at")
        if self.expires_at <= self.approved_at:
            raise ValueError("publication approval expiry is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "approval_id": self.approval_id,
            "approved_at": _timestamp(self.approved_at, "approved_at"),
            "approved_by": self.approved_by,
            "contract": "EvaProductPublicationApproval/v1",
            "expires_at": _timestamp(self.expires_at, "expires_at"),
            "live_mode": self.live_mode,
            "plan_sha256": self.plan_sha256,
            "provider_account_id": self.provider_account_id,
            "release_sha256": self.release_sha256,
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())

    def authorize(self, plan: ProductPublicationPlan, *, now: datetime) -> None:
        _timestamp(now, "authorization time")
        if now < self.approved_at or now >= self.expires_at:
            raise CommerceError("publication approval is not currently valid")
        if (
            self.plan_sha256 != plan.sha256
            or self.release_sha256 != plan.release_sha256
            or self.provider_account_id != plan.provider_account_id
            or self.live_mode is not plan.live_mode
        ):
            raise CommerceError("publication approval does not bind this plan")


@dataclass(frozen=True, slots=True)
class ProductPublicationReceipt:
    provider: str
    provider_account_id: str
    provider_resource_id: str
    public_url: str
    plan_sha256: str
    release_sha256: str
    price_eur_cents: int
    live_mode: bool
    active: bool
    observed_at: datetime

    def __post_init__(self) -> None:
        if self.provider != "stripe_payment_links":
            raise ValueError("publication receipt provider is invalid")
        if _STRIPE_ACCOUNT.fullmatch(self.provider_account_id) is None:
            raise ValueError("publication receipt account is invalid")
        if not isinstance(self.live_mode, bool) or not isinstance(self.active, bool):
            raise ValueError("publication receipt state is invalid")
        if not re.fullmatch(r"plink_[A-Za-z0-9]{8,128}", self.provider_resource_id):
            raise ValueError("publication receipt resource is invalid")
        if (
            not self.public_url.startswith("https://buy.stripe.com/")
            or _SHA256.fullmatch(self.plan_sha256) is None
            or _SHA256.fullmatch(self.release_sha256) is None
            or not 299 <= self.price_eur_cents <= 1_999
        ):
            raise ValueError("publication receipt content is invalid")
        _timestamp(self.observed_at, "observed_at")

    def to_payload(self) -> dict[str, object]:
        return {
            "active": self.active,
            "contract": "EvaProductPublicationReceipt/v1",
            "live_mode": self.live_mode,
            "observed_at": _timestamp(self.observed_at, "observed_at"),
            "plan_sha256": self.plan_sha256,
            "price_currency": "EUR",
            "price_minor_units": self.price_eur_cents,
            "provider": self.provider,
            "provider_account_id": self.provider_account_id,
            "provider_resource_id": self.provider_resource_id,
            "public_url": self.public_url,
            "release_sha256": self.release_sha256,
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())


@dataclass(frozen=True, slots=True)
class PaidOrderReceipt:
    provider: str
    provider_event_id: str
    checkout_session_id: str
    provider_resource_id: str
    plan_sha256: str
    release_sha256: str
    amount_eur_cents: int
    live_mode: bool
    buyer_email_sha256: str | None
    paid_at: datetime

    def __post_init__(self) -> None:
        if self.provider != "stripe_checkout":
            raise ValueError("paid order provider is invalid")
        if not re.fullmatch(r"evt_[A-Za-z0-9]{8,128}", self.provider_event_id):
            raise ValueError("paid order event ID is invalid")
        if not re.fullmatch(r"cs_(?:test_|live_)?[A-Za-z0-9]{8,160}", self.checkout_session_id):
            raise ValueError("paid order session ID is invalid")
        if not re.fullmatch(r"plink_[A-Za-z0-9]{8,128}", self.provider_resource_id):
            raise ValueError("paid order Payment Link ID is invalid")
        if (
            _SHA256.fullmatch(self.plan_sha256) is None
            or _SHA256.fullmatch(self.release_sha256) is None
            or self.buyer_email_sha256 is not None
            and _SHA256.fullmatch(self.buyer_email_sha256) is None
            or not 299 <= self.amount_eur_cents <= 1_999
            or not isinstance(self.live_mode, bool)
        ):
            raise ValueError("paid order binding is invalid")
        _timestamp(self.paid_at, "paid_at")

    def to_payload(self) -> dict[str, object]:
        return {
            "amount_currency": "EUR",
            "amount_minor_units": self.amount_eur_cents,
            "buyer_email_sha256": self.buyer_email_sha256,
            "checkout_session_id": self.checkout_session_id,
            "contract": "EvaPaidOrderReceipt/v1",
            "live_mode": self.live_mode,
            "paid_at": _timestamp(self.paid_at, "paid_at"),
            "plan_sha256": self.plan_sha256,
            "provider": self.provider,
            "provider_event_id": self.provider_event_id,
            "provider_resource_id": self.provider_resource_id,
            "release_sha256": self.release_sha256,
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())


@dataclass(frozen=True, slots=True)
class DigitalFulfillmentGrant:
    checkout_session_id: str
    paid_order_sha256: str
    release_sha256: str
    filename: str
    byte_length: int
    granted_at: datetime

    def __post_init__(self) -> None:
        if not re.fullmatch(
            r"cs_(?:test_|live_)?[A-Za-z0-9]{8,160}", self.checkout_session_id
        ):
            raise ValueError("fulfillment session ID is invalid")
        if (
            _SHA256.fullmatch(self.paid_order_sha256) is None
            or _SHA256.fullmatch(self.release_sha256) is None
            or not self.filename.endswith(".zip")
            or "/" in self.filename
            or "\\" in self.filename
            or len(self.filename) > 120
            or not 1 <= self.byte_length <= 524_288
        ):
            raise ValueError("fulfillment artifact binding is invalid")
        _timestamp(self.granted_at, "granted_at")

    def to_payload(self) -> dict[str, object]:
        return {
            "byte_length": self.byte_length,
            "checkout_session_id": self.checkout_session_id,
            "contract": "EvaDigitalFulfillmentGrant/v1",
            "filename": self.filename,
            "granted_at": _timestamp(self.granted_at, "granted_at"),
            "media_type": "application/zip",
            "paid_order_sha256": self.paid_order_sha256,
            "release_sha256": self.release_sha256,
        }

    @property
    def sha256(self) -> str:
        return canonical_sha256(self.to_payload())


@dataclass(frozen=True, slots=True)
class DigitalFulfillmentDownload:
    content: bytes
    grant: DigitalFulfillmentGrant

    def __post_init__(self) -> None:
        if (
            not isinstance(self.content, bytes)
            or len(self.content) != self.grant.byte_length
            or hashlib.sha256(self.content).hexdigest() != self.grant.release_sha256
        ):
            raise ValueError("fulfillment download differs from durable grant")


def publication_plan(
    artifact: PersonalProductReleaseArtifact,
    *,
    tenant_id: str,
    installation_id: str,
    principal_id: str,
    provider_account_id: str,
    live_mode: bool,
    created_at: datetime,
) -> ProductPublicationPlan:
    release = artifact.release
    created = _timestamp(created_at, "created_at")
    operation_id = "commerce:publish:" + hashlib.sha256(
        (
            f"{provider_account_id}\0{artifact.intent_id}\0"
            f"{release.content_sha256}\0{release.listing.price_eur_cents}\0"
            f"{live_mode}\0{created}"
        ).encode("utf-8")
    ).hexdigest()
    return ProductPublicationPlan(
        operation_id=operation_id,
        tenant_id=tenant_id,
        installation_id=installation_id,
        principal_id=principal_id,
        release_intent_id=artifact.intent_id,
        release_sha256=release.content_sha256,
        headline=release.listing.headline,
        description=release.listing.description,
        price_eur_cents=release.listing.price_eur_cents,
        provider="stripe_payment_links",
        provider_account_id=provider_account_id,
        live_mode=live_mode,
        created_at=created_at,
    )


__all__ = [
    "CommerceError",
    "PaidOrderReceipt",
    "ProductPublicationApproval",
    "ProductPublicationPlan",
    "ProductPublicationReceipt",
    "publication_plan",
]
