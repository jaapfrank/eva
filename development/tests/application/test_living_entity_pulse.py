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
    for non_question_text in ("Hi", "Bonjour!", "Hoe is het?", "Ik plof net lekker op de bank met koffie."):
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
        assert "ik ben hier om je te ondersteunen" not in res_nq.text.lower()


def test_jepa_world_model_titans_synaptic_memory_sleep_consolidation_and_multi_brain_cloud_failover(
    tmp_path, monkeypatch
) -> None:
    import json
    from dataclasses import replace
    import httpx2
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from eva.adapters.api.dashboard_access import DashboardAccess
    from eva.adapters.api.dashboard_cortex import install_dashboard_cortex
    from eva.adapters.model.contracts import ConversationModelError
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.application.native_fastpath import (
        compute_concept_vector_fast,
        simulate_world_model_fast,
        update_titans_synaptic_memory_fast,
    )
    from eva.bootstrap.optional_model_providers import OptionalConversationModel, OptionalModelProviders
    from tests.model.test_pydantic_conversation_model import _identity, _memory, _turn

    # 1. C/SIMD JEPA Causal World Model simulation
    wm_action, wm_trajs, wm_next = simulate_world_model_fast(
        energy=0.82,
        receptivity=0.86,
        cognitive_load=0.18,
        task_urgency=0.78,
        friction=0.42,
        trust=0.92,
    )
    assert wm_action == "stage_action_jarvis"
    assert len(wm_trajs) == 5
    assert wm_next["task_urgency"] < 0.78
    assert wm_next["friction"] < 0.42

    # 2. C/SIMD Titans O(1) 8x8 fast-weights surprise-gated synaptic convergence
    m = [0.0] * 64
    s = [0.0] * 64
    k_vec = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    v_vec = [0.0, 0.9, 0.0, 0.8, 0.0, 0.0, 0.0, 0.0]
    m, s, pred0, surp0, frob0 = update_titans_synaptic_memory_fast(m, s, k_vec, v_vec)
    assert surp0 > 0.10
    assert frob0 > 0.0
    for _ in range(14):
        m, s, pred1, surp1, frob1 = update_titans_synaptic_memory_fast(m, s, k_vec, v_vec)
    assert surp1 < surp0
    assert pred1[1] > 0.75

    # 3. Language-agnostic 8D Concept Space & Neuro-Symbolic Guard
    for social_txt in ("Hi", "Bonjour!", "Hola Eva", "Hoe is het?", "How are you?"):
        c = compute_concept_vector_fast(social_txt)
        assert c["is_non_task_social"] is True
        assert c["requires_memory_lookup"] is False
    c_q = compute_concept_vector_fast("Wat weet je uit ons geheugen over Lola op maandag?")
    assert c_q["is_non_task_social"] is False
    assert c_q["requires_memory_lookup"] is True

    # 4. Sleep Consolidation ("Dromen"), Jarvis Staged Actions & Persistence
    persist_file = tmp_path / "living-cortex-jarvis.json"
    engine = LivingEntityPulseEngine(persist_path=persist_file)
    engine.record_turn(principal_id="person:jaap", user_text="Hoe is het?")
    sleep_summary = engine.run_sleep_consolidation()
    assert sleep_summary.cycles_completed >= 2
    assert "Droomcyclus" in sleep_summary.dream_insight_nl

    staged = engine.decide_jarvis_staged_action("jarvis-stage-mon-tue-menu", decision="confirm")
    assert staged.status == "confirmed"

    reloaded = LivingEntityPulseEngine(persist_path=persist_file).snapshot()
    assert reloaded.last_sleep_consolidation.cycles_completed == sleep_summary.cycles_completed
    assert reloaded.synaptic_memory.updates_count >= 2
    assert any(a.action_id == "jarvis-stage-mon-tue-menu" and a.status == "confirmed" for a in reloaded.jarvis_staged_actions)

    # 5. Cortex Cockpit UI & Endpoints (no fake "1-Klik Zonder API-Key" text, real Cloud Configurator)
    mp_dir = tmp_path / "mp-cortex"
    monkeypatch.setenv("EVA_MODEL_PROVIDER_DIRECTORY", str(mp_dir))
    monkeypatch.setenv("EVA_SUBSCRIPTION_STATE_FILE", str(mp_dir / "subscription-llm-state.json"))

    class DummyRuntime:
        _living_pulse_engine = engine

    app = FastAPI()
    install_dashboard_cortex(app, access=DashboardAccess(None), runtime=DummyRuntime())
    client = TestClient(app)

    html = client.get("/internal/v1/dashboard/cortex").text
    assert "Van context naar actie." in html
    assert "Multi-Brein Cloud &amp; Optioneel Lokaal LLM Beheer" in html or "Multi-Brein Cloud & Optioneel Lokaal LLM Beheer" in html
    assert "JEPA Causaal Wereldmodel" in html
    assert "Titans O(1) Synaptisch Geheugen" in html
    assert "1-Klik Zonder API-Key" not in html
    assert "1-Klik Abonnements-LLM Kiezer" not in html

    res_glb = client.get("/internal/v1/dashboard/cortex/assets/eva.glb")
    assert res_glb.status_code == 200
    assert res_glb.headers["content-type"] == "model/gltf-binary"
    assert len(res_glb.content) > 5_000_000

    for js_asset in ("three.core.min.js", "three.module.min.js", "BufferGeometryUtils.js", "GLTFLoader.js"):
        res_js = client.get(f"/internal/v1/dashboard/cortex/assets/{js_asset}")
        assert res_js.status_code == 200
        assert "javascript" in res_js.headers["content-type"]

    assert client.get("/internal/v1/dashboard/cortex/assets/unknown.txt").status_code == 404

    res_cp = client.post(
        "/internal/v1/dashboard/cortex/cloud-provider",
        json={
            "action": "configure_and_activate",
            "kind": "gemini",
            "label": "Google Gemini Cloud",
            "model": "gemini-2.5-pro",
            "endpoint": "https://generativelanguage.googleapis.com/v1beta/openai",
            "api_key": "synthetic-gemini-key-never-echoed",
            "connection_id": "",
        },
    )
    assert res_cp.status_code == 200
    cp_json_str = json.dumps(res_cp.json())
    assert "synthetic-gemini-key-never-echoed" not in cp_json_str
    assert any(c["kind"] == "gemini" for c in res_cp.json()["cloud_providers"]["connections"])

    res_sleep = client.post("/internal/v1/dashboard/cortex/sleep-consolidate", json={})
    assert res_sleep.status_code == 200
    assert res_sleep.json()["pulse"]["last_sleep_consolidation"]["cycles_completed"] >= 3

    res_jarvis = client.post(
        "/internal/v1/dashboard/cortex/jarvis-action",
        json={"action_id": "jarvis-stage-cloud-failover-guard", "decision": "confirm"},
    )
    assert res_jarvis.status_code == 200

    # 6. Multi-Brain Cloud Failover: when primary OpenAI Codex hits 429, fail over to verified Gemini Cloud!
    gemini_calls = []

    def gemini_mock_handler(request):
        body = json.loads(request.content)
        gemini_calls.append(body)
        if not body.get("stream"):
            name = body["tools"][0]["function"]["name"]
            return httpx2.Response(
                200,
                json={
                    "id": "chk-1",
                    "object": "chat.completion",
                    "created": 1,
                    "model": body["model"],
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "call_chk",
                                        "type": "function",
                                        "function": {"name": name, "arguments": '{"echoed":"eva-provider-check"}'},
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28},
                },
            )
        chunk = {
            "id": "gem-stream-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": body["model"],
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": "Hoi Jaap! Ik draai nu naadloos via Google Gemini Cloud."},
                    "finish_reason": "stop",
                }
            ],
        }
        usage = {
            "id": "gem-stream-1",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": body["model"],
            "choices": [],
            "usage": {"prompt_tokens": 42, "completion_tokens": 14, "total_tokens": 56},
        }
        wire = f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(usage)}\n\ndata: [DONE]\n\n"
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=wire)

    failover_dir = tmp_path / "failover-providers"
    failover_dir.mkdir(parents=True, mode=0o700)
    (failover_dir / "subscription-llm-state.json").write_text(
        json.dumps({"selected_provider": "openai_codex", "auto_fallback": True}),
        encoding="utf-8",
    )
    baseline = PydanticConversationModel(auth_file=tmp_path / "no-codex")
    control = OptionalModelProviders(
        baseline,
        failover_dir,
        transport=httpx2.MockTransport(gemini_mock_handler),
    )
    gem_id = control.settings.configure(
        kind="gemini",
        label="Gemini Cloud Backup",
        model="gemini-2.5-pro",
        endpoint="",
        api_key="synthetic-gemini-backup-key",
    )
    chk_status = control.check(gem_id)
    assert chk_status["connections"][0]["check_state"] == "ready"

    # Simulate primary OpenAI Codex being selected as default (`selected_id == None`) and hitting HTTP 429:
    orig_baseline_gen = baseline.generate

    def codex_429_If_no_custom_backend(self_or_turn, *a, **kw):
        raise ConversationModelError("HTTP 429 usage_limit_reached on primary codex")

    # Note: OptionalConversationModel._run_configured_connection copies baseline and sets _model_backend;
    # so only fail when baseline itself (without OptionalBackend) is called!
    def smart_gen(turn_arg, *a, **kw):
        raise ConversationModelError("HTTP 429 usage_limit_reached")

    # Monkeypatch baseline.credential_status to report ready=True so baseline is tried first, then fails with 429!
    from types import SimpleNamespace
    monkeypatch.setattr(baseline, "credential_status", lambda: SimpleNamespace(ready=True, state="ready"))
    orig_cls_gen = PydanticConversationModel.generate

    def conditional_gen(self_model, turn_arg, *a, **kw):
        if self_model is baseline:
            raise ConversationModelError("HTTP 429 usage_limit_reached")
        return orig_cls_gen(self_model, turn_arg, *a, **kw)

    monkeypatch.setattr(PydanticConversationModel, "generate", conditional_gen)

    multi_brain = OptionalConversationModel(baseline, control)
    failover_turn = replace(_turn(), conversation_id="telegram:6034485735", text="Hoi Eva, hoe gaat het?")
    failover_res = multi_brain.generate(failover_turn, _identity(), _memory(), ())
    assert failover_res.provider == "gemini:gemini-2.5-pro"
    assert "Google Gemini Cloud" in failover_res.text






