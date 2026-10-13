from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from eva.application.commerce import (
    CommerceError,
    ProductPublicationApproval,
    publication_plan,
)
from eva.application.personal_interests import (
    PersonalProductListing,
    PersonalProductRelease,
    PersonalProductReleaseArtifact,
)


NOW = datetime(2026, 8, 24, 10, 0, tzinfo=UTC)


def _artifact() -> PersonalProductReleaseArtifact:
    return PersonalProductReleaseArtifact(
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


def _plan():  # type: ignore[no-untyped-def]
    return publication_plan(
        _artifact(),
        tenant_id="tenant:live-test",
        installation_id="installation:live-test",
        principal_id="principal:jaap",
        provider_account_id="acct_12345678",
        live_mode=False,
        created_at=NOW,
    )


def _approval(plan):  # type: ignore[no-untyped-def]
    return ProductPublicationApproval(
        approval_id="approval:publication:001",
        plan_sha256=plan.sha256,
        release_sha256=plan.release_sha256,
        provider_account_id=plan.provider_account_id,
        live_mode=plan.live_mode,
        approved_by="principal:jaap",
        approved_at=NOW,
        expires_at=NOW + timedelta(minutes=15),
    )


def test_publication_plan_is_deterministic_and_binds_release_price_and_account() -> None:
    first = _plan()
    second = _plan()

    assert first == second
    assert first.sha256 == second.sha256
    assert first.release_sha256 == _artifact().release.content_sha256
    assert first.price_eur_cents == 499
    assert first.provider_account_id == "acct_12345678"
    assert first.operation_id.startswith("commerce:publish:")
    replanned_later = publication_plan(
        _artifact(),
        tenant_id=first.tenant_id,
        installation_id=first.installation_id,
        principal_id=first.principal_id,
        provider_account_id=first.provider_account_id,
        live_mode=first.live_mode,
        created_at=NOW + timedelta(seconds=1),
    )
    assert replanned_later.operation_id != first.operation_id


def test_publication_approval_is_short_lived_and_exact() -> None:
    plan = _plan()
    approval = _approval(plan)

    approval.authorize(plan, now=NOW + timedelta(minutes=1))

    with pytest.raises(CommerceError, match="not currently valid"):
        approval.authorize(plan, now=NOW + timedelta(minutes=15))

    changed = publication_plan(
        _artifact(),
        tenant_id=plan.tenant_id,
        installation_id=plan.installation_id,
        principal_id=plan.principal_id,
        provider_account_id=plan.provider_account_id,
        live_mode=True,
        created_at=NOW,
    )
    with pytest.raises(CommerceError, match="does not bind"):
        approval.authorize(changed, now=NOW + timedelta(minutes=1))
