"""Cross-version native continuation: never re-execute a started action."""
from dataclasses import replace
from datetime import timedelta
import pytest

from tests.postgres.test_native_initiative_digital_work import native_digital, admitted, started, result, costless_native, NOW
from eva.bootstrap.native_initiative_work import refresh_one_unstarted_native, resume_native_initiative
from eva.adapters.postgres.delegated_work import DelegatedWorkConflict
from eva.adapters.postgres.initiative_execution import InitiativeMandateDenied
from eva.application.initiative_execution import unstarted_profile_refresh


def change_program(f):
    from pathlib import Path
    import hashlib
    name = "contracts/canonical_json.py"
    root = Path(__file__).resolve().parents[3] / "src/eva"
    f.profile = replace(f.profile, execution_source_sha256=(
        *f.profile.execution_source_sha256, (name, hashlib.sha256((root / name).read_bytes()).hexdigest())))
    f.service.digital_profile = f.profile


def test_expired_unstarted_admission_continues_with_same_intent_current_program(native_digital):
    f = native_digital
    _, old = admitted(f)
    change_program(f)
    f.service._clock = lambda: NOW + timedelta(hours=2)
    assert refresh_one_unstarted_native(f.service) == "profile_refreshed"
    retired = f.store.load(old.spec.work_id, **f.service._owner)
    assert retired.spec == old.spec and unstarted_profile_refresh(retired)
    event = f.auth.timeline.events(f.claim.thread_id)[-1]
    assert event.payload["data"]["recovery_kind"] == "host_unstarted_profile_refresh"
    assert event.payload["data"]["recovery_message_id"] is None
    work = f.store.load(event.payload["data"]["work_id"], **f.service._owner)
    for field in ("task", "origin", "execution_id", "source", "source_ingress_sha256", "episode_id", "grant"):
        assert getattr(work.spec, field) == getattr(old.spec, field)
    assert work.spec.worker_program_sha256 != old.spec.worker_program_sha256
    assert work.spec.generation == 2 and work.spec.parent_task_id == old.spec.work_id
    assert refresh_one_unstarted_native(f.service) == "idle"
    f.auth.bind_work(work.spec)
    call = f.store.start_call(f.store.claim_next(worker_id="current", release=f.profile.release,
        now=f.service.now()), now=f.service.now())
    saved = f.store.record_success(call, result(call).model_copy(update={
        "completed_at": f.service.now().strftime("%Y-%m-%dT%H:%M:%SZ")}), now=f.service.now())
    assert saved.state == "succeeded"
    assert f.control.artifact(f.claim.intent_id, now=f.service.now())["content"] == saved.result.output
    assert f.store.load(old.spec.work_id, **f.service._owner) == retired


@pytest.mark.parametrize("boundary", ["claimed", "started", "failed", "ambiguous", "stopped", "same_profile"])
def test_refresh_never_retries_claimed_started_failed_uncertain_or_stopped_work(native_digital, boundary):
    f = native_digital
    _, old = admitted(f)
    if boundary == "claimed":
        f.store.claim_next(worker_id="old", release=f.profile.release, now=NOW)
    elif boundary in {"started", "failed", "ambiguous"}:
        call = started(f, old)
        if boundary != "started":
            f.store.record_failure(call, usage=result(call).usage, error_code="internal_error",
                error_fingerprint="f" * 64, ambiguous=boundary == "ambiguous", now=NOW)
    elif boundary == "stopped":
        f.control.stop(f.claim.intent_id, now=NOW)
    if boundary != "same_profile": change_program(f)
    before = f.auth.timeline.events(f.claim.thread_id)
    try:
        assert refresh_one_unstarted_native(f.service) == "idle"
    except (InitiativeMandateDenied, DelegatedWorkConflict):
        assert boundary == "stopped"
    assert f.auth.timeline.events(f.claim.thread_id) == before


def test_refresh_restarts_after_crash_between_retirement_and_successor(native_digital, monkeypatch):
    import eva.bootstrap.native_initiative_work as bridge
    f = native_digital
    _, old = admitted(f)
    change_program(f)
    original = bridge.resume_native_initiative
    def fail(*args, **kwargs): raise RuntimeError("synthetic crash after retirement")
    monkeypatch.setattr(bridge, "resume_native_initiative", fail)
    with pytest.raises(RuntimeError, match="after retirement"):
        refresh_one_unstarted_native(f.service)
    assert unstarted_profile_refresh(f.store.load(old.spec.work_id, **f.service._owner))
    monkeypatch.setattr(bridge, "resume_native_initiative", original)
    assert refresh_one_unstarted_native(f.service) == "profile_refreshed"
    assert refresh_one_unstarted_native(f.service) == "idle"


def test_host_refresh_cannot_launder_failed_work_or_changed_mandate(native_digital):
    from tests.postgres.test_native_initiative_resume import failed
    f = native_digital
    old = failed(f)
    change_program(f)
    with pytest.raises(InitiativeMandateDenied, match="unstarted obsolete"):
        resume_native_initiative(f.service, old, created=NOW, _profile_refresh=True)


def test_changed_authority_after_retirement_does_not_starve_later_reviews(native_digital):
    import json
    from tests.postgres.test_native_initiative_resume import revise_mandate
    f = native_digital
    _, old = admitted(f)
    change_program(f)
    retired = f.store.retire_one_unstarted_native(release=f.profile.release,
        owner_principal_id=f.service.owner_principal_id,
        principal_binding_id=f.service.principal_binding_id, now=NOW)
    assert unstarted_profile_refresh(retired)
    before, after = revise_mandate(f)
    f.auth.admit_revision(before, after)
    assert refresh_one_unstarted_native(f.service) == "refresh_not_authorized"
    assert refresh_one_unstarted_native(f.service) == "idle"
    withdrawn = f.store.load(old.spec.work_id, **f.service._owner)
    assert withdrawn.error_code == "unstarted_profile_refresh_withdrawn"
    assert withdrawn.call_id is None and withdrawn.claim_count == 0
    assert f.auth.timeline.load(f.claim.thread_id).phase == "initiative_blocked"


def test_stopped_retired_parent_does_not_reappear_or_resurrect(native_digital):
    f = native_digital
    _, old = admitted(f)
    change_program(f)
    f.store.retire_one_unstarted_native(release=f.profile.release,
        owner_principal_id=f.service.owner_principal_id,
        principal_binding_id=f.service.principal_binding_id, now=NOW)
    f.control.stop(f.claim.intent_id, now=NOW)
    assert refresh_one_unstarted_native(f.service) == "idle"
    assert refresh_one_unstarted_native(f.service) == "idle"
    assert f.auth.timeline.load(f.claim.thread_id).phase == "personal_stopped"
    assert f.store.load(old.spec.work_id, **f.service._owner).error_code == "unstarted_profile_refresh_withdrawn"


def test_concurrent_refresh_admits_only_one_successor(native_digital):
    from concurrent.futures import ThreadPoolExecutor
    f = native_digital
    admitted(f)
    change_program(f)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: refresh_one_unstarted_native(f.service), range(2)))
    assert results.count("profile_refreshed") == 1
    events = f.auth.timeline.events(f.claim.thread_id)
    assert sum(event.event_type == "PERSONAL_INITIATIVE_WORK_RESUMED" for event in events) == 1
    assert refresh_one_unstarted_native(f.service) == "idle"


def test_pending_work_maintenance_progresses_while_optional_idle_lane_is_closed(native_digital):
    from types import SimpleNamespace
    from eva.application.memory_background import HindsightColdPathWorker
    f = native_digital
    admitted(f)
    change_program(f)
    assert f.store.has_pending_or_running_work()
    outcomes = []
    worker = HindsightColdPathWorker(SimpleNamespace(), idle_capacity=lambda: False,
        service_tasks=(lambda: outcomes.append(refresh_one_unstarted_native(f.service)),))
    # One genuine service-lane iteration; model/idle tasks remain disabled.
    worker._service_tasks = (*worker._service_tasks, worker._stop.set)
    worker._run_services()
    assert outcomes == ["profile_refreshed"]


def test_native_background_research_uses_initiative_policy_not_owner_adapter(native_digital):
    import asyncio
    from types import SimpleNamespace
    from eva.application.initiative_execution import initiative_execution
    from eva.bootstrap.background_research import task_research_tools
    from eva.application.conversation import ConversationActionCall, ConversationConflict
    from tests.postgres.test_owner_research_continuation import Research, ARGS, RESEARCH
    f = native_digital
    guard, turn = f.auth.bind(f.claim)
    # Preparation needs no provider execution or journal request. It must select
    # the native adapter, require a live guard and enforce public projection.
    with initiative_execution(guard):
        research = Research()
        research.native_research_timeout_seconds = lambda: 55
        tools = task_research_tools(f.store._engine, research, turn=turn,
            journal=SimpleNamespace(), authorize=guard.check, grant=f.profile.grant(),
            profile=SimpleNamespace(maximum_response_input_tokens=100,maximum_response_output_tokens=100))
        adapter = tools._planner
        assert type(adapter).__name__ == "NativeResearchTool"
        public = {**ARGS, "public_review":"public_unlinked"}
        prep = asyncio.run(adapter.prepare(turn, ConversationActionCall("native-research", RESEARCH, public)))
        assert prep.receipt_output["public_input_checks"]["model_assessment"] == "public_unlinked"
        with pytest.raises(ConversationConflict, match="uncertain or not reviewed"):
            asyncio.run(adapter.prepare(turn, ConversationActionCall("not-reviewed", RESEARCH, ARGS)))
