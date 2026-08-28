from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from eva.adapters.api.installer_wizard import (
    InstallerBootstrap,
    create_installer_wizard,
)
from eva.application.conversation import load_current_identity_pack
from eva.application.soul_markdown import soul_markdown_to_draft
from eva.operations.identity_pack_lifecycle import bootstrap_identity_authority
from eva.operations.installer_identity_lifecycle import InstallerIdentityLifecycle


def _receipt(*, status: str = "blocked") -> dict[str, object]:
    return {
        "contract": "EvaInstallerPreflight/v1",
        "read_only": True,
        "status": status,
        "platform_lock_claim_state": "selected_for_validation",
        "host": {
            "operating_system": "Ubuntu",
            "architecture": "amd64",
            "python_version": "3.12.3",
            "docker_version": "29.1.3",
        },
        "checks": [
            {"id": "release_support", "state": "blocked", "detail": "Nog niet bewezen"}
        ],
        "next": "Er is niets geïnstalleerd of gewijzigd. Los eerst de geblokkeerde controle op.",
        "non_claims": ["geen wijziging"],
    }


def _client(
    bootstrap: InstallerBootstrap,
    *,
    receipt: dict[str, object] | None = None,
    identity_lifecycle: InstallerIdentityLifecycle | None = None,
) -> TestClient:
    return TestClient(
        create_installer_wizard(
            receipt or _receipt(),
            bootstrap,
            identity_lifecycle=identity_lifecycle,
        ),
        headers={"host": "127.0.0.1"},
        client=("127.0.0.1", 40000),
    )


def _identity_lifecycle(root: Path) -> InstallerIdentityLifecycle:
    template = load_current_identity_pack()
    authority = root / "authority"
    bootstrap_identity_authority(
        output_directory=authority,
        template=template,
        draft=soul_markdown_to_draft(
            "## Naam\nEvi", draft_id="installer-api-bootstrap", base=template
        ),
        identity_id="identity:installer:api-test",
        issuer="issuer:installer:api-test",
        issued_at="2026-08-29T01:10:00Z",
    )
    workspace = root / "identity-workspace"
    workspace.mkdir(mode=0o700)
    return InstallerIdentityLifecycle(
        current_path=authority / "current.json",
        trust_path=authority / "trust.json",
        private_key_path=authority / "private-key.bin",
        workspace=workspace,
        clock=lambda: "2026-08-29T01:11:00Z",
    )


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

    exchange = client.post(
        "/internal/v1/bootstrap",
        json={"token": "t" * 43},
        headers={"origin": "http://127.0.0.1"},
    )
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
    identity_status = client.get("/internal/v1/identity/status")
    assert identity_status.status_code == 200
    assert identity_status.json() == {
        "contract": "EvaInstallerIdentityWorkflowStatus/v1",
        "enabled": False,
        "phase": "unavailable",
        "base_pack_sha256": None,
        "current_pack_sha256": None,
        "draft_sha256": None,
        "candidate_sha256": None,
        "updated_at": None,
    }


def test_wizard_rejects_reused_wrong_or_cross_origin_bootstrap() -> None:
    bootstrap = InstallerBootstrap(token="u" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(bootstrap)

    assert (
        client.post("/internal/v1/bootstrap", json={"token": "wrong"}).status_code
        == 401
    )
    assert (
        client.post(
            "/internal/v1/bootstrap",
            json={"token": "u" * 43},
            headers={"origin": "http://evil.invalid"},
        ).status_code
        == 403
    )
    assert (
        client.post("/internal/v1/bootstrap", json={"token": "u" * 43}).status_code
        == 204
    )
    assert (
        client.post("/internal/v1/bootstrap", json={"token": "u" * 43}).status_code
        == 401
    )


def test_wizard_session_expires_on_the_server() -> None:
    now = [1.0]
    bootstrap = InstallerBootstrap(
        token="e" * 43, created_at=now[0], clock=lambda: now[0]
    )
    client = _client(bootstrap)
    assert (
        client.post("/internal/v1/bootstrap", json={"token": "e" * 43}).status_code
        == 204
    )
    assert client.get("/").status_code == 200
    now[0] += 601
    assert client.get("/").status_code == 401


def test_wizard_refuses_non_loopback_host_or_client() -> None:
    bootstrap = InstallerBootstrap(token="v" * 43, created_at=1.0, clock=lambda: 1.0)
    app = create_installer_wizard(_receipt(), bootstrap)
    wrong_host = TestClient(
        app, headers={"host": "example.invalid"}, client=("127.0.0.1", 40000)
    )
    wrong_client = TestClient(
        app, headers={"host": "127.0.0.1"}, client=("10.0.0.2", 40000)
    )

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
    assert (
        client.post("/internal/v1/bootstrap", json={"token": "s" * 43}).status_code
        == 204
    )

    preview = client.post(
        "/internal/v1/identity/soul-import/diff",
        json={
            "draft_id": "installer-preview",
            "soul_markdown": "# Eva\n\n## Naam\nNoor\n\n## Temperament\nwarm, nieuwsgierig",
        },
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


def test_wizard_identity_lifecycle_requires_ready_preflight(
    private_tmp_path: Path,
) -> None:
    lifecycle = _identity_lifecycle(private_tmp_path)
    bootstrap = InstallerBootstrap(token="b" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(bootstrap, identity_lifecycle=lifecycle)
    assert (
        client.post("/internal/v1/bootstrap", json={"token": "b" * 43}).status_code
        == 204
    )
    page = client.get("/")
    assert "alleen voorvertonen" in page.text
    assert 'id="soul-stage"' not in page.text

    denied = client.post(
        "/internal/v1/identity/soul-import/stage",
        json={
            "draft_id": "installer-preview",
            "soul_markdown": "## Naam\nNoor",
            "expected_base_sha256": "0" * 64,
        },
        headers={"origin": "http://127.0.0.1"},
    )
    assert denied.status_code == 409
    assert list(lifecycle.workspace.iterdir()) == []


def test_wizard_stages_signs_activates_and_rolls_back_without_exposing_key(
    private_tmp_path: Path,
) -> None:
    lifecycle = _identity_lifecycle(private_tmp_path)
    bootstrap = InstallerBootstrap(token="w" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(
        bootstrap,
        receipt=_receipt(status="ready"),
        identity_lifecycle=lifecycle,
    )
    assert (
        client.post("/internal/v1/bootstrap", json={"token": "w" * 43}).status_code
        == 204
    )
    assert "1. Bewaar voorstel" in client.get("/").text
    exported = client.get("/internal/v1/identity/soul-export")
    assert exported.status_code == 200
    assert exported.headers["content-disposition"] == 'attachment; filename="SOUL.md"'
    assert "## Naam\nEvi" in exported.text
    assert "identity:installer" not in exported.text
    assert "signature" not in exported.text
    markdown = (
        "# Noor\n\n## Naam\nNoor\n\n## Humor\nlicht en speels\n\n"
        "## Temperament\nwarm, nieuwsgierig, eigenwijs"
    )
    origin = {"origin": "http://127.0.0.1"}
    preview = client.post(
        "/internal/v1/identity/soul-import/diff",
        json={"draft_id": "installer-preview", "soul_markdown": markdown},
        headers=origin,
    )
    assert preview.status_code == 200
    base_sha = preview.json()["base_pack_sha256"]

    staged = client.post(
        "/internal/v1/identity/soul-import/stage",
        json={
            "draft_id": "installer-preview",
            "soul_markdown": markdown,
            "expected_base_sha256": base_sha,
        },
        headers=origin,
    )
    assert staged.status_code == 200
    assert staged.json()["status"] == "staged"
    assert staged.json()["active"] is False

    unconfirmed = client.post(
        "/internal/v1/identity/staged/issue",
        json={
            "draft_sha256": staged.json()["draft_sha256"],
            "expected_base_sha256": base_sha,
            "confirmation": "ja",
        },
        headers=origin,
    )
    assert unconfirmed.status_code == 409
    issued = client.post(
        "/internal/v1/identity/staged/issue",
        json={
            "draft_sha256": staged.json()["draft_sha256"],
            "expected_base_sha256": base_sha,
            "confirmation": "ONDERTEKENEN",
        },
        headers=origin,
    )
    assert issued.status_code == 200
    assert issued.json()["status"] == "issued"
    assert issued.json()["active"] is False
    candidate_sha = issued.json()["candidate_sha256"]

    cross_origin = client.post(
        "/internal/v1/identity/candidate/activate",
        json={
            "candidate_sha256": candidate_sha,
            "expected_current_sha256": base_sha,
            "confirmation": "ACTIVEREN",
        },
        headers={"origin": "http://evil.invalid"},
    )
    assert cross_origin.status_code == 403
    activated = client.post(
        "/internal/v1/identity/candidate/activate",
        json={
            "candidate_sha256": candidate_sha,
            "expected_current_sha256": base_sha,
            "confirmation": "ACTIVEREN",
        },
        headers=origin,
    )
    assert activated.status_code == 200
    assert activated.json()["status"] == "activated"
    assert lifecycle.current_identity().display_name == "Noor"
    assert "Hoe Noor zich aan jou voorstelt" in client.get("/").text

    rolled_back = client.post(
        "/internal/v1/identity/rollback",
        json={
            "expected_current_sha256": activated.json()["current_sha256"],
            "confirmation": "TERUGZETTEN",
        },
        headers=origin,
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["status"] == "rolled_back"
    assert lifecycle.current_identity().display_name == "Evi"
    assert client.get("/").status_code == 401

    combined_output = (
        staged.text + issued.text + activated.text + rolled_back.text
    ).encode()
    assert (lifecycle.private_key_path.read_bytes()) not in combined_output
    assert b"Noor" not in combined_output


def test_wizard_resumes_identity_workflow_after_server_and_session_restart(
    private_tmp_path: Path,
) -> None:
    lifecycle = _identity_lifecycle(private_tmp_path)
    origin = {"origin": "http://127.0.0.1"}
    first_bootstrap = InstallerBootstrap(
        token="r" * 43, created_at=1.0, clock=lambda: 1.0
    )
    first = _client(
        first_bootstrap,
        receipt=_receipt(status="ready"),
        identity_lifecycle=lifecycle,
    )
    assert (
        first.post("/internal/v1/bootstrap", json={"token": "r" * 43}).status_code
        == 204
    )
    base_sha = first.get("/internal/v1/identity/status").json()["base_pack_sha256"]
    staged = first.post(
        "/internal/v1/identity/soul-import/stage",
        json={
            "draft_id": "installer-resume",
            "soul_markdown": "## Naam\nNoor",
            "expected_base_sha256": base_sha,
        },
        headers=origin,
    )
    assert staged.status_code == 200

    second_bootstrap = InstallerBootstrap(
        token="s" * 43, created_at=2.0, clock=lambda: 2.0
    )
    second = _client(
        second_bootstrap,
        receipt=_receipt(status="ready"),
        identity_lifecycle=InstallerIdentityLifecycle(
            current_path=lifecycle.current_path,
            trust_path=lifecycle.trust_path,
            private_key_path=lifecycle.private_key_path,
            workspace=lifecycle.workspace,
            clock=lambda: "2026-08-29T01:12:00Z",
        ),
    )
    assert (
        second.post("/internal/v1/bootstrap", json={"token": "s" * 43}).status_code
        == 204
    )
    staged_status = second.get("/internal/v1/identity/status").json()
    assert staged_status["phase"] == "staged"
    assert staged_status["draft_sha256"] == staged.json()["draft_sha256"]
    staged_page = second.get("/")
    assert "Voorstel teruggevonden. Je kunt nu ondertekenen." in staged_page.text
    assert "issueButton.disabled = false" in staged_page.text
    issued = second.post(
        "/internal/v1/identity/staged/issue",
        json={
            "draft_sha256": staged_status["draft_sha256"],
            "expected_base_sha256": staged_status["base_pack_sha256"],
            "confirmation": "ONDERTEKENEN",
        },
        headers=origin,
    )
    assert issued.status_code == 200

    third_bootstrap = InstallerBootstrap(
        token="v" * 43, created_at=3.0, clock=lambda: 3.0
    )
    third = _client(
        third_bootstrap,
        receipt=_receipt(status="ready"),
        identity_lifecycle=InstallerIdentityLifecycle(
            current_path=lifecycle.current_path,
            trust_path=lifecycle.trust_path,
            private_key_path=lifecycle.private_key_path,
            workspace=lifecycle.workspace,
            clock=lambda: "2026-08-29T01:13:00Z",
        ),
    )
    assert (
        third.post("/internal/v1/bootstrap", json={"token": "v" * 43}).status_code
        == 204
    )
    issued_status = third.get("/internal/v1/identity/status").json()
    assert issued_status["phase"] == "issued"
    issued_page = third.get("/")
    assert (
        "Ondertekend voorstel teruggevonden. Je kunt nu activeren." in issued_page.text
    )
    assert "activateButton.disabled = false" in issued_page.text
    activated = third.post(
        "/internal/v1/identity/candidate/activate",
        json={
            "candidate_sha256": issued_status["candidate_sha256"],
            "expected_current_sha256": issued_status["base_pack_sha256"],
            "confirmation": "ACTIVEREN",
        },
        headers=origin,
    )
    assert activated.status_code == 200

    fourth_bootstrap = InstallerBootstrap(
        token="x" * 43, created_at=4.0, clock=lambda: 4.0
    )
    fourth = _client(
        fourth_bootstrap,
        receipt=_receipt(status="ready"),
        identity_lifecycle=InstallerIdentityLifecycle(
            current_path=lifecycle.current_path,
            trust_path=lifecycle.trust_path,
            private_key_path=lifecycle.private_key_path,
            workspace=lifecycle.workspace,
            clock=lambda: "2026-08-29T01:14:00Z",
        ),
    )
    assert (
        fourth.post("/internal/v1/bootstrap", json={"token": "x" * 43}).status_code
        == 204
    )
    active_status = fourth.get("/internal/v1/identity/status").json()
    assert active_status["phase"] == "active"
    active_page = fourth.get("/")
    assert "De nieuwe identiteit is actief." in active_page.text
    assert "rollbackButton.disabled = false" in active_page.text
    rolled_back = fourth.post(
        "/internal/v1/identity/rollback",
        json={
            "expected_current_sha256": active_status["candidate_sha256"],
            "confirmation": "TERUGZETTEN",
        },
        headers=origin,
    )
    assert rolled_back.status_code == 200
    assert lifecycle.status().phase == "rolled_back"
