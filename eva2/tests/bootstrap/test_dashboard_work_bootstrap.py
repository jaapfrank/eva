from uuid import UUID
import pytest
from eva.bootstrap.dashboard_work import dashboard_delegated_work


def test_shared_service_receives_fixed_private_owner_and_authority_rechecked():
    calls = []
    class Service:
        def validate_authority(self, turn):
            calls.append(("authority", turn))
        def execute(self, operation, turn, **kwargs):
            calls.append((operation, turn, kwargs))
            return {"work": {"state": "stopped"}}
    owner, binding = UUID(int=1), UUID(int=2)
    result = dashboard_delegated_work(Service(), tenant_id="tenant:test", installation_id="installation:test",
        owner_principal_id=owner, principal_binding_id=binding, operation="stop", work_id="work:test")
    assert result["work"]["state"] == "stopped"
    assert [item[0] for item in calls] == ["authority", "stop", "authority"]
    turn = calls[1][1]
    assert turn.principal_id == str(owner) and turn.audience.kind == "private_person"
    assert turn.conversation_id == f"dashboard:{binding}"
    assert calls[1][2]["work_id"] == "work:test"


def test_revoked_authority_blocks_saved_result():
    class Service:
        count = 0
        def validate_authority(self, turn):
            self.count += 1
            if self.count == 2: raise RuntimeError("revoked")
        def execute(self, *args, **kwargs):
            return {"private": "result"}
    with pytest.raises(RuntimeError, match="revoked"):
        dashboard_delegated_work(Service(), tenant_id="tenant:test", installation_id="installation:test",
            owner_principal_id=UUID(int=1), principal_binding_id=UUID(int=2), operation="get", work_id="work:test")
