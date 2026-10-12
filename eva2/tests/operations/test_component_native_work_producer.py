"""Model-free contracts against the ordinary consumer and durable physical fences."""
from copy import deepcopy
from datetime import UTC, datetime, timedelta
import json
import os
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import pytest

from eva.operations import component_native_work_producer as p
from eva.contracts.canonical_json import canonical_sha256


def digest(value):
    return p.sha(value.encode())


@pytest.fixture
def setup(tmp_path):
    tmp_path.chmod(0o700)
    roots = {}
    for name in ("output", "accounting", "workspace", "receipts", "evidence", "bindings"):
        roots[name] = tmp_path / name
        roots[name].mkdir(mode=0o700)
    baseline = b'class _TaskModel:\n    def _work_strategy(self):\n        return "Keep a short plan."\n'
    candidate = baseline.replace(b"Keep a short plan.", b"Record useful progress.")
    release = {key: digest(key) for key in ("worker_profile_release_sha256", "model_profile_release_sha256",
        "worker_program_sha256", "engine_release_sha256", "output_schema_sha256")}
    hashes = {"bootstrap/delegated_digital_runner.py": p.sha(baseline)}
    release["worker_program_sha256"] = canonical_sha256({"contract": "EvaDigitalWorkProgram/v4", "sources": hashes})
    actor = {"tenant_id": "evaluation-tenant", "installation_id": "evaluation-installation",
        "principal_id": "33333333-3333-3333-8333-333333333333",
        "principal_binding_id": "44444444-4444-4444-8444-444444444444"}
    cases = [{"case_id": "one"}]
    cases_file = tmp_path / "cases.json"
    cases_file.write_bytes(p.canonical(cases))
    config = {"evidence_root": str(roots["evidence"]), "run_bindings_root": str(roots["bindings"]),
        "registration_sha256": digest("registration"), "case_set_sha256": p.sha(cases_file.read_bytes()),
        "baseline_sha256": p.sha(baseline), "scope_sha256": canonical_sha256({k: actor[k] for k in ("tenant_id", "installation_id", "principal_id")}),
        "runtime_bindings": {**{k: release[k] for k in release if k != "worker_program_sha256"},
            "tool_configuration_sha256": release["model_profile_release_sha256"]},
        "limits": {"per_case": {"contact_attempts": 0, "worker_and_helper_attempts": 8,
            "input_tokens": 100000, "output_tokens": 100000}, "total_provider_attempts": 42,
            "total_input_tokens": 200000, "total_output_tokens": 200000}}
    assessment = {"work_evidence": config, "cases_file": str(cases_file)}
    case = {"case_id": "one", "task": "Registered isolated work, not an owner question.",
        "project_id": None, "fixture_files": {"input.json": "{}"},
        "checks": [{"check_id": "native-check", "stdin": {"value": 1}}],
        "required_action_names": ["write_programming_file", "start_programming_process"], "case_provider_attempts": 6}
    inputs = {"contract": "EvaRegisteredComponentWorkInputs/v1", "stage": "development",
        **{k: config[k] for k in ("registration_sha256", "case_set_sha256", "scope_sha256")}, "cases": [case]}
    input_file = tmp_path / "inputs.json"
    p.exclusive(input_file, inputs)
    resources_file = tmp_path / "resources.json"
    p.exclusive(resources_file, {"duration": {"per_simple_work_ceiling_seconds": 150,
        "per_multistep_work_ceiling_seconds": 210}, "provider_attempts": {
            "simple_per_episode_including_contact_helpers": 6,
            "multistep_per_episode_including_contact_helpers": 8}})
    client_file = tmp_path / "client.json"
    p.exclusive(client_file, {"preview": True})
    settings = {"contract": "EvaNativeComponentWorkProducerSettings/v1", "runtime": {
        "core_container": "fixed-core", "image_id": "sha256:" + digest("core"),
        "configuration_sha256": digest("config"), "source_hashes": hashes, "release": release,
        "capability_ids": case["required_action_names"], "model_profile_id": "model:conversation:gpt-6-astra",
        "actor": actor, "client_file": str(client_file), "client_sha256": p.sha(client_file.read_bytes())},
        "inputs_file": str(input_file), "inputs_sha256": p.sha(input_file.read_bytes()),
        "resources_file": str(resources_file), "resources_sha256": p.sha(resources_file.read_bytes()),
        "case_deadline_seconds": {"one": 150},
        **{name + "_root": str(roots[key]) for name, key in (("output", "output"), ("accounting", "accounting"), ("workspace", "workspace"), ("receipt", "receipts"))},
        "procedure_execution": {"image": "sha256:" + digest("check-image")},
        "limits": {"total_provider_attempts": 42, "case_provider_attempts": 8, "total_tokens": 200000,
            "total_tool_calls": 70, "retry_attempts": 0}, "parent_deadline": "2099-01-01T00:00:00Z"}
    binding = {"contract": "EvaScopedComponentEvaluationBinding/v1", "task_id": "original-task",
        "draft_step": 1, "evaluation_step": 2, "evaluation_call_id": "original-call", "execution_binding_ref": digest("original-ref")}
    request = SimpleNamespace(relative_path="bootstrap/delegated_digital_runner.py", baseline_sha256=p.sha(baseline), source=candidate.decode())
    policy = p._policy(settings, config, binding, request, [case])
    identity = {**p._case_identity(settings, config, "baseline", case), "source_sha256": p.sha(baseline)}
    return SimpleNamespace(settings=settings, assessment=assessment, binding=binding, request=request,
        policy=policy, identity=identity, case=case, baseline=baseline, candidate=candidate, roots=roots)


def authority(s):
    return {"verified": True, "execution_binding_ref": s.binding["execution_binding_ref"],
        "parent_deadline": s.settings["parent_deadline"]}


def accounting(s, tmp_path):
    root = tmp_path / "ledger"
    root.mkdir(mode=0o700)
    guard = p.PhysicalAccounting(root, s.policy, execution_enabled=True)
    guard.authority_check = lambda: {"authorization_current": True, "source_scope_valid": True, "stop_respected": True}
    return guard


def test_registered_settings_and_input_only_checks(setup):
    s = setup
    assert p.validate_settings(s.settings, s.assessment) == s.settings
    cases, stage = p._cases(s.settings, s.assessment)
    assert cases == [s.case] and stage == "development"
    assert cases[0]["checks"] == [{"check_id": "native-check", "stdin": {"value": 1}}]


@pytest.mark.parametrize("change", ["wrong_duration", "extra_case", "resource_tamper", "wrong_provider_pair"])
def test_fixed_resource_duration_binding_rejects_forged_mapping(setup, change):
    s = setup
    if change == "wrong_duration": s.settings["case_deadline_seconds"]["one"] = 210
    elif change == "extra_case": s.settings["case_deadline_seconds"]["foreign"] = 150
    elif change == "resource_tamper": Path(s.settings["resources_file"]).write_bytes(b"{}")
    else:
        body = json.loads(Path(s.settings["inputs_file"]).read_bytes())
        body["cases"][0]["case_provider_attempts"] = 7
        Path(s.settings["inputs_file"]).write_bytes(p.canonical(body))
        s.settings["inputs_sha256"] = p.sha(Path(s.settings["inputs_file"]).read_bytes())
    with pytest.raises(p.ComponentWorkProductionError):
        p.validate_settings(s.settings, s.assessment)
        p._cases(s.settings, s.assessment)


def test_case_resource_policy_cannot_reset_existing_accounting(setup, tmp_path):
    s = setup
    guard = accounting(s, tmp_path)
    with guard.case(s.identity):
        guard.reserve_model("original", {"model": "gpt-6-astra", "reasoning": {"effort": "medium"}})
        guard.complete_model("original", SimpleNamespace(input_tokens=2, output_tokens=1, total_tokens=3))
    before = (guard.root / "accounting.json").read_bytes()
    changed = deepcopy(s.policy)
    changed["case_deadline_seconds"]["one"] = 210
    rebound = p.PhysicalAccounting(guard.root, changed, execution_enabled=True)
    with pytest.raises(p.ComponentWorkProductionError, match="never reset"):
        rebound.ensure_rankable()
    assert (guard.root / "accounting.json").read_bytes() == before
    assert s.policy["case_deadline_seconds"] == {"one": 150}
    assert s.policy["resources_ref"] == {"path": s.settings["resources_file"], "sha256": s.settings["resources_sha256"]}


@pytest.mark.parametrize("attempts,case_seconds,profile_seconds,parent_seconds,expected_seconds", [
    (6, 150, 1200, 900, 150), (8, 210, 1200, 900, 210),
    (6, 150, 60, 900, 60), (8, 210, 1200, 90, 90)])
def test_admission_preserves_registered_case_profile_and_parent_deadline(setup, monkeypatch,
        attempts, case_seconds, profile_seconds, parent_seconds, expected_seconds):
    from eva.adapters.postgres import conversation_journal as journal_module
    from eva.contracts.timestamps import parse_utc_timestamp
    s = setup
    now = datetime(2026, 10, 12, 12, 0, 0, tzinfo=UTC)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return now
    monkeypatch.setattr(p, "datetime", Clock)
    s.case["case_provider_attempts"] = attempts
    body = json.loads(Path(s.settings["inputs_file"]).read_bytes())
    body["cases"] = [s.case]
    Path(s.settings["inputs_file"]).write_bytes(p.canonical(body))
    s.settings["inputs_sha256"] = p.sha(Path(s.settings["inputs_file"]).read_bytes())
    s.settings["case_deadline_seconds"]["one"] = case_seconds
    assert p._cases(s.settings, s.assessment)[0] == [s.case]
    s.settings["parent_deadline"] = (now + timedelta(seconds=parent_seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")
    policy = p._policy(s.settings, s.assessment["work_evidence"], s.binding, s.request, [s.case])
    original = journal_module.PostgresConversationJournal
    class Journal:
        _ingress_data = staticmethod(original._ingress_data)
        _thread_id = staticmethod(original._thread_id)
        def __init__(self, *args): self._timeline = SimpleNamespace(events=lambda thread: ())
        def admit(self, turn): pass
    monkeypatch.setattr(journal_module, "PostgresConversationJournal", Journal)
    monkeypatch.setattr(p, "_reserve_memory_exclusion", lambda *args: {"disposition": "host_evaluation_excluded"})
    actor = s.settings["runtime"]["actor"]
    service = SimpleNamespace(owner_principal_id=actor["principal_id"], principal_binding_id=actor["principal_binding_id"],
        validate_authority=lambda turn: None, command_key=lambda turn: ":".join((turn.tenant_id,
            turn.installation_id, turn.principal_id, turn.conversation_id, turn.message_id)))
    accepted = []
    def build_spec(**arguments):
        accepted.append(arguments)
        return SimpleNamespace(grant=object(), sha256=digest("admitted-spec"))
    profile = SimpleNamespace(timeout_seconds=profile_seconds, build_spec=build_spec, accepts_grant=lambda grant: True)
    admissions = []
    def admit(**arguments):
        admissions.append(arguments)
        return SimpleNamespace(disposition="inserted")
    config = {"runtime": s.settings["runtime"], "policy": policy, "case_deadline_seconds": case_seconds,
        "parent_deadline": s.settings["parent_deadline"], "execution_binding": s.binding}
    entry = p._NativeEntry(None, None, SimpleNamespace(admit=admit), SimpleNamespace(profile=profile), service, None, config)
    entry.parent_current = lambda: authority(s)
    turn, receipt = entry.admit(s.identity, s.case)
    assert accepted[0]["deadline"] == now + timedelta(seconds=expected_seconds)
    assert accepted[0]["created_at"] == now
    assert accepted[0]["parent_task_id"] == s.binding["task_id"]
    assert accepted[0]["deadline"] <= parse_utc_timestamp(s.settings["parent_deadline"])
    assert turn.occurred_at == "2026-10-12T12:00:00Z" and admissions[0]["occurred_at"] == now


def test_expired_case_refuses_physical_dispatch_before_store_effect(setup, tmp_path, monkeypatch):
    import asyncio
    from eva.operations import delegated_work_worker as worker_module
    s = setup
    guard = accounting(s, tmp_path)
    dispatches = []
    call = SimpleNamespace(work_id=s.identity["task_id"], call_id="native-call",
        spec=SimpleNamespace(deadline="2000-01-01T00:00:00Z"))
    store = SimpleNamespace(start_call=lambda *args, **kwargs: call,
        authorize_tool_dispatch=lambda call: dispatches.append(call))
    class Worker:
        def __init__(self, store, *args, **kwargs): self.store = store
        async def run_once(self):
            self.store.start_call(SimpleNamespace(work_id=s.identity["task_id"]))
            guard.authority_check()
    monkeypatch.setattr(worker_module, "DelegatedWorkWorker", Worker)
    runner = SimpleNamespace(profile=SimpleNamespace(release=object()))
    entry = p._NativeEntry(None, None, store, runner, None, guard, {"execution_binding": s.binding})
    entry.parent_current = lambda: authority(s)
    with pytest.raises(p.ComponentWorkProductionError, match="case deadline expired"):
        asyncio.run(entry.run(s.identity))
    assert dispatches == []


@pytest.mark.parametrize("change", ["fixture_escape", "nonisolated_action", "attempts", "tamper"])
def test_input_binding_rejects_unsafe_or_changed_cases(setup, change):
    s = setup
    path = Path(s.settings["inputs_file"])
    body = json.loads(path.read_bytes())
    case = body["cases"][0]
    if change == "fixture_escape": case["fixture_files"] = {"../other.py": "bad"}
    elif change == "nonisolated_action": case["required_action_names"] = ["save_workspace_document"]
    elif change == "attempts": case["case_provider_attempts"] = 9
    else: case["task"] += " changed"
    path.write_bytes(p.canonical(body))
    if change != "tamper": s.settings["inputs_sha256"] = p.sha(path.read_bytes())
    with pytest.raises(p.ComponentWorkProductionError): p._cases(s.settings, s.assessment)


def test_expired_and_extended_parent_authority_refused(setup):
    s = setup
    with pytest.raises(p.ComponentWorkProductionError, match="expired"):
        p._authority(lambda: authority(s), s.binding, "2000-01-01T00:00:00Z")
    with pytest.raises(p.ComponentWorkProductionError, match="unconfirmed"):
        p._authority(lambda: {**authority(s), "parent_deadline": "2099-01-02T00:00:00Z"}, s.binding, s.settings["parent_deadline"])


def test_provider_and_tool_stop_are_checked_before_reservation(setup, tmp_path):
    s = setup
    guard = accounting(s, tmp_path)
    guard.authority_check = lambda: {"authorization_current": False, "source_scope_valid": True, "stop_respected": False}
    with guard.case(s.identity):
        with pytest.raises(p.ComponentWorkProductionError): guard.reserve_model("no-send", {"model": "gpt-6-astra", "reasoning": {"effort": "medium"}})
        with pytest.raises(p.ComponentWorkProductionError): guard.reserve_tool(SimpleNamespace(name="write_programming_file", call_id="no-write"))
    with guard._ledger() as state:
        assert state["requests"] == state["tools"] == []


def test_unknown_usage_blocks_retries_and_tools_preserving_original(setup, tmp_path):
    s = setup
    guard = accounting(s, tmp_path)
    with guard.case(s.identity):
        guard.start_case_once(s.identity)
        guard.reserve_model("original", {"model": "gpt-6-astra", "reasoning": {"effort": "medium"}})
        with pytest.raises(p.ComponentWorkProductionError, match="unmeasured"):
            guard.reserve_model("retry", {"model": "gpt-6-astra", "reasoning": {"effort": "medium"}})
        with pytest.raises(p.ComponentWorkProductionError, match="unknown"):
            guard.reserve_tool(SimpleNamespace(name="write_programming_file", call_id="retry-tool"))
        with pytest.raises(p.ComponentWorkProductionError, match="already started"): guard.start_case_once(s.identity)
    with guard._ledger() as state:
        assert len(state["requests"]) == 1 and state["requests"][0]["usage"] is None
        assert state["tools"] == []


def test_physical_case_limit_is_registered_not_fixed_four(setup, tmp_path):
    s = setup
    guard = accounting(s, tmp_path)
    with guard.case(s.identity):
        for i in range(6):
            guard.reserve_model(str(i), {"model": "gpt-6-astra", "reasoning": {"effort": "medium"}})
            guard.complete_model(str(i), SimpleNamespace(input_tokens=2, output_tokens=1, total_tokens=3))
        with pytest.raises(p.ComponentWorkProductionError, match="allowance exhausted"):
            guard.reserve_model("seventh", {"model": "gpt-6-astra", "reasoning": {"effort": "medium"}})
    assert len(guard.ensure_rankable()["requests"]) == 6


def test_measured_overshoot_preserved_and_never_renewed(setup, tmp_path):
    s = setup
    s.policy["total_tokens"] = 3
    guard = accounting(s, tmp_path)
    with guard.case(s.identity):
        guard.reserve_model("one", {"model": "gpt-6-astra", "reasoning": {"effort": "medium"}})
        with pytest.raises(p.ComponentWorkProductionError, match="exceeded"):
            guard.complete_model("one", SimpleNamespace(input_tokens=3, output_tokens=2, total_tokens=5))
        with pytest.raises(p.ComponentWorkProductionError, match="token allowance"):
            guard.reserve_model("two", {"model": "gpt-6-astra", "reasoning": {"effort": "medium"}})
    with guard._ledger() as state: assert state["requests"][0]["usage"]["total_tokens"] == 5
    with pytest.raises(p.ComponentWorkProductionError, match="overrun"): guard.ensure_rankable()


def test_empty_memory_is_exact_registered_namespace_and_never_writable(setup):
    s = setup
    repository = p._EmptyEvaluationMemory(s.settings["runtime"]["actor"], s.identity, None)
    query = SimpleNamespace(tenant_id="evaluation-tenant", principal_id=s.settings["runtime"]["actor"]["principal_id"],
        conversation_id=s.identity["conversation_id"], project_id=None)
    assert repository.recall_current(query) == ()
    query.conversation_id = "ordinary-family-conversation"
    with pytest.raises(p.ComponentWorkProductionError): repository.recall_current(query)
    with pytest.raises(p.ComponentWorkProductionError): repository.remember(None)


def test_exclusion_marker_only_inserts_exact_fresh_work_not_projection(setup):
    import sqlalchemy as sa
    from eva.adapters.postgres.operational_timeline import TimelineScope
    s = setup
    statements = []
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def scalar(self, statement): statements.append(statement); return None
        def execute(self, statement, *args): statements.append(statement)
        def in_transaction(self): return True
        def in_nested_transaction(self): return False
    scope = TimelineScope(UUID("11111111-1111-1111-8111-111111111111"), UUID("22222222-2222-2222-8222-222222222222"))
    connection = Connection()
    origin = {"identity": s.identity, "execution_binding_ref": s.binding["execution_binding_ref"]}
    result = p._reserve_memory_exclusion(SimpleNamespace(begin=lambda: connection),
        SimpleNamespace(_scope=lambda c: None, scope=scope), s.identity, origin, datetime.now(UTC).replace(microsecond=0))
    assert result["thread_id"] == "work-memory:" + digest(s.identity["task_id"])
    assert result["disposition"] == "host_evaluation_excluded"
    assert all(isinstance(q, (sa.sql.Select, sa.sql.dml.Insert)) for q in statements)
    insertions = [q.compile().params for q in statements if isinstance(q, sa.sql.dml.Insert)]
    assert len(insertions) == 2
    assert insertions[0]["phase"] == "host_evaluation_excluded"
    assert insertions[1]["event_type"] == "HOST_EVALUATION_MEMORY_EXCLUDED"
    assert "processed" not in json.dumps(insertions, default=str)
    connection.scalar = lambda statement: True
    with pytest.raises(p.ComponentWorkProductionError, match="already exists"):
        p._reserve_memory_exclusion(SimpleNamespace(begin=lambda: connection), SimpleNamespace(_scope=lambda c: None, scope=scope), s.identity, origin, datetime.now(UTC).replace(microsecond=0))


def test_final_stage_cannot_reuse_development_identity_or_authority(setup):
    s = setup
    ordinary = p._case_identity(s.settings, s.assessment["work_evidence"], "baseline", s.case)
    final = p._case_identity(s.settings, s.assessment["work_evidence"], "baseline", s.case, stage="independent_final")
    assert ordinary["task_id"] != final["task_id"] and final["stage"] == "independent_final"
    assert ordinary["source_ingress_sha256"] != final["source_ingress_sha256"]


def test_existing_close_receipt_exact_session_identity_and_boolean(setup):
    s = setup
    raw = {"identity": s.identity, "programming_session_key": "session", "control_receipts": [{
        "identity": s.identity, "session_key": "session", "kind": "close", "result": {"closed": True}}]}
    assert p.original_cleanup_verified(raw)
    raw["control_receipts"][0]["session_key"] = "different"
    assert not p.original_cleanup_verified(raw)
    raw["control_receipts"][0]["session_key"] = "session"
    raw["control_receipts"][0]["result"] = {"closed": 1}
    assert not p.original_cleanup_verified(raw)


def test_partial_check_keeps_original_hash_without_scoreable_exit_stdout():
    report = {"source_sha256": digest("program"), "input_sha256": digest("stdin"),
        "result": {"stdout": "partial", "stderr": "", "exit_code": 0, "timed_out": False,
            "stdout_truncated": True, "stderr_truncated": False, "cancelled": False}}
    observed = p.check_record(report, "check")
    assert observed["status"] == "incomplete" and observed["actual_report_sha256"] == canonical_sha256(report)
    assert "stdout" not in observed and "exit_code" not in observed


def test_wrong_claim_never_starts_unrelated_work():
    started = []
    other = SimpleNamespace(work_id="other")
    store = SimpleNamespace(claim_next=lambda **args: other, start_call=lambda *args, **kwargs: started.append(args))
    wrapper = p.ExactClaimStore(store, "registered")
    with pytest.raises(p.ComponentWorkProductionError, match="unrelated"): wrapper.claim_next()
    with pytest.raises(p.ComponentWorkProductionError, match="differs"): wrapper.start_call(other)
    assert started == []


@pytest.mark.parametrize("stage", ["development", "independent_final"])
def test_baseline_candidate_export_is_consumed_and_missing_result_stays_failed(setup, monkeypatch, stage):
    if os.geteuid() != 0: pytest.skip("ownership-preserving synthetic host flow requires root")
    s = setup
    if stage == "independent_final":
        inputs = json.loads(Path(s.settings["inputs_file"]).read_bytes())
        inputs["stage"] = stage
        Path(s.settings["inputs_file"]).write_bytes(p.canonical(inputs))
        s.settings["inputs_sha256"] = p.sha(Path(s.settings["inputs_file"]).read_bytes())
        s.settings["parent_bindings"] = {"task_id": s.binding["task_id"], "spec_sha256": digest("terminal-spec"), "source_ingress_sha256": digest("parent-ingress")}
        s.settings["runtime_admission_sha256"] = digest("final-admission")
        s.binding["contract"] = "EvaIndependentComponentWorkBinding/v1"
    monkeypatch.setattr(p, "_prepare_source", lambda settings, request: (s.baseline, {}))
    def launch(settings, configuration, source, identity, case, policy, run_root, ledger_root, authorize, binding):
        guard = p.PhysicalAccounting(ledger_root, policy, execution_enabled=True, ledger_uid=65532, host_binding=True)
        guard.authority_check = lambda: {"authorization_current": True, "source_scope_valid": True, "stop_respected": True}
        with guard.case(identity):
            guard.start_case_once(identity)
            request_id = identity["leg"] + ":provider"
            guard.reserve_model(request_id, {"model": "gpt-6-astra", "reasoning": {"effort": "medium"}})
            guard.complete_model(request_id, SimpleNamespace(input_tokens=5, output_tokens=2, total_tokens=7))
            guard.reserve_tool(SimpleNamespace(name="write_programming_file", call_id=identity["leg"] + ":write"), cleanup_session="session")
            close_id = guard.cleanup_dispatch("session")
            guard.observe_programming_finally("session", True)
            with guard._ledger() as ledger:
                requests = [r for r in ledger["requests"] if r["identity"] == identity]
                tools = [r for r in ledger["tools"] if r["identity"] == identity]
        workspace = next(Path(settings["workspace_root"]).iterdir()) if identity["leg"] == "baseline" else sorted(Path(settings["workspace_root"]).iterdir(), key=lambda x:x.stat().st_mtime_ns)[-1]
        p.exclusive(workspace / "main.py", b"import json,sys; print(json.dumps(json.load(sys.stdin)))", uid=10001, raw=True)
        origin = {"execution_binding_ref": binding["execution_binding_ref"], "identity": identity}
        raw = {"identity": identity, "stored_work": {"state": "failed"}, "physical_requests": requests, "physical_tools": tools,
            "journal_usage": {"measurement": "measured", "model_attempts": 1, "input_tokens": 5, "output_tokens": 2},
            "runtime_release": p._selected_runtime(settings, source)["release"], "strategy_projection": {"verified": True},
            "workspace_key": workspace.name, "programming_session_key": "session",
            "action_receipts": [{"identity": identity, "step_index": 1, "receipt": {"call": {
                "name": "write_programming_file", "call_id": identity["leg"] + ":write", "arguments": {}}, "output": {"status": "verified"}}}],
            "control_receipts": [{"identity": identity, "id": close_id, "kind": "close", "session_key": "session", "result": {"closed": True}},
                {"identity": identity, "kind": "programming_finally", "session_key": "session", "started": True}],
            "authorization_checks": [{"identity": identity, "authorization_current": True, "source_scope_valid": True, "stop_respected": True}],
            "terminal_authority": {"authorization_current": True, "source_scope_valid": True, "stop_requested": False},
            "progress": None, "origin": origin, "timing": {"elapsed_seconds": 1.0, "started_monotonic_seconds": time.monotonic() - 1, "boot_id_sha256": p.sha(Path("/proc/sys/kernel/random/boot_id").read_bytes())},
            "memory_disposition": {"disposition": "host_evaluation_excluded", "thread_id": "work-memory:" + digest(identity["task_id"]), "origin_sha256": canonical_sha256(origin)}}
        p.exclusive(run_root / "raw-native.json", raw)
        return raw
    monkeypatch.setattr(p, "_launch", launch)
    from eva.operations.procedure_execution import ProcedureExecutor
    def isolated(self, source, stdin):
        return {"source_sha256": digest(source), "input_sha256": canonical_sha256(stdin),
            "result": {"stdout": json.dumps(stdin), "stderr": "", "exit_code": 0, "timed_out": False,
                "stdout_truncated": False, "stderr_truncated": False, "cancelled": False}}
    monkeypatch.setattr(ProcedureExecutor, "execute", isolated)
    from eva.operations.component_development import _WorkEvidence
    p.produce_work_evidence(leg="baseline", request=s.request, execution_binding=s.binding,
        settings=s.settings, assessment=s.assessment, authorize=lambda: authority(s))
    consumer = _WorkEvidence(s.assessment["work_evidence"], [{"case_id": "one"}], s.assessment["work_evidence"]["case_set_sha256"]) if stage == "development" else None
    p.produce_work_evidence(leg="candidate", request=s.request, execution_binding=s.binding,
        settings=s.settings, assessment=s.assessment, authorize=lambda: authority(s))
    report = {"contract": "EvaIsolatedComponentEvaluation/v1", "relative_path": "bootstrap/delegated_digital_runner.py",
        "installed": False, "semantic_improvement_verified": False, "baseline_sha256": p.sha(s.baseline), "candidate_sha256": p.sha(s.candidate)}
    for leg in ("baseline", "candidate"):
        report[leg] = {"exit_code": 0, "timed_out": False, "stdout_truncated": False, "stderr_truncated": False}
    def scorer(cases, evidence):
        run = evidence["runs"][0]
        assert run["status"] == "failed"
        assert run["artifact_evidence"]["files"]["result"]["status"] == "missing"
        assert run["artifact_evidence"]["checks"][0]["exit_code"] == 0
        return {"completed": 0}
    if consumer is not None:
        result = consumer.assess(report, {}, scorer, [{"case_id": "one"}])
        assert result["baseline"]["complete"] is True and result["candidate"]["complete"] is True
        assert result["baseline"]["metrics"] == result["candidate"]["metrics"] == {"completed": 0}
    else:
        for source in (s.baseline, s.candidate):
            evidence = json.loads((s.roots["evidence"] / (p.sha(source) + ".json")).read_bytes())
            assert evidence["binding"]["stage"] == "independent_final"
            assert evidence["runs"][0]["artifact_evidence"]["meta"]["parent_bindings"] == s.settings["parent_bindings"]
            assert evidence["runs"][0]["artifact_evidence"]["meta"]["runtime_admission_sha256"] == s.settings["runtime_admission_sha256"]
            assert evidence["runs"][0]["status"] == "failed"
    with pytest.raises(FileExistsError):
        p.produce_work_evidence(leg="baseline", request=s.request, execution_binding=s.binding,
            settings=s.settings, assessment=s.assessment, authorize=lambda: authority(s))
    ledger_path = Path(s.settings["accounting_root"]) / s.binding["execution_binding_ref"] / "accounting.json"
    state = json.loads(ledger_path.read_bytes())
    assert len(state["requests"]) == 2 and len(state["tools"]) == 6


def test_host_final_metadata_pinned_without_granting_authority(setup, tmp_path):
    s = setup
    s.settings["parent_bindings"] = {"task_id": "original-task", "spec_sha256": digest("spec"), "source_ingress_sha256": digest("ingress")}
    s.settings["runtime_admission_sha256"] = digest("admission")
    assert p.validate_settings(s.settings, s.assessment) == s.settings
    policy = p._policy(s.settings, s.assessment["work_evidence"], s.binding, s.request, [s.case])
    guard = p.PhysicalAccounting(tmp_path / "pinned", policy, execution_enabled=True)
    guard.start_case_once(s.identity)
    s.settings["runtime_admission_sha256"] = digest("different-admission")
    shifted = p._policy(s.settings, s.assessment["work_evidence"], s.binding, s.request, [s.case])
    with pytest.raises(p.ComponentWorkProductionError, match="never reset"):
        p.PhysicalAccounting(guard.root, shifted, execution_enabled=True).ensure_rankable()
    s.settings["runtime_admission_sha256"] = "unknown"
    with pytest.raises(p.ComponentWorkProductionError, match="admission hash"):
        p.validate_settings(s.settings, s.assessment)
    with pytest.raises(p.ComponentWorkProductionError, match="unconfirmed"):
        p._authority(lambda: {"verified": False}, s.binding, s.settings["parent_deadline"])


def test_existing_finally_close_consumes_reserved_capacity_after_unknown_and_stop(setup, tmp_path, monkeypatch):
    from eva.adapters.postgres.programming_actions import ProgrammingActions
    from eva.adapters.connectors.programming_workspace import ProgrammingWorkspaceClient
    s = setup
    s.policy["total_tool_calls"] = 3
    guard = accounting(s, tmp_path)
    literal = "Keep a short plan."
    guard.native_projection_check = lambda: ({"source_sha256": s.identity["source_sha256"]}, literal)
    closed = []
    monkeypatch.setattr(ProgrammingWorkspaceClient, "close", lambda self, session: closed.append(session) or {"closed": True})
    programming = object.__new__(ProgrammingActions)
    programming.client = object.__new__(ProgrammingWorkspaceClient)
    programming.session_key, programming.started = "session", True
    guard.install()
    try:
        from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions
        isolated = SimpleNamespace(action_names=("save_work_progress",), available_for=lambda t: True, active_context=lambda t: ("own-evaluation",))
        family = SimpleNamespace(action_names=("resolve_reusable_procedure",), available_for=lambda t: True,
            active_context=lambda t: pytest.fail("family/private service context must not be read"))
        catalog = BackgroundConnectorActions((isolated, family), ("save_work_progress", "resolve_reusable_procedure"), lambda t: None, 10)
        assert catalog.available_action_names(None) == ("save_work_progress",)
        assert catalog.active_context(None) == ("own-evaluation",)
        with guard.case(s.identity):
            guard.reserve_tool(SimpleNamespace(name="write_programming_file", call_id="write"), cleanup_session="session")
            with pytest.raises(p.ComponentWorkProductionError, match="allowance"):
                guard.reserve_tool(SimpleNamespace(name="save_work_progress", call_id="no-capacity"))
            guard.reserve_model("unknown", {"model": "gpt-6-astra", "reasoning": {"effort": "medium"}, "input": literal})
            guard.authority_check = lambda: {"authorization_current": False, "source_scope_valid": True, "stop_respected": False}
            programming.close()  # The ordinary method, no new close/status action.
            with pytest.raises(p.ComponentWorkProductionError, match="replayed"):
                programming.close()
        assert closed == ["session"]
        with guard._ledger() as ledger:
            assert ledger["requests"][0]["usage"] is None
            assert len(ledger["tools"]) == 2 and ledger["tools"][-1]["kind"] == "native_cleanup"
            assert ledger["cleanup_reservations"][0]["phase"] == "finalized"
        with pytest.raises(p.ComponentWorkProductionError, match="unknown"):
            guard.ensure_rankable()
        assert p.original_cleanup_verified({"identity": s.identity, "programming_session_key": "session", "control_receipts": guard.control_receipts})
    finally:
        guard.close()


def test_unstarted_programming_reservation_is_not_fake_physical_cleanup(setup, tmp_path):
    s = setup
    guard = accounting(s, tmp_path)
    with guard.case(s.identity):
        guard.reserve_tool(SimpleNamespace(name="list_programming_tools", call_id="invalid-input"), cleanup_session="session")
        guard.observe_programming_finally("session", False)
    state = guard.ensure_rankable()
    assert len(state["tools"]) == 1 and state["cleanup_reservations"][0]["phase"] == "not_started"
    assert not p.original_cleanup_verified({"identity": s.identity, "programming_session_key": "session", "control_receipts": guard.control_receipts})


def test_actual_imported_literal_and_source_program_binding_refuse_stale_projection(setup):
    s = setup
    baseline = p._selected_runtime(s.settings, s.baseline)
    candidate = p._selected_runtime(s.settings, s.candidate)
    assert baseline["release"]["worker_program_sha256"] != candidate["release"]["worker_program_sha256"]
    assert baseline["release"]["worker_profile_release_sha256"] == candidate["release"]["worker_profile_release_sha256"]
    result, literal = p.strategy_projection(s.candidate, lambda _: "Record useful progress.",
        source_sha256=p.sha(s.candidate), literal_sha256=candidate["strategy_sha256"])
    assert literal == "Record useful progress." and result["imported_method_equals_ast_literal"] is True
    with pytest.raises(p.ComponentWorkProductionError, match="actual imported"):
        p.strategy_projection(s.candidate, lambda _: "Keep a short plan.",
            source_sha256=p.sha(s.candidate), literal_sha256=candidate["strategy_sha256"])


def test_changed_immutable_fixture_refused_before_any_isolated_check(setup, monkeypatch):
    if os.geteuid() != 0: pytest.skip("synthetic workspace ownership requires root")
    s = setup
    workspace, _ = p._workspace(s.settings, s.identity, s.case)
    (workspace / "input.json").write_text('{"tampered":true}')
    raw = {"identity": s.identity, "workspace_key": workspace.name, "programming_session_key": "session",
        "control_receipts": [{"identity": s.identity, "kind": "programming_finally", "session_key": "session", "started": False}],
        "timing": {"boot_id_sha256": p.sha(Path("/proc/sys/kernel/random/boot_id").read_bytes())}}
    from eva.operations.procedure_execution import ProcedureExecutor
    monkeypatch.setattr(ProcedureExecutor, "execute", lambda *args: pytest.fail("no check dispatch on changed fixtures"))
    with pytest.raises(p.ComponentWorkProductionError, match="immutable"):
        p._artifacts(s.settings, s.identity, s.case, raw, workspace, s.roots["output"], None, lambda: authority(s), s.binding)
