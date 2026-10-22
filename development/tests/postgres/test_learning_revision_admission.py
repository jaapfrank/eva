"""Corrections cannot be bypassed by stale or concurrent learning writes."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
import hashlib

import pytest

from eva.application.conversation import ConversationActionCall, ConversationActionInputRejected, ConversationTurn
from eva.application.human_understanding import build_human_understanding_document
from eva.providers.human_understanding_actions import ACTION, HumanUnderstandingActions
from tests.postgres.test_people_memory_overview import (
    people_memory_engine, selected_private_case, _seed_learning, _retain_learning, _learning_arguments, NOW,
)


def revision(case, name, previous, *, seconds_ago=2):
    text = "Synthetic current exception: " + name
    stamp = NOW - timedelta(seconds=seconds_ago)
    doc, _ = case.seed(name, text, ingress_at=stamp, occurred_at=stamp.strftime("%Y-%m-%dT%H:%M:%SZ"))
    turn = ConversationTurn.private(doc.tenant_id, doc.installation_id, doc.principal_id,
        doc.conversation_id, doc.source_ref, text, doc.occurred_at, project_id=doc.project_id)
    call = ConversationActionCall(name, ACTION, _learning_arguments(
        previous_source_ref=previous.source_ref, previous_evidence_ref=previous.evidence_ref,
        outcome="refuted", interpretation="Revised conditional expectation: " + name))
    return turn, call


def provider(case, submit=None):
    return HumanUnderstandingActions(case.engine, source_reader=lambda: case.reader,
        submit=submit or case.outbox.enqueue, clock=lambda: NOW)


@pytest.mark.parametrize("state", ["retained", "queued", "erased"])
def test_stale_parent_cannot_bypass_existing_correction(selected_private_case, state):
    case = selected_private_case
    parent = _seed_learning(case, "original", seconds_ago=30)
    correction_turn, correction_call = revision(case, "correction", parent, seconds_ago=20)
    actions = provider(case)
    actions.execute(correction_turn, correction_call)
    corrected = build_human_understanding_document(correction_turn, correction_call.arguments)
    if state != "queued":
        _retain_learning(case, corrected)
    if state == "erased":
        case.outbox.erase_content(principal_sha256=hashlib.sha256(str(case.owner).encode()).hexdigest(),
            document_id=corrected.document_id, operation_sha256="af" * 32, erased_at=NOW)
    turn, call = revision(case, "stale", parent)
    with pytest.raises(ConversationActionInputRejected):
        actions.execute(turn, call)
    assert case.timeline.events(actions._slot(turn, 0)) == ()


def test_current_revision_and_completed_replay_remain_usable(selected_private_case):
    case = selected_private_case
    parent = _seed_learning(case, "original", seconds_ago=30)
    corrected = _seed_learning(case, "correction", seconds_ago=20, previous=parent, outcome="refuted")
    turn, call = revision(case, "latest", corrected)
    actions = provider(case)
    receipt = actions.execute(turn, call)
    assert receipt.output["status"] == "queued"
    assert actions.execute(turn, call) == receipt  # Own pending successor is not a stale writer.
    doc = build_human_understanding_document(turn, call.arguments)
    _retain_learning(case, doc)
    context = case.reader.learning_source(case.query, parent.source_ref, parent.evidence_ref, now=NOW)
    assert [r["source_ref"] for r in context["records"]] == [doc.source_ref, corrected.source_ref, parent.source_ref]
    assert actions.pending(turn) == receipt


def test_admitted_revision_can_resume_after_interrupted_submission(selected_private_case):
    case = selected_private_case
    parent = _seed_learning(case, "original", seconds_ago=30)
    turn, call = revision(case, "resume", parent)
    def interrupted(doc):
        raise RuntimeError("synthetic interruption before enqueue")
    actions = provider(case, interrupted)
    with pytest.raises(RuntimeError, match="synthetic interruption"):
        actions.execute(turn, call)
    actions.submit = case.outbox.enqueue
    receipt = actions.execute(turn, call)
    assert receipt.output["status"] == "queued"
    assert len(case.timeline.events(actions._slot(turn, 0))) == 2


def test_two_conversations_cannot_admit_sibling_corrections(selected_private_case):
    case = selected_private_case
    parent = _seed_learning(case, "original", seconds_ago=30)
    turns = [revision(case, name, parent) for name in ("concurrent-a", "concurrent-b")]
    barrier = Barrier(2)
    def attempt(pair):
        actions = provider(case)
        barrier.wait(timeout=10)
        try:
            return actions.execute(*pair).output["status"]
        except ConversationActionInputRejected:
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, turns))
    assert sorted(results) == ["queued", "stale"]
    actions = provider(case)
    assert sum(bool(case.timeline.events(actions._slot(turn, 0))) for turn, _ in turns) == 1
