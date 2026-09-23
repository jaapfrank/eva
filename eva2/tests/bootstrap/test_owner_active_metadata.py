"""Task context reads only bounded owner metadata, not delivery presentation."""
from types import SimpleNamespace as NS
from uuid import uuid4

import pytest

from eva.adapters.postgres.delegated_work import DelegatedWorkConflict
from eva.adapters.postgres.operational_timeline import external_timeline_scope
from eva.application.conversation import ConversationTurn
from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService


def setup(jobs):
    service = object.__new__(DelegatedWorkOwnerService)
    service.owner_principal_id, service.principal_binding_id = uuid4(), uuid4()
    service._private_bridge_principal_loader = None
    turn = ConversationTurn.private("tenant:test", "installation:test",
        str(service.owner_principal_id), "private:test", "message:test", "Hello",
        "2026-09-23T12:00:00Z")
    service.conversation_scope = external_timeline_scope(turn.tenant_id, turn.installation_id, "test")
    reads = []
    def list_owned(**kwargs):
        reads.append(kwargs)
        return jobs[:kwargs["limit"]]
    service.store = NS(list_owned=list_owned)
    service._delivery_status_loader = lambda _: pytest.fail("metadata must not load delivery status")
    service._view = lambda *a, **kw: pytest.fail("metadata must not build full task views")
    return service, turn, reads


def job(index, *, stopped=False):
    return NS(spec=NS(work_id=f"work:{index}", task="PRIVATE TASK"), title=f"Title {index}",
              state="call_started", stop_requested_at="some time" if stopped else None,
              result=NS(output="PRIVATE RESULT"))


def test_metadata_has_only_selection_fields_without_delivery_or_result_reads():
    service, turn, reads = setup([job(1), job(2, stopped=True)])
    assert service.active_work_metadata(turn) == {"jobs": [
        {"work_id": "work:1", "title": "Title 1", "state": "call_started", "stop_requested": False},
        {"work_id": "work:2", "title": "Title 2", "state": "call_started", "stop_requested": True},
    ], "truncated": False}
    assert reads == [{"owner_principal_id": service.owner_principal_id,
                      "principal_binding_id": service.principal_binding_id, "limit": 21}]


def test_metadata_caps_twenty_and_reports_truncation():
    service, turn, reads = setup([job(i) for i in range(25)])
    result = service.active_work_metadata(turn)
    assert len(result["jobs"]) == 20 and result["truncated"] is True
    assert reads[0]["limit"] == 21


def test_metadata_rejects_other_owner_before_reading():
    service, turn, reads = setup([job(1)])
    other = ConversationTurn.private(turn.tenant_id, turn.installation_id, str(uuid4()),
        turn.conversation_id, turn.message_id, turn.text, turn.occurred_at)
    with pytest.raises(DelegatedWorkConflict):
        service.active_work_metadata(other)
    assert reads == []


def test_metadata_does_not_swallow_revoked_store_authority():
    service, turn, reads = setup([])
    def revoked(**kwargs):
        raise DelegatedWorkConflict("owner binding revoked")
    service.store.list_owned = revoked
    with pytest.raises(DelegatedWorkConflict, match="revoked"):
        service.active_work_metadata(turn)


def test_normal_owner_list_still_uses_full_presentation():
    service, turn, reads = setup([job(1)])
    viewed = []
    service._view = lambda work, **kwargs: viewed.append((work, kwargs)) or {"delivery": "full-view"}
    result = service.execute("list", turn, request_key="normal-list")
    assert result["jobs"] == [{"delivery": "full-view"}]
    assert viewed[0][1] == {"include_result": False, "turn": turn}
