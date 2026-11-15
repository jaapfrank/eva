from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
import stat

from fastapi import FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest

from eva.adapters.api.dashboard_household_observation import (
    install_dashboard_household_observation,
)
from eva.application.household_observation import HouseholdObservationLedger


class _StubAccess:
    def __init__(self, *, token: str | None = "valid-owner-secret") -> None:
        self.token = token

    def require(self, request: Request) -> None:
        if self.token is not None and request.headers.get("X-Owner-Token") != self.token:
            raise HTTPException(status_code=401, detail="unauthorized")


class _StubRuntime:
    def __init__(
        self,
        ledger: HouseholdObservationLedger,
        *,
        owner_principal_id: str | None = "owner-jaap",
    ) -> None:
        self._household_observation = ledger
        self._dashboard_owner_principal_id = owner_principal_id


def test_household_ledger_enforces_jaap_lola_scope_and_honest_null_costs(tmp_path: Path) -> None:
    state_file = tmp_path / "obs" / "ledger.json"
    t0 = datetime(2026, 11, 14, 10, 0, 0, tzinfo=UTC)
    ledger = HouseholdObservationLedger(
        state_file=state_file,
        release_id="rel-20261114-a",
        runtime_metrics_probe=lambda: {"state": "ready", "ingress_turns_24h": 4},
        now=t0,
    )

    assert stat.S_IMODE(state_file.stat().st_mode) == 0o600
    assert stat.S_IMODE(state_file.parent.stat().st_mode) == 0o700

    ledger.record_turn(principal_id="owner-jaap", channel="telegram", used_model=True, known_cost_eur=None, now=t0)
    ledger.record_turn(principal_id="family-lola", channel="holostage", used_model=False, now=t0)
    ledger.record_turn(principal_id="household-device:living-room-satellite", channel="native_speaker", used_model=True, known_cost_eur=None, now=t0)
    ledger.record_turn(principal_id="untrusted-stranger-payload", channel="telegram", used_model=False, now=t0)
    ledger.record_zero_token_idle_tick(count=18, now=t0)
    ledger.record_reflex_hit(latency_ms=1.42, now=t0)
    ledger.record_proactive_decision(accepted=True, now=t0)
    ledger.record_proactive_decision(accepted=False, now=t0)

    raw_disk = state_file.read_text(encoding="utf-8")
    assert "untrusted-stranger-payload" not in raw_disk
    assert "living-room-satellite" not in raw_disk

    snap = ledger.snapshot(now=t0 + timedelta(hours=2))
    metrics = snap["symbiotic_metrics"]
    assert metrics["turns_jaap"] == 1
    assert metrics["turns_lola"] == 1
    assert metrics["turns_shared_household"] == 1
    assert metrics["rejected_non_household_attempts"] == 1
    assert metrics["channel_turns"]["telegram"] == 1
    assert metrics["channel_turns"]["holostage"] == 1
    assert metrics["channel_turns"]["native_speaker"] == 1
    assert metrics["measured_model_calls"] == 2
    assert metrics["unpriced_model_calls"] == 2
    assert metrics["reflex_organogenesis_hits"] == 1
    assert metrics["reflex_p95_ms"] == 1.42
    assert metrics["zero_token_autonomy_ratio"] == round((18 + 1) / (18 + 1 + 2), 4)
    assert metrics["proactive_acceptance_ratio"] == 0.5
    # Subscription / unpriced call keeps total cost explicitly None (never invented)
    assert metrics["estimated_runtime_cost_eur"] is None
    assert metrics["cost_measurement_status"] == "unknown_subscription_or_partial_credits"
    assert snap["live_database_window"] == {"state": "ready", "ingress_turns_24h": 4}


def test_seven_day_stability_gate_blocks_premature_acceptance_and_resets_on_new_release(
    tmp_path: Path,
) -> None:
    state_file = tmp_path / "obs" / "ledger.json"
    t0 = datetime(2026, 11, 14, 8, 0, 0, tzinfo=UTC)
    ledger = HouseholdObservationLedger(state_file=state_file, release_id="rel-v1")
    # Force deterministic start timestamp in ledger
    with ledger._locked_state(write=True, now=t0) as state:
        state["release_started_at"] = "2026-11-14T08:00:00Z"

    for day in range(6):
        ledger.record_turn(
            principal_id="owner-jaap",
            channel="holostage",
            used_model=True,
            known_cost_eur=0.01,
            now=t0 + timedelta(days=day, hours=1),
        )

    # At 167 hours / 6 active days -> F2.1 gate must still be False
    with pytest.raises(ValueError, match="7 contiguous days"):
        ledger.record_owner_acceptance(note="Te vroeg", now=t0 + timedelta(hours=167))

    # Day 7 active turn + >= 168h elapsed, but inject an unresolved critical error
    ledger.record_turn(
        principal_id="family-lola",
        channel="holostage",
        used_model=False,
        now=t0 + timedelta(days=6, hours=2),
    )
    ledger.record_runtime_fault(critical=True, repaired=False, now=t0 + timedelta(days=6, hours=3))
    blocked_snap = ledger.snapshot(now=t0 + timedelta(hours=169))
    assert blocked_snap["release_window"]["seven_day_gate_met"] is False
    assert blocked_snap["release_window"]["unresolved_critical_errors"] == 1

    # Self-repair resolves critical error -> gate becomes True and F2.2 sign-off is permitted
    ledger.record_self_repair_verified(resolved_critical=True, now=t0 + timedelta(hours=169, minutes=5))
    ready_snap = ledger.snapshot(now=t0 + timedelta(hours=170))
    assert ready_snap["release_window"]["seven_day_gate_met"] is True
    assert ready_snap["release_window"]["f2_2_owner_accepted"] is False
    assert ready_snap["symbiotic_metrics"]["estimated_runtime_cost_eur"] == 0.06

    accepted = ledger.record_owner_acceptance(
        note="Zeven dagen stabiel gebruikt door Jaap en Lola.",
        now=t0 + timedelta(hours=170, minutes=10),
    )
    assert accepted["release_window"]["f2_2_owner_accepted"] is True

    # Deploying a new release archives prior window and resets 7-day contiguous clock
    upgraded = HouseholdObservationLedger(
        state_file=state_file, release_id="rel-v2", now=t0 + timedelta(hours=170, minutes=30),
    )
    reset_snap = upgraded.snapshot(now=t0 + timedelta(hours=171))
    assert reset_snap["release_window"]["release_id"] == "rel-v2"
    assert reset_snap["release_window"]["seven_day_gate_met"] is False
    assert reset_snap["release_window"]["f2_2_owner_accepted"] is False
    assert reset_snap["release_window"]["prior_release_windows_count"] == 1


def test_household_observation_api_requires_owner_auth_csrf_guards_and_7_day_gate(
    tmp_path: Path,
) -> None:
    t0 = datetime(2026, 11, 14, 8, 0, 0, tzinfo=UTC)
    ledger = HouseholdObservationLedger(
        state_file=tmp_path / "api-obs" / "ledger.json",
        release_id="rel-api-test",
    )
    app = FastAPI()
    install_dashboard_household_observation(app, access=_StubAccess(), runtime=_StubRuntime(ledger))
    client = TestClient(app)

    assert client.get("/internal/v1/dashboard/household-observation").status_code == 401
    assert (
        client.post("/internal/v1/dashboard/household-observation/accept", json={"note": "ok"}).status_code
        == 401
    )

    headers = {"X-Owner-Token": "valid-owner-secret"}
    res = client.get("/internal/v1/dashboard/household-observation", headers=headers)
    assert res.status_code == 200
    assert res.headers.get("x-content-type-options") == "nosniff"
    assert res.headers.get("x-frame-options") == "DENY"
    body = res.json()
    assert body["contract"] == "EvaHouseholdObservation/v1"
    assert body["release_window"]["seven_day_gate_met"] is False

    # Query parameters must be rejected (422)
    assert (
        client.get("/internal/v1/dashboard/household-observation?extra=1", headers=headers).status_code
        == 422
    )

    # Missing X-Eva-Owner-Confirm header -> 403
    assert (
        client.post(
            "/internal/v1/dashboard/household-observation/accept",
            headers=headers,
            json={"note": "Zonder confirm header"},
        ).status_code
        == 403
    )

    confirm_headers = {**headers, "X-Eva-Owner-Confirm": "1"}

    # Cross-site fetch metadata or mismatched Origin -> 403
    assert (
        client.post(
            "/internal/v1/dashboard/household-observation/accept",
            headers={**confirm_headers, "Sec-Fetch-Site": "cross-site"},
            json={"note": "CSRF poging"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/internal/v1/dashboard/household-observation/accept",
            headers={**confirm_headers, "Origin": "https://evil.example"},
            json={"note": "Origin mismatch"},
        ).status_code
        == 403
    )

    # Wrong Content-Type -> 415
    assert (
        client.post(
            "/internal/v1/dashboard/household-observation/accept",
            headers={**confirm_headers, "Content-Type": "text/plain"},
            content=b'{"note":"ok"}',
        ).status_code
        == 415
    )

    # Extra body keys -> 422
    assert (
        client.post(
            "/internal/v1/dashboard/household-observation/accept",
            headers=confirm_headers,
            json={"note": "ok", "unexpected": True},
        ).status_code
        == 422
    )

    # Valid auth + CSRF headers before 7-day gate -> 409
    premature = client.post(
        "/internal/v1/dashboard/household-observation/accept",
        headers=confirm_headers,
        json={"note": "Probeer te vroeg"},
    )
    assert premature.status_code == 409

    # Tokenless access without runtime owner authority -> 403 fail-closed
    unconfigured_app = FastAPI()
    install_dashboard_household_observation(
        unconfigured_app,
        access=_StubAccess(token=None),
        runtime=_StubRuntime(ledger, owner_principal_id=None),
    )
    unconfigured_client = TestClient(unconfigured_app)
    assert unconfigured_client.get("/internal/v1/dashboard/household-observation").status_code == 403
    assert (
        unconfigured_client.post(
            "/internal/v1/dashboard/household-observation/accept",
            headers={"X-Eva-Owner-Confirm": "1"},
            json={"note": "Geen autoriteit"},
        ).status_code
        == 403
    )

    # Symlink refusal check (O_NOFOLLOW) on ledger path
    symlink_dir = tmp_path / "symlink-obs"
    symlink_dir.mkdir(mode=0o700)
    real_target = tmp_path / "outside.json"
    real_target.write_text("{}", encoding="utf-8")
    symlink_ledger = symlink_dir / "ledger.json"
    symlink_ledger.symlink_to(real_target)
    with pytest.raises(OSError):
        HouseholdObservationLedger(state_file=symlink_ledger, release_id="rel-sym")

    # Satisfy 7-day contiguous window and verify 200 owner acceptance via API
    now_utc = datetime.now(UTC)
    start_7d_ago = now_utc - timedelta(hours=170)
    with ledger._locked_state(write=True, now=now_utc) as st:
        st["release_started_at"] = start_7d_ago.strftime("%Y-%m-%dT%H:%M:%SZ")
    for d in range(7):
        ledger.record_turn(
            principal_id="owner-jaap" if d % 2 == 0 else "family-lola",
            channel="holostage",
            used_model=False,
            now=start_7d_ago + timedelta(days=d, hours=1),
        )
    accepted_res = client.post(
        "/internal/v1/dashboard/household-observation/accept",
        headers={**confirm_headers, "Origin": "http://testserver"},
        json={"note": "Zeven dagen stabiel gebruikt door Jaap en Lola"},
    )
    assert accepted_res.status_code == 200
    assert accepted_res.json()["release_window"]["f2_2_owner_accepted"] is True
