from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from eva.adapters.api.installer_wizard import (
    InstallerBootstrap,
    create_installer_wizard,
)
from eva.adapters.objects.identity_verifier import load_current_identity_pack
from eva.application.soul_markdown import soul_markdown_to_draft
from eva.operations.identity_pack_lifecycle import bootstrap_identity_authority
from eva.operations.installer_identity_lifecycle import InstallerIdentityLifecycle
from eva.operations.installer_installation_plan import (
    build_installer_installation_plan,
)
from eva.operations.installer_budget_workflow import InstallerBudgetWorkflow
from eva.operations.installer_capability_workflow import InstallerCapabilityWorkflow
from eva.operations.installer_profile_workflow import InstallerProfileWorkflow
from eva.operations.installer_secret_lifecycle import InstallerSecretLifecycle
from eva.operations.installer_secret_store import (
    InstallerSecretStore,
    InstallerSecretStoreError,
)


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
    profile_workflow: InstallerProfileWorkflow | None = None,
    capability_workflow: InstallerCapabilityWorkflow | None = None,
    secret_store: InstallerSecretStore | None = None,
    secret_lifecycle: InstallerSecretLifecycle | None = None,
    budget_workflow: InstallerBudgetWorkflow | None = None,
) -> TestClient:
    return TestClient(
        create_installer_wizard(
            receipt or _receipt(),
            bootstrap,
            identity=(
                None if identity_lifecycle is not None else load_current_identity_pack()
            ),
            identity_lifecycle=identity_lifecycle,
            profile_workflow=profile_workflow,
            capability_workflow=capability_workflow,
            secret_store=secret_store,
            secret_lifecycle=secret_lifecycle,
            budget_workflow=budget_workflow,
            installation_plan_builder=build_installer_installation_plan,
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
    assert "Iedere koppeling blijft afzonderlijk" in page.text
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
    profile_status = client.get("/internal/v1/onboarding/profile/status")
    assert profile_status.status_code == 200
    assert profile_status.json()["phase"] == "unavailable"
    assert profile_status.json()["enabled"] is False
    capability_status = client.get("/internal/v1/onboarding/capabilities/status")
    assert capability_status.status_code == 200
    assert capability_status.json()["enabled"] is False
    assert {item["state"] for item in capability_status.json()["steps"]} == {
        "unavailable"
    }
    secret_status = client.get("/internal/v1/onboarding/secrets/status")
    assert secret_status.status_code == 200
    assert secret_status.json()["enabled"] is False
    assert {item["state"] for item in secret_status.json()["slots"]} == {"unavailable"}
    budget_status = client.get("/internal/v1/onboarding/budgets/status")
    assert budget_status.status_code == 200
    assert budget_status.json()["phase"] == "unavailable"
    assert budget_status.json()["enabled"] is False
    plan = client.get("/internal/v1/onboarding/install/plan")
    assert plan.status_code == 200
    assert plan.json()["status"] == "blocked"
    assert plan.json()["read_only"] is True


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
    app = create_installer_wizard(
        _receipt(), bootstrap, identity=load_current_identity_pack()
    )
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
        create_installer_wizard(
            _receipt(), bootstrap, identity=load_current_identity_pack()
        ),
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


def test_wizard_resumes_non_secret_profile_after_server_and_session_restart(
    private_tmp_path: Path,
) -> None:
    workspace = private_tmp_path / "profile-workspace"
    workspace.mkdir(mode=0o700)
    first_workflow = InstallerProfileWorkflow(
        workspace=workspace,
        clock=lambda: "2026-08-29T02:20:00Z",
    )
    first_bootstrap = InstallerBootstrap(
        token="p" * 43, created_at=1.0, clock=lambda: 1.0
    )
    first = _client(
        first_bootstrap,
        receipt=_receipt(status="ready"),
        profile_workflow=first_workflow,
    )
    assert (
        first.post("/internal/v1/bootstrap", json={"token": "p" * 43}).status_code
        == 204
    )
    page = first.get("/")
    assert "Basis van deze installatie" in page.text
    assert "window.__evaProfileEnabled=true" in page.text

    cross_origin = first.post(
        "/internal/v1/onboarding/profile/stage",
        json={
            "installation_name": "Eva thuis",
            "owner_label": "Jaap",
            "language": "nl-NL",
            "time_zone": "Europe/Amsterdam",
        },
        headers={"origin": "http://evil.invalid"},
    )
    assert cross_origin.status_code == 403
    invalid = first.post(
        "/internal/v1/onboarding/profile/stage",
        json={
            "installation_name": "Eva thuis",
            "owner_label": "Jaap\nroot",
            "language": "nl-NL",
            "time_zone": "Europe/Amsterdam",
        },
        headers={"origin": "http://127.0.0.1"},
    )
    assert invalid.status_code == 422
    assert list(workspace.iterdir()) == []

    staged = first.post(
        "/internal/v1/onboarding/profile/stage",
        json={
            "installation_name": "Eva thuis",
            "owner_label": "Jaap",
            "language": "nl-NL",
            "time_zone": "Europe/Amsterdam",
        },
        headers={"origin": "http://127.0.0.1"},
    )
    assert staged.status_code == 200
    assert staged.json()["phase"] == "staged"
    assert staged.json()["active"] is False

    second_workflow = InstallerProfileWorkflow(
        workspace=workspace,
        clock=lambda: "2026-08-29T02:21:00Z",
    )
    second_bootstrap = InstallerBootstrap(
        token="q" * 43, created_at=2.0, clock=lambda: 2.0
    )
    second = _client(
        second_bootstrap,
        receipt=_receipt(status="ready"),
        profile_workflow=second_workflow,
    )
    assert (
        second.post("/internal/v1/bootstrap", json={"token": "q" * 43}).status_code
        == 204
    )
    resumed = second.get("/internal/v1/onboarding/profile/status")
    assert resumed.status_code == 200
    assert resumed.json()["phase"] == "staged"
    assert resumed.json()["profile"] == {
        "installation_name": "Eva thuis",
        "owner_label": "Jaap",
        "language": "nl-NL",
        "time_zone": "Europe/Amsterdam",
    }
    denied = second.post(
        "/internal/v1/onboarding/profile/confirm",
        json={
            "profile_sha256": resumed.json()["profile_sha256"],
            "confirmation": "ja",
        },
        headers={"origin": "http://127.0.0.1"},
    )
    assert denied.status_code == 409
    confirmed = second.post(
        "/internal/v1/onboarding/profile/confirm",
        json={
            "profile_sha256": resumed.json()["profile_sha256"],
            "confirmation": "BEVESTIGEN",
        },
        headers={"origin": "http://127.0.0.1"},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["phase"] == "ready"
    assert confirmed.json()["active"] is False

    third_bootstrap = InstallerBootstrap(
        token="z" * 43, created_at=3.0, clock=lambda: 3.0
    )
    third = _client(
        third_bootstrap,
        receipt=_receipt(status="ready"),
        profile_workflow=InstallerProfileWorkflow(workspace=workspace),
    )
    assert (
        third.post("/internal/v1/bootstrap", json={"token": "z" * 43}).status_code
        == 204
    )
    ready = third.get("/internal/v1/onboarding/profile/status")
    assert ready.status_code == 200
    assert ready.json()["phase"] == "ready"
    assert ready.json()["revision"] == 2
    assert ready.json()["non_claims"] == [
        "Deze keuzes maken nog geen installatie of eigenaaraccount.",
        "Deze route bewaart geen connectorsecret en activeert geen dienst.",
    ]


def test_wizard_blocks_profile_writes_when_platform_preflight_is_blocked(
    private_tmp_path: Path,
) -> None:
    workspace = private_tmp_path / "blocked-profile"
    workspace.mkdir(mode=0o700)
    bootstrap = InstallerBootstrap(token="k" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(
        bootstrap,
        profile_workflow=InstallerProfileWorkflow(workspace=workspace),
    )
    assert (
        client.post("/internal/v1/bootstrap", json={"token": "k" * 43}).status_code
        == 204
    )
    assert "window.__evaProfileEnabled=false" in client.get("/").text

    denied = client.post(
        "/internal/v1/onboarding/profile/stage",
        json={
            "installation_name": "Eva thuis",
            "owner_label": "Jaap",
            "language": "nl-NL",
            "time_zone": "Europe/Amsterdam",
        },
        headers={"origin": "http://127.0.0.1"},
    )
    assert denied.status_code == 409
    assert list(workspace.iterdir()) == []


def test_wizard_retains_capability_choices_but_cannot_claim_provider_ready(
    private_tmp_path: Path,
) -> None:
    workspace = private_tmp_path / "capability-workspace"
    workspace.mkdir(mode=0o700)
    first_workflow = InstallerCapabilityWorkflow(
        workspace=workspace,
        clock=lambda: "2026-08-29T03:20:00Z",
    )
    first_bootstrap = InstallerBootstrap(
        token="c" * 43, created_at=1.0, clock=lambda: 1.0
    )
    first = _client(
        first_bootstrap,
        receipt=_receipt(status="ready"),
        capability_workflow=first_workflow,
    )
    assert (
        first.post("/internal/v1/bootstrap", json={"token": "c" * 43}).status_code
        == 204
    )
    page = first.get("/")
    assert "Wat wil je koppelen?" in page.text
    assert "window.__evaCapabilityEnabled=true" in page.text

    origin = {"origin": "http://127.0.0.1"}
    cross_origin = first.post(
        "/internal/v1/onboarding/capabilities/select",
        json={"step_id": "telegram", "wanted": True},
        headers={"origin": "http://evil.invalid"},
    )
    assert cross_origin.status_code == 403
    fake_ready = first.post(
        "/internal/v1/onboarding/capabilities/select",
        json={"step_id": "telegram", "wanted": True, "state": "ready"},
        headers=origin,
    )
    assert fake_ready.status_code == 422
    assert (
        first.post(
            "/internal/v1/onboarding/capabilities/verify",
            json={"step_id": "telegram", "state": "ready"},
            headers=origin,
        ).status_code
        == 404
    )

    for step_id, wanted in (
        ("model_provider", True),
        ("telegram", True),
        ("email", False),
    ):
        selected = first.post(
            "/internal/v1/onboarding/capabilities/select",
            json={"step_id": step_id, "wanted": wanted},
            headers=origin,
        )
        assert selected.status_code == 200
        assert selected.json()["provider_verified"] is False
        assert selected.json()["evidence_sha256"] is None

    second_bootstrap = InstallerBootstrap(
        token="d" * 43, created_at=2.0, clock=lambda: 2.0
    )
    second = _client(
        second_bootstrap,
        receipt=_receipt(status="ready"),
        capability_workflow=InstallerCapabilityWorkflow(workspace=workspace),
    )
    assert (
        second.post("/internal/v1/bootstrap", json={"token": "d" * 43}).status_code
        == 204
    )
    resumed = second.get("/internal/v1/onboarding/capabilities/status")
    assert resumed.status_code == 200
    states = {item["step_id"]: item["state"] for item in resumed.json()["steps"]}
    assert states == {
        "model_provider": "selected",
        "telegram": "selected",
        "home_assistant": "pending",
        "email": "skipped",
        "adaptive_connector": "pending",
        "budgets": "pending",
    }
    assert resumed.json()["revision"] == 3
    assert resumed.json()["non_claims"][1].startswith("Alleen een afzonderlijke")

    operator = InstallerCapabilityWorkflow(
        workspace=workspace,
        clock=lambda: "2026-08-29T03:21:00Z",
    )
    operator.record_verification(
        step_id="telegram",
        outcome="ready",
        evidence_sha256="e" * 64,
    )
    third_bootstrap = InstallerBootstrap(
        token="f" * 43, created_at=3.0, clock=lambda: 3.0
    )
    third = _client(
        third_bootstrap,
        receipt=_receipt(status="ready"),
        capability_workflow=InstallerCapabilityWorkflow(workspace=workspace),
    )
    assert (
        third.post("/internal/v1/bootstrap", json={"token": "f" * 43}).status_code
        == 204
    )
    verified = third.get("/internal/v1/onboarding/capabilities/status").json()
    telegram = next(item for item in verified["steps"] if item["step_id"] == "telegram")
    assert telegram["state"] == "ready"
    assert telegram["evidence_sha256"] == "e" * 64


def test_wizard_blocks_capability_choices_when_platform_preflight_is_blocked(
    private_tmp_path: Path,
) -> None:
    workspace = private_tmp_path / "blocked-capabilities"
    workspace.mkdir(mode=0o700)
    bootstrap = InstallerBootstrap(token="m" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(
        bootstrap,
        capability_workflow=InstallerCapabilityWorkflow(workspace=workspace),
    )
    assert (
        client.post("/internal/v1/bootstrap", json={"token": "m" * 43}).status_code
        == 204
    )
    assert "window.__evaCapabilityEnabled=false" in client.get("/").text
    denied = client.post(
        "/internal/v1/onboarding/capabilities/select",
        json={"step_id": "telegram", "wanted": True},
        headers={"origin": "http://127.0.0.1"},
    )
    assert denied.status_code == 409
    assert list(workspace.iterdir()) == []


def test_wizard_stores_selected_connector_secret_once_without_returning_it(
    private_tmp_path: Path,
) -> None:
    capability_workspace = private_tmp_path / "secret-capabilities"
    secret_workspace = private_tmp_path / "secrets"
    capability_workspace.mkdir(mode=0o700)
    secret_workspace.mkdir(mode=0o700)
    capabilities = InstallerCapabilityWorkflow(
        workspace=capability_workspace,
        clock=lambda: "2026-08-29T04:20:00Z",
    )
    secrets = InstallerSecretStore(
        workspace=secret_workspace,
        clock=lambda: "2026-08-29T04:21:00Z",
    )
    bootstrap = InstallerBootstrap(token="n" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(
        bootstrap,
        receipt=_receipt(status="ready"),
        capability_workflow=capabilities,
        secret_store=secrets,
    )
    assert (
        client.post("/internal/v1/bootstrap", json={"token": "n" * 43}).status_code
        == 204
    )
    page = client.get("/")
    assert "Eenmalige toegang invoeren" in page.text
    assert 'autocomplete="new-password"' in page.text
    assert "localStorage" not in page.text
    assert "sessionStorage" not in page.text
    assert "window.__evaSecretEnabled=true" in page.text
    origin = {"origin": "http://127.0.0.1"}

    not_selected = client.post(
        "/internal/v1/onboarding/secrets/telegram/store",
        json={"secret": "synthetic-telegram-token-123456789"},
        headers=origin,
    )
    assert not_selected.status_code == 409
    selected = client.post(
        "/internal/v1/onboarding/capabilities/select",
        json={"step_id": "telegram", "wanted": True},
        headers=origin,
    )
    assert selected.status_code == 200
    cross_origin = client.post(
        "/internal/v1/onboarding/secrets/telegram/store",
        json={"secret": "synthetic-telegram-token-123456789"},
        headers={"origin": "http://evil.invalid"},
    )
    assert cross_origin.status_code == 403
    extra_field = client.post(
        "/internal/v1/onboarding/secrets/telegram/store",
        json={"secret": "synthetic-telegram-token-123456789", "ready": True},
        headers=origin,
    )
    assert extra_field.status_code == 422

    secret = "synthetic-telegram-token-123456789"
    stored = client.post(
        "/internal/v1/onboarding/secrets/telegram/store",
        json={"secret": secret},
        headers=origin,
    )
    assert stored.status_code == 200
    assert stored.json() == {
        "contract": "EvaInstallerSecretStored/v1",
        "slot_id": "telegram",
        "state": "present",
        "reference": "installer-secret://telegram/v1",
        "created_at": "2026-08-29T04:21:00Z",
        "provider_verified": False,
        "secret_returned": False,
    }
    assert secret not in stored.text
    assert (
        client.get("/internal/v1/onboarding/secrets/telegram/read").status_code == 404
    )
    duplicate = client.post(
        "/internal/v1/onboarding/secrets/telegram/store",
        json={"secret": secret},
        headers=origin,
    )
    assert duplicate.status_code == 409

    next_bootstrap = InstallerBootstrap(
        token="o" * 43, created_at=2.0, clock=lambda: 2.0
    )
    restarted = _client(
        next_bootstrap,
        receipt=_receipt(status="ready"),
        capability_workflow=InstallerCapabilityWorkflow(workspace=capability_workspace),
        secret_store=InstallerSecretStore(workspace=secret_workspace),
    )
    assert (
        restarted.post("/internal/v1/bootstrap", json={"token": "o" * 43}).status_code
        == 204
    )
    status = restarted.get("/internal/v1/onboarding/secrets/status")
    assert status.status_code == 200
    assert secret not in status.text
    telegram = next(
        item for item in status.json()["slots"] if item["slot_id"] == "telegram"
    )
    assert telegram == {
        "slot_id": "telegram",
        "label": "Telegram",
        "state": "present",
        "eligible": True,
        "reference": "installer-secret://telegram/v1",
        "created_at": "2026-08-29T04:21:00Z",
    }
    assert status.json()["non_claims"][0].endswith("hostpad.")


def test_wizard_rejects_oversized_or_blocked_secret_intake(
    private_tmp_path: Path,
) -> None:
    capability_workspace = private_tmp_path / "blocked-secret-capabilities"
    secret_workspace = private_tmp_path / "blocked-secrets"
    capability_workspace.mkdir(mode=0o700)
    secret_workspace.mkdir(mode=0o700)
    capabilities = InstallerCapabilityWorkflow(
        workspace=capability_workspace,
        clock=lambda: "2026-08-29T04:30:00Z",
    )
    capabilities.select(step_id="telegram", wanted=True)
    bootstrap = InstallerBootstrap(token="y" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(
        bootstrap,
        capability_workflow=capabilities,
        secret_store=InstallerSecretStore(workspace=secret_workspace),
    )
    assert (
        client.post("/internal/v1/bootstrap", json={"token": "y" * 43}).status_code
        == 204
    )
    denied = client.post(
        "/internal/v1/onboarding/secrets/telegram/store",
        json={"secret": "synthetic-telegram-token-123456789"},
        headers={"origin": "http://127.0.0.1"},
    )
    assert denied.status_code == 409
    assert list(secret_workspace.iterdir()) == []

    ready_bootstrap = InstallerBootstrap(
        token="x" * 43, created_at=2.0, clock=lambda: 2.0
    )
    ready = _client(
        ready_bootstrap,
        receipt=_receipt(status="ready"),
        capability_workflow=capabilities,
        secret_store=InstallerSecretStore(workspace=secret_workspace),
    )
    assert (
        ready.post("/internal/v1/bootstrap", json={"token": "x" * 43}).status_code
        == 204
    )
    oversized = ready.post(
        "/internal/v1/onboarding/secrets/telegram/store",
        content=b'{"secret":"' + (b"a" * 13_000) + b'"}',
        headers={
            "origin": "http://127.0.0.1",
            "content-type": "application/json",
        },
    )
    assert oversized.status_code == 413
    assert list(secret_workspace.iterdir()) == []


def test_wizard_rotates_and_removes_secret_without_returning_either_value(
    private_tmp_path: Path,
) -> None:
    capability_workspace = private_tmp_path / "lifecycle-capabilities"
    secret_workspace = private_tmp_path / "lifecycle-secrets"
    evidence_workspace = private_tmp_path / "lifecycle-evidence"
    for path in (capability_workspace, secret_workspace, evidence_workspace):
        path.mkdir(mode=0o700)
    capabilities = InstallerCapabilityWorkflow(
        workspace=capability_workspace,
        clock=lambda: "2026-08-29T08:00:00Z",
    )
    secrets = InstallerSecretStore(
        workspace=secret_workspace,
        clock=lambda: "2026-08-29T08:01:00Z",
    )
    lifecycle = InstallerSecretLifecycle(
        capability_workflow=capabilities,
        secret_store=secrets,
        evidence_workspace=evidence_workspace,
        clock=lambda: "2026-08-29T08:02:00Z",
    )
    old_secret = "synthetic-old-telegram-token-123456"
    new_secret = "synthetic-new-telegram-token-654321"
    capabilities.select(step_id="telegram", wanted=True)
    secrets.store(slot_id="telegram", secret=old_secret)
    capabilities.record_verification(
        step_id="telegram", outcome="ready", evidence_sha256="a" * 64
    )
    bootstrap = InstallerBootstrap(token="z" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(
        bootstrap,
        receipt=_receipt(status="ready"),
        capability_workflow=capabilities,
        secret_store=secrets,
        secret_lifecycle=lifecycle,
    )
    assert (
        client.post("/internal/v1/bootstrap", json={"token": "z" * 43}).status_code
        == 204
    )
    page = client.get("/")
    assert "Toegang vervangen" in page.text
    assert "Koppeling losmaken" in page.text
    assert "window.confirm" in page.text
    status = client.get("/internal/v1/onboarding/secrets/status").json()
    assert status["lifecycle_enabled"] is True
    origin = {"origin": "http://127.0.0.1"}
    denied = client.post(
        "/internal/v1/onboarding/secrets/telegram/rotate",
        json={"secret": new_secret, "confirmation": "TOEGANG VERVANGEN"},
        headers={"origin": "http://evil.invalid"},
    )
    assert denied.status_code == 403
    unconfirmed = client.post(
        "/internal/v1/onboarding/secrets/telegram/rotate",
        json={"secret": new_secret, "confirmation": "ja"},
        headers=origin,
    )
    assert unconfirmed.status_code == 422
    rotated = client.post(
        "/internal/v1/onboarding/secrets/telegram/rotate",
        json={"secret": new_secret, "confirmation": "TOEGANG VERVANGEN"},
        headers=origin,
    )
    assert rotated.status_code == 200
    assert rotated.json()["operation"] == "rotate"
    assert rotated.json()["generation"] == 2
    assert rotated.json()["secret_returned"] is False
    assert old_secret not in rotated.text
    assert new_secret not in rotated.text
    assert capabilities.status().step("telegram").state == "blocked"
    assert secrets.read_secret(slot_id="telegram") == new_secret.encode()

    removed = client.post(
        "/internal/v1/onboarding/secrets/telegram/remove",
        json={"confirmation": "KOPPELING LOSMAKEN"},
        headers=origin,
    )
    assert removed.status_code == 200
    assert removed.json()["operation"] == "remove"
    assert removed.json()["state"] == "missing"
    assert removed.json()["secret_returned"] is False
    assert old_secret not in removed.text
    assert new_secret not in removed.text
    telegram = next(
        item
        for item in client.get("/internal/v1/onboarding/secrets/status").json()[
            "slots"
        ]
        if item["slot_id"] == "telegram"
    )
    assert telegram["state"] == "missing"


def test_wizard_recovers_interrupted_rotation_after_new_server(
    private_tmp_path: Path, monkeypatch
) -> None:
    capability_workspace = private_tmp_path / "recovery-capabilities"
    secret_workspace = private_tmp_path / "recovery-secrets"
    evidence_workspace = private_tmp_path / "recovery-evidence"
    for path in (capability_workspace, secret_workspace, evidence_workspace):
        path.mkdir(mode=0o700)
    capabilities = InstallerCapabilityWorkflow(workspace=capability_workspace)
    secrets = InstallerSecretStore(workspace=secret_workspace)
    lifecycle = InstallerSecretLifecycle(
        capability_workflow=capabilities,
        secret_store=secrets,
        evidence_workspace=evidence_workspace,
    )
    capabilities.select(step_id="model_provider", wanted=True)
    secrets.store(
        slot_id="model_provider", secret="synthetic-old-model-secret-123456"
    )
    capabilities.record_verification(
        step_id="model_provider", outcome="ready", evidence_sha256="b" * 64
    )
    evidence, _revision, _head = lifecycle._invalidate(
        slot_id="model_provider",
        operation="rotate",
        from_generation=1,
        to_generation=2,
    )
    confirm = lifecycle._confirm_invalidation(
        slot_id="model_provider",
        evidence_sha256=evidence,
        from_generation=1,
        to_generation=2,
    )
    real_replace = os.replace

    def interrupt(source: object, destination: object) -> None:
        if str(source).endswith(".rotation-next"):
            raise OSError("synthetic interruption")
        real_replace(source, destination)

    monkeypatch.setattr(os, "replace", interrupt)
    with pytest.raises(InstallerSecretStoreError, match="needs recovery"):
        secrets.rotate(
            slot_id="model_provider",
            secret="synthetic-new-model-secret-654321",
            invalidate=confirm,
        )
    monkeypatch.setattr(os, "replace", real_replace)

    resumed_lifecycle = InstallerSecretLifecycle(
        capability_workflow=InstallerCapabilityWorkflow(
            workspace=capability_workspace
        ),
        secret_store=InstallerSecretStore(workspace=secret_workspace),
        evidence_workspace=evidence_workspace,
    )
    bootstrap = InstallerBootstrap(token="w" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(
        bootstrap,
        receipt=_receipt(status="ready"),
        capability_workflow=resumed_lifecycle.capability_workflow,
        secret_store=resumed_lifecycle.secret_store,
        secret_lifecycle=resumed_lifecycle,
    )
    assert (
        client.post("/internal/v1/bootstrap", json={"token": "w" * 43}).status_code
        == 204
    )
    status = client.get("/internal/v1/onboarding/secrets/status")
    assert "synthetic-new-model" not in status.text
    model = next(
        item for item in status.json()["slots"] if item["slot_id"] == "model_provider"
    )
    assert model["state"] == "needs_help"
    recovered = client.post(
        "/internal/v1/onboarding/secrets/model_provider/recover",
        json={"confirmation": "WIJZIGING HERSTELLEN"},
        headers={"origin": "http://127.0.0.1"},
    )
    assert recovered.status_code == 200
    assert recovered.json()["operation"] == "recover"
    assert recovered.json()["generation"] == 2
    assert recovered.json()["secret_returned"] is False
    assert resumed_lifecycle.secret_store.read_secret(slot_id="model_provider") == (
        b"synthetic-new-model-secret-654321"
    )


def test_wizard_shows_ready_read_only_installation_plan(private_tmp_path: Path) -> None:
    profile_workspace = private_tmp_path / "plan-profile"
    capability_workspace = private_tmp_path / "plan-capabilities"
    secret_workspace = private_tmp_path / "plan-secrets"
    budget_workspace = private_tmp_path / "plan-budgets"
    for path in (
        profile_workspace,
        capability_workspace,
        secret_workspace,
        budget_workspace,
    ):
        path.mkdir(mode=0o700)
    profile = InstallerProfileWorkflow(
        workspace=profile_workspace, clock=lambda: "2026-08-29T04:40:00Z"
    )
    staged = profile.stage(
        installation_name="Thuis",
        owner_label="Eigenaar",
        language="nl-NL",
        time_zone="Europe/Amsterdam",
    )
    profile.confirm(expected_profile_sha256=staged.profile_sha256)
    capabilities = InstallerCapabilityWorkflow(
        workspace=capability_workspace, clock=lambda: "2026-08-29T04:41:00Z"
    )
    capabilities.select(step_id="model_provider", wanted=True)
    capabilities.record_verification(
        step_id="model_provider", outcome="ready", evidence_sha256="a" * 64
    )
    for step_id in ("telegram", "home_assistant", "email", "adaptive_connector"):
        capabilities.select(step_id=step_id, wanted=False)
    capabilities.select(step_id="budgets", wanted=True)
    budgets = InstallerBudgetWorkflow(
        workspace=budget_workspace,
        capability_workflow=capabilities,
        clock=lambda: "2026-08-29T04:42:00Z",
    )
    staged_budget = budgets.stage(
        background_reflections_per_installation_day=40,
        background_reflections_per_person_day=4,
        external_monthly_spend_cap_eur_cents=0,
        max_input_tokens=12_000,
        max_output_tokens=1_200,
        memory_bytes_per_person=20_971_520,
        memory_bytes_per_group=20_971_520,
        memory_bytes_per_installation=209_715_200,
        memory_bytes_per_project=20_971_520,
        memory_records_per_person=2_000,
        memory_records_per_group=2_000,
        memory_records_per_installation=20_000,
        memory_records_per_project=2_000,
        profile_context_tokens=800,
        recall_context_tokens=3_000,
        retention_days=365,
        retrieval_nodes=50,
    )
    budgets.confirm(expected_budget_sha256=staged_budget.budget_sha256)
    secrets = InstallerSecretStore(workspace=secret_workspace)
    secrets.store(
        slot_id="model_provider", secret="synthetic-model-plan-secret-123456"
    )
    bootstrap = InstallerBootstrap(token="p" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(
        bootstrap,
        receipt=_receipt(status="ready"),
        profile_workflow=profile,
        capability_workflow=capabilities,
        secret_store=secrets,
        budget_workflow=budgets,
    )
    assert client.post(
        "/internal/v1/bootstrap", json={"token": "p" * 43}
    ).status_code == 204

    page = client.get("/")
    plan = client.get("/internal/v1/onboarding/install/plan")

    assert "Installatieplan" in page.text
    assert plan.status_code == 200
    assert plan.headers["cache-control"] == "no-store"
    assert plan.json()["status"] == "ready_for_release_preparation"
    assert plan.json()["blockers"] == []
    assert "synthetic-model-plan-secret" not in plan.text
    assert all(route.path != "/internal/v1/onboarding/install/apply" for route in client.app.routes)


def test_wizard_user_can_confirm_safe_budgets_and_resume(
    private_tmp_path: Path,
) -> None:
    capability_workspace = private_tmp_path / "budget-api-capabilities"
    budget_workspace = private_tmp_path / "budget-api-state"
    capability_workspace.mkdir(mode=0o700)
    budget_workspace.mkdir(mode=0o700)
    capabilities = InstallerCapabilityWorkflow(workspace=capability_workspace)
    capabilities.select(step_id="budgets", wanted=True)
    budgets = InstallerBudgetWorkflow(
        workspace=budget_workspace,
        capability_workflow=capabilities,
        clock=lambda: "2026-08-29T05:10:00Z",
    )
    bootstrap = InstallerBootstrap(token="q" * 43, created_at=1.0, clock=lambda: 1.0)
    client = _client(
        bootstrap,
        receipt=_receipt(status="ready"),
        capability_workflow=capabilities,
        budget_workflow=budgets,
    )
    assert client.post(
        "/internal/v1/bootstrap", json={"token": "q" * 43}
    ).status_code == 204
    origin = {"origin": "http://127.0.0.1"}
    values = {
        "background_reflections_per_installation_day": 40,
        "background_reflections_per_person_day": 4,
        "external_monthly_spend_cap_eur_cents": 0,
        "max_input_tokens": 12_000,
        "max_output_tokens": 1_200,
        "memory_bytes_per_person": 20_971_520,
        "memory_bytes_per_group": 20_971_520,
        "memory_bytes_per_installation": 209_715_200,
        "memory_bytes_per_project": 20_971_520,
        "memory_records_per_person": 2_000,
        "memory_records_per_group": 2_000,
        "memory_records_per_installation": 20_000,
        "memory_records_per_project": 2_000,
        "profile_context_tokens": 800,
        "recall_context_tokens": 3_000,
        "retention_days": 365,
        "retrieval_nodes": 50,
    }
    staged = client.post(
        "/internal/v1/onboarding/budgets/stage", json=values, headers=origin
    )
    assert staged.status_code == 200
    denied = client.post(
        "/internal/v1/onboarding/budgets/confirm",
        json={
            "budget_sha256": staged.json()["budget_sha256"],
            "confirmation": "LIMIETEN BEVESTIGEN",
        },
        headers={"origin": "http://evil.invalid"},
    )
    assert denied.status_code == 403
    confirmed = client.post(
        "/internal/v1/onboarding/budgets/confirm",
        json={
            "budget_sha256": staged.json()["budget_sha256"],
            "confirmation": "LIMIETEN BEVESTIGEN",
        },
        headers=origin,
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["phase"] == "ready"
    assert capabilities.status().step("budgets").evidence_sha256 == (
        confirmed.json()["budget_sha256"]
    )

    resumed = InstallerBudgetWorkflow(
        workspace=budget_workspace,
        capability_workflow=InstallerCapabilityWorkflow(
            workspace=capability_workspace
        ),
    )
    second_bootstrap = InstallerBootstrap(
        token="r" * 43, created_at=1.0, clock=lambda: 1.0
    )
    second = _client(
        second_bootstrap,
        receipt=_receipt(status="ready"),
        capability_workflow=resumed.capability_workflow,
        budget_workflow=resumed,
    )
    assert second.post(
        "/internal/v1/bootstrap", json={"token": "r" * 43}
    ).status_code == 204
    status = second.get("/internal/v1/onboarding/budgets/status")
    assert status.status_code == 200
    assert status.json()["phase"] == "ready"
    assert status.json()["capability_bound"] is True
    assert status.json()["budget"]["unknown_cost_policy"] == (
        "block_autonomous_spend"
    )
