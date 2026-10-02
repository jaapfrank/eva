"""Focused boundaries between durable work and the shared host service."""
import asyncio
from datetime import UTC, datetime
import json
import threading
from types import SimpleNamespace
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from eva.operations.component_evaluation_service import EvaluationServer
from eva.adapters.connectors.programming_workspace import ProgrammingWorkspaceClient
from eva.adapters.postgres import programming_actions as actions
from eva.application.cancellation import CancellationSignal, WorkInterrupted, cancellation_scope
from eva.application.conversation import ConversationActionCall, ConversationTurn


class Workspace:
    def __init__(self):
        self.calls = []
    def close(self, key):
        self.calls.append(("close", key))
    def processes_idle(self, key, *, workspace_key):
        self.calls.append(("status", key, workspace_key))
        return {"idle": False}
    def list_tools(self, key, *, workspace_key):
        self.calls.append(("list", key, workspace_key))
        return {"tools": []}
    def call_tool(self, key, name, arguments, request_id, *, workspace_key):
        self.calls.append((key, name, arguments, request_id, workspace_key))
        return {"content": [], "isError": False}


@pytest.fixture
def service():
    workspace = Workspace()
    server = EvaluationServer(("127.0.0.1", 0), evaluator=None, token="x" * 40,
                              allowed_paths=(), programming_workspaces=workspace)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, workspace
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def request(server, data, token="x" * 40):
    url = "http://127.0.0.1:" + str(server.server_address[1]) + "/programming"
    req = Request(url, json.dumps(data).encode(), headers={
        "Authorization": "Bearer " + token, "Content-Type": "application/json"})
    with urlopen(req, timeout=3) as response:
        return json.load(response)


def test_stop_is_not_blocked_by_evaluation_lock(service):
    server, workspace = service
    server.execution_lock.acquire()
    try:
        assert request(server, {"action": "close", "session_key": "a" * 64}) == {"closed": True}
    finally:
        server.execution_lock.release()
    assert workspace.calls == [("close", "a" * 64)]


def test_service_passes_bound_workspace_and_request_without_host_paths(service):
    server, workspace = service
    data = {"action": "call", "session_key": "a" * 64, "workspace_key": "b" * 64,
            "name": "read_file", "arguments": {"path": "/workspace/a.py"}, "request_id": "c" * 64}
    assert request(server, data)["isError"] is False
    assert workspace.calls[0] == ("a" * 64, "read_file", data["arguments"], "c" * 64, "b" * 64)
    with pytest.raises(HTTPError) as error:
        request(server, {**data, "host_path": "/home"})
    assert error.value.code == 400
    assert len(workspace.calls) == 1


def test_service_requires_existing_token_before_execution(service):
    server, workspace = service
    with pytest.raises(HTTPError) as error:
        request(server, {"action": "close", "session_key": "a" * 64}, token="wrong")
    assert error.value.code == 401
    assert workspace.calls == []


def test_client_stop_cancels_remote_session_after_http_cancel(monkeypatch):
    seen = []
    signal = CancellationSignal()
    async def post(self, payload):
        seen.append(payload)
        if payload["action"] != "close":
            signal.cancel()
            await asyncio.sleep(0)
        return {"closed": True}
    monkeypatch.setattr(ProgrammingWorkspaceClient, "_post", post)
    client = ProgrammingWorkspaceClient("http://localhost:1", "/unused")
    with cancellation_scope(signal), pytest.raises(WorkInterrupted):
        client.request("a" * 64, "b" * 64, "call", name="start_process")
    assert [x["action"] for x in seen] == ["call", "close"]


class Timeline:
    def __init__(self, *args):
        self.items = {}
    def events(self, name):
        return self.items.get(name, [])
    def create_thread(self, **kwargs):
        self.items[kwargs["thread_id"]] = [SimpleNamespace(payload={"data": kwargs["data"]})]


class Client:
    def __init__(self):
        self.calls = []
    def request(self, session, workspace, action, **kw):
        self.calls.append((session, workspace, action, kw))
        if action == "list":
            return {"tools": [{"name": "start_process", "description": "Run a process",
                               "inputSchema": {"type": "object", "properties": {"command": {"type": "string"}}}}]}
        return {"content": [{"type": "text", "text": "executed"}], "isError": False}
    def close(self, key):
        self.calls.append(("close", key))


def fixture_actions(monkeypatch, work_id="attempt-one"):
    monkeypatch.setattr(actions, "PostgresOperationalTimeline", Timeline)
    turn = ConversationTurn.private("tenant", "installation", "owner", "conversation", "message", "Write code", "2026-10-02T00:00:00Z")
    spec = SimpleNamespace(execution_id="stable-execution", work_id=work_id,
        source=SimpleNamespace(model_dump=lambda **kw: {"tenant": "tenant", "owner": "owner"}), source_ingress_sha256="a" * 64)
    client = Client()
    tool = actions.ProgrammingActions(None, spec, turn, lambda t: None, lambda t: None,
                                      lambda: datetime.now(UTC), client=client)
    return tool, turn, client


def test_resume_reuses_workspace_with_new_process_session(monkeypatch):
    first, _, _ = fixture_actions(monkeypatch)
    second, _, _ = fixture_actions(monkeypatch, "attempt-two")
    assert first.workspace_key == second.workspace_key
    assert first.session_key != second.session_key


def test_recorded_programming_step_replays_without_execution(monkeypatch):
    tool, turn, client = fixture_actions(monkeypatch)
    call = ConversationActionCall("call", actions.CALL, {"tool_name": "start_process", "arguments_json": '{"command":"python -V"}'})
    result = tool.execute(turn, call, step_index=2)
    assert tool.execute(turn, call, step_index=2) == result
    assert len(client.calls) == 1
    assert result.output["installed"] is False
    assert result.output["semantic_success_verified"] is False
    tool.close()
    assert client.calls[-1] == ("close", tool.session_key)


def test_catalog_listing_is_compact_and_specific_schema_is_available(monkeypatch):
    tool, turn, _ = fixture_actions(monkeypatch)
    compact = tool.execute(turn, ConversationActionCall("list", actions.LIST, {"tool_name": ""}))
    assert "inputSchema" not in compact.output["programming_result"]["tools"][0]
    detail = tool.execute(turn, ConversationActionCall("schema", actions.LIST, {"tool_name": "start_process"}), step_index=1)
    assert detail.output["programming_result"]["tools"][0]["inputSchema"]["type"] == "object"


def test_process_status_comes_from_bound_host_session(service):
    server, workspace = service
    assert request(server, {"action": "status", "session_key": "a" * 64,
                            "workspace_key": "b" * 64}) == {"idle": False}
    assert workspace.calls == [("status", "a" * 64, "b" * 64)]


def test_running_process_cannot_be_confirmed_by_tool_receipt(monkeypatch):
    tool, turn, client = fixture_actions(monkeypatch)
    tool.started = True
    assert tool.verify_idle() is False
    assert client.calls[-1][2] == "status"


def test_replay_only_attempt_still_inspects_and_closes_remote_session(monkeypatch):
    tool, turn, client = fixture_actions(monkeypatch)
    call = ConversationActionCall("launch", actions.CALL, {
        "tool_name": "start_process", "arguments_json": '{"command":"python -V"}'})
    saved = tool.execute(turn, call, step_index=2)
    tool.started = False  # New worker object over the same durable receipts.
    assert tool.pending(turn, step_index=2) == saved
    assert tool.started and tool.verify_idle() is False
    tool.close()
    assert [item[2] for item in client.calls if len(item) == 4] == ["call", "status"]
    assert client.calls[-1] == ("close", tool.session_key)
