"""Bounded owner recovery retains real v4 receipts and unknown provider usage."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import hashlib
import importlib
import json
from pathlib import Path
from types import SimpleNamespace
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config

from eva.adapters.model.openai_codex_provider import CodexProviderError
from eva.adapters.model.pydantic_conversation import PydanticConversationModel
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.current_catalog import delegated_work_runs
from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import operational_events, operational_checkpoints
from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
from eva.adapters.projection.hindsight import HindsightMemoryEngine
from eva.application.conversation import (
    ConversationActionCall, ConversationToolDescriptor, ConversationToolPreparation, ConversationTurn,
    GroundedReply, IdentityProfile,
)
from eva.application.orchestration import ToolDispatchResult, ToolProofObservation
from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.bootstrap.delegated_digital_profile import DelegatedDigitalProfile
from eva.bootstrap.delegated_digital_runner import DelegatedDigitalRunner
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
from eva.contracts.delegated_work import DelegatedWorkUsage
from eva.contracts.timestamps import parse_utc_timestamp
from eva.providers.mcp_autonomy import _SourceTurnLoader
from tests.postgres.test_owner_initiatives import costless_native, NOW  # noqa: F401
from tests.postgres import test_scheduled_failure_return as scheduled_fixture
from tests.postgres.test_people_memory_overview import _upgrade
from tests.support.scripted_pydantic_conversation import (
    ScriptedCall, ScriptedPhase, ScriptedPydanticConversation,
)


@pytest.fixture
def postgres_engine(database):
    """Reuse the existing isolated schema fixture, including its cleanup."""
    return database


@pytest.fixture(scope="module")
def database():
    """Reuse the disposable server fixture with the existing migration gate.

    The installed runtime's operator installer predates the current schema head.
    This only selects existing test migration infrastructure; application modules
    under test still come from the installed baseline plus the three candidates.
    """
    def migrate_disposable(dsn, *, alembic_ini):
        engine = sa.create_engine(dsn)
        assert engine.url.database.startswith("eva2_4b_disposable_")
        config = Config(str(alembic_ini))
        config.set_main_option("sqlalchemy.url", dsn.replace("%", "%%"))
        try:
            _upgrade(engine, config)
        finally:
            engine.dispose()

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(scheduled_fixture, "install_schema", migrate_disposable)
        yield from scheduled_fixture.database.__wrapped__()


@pytest.fixture
def provider_failure(costless_native, tmp_path, request):
    authority, _, _, owner, _, policy_path, policy = costless_native
    engine = owner._engine
    clock = [NOW]
    uncertain_effect = getattr(request, "param", None) == "uncertain_effect"
    source_name = "bootstrap/delegated_digital_runner.py"
    source = Path(importlib.import_module(DelegatedDigitalRunner.__module__).__file__)
    profile = DelegatedDigitalProfile(
        "model:provider-recovery-fixture", "a" * 64, "b" * 64,
        ("use_connector_tool",),
        ((source_name, hashlib.sha256(source.read_bytes()).hexdigest()),),
        maximum_tool_calls=7 if uncertain_effect else 6,
    )
    store = PostgresDelegatedWorkStore(engine, owner._authority,
        initiative_authority_loader=lambda: authority)
    service = DelegatedWorkOwnerService(store, owner_principal_id=owner._owner,
        principal_binding_id=owner._binding, conversation_scope=owner._scope,
        digital_profile=profile, clock=lambda: clock[0])
    turn = ConversationTurn.private(owner._tenant_id, owner._installation_id,
        str(owner._owner), "dashboard:" + str(owner._binding),
        "provider-recovery:" + uuid4().hex, "Read the existing private documents.",
        NOW.isoformat().replace("+00:00", "Z"))
    journal = PostgresConversationJournal(engine, owner._scope)
    journal.admit(turn)
    foreground = GroundedReply(turn.conversation_id, turn.message_id,
        "Ik ga de documenten lezen.", (), turn.occurred_at, "fixture-contact")
    journal.commit_reply(turn, foreground)
    work_id = service.execute("digital", turn, task=turn.text,
        request_key="original")["work"]["work_id"]
    original = store.load(work_id, **service._owner)
    observed = {}
    dispatches = []

    class Memory:
        def recall_for_presence(self, query):
            return HindsightMemoryEngine._empty_presence(query)

    class Planner:
        action_names = profile.capability_ids

        async def prepare(self, source_turn, call):
            assert source_turn == turn
            expected = {"document": call.arguments["document"]}
            operation = "effect" if call.arguments["document"] == "pending-effect" else "read"
            return ConversationToolPreparation("fixture.document.read", call.arguments,
                expected, {"transport": "mcp_gateway", "operation_kind": operation,
                           "provider_tool_name": "fixture-document-read"},
                tool_configuration_sha256="c" * 64)

    class Provider:
        async def execute(self, request):
            assert request.operation_id not in observed, "verified read repeated"
            dispatches.append(request.arguments["document"])
            observed[request.operation_id] = dict(request.arguments)
            operation = "effect" if request.arguments["document"] == "pending-effect" else "read"
            return ToolDispatchResult(True, request.operation_id, {"operation_kind": operation})

        async def observe(self, request, dispatch):
            if request.arguments["document"] == "pending-effect":
                if request.operation_id not in observed:
                    return ToolProofObservation("safe_to_execute", {}, {"source": "fixture-precheck"})
                return ToolProofObservation("conflict", {}, {"source": "unconfirmed-effect"})
            return ToolProofObservation("verified", observed[request.operation_id],
                                       {"source": "fixture-readback"})

    scripted = ScriptedPydanticConversation(tuple(
        ScriptedPhase.deferred(ScriptedCall("read-" + str(i), "use_connector_tool",
                                           {"document": str(i)}))
        for i in range(6)
    ) + (ScriptedPhase.failure(CodexProviderError("unavailable")),
         ScriptedPhase.text("This must not bypass incomplete usage.")))
    descriptor = ConversationToolDescriptor("use_connector_tool", "Read a document.",
        {"type": "object", "properties": {"document": {"type": "string"}},
         "required": ["document"], "additionalProperties": False})
    model = PydanticConversationModel(auth_file=tmp_path / "unused-auth.json",
        model_backend=scripted.model, conversation_tools=(descriptor,))
    tools = PostgresExternalConversationTools(engine, Planner(), Provider(),
        authorize_turn=service.validate_authority)
    runner = DelegatedDigitalRunner(store, profile,
        source_loader=_SourceTurnLoader(engine, SimpleNamespace(
            scope=owner._scope, authorize_turn=service.validate_authority)),
        memory=Memory(), model=model, identity=IdentityProfile("Eva", "Helpful", ("Be honest",)),
        connector_tools=(tools,), clock=lambda: clock[0])
    claimed = store.claim_next(worker_id="fixture:provider-recovery",
        release=profile.release, now=clock[0])
    assert claimed.spec == original.spec
    call = store.start_call(claimed, now=clock[0])
    outcome = asyncio.run(runner.run(original.spec, call_id=call.call_id))
    assert outcome.status == "provider_unavailable", outcome
    assert outcome.result is None and len(scripted.captures) == 7
    assert dispatches == [str(i) for i in range(6)]
    if uncertain_effect:
        # An admitted external effect can remain unresolved when the provider
        # fails. Use the real durable tool port while the work claim is live.
        store.authorize_tool_dispatch(call)
        def authorize_effect(source_turn):
            assert source_turn == turn
            store.authorize_tool_dispatch(call)
            assert len(dispatches) < original.spec.grant.maximum_tool_calls

        with background_execution(BackgroundExecutionScope(
                turn, original.spec.execution_id, authorize_effect)):
            receipt = tools.execute(turn, ConversationActionCall("pending-effect",
                "use_connector_tool", {"document": "pending-effect"}), step_index=6)
        assert receipt.output["status"] != "verified"
        outcome_usage = outcome.usage.model_copy(update={"tool_calls": 7})
    else:
        outcome_usage = outcome.usage
    store.record_failure(call, error_code="provider_unavailable",
        error_fingerprint="d" * 64, ambiguous=True, usage=outcome_usage, now=clock[0])
    failed = store.load(work_id, **service._owner)
    return SimpleNamespace(authority=authority, owner=owner, policy=policy,
        policy_path=policy_path, engine=engine, clock=clock, profile=profile,
        store=store, service=service, turn=turn, journal=journal, foreground=foreground,
        original=original, failed=failed, outcome=outcome, scripted=scripted,
        dispatches=dispatches, tools=tools,
        unverified_receipt=receipt if uncertain_effect else None)


def test_unmeasured_provider_failure_preserves_six_reads_and_original_reply(provider_failure):
    case = provider_failure
    assert case.failed.state == "ambiguous"
    assert case.failed.error_code == "provider_unavailable"
    assert case.failed.usage.measurement == "partial"
    assert case.failed.usage.cost_usd_micros is None
    assert case.failed.result is None
    assert case.journal.committed_reply(case.turn) == case.foreground
    # Independent durable SQL readback, rather than a successful model response,
    # establishes that the failed request's input/output usage remains unknown.
    with case.engine.connect() as connection:
        failures = connection.execute(sa.select(operational_events.c.payload).where(
            operational_events.c.event_type == "CONVERSATION_MODEL_RECORDED",
            operational_events.c.tenant_id == case.owner._scope.tenant_id,
            operational_events.c.installation_id == case.owner._scope.installation_id,
            operational_events.c.payload["data"]["outcome"].astext == "failed",
        )).scalars().all()
    assert len(failures) == 1
    usage = failures[0]["data"]["usage"]
    assert usage["measurement"] == "unmeasured"
    assert usage["input_tokens"] is None and usage["output_tokens"] is None
    assert case.scripted.remaining_phases == 1


def authorize_recovery(case, *, enabled=True, source_admitted_not_before=None):
    case.policy["self_repair"] = {
        "enabled": True, "authorization_source": "Explicit isolated owner recovery test",
    }
    case.policy["digital_work"] = {
        "worker_profile_release_sha256": case.profile.release.worker_profile_release_sha256,
        "capability_ids": list(case.profile.capability_ids),
    }
    case.policy["private_work_recovery"] = {
        "enabled": enabled,
        "authorization_source": "One separate bounded private recovery on the existing subscription",
        "source_contract": "DelegatedDigitalWork/v4",
        "error_codes": ["provider_unavailable"],
        "maximum_additional_attempts": 1,
        "manual_successors_count": True,
        "new_attempt_grant": "same_limits_as_original_attempt",
        "new_attempt_clock": "current_admission_time_original_timeout",
        "provider_route": "same_existing_subscription_only",
        "model_profile_release_sha256": case.profile.release.model_profile_release_sha256,
        "source_admitted_not_before": source_admitted_not_before or (
            parse_utc_timestamp(case.original.spec.created_at) - timedelta(seconds=1)
        ).isoformat().replace("+00:00", "Z"),
        "require_no_finite_monetary_cap": True,
        "prior_effects": "verified_reads_only",
    }
    case.policy_path.write_text(json.dumps(case.policy))


def work_rows(case):
    with case.engine.connect() as connection:
        return connection.execute(sa.select(delegated_work_runs).where(
            delegated_work_runs.c.tenant_id == case.store.scope.tenant_id,
            delegated_work_runs.c.installation_id == case.store.scope.installation_id,
            delegated_work_runs.c.spec_payload["execution_id"].astext == case.failed.spec.execution_id,
        ).order_by(delegated_work_runs.c.generation)).mappings().all()


def recovery_requests(case):
    scheduler = ScheduledOwnerWork(case.service)
    with case.engine.connect() as connection:
        return connection.execute(sa.select(operational_events.c.payload).where(
            operational_events.c.tenant_id == case.store.scope.tenant_id,
            operational_events.c.installation_id == case.store.scope.installation_id,
            operational_events.c.thread_id.startswith(scheduler.prefix),
            operational_events.c.sequence == 1,
            operational_events.c.payload["data"]["provider_recovery"].is_not(None),
        )).scalars().all()


def test_one_separate_recovery_retains_unknown_old_usage_and_fresh_bounded_clock(provider_failure):
    case = provider_failure
    authorize_recovery(case)
    before = dict(work_rows(case)[0])
    case.clock[0] += timedelta(seconds=45)
    scheduler = ScheduledOwnerWork(case.service)
    scheduler.schedule_provider_recovery()
    assert len(recovery_requests(case)) == 1
    assert len(work_rows(case)) == 1  # Registration is not a model or work dispatch.
    # Recreating the scheduler cannot register another recovery appointment.
    ScheduledOwnerWork(case.service).schedule_provider_recovery()
    assert len(recovery_requests(case)) == 1
    case.clock[0] += timedelta(seconds=2)
    assert scheduler.run_once() == "dispatched"
    rows = work_rows(case)
    assert len(rows) == 2
    child = case.store.load(rows[1]["work_id"], **case.service._owner)
    assert child.state == "admitted"
    assert child.spec.generation == 2
    for key in ("source", "source_ingress_sha256", "execution_id", "task",
                "owner_principal_id", "principal_binding_id", "grant"):
        assert child.spec.to_payload()[key] == case.failed.spec.to_payload()[key]
    assert parse_utc_timestamp(child.spec.deadline) == (
        case.clock[0] + timedelta(seconds=case.failed.spec.grant.timeout_seconds))
    assert parse_utc_timestamp(child.spec.deadline) > parse_utc_timestamp(case.failed.spec.deadline)
    unchanged = dict(rows[0])
    assert unchanged == before
    assert unchanged["usage_payload"]["cost_usd_micros"] is None
    assert case.journal.committed_reply(case.turn) == case.foreground
    assert len(case.scripted.captures) == 7 and len(case.dispatches) == 6
    for _ in range(3):
        ScheduledOwnerWork(case.service).run_once()
    assert len(work_rows(case)) == 2 and len(recovery_requests(case)) == 1
    claimed = case.store.claim_next(worker_id="fixture:recovery-failed-again",
        release=case.profile.release, now=case.clock[0])
    assert claimed.spec == child.spec
    call = case.store.start_call(claimed, now=case.clock[0])
    case.store.record_failure(call, error_code="provider_unavailable",
        error_fingerprint="e" * 64, ambiguous=True,
        usage=DelegatedWorkUsage.unmeasured(), now=case.clock[0])
    for _ in range(2):
        ScheduledOwnerWork(case.service).run_once()
    assert len(work_rows(case)) == 2 and len(recovery_requests(case)) == 1


@pytest.mark.parametrize("policy_mode", ["absent", "disabled"])
def test_recovery_requires_explicit_enabled_owner_policy(provider_failure, policy_mode):
    case = provider_failure
    if policy_mode == "disabled":
        authorize_recovery(case, enabled=False)
    ScheduledOwnerWork(case.service).schedule_provider_recovery()
    ScheduledOwnerWork(case.service).run_once()
    assert len(work_rows(case)) == 1
    assert recovery_requests(case) == []
    assert len(case.scripted.captures) == 7 and len(case.dispatches) == 6


@pytest.mark.parametrize("already_admitted", [False, True])
def test_owner_stop_prevents_a_recovery_request(provider_failure, already_admitted):
    case = provider_failure
    authorize_recovery(case)
    scheduler = ScheduledOwnerWork(case.service)
    if already_admitted:
        assert scheduler.schedule_provider_recovery() == "provider_recovery_scheduled"
        case.clock[0] += timedelta(seconds=2)
        assert scheduler.run_once() == "dispatched"
        assert len(work_rows(case)) == 2
    case.service.execute("stop", case.turn, work_id=case.failed.spec.work_id,
                         request_key="explicit-owner-stop")
    scheduler.run_once()
    if already_admitted:
        assert len(work_rows(case)) == 2
        child = case.store.load(work_rows(case)[1]["work_id"], **case.service._owner)
        assert child.state == "stopped"
        assert case.store.claim_next(worker_id="fixture:after-owner-stop",
            release=case.profile.release, now=case.clock[0]) is None
    else:
        assert len(work_rows(case)) == 1
        assert recovery_requests(case) == []
    assert case.store.load(case.failed.spec.work_id, **case.service._owner) == case.failed
    assert len(case.scripted.captures) == 7


@pytest.mark.parametrize("provider_failure", ["uncertain_effect"], indirect=True)
def test_unverified_external_effect_prevents_recovery(provider_failure):
    case = provider_failure
    authorize_recovery(case)
    assert case.failed.usage.tool_calls == 7
    scheduler = ScheduledOwnerWork(case.service)
    assert scheduler.schedule_provider_recovery() == "provider_recovery_blocked"
    scheduler.run_once()
    assert len(work_rows(case)) == 1
    assert recovery_requests(case) == []
    assert len(case.dispatches) == 7, case.unverified_receipt.output
    assert len(case.scripted.captures) == 7


def test_manual_successor_consumes_the_only_additional_attempt(provider_failure):
    case = provider_failure
    authorize_recovery(case)
    manual = case.service.execute("resume", case.turn, work_id=case.failed.spec.work_id,
                                  request_key="explicit-manual-resume")["work"]
    assert len(work_rows(case)) == 2
    scheduler = ScheduledOwnerWork(case.service)
    assert scheduler.schedule_provider_recovery() == "provider_recovery_blocked"
    scheduler.run_once()
    assert len(work_rows(case)) == 2
    assert recovery_requests(case) == []
    assert work_rows(case)[1]["work_id"] == manual["work_id"]
    # Stopping the failed original cannot stop a separately requested manual child.
    case.service.execute("stop", case.turn, work_id=case.failed.spec.work_id,
                         request_key="stop-original-after-manual")
    assert case.store.load(manual["work_id"], **case.service._owner).state == "admitted"
    assert len(case.scripted.captures) == 7 and len(case.dispatches) == 6


def test_current_finite_monetary_cap_cannot_authorize_recovery(provider_failure):
    case = provider_failure
    authorize_recovery(case)
    case.service.digital_profile = replace(case.profile, maximum_cost_usd_micros=1)
    scheduler = ScheduledOwnerWork(case.service)
    assert scheduler.schedule_provider_recovery() == "provider_recovery_blocked"
    scheduler.run_once()
    assert len(work_rows(case)) == 1 and recovery_requests(case) == []
    assert case.store.load(case.failed.spec.work_id, **case.service._owner).usage.cost_usd_micros is None
    assert len(case.scripted.captures) == 7


def test_policy_activation_does_not_resurrect_an_older_owner_failure(provider_failure):
    case = provider_failure
    cutoff = (parse_utc_timestamp(case.original.spec.created_at) + timedelta(seconds=1)
              ).isoformat().replace("+00:00", "Z")
    authorize_recovery(case, source_admitted_not_before=cutoff)
    scheduler = ScheduledOwnerWork(case.service)
    assert scheduler.schedule_provider_recovery() == "idle"
    scheduler.run_once()
    assert len(work_rows(case)) == 1 and recovery_requests(case) == []
    assert len(case.scripted.captures) == 7 and len(case.dispatches) == 6


@pytest.mark.parametrize("already_scheduled", [False, True])
def test_source_stop_prevents_recovery_before_and_after_registration(provider_failure, already_scheduled):
    case = provider_failure
    authorize_recovery(case)
    scheduler = ScheduledOwnerWork(case.service)
    if already_scheduled:
        assert scheduler.schedule_provider_recovery() == "provider_recovery_scheduled"
    case.journal.request_turn_stop(case.turn)
    case.clock[0] += timedelta(seconds=2)
    scheduler.run_once()
    assert len(work_rows(case)) == 1
    assert len(case.scripted.captures) == 7 and len(case.dispatches) == 6
    thread = scheduler.prefix + "provider-recovery:" + hashlib.sha256(
        case.failed.spec.execution_id.encode()).hexdigest()
    events = scheduler.timeline.events(thread)
    assert events[-1].event_type == "SCHEDULED_WORK_BLOCKED"
    scheduler.run_once()
    assert scheduler.timeline.events(thread) == events


def test_busy_correction_defers_without_consuming_recovery(provider_failure):
    from eva.adapters.postgres.work_corrections import correction_lock_key

    case = provider_failure
    authorize_recovery(case)
    case.authority.policy()
    scheduler = ScheduledOwnerWork(case.service)
    pool = ThreadPoolExecutor(max_workers=1)
    try:
        with case.engine.begin() as correction:
            correction.execute(sa.text(
                "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": correction_lock_key(case.failed.spec)})
            result = pool.submit(scheduler.schedule_provider_recovery).result(timeout=3)
            assert result == "provider_recovery_waiting"
            assert len(work_rows(case)) == 1
            with case.engine.connect() as read:
                assert read.scalar(sa.select(sa.func.count()).select_from(operational_checkpoints).where(
                    operational_checkpoints.c.tenant_id == case.store.scope.tenant_id,
                    operational_checkpoints.c.installation_id == case.store.scope.installation_id,
                    operational_checkpoints.c.thread_id.startswith(scheduler.prefix))) == 0
    finally:
        # Release the real correction transaction before waiting for its contender.
        pool.shutdown(wait=True)
    assert scheduler.schedule_provider_recovery() == "provider_recovery_scheduled"
    case.clock[0] += timedelta(seconds=2)
    assert scheduler.run_once() == "dispatched"
    assert len(work_rows(case)) == 2 and len(recovery_requests(case)) == 1


def test_policy_revocation_after_preflight_prevents_admission(provider_failure):
    case = provider_failure
    authorize_recovery(case)
    scheduler = ScheduledOwnerWork(case.service)
    assert scheduler.schedule_provider_recovery() == "provider_recovery_scheduled"
    case.authority.revoke()
    case.clock[0] += timedelta(seconds=2)
    assert scheduler.run_once() == "blocked"
    assert len(work_rows(case)) == 1
    assert len(case.scripted.captures) == 7


def test_policy_revocation_waits_for_an_admission_that_already_holds_authority(provider_failure, monkeypatch):
    case = provider_failure
    authorize_recovery(case)
    scheduler = ScheduledOwnerWork(case.service)
    assert scheduler.schedule_provider_recovery() == "provider_recovery_scheduled"
    case.clock[0] += timedelta(seconds=2)
    admitted, release = Event(), Event()
    admit = case.store.admit

    def hold_admitted_transaction(*args, **kwargs):
        result = admit(*args, **kwargs)
        admitted.set()
        assert release.wait(5), "test must release the admitted root transaction"
        return result

    monkeypatch.setattr(case.store, "admit", hold_admitted_transaction)
    pool = ThreadPoolExecutor(max_workers=2)
    try:
        admission = pool.submit(scheduler.run_once)
        assert admitted.wait(5), "recovery never reached its real store admission"
        revocation = pool.submit(case.authority.revoke)
        deadline = monotonic() + 3
        blocked = False
        while monotonic() < deadline:
            with case.engine.connect() as read:
                blocked = bool(read.scalar(sa.text(
                    "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                    "WHERE datname=current_database() AND pid<>pg_backend_pid() "
                    "AND wait_event_type='Lock' AND cardinality(pg_blocking_pids(pid))>0)")))
            if blocked:
                break
            sleep(0.02)
        assert blocked, "revocation did not wait for the held policy checkpoint"
        assert not revocation.done()
        assert len(work_rows(case)) == 1  # Uncommitted child is still invisible.
        release.set()
        assert admission.result(timeout=5) == "dispatched"
        revocation.result(timeout=5)
    finally:
        release.set()
        pool.shutdown(wait=True)
    assert len(work_rows(case)) == 2
    assert case.authority.timeline.load(case.authority.thread).phase == "initiative_mandate_revoked"
    assert len(case.scripted.captures) == 7
