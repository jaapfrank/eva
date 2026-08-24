from __future__ import annotations

from fastapi.testclient import TestClient

import eva.adapters.api.app as api_app
from eva.adapters.api.app import RuntimeHealth, create_app
from eva.application.conversation import load_current_identity_pack
from eva.contracts.canonical_json import canonical_sha256


class _Runtime:
    def health(self) -> RuntimeHealth:
        return RuntimeHealth(
            ready=False,
            database="unavailable",
            memory_projection="degraded",
            detail="database password=not-a-real-secret must not be exposed",
            model_provider="unavailable",
        )


def test_dashboard_status_is_read_only_and_non_secret() -> None:
    response = TestClient(create_app(_Runtime())).get("/internal/v1/dashboard/status")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "contract": "EvaDashboardStatus/v1",
        "read_only": True,
        "runtime": {
            "ready": False,
            "state": "not_ready",
        },
    }
    assert "not-a-real-secret" not in response.text


def test_dashboard_page_has_only_the_read_only_status_surface() -> None:
    response = TestClient(create_app(_Runtime())).get("/internal/v1/dashboard")

    assert response.status_code == 200
    assert "dashboard/status" in response.text
    assert "dashboard/identity" in response.text
    assert "<form" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "connect-src 'self'" in response.headers["content-security-policy"]


def test_dashboard_identity_exposes_only_verified_operator_fields() -> None:
    identity = load_current_identity_pack()

    response = TestClient(create_app(_Runtime())).get("/internal/v1/dashboard/identity")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "contract": "EvaDashboardIdentity/v1",
        "read_only": True,
        "identity": {
            "display_name": identity.display_name,
            "identity_id": identity.identity_id,
            "language": identity.language,
            "pack_sha256": canonical_sha256(identity),
            "pack_version": identity.pack_version,
            "state": "current",
            "temperament": identity.temperament,
            "voice_style": {
                "maximum_paragraphs": identity.voice_style.maximum_paragraphs,
                "name": identity.voice_style.name,
                "qualities": identity.voice_style.qualities,
            },
        },
    }
    assert "signature" not in response.text
    assert "source_sha256" not in response.text


def test_dashboard_identity_fails_closed_without_exposing_verifier_detail(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    def unavailable():  # type: ignore[no-untyped-def]
        raise ValueError("identity signature contains private diagnostic detail")

    monkeypatch.setattr(api_app, "load_current_identity_pack", unavailable)

    response = TestClient(create_app(_Runtime())).get("/internal/v1/dashboard/identity")

    assert response.status_code == 503
    assert response.json() == {"detail": "active identity unavailable"}
    assert "private diagnostic detail" not in response.text


def test_dashboard_identity_rejects_a_verified_but_noncurrent_pack(
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    staged = load_current_identity_pack().model_copy(update={"state": "staged"})
    monkeypatch.setattr(api_app, "load_current_identity_pack", lambda: staged)

    response = TestClient(create_app(_Runtime())).get("/internal/v1/dashboard/identity")

    assert response.status_code == 503
    assert response.json() == {"detail": "active identity unavailable"}
