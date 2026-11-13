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
    assert any(r["perceived_mood"] == "druk" for r in res_stim.json()["pulse"]["residents"])


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


def test_e17_routine_counter_evidence_e18_foresight_e110_curiosity_and_e114_persistence(tmp_path) -> None:
    import json
    from dataclasses import replace
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.bootstrap.optional_model_providers import OptionalConversationModel, OptionalModelProviders
    from tests.model.test_pydantic_conversation_model import _identity, _memory, _turn

    persist_file = tmp_path / "living-cortex-state.json"
    engine1 = LivingEntityPulseEngine(persist_path=persist_file)
    updated_routine = engine1.record_routine_observation(
        "routine-healthy-mon-tue",
        is_exception=True,
        detail_nl="Vandaag eten we laat wegens een drukke middag.",
    )
    assert updated_routine.exceptions_count == 1
    assert updated_routine.norm_preserved is True
    assert "Uitzondering vandaag verwerkt" in updated_routine.adjusted_behavior_nl
    assert persist_file.is_file()

    # E1.14: New engine instance reloads persisted state across restart
    engine2 = LivingEntityPulseEngine(persist_path=persist_file)
    snap2 = engine2.snapshot()
    assert snap2.routines[0].exceptions_count == 1
    assert snap2.routines[0].norm_preserved is True
    assert len(snap2.foresight) >= 2
    assert len(snap2.curiosity_projects) >= 2

    # Conversational E1.7 exception handling, E1.8 foresight & E1.10 curiosity
    sub_dir = tmp_path / "model-providers"
    sub_dir.mkdir(parents=True, mode=0o700)
    (sub_dir / "subscription-llm-state.json").write_text(
        json.dumps({"selected_provider": "gemini_argon", "auto_fallback": True}),
        encoding="utf-8",
    )
    baseline = PydanticConversationModel(auth_file=tmp_path / "no-key")
    control = OptionalModelProviders(baseline, sub_dir)
    model = OptionalConversationModel(baseline, control)

    exc_turn = replace(
        _turn(),
        conversation_id="dashboard:owner",
        text="Vandaag een uitzondering op maandag: het loopt uit dus we koken niet gezond.",
    )
    res_exc = model.generate(exc_turn, _identity(), _memory(), ())
    assert "uitzondering" in (res_exc.text or "").lower()
    assert "behouden" in (res_exc.text or "").lower()

    fs_turn = replace(
        _turn(),
        conversation_id="dashboard:owner",
        text="Zie je nog knelpunten of een inhoudelijk alternatief in je vooruitblik?",
    )
    res_fs = model.generate(fs_turn, _identity(), _memory(), ())
    assert "vooruitblik" in (res_fs.text or "").lower()

    cur_turn = replace(
        _turn(),
        conversation_id="dashboard:owner",
        text="Waar ben je zelf nieuwsgierig naar in je eigen project?",
    )
    res_cur = model.generate(cur_turn, _identity(), _memory(), ())
    assert "werk zelf aan" in (res_cur.text or "").lower()


def test_expected_free_energy_actr_salience_e16_source_watches_and_e111_e112_responsibilities(tmp_path) -> None:
    import json
    from dataclasses import replace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from eva.adapters.api.dashboard_access import DashboardAccess
    from eva.adapters.api.dashboard_cortex import install_dashboard_cortex
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.application.native_fastpath import (
        compute_cognitive_salience_fast,
        compute_expected_free_energy_fast,
    )
    from eva.bootstrap.optional_model_providers import OptionalConversationModel, OptionalModelProviders
    from tests.model.test_pydantic_conversation_model import _identity, _memory, _turn

    # 1. Karl Friston Expected Free Energy G(π) & Boltzmann posterior P(π)
    winner_id, policies = compute_expected_free_energy_fast(
        0.78, 0.72, 0.65, 0.88, 0.45,
        max_receptivity=0.80,
        mean_uncertainty=0.15,
    )
    assert isinstance(winner_id, str)
    assert len(policies) == 5
    assert abs(sum(p["posterior_probability"] for p in policies) - 1.0) < 1e-3
    assert any(p["selected"] for p in policies)

    # 2. ACT-R + Stanford Generative Agents Tri-Factor Salience
    s_high = compute_cognitive_salience_fast(
        lexical_relevance=0.95,
        age_hours=1.0,
        trust_weight=0.95,
        is_correction_or_routine=True,
    )
    s_low = compute_cognitive_salience_fast(
        lexical_relevance=0.20,
        age_hours=120.0,
        trust_weight=0.35,
        is_correction_or_routine=False,
    )
    assert s_high > s_low
    assert 0.0 <= s_high <= 1.0

    # 3. E1.6 Source-Change Watches: callback deduplication & missing-access diagnosis
    persist_file = tmp_path / "living-cortex-sota.json"
    engine = LivingEntityPulseEngine(persist_path=persist_file)
    w1 = engine.record_source_watch_event(
        "watch-calendar-transition",
        event_payload_sha256="cb-dentist-1645-sha",
        summary_nl="Tandartsafspraak verplaatst naar 16:45",
    )
    assert w1.deduplicated_callbacks_count == 2  # baseline had 2

    # Duplicate reconnect callback with identical SHA-256 is deduplicated!
    w2 = engine.record_source_watch_event(
        "watch-calendar-transition",
        event_payload_sha256="cb-dentist-1645-sha",
        summary_nl="Tandartsafspraak verplaatst naar 16:45",
    )
    assert w2.deduplicated_callbacks_count == 3

    # Missing access is reported concretely without widening permissions
    w_missing = engine.record_source_watch_event(
        "watch-home-energy-sensor",
        event_payload_sha256="cb-missing-001",
        summary_nl="Geen geldige sessie",
        access_available=False,
    )
    assert w_missing.access_status == "missing_access"
    assert w_missing.missing_access_explanation_nl is not None
    assert "verbreed nooit zelfstandig rechten" in w_missing.missing_access_explanation_nl.lower()

    # 4. E1.11 & E1.12 Household Responsibilities & Provisions: adopt, pause, take_over, rollback
    r_adopted = engine.manage_household_responsibility(
        "household-provision:weekday-evening-calm",
        action="adopt",
    )
    assert r_adopted.lifecycle_state == "active"
    assert r_adopted.adoption_state == "explicitly_adopted"

    r_paused = engine.manage_household_responsibility(
        "household-provision:weekday-evening-calm",
        action="pause",
    )
    assert r_paused.lifecycle_state == "paused"
    assert r_paused.adoption_state == "paused_by_owner"

    r_rolled_back = engine.manage_household_responsibility(
        "household-provision:weekday-evening-calm",
        action="rollback",
    )
    assert r_rolled_back.adoption_state == "rolled_back"
    assert r_rolled_back.version == 1

    r_taken_over = engine.manage_household_responsibility(
        "household-provision:zero-token-night-guard",
        action="take_over",
    )
    assert r_taken_over.lifecycle_state == "paused"
    assert r_taken_over.adoption_state == "paused_by_owner"

    # 5. FastAPI Endpoints & Conversational integration
    class DummyRuntime:
        _living_pulse_engine = engine

    app = FastAPI()
    install_dashboard_cortex(app, access=DashboardAccess(None), runtime=DummyRuntime())
    client = TestClient(app)

    res_sw = client.post(
        "/internal/v1/dashboard/cortex/source-watch",
        json={
            "watch_id": "watch-calendar-transition",
            "event_payload_sha256": "cb-dentist-1645-sha",
            "summary_nl": "Tandartsafspraak verplaatst naar 16:45",
            "access_available": True,
        },
    )
    assert res_sw.status_code == 200
    sw_items = res_sw.json()["pulse"]["source_watches"]
    assert sw_items[0]["deduplicated_callbacks_count"] == 4

    res_rp = client.post(
        "/internal/v1/dashboard/cortex/responsibility",
        json={
            "artifact_id": "household-provision:weekday-evening-calm",
            "action": "adopt",
        },
    )
    assert res_rp.status_code == 200
    rp_items = res_rp.json()["pulse"]["responsibilities"]
    assert rp_items[0]["adoption_state"] == "explicitly_adopted"

    sub_dir = tmp_path / "model-providers"
    sub_dir.mkdir(parents=True, mode=0o700)
    (sub_dir / "subscription-llm-state.json").write_text(
        json.dumps({"selected_provider": "gemini_argon", "auto_fallback": True}),
        encoding="utf-8",
    )
    baseline = PydanticConversationModel(auth_file=tmp_path / "no-key")
    control = OptionalModelProviders(baseline, sub_dir)
    model = OptionalConversationModel(baseline, control)

    resp_turn = replace(
        _turn(),
        conversation_id="dashboard:owner",
        text="Welke voorzieningen en verantwoordelijkheden beheer je nu?",
    )
    res_resp = model.generate(resp_turn, _identity(), _memory(), ())
    assert "voorzieningen" in (res_resp.text or "").lower()

    sw_turn = replace(
        _turn(),
        conversation_id="dashboard:owner",
        text="Hoe staat het met bron-aandacht en missende toegang?",
    )
    res_sw_chat = model.generate(sw_turn, _identity(), _memory(), ())
    assert "ontdubbeld" in (res_sw_chat.text or "").lower() or "toegang" in (res_sw_chat.text or "").lower()

    # 6. Verify natural human responses on greetings in any language and non-question remarks
    for non_question_text in ("Hi", "Bonjour!", "Ik plof net lekker op de bank met koffie."):
        nq_turn = replace(
            _turn(),
            conversation_id="telegram:6034485735",
            text=non_question_text,
        )
        res_nq = model.generate(nq_turn, _identity(), _memory(), ())
        assert res_nq.action_call is None
        assert res_nq.text
        assert "curious_exploring" not in res_nq.text
        assert "lifecycle_state" not in res_nq.text
        assert "uitzoeken of klaarzetten" not in res_nq.text
        assert "direct mee aan de slag gaan" not in res_nq.text





