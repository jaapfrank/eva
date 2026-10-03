"""Reviewed updates reuse authenticated transport and fixed task authority."""
from contextlib import contextmanager
from types import SimpleNamespace as NS
import json
import threading
from urllib.error import HTTPError

import pytest

from eva.adapters.connectors.component_evaluation import ComponentEvaluationClient, configured_component_evaluator
from eva.adapters.postgres import component_improvement_actions as actions
from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.contracts.canonical_json import canonical_sha256
from eva.operations.component_application_runtime import ComponentApplicationRuntime
from eva.operations.component_evaluation_service import EvaluationServer


@contextmanager
def host(queue):
    server = EvaluationServer(("127.0.0.1", 0), evaluator=None, token="test-token",
                              allowed_paths=(), reviewed_applications=queue)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown(); thread.join(); server.server_close()


def test_request_uses_existing_authenticated_host_and_exact_identifiers(tmp_path):
    received = []
    queue = NS(request=lambda *args: received.append(args) or {"state": "queued", "installed": False})
    token = tmp_path / "token"; token.write_text("test-token")
    with host(queue) as url:
        client = ComponentEvaluationClient(url, str(token), ("contracts/timestamps.py",),
                                            "a" * 64, reviewed_releases=(("accepted", "b" * 64),))
        result = client.application("request", review_id="accepted", request_id="request1", task_id="work:1")
        assert result == {"state": "queued", "installed": False}
        assert received == [("accepted", "request1", "work:1")]
        token.write_text("wrong-token")
        with pytest.raises(HTTPError) as error:
            client.application("request", review_id="accepted", request_id="request1", task_id="work:1")
        assert error.value.code == 401 and len(received) == 1


def test_no_application_endpoint_without_configured_queue(tmp_path):
    token = tmp_path / "token"; token.write_text("test-token")
    with host(None) as url:
        client = ComponentEvaluationClient(url, str(token), (), "a" * 64,
                                            reviewed_releases=(("accepted", "b" * 64),))
        with pytest.raises(HTTPError) as error:
            client.application("request", review_id="accepted", request_id="request1", task_id="work:1")
        assert error.value.code == 404


def test_legacy_client_config_does_not_add_application_tool(tmp_path, monkeypatch):
    config = tmp_path / "client.json"
    config.write_text(json.dumps({"url": "http://127.0.0.1:9999", "token_file": "/tmp/token",
        "allowed_paths": ["contracts/timestamps.py"], "suite_sha256": "a"*64, "timeout_seconds": 120}))
    monkeypatch.setenv("EVA_COMPONENT_EVALUATION_CLIENT_FILE", str(config))
    assert configured_component_evaluator().reviewed_releases == ()
    assert actions.APPLY not in {d.name for d in actions.component_descriptors()}


@pytest.fixture
def application(monkeypatch):
    turn = NS(tenant_id="home", installation_id="eva", principal_id="owner")
    scope = canonical_sha256(vars(turn))
    received, dispatches = [], []
    client = NS(reviewed_releases=(("accepted", scope),),
        application=lambda *args, **kwargs: received.append((args, kwargs)) or {"state": "queued", "installed": False})
    class Timeline:
        def __init__(self, *args): self.rows = {}
        def events(self, key): return self.rows.get(key, [])
        def create_thread(self, **kwargs):
            self.rows[kwargs["thread_id"]] = [NS(payload={"data": kwargs["data"]})]
    monkeypatch.setattr(actions, "configured_component_evaluator", lambda: client)
    monkeypatch.setattr(actions, "PostgresOperationalTimeline", Timeline)
    spec = NS(execution_id="execution:1", work_id="work:1", source=NS(model_dump=lambda **kwargs: vars(turn)))
    provider = actions.ComponentImprovementActions(None, spec, turn, lambda incoming: None,
                                                   lambda incoming: dispatches.append(incoming), lambda: None)
    return provider, turn, client, received, dispatches


def test_scoped_request_is_durable_and_replays_without_redispatch(application):
    provider, turn, client, received, dispatches = application
    call = ConversationActionCall("call:1", actions.APPLY, {"review_id": "accepted"})
    first = provider.execute(turn, call, step_index=2)
    assert first.output["scope"] == "reviewed_application_requested"
    assert first.output["operation_result"]["installed"] is False
    assert provider.execute(turn, call, step_index=2) == first
    assert len(received) == len(dispatches) == 1
    assert received[0][1]["task_id"] == "work:1"
    assert len(received[0][1]["request_id"]) == 64


def test_different_source_cannot_request_reviewed_release(application):
    provider, turn, client, received, dispatches = application
    client.reviewed_releases = (("accepted", "0"*64),)
    with pytest.raises(ConversationConflict, match="not approved"):
        provider.execute(turn, ConversationActionCall("call:1", actions.APPLY, {"review_id": "accepted"}))
    assert not received and not dispatches


def test_model_cannot_supply_command_or_task_identity(application):
    provider, turn, client, received, dispatches = application
    with pytest.raises(ConversationConflict, match="fields are invalid"):
        provider.execute(turn, ConversationActionCall("call:1", actions.APPLY,
            {"review_id": "accepted", "task_id": "work:other", "command": "something"}))
    assert not received and not dispatches


@pytest.mark.parametrize("state", ["ready", "waiting", "cancelled"])
def test_host_reads_metadata_in_existing_core_and_keeps_credentials_there(state):
    received = []
    def run(argv, **kwargs):
        received.append((argv, kwargs))
        return NS(returncode=0, stdout=json.dumps({"state": state}), stderr="")
    runtime = ComponentApplicationRuntime({"core_container": "eva-core", "scope_sha256": "a"*64}, run=run)
    assert runtime.ready_to_apply("work:1") == state
    argv, arguments = received[0]
    assert argv[:6] == ["docker", "exec", "-i", "eva-core", "python", "-"]
    assert json.loads(argv[6])["task_id"] == "work:1"
    assert "stop_requested_at" in arguments["input"] and "pg_locks" in arguments["input"]
    assert "shell" not in arguments


def test_failed_readiness_does_not_authorize_update():
    runtime = ComponentApplicationRuntime({"core_container": "eva-core", "scope_sha256": "a"*64},
        run=lambda *a, **kw: NS(returncode=1, stdout="", stderr="private error"))
    with pytest.raises(ValueError, match="unavailable"):
        runtime.ready_to_apply("work:1")


def test_later_status_reads_by_approved_review_without_new_application(tmp_path):
    calls = []
    queue = NS(request=lambda *args: pytest.fail("status redispatched application"),
        review_status=lambda review: calls.append(review) or {"state": "completed", "installed": True})
    token = tmp_path / "token"; token.write_text("test-token")
    with host(queue) as url:
        client = ComponentEvaluationClient(url, str(token), (), "a"*64,
            reviewed_releases=(("accepted", "b"*64),))
        result = client.application("status", review_id="accepted", request_id="later-request", task_id="work:later")
    assert result == {"state": "completed", "installed": True}
    assert calls == ["accepted"]


def test_status_keeps_same_verified_person_boundary(application):
    provider, turn, client, received, dispatches = application
    call = ConversationActionCall("status:1", actions.APPLICATION_STATUS, {"review_id": "accepted"})
    result = provider.execute(turn, call)
    assert result.output["scope"] == "reviewed_application_status"
    assert received[0][0] == ("status",)
    client.reviewed_releases = (("accepted", "0"*64),)
    with pytest.raises(ConversationConflict, match="not approved"):
        provider.execute(turn, call, step_index=1)
    assert len(received) == len(dispatches) == 1
