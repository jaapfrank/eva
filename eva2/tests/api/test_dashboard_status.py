from __future__ import annotations

from fastapi.testclient import TestClient

from eva.adapters.api.app import RuntimeHealth, create_app


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
    assert "<form" not in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "connect-src 'self'" in response.headers["content-security-policy"]
