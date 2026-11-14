"""Tests for sensory revalidation availability silence, lease alignment, and owner cockpit observation summary."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from eva.adapters.api.dashboard_access import DashboardAccess
from eva.adapters.api.dashboard_holostage import install_dashboard_holostage
from eva.adapters.connectors.home_assistant_events import HomeAssistantEventStream
from eva.application.conversation import IdentityProfile
from eva.application.free_will import FreeWillDecision, SensoryDecisionClaim, SensoryDecisionWorker
from eva.application.holostage import HoloStageManager
from eva.application.household_observation import HouseholdObservationLedger
from eva.application.sensory import SensoryEvent, SensoryObservationCurrentlyUnavailable


def test_ha_revalidate_distinguishes_authoritative_unavailable_from_malformed_or_future(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 14, 15, 34, 5, tzinfo=UTC)
    source = HomeAssistantEventStream(
        base_url="http://ha.test",
        token="fixture-token",
        admitted_domains=("binary_sensor",),
    )
    event = SensoryEvent(
        "home_assistant",
        "home-assistant:roam-1",
        "state_changed",
        now - timedelta(minutes=15),
        {"entity_id": "binary_sensor.sonos_roam_power", "old_state": "on", "new_state": "off"},
    )

    # 1. Authoritative HA "unavailable" / "unknown" raises SensoryObservationCurrentlyUnavailable (subclass of RuntimeError)
    for offline_state in ("unavailable", "unknown"):
        monkeypatch.setattr(
            source._states,
            "read_entity_state",
            lambda **kw: {"state": offline_state, "last_updated": "2026-11-14T15:00:00+00:00"},
        )
        with pytest.raises(SensoryObservationCurrentlyUnavailable) as exc_info:
            source.revalidate(event, now=now)
        assert isinstance(exc_info.value, RuntimeError)

    # 2. Sub-second HA update within the same whole-second runtime clock succeeds cleanly
    monkeypatch.setattr(
        source._states,
        "read_entity_state",
        lambda **kw: {
            "state": "on",
            "last_updated": "2026-11-14T15:34:05.789123+00:00",
            "attributes": {"device_class": "battery_charging", "friendly_name": "Sonos Roam Charging"},
        },
    )
    checked = source.revalidate(event, now=now)
    assert checked.event_type == "observation_revalidated"
    assert checked.payload["current_state"] == "on"

    # 3. Future whole second (+1s) still raises plain RuntimeError, never SensoryObservationCurrentlyUnavailable
    monkeypatch.setattr(
        source._states,
        "read_entity_state",
        lambda **kw: {"state": "on", "last_updated": "2026-11-14T15:34:06.000000+00:00"},
    )
    with pytest.raises(RuntimeError) as future_exc:
        source.revalidate(event, now=now)
    assert not isinstance(future_exc.value, SensoryObservationCurrentlyUnavailable)

    # 4. Missing last_updated raises plain RuntimeError, never SensoryObservationCurrentlyUnavailable
    monkeypatch.setattr(
        source._states,
        "read_entity_state",
        lambda **kw: {"state": "unavailable"},
    )
    with pytest.raises(RuntimeError) as malformed_exc:
        source.revalidate(event, now=now)
    assert not isinstance(malformed_exc.value, SensoryObservationCurrentlyUnavailable)


def test_worker_silences_stale_unavailable_entity_without_memory_model_or_recovery_failure() -> None:
    now = datetime(2026, 11, 14, 16, 0, 0, tzinfo=UTC)
    stale_event = SensoryEvent(
        "home_assistant",
        "home-assistant:history:batch1",
        "historical_observation_batch",
        now - timedelta(hours=2),
        {"entity_id": "binary_sensor.sonos_roam_power", "historical_observations": []},
    )
    claim = SensoryDecisionClaim(
        event=stale_event,
        thread_id="sensory:home-assistant:history:batch1",
        operation_id="home-assistant:history:batch1",
        revision=2,
        retry_count=0,
    )
    completed: list[tuple[SensoryDecisionClaim, FreeWillDecision]] = []
    failures: list[Exception] = []

    class FakeStore:
        def claim_next(self, *, now, include_waiting=True):
            return None

        def claim_next_revalidation(self, *, now):
            return claim if not completed and not failures else None

        def complete(self, c, decision, *, now):
            completed.append((c, decision))

        def fail(self, c, error, *, now):
            failures.append(error)

    class OfflineRevalidator:
        def revalidate(self, event, *, now):
            raise SensoryObservationCurrentlyUnavailable("Home Assistant current state is unavailable")

    class ForbiddenIO:
        def recall_for_presence(self, query):
            pytest.fail("offline entity must not query memory")

        def evaluate_sensory_event(self, *args, **kwargs):
            pytest.fail("offline entity must not invoke model")

    worker = SensoryDecisionWorker(
        FakeStore(),  # type: ignore[arg-type]
        ForbiddenIO(),  # type: ignore[arg-type]
        ForbiddenIO(),
        IdentityProfile("Eva", "Huisgenoot", ("Wees eerlijk.",), 80),
        tenant_id="tenant-1",
        installation_id="inst-1",
        principal_id="owner-jaap",
        revalidator=OfflineRevalidator(),
    )

    assert worker.run_once(now=now) == "silence"
    assert failures == []
    assert len(completed) == 1
    assert completed[0][1].disposition == "silence"
    assert completed[0][1].urgency == 0
    assert completed[0][1].provider == "eva:revalidation-state-unavailable"

    # By contrast, a genuine transport error still routes to store.fail()
    completed.clear()

    class BrokenTransportRevalidator:
        def revalidate(self, event, *, now):
            raise TimeoutError("HA read timed out")

    worker_broken = SensoryDecisionWorker(
        FakeStore(),  # type: ignore[arg-type]
        ForbiddenIO(),  # type: ignore[arg-type]
        ForbiddenIO(),
        IdentityProfile("Eva", "Huisgenoot", ("Wees eerlijk.",), 80),
        tenant_id="tenant-1",
        installation_id="inst-1",
        principal_id="owner-jaap",
        revalidator=BrokenTransportRevalidator(),
    )
    assert worker_broken.run_once(now=now) == "failed"
    assert len(failures) == 1
    assert isinstance(failures[0], TimeoutError)


def test_holostage_state_exposes_observation_summary_only_to_explicit_owner(
    tmp_path: Path,
) -> None:
    mgr = HoloStageManager(directory=tmp_path / "holostage")
    mgr.set_public_stage_origin("https://jaap.diplodocus-goldeye.ts.net")
    ledger = HouseholdObservationLedger(
        state_file=tmp_path / "obs" / "ledger-v1.json",
        release_id="rel-test-20261114",
    )
    ledger.record_turn(
        principal_id="owner-jaap",
        channel="holostage",
        used_model=True,
        known_cost_eur=0.001,
    )
    runtime = SimpleNamespace(
        _holostage_manager=mgr,
        _household_observation=ledger,
        _dashboard_owner_principal_id="owner-jaap",
    )
    owner_tok = "owner-secret-token-" + "x" * 24
    app = FastAPI()
    install_dashboard_holostage(
        app,
        access=DashboardAccess(token=owner_tok),
        runtime=runtime,
    )
    client = TestClient(app)

    # 1. Explicit owner receives both public_stage_origin and household_observation_summary
    res_owner = client.get(
        "/internal/v1/dashboard/holostage/state?mode=cockpit",
        headers={"Authorization": f"Bearer {owner_tok}"},
    )
    assert res_owner.status_code == 200
    owner_body = res_owner.json()
    assert owner_body["public_stage_origin"] == "https://jaap.diplodocus-goldeye.ts.net"
    assert owner_body["household_observation_summary"]["totals"]["turns_jaap"] == 1
    assert owner_body["household_observation_summary"]["release_window"]["required_contiguous_hours"] == 168
    assert owner_body["household_observation_summary"]["owner_accepted"] is False

    # 2. Paired ambient / private stage device does NOT receive household_observation_summary
    issued = mgr.issue_device_token(
        label="Woonkamer TV",
        form_factor="tv_cast",
        room_id="woonkamer",
        allow_private_cards_when_alone=False,
        allow_owner_voice=False,
    )
    pair_token = issued["pair_path"].split("#", 1)[1]
    sess = client.post(
        "/internal/v1/dashboard/holostage/session",
        headers={"Content-Type": "application/json", "X-HoloStage-Client": "1"},
        json={"token": pair_token},
    )
    assert sess.status_code == 200
    res_tv = client.get("/internal/v1/dashboard/holostage/state?mode=tv_cast")
    assert res_tv.status_code == 200
    tv_body = res_tv.json()
    assert tv_body["public_stage_origin"] is None
    assert "household_observation_summary" not in tv_body

    # 3. Bare public Funnel visit shows clear pairing gate + paste form; :443 Host + HTTPS sets Secure cookie
    fresh_client = TestClient(app)
    gate = fresh_client.get(
        "/internal/v1/dashboard/holostage?mode=pocket",
        headers={
            "Host": "jaap.diplodocus-goldeye.ts.net",
            "X-HoloStage-Lan-Proxy": "1",
            "X-HoloStage-Https": "1",
        },
    )
    assert gate.status_code == 200
    assert "Apparaat nog niet gekoppeld" in gate.text
    assert 'id="pair-form"' in gate.text
    assert "Gezinshoofd Cockpit (localhost:44711)" in gate.text

    sess_https = fresh_client.post(
        "/internal/v1/dashboard/holostage/session",
        headers={
            "Host": "jaap.diplodocus-goldeye.ts.net:443",
            "Origin": "https://jaap.diplodocus-goldeye.ts.net",
            "Content-Type": "application/json",
            "X-HoloStage-Client": "1",
            "X-HoloStage-Lan-Proxy": "1",
            "X-HoloStage-Https": "1",
        },
        json={"token": pair_token},
    )
    assert sess_https.status_code == 200
    set_cookie_hdr = sess_https.headers.get("set-cookie", "").lower()
    assert "secure" in set_cookie_hdr
    assert "httponly" in set_cookie_hdr
    assert "samesite=strict" in set_cookie_hdr

