from __future__ import annotations

from fastapi.testclient import TestClient

from eva.adapters.api.installer_wizard import InstallerBootstrap, create_installer_wizard


def _receipt() -> dict[str, object]:
    return {
        "contract": "EvaInstallerPreflight/v1",
        "read_only": True,
        "status": "blocked",
        "platform_lock_claim_state": "selected_for_validation",
        "host": {"operating_system": "Ubuntu", "architecture": "amd64", "python_version": "3.12.3", "docker_version": "29.1.3"},
        "checks": [{"id": "release_support", "state": "blocked", "detail": "Nog niet bewezen"}],
        "next": "Er is niets geïnstalleerd of gewijzigd. Los eerst de geblokkeerde controle op.",
        "non_claims": ["geen wijziging"],
    }


def _client(bootstrap: InstallerBootstrap) -> TestClient:
    return TestClient(create_installer_wizard(_receipt(), bootstrap), headers={"host": "127.0.0.1"}, client=("127.0.0.1", 40000))


def test_wizard_requires_one_time_bootstrap_before_any_read() -> None:
    bootstrap = InstallerBootstrap(token="t" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(bootstrap)

    denied = client.get("/")
    assert denied.status_code == 401
    assert denied.headers["cache-control"] == "no-store"
    assert "t" * 43 not in denied.text

    start = client.get("/bootstrap")
    assert start.status_code == 200
    assert "location.hash" in start.text
    assert "t" * 43 not in start.text

    exchange = client.post("/internal/v1/bootstrap", json={"token": "t" * 43}, headers={"origin": "http://127.0.0.1"})
    assert exchange.status_code == 204
    assert "httponly" in exchange.headers["set-cookie"].casefold()
    assert "samesite=strict" in exchange.headers["set-cookie"].casefold()

    page = client.get("/")
    assert page.status_code == 200
    assert "Voordat Eva iets installeert" in page.text
    assert "geen connector-, secret- of installatieknop" in page.text
    assert "Nog niet bewezen" in page.text

    report = client.get("/internal/v1/preflight")
    assert report.status_code == 200
    assert report.json()["status"] == "blocked"
    assert report.headers["content-security-policy"].startswith("default-src 'none'")


def test_wizard_rejects_reused_wrong_or_cross_origin_bootstrap() -> None:
    bootstrap = InstallerBootstrap(token="u" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(bootstrap)

    assert client.post("/internal/v1/bootstrap", json={"token": "wrong"}).status_code == 401
    assert client.post("/internal/v1/bootstrap", json={"token": "u" * 43}, headers={"origin": "http://evil.invalid"}).status_code == 403
    assert client.post("/internal/v1/bootstrap", json={"token": "u" * 43}).status_code == 204
    assert client.post("/internal/v1/bootstrap", json={"token": "u" * 43}).status_code == 401


def test_wizard_refuses_non_loopback_host_or_client() -> None:
    bootstrap = InstallerBootstrap(token="v" * 43, created_at=1.0, clock=lambda: 1.0)
    app = create_installer_wizard(_receipt(), bootstrap)
    wrong_host = TestClient(app, headers={"host": "example.invalid"}, client=("127.0.0.1", 40000))
    wrong_client = TestClient(app, headers={"host": "127.0.0.1"}, client=("10.0.0.2", 40000))

    assert wrong_host.get("/bootstrap").status_code == 400
    assert wrong_client.get("/bootstrap").status_code == 400


def test_wizard_accepts_ipv6_loopback() -> None:
    bootstrap = InstallerBootstrap(token="i" * 43, created_at=1.0, clock=lambda: 1.0)
    client = TestClient(
        create_installer_wizard(_receipt(), bootstrap),
        headers={"host": "[::1]"},
        client=("::1", 40000),
    )

    assert client.get("/bootstrap").status_code == 200


def test_wizard_previews_soul_markdown_without_storing_or_activating_it() -> None:
    bootstrap = InstallerBootstrap(token="s" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(bootstrap)
    assert client.post("/internal/v1/bootstrap", json={"token": "s" * 43}).status_code == 204

    preview = client.post(
        "/internal/v1/identity/soul-import/diff",
        json={"draft_id": "installer-preview", "soul_markdown": "# Eva\n\n## Naam\nNoor\n\n## Temperament\nwarm, nieuwsgierig"},
        headers={"origin": "http://127.0.0.1"},
    )

    assert preview.status_code == 200
    assert preview.headers["cache-control"] == "no-store"
    assert preview.json()["contract"] == "EvaInstallerSoulMarkdownPreview/v1"
    assert preview.json()["read_only"] is True
    assert preview.json()["changes"] == [
        {"field": "display_name", "before": "Eva", "after": "Noor"},
        {
            "field": "temperament",
            "before": ["warm", "curious", "slightly_stubborn"],
            "after": ["warm", "nieuwsgierig"],
        },
    ]
    assert preview.json()["non_claims"] == [
        "SOUL.md is niet opgeslagen of geactiveerd.",
        "SOUL.md kan geen prompt, tool, policy, geheugen of permissie wijzigen.",
    ]

    denied = client.post(
        "/internal/v1/identity/soul-import/diff",
        json={"draft_id": "installer-preview", "soul_markdown": "## Naam\nNoor"},
        headers={"origin": "http://evil.invalid"},
    )
    assert denied.status_code == 403
