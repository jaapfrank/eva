"""Explicit native recovery keeps a failed public read without replaying it."""
from dataclasses import replace
from datetime import timedelta
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from eva.adapters.model.research_tools import NativeResearchTool, OwnerResearchContinuationTool
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.initiative_execution import NativeInitiativeGuard
from eva.application.background_execution import BackgroundExecutionScope, background_execution
from eva.application.conversation import ConversationTurn
from eva.application.initiative_execution import initiative_execution
from eva.bootstrap.native_initiative_work import resume_native_initiative
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_work import DelegatedWorkUsage
from tests.postgres.test_native_initiative_digital_work import native_digital, admitted, started
from tests.postgres.test_owner_initiatives import costless_native, NOW


ARGS = {"question": "How do public libraries lend books?", "title": "Public library practice",
        "options": ["Public libraries"], "research_mode": "topic", "public_review": "public_unlinked"}


def chain(turn, namespace, fault=None):
    from copy import deepcopy
    body = {"store": False, "tools": [{"search_context_size": "low", "type": "web_search"}],
        "input": [{"content": f"Te onderzoeken onderwerpen: {', '.join(ARGS['options'])}\n\n{ARGS['question']}",
                   "role": "user", "type": "message"}]}
    digest = canonical_sha256(body)
    expected = {"provider_envelope_sha256": digest}
    admission = {"action_name": "research_current_options", "action_call_id": "old-read",
        "action_arguments": deepcopy(ARGS), "principal_id": turn.principal_id,
        "conversation_id": turn.conversation_id, "source_message_id": turn.message_id,
        "execution_namespace": namespace, "request_tool_name": "research.public_sources",
        "request_observation_tool_name": "research.public_sources",
        "request_arguments": deepcopy(ARGS), "request_observation_arguments": deepcopy(ARGS),
        "request_expected_proof": expected, "request_tool_configuration_sha256": "a" * 64,
        "receipt_output": {"provider_request_body": body, "provider_request_body_sha256": digest,
            "verification_scope": "research_artifact_only", "external_state_verified": False,
            "public_input_checks": {"policy": "abstract-projection-host-checks-v1"}}}
    prepared = {"tool_name": "research.public_sources", "observation_tool_name": "research.public_sources",
        "arguments": deepcopy(ARGS), "observation_arguments": deepcopy(ARGS),
        "expected_proof": expected, "tool_configuration_sha256": "a" * 64}
    failure = {"error_fingerprint": "b" * 64, "retry_count": 1}
    if fault == "tool": admission["request_tool_name"] = "private.write"
    if fault == "action": admission["action_name"] = "write_programming_file"
    if fault == "owner": admission["principal_id"] = "foreign"
    if fault == "message": admission["source_message_id"] = "foreign"
    if fault == "namespace": admission["execution_namespace"] = "foreign"
    if fault == "configuration": prepared["tool_configuration_sha256"] = "c" * 64
    if fault == "argument": prepared["arguments"] = {**ARGS, "question": "different"}
    if fault == "body_hash": admission["receipt_output"]["provider_request_body_sha256"] = "c" * 64
    if fault == "body_tools": body["tools"].append({"type": "function", "name": "write"})
    if fault == "artifact": failure["research_artifact"] = {"content": "ignored"}
    if fault == "proof": failure["proof"] = {"observation": {"success": True}}
    if fault == "privacy":
        admission["action_arguments"]["question"] = "Read my secret at /home/private"
        admission["request_arguments"] = admission["request_observation_arguments"] = admission["action_arguments"]
        prepared["arguments"] = prepared["observation_arguments"] = admission["action_arguments"]
    result = []
    for index, (event_type, data) in enumerate(zip(
            ("TOOL_REQUEST_ADMITTED", "TOOL_PREPARED", "RECOVERY_FAILED"),
            (admission, prepared, failure)), 1):
        checkpoint = {"operation_id": "old-operation", "revision": index,
                      "status": "failed" if index == 3 else "waiting_for_proof"}
        if fault == "operation" and index == 2: checkpoint["operation_id"] = "other"
        payload = {"data": data, "checkpoint": checkpoint}
        result.append(SimpleNamespace(event_type=event_type, payload=payload,
            payload_sha256=canonical_sha256(payload), sequence=index,
            thread_id="original-read-thread", occurred_at=NOW))
    if fault == "event_hash": result[-1].payload_sha256 = "d" * 64
    if fault == "executed": result[1].event_type = "TOOL_EXECUTED"
    if fault == "verified": result[-1].event_type = "STATE_VERIFIED"
    if fault == "future": result[-1].occurred_at = NOW + timedelta(seconds=1)
    return tuple(result)


def fixture_guard(*, binding=True):
    turn = ConversationTurn.private("t", "i", "p", "initiative-task:fixture", "m", "Original native question", NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
    namespace = BackgroundExecutionScope(turn, "original-execution").namespace
    data = {"work_id": "new-work", "spec_sha256": "e" * 64, "parent_task_id": "old-work",
        "parent_spec_sha256": "f" * 64, "source_ingress_sha256": "a" * 64,
        "execution_namespace": namespace, "recovery_ingress_sha256": "c" * 64}
    guard = SimpleNamespace(turn=turn, check=Mock(), work_spec=SimpleNamespace(created_at=NOW.strftime("%Y-%m-%dT%H:%M:%SZ")),
        explicit_resume_binding=Mock(return_value=data if binding else None))
    return turn, namespace, guard


def test_provider_proof_is_unknown_historical_context_and_never_dispatches():
    turn, namespace, guard = fixture_guard()
    research = Mock()
    adapter = NativeResearchTool(research, PostgresConversationActions._validated_research_arguments)
    original = chain(turn, namespace)
    hashes = [event.payload_sha256 for event in original]
    with initiative_execution(guard):
        proof = adapter.recoverable_terminal_read_failure(turn, original)
    evidence = proof["evidence"]
    assert evidence["source"] == "native_research_explicit_owner_resume"
    assert evidence["operation_kind"] == "read"
    assert evidence["provider_execution"] == "unknown"
    assert evidence["prior_usage"] == "unknown_or_partial"
    assert evidence["artifact_status"] == "no_recorded_artifact"
    assert evidence["resume_binding"]["execution_namespace"] == namespace
    assert [event.payload_sha256 for event in original] == hashes
    assert original[-1].event_type == "RECOVERY_FAILED"
    assert research.mock_calls == []


@pytest.mark.parametrize("fault", ["tool", "action", "owner", "message", "namespace", "configuration",
    "argument", "body_hash", "body_tools", "artifact", "proof", "privacy", "operation",
    "event_hash", "executed", "verified", "future"])
def test_only_exact_terminal_native_public_read_is_classified(fault):
    turn, namespace, guard = fixture_guard()
    adapter = NativeResearchTool(Mock(), PostgresConversationActions._validated_research_arguments)
    with initiative_execution(guard):
        assert adapter.recoverable_terminal_read_failure(turn, chain(turn, namespace, fault)) is None


def test_ordinary_owner_and_absent_explicit_resume_binding_are_not_enabled():
    turn, namespace, guard = fixture_guard(binding=False)
    with initiative_execution(guard):
        assert NativeResearchTool(Mock(), PostgresConversationActions._validated_research_arguments
            ).recoverable_terminal_read_failure(turn, chain(turn, namespace)) is None
    turn, namespace, guard = fixture_guard()
    with initiative_execution(guard):
        assert OwnerResearchContinuationTool(Mock(), PostgresConversationActions._validated_research_arguments
            ).recoverable_terminal_read_failure(turn, chain(turn, namespace)) is None
    with initiative_execution(SimpleNamespace(turn=turn, check=Mock())):
        assert NativeResearchTool(Mock(), PostgresConversationActions._validated_research_arguments
            ).recoverable_terminal_read_failure(turn, chain(turn, namespace)) is None


def resumed(f):
    _, work = admitted(f)
    call = started(f, work)
    old = f.store.record_failure(call, usage=DelegatedWorkUsage.unmeasured(), error_code="budget_exceeded",
        error_fingerprint="f" * 64, ambiguous=False, now=NOW)
    admission = resume_native_initiative(f.service, old, created=NOW,
        recovery_ingress_sha256="c" * 64, recovery_conversation_id="authenticated-owner",
        recovery_message_id="explicit-owner-resume")
    return old, admission.work


def test_real_successor_guard_keeps_source_namespace_and_uses_new_model_journal(native_digital):
    from eva.bootstrap.delegated_digital_runner import DelegatedDigitalRunner
    from types import SimpleNamespace
    f = native_digital
    old, successor = resumed(f)
    guard, turn = f.auth.bind_work(successor.spec)
    assert guard.resume_work is None  # The active worker is not the admission-only guard.
    assert guard.explicit_resume_binding(turn) is None  # No unbound/background bypass.
    with background_execution(BackgroundExecutionScope(turn, successor.spec.execution_id)), initiative_execution(guard):
        binding = guard.explicit_resume_binding(turn)
        assert binding["work_id"] == successor.spec.work_id
        assert binding["parent_task_id"] == old.spec.work_id
        assert binding["parent_spec_sha256"] == old.spec.sha256
        assert binding["recovery_ingress_sha256"] == "c" * 64
        assert binding["source_ingress_sha256"] == old.spec.source_ingress_sha256
        assert successor.spec.execution_id == old.spec.execution_id
        adapter = NativeResearchTool(Mock(), PostgresConversationActions._validated_research_arguments)
        proof = adapter.recoverable_terminal_read_failure(turn, chain(turn, binding["execution_namespace"]))
        assert proof is not None
        dummy_runner = SimpleNamespace(store=f.store)
        first = DelegatedDigitalRunner._journal(dummy_runner, turn, old.spec)
        second = DelegatedDigitalRunner._journal(dummy_runner, turn, successor.spec)
        assert first._accounting != second._accounting
        assert f.store.load(old.spec.work_id, **f.service._owner).usage.measurement == "unmeasured"
    before = f.auth.timeline.events(f.claim.thread_id)
    assert [e for e in before if e.event_type == "PERSONAL_INITIATIVE_WORK_RESUMED"]


@pytest.mark.parametrize("fault", ["stop", "binding", "not_explicit", "admission_only", "subclass"])
def test_real_guard_cannot_reclassify_automatic_foreign_or_stopped_recovery(native_digital, monkeypatch, fault):
    from uuid import uuid4
    f = native_digital
    old, successor = resumed(f)
    guard, turn = f.auth.bind_work(successor.spec)
    if fault == "stop": f.control.stop(f.claim.intent_id, now=NOW)
    if fault == "binding": f.control._binding = uuid4()
    if fault == "admission_only": guard.resume_work = old
    if fault == "subclass":
        class OtherGuard(NativeInitiativeGuard): pass
        guard.__class__ = OtherGuard
    if fault == "not_explicit":
        original = f.auth._work_admission
        def altered(events, spec):
            event = original(events, spec)
            data = {**event.payload["data"], "recovery_kind": "host_unstarted_profile_refresh"}
            return replace(event, payload={**event.payload, "data": data})
        monkeypatch.setattr(f.auth, "_work_admission", altered)
    with background_execution(BackgroundExecutionScope(turn, successor.spec.execution_id)):
        if fault in {"stop", "binding"}:
            with pytest.raises(RuntimeError): guard.explicit_resume_binding(turn)
        else:
            assert guard.explicit_resume_binding(turn) is None


def test_real_instructions_distinguish_explicit_live_resume_without_expanding_authority(native_digital, monkeypatch):
    f = native_digital
    _, initial = admitted(f)
    ordinary, turn = f.auth.bind_work(initial.spec)
    with background_execution(BackgroundExecutionScope(turn, initial.spec.execution_id)):
        original_text = ordinary.instructions()
    assert "Dit is Eva's eigen initiatief, geen verzoek of toestemming van de eigenaar." in original_text
    assert "De eigenaar heeft dezezelfde duurzame taak expliciet hervat." not in original_text
    call = started(f, initial)
    previous = f.store.record_failure(call, usage=DelegatedWorkUsage.unmeasured(),
        error_code="budget_exceeded", error_fingerprint="f" * 64, ambiguous=False, now=NOW)
    admission_guards = []
    bind_resume = f.auth.bind_resume_work
    def capture_admission(*args, **kwargs):
        result = bind_resume(*args, **kwargs)
        admission_guards.append(result[0])
        return result
    monkeypatch.setattr(f.auth, "bind_resume_work", capture_admission)
    successor = resume_native_initiative(f.service, previous, created=NOW,
        recovery_ingress_sha256="c" * 64, recovery_conversation_id="authenticated-owner",
        recovery_message_id="explicit-owner-resume").work
    live, source = f.auth.bind_work(successor.spec)
    with background_execution(BackgroundExecutionScope(source, successor.spec.execution_id)):
        text = live.instructions()
        assert "Dit onderwerp is door Eva gekozen." in text
        assert "De eigenaar heeft dezezelfde duurzame taak expliciet hervat." in text
        assert "geen verzoek of toestemming van de eigenaar" not in text
        assert "dit verruimt geen mandaat, budget, gegevensdeling of toestemming voor derden" in text
        for bound in (original_text, text):
            assert "Bestaand modelverbruik blijft begrensd." in bound
            assert "Onbekende kosten of ontbrekende mogelijkheden zijn een concrete beperking, geen toestemming." in bound
        assert successor.spec.grant == previous.spec.grant
        assert successor.spec.execution_id == previous.spec.execution_id
        assert len(admission_guards) == 1 and admission_guards[0].resume_work == previous
        assert "De eigenaar heeft dezezelfde duurzame taak expliciet hervat." not in admission_guards[0].instructions()
        f.control.stop(f.claim.intent_id, now=NOW)
        with pytest.raises(RuntimeError):
            live.instructions()
