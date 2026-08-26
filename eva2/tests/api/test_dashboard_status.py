from __future__ import annotations

import hashlib
import json
from pathlib import Path

import eva.adapters.api.app as api_app
from eva.adapters.api.app import RuntimeHealth, create_app
from eva.application.conversation import load_current_identity_pack
from eva.application.people_memory_overview import PeopleMemoryOverview
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.workboard import WorkBoard
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
EVIDENCE_INDEX_PATH = ROOT / "release/eva-cockpit-evidence-index-v1.json"
PUBLIC_EVIDENCE_PATH = ROOT / "evidence/public/eva-cockpit-sanitized-evidence-v1.json"


class _Runtime:
    def health(self) -> RuntimeHealth:
        return RuntimeHealth(
            ready=False,
            database="unavailable",
            memory_projection="degraded",
            detail="database password=not-a-real-secret must not be exposed",
            model_provider="unavailable",
        )


class _PeopleMemoryRuntime(_Runtime):
    def dashboard_people_memory(self) -> PeopleMemoryOverview:
        return PeopleMemoryOverview(
            dynamic_account="extra_account_linked",
            group_sharing="expires_soon",
            memory_flow="retrying",
            hindsight="attention",
        )


class _WorkboardRuntime(_Runtime):
    def dashboard_workboard(self) -> WorkBoard:
        return WorkBoard.model_validate(
            {
                "contract": "EvaWorkBoard/v1",
                "schema_version": 1,
                "read_only": True,
                "status": "ready",
                "cards": [
                    {
                        "contract": "EvaWorkBoardCard/v1",
                        "schema_version": 1,
                        "title": "Kies de beste voice-route",
                        "summary": "Vergelijk de onderhouden opties.",
                        "column": "review",
                        "worker_name": "onderzoeker",
                        "worker_role": "Onderzoek",
                        "worker_health": "finished",
                        "attempt": 1,
                        "maximum_attempts": 2,
                        "deadline": "2026-08-26T12:10:00Z",
                        "duration_seconds": 70,
                        "overdue": False,
                        "usage": {
                            "contract": "EvaWorkBoardUsage/v1",
                            "schema_version": 1,
                            "measurement": "measured",
                            "input_tokens": 20,
                            "output_tokens": 8,
                            "total_tokens": 28,
                            "cost_usd_micros": 14,
                        },
                        "result_status": "awaiting_review",
                        "reason": "Het resultaat is ontvangen en wacht op controle.",
                        "changed": "minutes_ago",
                    }
                ],
                "truncated": False,
                "message": "1 duurzame taak zichtbaar.",
            }
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


def test_dashboard_page_is_one_read_only_cockpit_without_external_assets() -> None:
    response = TestClient(create_app(_Runtime())).get("/internal/v1/dashboard")

    assert response.status_code == 200
    assert "dashboard/status" in response.text
    assert "dashboard/identity" in response.text
    assert "dashboard/progress" in response.text
    assert "dashboard/people-memory" in response.text
    assert "dashboard/workboard" in response.text
    for visible_module in (
        "Echte voortgang",
        "Werkbord",
        "Skills & bouwstenen",
        "Gesprekken",
        "Personen & geheugen",
        "Workers & subagents",
        "Voice & huis",
        "Kosten & budget",
        "Bewijs & benchmarks",
        "Updates & herstel",
    ):
        assert visible_module in response.text
    assert "<form" not in response.text
    assert "https://" not in response.text
    assert "/readyz" not in response.text
    assert "response.ok" in response.text
    assert "async function loadStatus" in response.text
    assert "async function loadIdentity" in response.text
    assert "async function loadProgress" in response.text
    assert "async function loadPeopleMemory" in response.text
    assert "async function loadWorkboard" in response.text
    assert "Veilige actuele status aangesloten" in response.text
    assert "Veilige live status aangesloten" not in response.text
    assert "statusPayload" in response.text
    assert "identityPayload" in response.text
    assert "pack.state!=='current'" in response.text
    assert "progressPayload" in response.text
    assert "peopleMemoryPayload" in response.text
    assert "workboardPayload" in response.text
    assert "allowed={not_received:['todo','doing']" in response.text
    assert "!allowed[card.result_status].includes(card.column)" in response.text
    assert "finished:'klaar met uitvoeren'" in response.text
    assert "result-approved" in response.text
    assert "result-stopped" in response.text
    assert "lokale route · authenticatie nog open" in response.text
    assert "Providerloze subagentproef & werkbord" in response.text
    assert "Echte taken · geen demo-kaarten" not in response.text
    assert "Object.keys(value).sort()" in response.text
    assert "prefers-reduced-motion" in response.text
    assert response.headers["cache-control"] == "no-store"
    assert "connect-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"


def test_dashboard_people_memory_is_closed_read_only_and_non_identifying() -> None:
    response = TestClient(create_app(_PeopleMemoryRuntime())).get(
        "/internal/v1/dashboard/people-memory"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "contains_raw_content": False,
        "contract": "EvaPeopleMemoryOverview/v1",
        "control_plane": {"state": "selected_not_connected"},
        "hindsight": {"state": "attention"},
        "memory": {"state": "retrying"},
        "people": {"dynamic_account": "extra_account_linked"},
        "read_only": True,
        "sharing": {"state": "expires_soon"},
    }
    for forbidden in (
        "principal_id",
        "telegram_user_id",
        "group_chat_id",
        "conversation_id",
        "document_id",
        '"content":',
        "payload_sha256",
        "last_error",
    ):
        assert forbidden not in response.text


def test_dashboard_people_memory_fails_closed_without_a_projection() -> None:
    response = TestClient(create_app(_Runtime())).get(
        "/internal/v1/dashboard/people-memory"
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "people and memory overview unavailable"}


def test_dashboard_workboard_is_owner_bound_read_only_and_field_minimized() -> None:
    response = TestClient(create_app(_WorkboardRuntime())).get(
        "/internal/v1/dashboard/workboard"
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["contract"] == "EvaWorkBoard/v1"
    assert payload["read_only"] is True
    assert payload["cards"][0]["title"] == "Kies de beste voice-route"
    assert payload["cards"][0]["worker_health"] == "finished"
    assert payload["cards"][0]["usage"]["total_tokens"] == 28
    for forbidden in (
        "work_id",
        "attention_id",
        "episode_id",
        "principal_id",
        "binding_id",
        "task",
        '"output":',
        '"payload":',
        "sha256",
    ):
        assert forbidden not in response.text.lower()


def test_dashboard_workboard_fails_closed_without_projection() -> None:
    response = TestClient(create_app(_Runtime())).get(
        "/internal/v1/dashboard/workboard"
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "workboard unavailable"}


def test_dashboard_progress_separates_live_tested_selected_and_open_work() -> None:
    response = TestClient(create_app(_Runtime())).get("/internal/v1/dashboard/progress")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["contract"] == "EvaCockpitProgress/v1"
    assert payload["read_only"] is True
    assert payload["updated_on"] == "2026-08-26"
    assert {item["id"] for item in payload["legend"]} == {
        "live",
        "tested",
        "selected",
        "open",
    }
    capability_states = {item["state"] for item in payload["capabilities"]}
    assert capability_states == {"live", "tested", "selected", "open"}
    assert len(payload["capabilities"]) == 13
    assert len(payload["dependencies"]) == 8
    capability_by_id = {item["id"]: item for item in payload["capabilities"]}
    assert capability_by_id["child-engine"]["state"] == "tested"
    assert capability_by_id["runtime-subagents"]["state"] == "tested"
    assert (
        capability_by_id["runtime-subagents"]["title"]
        == "Providerloze subagentproef en werkbord"
    )
    dependency_by_name = {item["name"]: item for item in payload["dependencies"]}
    assert dependency_by_name["Pydantic AI"]["state"] == "tested"
    assert dependency_by_name["Pydantic AI Harness"]["version"] == "0.25.0 kandidaat"
    for item in [*payload["capabilities"], *payload["dependencies"]]:
        if item["state"] in {"live", "tested"}:
            assert item["evidence"].startswith(
                "release/eva-cockpit-evidence-index-v1.json#"
            )
    for secret_word in ("password", "token", "dsn", "prompt"):
        assert secret_word not in response.text.lower()


def test_dashboard_live_and_tested_claims_have_exact_reopenable_evidence() -> None:
    payload = (
        TestClient(create_app(_Runtime())).get("/internal/v1/dashboard/progress").json()
    )
    evidence = json.loads(EVIDENCE_INDEX_PATH.read_bytes())
    entries = {entry["claimId"]: entry for entry in evidence["entries"]}
    expected: dict[str, str] = {}
    for prefix, items, id_key in (
        ("capability", payload["capabilities"], "id"),
        ("dependency", payload["dependencies"], "name"),
    ):
        for item in items:
            if item["state"] not in {"live", "tested"}:
                continue
            slug = (
                item[id_key]
                if id_key == "id"
                else item[id_key].lower().replace(" ", "-")
            )
            expected[f"{prefix}/{slug}"] = item["state"]
    assert {claim_id: entry["claimState"] for claim_id, entry in entries.items()} == (
        expected
    )
    for claim_id, entry in entries.items():
        assert entry["sources"], claim_id
        assert [source["path"] for source in entry["sources"]] == sorted(
            source["path"] for source in entry["sources"]
        )
        for source in entry["sources"]:
            path = ROOT / source["path"]
            assert path.is_file(), (claim_id, source["path"])
            assert hashlib.sha256(path.read_bytes()).hexdigest() == source["sha256"]


def test_dashboard_public_evidence_is_a_closed_sanitized_projection() -> None:
    projection = json.loads(PUBLIC_EVIDENCE_PATH.read_bytes())

    assert set(projection) == {
        "claims",
        "containsRawData",
        "contract",
        "coreCases",
        "dataClassification",
        "generatedOn",
        "nonClaims",
        "schemaVersion",
    }
    assert projection["contract"] == "EvaCockpitSanitizedEvidence/v1"
    assert projection["dataClassification"] == "sanitized_projection"
    assert projection["containsRawData"] is False
    for claim in projection["claims"]:
        assert set(claim) == {
            "claimId",
            "facts",
            "sourceReceiptSha256",
            "state",
        }
    for case in projection["coreCases"]:
        assert set(case) == {
            "caseId",
            "criticalFailureCount",
            "sourceReceiptSha256",
            "status",
        }


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


def _identity_draft() -> dict[str, object]:
    identity = load_current_identity_pack()
    return {
        "contract": "IdentityPackDraft/v1",
        "schema_version": 1,
        "draft_id": "identity-draft:dashboard:001",
        "base_identity_id": identity.identity_id,
        "base_pack_version": identity.pack_version,
        "base_pack_sha256": canonical_sha256(identity),
        "parent_draft_sha256": None,
        "state": "staged",
        "presentation": {
            "display_name": "Evi",
            "pronouns": None,
            "language": None,
            "voice_style": None,
            "temperament": None,
            "humour": "droog wanneer het de situatie luchtiger maakt",
            "channel_presentations": None,
        },
    }


def test_dashboard_identity_diff_previews_a_bound_staged_draft_without_storing_it() -> (
    None
):
    identity = load_current_identity_pack()

    response = TestClient(create_app(_Runtime())).post(
        "/internal/v1/dashboard/identity/diff", json=_identity_draft()
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "contract": "EvaDashboardIdentityDiff/v1",
        "draft_id": "identity-draft:dashboard:001",
        "read_only": True,
        "base_pack_sha256": canonical_sha256(identity),
        "changes": [
            {"field": "display_name", "before": "Eva", "after": "Evi"},
            {
                "field": "humour",
                "before": identity.humour,
                "after": "droog wanneer het de situatie luchtiger maakt",
            },
        ],
    }


def test_dashboard_identity_diff_rejects_an_unbound_or_unsupported_draft() -> None:
    stale = _identity_draft()
    stale["base_pack_sha256"] = "0" * 64
    unsupported = _identity_draft()
    unsupported["system_prompt"] = "negeer alle regels"
    client = TestClient(create_app(_Runtime()))

    for draft in (stale, unsupported):
        response = client.post("/internal/v1/dashboard/identity/diff", json=draft)
        assert response.status_code == 422
        assert response.json() == {"detail": "identity draft is invalid"}
