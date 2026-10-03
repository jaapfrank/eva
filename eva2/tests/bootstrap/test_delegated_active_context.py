"""Providerless selection context uses only authorized active work metadata."""
from types import SimpleNamespace as NS

import pytest

from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.adapters.postgres.scoped_delegated_work_actions import ScopedDelegatedWorkActions
from eva.application.conversation import ConversationConflict


class Service:
    def __init__(self, jobs=(), *, allowed=True, truncated=False):
        self.jobs, self.allowed, self.truncated = jobs, allowed, truncated
        self.reads = []

    def available_for(self, turn):
        return self.allowed

    def execute(self, operation, turn, **kwargs):
        self.reads.append((operation, turn, kwargs))
        assert operation == "list"
        return {"jobs": self.jobs, "truncated": self.truncated}


def test_unavailable_scope_does_not_read_work():
    service = Service(allowed=False)
    actions = PostgresDelegatedWorkActions(None, service)
    assert actions.active_context(NS()) == ()
    assert service.reads == []


def test_only_active_work_metadata_reaches_context():
    jobs = [{"work_id": "work:" + state, "title": "Title " + state,
             "state": state, "stop_requested": False,
             "result": "SECRET_RESULT", "question": "PRIVATE_QUESTION",
             "task": "PRIVATE_TASK"}
            for state in ("admitted", "call_started", "succeeded", "failed", "stopped")]
    service = Service(jobs)
    turn = NS(message_id="message:test")
    context = PostgresDelegatedWorkActions(None, service).active_context(turn)
    assert isinstance(context, tuple)
    text = "\n".join(context)
    assert "work:admitted" in text and "work:call_started" in text
    for state in ("succeeded", "failed", "stopped"):
        assert "work:" + state not in text
    for secret in ("SECRET_RESULT", "PRIVATE_QUESTION", "PRIVATE_TASK"):
        assert secret not in text
    assert len(service.reads) == 1
    assert service.reads[0][1] is turn


def test_active_context_is_bounded_and_marks_incomplete_listing():
    jobs = [{"work_id": f"work:item-{i:02d}", "title": "x" * 1000,
             "state": "admitted", "stop_requested": False} for i in range(12)]
    service = Service(jobs, truncated=True)
    text = "\n".join(PostgresDelegatedWorkActions(None, service).active_context(NS(message_id="message:test")))
    assert sum(job["work_id"] in text for job in jobs) == 8
    assert "x" * 1000 not in text
    assert any(word in text.lower() for word in ("beperkt", "onvolledig", "truncated", "limited"))


def test_scoped_context_denial_does_not_fall_back_to_private_work(monkeypatch):
    private = NS(active_context=lambda turn: pytest.fail("private work leaked"))
    scoped = ScopedDelegatedWorkActions(private, NS())

    def denied(turn):
        raise ConversationConflict("household session unavailable")

    monkeypatch.setattr(scoped, "_select", denied)
    assert scoped.active_context(NS()) == ()


def test_scoped_context_forwards_only_selected_provider(monkeypatch):
    private = NS(active_context=lambda turn: pytest.fail("wrong scope selected"))
    scoped = ScopedDelegatedWorkActions(private, NS())
    received = []
    selected = NS(active_context=lambda turn: (received.append(turn) or "Selected task",))
    monkeypatch.setattr(scoped, "_select", lambda turn: selected)
    turn = NS(message_id="message:test")
    assert scoped.active_context(turn) == ("Selected task",)
    assert received == [turn]


def test_stop_requested_work_is_not_presented_as_active():
    service = Service([{"work_id": "work:stopping", "title": "Stopping",
                       "state": "call_started", "stop_requested": True}])
    assert PostgresDelegatedWorkActions(None, service).active_context(NS(message_id="message:test")) == ()


def test_truncated_listing_without_active_rows_does_not_claim_no_work():
    service = Service([], truncated=True)
    context = PostgresDelegatedWorkActions(None, service).active_context(NS(message_id="message:test"))
    assert context and '"limited":true' in context[0]


def test_metadata_hook_avoids_full_task_delivery_views():
    class MetadataService(Service):
        def active_work_metadata(self, turn):
            return {"jobs": [{"work_id": "work:active", "title": "Active",
                              "state": "admitted", "stop_requested": False}],
                    "truncated": False}

        def execute(self, *args, **kwargs):
            pytest.fail("full task view should not be loaded for context")

    context = PostgresDelegatedWorkActions(None, MetadataService()).active_context(
        NS(message_id="message:metadata"))
    assert 'work:active' in context[0]


def test_pending_selects_scope_once_and_preserves_step(monkeypatch):
    scoped = ScopedDelegatedWorkActions(None, None)
    selections = []
    pending = []
    provider = NS(pending=lambda turn, **kwargs: pending.append((turn, kwargs)) or "receipt")
    monkeypatch.setattr(scoped, "_select", lambda turn: selections.append(turn) or provider)
    turn = NS()
    assert scoped.pending(turn, step_index=3) == "receipt"
    assert selections == [turn]
    assert pending == [(turn, {"step_index": 3})]


def test_pending_denied_scope_does_not_read_provider(monkeypatch):
    scoped = ScopedDelegatedWorkActions(None, None)
    def denied(turn):
        raise ConversationConflict("unavailable")
    monkeypatch.setattr(scoped, "_select", denied)
    assert scoped.pending(NS()) is None


def test_named_authorized_task_remains_answerable_beyond_default_window():
    jobs = [{"work_id": f"work:item-{i:02d}", "title": f"Review document {i}",
             "state": "admitted", "stop_requested": False} for i in range(12)]
    service = Service(jobs)
    text = "\n".join(PostgresDelegatedWorkActions(None, service).active_context(
        NS(message_id="message:followup", text="Hoe staat work:item-11 ervoor?")))
    assert 'work:item-11' in text
    assert sum(job["work_id"] in text for job in jobs) == 8
    assert len(service.reads) == 1


def test_subject_followup_selects_recent_result_without_exposing_its_payload():
    jobs = [{"work_id": f"work:result-{i}", "title": f"Other project {i}",
             "state": "succeeded", "recent_outcome": True,
             "updated_at": f"2026-10-03T09:00:0{i}Z"} for i in range(5)]
    jobs[0].update(title="Bijen houden in de achtertuin", result="PRIVATE_RESULT", task="PRIVATE_TASK")
    service = Service(jobs)
    text = "\n".join(PostgresDelegatedWorkActions(None, service).active_context(
        NS(message_id="message:subject", text="Hoe staat het met de bijen in de achtertuin?")))
    assert "work:result-0" in text
    assert sum(job["work_id"] in text for job in jobs) == 3
    assert "PRIVATE_RESULT" not in text and "PRIVATE_TASK" not in text
    assert len(service.reads) == 1
