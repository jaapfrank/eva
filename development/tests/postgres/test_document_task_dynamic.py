"""Dynamic project claims preserve old slots and do not cap project count."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import json
import pytest
from eva.application.conversation import ConversationActionCall, ConversationConflict
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.providers.document_goal_actions import DocumentGoalActionsProviderFactory
from eva.providers.document_task_input import DocumentTaskInput, DocumentTaskPool
from tests.postgres.test_document_goal_actions import project
from tests.postgres.test_operational_timeline import timeline_engine


def test_dynamic_claims_legacy_replay_concurrency_listing_stop_and_worker_restart(project, private_tmp_path, monkeypatch):
    p = project
    old_payload = {"contract": "EvaDocumentTaskPool/v1", "slots": [
        {"slot_id": "old-one", "tool_configuration_sha256": "a" * 64},
        {"slot_id": "old-two", "tool_configuration_sha256": "b" * 64}]}
    old_pool = DocumentTaskPool.from_json(json.dumps(old_payload))
    task = DocumentTaskInput("Legacy project", "First", "Second")
    legacy_turn = p["turn"]("legacy-before-upgrade")
    common = {"authority_sha256": p["authority"].configuration_sha256,
        "tenant_id": str(p["authority"].scope.tenant_id), "installation_id": str(p["authority"].scope.installation_id)}
    old = old_pool.claim(p["store"], task=task, turn=legacy_turn, call_id="legacy", **common)
    raw = json.dumps({**old_payload, "contract": "EvaDocumentTaskPool/v2",
                     "dynamic_tool_configuration_sha256": "c" * 64})
    pool = DocumentTaskPool.from_json(raw)
    assert pool.legacy_sha256 == old_pool.sha256 and pool.sha256 != old_pool.sha256
    assert pool.claim(p["store"], task=task, turn=legacy_turn, call_id="legacy", **common) == old
    new_turn = p["turn"]("concurrent-new")
    def claim(_):
        return pool.claim(p["store"], task=replace(task, title="Concurrent project"),
                          turn=new_turn, call_id="race", **common)
    with ThreadPoolExecutor(max_workers=3) as executor:
        concurrent = list(executor.map(claim, range(3)))
    assert concurrent[0] == concurrent[1] == concurrent[2]
    assert concurrent[0]["slot_id"] == "task:" + concurrent[0]["task_id"]
    with pytest.raises(ConversationConflict, match="replay"):
        pool.claim(p["store"], task=replace(task, title="Changed"), turn=new_turn, call_id="race", **common)
    # More than twenty projects exercises public paging; none consume legacy slots.
    for index in range(22):
        pool.claim(p["store"], task=replace(task, title=f"New project {index}"),
                   turn=p["turn"](f"new-{index}"), call_id=f"new-{index}", **common)
    assert p["store"].document_slot("old-one") == old
    assert p["store"].document_slot("old-two") is None
    # Force several real SQL pages without manufacturing additional project work.
    original_page = p["store"].document_claims
    monkeypatch.setattr(p["store"], "document_claims",
        lambda **kwargs: original_page(**{**kwargs, "limit": 7}))
    claims = list(pool.claims(p["store"]))
    assert len(claims) == 24 and len({item[1]["task_id"] for item in claims}) == 24
    pool_file = private_tmp_path / "dynamic-pool.json"
    pool_file.write_text(raw); pool_file.chmod(0o600)
    settings = replace(p["settings"], pool_file=str(pool_file), plan_file="", definition_file="")
    def call(message, name, arguments):
        bundle = DocumentGoalActionsProviderFactory.build(settings, p["timeline_engine"])
        route = PostgresConversationActions(p["timeline_engine"], external_tools=bundle.external_tools)
        return route.execute(p["turn"](message), ConversationActionCall(message, name, arguments)).output["result"]
    first = call("page-one", "list_document_projects", {})
    assert len(first["projects"]) == 20 and first["next_cursor"]
    second = call("page-two", "list_document_projects", {"cursor": first["next_cursor"]})
    assert len(second["projects"]) == 4 and "next_cursor" not in second
    assert len({row["goal_id"] for row in first["projects"] + second["projects"]}) == 24
    selected = call("title-search", "list_document_projects", {"query": "Concurrent"})
    assert [row["goal_id"] for row in selected["projects"]] == [concurrent[0]["plan"]["goal_id"]]
    # Existing worker repairs claims whose process ended before SQL admission.
    import eva.bootstrap.document_goal_worker as bootstrap
    monkeypatch.setenv("EVA_DOCUMENT_TASK_POOL_FILE", str(pool_file))
    monkeypatch.setenv("EVA_DOCUMENT_GOAL_PLAN_FILE", "")
    monkeypatch.setenv("EVA_MCP_AUTONOMY_MANDATE_FILE", str(p["mandate_path"]))
    monkeypatch.delenv("EVA_MCP_MANAGED_ENDPOINTS_FILE", raising=False)
    callbacks = {}
    def capture(**kwargs):
        callbacks.update(kwargs)
        return 0
    monkeypatch.setattr(bootstrap, "run_loop", capture)
    monkeypatch.setattr(bootstrap, "build_document_goal_ports", lambda **kw: json.loads(kw["read_bound_plan"]()))
    bootstrap.main()
    pending = callbacks["select_goals"](p["store"], p["goal"].created_at)
    assert len(pending) == 24
    for goal in pending:
        callbacks["worker_factory"](p["timeline_engine"], p["store"], goal, settings.tenant_id, settings.installation_id)
    target = concurrent[0]["plan"]["goal_id"]
    assert call("stop-new", "stop_document_project", {"goal_id": target})["project_status"] == "cancelled"
    assert call("legacy-status", "get_document_project", {"goal_id": old["plan"]["goal_id"]})["project_status"] == "waiting"
    bootstrap.main()
    assert len(callbacks["select_goals"](p["store"], p["goal"].created_at)) == 23
    assert p["store"].document_slot("old-one") == old
    changed = DocumentTaskPool.from_json(raw.replace('"c' + 'c' * 63 + '"', '"d' + 'd' * 63 + '"'))
    with pytest.raises(ConversationConflict, match="binding"):
        changed.find(p["store"], concurrent[0]["task_id"])

    # Read-only health must traverse dynamic claims as well as retained slots.
    import importlib.util
    from pathlib import Path
    spec = importlib.util.spec_from_file_location("dynamic_health",
        Path(__file__).resolve().parents[3] / "deploy/living-core/document-worker-health.py")
    health = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(health)
    monkeypatch.setattr(health, "check_process", lambda: None)
    dsn = private_tmp_path / "dynamic-health-dsn"
    dsn.write_text(p["timeline_engine"].url.render_as_string(hide_password=False)); dsn.chmod(0o600)
    monkeypatch.setenv("EVA_DATABASE_DSN_FILE", str(dsn))
    monkeypatch.setenv("EVA_RUNTIME_TENANT_ID", settings.tenant_id)
    monkeypatch.setenv("EVA_RUNTIME_INSTALLATION_ID", settings.installation_id)
    health.check()
    pool_file.write_text(raw.replace('"c' + 'c' * 63 + '"', '"d' + 'd' * 63 + '"'))
    with pytest.raises(ConversationConflict, match="binding"):
        health.check()
