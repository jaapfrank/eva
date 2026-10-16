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


def test_http_runtime_and_existing_service_share_saved_stop_and_private_result(monkeypatch):
    # This HTTP/service test isolates storage; current SQL counts have their own
    # disposable PostgreSQL tests. Keep its fake store focused on control paths.
    monkeypatch.setattr("eva.bootstrap.dashboard_work_overview.owner_work_overview",
                        lambda service, turn, result: result)
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from eva.adapters.api.dashboard_access import DashboardAccess
    from eva.adapters.api.dashboard_work import install_dashboard_work
    from eva.bootstrap.delegated_work_owner_service import DelegatedWorkOwnerService
    from eva.bootstrap.runtime import PostgresHindsightMemoryRuntime
    from tests.bootstrap.test_delegated_work_commands import FakeStore, OWNER, BINDING, NOW
    from types import SimpleNamespace

    class Store(FakeStore):
        revoked = False
        def validate_admission_authority(self, **values):
            if self.revoked:
                raise RuntimeError("owner revoked")
            super().validate_admission_authority(**values)
        def request_stop(self, work_id, **values):
            row = self.load(work_id)
            row.state = "stopped"
            return row

    monkeypatch.setattr("eva.adapters.postgres.scheduled_work.ScheduledOwnerWork",
                        lambda service: SimpleNamespace(list=lambda: {"schedules": [], "truncated": False}))
    store = Store()
    store.owned_work = [store._work()]
    service = DelegatedWorkOwnerService(store, owner_principal_id=OWNER,
        principal_binding_id=BINDING, clock=lambda: NOW,
        delivery_status_loader=lambda identifier: "blocked")
    # The real constructor and real owner service are used. Storage is isolated;
    # existing Postgres lifecycle tests cover its durable implementation.
    runtime = PostgresHindsightMemoryRuntime(
        engine=None, memory=None, conversation=None, cold_path=None,
        prototype_store=None, release_store=None, conversation_actions=None,
        runtime_tenant_id="11111111-1111-1111-1111-111111111111",
        runtime_installation_id="22222222-2222-2222-2222-222222222222",
        delegated_work_store=store, delegated_work_owner_service=service,
        dashboard_owner_principal_id=OWNER, dashboard_principal_binding_id=BINDING,
    )
    assert runtime._delegated_work_owner_service is service
    app = FastAPI()
    install_dashboard_work(app, access=DashboardAccess(), runtime=runtime)
    client = TestClient(app)
    url = "/internal/v1/dashboard/work"
    assert client.get(url).json()["jobs"][0]["state"] == "admitted"
    assert client.post(url + "/stop", json={"work_id": "work:fake"}).json()["work"]["state"] == "stopped"
    assert client.get(url).json()["jobs"][0]["state"] == "stopped"
    row = store.owned_work[0]
    row.state = "succeeded"
    row.result = SimpleNamespace(output="Bewaard privéresultaat", output_sha256="a" * 64,
        completed_at="2026-08-26T14:00:00Z")
    assert client.post(url + "/result", json={"work_id": "work:fake"}).json()["work"]["result"]["output"] == "Bewaard privéresultaat"
    store.revoked = True
    response = client.post(url + "/result", json={"work_id": "work:fake"})
    assert response.status_code == 503 and "Bewaard privéresultaat" not in response.text
