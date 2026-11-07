"""Candidate-blind source and runtime shape for the existing scheduled-failure oracle."""
import base64
import hashlib
import json
from pathlib import Path
import re

from eva.application.self_repair import MAX_REPAIR_SOURCE_BYTES, MAX_REPAIR_TOTAL_SOURCE_BYTES
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from eva.operations.reviewed_component_application import _artifact
from eva.operations.runtime_repair_development import _put
from eva.operations.runtime_repair_publisher import (
    _ABSENT_SOURCE, _IMAGE_SOURCE_READER, current_repair_installation, require_unshadowed_repair_source,
)
from eva.operations.runtime_repair_target import _sha, repair_services, verified_failure_binding


_LOADER = "adapters/postgres/scheduled_work_failure.py"
_CALLER = "bootstrap/self_repair.py"
_READ_ONLY_SOURCES = (
    _CALLER,
    "adapters/postgres/operational_timeline.py",
    "contracts/canonical_json.py",
    "adapters/postgres/scheduled_work.py",
    "adapters/postgres/private_work_return.py",
)
_MANIFEST = "EvaScheduledFailureDiagnostics/v1"
_CONTEXT = "EvaScheduledFailureOriginalShape/v1"
_WORKSPACE = "repair/diagnostics"

_CORE_CONTEXT_READER = '''
from pathlib import Path
import hashlib
import json
import sys
import sqlalchemy as sa

from eva.adapters.postgres.current_catalog import delegated_work_runs
from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline, operational_checkpoints, operational_events,
)
from eva.adapters.postgres import scheduled_work_failure as installed_loader
from eva.adapters.postgres.self_repair import HOST_SCHEDULED_FAILURE_SOURCE
from eva.bootstrap import self_repair as installed_caller
from eva.bootstrap.runtime_repair_host import _context
from eva.contracts.canonical_json import canonical_sha256

request = json.loads(sys.argv[1])
binding = request["binding"]
source_root = Path("/opt/eva/src/eva")
origins = {"bootstrap/self_repair.py": installed_caller, "adapters/postgres/scheduled_work_failure.py": installed_loader}
if set(request["observed_sources"]) != set(origins):
    raise ValueError("observed source scope differs")
for relative, digest in request["observed_sources"].items():
    path = source_root / relative
    if (Path(origins[relative].__file__) != path or path.resolve(strict=True) != path
            or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
        raise ValueError("observed source differs")

def scoped(table, scope):
    return table.tenant_id == scope.tenant_id, table.installation_id == scope.installation_id

def readonly(connection):
    connection.exec_driver_sql("SET TRANSACTION READ ONLY")
    if connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "on":
        raise ValueError("read-only diagnostics transaction required")

def describe_timeline(state, events):
    return {
        "checkpoint": {"phase": state.phase, "status": state.status, "revision": state.revision,
                       "operation_id_present": state.operation_id is not None},
        "events": [{
            "sequence": event.sequence, "event_type": event.event_type,
            "matches_checkpoint_head": event.checkpoint_id == state.checkpoint_id,
            "canonical_payload_hash_matches": canonical_sha256(event.payload) == event.payload_sha256,
            "data_keys": sorted(event.payload["data"]),
            "return_binding_present": "return_binding" in event.payload["data"],
        } for event in events],
        "event_count": len(events),
    }

with _context(None, None, None) as (authority, service, store, _settings):
    owner = authority.owner
    if binding["scope_sha256"] != canonical_sha256({
        "tenant_id": owner._tenant_id, "installation_id": owner._installation_id,
        "principal_id": str(owner._owner),
    }):
        raise ValueError("diagnostic owner scope differs")
    c, e, w = operational_checkpoints.c, operational_events.c, delegated_work_runs.c
    with owner._engine.begin() as connection:
        readonly(connection)
        authority.timeline._scope(connection)
        incident = connection.execute(sa.select(e.payload, e.payload_sha256).join(
            operational_checkpoints, sa.and_(c.tenant_id == e.tenant_id, c.installation_id == e.installation_id,
                                              c.thread_id == e.thread_id)
        ).where(*scoped(e, owner._scope), e.thread_id == binding["incident_id"],
            e.event_type == "SELF_REPAIR_OBSERVED", c.phase == "repair_investigating",
            c.status == "waiting_for_proof")).one()
    data = incident.payload["data"]
    source = data["source"]
    proof = source["technical_provenance"]
    if (canonical_sha256(incident.payload) != bytes(incident.payload_sha256).hex()
            or bytes(incident.payload_sha256).hex() != binding["incident_event_sha256"]
            or data["incident_id"] != binding["incident_id"] or data["intent_id"] != binding["intent_id"]
            or data["owner_principal_id"] != str(owner._owner)
            or data["mandate_sha256"] != binding["mandate_sha256"]
            or data["observation"] != binding["observation"]
            or source["contract"] != HOST_SCHEDULED_FAILURE_SOURCE
            or data["fingerprint"] != source["observation_sha256"]
            or canonical_sha256(source) != data["source_sha256"]
            or data["source_sha256"] != binding["source_sha256"]
            or canonical_sha256(proof) != source["technical_provenance_sha256"]):
        raise ValueError("diagnostic incident evidence differs")
    if store.scope != owner._authority or service.conversation_scope != owner._scope:
        raise ValueError("diagnostic scope differs")
    with store._engine.begin() as connection:
        readonly(connection)
        PostgresOperationalTimeline(store._engine, store.scope)._scope(connection)
        work = connection.execute(sa.select(
            w.spec_payload, w.spec_sha256, w.state, w.error_code,
            w.call_started_at.is_not(None).label("call_started_present"),
            sa.or_(w.lease_owner.is_not(None), w.lease_expires_at.is_not(None)).label("lease_present"),
            w.result_payload.is_not(None).label("result_present"),
            w.stop_requested_at.is_not(None).label("stop_requested_present"),
        ).where(*scoped(w, store.scope), w.work_id == proof["work_id"],
                w.owner_principal_id == owner._owner, w.principal_binding_id == owner._binding)).one()
    spec = work.spec_payload
    if (canonical_sha256(spec) != bytes(work.spec_sha256).hex()
            or bytes(work.spec_sha256).hex() != proof["predecessor_spec_sha256"]
            or spec["source_ingress_sha256"] != proof["source_ingress_data_sha256"]):
        raise ValueError("diagnostic predecessor differs")

    # Use the existing timeline query with a read-only-only engine. Its own
    # connections cannot write, including on an unexpected execution path.
    from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
    read_engine = sa.create_engine(sqlalchemy_postgres_dsn(_settings.database_dsn),
                                  connect_args={"options": "-c default_transaction_read_only=on"})
    try:
        with read_engine.connect() as connection:
            if connection.exec_driver_sql("SHOW transaction_read_only").scalar_one() != "on":
                raise ValueError("read-only diagnostics engine required")
        read_timeline = PostgresOperationalTimeline(read_engine, store.scope)
        state, events = read_timeline.load_with_events(proof["schedule_id"])
        stop_state, stop_events = read_timeline.load_with_events(proof["recovery_stop_thread"])
    finally:
        read_engine.dispose()
    if (len(events) != 1 or len(stop_events) != 1
            or events[0].payload_sha256 != proof["scheduled_marker_sha256"]
            or stop_events[0].payload_sha256 != proof["recovery_stop_sha256"]):
        raise ValueError("diagnostic retained timeline differs")
    marker = events[0].payload["data"]
    stop = stop_events[0].payload["data"]
    if (marker.get("work_id") != proof["work_id"]
            or stop != {"work_id": proof["work_id"], "execution_id": spec["execution_id"],
                        "spec_sha256": proof["predecessor_spec_sha256"],
                        "source_ingress_sha256": proof["source_ingress_data_sha256"]}):
        raise ValueError("diagnostic work and marker linkage differs")
    facts = {
        "contract": "EvaScheduledFailureOriginalShape/v1",
        "schedule": describe_timeline(state, events),
        "recovery_stop": describe_timeline(stop_state, stop_events),
        "loader_selected_sequences": [event.sequence for event in events
                                      if event.sequence in (1, state.revision)],
        "first_sequence_equals_checkpoint_revision": state.revision == 1,
        "marker_operation": marker["operation"],
        "automatic_retry": marker["automatic_retry"],
        "predecessor": {
            "contract": spec["contract"], "generation": spec["generation"], "audience": spec["audience"],
            "state": work.state, "error_code": work.error_code, "call_started_present": work.call_started_present,
            "lease_present": work.lease_present, "result_present": work.result_present,
            "stop_requested_present": work.stop_requested_present,
        },
    }
    print(json.dumps(facts))
'''


def _target_binding(target):
    services = repair_services(target)
    paired = ("core", "delegated-work-worker")
    if services not in (("core",), paired) or set(target["sources"]) != {_LOADER}:
        raise ValueError("diagnostics require the original scheduled failure scope")
    if services == paired:
        # The existing paired target resolver verified the common loader bytes.
        # The diagnostic caller and actual observation still come from core.
        scope = {key: target[key] for key in (
            "contract", "failure", "installation", "sources", "excluded_paths",
        )}
        if canonical_sha256(scope) != _sha(target["target_id"]):
            raise ValueError("paired diagnostic target identity changed")
    binding = verified_failure_binding(target["failure"])
    caller = {frame["source_sha256"] for frame in binding["observation"]["frames"] if frame["path"] == _CALLER}
    loader = {frame["source_sha256"] for frame in binding["observation"]["frames"] if frame["path"] == _LOADER}
    if len(caller) != 1 or loader != {target["sources"][_LOADER]} or _CALLER not in target["excluded_paths"]:
        raise ValueError("diagnostic caller and loader must match the observed source frames")
    return binding, {_CALLER: next(iter(caller)), _LOADER: _sha(target["sources"][_LOADER])}


def validate_blocked_diagnostic(target, blocked):
    """Recognize the prior blocked oracle receipt; freeform text grants no authority."""
    _target_binding(target)
    flags = {"oracle_created", "behavioral_tests_executed", "candidate_submitted", "installation_changed"}
    digest = target["sources"][_LOADER]
    verified_source = {"expected_sha256": digest, "actual_sha256": digest}
    if (not isinstance(blocked, dict) or set(blocked) != flags | {
            "status", "role", "target_id", "verified_source", "observations",
            "missing_required_controls", "resume_action"}
            or blocked["status"] != "blocked" or blocked["role"] != "original_behavior_oracle"
            or blocked["target_id"] != _sha(target["target_id"])
            or any(blocked[key] is not False for key in flags)
            or blocked["verified_source"] not in (
                {**verified_source, "path": _LOADER},
                {**verified_source, "path": "/workspace/repair/source/eva/" + _LOADER},
            )
            or not isinstance(blocked["resume_action"], str) or not 1 <= len(blocked["resume_action"]) <= 4000):
        raise ValueError("exact prior candidate-blind blocked oracle required")
    for key in ("observations", "missing_required_controls"):
        if (not isinstance(blocked[key], list) or not 1 <= len(blocked[key]) <= 24
                or any(not isinstance(item, str) or not 1 <= len(item) <= 4000 for item in blocked[key])):
            raise ValueError("bounded blocked oracle metadata required")


def _installed_source(publisher, image, relative, directory):
    if relative not in _READ_ONLY_SOURCES or re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None:
        raise ValueError("closed read-only diagnostic source required")
    encoded = publisher._command(["docker", "run", "--rm", "--network", "none", "--read-only",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "10001:10001",
        "--entrypoint", "python", image, "-I", "-S", "-c", _IMAGE_SOURCE_READER, relative,
        str(MAX_REPAIR_SOURCE_BYTES), _ABSENT_SOURCE], directory)
    if encoded == _ABSENT_SOURCE or len(encoded) > 4 * ((MAX_REPAIR_SOURCE_BYTES + 2) // 3):
        raise ValueError("bounded installed diagnostic source required")
    raw = base64.b64decode(encoded, validate=True)
    if not 1 <= len(raw) <= MAX_REPAIR_SOURCE_BYTES:
        raise ValueError("bounded installed diagnostic source required")
    raw.decode("utf-8")
    return raw


def _validate_facts(facts):
    fields = {"contract", "schedule", "recovery_stop", "loader_selected_sequences",
              "first_sequence_equals_checkpoint_revision", "marker_operation", "automatic_retry", "predecessor"}
    if (not isinstance(facts, dict) or set(facts) != fields or facts["contract"] != _CONTEXT
            or facts["marker_operation"] != "resume" or facts["automatic_retry"] is not False):
        raise ValueError("scheduled-failure context differs")
    for key, phase, status, event_type, allowed in (
        ("schedule", "blocked", "needs_help", "SCHEDULED_WORK_BLOCKED",
         {"operation", "work_id", "run_at", "reason", "blocker_code", "automatic_retry"}),
        ("recovery_stop", "stopped", "completed", "PRIVATE_WORK_RECOVERY_STOPPED",
         {"work_id", "execution_id", "source_ingress_sha256", "spec_sha256"}),
    ):
        item = facts[key]
        if (not isinstance(item, dict) or set(item) != {"checkpoint", "events", "event_count"}
                or type(item["event_count"]) is not int or item["event_count"] != 1
                or not isinstance(item["events"], list) or len(item["events"]) != 1):
            raise ValueError("diagnostic timeline shape differs")
        checkpoint, event = item["checkpoint"], item["events"][0]
        if (checkpoint != {"phase": phase, "status": status, "revision": 1, "operation_id_present": False}
                or type(checkpoint["revision"]) is not int or checkpoint["operation_id_present"] is not False
                or event != {"sequence": 1, "event_type": event_type, "matches_checkpoint_head": True,
                             "canonical_payload_hash_matches": True, "data_keys": sorted(allowed),
                             "return_binding_present": False}
                or type(event["sequence"]) is not int or event["matches_checkpoint_head"] is not True
                or event["canonical_payload_hash_matches"] is not True
                or event["return_binding_present"] is not False):
            raise ValueError("diagnostic event shape differs")
    predecessor = facts["predecessor"]
    if (facts["loader_selected_sequences"] != [1] or type(facts["loader_selected_sequences"][0]) is not int
            or facts["first_sequence_equals_checkpoint_revision"] is not True
            or predecessor != {"contract": "DelegatedDigitalWork/v4", "generation": 1, "audience": "private",
                               "state": "ambiguous", "error_code": "provider_unavailable", "call_started_present": True,
                               "lease_present": False, "result_present": False, "stop_requested_present": False}
            or type(predecessor["generation"]) is not int or predecessor["call_started_present"] is not True
            or any(predecessor[key] is not False for key in ("lease_present", "result_present", "stop_requested_present"))):
        raise ValueError("diagnostic predecessor shape differs")


def _generation(publisher, runtime, directory, image):
    rows = json.loads(publisher._command(["docker", "inspect", runtime.config["core_container"]], directory))
    if not isinstance(rows, list) or len(rows) != 1:
        raise ValueError("one current diagnostic core required")
    current = rows[0]
    require_unshadowed_repair_source(current)
    if (current["Image"] != image or current["State"]["Running"] is not True
            or not re.fullmatch(r"[0-9a-f]{64}", current["Id"])):
        raise ValueError("diagnostic core generation differs")
    return current["Id"], current["State"]["StartedAt"]


def _bound_installation(publisher, directory, target):
    if current_repair_installation(publisher, directory)[1] != target["installation"]:
        raise ValueError("diagnostic source image or configuration changed")


def _current_binding(runtime, binding):
    if (runtime.config["scope_sha256"] != binding["scope_sha256"]
            or runtime.repair_call("binding", incident_id=binding["incident_id"]) != binding):
        raise ValueError("diagnostic repair authority changed")


def _manifest_identity(target, binding, blocked_sha256, parent_task_sha256):
    return {"contract": _MANIFEST, "target_id": _sha(target["target_id"]), "role": "original_behavior_oracle",
            "baseline": target["installation"]["core"], "incident_event_sha256": binding["incident_event_sha256"],
            "scope_sha256": binding["scope_sha256"], "source_sha256": binding["source_sha256"],
            "mandate_sha256": binding["mandate_sha256"], "blocked_sha256": _sha(blocked_sha256),
            "parent_task_sha256": _sha(parent_task_sha256)}


def prepare_scheduled_failure_diagnostics(publisher, runtime, target, directory, *,
                                          blocked_receipt, parent_task_sha256):
    """Freeze the exact current original context without granting additional editable sources."""
    _, blocked = _artifact(blocked_receipt)
    validate_blocked_diagnostic(target, blocked)
    binding, observed_sources = _target_binding(target)
    directory = Path(directory)
    identity = _manifest_identity(target, binding, blocked_receipt["sha256"], parent_task_sha256)
    image = identity["baseline"]["image"]
    _bound_installation(publisher, directory, target)
    generation = _generation(publisher, runtime, directory, image)
    _current_binding(runtime, binding)
    sources = {relative: _installed_source(publisher, image, relative, directory) for relative in _READ_ONLY_SOURCES}
    if (sum(map(len, sources.values())) > MAX_REPAIR_TOTAL_SOURCE_BYTES
            or hashlib.sha256(sources[_CALLER]).hexdigest() != observed_sources[_CALLER]):
        raise ValueError("diagnostic source snapshot differs")
    raw = json.dumps({"binding": binding, "observed_sources": observed_sources}, ensure_ascii=True)
    result = runtime.run(["docker", "exec", generation[0], "python", "-B", "-c", _CORE_CONTEXT_READER, raw],
                         text=True, capture_output=True, timeout=45)
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > 16384:
        raise ValueError("current read-only scheduled-failure context unavailable")
    facts = json.loads(result.stdout)
    _validate_facts(facts)
    _current_binding(runtime, binding)
    _bound_installation(publisher, directory, target)
    if _generation(publisher, runtime, directory, image) != generation:
        raise ValueError("diagnostic core generation changed")
    manifest = {**identity, "facts_sha256": canonical_sha256(facts),
                "read_only_sources": {path: {"sha256": hashlib.sha256(raw).hexdigest(), "size": len(raw)}
                                      for path, raw in sources.items()}}
    return _put(directory / "oracle-diagnostics.private.json", {
        "manifest": manifest, "facts": facts,
        "source_bytes": {path: base64.b64encode(raw).decode("ascii") for path, raw in sources.items()},
    })


def seed_scheduled_failure_diagnostics(artifacts, metadata, target, receipt, *,
                                       blocked_sha256, parent_task_sha256):
    """Expose only original code and redacted facts to the candidate-blind oracle."""
    binding, observed_sources = _target_binding(target)
    if (metadata.get("role") != "original_behavior_oracle" or metadata.get("target_id") != target["target_id"]
            or metadata.get("incident_id") != binding["incident_id"]
            or metadata.get("candidate_sha256") is not None or metadata.get("comparison_sha256") is not None):
        raise ValueError("diagnostics require exact candidate-blind oracle work")
    _, snapshot = _artifact(receipt)
    if set(snapshot) != {"manifest", "facts", "source_bytes"}:
        raise ValueError("diagnostic receipt differs")
    facts, manifest, encoded = snapshot["facts"], snapshot["manifest"], snapshot["source_bytes"]
    _validate_facts(facts)
    identity = _manifest_identity(target, binding, blocked_sha256, parent_task_sha256)
    if (not isinstance(manifest, dict) or set(manifest) != set(identity) | {"facts_sha256", "read_only_sources"}
            or any(manifest[key] != value for key, value in identity.items())
            or manifest["facts_sha256"] != canonical_sha256(facts)
            or not isinstance(encoded, dict) or set(encoded) != set(_READ_ONLY_SOURCES)
            or not isinstance(manifest["read_only_sources"], dict)
            or set(manifest["read_only_sources"]) != set(_READ_ONLY_SOURCES)):
        raise ValueError("diagnostic manifest scope differs")
    files = {_WORKSPACE + "/manifest.json": canonical_json_bytes(manifest),
             _WORKSPACE + "/context.json": canonical_json_bytes(facts)}
    total = 0
    for relative, source in encoded.items():
        if not isinstance(source, str) or len(source) > 4 * ((MAX_REPAIR_SOURCE_BYTES + 2) // 3):
            raise ValueError("bounded diagnostic source required")
        raw = base64.b64decode(source, validate=True)
        digest = hashlib.sha256(raw).hexdigest()
        if (not 1 <= len(raw) <= MAX_REPAIR_SOURCE_BYTES
                or manifest["read_only_sources"][relative] != {"sha256": digest, "size": len(raw)}
                or (relative == _CALLER and digest != observed_sources[_CALLER])):
            raise ValueError("diagnostic source receipt differs")
        raw.decode("utf-8")
        total += len(raw)
        files[_WORKSPACE + "/eva/" + relative] = raw
    if total > MAX_REPAIR_TOTAL_SOURCE_BYTES:
        raise ValueError("diagnostic source snapshot exceeds bound")
    artifacts._seed(metadata, files)
    return {"diagnostic_manifest": "/workspace/" + _WORKSPACE + "/manifest.json",
            "diagnostic_context": "/workspace/" + _WORKSPACE + "/context.json",
            "diagnostic_read_only_sources": "/workspace/" + _WORKSPACE + "/eva"}
