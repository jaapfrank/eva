"""Produce real work evidence through the existing component evaluation route.

The host owns the producer and fixed suite. A model supplies only its draft;
the scoped adapter supplies the original execution locator. This module does
not create tasks, choose scores or start a second work queue.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import stat
import subprocess
import threading

from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.timestamps import parse_utc_timestamp, format_utc_timestamp
from eva.operations.component_evaluation import (
    CandidateRequest, ComponentEvaluator, WORK_STRATEGY_PATH,
    validate_work_strategy_candidate,
)

_BINDING_FIELDS = {"contract", "task_id", "draft_step", "evaluation_step",
                   "evaluation_call_id", "execution_binding_ref"}


def validate_execution_binding(binding):
    if (not isinstance(binding, dict) or set(binding) != _BINDING_FIELDS
            or binding["contract"] != "EvaScopedComponentEvaluationBinding/v1"
            or not isinstance(binding["task_id"], str)
            or not 1 <= len(binding["task_id"]) <= 192 or "\\x00" in binding["task_id"]
            or not isinstance(binding["evaluation_call_id"], str)
            or not 1 <= len(binding["evaluation_call_id"]) <= 192
            or "\\x00" in binding["evaluation_call_id"]
            or type(binding["draft_step"]) is not int
            or type(binding["evaluation_step"]) is not int
            or not 0 <= binding["draft_step"] < binding["evaluation_step"]
            or not isinstance(binding["execution_binding_ref"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", binding["execution_binding_ref"])):
        raise ValueError("invalid scoped component work binding")
    return deepcopy(binding)


def _verified_work_authority(value, binding):
    fields = {"verified", "execution_binding_ref", "parent_deadline"}
    if (not isinstance(value, dict) or set(value) != fields
            or value["verified"] is not True
            or value["execution_binding_ref"] != binding["execution_binding_ref"]
            or not isinstance(value["parent_deadline"], str)):
        raise ValueError("original component work authority differs")
    deadline = parse_utc_timestamp(value["parent_deadline"])
    if format_utc_timestamp(deadline) != value["parent_deadline"]:
        raise ValueError("original component work deadline differs")
    return deepcopy(value)


def verify_original_execution(binding, *, path, suite_sha256, scope_sha256,
                              baseline_sha256=None, candidate_sha256=None):
    """Run inside the existing core; no credentials or source text leave it."""
    import sqlalchemy as sa
    from datetime import UTC, datetime
    from types import SimpleNamespace
    from uuid import UUID
    import eva
    from eva.bootstrap.runtime import RuntimeSettings
    from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
    from eva.adapters.postgres.current_catalog import delegated_work_runs
    from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore, DelegatedWorkCall
    from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline, TimelineScope, external_timeline_scope
    from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
    from eva.providers.procedure_reference_actions import ProcedureReferenceProviderFactory
    from eva.providers.mcp_autonomy import _SourceTurnLoader
    from eva.application.component_development import make_scoped_component_evaluation_binding
    from eva.application.component_improvements import read_component
    from eva.contracts.timestamps import parse_utc_timestamp

    binding = validate_execution_binding(binding)
    if path != WORK_STRATEGY_PATH:
        raise ValueError("native work producer is limited to the registered strategy component")
    settings = RuntimeSettings.from_environment()
    engine = sa.create_engine(sqlalchemy_postgres_dsn(settings.database_dsn))
    scope = TimelineScope(UUID(str(settings.dashboard_tenant_id)),
                          UUID(str(settings.dashboard_installation_id)))
    store = PostgresDelegatedWorkStore(engine, scope, initiative_authority_loader=lambda:
        ProcedureReferenceProviderFactory.native_authority(engine, settings))
    try:
        with engine.begin() as connection:
            store._scope(connection)
            raw = connection.execute(sa.select(delegated_work_runs.c.spec_payload).where(
                delegated_work_runs.c.work_id == binding["task_id"])).scalar_one_or_none()
        if not isinstance(raw, dict):
            raise ValueError("original component task unavailable")
        identity = {key: raw["source"][key] for key in (
            "tenant_id", "installation_id", "principal_id")}
        if canonical_sha256(identity) != scope_sha256:
            raise ValueError("original component source scope differs")
        loaded = store.load(binding["task_id"],
            owner_principal_id=UUID(raw["owner_principal_id"]),
            principal_binding_id=UUID(raw["principal_binding_id"]))
        spec = loaded.spec
        if (loaded.state != "call_started" or not loaded.call_id
                or loaded.stop_requested_at is not None
                or loaded.retention_until <= datetime.now(UTC)
                or parse_utc_timestamp(spec.deadline) <= datetime.now(UTC)):
            raise ValueError("original component execution is not current")
        call = DelegatedWorkCall(spec.work_id, loaded.storage_episode_id, loaded.call_id, spec)
        store.authorize_tool_dispatch(call)
        native = store._native_guard(spec)
        if native is not None:
            turn = native.turn
            native.check(turn)
        else:
            authority = SimpleNamespace(scope=external_timeline_scope(
                settings.background_tenant_id, settings.background_installation_id,
                "component work ingress scope required"),
                authorize_turn=lambda turn: store.authorize_tool_dispatch(call))
            turn = _SourceTurnLoader(engine, authority)(spec.source.model_dump(mode="json"))
        if canonical_sha256(PostgresConversationJournal._ingress_data(turn)) != spec.source_ingress_sha256:
            raise ValueError("original component ingress differs")
        timeline = PostgresOperationalTimeline(engine, external_timeline_scope(
            turn.tenant_id, turn.installation_id, "component draft scope required"))
        draft_thread = "component-draft:" + canonical_sha256({
            "execution": spec.execution_id, "source": spec.source.model_dump(mode="json"),
            "step": binding["draft_step"]})
        timeline.load(draft_thread)
        events = timeline.events(draft_thread)
        if not events:
            raise ValueError("original component draft unavailable")
        event = events[0]
        data = event.payload["data"]
        if (data.get("name") != "draft_own_component_improvement"
                or data.get("output", {}).get("status") != "verified"):
            raise ValueError("original component draft is not verified")
        draft = data["output"]["operation_result"]
        offered = (baseline_sha256, candidate_sha256)
        if ((baseline_sha256 is None) != (candidate_sha256 is None)
                or any(value is not None and (
                    not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value))
                    for value in offered)
                or baseline_sha256 is not None and offered != (
                    draft["source_sha256"], draft["candidate_sha256"])
                or hashlib.sha256(draft["candidate_content"].encode()).hexdigest()
                    != draft["candidate_sha256"]):
            raise ValueError("offered component differs from the original draft")
        current = read_component(Path(eva.__file__).resolve().parent, path)
        if (draft.get("path") != path or draft.get("source_target") is not None
                or draft["source_sha256"] != current["source_sha256"]):
            raise ValueError("installed component draft baseline differs")
        # Literal-only validation retains the existing candidate boundary.
        baseline = (Path(eva.__file__).resolve().parent / path).read_bytes()
        validate_work_strategy_candidate(baseline, draft["candidate_content"].encode())
        payload_hash = event.payload_sha256
        if isinstance(payload_hash, bytes):
            payload_hash = payload_hash.hex()
        expected = make_scoped_component_evaluation_binding(
            task_id=spec.work_id, draft_step=binding["draft_step"],
            evaluation_step=binding["evaluation_step"],
            evaluation_call_id=binding["evaluation_call_id"],
            spec_sha256=spec.sha256, source_ingress_sha256=spec.source_ingress_sha256,
            draft_thread_id=draft_thread, draft_event_payload_sha256=payload_hash,
            path=path, baseline_sha256=draft["source_sha256"],
            candidate_sha256=draft["candidate_sha256"], suite_sha256=suite_sha256)
        if binding != expected:
            raise ValueError("original component execution binding differs")
        store.authorize_tool_dispatch(call)
        if native is not None:
            native.check(turn)
        return {"verified": True, "execution_binding_ref": binding["execution_binding_ref"],
                "parent_deadline": spec.deadline}
    finally:
        engine.dispose()


class ComponentWorkRuntime:
    """Read current authority in the installed core, using its existing store."""

    def __init__(self, runtime_config, *, path, suite_sha256, run=subprocess.run):
        from eva.operations.component_application_runtime import ComponentApplicationRuntime
        checked = ComponentApplicationRuntime(runtime_config)
        if path != WORK_STRATEGY_PATH or not re.fullmatch(r"[0-9a-f]{64}", suite_sha256):
            raise ValueError("invalid registered work strategy runtime")
        self.config, self.path, self.suite_sha256, self.run = checked.config, path, suite_sha256, run

    def verify(self, binding, *, baseline_sha256=None, candidate_sha256=None):
        binding = validate_execution_binding(binding)
        request = {"binding": binding, "path": self.path, "suite_sha256": self.suite_sha256,
                   "scope_sha256": self.config["scope_sha256"],
                   "baseline_sha256": baseline_sha256, "candidate_sha256": candidate_sha256}
        program = (
            "import json,sys\n"
            "from eva.operations.component_work_execution import verify_original_execution\n"
            "x=json.loads(sys.argv[1])\n"
            "print(json.dumps(verify_original_execution(x.pop('binding'),**x)))\n"
        )
        result = self.run(["docker", "exec", "-i", self.config["core_container"],
                          "python", "-", json.dumps(request)], input=program,
                          text=True, capture_output=True, timeout=30)
        if result.returncode != 0 or len(result.stdout) > 1024:
            raise ValueError("original component work authority unavailable")
        return _verified_work_authority(json.loads(result.stdout), binding)


    def active_work_usage(self, task_id):
        from eva.operations.component_application_runtime import ComponentApplicationRuntime
        return ComponentApplicationRuntime(self.config, run=self.run).active_work_usage(task_id)


def _construction_remaining_settings(settings, usage):
    """The paused constructor has already spent part of the same work budget."""
    from eva.application.conversation import ConversationModelUsage
    model = ConversationModelUsage.from_wire(usage['model_usage'])
    if model.measurement != 'measured' or model.model_attempts < 1:
        raise ValueError('unmeasured construction prevents development work')
    contact = usage.get('contact_usage')
    if contact is None:
        if usage.get('contact_disposition') not in {
                'native_origin_without_foreground', 'registered_technical_origin_without_foreground'}:
            raise ValueError('original contact accounting absent')
        counts = (model,)
    else:
        contact = ConversationModelUsage.from_wire(contact)
        if contact.measurement != 'measured':
            raise ValueError('original contact accounting incomplete')
        counts = (model, contact)
    result = deepcopy(settings)
    limits = result['limits']
    limits['total_tool_calls'] -= sum(v.tool_calls for v in counts)
    limits['total_tokens'] -= sum(v.input_tokens + v.output_tokens for v in counts)
    if limits['total_tool_calls'] < 1 or limits['total_tokens'] < 1:
        raise ValueError('shared construction/development budget exhausted')
    return result


def _private_source(path, expected_sha256):
    path = Path(path)
    metadata = path.lstat()
    if (not path.is_absolute() or path.resolve() != path or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 0 or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) not in {0o400, 0o600}
            or metadata.st_size > 1_048_576):
        raise ValueError("fixed host work producer source required")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise ValueError("fixed host work producer source changed")
    return path


def _registered_producer_source(config, assessment, *, producer=None):
    fields = {"producer_file", "producer_sha256", "settings"}
    if (not isinstance(config, dict) or not fields <= set(config)
            or set(config) - fields - {"minimum_remaining_seconds", "baseline_maximum"}
            or not isinstance(config["settings"], dict)
            or not isinstance(config["producer_sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", config["producer_sha256"])
            or not isinstance(assessment, dict) or "work_evidence" not in assessment):
        raise ValueError("fixed native work producer configuration differs")
    source = _private_source(config["producer_file"], config["producer_sha256"])
    if producer is None:
        module_spec = importlib.util.spec_from_file_location("eva_registered_work_producer", source)
        module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(module)
        producer = module.produce_work_evidence
    if not callable(producer):
        raise ValueError("fixed host producer entry unavailable")
    return source, producer


class NativeComponentWorkProducer:
    """Use a pinned host producer, then the existing sealed-evidence consumer."""

    def __init__(self, config, *, runtime, assessment, producer=None):
        self.config, self.assessment = deepcopy(config), deepcopy(assessment)
        self.runtime, self.lock = runtime, threading.Lock()
        self.source, self.produce = _registered_producer_source(config, assessment, producer=producer)

    def authorize(self, binding, *, request=None):
        _private_source(self.source, self.config["producer_sha256"])
        offered = {} if request is None else {
            "baseline_sha256": request.baseline_sha256,
            "candidate_sha256": hashlib.sha256(request.source.encode()).hexdigest()}
        return _verified_work_authority(self.runtime.verify(binding, **offered), binding)

    def _check_shared_usage(self, binding, settings, construction_usage, *, allow_unfinished=False):
        resources = json.loads(_private_source(
            settings["resources_file"], settings["resources_sha256"]).read_bytes())
        registration = self.assessment["work_evidence"]["registration_sha256"]
        root = Path(settings["accounting_root"])
        if not root.is_absolute() or root.resolve() != root:
            raise ValueError("registered accounting root differs")
        child, _ = _ledger_usage(
            root / binding["execution_binding_ref"] / "accounting.json", registration,
            allow_absent=allow_unfinished, allow_inflight=allow_unfinished)
        input_tokens, output_tokens, tools = child["input_tokens"], child["output_tokens"], child["physical_tools"]
        for parent in (construction_usage["model_usage"], construction_usage.get("contact_usage")):
            if parent is not None:
                input_tokens += parent["input_tokens"]
                output_tokens += parent["output_tokens"]
                tools += parent["tool_calls"]
        limits = resources["tokens"]
        token_totals = (input_tokens, output_tokens, input_tokens + output_tokens)
        token_limits = (limits["shared_input"], limits["shared_output"], limits["shared_combined"])
        exhausted = any(actual >= ceiling if allow_unfinished else actual > ceiling
                        for actual, ceiling in zip(token_totals, token_limits))
        if exhausted or tools > resources["tools"]["shared_physical_total"]:
            raise ValueError("registered construction and child budget exceeded")

    def _work_usage(self, binding, settings):
        root = Path(settings["accounting_root"])
        if not root.is_absolute() or root.resolve() != root:
            raise ValueError("registered accounting root differs")
        registration = self.assessment["work_evidence"]["registration_sha256"]
        totals, receipt = _ledger_usage(
            root / binding["execution_binding_ref"] / "accounting.json", registration)
        return {"contract": "EvaDevelopmentWorkUsage/v1",
                "registration_sha256": registration,
                "execution_binding_ref": binding["execution_binding_ref"],
                "totals": totals, "receipt": receipt}

    def evaluate(self, request, *, execution_binding, evaluator, static_lock):
        from eva.operations.component_development import DevelopmentAssessment
        binding = validate_execution_binding(execution_binding)
        if (request.relative_path != WORK_STRATEGY_PATH or request.source_target is not None
                or request.baseline_sha256 != self.assessment["work_evidence"]["baseline_sha256"]):
            raise ValueError("native component suite binding differs")
        if not self.lock.acquire(blocking=False):
            raise ValueError("native component work is already running")
        try:
            authority = self.authorize(binding, request=request)
            from datetime import UTC, datetime
            minimum = self.config.get("minimum_remaining_seconds", 0)
            if type(minimum) is not int or minimum < 0:
                raise ValueError("registered preparation minimum differs")
            def require_remaining():
                if (parse_utc_timestamp(authority["parent_deadline"]) - datetime.now(UTC)).total_seconds() < minimum:
                    raise ValueError("original task has insufficient registered work time")
            require_remaining()
            settings = deepcopy(self.config["settings"])
            # Only the original core task supplies this bound; operator settings
            # and model draft arguments cannot extend it.
            settings["parent_deadline"] = authority["parent_deadline"]
            # Static isolation is still serialized, but real work must be free to
            # call /run-procedure without contending for this same lock.
            with static_lock:
                report = ComponentEvaluator(evaluator.config).evaluate(request)
            expected_suite = hashlib.sha256(json.dumps([
                report["image_id"], report["command_sha256"],
                evaluator.assessment.assessment_sha256], separators=(",", ":")).encode()).hexdigest()
            if expected_suite != self.runtime.suite_sha256:
                raise ValueError("registered native work suite changed")
            for outcome in (report["baseline"], report["candidate"]):
                if (type(outcome.get("exit_code")) is not int or outcome["exit_code"] != 0
                        or any(outcome.get(key) is not False for key in (
                            "timed_out", "stdout_truncated", "stderr_truncated"))):
                    raise ValueError("static component checks did not complete")
            # Do not spend work requests on altered cases, scoring or criteria.
            for path, digest in evaluator.assessment.paths.values():
                if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                    raise ValueError("fixed development assessment changed")
            preparation = DevelopmentAssessment(self.assessment)
            if preparation.assessment_sha256 != evaluator.assessment.assessment_sha256:
                raise ValueError("fixed native work assessment binding changed")
            self.authorize(binding, request=request)
            require_remaining()
            construction_usage = self.runtime.active_work_usage(binding['task_id'])
            settings = _construction_remaining_settings(settings, construction_usage)
            def authorizer():
                current = self.authorize(binding, request=request)
                if current["parent_deadline"] != authority["parent_deadline"]:
                    raise ValueError("original component work deadline changed")
                if self.runtime.active_work_usage(binding['task_id']) != construction_usage:
                    raise ValueError('construction changed during development work')
                self._check_shared_usage(binding, settings, construction_usage, allow_unfinished=True)
                return current
            baseline = self.produce(
                leg="baseline", request=request, execution_binding=deepcopy(binding),
                settings=deepcopy(settings),
                assessment=deepcopy(self.assessment), authorize=authorizer)
            if baseline is not None:
                raise ValueError("producer must seal evidence, not supply scores")
            # A new consumer pins the completed baseline BEFORE candidate work.
            assessor = DevelopmentAssessment(self.assessment)
            if assessor.assessment_sha256 != evaluator.assessment.assessment_sha256:
                raise ValueError("fixed native work assessment binding changed")
            if assessor.work_evidence.baseline_error is not None:
                raise ValueError("baseline work evidence is not sealed")
            baseline_score = assessor.assess(report)
            maximum = self.config.get("baseline_maximum")
            if maximum is not None:
                if (not isinstance(maximum, dict) or not maximum
                        or any(type(v) is not int or v < 0 for v in maximum.values())):
                    raise ValueError("registered baseline maximum differs")
                scored = baseline_score["baseline"]
                if scored["complete"] is True and all(scored["metrics"].get(k) == v for k,v in maximum.items()):
                    report["development_assessment"] = baseline_score
                    report["execution_binding_ref"] = binding["execution_binding_ref"]
                    report["candidate_work_not_run"] = "baseline_quality_maximum"
                    self._check_shared_usage(binding, settings, construction_usage)
                    report["work_usage"] = self._work_usage(binding, settings)
                    return report
            self.authorize(binding, request=request)
            candidate = self.produce(
                leg="candidate", request=request, execution_binding=deepcopy(binding),
                settings=deepcopy(settings),
                assessment=deepcopy(self.assessment), authorize=authorizer)
            if candidate is not None:
                raise ValueError("producer must seal evidence, not supply scores")
            self.authorize(binding, request=request)
            report["development_assessment"] = assessor.assess(report)
            report["execution_binding_ref"] = binding["execution_binding_ref"]
            self._check_shared_usage(binding, settings, construction_usage)
            report["work_usage"] = self._work_usage(binding, settings)
            return report
        finally:
            self.lock.release()


_FINAL_BINDING_FIELDS = {"contract", "task_id", "review_id", "registration_sha256",
                         "candidate_package_sha256", "execution_binding_ref"}


def validate_final_work_binding(value):
    if (not isinstance(value, dict) or set(value) != _FINAL_BINDING_FIELDS
            or value.get("contract") != "EvaIndependentComponentWorkBinding/v1"
            or not isinstance(value.get("task_id"), str)
            or not 1 <= len(value["task_id"]) <= 192 or "\x00" in value["task_id"]
            or any(not isinstance(value[k], str) or not re.fullmatch(r"[0-9a-f]{64}", value[k])
                   for k in ("review_id", "registration_sha256", "candidate_package_sha256",
                             "execution_binding_ref"))
            or value["review_id"] != value["candidate_package_sha256"]
            or value["execution_binding_ref"] != canonical_sha256(
                {k:v for k,v in value.items() if k != "execution_binding_ref"})):
        raise ValueError("independent component work binding differs")
    return deepcopy(value)


def _ledger_usage(path, registration_sha256, *, allow_absent=False, allow_inflight=False):
    """Read retained physical counters; unknown dispatches are never zero."""
    if allow_absent and not path.exists():
        return {"provider_attempts":0, "physical_tools":0, "input_tokens":0,
                "output_tokens":0, "unknown_attempts":0, "retries":0}, None
    metadata = path.lstat()
    if (path.resolve() != path or not stat.S_ISREG(metadata.st_mode)
            or metadata.st_uid != 65532 or metadata.st_nlink != 1
            or stat.S_IMODE(metadata.st_mode) != 0o600 or metadata.st_size > 16*1024*1024):
        raise ValueError("registered physical accounting unavailable")
    raw = path.read_bytes()
    value = json.loads(raw)
    if (value.get("registration_sha256") != registration_sha256
            or not isinstance(value.get("requests"), list) or not isinstance(value.get("tools"), list)):
        raise ValueError("registered physical accounting differs")
    totals = {"provider_attempts":len(value["requests"]), "physical_tools":len(value["tools"]),
              "input_tokens":0, "output_tokens":0, "unknown_attempts":0, "retries":0}
    seen = set()
    for row in value["requests"]:
        identifier = row.get("request_id")
        usage = row.get("usage")
        if not isinstance(identifier,str) or not identifier or identifier in seen:
            raise ValueError("physical accounting request identity differs")
        seen.add(identifier)
        if usage is None and allow_inflight:
            totals["unknown_attempts"] += 1
            if totals["unknown_attempts"] > 1:
                raise ValueError("more than one unfinished physical request")
            continue
        if (not isinstance(usage,dict) or set(usage) != {"input_tokens","output_tokens","total_tokens"}
                or any(type(v) is not int or v < 0 for v in usage.values())
                or usage["total_tokens"] != usage["input_tokens"]+usage["output_tokens"]):
            raise ValueError("unmeasured physical accounting prevents continuation")
        totals["input_tokens"] += usage["input_tokens"]
        totals["output_tokens"] += usage["output_tokens"]
    return totals, {"path":str(path),"sha256":hashlib.sha256(raw).hexdigest()}


class IndependentComponentWorkProducer:
    """One registered answer stage after the original development job is terminal."""
    def __init__(self, config, *, runtime, registration_sha256, producer=None):
        fields = {"producer","assessment","deadline_seconds","shared_budget","criteria","runtime_admission"}
        budget_fields = {"provider_attempts","physical_tools","input_tokens","output_tokens",
                         "construction_attempts","later_attempts","final_attempts"}
        if (not isinstance(config,dict) or set(config) != fields
                or type(config["deadline_seconds"]) is not int or config["deadline_seconds"] < 1
                or config["assessment"]["work_evidence"]["registration_sha256"] != registration_sha256
                or not isinstance(config["shared_budget"],dict)
                or set(config["shared_budget"]) != budget_fields
                or any(type(v) is not int or v < 1 for v in config["shared_budget"].values())):
            raise ValueError("registered independent work configuration differs")
        self.config, self.runtime = deepcopy(config), runtime
        self.registration_sha256 = registration_sha256
        self.source, self.produce = _registered_producer_source(
            config["producer"],config["assessment"],producer=producer)
        self.active = None
        self._metadata()

    def _metadata(self):
        from eva.operations.reviewed_component_application import _artifact
        _,criteria = _artifact(self.config["criteria"])
        _,admission = _artifact(self.config["runtime_admission"])
        if (criteria != self.config["assessment"]["criteria"]
                or admission.get("contract") != "EvaWorkerStrategyProductionRuntimeAdmission/v1"
                or admission.get("status") != "bound"
                or admission.get("registration_sha256") != self.registration_sha256
                or admission.get("runtime_bindings") != self.config["assessment"]["work_evidence"]["runtime_bindings"]):
            raise ValueError("fixed independent work metadata changed")

    def _usage(self, value, *, final_may_be_absent, allow_inflight=False):
        settings = self.config["producer"]["settings"]
        root = Path(settings["accounting_root"])
        if not root.is_absolute() or root.resolve() != root:
            raise ValueError("registered accounting root differs")
        development, dev_ref = _ledger_usage(root/value["development_ref"]/"accounting.json",
                                             self.registration_sha256)
        final, final_ref = _ledger_usage(root/value["binding"]["execution_binding_ref"]/"accounting.json",
                                        self.registration_sha256,allow_absent=final_may_be_absent,allow_inflight=allow_inflight)
        parent = self.runtime.reviewed_work_usage(value["binding"]["task_id"])
        if parent != value["parent_usage"]:
            raise ValueError("terminal construction accounting changed")
        model, terminal = parent["model_usage"], parent["terminal_usage"]
        if model["measurement"] != "measured" or terminal["measurement"] == "unmeasured":
            raise ValueError("unmeasured construction prevents final work")
        construction = {"provider_attempts":model["model_attempts"],
            "physical_tools":max(model["tool_calls"],terminal["tool_calls"]),
            "input_tokens":model["input_tokens"],"output_tokens":model["output_tokens"],
            "unknown_attempts":0,"retries":0}
        if any(type(v) is not int or v < 0 for v in construction.values()):
            raise ValueError("construction accounting incomplete")
        contact = parent.get("contact_usage")
        if contact is not None:
            if contact.get("measurement") != "measured":
                raise ValueError("original contact usage is incomplete")
            for target,source in (("provider_attempts","model_attempts"),("physical_tools","tool_calls"),
                                  ("input_tokens","input_tokens"),("output_tokens","output_tokens")):
                if type(contact.get(source)) is not int or contact[source] < 0:
                    raise ValueError("original contact usage is incomplete")
                construction[target] += contact[source]
        elif parent.get("contact_disposition") not in {
                "native_origin_without_foreground", "registered_technical_origin_without_foreground"}:
            raise ValueError("original contact accounting absent")
        budget = self.config["shared_budget"]
        if construction["provider_attempts"] > budget["construction_attempts"]:
            raise ValueError("registered construction allowance exceeded")
        total = {k:development[k]+construction[k]+final[k] for k in development}
        if (any(total[k] > budget[k] for k in
                ("provider_attempts","physical_tools","input_tokens","output_tokens"))
                or final["provider_attempts"] > budget["final_attempts"]):
            raise ValueError("registered cross-stage budget exceeded")
        return {"contract":"EvaComponentWorkSharedAccounting/v1",
                "registration_sha256":self.registration_sha256,
                "construction":construction,"development":development,"independent_final":final,
                "total":total,"development_receipt":dev_ref,"final_receipt":final_ref,
                "parent_usage_sha256":canonical_sha256(parent),
                "limits":deepcopy(budget)}

    def activate(self, package, *, task_id, review_id, output_root, job_snapshot):
        from datetime import UTC,datetime,timedelta
        from eva.operations.component_final_assessment import validate_selected_package
        from eva.operations.reviewed_component_application import _artifact,_save
        validate_selected_package(package)
        self._metadata()
        if (package["path"] != WORK_STRATEGY_PATH or package.get("source_target") is not None
                or package["candidate_package_sha256"] != review_id
                or package["baseline_sha256"] != self.config["assessment"]["work_evidence"]["baseline_sha256"]
                or self.active is not None):
            raise ValueError("independent package is not a fresh registered selection")
        _, job = _artifact(job_snapshot)
        if (job.get("state") != "answer_assessing" or job.get("task_id") != task_id
                or job.get("review_id") != review_id or job.get("protocol_sha256") != self.registration_sha256
                or job.get("path") != package["path"]):
            raise ValueError("original independent job binding differs")
        _, queued = _artifact(job["package"])
        if queued != package:
            raise ValueError("independent job package differs")
        original = self.runtime.reviewed_work_binding(task_id)
        if original["state"] != "ready":
            raise ValueError("original work is not completed and current")
        development_ref = package["evaluation"].get("execution_binding_ref")
        if not isinstance(development_ref,str) or not re.fullmatch(r"[0-9a-f]{64}",development_ref):
            raise ValueError("selected development accounting binding absent")
        binding = {"contract":"EvaIndependentComponentWorkBinding/v1","task_id":task_id,
                   "review_id":review_id,"registration_sha256":self.registration_sha256,
                   "candidate_package_sha256":package["candidate_package_sha256"]}
        binding["execution_binding_ref"] = canonical_sha256(binding)
        validate_final_work_binding(binding)
        value = {"contract":"EvaIndependentComponentWorkAdmission/v1","binding":binding,
            "original_work":original,"parent_usage":self.runtime.reviewed_work_usage(task_id),
            "development_ref":development_ref,"job_snapshot":deepcopy(job_snapshot),
            "parent_deadline":format_utc_timestamp(datetime.now(UTC).replace(microsecond=0)
                +timedelta(seconds=self.config["deadline_seconds"])),
            "package_sha256":package["candidate_package_sha256"],
            "producer_sha256":self.config["producer"]["producer_sha256"],
            "registration_sha256":self.registration_sha256}
        initial_usage = self._usage(value,final_may_be_absent=True)
        if initial_usage["independent_final"]["provider_attempts"] != 0:
            raise ValueError("independent stage was already consumed")
        total,budget = initial_usage["total"],self.config["shared_budget"]
        remaining = budget["provider_attempts"]-total["provider_attempts"]-budget["later_attempts"]
        tools = budget["physical_tools"]-total["physical_tools"]
        tokens = budget["input_tokens"]+budget["output_tokens"]-total["input_tokens"]-total["output_tokens"]
        if min(remaining,tools,tokens) < 1:
            raise ValueError("registered shared resources exhausted before final")
        value["remaining_limits"] = {**self.config["producer"]["settings"]["limits"],
            "total_provider_attempts":min(remaining,budget["final_attempts"]),
            "total_tool_calls":tools,"total_tokens":tokens}
        _save(output_root/"work-admission.private.json",value,initial=True)
        self.active = (value,deepcopy(package),output_root)

    def authorize(self, binding):
        from datetime import UTC,datetime
        from eva.operations.reviewed_component_application import _artifact
        binding = validate_final_work_binding(binding)
        if self.active is None:
            raise ValueError("independent answer work not activated")
        value,package,output_root = self.active
        self._metadata()
        _private_source(self.source,self.config["producer"]["producer_sha256"])
        admission = output_root/"work-admission.private.json"
        _,retained = _artifact({"path":str(admission),"sha256":hashlib.sha256(admission.read_bytes()).hexdigest()})
        _,job = _artifact(value["job_snapshot"])
        _,queued = _artifact(job["package"])
        if (binding != value["binding"] or retained != value or queued != package
                or datetime.now(UTC) >= parse_utc_timestamp(value["parent_deadline"])
                or self.runtime.reviewed_work_binding(binding["task_id"]) != value["original_work"]):
            raise ValueError("independent original work authority not current")
        self._usage(value,final_may_be_absent=True,allow_inflight=True)
        return {"verified":True,"execution_binding_ref":binding["execution_binding_ref"],
                "parent_deadline":value["parent_deadline"]}

    def execute(self, package, *, answer_evidence_file):
        from eva.operations.component_development import DevelopmentAssessment
        from eva.operations.reviewed_component_application import _save
        if self.active is None or package != self.active[1] or answer_evidence_file.exists():
            raise ValueError("independent work not the fresh activated package")
        value,_,output_root = self.active
        binding = value["binding"]
        assessment = deepcopy(self.config["assessment"])
        settings = deepcopy(self.config["producer"]["settings"])
        settings["parent_deadline"] = value["parent_deadline"]
        settings["limits"] = deepcopy(value["remaining_limits"])
        parent = {k:value["original_work"][k] for k in ("task_id","spec_sha256","source_ingress_sha256")}
        settings["parent_bindings"] = parent
        settings["runtime_admission_sha256"] = self.config["runtime_admission"]["sha256"]
        authorizer = lambda:self.authorize(binding)
        request = CandidateRequest(package["path"],package["baseline_sha256"],package["candidate_content"])
        initial = DevelopmentAssessment(assessment)
        for leg in ("baseline","candidate"):
            authorizer()
            if leg == "candidate":
                sealed = DevelopmentAssessment(assessment)
                if sealed.assessment_sha256 != initial.assessment_sha256 or sealed.work_evidence.baseline_error is not None:
                    raise ValueError("independent baseline work not sealed")
            result = self.produce(leg=leg,request=request,execution_binding=deepcopy(binding),
                settings=deepcopy(settings),assessment=deepcopy(assessment),authorize=authorizer)
            if result is not None:
                raise ValueError("independent producer may seal evidence only")
        authorizer()
        final = DevelopmentAssessment(assessment)
        if final.assessment_sha256 != initial.assessment_sha256:
            raise ValueError("independent work assessment changed")
        usage = self._usage(value,final_may_be_absent=False)
        accounting_file = output_root/"shared-accounting.private.json"
        _save(accounting_file,usage,initial=True)
        authority_file = output_root/"work-authority.private.json"
        proof = {"contract":"EvaWorkerStrategyFinalAuthorityProof/v1",
            "registration_sha256":self.registration_sha256,"candidate_sha256":package["candidate_sha256"],
            "package":load_job_package(value["job_snapshot"]),
            "queued_job_snapshot":value["job_snapshot"],"parent_bindings":parent,
            **{k:True for k in ("source_retained","owner_binding_current","mandate_current",
                "stop_respected","postcompletion_authority_current","shared_accounting_complete")}}
        _save(authority_file,proof,initial=True)
        work = assessment["work_evidence"]
        def references(source):
            refs = {}
            for key,rootkey in (("evidence","evidence_root"),("run_bindings","run_bindings_root")):
                filename = Path(work[rootkey])/(source+".json")
                refs[key] = {"path":str(filename),"sha256":hashlib.sha256(filename.read_bytes()).hexdigest()}
            return refs
        evidence = {"contract":"EvaWorkerStrategyIndependentAnswerEvidence/v1",
            "binding":{"registration_sha256":self.registration_sha256,
                "case_set_sha256":work["case_set_sha256"],
                "criteria_sha256":self.config["criteria"]["sha256"],
                "runtime_admission_sha256":self.config["runtime_admission"]["sha256"],
                "baseline_sha256":package["baseline_sha256"],"candidate_sha256":package["candidate_sha256"],
                "scope_sha256":work["scope_sha256"]},
            "baseline":references(package["baseline_sha256"]),"candidate":references(package["candidate_sha256"]),
            "authority_proof":{"path":str(authority_file),"sha256":canonical_sha256(proof)},
            "shared_usage":{**usage["total"],"accounting_receipt_sha256":canonical_sha256(usage)}}
        _save(answer_evidence_file,evidence,initial=True)


def load_job_package(snapshot):
    from eva.operations.reviewed_component_application import _artifact
    return deepcopy(_artifact(snapshot)[1]["package"])
