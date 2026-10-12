from copy import deepcopy
from types import SimpleNamespace as NS
import hashlib
import pytest

from eva.contracts.canonical_json import canonical_sha256
from eva.operations.component_application_runtime import _registered_lifecycle_origin, _READINESS


@pytest.mark.parametrize("project_template", (
    "component-work-evaluation:{registration}:construction",
    "component-work-strategy:{registration}",
))
def test_technical_zero_contact_requires_exact_original_source_and_spec(project_template):
    source = {"tenant_id":"tenant","installation_id":"installation","principal_id":"owner",
        "conversation_id":"technical-session","message_id":"technical-message"}
    spec = {"source":source,"work_id":"work:technical","owner_principal_id":"owner",
        "principal_binding_id":"binding","source_ingress_sha256":"1"*64,
        "task":"Improve actual work","created_at":"2026-10-12T00:00:00Z"}
    scope = canonical_sha256({k:source[k] for k in ("tenant_id","installation_id","principal_id")})
    turn = NS(project_id=project_template.format(registration="2" * 64))
    origin = {"kind":"registered_lifecycle_technical_probe","stage":"construction",
        "registration_sha256":"2"*64,"resources_sha256":"3"*64,
        "runtime_admission_sha256":"4"*64,"input_sha256":"5"*64,"scope_sha256":scope,
        "identity":{"task_id":spec["work_id"],"conversation_id":source["conversation_id"],
            "message_id":source["message_id"],"source_ingress_sha256":spec["source_ingress_sha256"]},
        "actor":dict(source,principal_binding_id="binding"),
        "spec_sha256":canonical_sha256(spec),
        "task_sha256":hashlib.sha256(spec["task"].encode()).hexdigest(),
        "project_id":turn.project_id,"ownerAuthoredText":False,
        "selfChosenInitiative":False,"foreground_contact_used":False}
    assert _registered_lifecycle_origin(origin,spec,turn,scope)
    for key,value in [("spec_sha256","0"*64),("task_sha256","0"*64),
                      ("project_id","ordinary-owner"),("ownerAuthoredText",True),
                      ("foreground_contact_used",True),
                      ("registration_sha256","0"*64),("stage","independent_final")]:
        altered = deepcopy(origin); altered[key] = value
        assert not _registered_lifecycle_origin(altered,spec,turn,scope)
    assert not _registered_lifecycle_origin(None,spec,turn,scope)
    alien = deepcopy(spec); alien["source"]["principal_id"] = "another-person"
    assert not _registered_lifecycle_origin(origin,alien,turn,scope)
    alien_owner = deepcopy(spec); alien_owner["owner_principal_id"] = "another-person"
    rehashed_origin = dict(origin, spec_sha256=canonical_sha256(alien_owner))
    assert not _registered_lifecycle_origin(rehashed_origin,alien_owner,turn,scope)
    alien_actor = deepcopy(origin); alien_actor["actor"]["principal_id"] = "another-person"
    assert not _registered_lifecycle_origin(alien_actor,spec,turn,scope)
    ordinary = NS(project_id=None)
    assert not _registered_lifecycle_origin(dict(origin,project_id=None),spec,ordinary,scope)


def test_actual_core_readiness_program_compiles_without_running():
    compile(_READINESS,"actual-core-readiness-program","exec")


def _resumed_readiness(monkeypatch, *, active=False, contact=False):
    """Execute the actual core script and journal SQL against retained fake rows."""
    from contextlib import contextmanager, redirect_stdout
    from datetime import UTC, datetime, timedelta
    from io import StringIO
    import json
    import sys
    from uuid import UUID
    import sqlalchemy as sa
    from eva.application.conversation import ConversationModelUsage, ConversationTurn
    from eva.adapters.postgres.background_model_journal import BackgroundModelJournal
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
    from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, TimelineScope
    from eva.adapters.postgres.scope import PostgresScopedRole
    from eva.bootstrap.runtime import RuntimeSettings

    owner = "33333333-3333-4333-8333-333333333333"
    binding = "44444444-4444-4444-8444-444444444444"
    now = datetime.now(UTC)
    turn = ConversationTurn.private("tenant", "installation", owner, "technical-session",
        "technical-message", "synthetic retained task", "2026-10-12T00:00:00Z",
        project_id="component-work-evaluation:" + "2" * 64 + ":construction")
    source = {key: getattr(turn, key) for key in
              ("tenant_id", "installation_id", "principal_id", "conversation_id", "message_id")}
    scope_hash = canonical_sha256({key: source[key] for key in
                                  ("tenant_id", "installation_id", "principal_id")})
    root = {"work_id": "work:original", "source": source, "execution_id": "work:original",
        "source_ingress_sha256": canonical_sha256(PostgresConversationJournal._ingress_data(turn)),
        "grant": {"maximum_model_runs": 32}, "owner_principal_id": owner,
        "principal_binding_id": binding, "audience": {"kind": "private_person"},
        "episode_id": "episode", "generation": 1, "parent_task_id": "chat:original",
        "contract": "DelegatedDigitalWork/v4", "task": turn.text,
        "deadline": "2026-10-12T00:00:00Z", "model_profile_release_sha256": "a" * 64}
    leaf = dict(root, work_id="work:resume", generation=2, parent_task_id=root["work_id"],
        deadline="2099-01-01T00:00:00Z", model_profile_release_sha256="b" * 64)
    def terminal(inputs, outputs, responses, tools):
        return {"contract": "DelegatedWorkUsage/v1", "schema_version": 1,
            "measurement": "partial", "completed_model_responses": responses,
            "tool_calls": tools, "input_tokens": inputs, "output_tokens": outputs,
            "total_tokens": inputs + outputs, "cost_usd_micros": None}
    rows = []
    for spec, state, usage in [(root, "ambiguous", terminal(4909, 78, 1, 1)),
                               (leaf, "call_started" if active else "succeeded",
                                None if active else terminal(20, 2, 2, 1))]:
        rows.append({"work_id": spec["work_id"], "state": state, "stop_requested_at": None,
            "spec_payload": spec, "retention_until": now + timedelta(days=2),
            "usage_payload": usage,
            "usage_sha256": None if usage is None else bytes.fromhex(canonical_sha256(usage))})
    origin = {"kind": "registered_lifecycle_technical_probe", "stage": "construction",
        "registration_sha256": "2" * 64, "resources_sha256": "3" * 64,
        "runtime_admission_sha256": "4" * 64, "input_sha256": "5" * 64,
        "scope_sha256": scope_hash, "actor": {**source, "principal_binding_id": binding},
        "identity": {"task_id": root["work_id"], "conversation_id": turn.conversation_id,
            "message_id": turn.message_id, "source_ingress_sha256": root["source_ingress_sha256"]},
        "spec_sha256": canonical_sha256(root), "task_sha256": hashlib.sha256(turn.text.encode()).hexdigest(),
        "project_id": turn.project_id, "ownerAuthoredText": False,
        "selfChosenInitiative": False, "foreground_contact_used": False}
    events, reads, loads, dispatches, markers, stopped = {}, [], [], [], [], [False]
    timeline_scope = TimelineScope(UUID("11111111-1111-4111-8111-111111111111"),
                                   UUID("22222222-2222-4222-8222-222222222222"))
    accounting_journal = PostgresConversationJournal(
        NS(url=sa.engine.make_url("postgresql://synthetic")), timeline_scope)
    def record(conversation, index, inputs, outputs, tools):
        request = conversation + ":" + str(index)
        events.setdefault(conversation, []).extend([
            NS(event_type="CONVERSATION_MODEL_ADMITTED", data={"request_id": request}),
            NS(event_type="CONVERSATION_MODEL_RECORDED", data={"request_id": request,
                "usage": ConversationModelUsage.measured(tool_calls=tools,
                    input_tokens=inputs, output_tokens=outputs).to_wire()})])
    keys = [BackgroundModelJournal(accounting_journal, turn, work_id=spec["work_id"])
            ._accounting.conversation_id for spec in (root, leaf)]
    record(keys[0], 0, 4909, 78, 1)
    record(keys[1], 0, 10, 1, 1); record(keys[1], 1, 10, 1, 0)
    if contact:
        record(turn.conversation_id, 0, 7, 3, 0)
    class Result:
        def __init__(self, values): self.values = values
        def all(self): return self.values
        def mappings(self): return self
        def scalar_one_or_none(self): return self.values
    requested = [root["work_id"]]
    class Connection:
        def execute(self, query):
            names = list(query.selected_columns.keys())
            if names == ["spec_payload"]:
                return Result(next(row["spec_payload"] for row in rows if row["work_id"] == requested[0]))
            if "work_id" in names:
                return Result(list(reversed(rows)))
            assert names == ["event_type", "data"]
            parameters = query.compile().params.values()
            conversation = next(v for v in parameters if isinstance(v, str) and v in [*keys, turn.conversation_id])
            reads.append(conversation)
            return Result(events.get(conversation, []))
        def scalar(self, query): return 1 if contact else 0
    class Engine:
        url = sa.engine.make_url("postgresql://synthetic")
        @contextmanager
        def begin(self): yield Connection()
        def dispose(self): pass
    monkeypatch.setattr(sa, "create_engine", lambda *a, **kw: Engine())
    monkeypatch.setattr(RuntimeSettings, "from_environment", lambda: NS(database_dsn="postgresql://synthetic",
        dashboard_tenant_id=timeline_scope.tenant_id, dashboard_installation_id=timeline_scope.installation_id))
    monkeypatch.setattr(PostgresScopedRole, "_scope", lambda *a: None)
    monkeypatch.setattr(PostgresOperationalTimeline, "_scope", lambda *a: None)
    def timeline_events(self, thread_id):
        markers.append(thread_id)
        stop_identity = PostgresConversationJournal._stop_identity(
            turn.principal_id, turn.conversation_id, turn.message_id)
        if stopped[0] and thread_id == PostgresConversationJournal._stop_thread_id(stop_identity):
            return [NS(event_type="CONVERSATION_STOP_REQUESTED", payload={"data": stop_identity})]
        if thread_id == "work-memory:" + hashlib.sha256(root["work_id"].encode()).hexdigest():
            return [NS(event_type="HOST_EVALUATION_MEMORY_EXCLUDED",
                payload={"data": {"disposition": "host_evaluation_excluded", "origin": origin}})]
        return []
    monkeypatch.setattr(PostgresOperationalTimeline, "events", timeline_events)
    def load(self, work_id, *, owner_principal_id, principal_binding_id):
        assert str(owner_principal_id) == owner and str(principal_binding_id) == binding
        loads.append(work_id)
        row = next(row for row in rows if row["work_id"] == work_id)
        return NS(spec=NS(sha256=canonical_sha256(row["spec_payload"]), work_id=work_id),
            state=row["state"], stop_requested_at=row["stop_requested_at"],
            retention_until=row["retention_until"], storage_episode_id=UUID(int=1), call_id="actual-leaf-call")
    monkeypatch.setattr(PostgresDelegatedWorkStore, "load", load)
    monkeypatch.setattr(PostgresDelegatedWorkStore, "authorize_tool_dispatch", lambda self, call: dispatches.append(call))
    class SourceLoader:
        def __init__(self, *args): pass
        def __call__(self, reference):
            assert reference == source
            return turn
    monkeypatch.setitem(sys.modules, "eva.providers.mcp_autonomy", NS(_SourceTurnLoader=SourceLoader))
    def execute(mode, task_id):
        requested[0] = task_id
        request = {"task_id": task_id, "scope_sha256": scope_hash, "require_idle": False, mode: True}
        monkeypatch.setattr(sys, "argv", ["readiness", json.dumps(request)])
        output = StringIO()
        with redirect_stdout(output):
            exec(compile(_READINESS, "actual-core-readiness", "exec"), {})
        assert len(output.getvalue()) <= 4096
        return json.loads(output.getvalue())
    return NS(execute=execute, rows=rows, root=root, leaf=leaf, keys=keys, turn=turn,
        events=events, reads=reads, loads=loads, dispatches=dispatches, markers=markers, stopped=stopped)


def test_actual_resume_terminal_reads_each_journal_and_root_origin_once(monkeypatch):
    f = _resumed_readiness(monkeypatch)
    before = deepcopy(f.rows)
    result = f.execute("include_usage", f.root["work_id"])
    assert result["model_usage"] == {"measurement": "measured", "model_attempts": 3,
        "tool_calls": 2, "input_tokens": 4929, "output_tokens": 80}
    assert result["terminal_usage"]["measurement"] == "partial"
    assert result["terminal_usage"]["completed_model_responses"] == 3
    assert result["terminal_usage"]["total_tokens"] == 5009
    assert result["contact_usage"] is None
    assert result["contact_disposition"] == "registered_technical_origin_without_foreground"
    assert f.reads == [*f.keys, f.turn.conversation_id]
    assert f.loads == [f.root["work_id"], f.leaf["work_id"]]
    assert "work-memory:" + hashlib.sha256(f.root["work_id"].encode()).hexdigest() in f.markers
    assert "work-memory:" + hashlib.sha256(f.leaf["work_id"].encode()).hexdigest() not in f.markers
    assert f.rows == before


def test_active_resume_uses_current_leaf_spec_and_dispatch_guard(monkeypatch):
    import pytest
    f = _resumed_readiness(monkeypatch, active=True)
    with pytest.raises(ValueError, match="current active call"):
        f.execute("include_active_usage", f.root["work_id"])
    result = f.execute("include_active_usage", f.leaf["work_id"])
    assert result["spec_sha256"] == canonical_sha256(f.leaf)
    assert result["model_usage"]["input_tokens"] == 4929
    assert result["contact_disposition"] == "registered_technical_origin_without_foreground"
    assert len(f.dispatches) == 1 and f.dispatches[0].work_id == f.leaf["work_id"]


def test_terminal_package_binding_keeps_requested_spec_after_resume(monkeypatch):
    f = _resumed_readiness(monkeypatch)
    result = f.execute("include_binding", f.root["work_id"])
    assert result["state"] == "ready" and result["task_id"] == f.root["work_id"]
    assert result["spec_sha256"] == canonical_sha256(f.root)
    assert result["source_ingress_sha256"] == f.root["source_ingress_sha256"]


def test_original_contact_is_counted_once_for_resumed_ordinary_work(monkeypatch):
    f = _resumed_readiness(monkeypatch, contact=True)
    result = f.execute("include_usage", f.leaf["work_id"])
    assert result["contact_usage"] == {"measurement": "measured", "model_attempts": 1,
        "tool_calls": 0, "input_tokens": 7, "output_tokens": 3}
    assert result["contact_disposition"] == "recorded_original_ingress"
    assert f.reads == [*f.keys, f.turn.conversation_id]


def test_unknown_ancestor_journal_cannot_be_hidden_by_measured_leaf(monkeypatch):
    import pytest
    f = _resumed_readiness(monkeypatch)
    f.events[f.keys[0]].pop()  # Admission without its recorded physical result.
    with pytest.raises(ValueError, match="journal usage is incomplete"):
        f.execute("include_usage", f.leaf["work_id"])


def test_retained_ancestor_hash_and_retention_are_still_required(monkeypatch):
    from datetime import UTC, datetime, timedelta
    import pytest
    f = _resumed_readiness(monkeypatch)
    original = f.rows[0]["usage_sha256"]
    f.rows[0]["usage_sha256"] = bytes(32)
    with pytest.raises(ValueError, match="terminal usage is not retained"):
        f.execute("include_usage", f.leaf["work_id"])
    f.rows[0]["usage_sha256"] = original
    f.rows[0]["retention_until"] = datetime.now(UTC) - timedelta(seconds=1)
    assert f.execute("include_binding", f.root["work_id"])["state"] == "cancelled"


def test_original_durable_conversation_stop_blocks_resumed_binding(monkeypatch):
    import pytest
    from eva.application.conversation import ConversationStopped
    f = _resumed_readiness(monkeypatch)
    f.stopped[0] = True
    with pytest.raises(ConversationStopped):
        f.execute("include_binding", f.root["work_id"])


def test_active_resume_still_requires_actual_leaf_dispatch_authority(monkeypatch):
    import pytest
    from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore, DelegatedWorkConflict
    f = _resumed_readiness(monkeypatch, active=True)
    def expired_lease(self, call):
        assert call.work_id == f.leaf["work_id"]
        raise DelegatedWorkConflict("actual leaf lease or deadline expired")
    monkeypatch.setattr(PostgresDelegatedWorkStore, "authorize_tool_dispatch", expired_lease)
    with pytest.raises(DelegatedWorkConflict, match="leaf lease or deadline"):
        f.execute("include_active_usage", f.leaf["work_id"])
    assert f.reads == []
