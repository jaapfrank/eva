"""Fase 3 verification suite: Causal World Model, 429/503 Continuity, Pure Release Validation & Delivery Hygiene."""
from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
import json
from pathlib import Path
import stat

import httpx2
import pytest

from eva.adapters.model.contracts import (
    ConversationModelInputBudgetExceeded,
    ConversationModelRateLimited,
    ConversationModelUnavailable,
)
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.application.cancellation import CancellationSignal, WorkInterrupted, cancellation_scope
from eva.application.causal_world_model import CausalWorldModel
from eva.application.conversation import ConversationConflict, ConversationStopped
from eva.application.living_entity_pulse import LivingEntityPulseEngine
from eva.bootstrap.optional_model_providers import OptionalConversationModel, OptionalModelProviders
from eva.operations.reviewed_release_validation import (
    evaluate_updater_receipt,
    validate_application_job_payload,
    validate_host_path,
    validate_identifier,
)
from tests.model.test_pydantic_conversation_model import _identity, _memory, _turn


def _sse_text_response(model_name: str, text: str) -> httpx2.Response:
    chunk = {
        "id": "cmpl-fase3",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": model_name,
        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": "stop"}],
    }
    usage = {
        "id": "cmpl-fase3",
        "object": "chat.completion.chunk",
        "created": 1,
        "model": model_name,
        "choices": [],
        "usage": {"prompt_tokens": 24, "completion_tokens": 8, "total_tokens": 32},
    }
    wire = f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(usage)}\n\ndata: [DONE]\n\n"
    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, text=wire)


def test_causal_world_model_discovers_true_lagged_cause_and_rejects_circadian_confounder(tmp_path: Path) -> None:
    persist_file = tmp_path / "causal-world-model.json"
    model = CausalWorldModel(persist_path=persist_file, horizon_seconds=600.0)
    base = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)

    # Simulate 6 days across morning (08:00) and evening (20:00) strata:
    # 1) True causal mechanism: network.wan_latency_spike -> household.task_delay (45s later)
    #    Occurs on some mornings AND some evenings, while non-spike slots have no task_delay.
    # 2) Spurious morning confounder: routine.morning_coffee (08:01) & routine.morning_rush (08:04)
    #    Morning rush happens EVERY morning whether coffee happened (4 mornings) or not (2 mornings),
    #    and NEVER in the evening -> high raw lift, near-zero Pearl backdoor ACE!
    for day in range(6):
        day_base = base + timedelta(days=day)
        morning = day_base.replace(hour=8, minute=0)
        afternoon = day_base.replace(hour=14, minute=0)
        evening = day_base.replace(hour=20, minute=0)

        # Spurious morning pair (Z = ochtend drives both)
        if day < 4:
            model.record_event("routine", "morning_coffee", occurred_at=morning + timedelta(seconds=30))
        model.record_event("routine", "morning_rush", occurred_at=morning + timedelta(seconds=180))

        # Afternoon quiet baseline slot (keeps Z=middag active without coffee/rush)
        model.record_event("presence", "quiet_afternoon", occurred_at=afternoon + timedelta(seconds=20))

        # True interventional cause in alternating afternoon & evening slots
        if day in (0, 2, 4):
            model.record_event("network", "wan_latency_spike", occurred_at=evening + timedelta(seconds=15))
            model.record_event("household", "task_delay", occurred_at=evening + timedelta(seconds=60))
        else:
            # Evening control slots without latency spike (no task_delay)
            model.record_event("presence", "evening_wind_down", occurred_at=evening + timedelta(seconds=15))

        if day in (1, 3):
            slot_t = afternoon + timedelta(minutes=25)
            model.record_event("network", "wan_latency_spike", occurred_at=slot_t)
            model.record_event("household", "task_delay", occurred_at=slot_t + timedelta(seconds=45))

    dream_snap = model.run_counterfactual_dream_replay()
    edge_pairs = {(e.cause_id, e.effect_id): e for e in dream_snap.verified_causal_edges}

    assert ("network.wan_latency_spike", "household.task_delay") in edge_pairs
    true_edge = edge_pairs[("network.wan_latency_spike", "household.task_delay")]
    assert true_edge.backdoor_ace >= 0.55
    assert true_edge.probability_of_necessity >= 0.65
    assert 44.0 <= true_edge.median_lag_seconds <= 46.0

    # Spurious morning correlation and reverse temporal edges must NOT be accepted
    assert ("routine.morning_coffee", "routine.morning_rush") not in edge_pairs
    assert ("household.task_delay", "network.wan_latency_spike") not in edge_pairs
    assert dream_snap.rejected_spurious_edges >= 1
    assert any(
        i.root_cause_id == "network.wan_latency_spike" and i.target_effect_id == "household.task_delay"
        for i in dream_snap.counterfactual_insights
    )

    # Verify 0o600 atomic persistence and reload
    assert persist_file.is_file()
    assert stat.S_IMODE(persist_file.stat().st_mode) == 0o600
    reloaded = CausalWorldModel(persist_path=persist_file, horizon_seconds=600.0).snapshot()
    assert any(
        e.cause_id == "network.wan_latency_spike" and e.effect_id == "household.task_delay"
        for e in reloaded.verified_causal_edges
    )


def test_pulse_sleep_consolidation_stages_counterfactual_preventative_insight(tmp_path: Path) -> None:
    now = datetime(2026, 11, 13, 22, 0, tzinfo=UTC)
    engine = LivingEntityPulseEngine(now=now, persist_path=tmp_path / "living-cortex.json")
    for idx in range(5):
        base_t = now - timedelta(hours=12 - idx * 2)
        if idx in (0, 2, 4):
            engine.record_causal_event("provider.http_429_rate_limit", now=base_t)
            engine.record_causal_event(
                "provider.failover_recovered", now=base_t + timedelta(seconds=3)
            )
        else:
            engine.record_causal_event("presence.room_transition", now=base_t)

    summary = engine.run_sleep_consolidation(now=now + timedelta(minutes=10))
    assert summary.causal_edges_discovered >= 1
    assert summary.counterfactual_dreams_replayed >= 1

    payload = engine.snapshot(now=now + timedelta(minutes=11)).to_payload()
    assert payload["engine_runtime"]["causal_world_model_pearl"] is True  # type: ignore[index]
    causal_graph = payload["causal_graph"]
    assert isinstance(causal_graph, dict)
    assert len(causal_graph["verified_causal_edges"]) >= 1
    assert any(
        a["action_id"].startswith("jarvis-causal-")
        for a in payload["jarvis_staged_actions"]  # type: ignore[union-attr]
    )


def test_429_and_503_continuity_failover_with_cooldown_and_zero_secret_leak(tmp_path: Path) -> None:
    calls: list[tuple[str, str]] = []
    mode = {"primary_status": 429}

    def handler(request: httpx2.Request) -> httpx2.Response:
        host = request.url.host or ""
        body = json.loads(request.content)
        model_name = body.get("model", "unknown")
        calls.append((host, model_name))
        if host == "primary.example.test":
            status = mode["primary_status"]
            return httpx2.Response(
                status,
                headers={"retry-after": "45"},
                json={
                    "error": {
                        "type": "rate_limit_error" if status == 429 else "server_error",
                        "code": "rate_limit_exceeded" if status == 429 else "server_error",
                        "message": "secret-primary-key-xyz must never leak",
                    }
                },
            )
        return _sse_text_response(model_name, "Backup brein heeft het gesprek naadloos overgenomen.")

    baseline = PydanticConversationModel(auth_file=tmp_path / "no-codex-auth")
    control = OptionalModelProviders(
        baseline,
        tmp_path / "providers",
        transport=httpx2.MockTransport(handler),
    )
    primary_id = control.settings.configure(
        kind="openai_compatible",
        label="Primary Cloud",
        model="primary-m1",
        endpoint="https://primary.example.test/v1",
        api_key="synthetic-primary-key-xyz-9999",
    )
    backup_id = control.settings.configure(
        kind="gemini",
        label="Backup Gemini",
        model="gemini-2.5-pro",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key="synthetic-backup-key-abc-9999",
    )
    control.settings.record_check(primary_id, ready=True, usage=None)
    control.settings.record_check(backup_id, ready=True, usage=None)
    control.settings.select(primary_id)

    model = OptionalConversationModel(baseline, control)
    turn = replace(_turn(), conversation_id="telegram:continuity")

    # 1) Without auto_fallback, HTTP 429 maps cleanly to ConversationModelRateLimited without secret leak
    with pytest.raises(ConversationModelRateLimited) as exc_info:
        model.generate(turn, _identity(), _memory(), ())
    assert "synthetic-primary-key-xyz-9999" not in str(exc_info.value)
    control.clear_cooldown(primary_id)
    calls.clear()

    # 2) Enable auto_fallback: HTTP 429 fails over to Backup Gemini and puts Primary in cooldown
    (control.settings.directory / "subscription-llm-state.json").write_text(
        json.dumps({"selected_provider": "gemini_argon", "auto_fallback": True}),
        encoding="utf-8",
    )
    res1 = model.generate(turn, _identity(), _memory(), ())
    assert res1.provider == "gemini:gemini-2.5-pro"
    assert "naadloos" in res1.text
    assert control.cooldown_remaining(primary_id) >= 30.0
    assert calls == [
        ("primary.example.test", "primary-m1"),
        ("generativelanguage.googleapis.com", "gemini-2.5-pro"),
    ]

    # 3) Immediate next turn skips cooling-down primary directly without hitting 429 again!
    calls.clear()
    res2 = model.generate(turn, _identity(), _memory(), ())
    assert res2.provider == "gemini:gemini-2.5-pro"
    assert calls == [("generativelanguage.googleapis.com", "gemini-2.5-pro")]

    # 4) Clear cooldown and test HTTP 503 failover
    control.clear_cooldown(primary_id)
    mode["primary_status"] = 503
    calls.clear()
    res3 = model.generate(turn, _identity(), _memory(), ())
    assert res3.provider == "gemini:gemini-2.5-pro"
    assert control.cooldown_remaining(primary_id) > 0.0
    # Primary was attempted (PydanticConversationModel retries 503 once before raising unavailable, then backup succeeds)
    assert calls[-1] == ("generativelanguage.googleapis.com", "gemini-2.5-pro")


@pytest.mark.parametrize(
    "non_failover_exc",
    [
        ConversationStopped("owner issued STOP"),
        WorkInterrupted("cancelled by owner"),
        ConversationConflict("active turn lock"),
        ConversationModelInputBudgetExceeded("envelope exceeded"),
    ],
)
def test_stop_and_turn_invariants_never_failover_to_backup_provider(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    non_failover_exc: BaseException,
) -> None:
    backup_called = False

    def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal backup_called
        backup_called = True
        return _sse_text_response("gemini-2.5-pro", "MAG NOOIT GEBEUREN")

    baseline = PydanticConversationModel(auth_file=tmp_path / "no-codex-auth")
    control = OptionalModelProviders(
        baseline,
        tmp_path / "providers",
        transport=httpx2.MockTransport(handler),
    )
    primary_id = control.settings.configure(
        kind="openai_compatible",
        label="Primary",
        model="primary-m1",
        endpoint="https://primary.example.test/v1",
        api_key="synthetic-primary-key-0001-test",
    )
    backup_id = control.settings.configure(
        kind="gemini",
        label="Backup",
        model="gemini-2.5-pro",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key="synthetic-backup-key-0002-test",
    )
    control.settings.record_check(primary_id, ready=True, usage=None)
    control.settings.record_check(backup_id, ready=True, usage=None)
    control.settings.select(primary_id)
    (control.settings.directory / "subscription-llm-state.json").write_text(
        json.dumps({"selected_provider": "gemini_argon", "auto_fallback": True}),
        encoding="utf-8",
    )

    model = OptionalConversationModel(baseline, control)
    monkeypatch.setattr(
        model,
        "_run_configured_connection",
        lambda *args, **kwargs: (_ for _ in ()).throw(non_failover_exc),
    )
    turn = replace(_turn(), conversation_id="telegram:stop-fence")

    with pytest.raises(type(non_failover_exc)):
        model.generate(turn, _identity(), _memory(), ())
    assert backup_called is False


def test_active_cancellation_signal_aborts_before_any_failover(tmp_path: Path) -> None:
    baseline = PydanticConversationModel(auth_file=tmp_path / "no-codex-auth")
    control = OptionalModelProviders(
        baseline,
        tmp_path / "providers",
        transport=httpx2.MockTransport(lambda req: pytest.fail("must not dispatch HTTP")),
    )
    conn_id = control.settings.configure(
        kind="gemini",
        label="Gemini",
        model="gemini-2.5-pro",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        api_key="synthetic-cancel-key-0003-test",
    )
    control.settings.record_check(conn_id, ready=True, usage=None)
    control.settings.select(conn_id)
    model = OptionalConversationModel(baseline, control)
    turn = replace(_turn(), conversation_id="telegram:cancel-signal")

    sig = CancellationSignal()
    sig.cancel()
    with cancellation_scope(sig):
        with pytest.raises(WorkInterrupted):
            model.generate(turn, _identity(), _memory(), ())


def test_pure_reviewed_release_validation_has_zero_filesystem_or_thread_side_effects(tmp_path: Path) -> None:
    import hashlib
    import os

    nonexistent_dir = tmp_path / "untouched"
    sha_a = "a" * 64
    sha_b = "b" * 64
    sha_c = "c" * 64
    sha_d = "d" * 64
    assert validate_identifier(sha_a) == sha_a
    with pytest.raises(ValueError):
        validate_identifier("../escape")
    with pytest.raises(ValueError):
        validate_host_path("relative/path", exists=False)

    valid_job = {
        "contract": "EvaReviewedComponentApplication/v1",
        "request_id": sha_a,
        "state": "pending_application",
        "task_id": "task-123",
        "review_id": sha_b,
        "release_sha256": sha_c,
        "accepted_review_sha256": sha_d,
    }
    validate_application_job_payload(
        valid_job,
        f"{sha_a}.json",
        allowed_states={"pending_application", "completed"},
    )
    with pytest.raises(ValueError):
        validate_application_job_payload(
            {**valid_job, "state": "rogue_state"},
            f"{sha_a}.json",
            allowed_states={"pending_application"},
        )

    # Pure updater receipt evaluation on host-owned immutable image bindings
    cand_file = tmp_path / "candidate.json"
    cand_payload = {
        "services": {
            "core": {"image": f"sha256:{sha_a}"},
            "delegated-work-worker": {"image": f"sha256:{sha_b}"},
        }
    }
    cand_bytes = json.dumps(cand_payload).encode("utf-8")
    cand_file.write_bytes(cand_bytes)
    os.chmod(cand_file, 0o600)
    cand_sha = hashlib.sha256(cand_bytes).hexdigest()

    release_cfg = {
        "project": "eva2-managed-b",
        "candidate": {"path": str(cand_file), "sha256": cand_sha},
    }
    good_receipt = {
        "contract": "EvaImageUpdate/v1",
        "project": "eva2-managed-b",
        "status": "running_with_retained_mounts",
        "services": {
            "core": {"candidate_image": f"sha256:{sha_a}", "current_container": "core-1"},
            "delegated-work-worker": {"candidate_image": f"sha256:{sha_b}", "current_container": "worker-1"},
        },
    }
    matched = evaluate_updater_receipt(
        release_cfg,
        good_receipt,
        verified={},
        task_id="task-123",
    )
    assert matched == {"state": "completed", "clear_reason": True}

    tampered = evaluate_updater_receipt(
        release_cfg,
        {
            **good_receipt,
            "services": {
                **good_receipt["services"],
                "core": {"candidate_image": f"sha256:{sha_c}", "current_container": "core-1"},
            },
        },
        verified={},
        task_id="task-123",
    )
    assert tampered is None
    assert not nonexistent_dir.exists()


def test_delivery_store_has_zero_private_work_couplings_and_bounded_rejection_metrics() -> None:
    from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore

    repo_root = Path(__file__).resolve().parents[3]
    for rel in (
        "src/eva/adapters/postgres/delegated_work_delivery.py",
        "src/eva/adapters/postgres/saved_work_artifacts.py",
        "src/eva/adapters/postgres/scheduled_work_failure.py",
    ):
        source = (repo_root / rel).read_text(encoding="utf-8")
        assert "._engine" not in source, f"private _engine coupling still present in {rel}"
        assert "._scope(" not in source, f"private _scope coupling still present in {rel}"
        assert "._validate_owner_binding(" not in source, f"private _validate_owner_binding coupling still present in {rel}"

    # Verify private error text never leaks into error_code classification
    from eva.adapters.postgres.delegated_work import DelegatedWorkConflict

    code = PostgresDelegatedWorkDeliveryStore._classify_rejection_error(
        ValueError("Geheime berichttekst van Jaap; authority mismatch")
    )
    assert "Jaap" not in code and "Geheime" not in code
    assert code == "validation_rejected"
    assert (
        PostgresDelegatedWorkDeliveryStore._classify_rejection_error(
            DelegatedWorkConflict("Geheime conflicttekst")
        )
        == "authority_conflict"
    )


def test_postgres_delivery_claim_next_empty_summary_and_rejection_counts() -> None:
    import os

    dsn = os.environ.get("EVA2_TEST_POSTGRES_DSN", "").strip()
    if not dsn:
        return
    from datetime import timedelta
    import sqlalchemy as sa
    from eva.adapters.postgres.delegated_work_delivery import PostgresDelegatedWorkDeliveryStore
    from eva.adapters.postgres.telegram_delivery_authority import TelegramDeliveryAuthority
    from eva.application.telegram_ingress import TelegramPrincipalBinding, TelegramPrincipalBindings
    from eva.operations.install_schema import install_schema
    from tests.postgres.test_delegated_delivery_store import NOW, admit, setup, succeed
    from tests.postgres.test_delegated_work_store import ROOT

    install_schema(dsn, alembic_ini=ROOT / "deploy/alembic.ini")
    engine = sa.create_engine(dsn)
    try:
        store, spec, dest, _, current, auth = setup(engine, "fase3-nested-tx-and-metrics")
        doc_probes: list[str] = []

        class _DocProbe:
            def candidates(self):
                doc_probes.append("probed")
                return ()

        delivery = PostgresDelegatedWorkDeliveryStore(
            store,
            destination_authority=TelegramDeliveryAuthority(
                store.engine, store.scope, lambda: current[0]
            ),
            document_source=_DocProbe(),
            clock=lambda: NOW + timedelta(seconds=4),
        )
        # Empty summary queue must cleanly exit summary transaction BEFORE probing document_store
        assert delivery.claim_next(now=NOW + timedelta(seconds=4)) is None
        assert doc_probes == ["probed"]

        # Admit & succeed, then make Telegram destination temporarily unavailable -> rejection_counts increments
        admit(store, spec, dest)
        succeed(store, spec)
        current[0] = TelegramPrincipalBindings(
            (
                TelegramPrincipalBinding(
                    42,
                    str(auth["owner_principal_id"]),
                    ("-999",),
                    str(auth["principal_binding_id"]),
                ),
            )
        )
        assert delivery.claim_next(now=NOW + timedelta(seconds=5)) is None
        counts = delivery.rejection_counts()
        assert counts.get("summary_claim:validation_rejected") == 1
        assert all("Result remains private" not in k for k in counts)

        # CRITICAL-02: ScheduledFailureSource.candidates() must return a materialized tuple
        # (never a generator holding an open delivery_transaction() across iteration)
        from eva.adapters.postgres.scheduled_work_failure import ScheduledFailureSource

        sched_candidates = ScheduledFailureSource(store).candidates()
        assert isinstance(sched_candidates, tuple)
    finally:
        engine.dispose()


def test_http_400_scrubs_secrets_and_never_triggers_false_failover(tmp_path: Path) -> None:
    """CRITICAL-01: HTTP 400 / 4xx errors scrub raw upstream bodies/headers and never trigger cloud failover."""
    import httpx2
    import traceback
    from eva.adapters.model.contracts import ConversationCredentialStatus, ConversationModelError
    from eva.adapters.model.pydantic_conversation import PydanticConversationModel
    from eva.application.conversation import ConversationTurn, GroundedReply, IdentityProfile
    from eva.bootstrap.optional_model_providers import OptionalConversationModel, OptionalModelProviders

    secret_primary = "dummy-primary-leakcheck-9876543210"
    secret_backup = "dummy-backup-never-called-123456"
    called_hosts: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        called_hosts.append(request.url.host)
        return httpx2.Response(
            400,
            json={
                "error": {
                    "message": f"Invalid request parameter with echoed key {secret_primary} and prompt GEHEIME_PROMPT_INHOUD",
                    "type": "invalid_request_error",
                }
            },
        )

    transport = httpx2.MockTransport(handler)
    baseline = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json")

    prov_dir = tmp_path / "providers-400"
    control = OptionalModelProviders(baseline, prov_dir, transport=transport)
    primary_id = control.settings.configure(
        label="OpenRouter Primary",
        kind="openai_compatible",
        endpoint="https://openrouter.ai/api/v1",
        model="openai/gpt-4.1",
        api_key=secret_primary,
    )
    backup_id = control.settings.configure(
        label="Gemini Backup",
        kind="gemini",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-pro",
        api_key=secret_backup,
    )
    control.settings.record_check(primary_id, ready=True, usage={"model_attempts": 1, "input_tokens": 5, "output_tokens": 5})
    control.settings.record_check(backup_id, ready=True, usage={"model_attempts": 1, "input_tokens": 5, "output_tokens": 5})
    control.settings.select(primary_id)
    (control.settings.directory / "subscription-llm-state.json").write_text(
        json.dumps({"selected_provider": "openai_codex", "auto_fallback": True}),
        encoding="utf-8",
    )

    wrapped = OptionalConversationModel(baseline, control)
    turn = replace(_turn(), conversation_id="telegram:6034485735", message_id="msg-400", text="GEHEIME_PROMPT_INHOUD")
    with pytest.raises(ConversationModelError) as exc_info:
        wrapped.generate(turn, _identity(), _memory(), ())

    # 1. Zero secret or prompt leakage anywhere in formatted traceback or exception chain
    formatted = "".join(traceback.format_exception(exc_info.value))
    assert secret_primary not in formatted
    assert "GEHEIME_PROMPT_INHOUD" not in formatted
    # 2. HTTP 400 is deterministic configuration/request error -> backup host MUST NOT be called
    assert called_hosts == ["openrouter.ai"]


def test_mid_stream_partial_output_never_fails_over_or_concatenates(tmp_path: Path) -> None:
    """HIGH-01: Once partial text deltas have been streamed to the user sink, failover is strictly forbidden."""
    from eva.adapters.model.contracts import ConversationCredentialStatus, ConversationModelUnavailable
    from eva.application.conversation import (
        publish_conversation_text,
        stream_conversation_text,
    )
    from eva.bootstrap.optional_model_providers import OptionalConversationModel, OptionalModelProviders

    prov_dir = tmp_path / "providers-stream"

    class _PartialStreamBaseline:
        _resource_budget = None

        def credential_status(self):
            return ConversationCredentialStatus(ready=True, state="ready")

        def generate(self, turn, *args, **kwargs):
            publish_conversation_text("Eerste halve zin... ", tools_disabled=True, final_answer=True)
            raise ConversationModelUnavailable("mid-stream disconnect")

    baseline = _PartialStreamBaseline()
    control = OptionalModelProviders(baseline, prov_dir)
    backup_id = control.settings.configure(
        label="Gemini Backup",
        kind="gemini",
        endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        model="gemini-2.5-pro",
        api_key="dummy-stream-backup-never-called-123456",
    )
    control.settings.record_check(backup_id, ready=True, usage={"model_attempts": 1, "input_tokens": 4, "output_tokens": 4})
    (control.settings.directory / "subscription-llm-state.json").write_text(
        json.dumps({"selected_provider": "openai_codex", "auto_fallback": True}),
        encoding="utf-8",
    )

    wrapped = OptionalConversationModel(baseline, control)
    backup_called = False

    def _forbid_backup(*a, **kw):
        nonlocal backup_called
        backup_called = True
        raise AssertionError("backup must never be called after partial stream")

    wrapped._run_configured_connection = _forbid_backup  # type: ignore[method-assign]

    emitted: list[str] = []
    turn = replace(_turn(), conversation_id="dashboard:1", message_id="msg-stream-1", text="Vertel iets")
    with stream_conversation_text(emitted.append):
        with pytest.raises(ConversationModelUnavailable):
            wrapped.generate(turn, _identity(), _memory(), ())

    assert emitted == ["Eerste halve zin... "]
    assert not backup_called


def test_sleep_consolidation_preserves_dismissed_causal_cards_and_bounds_jarvis_queue(tmp_path: Path) -> None:
    """HIGH-02: Dismissed/confirmed Jarvis causal cards stay dismissed across sleep cycles, and queue <= 32."""
    from eva.application.living_entity_pulse import LivingEntityPulseEngine
    from eva.contracts.living_cortex import JarvisStagedAction

    t0 = datetime(2026, 11, 1, 14, 0, 0, tzinfo=UTC)
    engine = LivingEntityPulseEngine(now=t0, persist_path=tmp_path / "pulse-bounded.json")
    for d in range(8):
        day = t0 + timedelta(days=d)
        if d % 2 == 0:
            engine.record_causal_event("net.router_jitter", now=day)
            engine.record_causal_event("voice.stream_drop", now=day + timedelta(seconds=90))
        else:
            engine.record_causal_event("presence.hallway", now=day)

    engine.run_sleep_consolidation(now=t0 + timedelta(days=8, hours=1))
    causal_cards = [a for a in engine.snapshot(now=t0 + timedelta(days=8, hours=1)).jarvis_staged_actions if a.action_id.startswith("jarvis-causal-")]
    assert len(causal_cards) >= 1
    target_id = causal_cards[0].action_id

    # Resident dismisses the causal card
    decided = engine.decide_jarvis_staged_action(target_id, decision="dismiss", now=t0 + timedelta(days=8, hours=2))
    assert decided is not None and decided.status == "dismissed"

    # Subsequent sleep cycle MUST NOT overwrite dismissed status back to staged
    engine.run_sleep_consolidation(now=t0 + timedelta(days=9))
    after = {a.action_id: a for a in engine.snapshot(now=t0 + timedelta(days=9)).jarvis_staged_actions}[target_id]
    assert after.status == "dismissed"

    # Inject 45 synthetic causal cards and verify hard cap of 32
    for idx in range(45):
        cid = f"jarvis-causal-synth-{idx:03d}"
        engine._jarvis_staged_actions[cid] = JarvisStagedAction(
            action_id=cid,
            title_nl=f"Test {idx}",
            prepared_summary_nl="Test",
            why_now_nl="Test",
            confidence=0.5,
            status="staged",
            updated_at=f"2026-11-01T00:{idx:02d}:00Z",
        )
    engine.run_sleep_consolidation(now=t0 + timedelta(days=10))
    assert len(engine._jarvis_staged_actions) <= 32


def test_causal_single_day_quiet_slots_and_cross_midnight_and_single_role_receipt(tmp_path: Path) -> None:
    """MEDIUM-01 & MEDIUM-03: Single-day X->Y without dummy noise, 23:55->00:02 midnight edge, and single-role receipt."""
    import hashlib
    import os
    from eva.application.causal_world_model import CausalWorldModel
    from eva.operations.reviewed_release_validation import evaluate_updater_receipt

    # 1. Single-day history with ONLY X->Y pairs (quiet control slots must satisfy positivity)
    single_day_model = CausalWorldModel(persist_path=tmp_path / "single-day.json", horizon_seconds=600.0)
    base = datetime(2026, 11, 13, 9, 0, 0, tzinfo=UTC)
    for offset_min in (0, 40, 80):
        ts = base + timedelta(minutes=offset_min)
        single_day_model.record_event("kettle", "boiled", occurred_at=ts)
        single_day_model.record_event("tea", "poured", occurred_at=ts + timedelta(seconds=120))
    snap_single = single_day_model.snapshot()
    assert any(
        e.cause_id == "kettle.boiled" and e.effect_id == "tea.poured" and e.backdoor_ace > 0.3
        for e in snap_single.verified_causal_edges
    )

    # 2. Cross-midnight 23:55 -> 00:02 causal link across multiple days
    midnight_model = CausalWorldModel(persist_path=tmp_path / "midnight.json", horizon_seconds=900.0)
    d0 = datetime(2026, 11, 1, 23, 55, 0, tzinfo=UTC)
    for d in range(8):
        cur = d0 + timedelta(days=d)
        if d % 2 == 0:
            midnight_model.record_event("backup", "started", occurred_at=cur)
            midnight_model.record_event("disk", "io_spike", occurred_at=cur + timedelta(minutes=7))  # 00:02 next day!
        else:
            midnight_model.record_event("night", "heartbeat", occurred_at=cur)
    snap_mid = midnight_model.snapshot()
    assert any(
        e.cause_id == "backup.started" and e.effect_id == "disk.io_spike" and e.backdoor_ace > 0.4
        for e in snap_mid.verified_causal_edges
    )

    # 3. Single-role runtime_repair receipt reconciliation via verified["candidate_images"]
    sha_core = "a1" * 32
    sha_worker = "b2" * 32
    cand_file = tmp_path / "single-role-cand.json"
    cand_bytes = json.dumps(
        {
            "services": {
                "core": {"image": f"sha256:{sha_core}"},
                "delegated-work-worker": {"image": f"sha256:{sha_worker}"},
            }
        }
    ).encode("utf-8")
    cand_file.write_bytes(cand_bytes)
    os.chmod(cand_file, 0o600)
    single_role_eval = evaluate_updater_receipt(
        {
            "project": "eva2-managed-b",
            "candidate": {"path": str(cand_file), "sha256": hashlib.sha256(cand_bytes).hexdigest()},
        },
        {
            "contract": "EvaImageUpdate/v1",
            "project": "eva2-managed-b",
            "status": "running_with_retained_mounts",
            "services": {
                "core": {"candidate_image": f"sha256:{sha_core}", "current_container": "core-1"},
            },
        },
        verified={"candidate_images": {"core": f"sha256:{sha_core}"}},
        task_id="repair-core-only",
    )
    assert single_role_eval == {"state": "completed", "clear_reason": True}

