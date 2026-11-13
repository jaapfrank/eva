"""Tests for Eva's Bio-Homeostatic Pulse and Theory-of-Mind Engine."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from eva.application.living_entity_pulse import (
    HomeostaticDrives,
    LivingEntityPulseEngine,
    ResidentMindState,
)


def test_circadian_and_homeostatic_drive_evolution_is_bounded() -> None:
    drives = HomeostaticDrives(
        energy=0.50,
        social_warmth=0.50,
        curiosity=0.40,
        household_harmony=0.90,
        anticipation=0.25,
    )
    evolved = drives.evolve(
        elapsed_seconds=7200,
        hour_of_day=14.0,
        household_quiet=True,
        unresolved_tensions=0,
    )
    assert evolved.energy > drives.energy
    assert evolved.curiosity > drives.curiosity
    assert 0.0 <= evolved.household_harmony <= 1.0


def test_sensory_stimulus_shifts_mood_and_triggers_cortex_threshold() -> None:
    t0 = datetime(2026, 11, 13, 17, 30, tzinfo=UTC)
    engine = LivingEntityPulseEngine(activation_threshold=0.68, now=t0)
    engine.upsert_resident(
        ResidentMindState(
            principal_id="owner-1",
            display_name="Jaap",
            perceived_mood="gefocust",
            perceived_energy=0.80,
            social_receptivity=0.85,
            current_context_hypothesis="Rondt werkdag af",
            open_care_threads=("Gezinsdashboard vernieuwen",),
            updated_at=t0,
        )
    )
    snap = engine.apply_sensory_stimulus(
        kind="resident_arrived",
        intensity=0.9,
        now=t0 + timedelta(minutes=5),
    )
    assert snap.mood == "warm_engaged"
    assert snap.should_wake_cortex is True
    payload = snap.to_payload()
    assert payload["contract"] == "EvaLivingEntityPulse/v1"
    assert len(payload["residents"]) == 1


def test_active_inference_prediction_error_updates_resident_receptivity() -> None:
    t0 = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
    resident = ResidentMindState(
        principal_id="owner-1",
        display_name="Jaap",
        social_receptivity=0.90,
        prediction_confidence=0.85,
        updated_at=t0,
    )
    updated = resident.record_prediction_error(
        actual_receptivity=0.20,
        observed_mood="druk_bezet",
        now=t0 + timedelta(minutes=10),
    )
    assert updated.social_receptivity < resident.social_receptivity
    assert updated.perceived_mood == "druk_bezet"
    assert updated.prediction_confidence < resident.prediction_confidence
    assert updated.last_prediction_error > 0.5


def test_native_fastpath_and_pydantic_v2_cortex_routes(tmp_path, monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from eva.adapters.api.dashboard_access import DashboardAccess
    from eva.adapters.api.dashboard_cortex import install_dashboard_cortex
    from eva.application.native_fastpath import (
        NATIVE_ACCELERATOR_ACTIVE,
        compute_active_inference_fast,
        compute_homeostatic_step_fast,
        fast_canonical_sha256,
    )

    monkeypatch.setenv("EVA_MODEL_PROVIDER_DIRECTORY", str(tmp_path))
    monkeypatch.setenv("EVA_SUBSCRIPTION_STATE_FILE", str(tmp_path / "subscription-llm-state.json"))

    assert NATIVE_ACCELERATOR_ACTIVE is True
    drives, norm = compute_homeostatic_step_fast(
        0.75,
        0.60,
        0.55,
        0.85,
        0.40,
        hour_of_day=15.0,
        hours_since_interaction=3.0,
        pending_tasks=1,
        unresolved_alerts=0,
        dt_hours=0.5,
    )
    assert len(drives) == 5
    assert 0.0 <= norm <= 2.0

    inf = compute_active_inference_fast(0.80, 0.85, 0.4, 0.85, "brief_or_busy")
    assert inf[1] < 0.85  # receptivity lowered
    assert inf[3] > 0.30  # prediction error recorded
    assert len(fast_canonical_sha256({"a": 1, "b": "Eva"})) == 64

    class DummyRuntime:
        pass

    app = FastAPI()
    install_dashboard_cortex(app, access=DashboardAccess(None), runtime=DummyRuntime())
    client = TestClient(app)

    res_html = client.get("/internal/v1/dashboard/cortex")
    assert res_html.status_code == 200
    assert "Eva — Living Entity Cortex" in res_html.text

    res_state = client.get("/internal/v1/dashboard/cortex/state")
    assert res_state.status_code == 200
    body = res_state.json()
    assert body["contract"] == "EvaLivingCortexState/v1"
    assert body["subscriptions"]["api_keys_required"] is False
    assert len(body["subscriptions"]["providers"]) == 3

    res_sub = client.post(
        "/internal/v1/dashboard/cortex/subscription",
        json={"provider_id": "claude_subscription", "auto_fallback": True},
    )
    assert res_sub.status_code == 200
    assert res_sub.json()["subscriptions"]["selected_provider"] == "claude_subscription"

    res_stim = client.post(
        "/internal/v1/dashboard/cortex/stimulus",
        json={
            "kind": "resident_feedback",
            "actual_receptivity": 0.2,
            "observed_mood": "druk",
            "context_hypothesis": "In vergadering",
        },
    )
    assert res_stim.status_code == 200
    assert res_stim.json()["pulse"]["residents"][0]["perceived_mood"] == "druk"


def test_subscription_cortex_two_phase_hindsight_recall_and_tool_synthesis(tmp_path) -> None:
    import json
    from dataclasses import replace
    from types import SimpleNamespace
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.application.conversation import (
        DEFERRED_MEMORY_PROJECTION,
        MEMORY_SEARCH_ACTION,
        ConversationActionReceipt,
    )
    from eva.bootstrap.optional_model_providers import OptionalConversationModel, OptionalModelProviders
    from tests.model.test_pydantic_conversation_model import _identity, _memory, _turn

    sub_dir = tmp_path / "model-providers"
    sub_dir.mkdir(parents=True, mode=0o700)
    (sub_dir / "subscription-llm-state.json").write_text(
        json.dumps({"selected_provider": "gemini_argon", "auto_fallback": True}),
        encoding="utf-8",
    )

    baseline = PydanticConversationModel(auth_file=tmp_path / "no-key")
    control = OptionalModelProviders(baseline, sub_dir)
    model = OptionalConversationModel(baseline, control)
    turn = replace(_turn(), conversation_id="dashboard:owner", text="Wat weet je over Lola en gezond eten op maandag?")

    # Phase 0: with deferred memory projection, requests search_personal_memory
    deferred_mem = replace(
        _memory(),
        recall=_memory().recall.model_copy(update={"projection_version": DEFERRED_MEMORY_PROJECTION}),
        confirmed_facts=(),
    )
    phase0 = model.generate(turn, _identity(), deferred_mem, ())
    assert phase0.action_call is not None
    assert phase0.action_call.name == MEMORY_SEARCH_ACTION
    assert phase0.provider == "gemini-argon:gemini-3.1-pro-argon"

    # Phase 1: with recalled Hindsight facts, synthesizes grounded Dutch answer
    fact = SimpleNamespace(
        fact_id="fact-lola-1",
        value={"text": "Op maandag en dinsdag probeert Jaap iets gezonds te maken omdat Lola dan thuis is."},
    )
    recalled_mem = replace(_memory(), confirmed_facts=(fact,))
    mem_receipt = ConversationActionReceipt(
        call=phase0.action_call,
        output={"result_count": 1, "conversation_exchanges": []},
    )
    phase1 = model.generate(
        turn,
        _identity(),
        recalled_mem,
        (),
        completed_action=mem_receipt,
    )
    assert phase1.action_call is None
    assert "Lola" in phase1.text and "maandag" in phase1.text
    assert phase1.provider == "gemini-argon:gemini-3.1-pro-argon"


