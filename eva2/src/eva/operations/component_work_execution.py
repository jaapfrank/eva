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
        return {"verified": True, "execution_binding_ref": binding["execution_binding_ref"]}
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
        value = json.loads(result.stdout)
        if value != {"verified": True, "execution_binding_ref": binding["execution_binding_ref"]}:
            raise ValueError("original component work authority differs")
        return value


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


class NativeComponentWorkProducer:
    """Use a pinned host producer, then the existing sealed-evidence consumer."""

    def __init__(self, config, *, runtime, assessment, producer=None):
        fields = {"producer_file", "producer_sha256", "settings"}
        if (not isinstance(config, dict) or set(config) != fields
                or not isinstance(config["settings"], dict)
                or not isinstance(config["producer_sha256"], str)
                or not re.fullmatch(r"[0-9a-f]{64}", config["producer_sha256"])
                or not isinstance(assessment, dict) or "work_evidence" not in assessment):
            raise ValueError("fixed native work producer configuration differs")
        self.config, self.assessment = deepcopy(config), deepcopy(assessment)
        self.runtime, self.lock = runtime, threading.Lock()
        self.source = _private_source(config["producer_file"], config["producer_sha256"])
        if producer is None:
            module_spec = importlib.util.spec_from_file_location("eva_registered_work_producer", self.source)
            module = importlib.util.module_from_spec(module_spec)
            module_spec.loader.exec_module(module)
            producer = module.produce_work_evidence
        if not callable(producer):
            raise ValueError("fixed host producer entry unavailable")
        self.produce = producer

    def authorize(self, binding, *, request=None):
        _private_source(self.source, self.config["producer_sha256"])
        offered = {} if request is None else {
            "baseline_sha256": request.baseline_sha256,
            "candidate_sha256": hashlib.sha256(request.source.encode()).hexdigest()}
        return self.runtime.verify(binding, **offered)

    def evaluate(self, request, *, execution_binding, evaluator, static_lock):
        from eva.operations.component_development import DevelopmentAssessment
        binding = validate_execution_binding(execution_binding)
        if (request.relative_path != WORK_STRATEGY_PATH or request.source_target is not None
                or request.baseline_sha256 != self.assessment["work_evidence"]["baseline_sha256"]):
            raise ValueError("native component suite binding differs")
        if not self.lock.acquire(blocking=False):
            raise ValueError("native component work is already running")
        try:
            self.authorize(binding, request=request)
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
            authorizer = lambda: self.authorize(binding, request=request)
            baseline = self.produce(
                leg="baseline", request=request, execution_binding=deepcopy(binding),
                settings=deepcopy(self.config["settings"]),
                assessment=deepcopy(self.assessment), authorize=authorizer)
            if baseline is not None:
                raise ValueError("producer must seal evidence, not supply scores")
            # A new consumer pins the completed baseline BEFORE candidate work.
            assessor = DevelopmentAssessment(self.assessment)
            if assessor.assessment_sha256 != evaluator.assessment.assessment_sha256:
                raise ValueError("fixed native work assessment binding changed")
            if assessor.work_evidence.baseline_error is not None:
                raise ValueError("baseline work evidence is not sealed")
            self.authorize(binding, request=request)
            candidate = self.produce(
                leg="candidate", request=request, execution_binding=deepcopy(binding),
                settings=deepcopy(self.config["settings"]),
                assessment=deepcopy(self.assessment), authorize=authorizer)
            if candidate is not None:
                raise ValueError("producer must seal evidence, not supply scores")
            self.authorize(binding, request=request)
            report["development_assessment"] = assessor.assess(report)
            report["execution_binding_ref"] = binding["execution_binding_ref"]
            return report
        finally:
            self.lock.release()
