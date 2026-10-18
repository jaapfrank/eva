"""Daily orientation retains unfinished work without reviving old closed requests."""
from datetime import timedelta
import pytest
from tests.bootstrap.test_initiative_work_context import work
from tests.bootstrap.test_initiative_conversation_context import context_service, NOW


@pytest.mark.parametrize("state", ["succeeded", "verified", "stopped"])
def test_old_terminal_work_is_not_todays_obligation(context_service, state):
    service, _, _, _ = context_service
    old, recent = work("old"), work("recent")
    old.state = state
    old.updated_at = NOW - timedelta(days=2)
    old.spec.created_at = old.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows = [old, recent]
    service.store.list_owned = lambda **kw: rows
    result = service.private_initiative_work_context(now=NOW)
    assert [x["work_id"] for x in result["jobs"]] == [recent.spec.work_id]
    assert rows == [old, recent] and old.state == state
    assert result["is_execution_permission"] is False


@pytest.mark.parametrize("state", ["admitted", "call_started", "ambiguous", "needs_help"])
def test_old_unresolved_work_remains_visible_without_resume_permission(context_service, state):
    service, _, _, _ = context_service
    old = work("unresolved")
    old.state = state
    old.updated_at = NOW - timedelta(days=2)
    old.spec.created_at = old.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    service.store.list_owned = lambda **kw: [old]
    result = service.private_initiative_work_context(now=NOW)
    assert result["jobs"][0]["state"] == state
    assert result["is_execution_permission"] is False


def test_old_success_does_not_erase_uncertain_earlier_generation(context_service):
    service, _, _, _ = context_service
    old, new = work("old-unknown"), work("old-success")
    old.state = "ambiguous"
    for index, row in enumerate([old, new]):
        row.updated_at = NOW - timedelta(days=2, seconds=1-index)
        row.spec.created_at = row.updated_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    old.spec.source_ingress_sha256 = new.spec.source_ingress_sha256
    service.store.list_owned = lambda **kw: [old, new]
    job, = service.private_initiative_work_context(now=NOW)["jobs"]
    assert job["work_id"] == new.spec.work_id
    assert job["state"] == "succeeded" and job["earlier_uncertain_attempts"] == 1
