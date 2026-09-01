#!/usr/bin/env python3
"""Validate TOOL01 v2 evidence without trusting self-asserted success."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


_V1_GENERATOR = (
    Path(__file__).resolve().parent.parent
    / "tool01-replacement-acceptance-v1"
    / "generate_source_authority.py"
)
_SOURCE_SPEC = importlib.util.spec_from_file_location(
    "tool01_v1_source_authority", _V1_GENERATOR
)
if _SOURCE_SPEC is None or _SOURCE_SPEC.loader is None:
    raise RuntimeError("TOOL01 v1 source authority is unavailable")
source_authority = importlib.util.module_from_spec(_SOURCE_SPEC)
_SOURCE_SPEC.loader.exec_module(source_authority)


BUNDLE_CONTRACT = "EvaTool01AcceptanceBundle/v2"
CANDIDATE_CONTRACT = "EvaTool01CandidateAuthority/v2"
CATALOG_CONTRACT = "EvaTool01RuntimeCatalog/v2"
TRAJECTORY_CONTRACT = "EvaTool01FrameworkTrajectory/v2"
TIMELINE_CONTRACT = "EvaTool01DurableTimeline/v2"
PROVIDER_CONTRACT = "EvaTool01ExternalProviderCounters/v2"
CRASH_CONTRACT = "EvaTool01ContainerCrashAudit/v2"
RESULT_CONTRACT = "EvaTool01AcceptanceResult/v2"

PROMPT = (
    'Schrijf exact "warme kopermaan" naar "tool01-proof.txt" in de beschikbare '
    "proefwerkmap en controleer daarna of het bestand exact zo is opgeslagen."
)
EXPECTED_FILESYSTEM_ARGUMENTS = {
    "content": "warme kopermaan",
    "path": "tool01-proof.txt",
}
MODEL = "gpt-5.4-mini"
REASONING = "medium"
BUDGET = {
    "modelRequests": 8,
    "outputTokens": 1_500,
    "toolCalls": 8,
    "totalTokens": 8_000,
    "wallSeconds": 120,
}

_CONTAINER_ID = re.compile(r"^[0-9a-f]{64}$")
_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,159}$")
_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_NONCE = re.compile(r"^[0-9a-f]{12}$")
_MAX_ARTIFACT_BYTES = 1_000_000


class Tool01V2Error(RuntimeError):
    """An artifact or external authority is malformed."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _sha256(value: object) -> str:
    return hashlib.sha256(_canonical(value)).hexdigest()


def _exact(value: object, keys: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise Tool01V2Error(f"{label} fields differ")
    return value


def _text(value: object, *, label: str, limit: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > limit
    ):
        raise Tool01V2Error(f"{label} is invalid")
    return value


def _identifier(value: object, *, label: str) -> str:
    result = _text(value, label=label, limit=160)
    if _IDENTIFIER.fullmatch(result) is None:
        raise Tool01V2Error(f"{label} is invalid")
    return result


def _digest(value: object, *, label: str) -> str:
    result = _text(value, label=label, limit=64)
    if _DIGEST.fullmatch(result) is None:
        raise Tool01V2Error(f"{label} is invalid")
    return result


def _container_id(value: object, *, label: str) -> str:
    result = _text(value, label=label, limit=64)
    if _CONTAINER_ID.fullmatch(result) is None:
        raise Tool01V2Error(f"{label} is invalid")
    return result


def _positive(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise Tool01V2Error(f"{label} is invalid")
    return value


def _counter(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise Tool01V2Error(f"{label} is invalid")
    return value


def _filesystem_projection(
    model_arguments: object,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]] | None:
    """Recompute the concrete TOOL01 filesystem commands from model output."""

    if not isinstance(model_arguments, dict) or set(model_arguments) != {
        "content",
        "path",
    }:
        return None
    try:
        path = _safe_path(model_arguments["path"], label="model filesystem path")
        content = _text(
            model_arguments["content"], label="model filesystem content", limit=4_096
        )
    except Tool01V2Error:
        return None
    execute = {"content": content, "path": path}
    if execute != EXPECTED_FILESYSTEM_ARGUMENTS:
        return None
    observe = {"path": path}
    expected = {"content": content}
    return execute, observe, expected


def _reject_pass_claim(value: object) -> None:
    if isinstance(value, dict):
        if "passed" in value or "allPassed" in value:
            raise Tool01V2Error("self-asserted pass fields are forbidden")
        for item in value.values():
            _reject_pass_claim(item)
    elif isinstance(value, list):
        for item in value:
            _reject_pass_claim(item)


def _safe_path(value: object, *, label: str) -> str:
    result = _text(value, label=label)
    candidate = PurePosixPath(result)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or result != candidate.as_posix()
    ):
        raise Tool01V2Error(f"{label} is unsafe")
    return result


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    if not raw or len(raw) > _MAX_ARTIFACT_BYTES:
        raise Tool01V2Error(f"{label} size is invalid")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise Tool01V2Error(f"{label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise Tool01V2Error(f"{label} must be an object")
    _reject_pass_claim(value)
    return value, raw


def _artifact(bundle_root: Path, reference: object, *, label: str) -> dict[str, Any]:
    row = _exact(reference, {"bytes", "path", "sha256"}, label=f"{label} reference")
    relative = _safe_path(row["path"], label=f"{label} path")
    digest = _digest(row["sha256"], label=f"{label} sha256")
    length = row["bytes"]
    if not isinstance(length, int) or isinstance(length, bool) or length <= 0:
        raise Tool01V2Error(f"{label} byte length is invalid")
    root = bundle_root.resolve()
    target = (bundle_root / relative).resolve(strict=True)
    if not target.is_relative_to(root):
        raise Tool01V2Error(f"{label} path escapes the bundle")
    value, raw = _read_json(target, label=label)
    if len(raw) != length or hashlib.sha256(raw).hexdigest() != digest:
        raise Tool01V2Error(f"{label} binding differs")
    return value


def _header(value: dict[str, Any], contract: str, run_id: str, *, label: str) -> None:
    if value.get("contract") != contract or value.get("runId") != run_id:
        raise Tool01V2Error(f"{label} authority differs")


def _candidate(
    value: dict[str, Any],
    run_id: str,
    *,
    expected_image_id: str,
    expected_source_commit: str,
) -> dict[str, str]:
    row = _exact(
        value,
        {"contract", "imageId", "proofHarnessSha256", "runId", "sourceCommitLabel"},
        label="candidate authority",
    )
    _header(row, CANDIDATE_CONTRACT, run_id, label="candidate authority")
    image_id = _text(row["imageId"], label="candidate image id", limit=71)
    commit = _text(row["sourceCommitLabel"], label="candidate source commit", limit=40)
    _digest(row["proofHarnessSha256"], label="proof harness sha256")
    if _IMAGE_ID.fullmatch(image_id) is None or image_id != expected_image_id:
        raise Tool01V2Error("candidate image differs from external authority")
    if _GIT_COMMIT.fullmatch(commit) is None or commit != expected_source_commit:
        raise Tool01V2Error("candidate source commit differs from external authority")
    return {"imageId": image_id, "sourceCommit": commit}


def _crash_audit(
    value: dict[str, Any],
    run_id: str,
    candidate: dict[str, str],
) -> dict[str, str]:
    row = _exact(
        value, {"containers", "contract", "kill", "runId"}, label="crash audit"
    )
    _header(row, CRASH_CONTRACT, run_id, label="crash audit")
    containers = row["containers"]
    if not isinstance(containers, list) or len(containers) != 3:
        raise Tool01V2Error("crash audit must contain exactly three containers")
    by_process: dict[str, dict[str, Any]] = {}
    ids: list[str] = []
    for container in containers:
        item = _exact(
            container,
            {"containerId", "imageId", "processId", "sourceCommitLabel", "state"},
            label="container audit",
        )
        process_id = _text(item["processId"], label="process id", limit=2)
        container_id = _container_id(item["containerId"], label="container id")
        if process_id in by_process:
            raise Tool01V2Error("container process is duplicated")
        if item["imageId"] != candidate["imageId"]:
            raise Tool01V2Error("container image differs from candidate")
        if item["sourceCommitLabel"] != candidate["sourceCommit"]:
            raise Tool01V2Error("container source commit differs from candidate")
        state = _exact(
            item["state"], {"exitCode", "oomKilled", "status"}, label="container state"
        )
        if state["status"] != "exited" or not isinstance(state["oomKilled"], bool):
            raise Tool01V2Error("container terminal state is invalid")
        by_process[process_id] = item
        ids.append(container_id)
    if set(by_process) != {"p1", "p2", "p3"} or len(set(ids)) != 3:
        raise Tool01V2Error("three unique p1/p2/p3 containers are required")
    p1_state = by_process["p1"]["state"]
    if p1_state != {"exitCode": 137, "oomKilled": False, "status": "exited"}:
        raise Tool01V2Error("p1 was not a non-OOM exit-137 crash")
    clean_state = {"exitCode": 0, "oomKilled": False, "status": "exited"}
    if (
        by_process["p2"]["state"] != clean_state
        or by_process["p3"]["state"] != clean_state
    ):
        raise Tool01V2Error("p2 and p3 must exit cleanly")
    kill = _exact(
        row["kill"],
        {"commandExitCode", "preKillRunning", "signal", "targetContainerId"},
        label="kill audit",
    )
    if kill != {
        "commandExitCode": 0,
        "preKillRunning": True,
        "signal": "KILL",
        "targetContainerId": by_process["p1"]["containerId"],
    }:
        raise Tool01V2Error("external SIGKILL audit differs")
    return {process: item["containerId"] for process, item in by_process.items()}


def _catalog(
    value: dict[str, Any], run_id: str, containers: dict[str, str]
) -> dict[str, Any]:
    row = _exact(
        value,
        {
            "contract",
            "discoveredByContainerId",
            "discoveryReceiptSha256",
            "nonce",
            "runId",
            "tools",
        },
        label="catalog",
    )
    _header(row, CATALOG_CONTRACT, run_id, label="catalog")
    if row["discoveredByContainerId"] != containers["p1"]:
        raise Tool01V2Error("catalog was not discovered by p1")
    _digest(row["discoveryReceiptSha256"], label="catalog discovery receipt")
    nonce = _text(row["nonce"], label="catalog nonce", limit=12)
    if _NONCE.fullmatch(nonce) is None:
        raise Tool01V2Error("catalog nonce is invalid")
    tools = row["tools"]
    if not isinstance(tools, list) or len(tools) != 3:
        raise Tool01V2Error("catalog must contain effect, readback and distractor")
    by_role: dict[str, dict[str, str]] = {}
    names: set[str] = set()
    for tool in tools:
        item = _exact(
            tool,
            {"description", "inputSchema", "name", "providerId", "role"},
            label="catalog tool",
        )
        name = _identifier(item["name"], label="catalog tool name")
        role = _text(item["role"], label="catalog role", limit=16)
        provider_id = _identifier(item["providerId"], label="provider id")
        _text(item["description"], label="tool description", limit=4_096)
        if not isinstance(item["inputSchema"], dict) or not name.endswith(f"_{nonce}"):
            raise Tool01V2Error("catalog tool is not runtime nonce-bound")
        if role in by_role or name in names:
            raise Tool01V2Error("catalog tool or role is duplicated")
        by_role[role] = {"name": name, "providerId": provider_id}
        names.add(name)
    if set(by_role) != {"distractor", "effect", "readback"}:
        raise Tool01V2Error("catalog roles differ")
    if by_role["effect"]["providerId"] != by_role["readback"]["providerId"]:
        raise Tool01V2Error("effect and readback must share one provider")
    if by_role["distractor"]["providerId"] == by_role["effect"]["providerId"]:
        raise Tool01V2Error("distractor must come from another provider")
    return {"byRole": by_role, "sha256": _sha256(row)}


def _trajectory(
    value: dict[str, Any],
    run_id: str,
    containers: dict[str, str],
    catalog: dict[str, Any],
) -> tuple[dict[str, str], list[str]]:
    violations: list[str] = []
    row = _exact(
        value,
        {"budget", "contract", "events", "model", "prompt", "reasoning", "runId"},
        label="framework trajectory",
    )
    _header(row, TRAJECTORY_CONTRACT, run_id, label="framework trajectory")
    if row["prompt"] != PROMPT:
        violations.append("prompt_differs")
    if row["model"] != MODEL or row["reasoning"] != REASONING:
        violations.append("model_authority_differs")
    if row["budget"] != BUDGET:
        violations.append("budget_differs")
    events = row["events"]
    if not isinstance(events, list) or not events:
        raise Tool01V2Error("framework events are invalid")
    by_kind: dict[str, dict[str, Any]] = {}
    previous = 0
    for event in events:
        item = _exact(
            event, {"containerId", "data", "kind", "sequence"}, label="framework event"
        )
        sequence = _positive(item["sequence"], label="framework sequence")
        if sequence <= previous:
            raise Tool01V2Error("framework sequences are not increasing")
        previous = sequence
        kind = _text(item["kind"], label="framework event kind", limit=64)
        if kind in by_kind or not isinstance(item["data"], dict):
            raise Tool01V2Error("framework event is duplicated or malformed")
        by_kind[kind] = item
    expected_kinds = {
        "action_port_execute",
        "catalog_discovered",
        "deferred_requests",
        "deferred_results",
        "final_answer",
        "replay_received",
        "replay_result",
    }
    if set(by_kind) != expected_kinds:
        violations.append("framework_event_set_differs")
        return {}, sorted(set(violations))
    if (
        by_kind["catalog_discovered"]["containerId"] != containers["p1"]
        or by_kind["catalog_discovered"]["data"].get("catalogSha256")
        != catalog["sha256"]
    ):
        violations.append("catalog_trajectory_binding_differs")
    deferred = by_kind["deferred_requests"]
    action = by_kind["action_port_execute"]
    results = by_kind["deferred_results"]
    final = by_kind["final_answer"]
    replay_received = by_kind["replay_received"]
    replay_result = by_kind["replay_result"]
    binding_fields = ("callId", "messageId", "operationId", "toolName")
    binding = {field: deferred["data"].get(field) for field in binding_fields}
    for field, field_value in binding.items():
        if not isinstance(field_value, str) or not field_value:
            violations.append(f"deferred_{field}_missing")
    history = deferred["data"].get("historySha256")
    if not isinstance(history, str) or _DIGEST.fullmatch(history) is None:
        violations.append("framework_history_missing")
    if deferred["containerId"] != containers["p1"] or catalog["byRole"]["effect"][
        "name"
    ] != binding.get("toolName"):
        violations.append("model_did_not_select_effect")
    if (
        action["containerId"] != containers["p1"]
        or action["data"].get("owner") != "ConversationActionPort"
        or any(
            action["data"].get(field) != binding.get(field) for field in binding_fields
        )
    ):
        violations.append("action_port_binding_differs")
    receipt_sha = results["data"].get("receiptSha256")
    if (
        results["containerId"] != containers["p2"]
        or results["data"].get("inputKind") != "DeferredToolResults"
        or results["data"].get("receiptSource") != "durable_timeline"
        or results["data"].get("historySha256") != history
        or not isinstance(receipt_sha, str)
        or _DIGEST.fullmatch(receipt_sha) is None
    ):
        violations.append("durable_deferred_result_differs")
    if (
        final["containerId"] != containers["p2"]
        or final["data"].get("receiptSha256") != receipt_sha
    ):
        violations.append("final_answer_binding_differs")
    if (
        replay_received["containerId"] != containers["p3"]
        or replay_result["containerId"] != containers["p3"]
        or any(
            replay_received["data"].get(field) != binding.get(field)
            for field in ("messageId", "operationId")
        )
        or replay_result["data"].get("receiptSha256") != receipt_sha
    ):
        violations.append("third_process_replay_differs")
    return {
        **{key: str(value) for key, value in binding.items()},
        "historySha256": str(history),
        "receiptSha256": str(receipt_sha),
    }, sorted(set(violations))


def _timeline(
    value: dict[str, Any],
    run_id: str,
    containers: dict[str, str],
    binding: dict[str, str],
) -> tuple[dict[str, str], list[str]]:
    violations: list[str] = []
    row = _exact(
        value,
        {"contract", "database", "events", "runId", "snapshots"},
        label="durable timeline",
    )
    _header(row, TIMELINE_CONTRACT, run_id, label="durable timeline")
    database = _exact(
        row["database"], {"alembicHead", "name", "postgresVersionNum"}, label="database"
    )
    _text(database["alembicHead"], label="alembic head", limit=80)
    if (
        database["name"] != "eva2_living_disposable_tool01"
        or database["postgresVersionNum"] != 160013
    ):
        violations.append("database_authority_differs")
    events = row["events"]
    if not isinstance(events, list) or len(events) != 3:
        violations.append("timeline_event_sequence_differs")
        return {}, sorted(set(violations))
    expected_types = ["TOOL_REQUEST_ADMITTED", "TOOL_PREPARED", "STATE_VERIFIED"]
    previous = 0
    for index, event in enumerate(events):
        item = _exact(
            event,
            {
                "eventType",
                "firstObservedByContainerId",
                "operationId",
                "payload",
                "payloadSha256",
                "sequence",
            },
            label="timeline event",
        )
        sequence = _positive(item["sequence"], label="timeline sequence")
        if sequence <= previous:
            raise Tool01V2Error("timeline sequences are not increasing")
        previous = sequence
        if item["eventType"] != expected_types[index]:
            violations.append("timeline_event_sequence_differs")
        expected_container = containers["p1"] if index < 2 else containers["p2"]
        if item["firstObservedByContainerId"] != expected_container:
            violations.append("timeline_process_boundary_differs")
        if item["operationId"] != binding.get("operationId"):
            violations.append("timeline_operation_differs")
        if not isinstance(item["payload"], dict) or item["payloadSha256"] != _sha256(
            item["payload"]
        ):
            violations.append("timeline_payload_hash_differs")
    admission_data = events[0]["payload"].get("data", {})
    prepared_data = events[1]["payload"].get("data", {})
    verified_data = events[2]["payload"].get("data", {})
    if not all(
        isinstance(item, dict)
        for item in (admission_data, prepared_data, verified_data)
    ):
        raise Tool01V2Error("timeline event data is malformed")
    model_arguments = admission_data.get("action_arguments")
    filesystem_projection = _filesystem_projection(model_arguments)
    model_arguments_sha = (
        _sha256(model_arguments) if filesystem_projection is not None else ""
    )
    execute_arguments = admission_data.get("request_arguments")
    observe_arguments = admission_data.get("request_observation_arguments")
    execute_arguments_sha = (
        _sha256(execute_arguments) if isinstance(execute_arguments, dict) else ""
    )
    observe_arguments_sha = (
        _sha256(observe_arguments) if isinstance(observe_arguments, dict) else ""
    )
    execute_tool_name = admission_data.get("request_tool_name")
    observe_tool_name = admission_data.get("request_observation_tool_name")
    configuration_sha = admission_data.get("request_tool_configuration_sha256")
    if (
        admission_data.get("action_call_id") != binding.get("callId")
        or admission_data.get("action_name") != binding.get("toolName")
        or admission_data.get("source_message_id") != binding.get("messageId")
        or not model_arguments_sha
        or not execute_arguments_sha
        or not observe_arguments_sha
        or not isinstance(execute_tool_name, str)
        or not isinstance(observe_tool_name, str)
        or not isinstance(configuration_sha, str)
        or _DIGEST.fullmatch(configuration_sha) is None
    ):
        violations.append("timeline_admission_binding_differs")
    if filesystem_projection is None:
        violations.append("model_filesystem_projection_differs")
        projected_execute: dict[str, str] = {}
        projected_observe: dict[str, str] = {}
        projected_expected: dict[str, str] = {}
    else:
        projected_execute, projected_observe, projected_expected = filesystem_projection
        if (
            execute_arguments != projected_execute
            or observe_arguments != projected_observe
        ):
            violations.append("model_filesystem_projection_differs")
    provider_tool_name = prepared_data.get("tool_name")
    if (
        not isinstance(provider_tool_name, str)
        or provider_tool_name != execute_tool_name
        or prepared_data.get("arguments") != execute_arguments
        or prepared_data.get("observation_tool_name") != observe_tool_name
        or prepared_data.get("observation_arguments") != observe_arguments
        or prepared_data.get("tool_configuration_sha256") != configuration_sha
        or prepared_data.get("expected_proof") != projected_expected
    ):
        violations.append("timeline_preparation_differs")
    proof = verified_data.get("proof")
    if (
        not isinstance(proof, dict)
        or proof.get("expected") != proof.get("observed")
        or proof.get("expected") != prepared_data.get("expected_proof")
    ):
        violations.append("state_verification_differs")
    snapshots = _exact(
        row["snapshots"],
        {"afterExecuteBeforeKill", "afterResume", "afterThirdProcessReplay"},
        label="timeline snapshots",
    )
    expected_snapshots = (
        (
            "afterExecuteBeforeKill",
            containers["p1"],
            "tool_prepared",
            "waiting_for_proof",
            2,
            2,
            None,
        ),
        (
            "afterResume",
            containers["p2"],
            "state_verified",
            "completed",
            3,
            3,
            binding.get("receiptSha256"),
        ),
        (
            "afterThirdProcessReplay",
            containers["p3"],
            "state_verified",
            "completed",
            3,
            3,
            binding.get("receiptSha256"),
        ),
    )
    for (
        name,
        container_id,
        phase,
        status,
        revision,
        count,
        receipt_sha,
    ) in expected_snapshots:
        snapshot = _exact(
            snapshots[name],
            {
                "checkpointProjectionSha256",
                "containerId",
                "eventCount",
                "eventsSha256",
                "phase",
                "receiptSha256",
                "revision",
                "status",
            },
            label=f"{name} snapshot",
        )
        projection = {"phase": phase, "revision": revision, "status": status}
        if snapshot != {
            "checkpointProjectionSha256": _sha256(projection),
            "containerId": container_id,
            "eventCount": count,
            "eventsSha256": _sha256(events[:count]),
            "phase": phase,
            "receiptSha256": receipt_sha,
            "revision": revision,
            "status": status,
        }:
            violations.append(f"{name}_snapshot_differs")
    return {
        "executeArgumentsSha256": execute_arguments_sha,
        "executeToolName": str(execute_tool_name),
        "observeArgumentsSha256": observe_arguments_sha,
        "observeToolName": str(observe_tool_name),
        "toolConfigurationSha256": str(configuration_sha),
    }, sorted(set(violations))


def _provider(
    value: dict[str, Any],
    run_id: str,
    containers: dict[str, str],
    catalog: dict[str, Any],
    binding: dict[str, str],
    timeline: dict[str, str],
) -> list[str]:
    violations: list[str] = []
    row = _exact(
        value,
        {"calls", "contract", "providerInstanceId", "runId", "snapshots"},
        label="provider counters",
    )
    _header(row, PROVIDER_CONTRACT, run_id, label="provider counters")
    instance_id = _identifier(row["providerInstanceId"], label="provider instance id")
    calls = row["calls"]
    if not isinstance(calls, list) or len(calls) != 2:
        violations.append("provider_call_set_differs")
        return sorted(set(violations))
    expected_call_kinds = ("execute", "observe")
    for call, kind in zip(calls, expected_call_kinds, strict=True):
        _exact(
            call,
            {
                "argumentsSha256",
                "callId",
                "containerId",
                "kind",
                "operationId",
                "toolConfigurationSha256",
                "toolName",
            },
            label="provider call",
        )
        if call["kind"] != kind:
            violations.append("provider_call_sequence_differs")
    execute, observe = calls
    if (
        execute["containerId"] != containers["p1"]
        or observe["containerId"] != containers["p2"]
        or execute["callId"] == observe["callId"]
        or execute["operationId"] != binding.get("operationId")
        or observe["operationId"] != binding.get("operationId")
        or execute["argumentsSha256"] != timeline.get("executeArgumentsSha256")
        or observe["argumentsSha256"] != timeline.get("observeArgumentsSha256")
        or execute["toolConfigurationSha256"] != timeline.get("toolConfigurationSha256")
        or observe["toolConfigurationSha256"] != timeline.get("toolConfigurationSha256")
    ):
        violations.append("separate_execute_observe_binding_differs")
    if (
        execute["toolName"] != timeline.get("executeToolName")
        or execute["toolName"] != catalog["byRole"]["effect"]["name"]
        or observe["toolName"] != timeline.get("observeToolName")
        or observe["toolName"] != catalog["byRole"]["readback"]["name"]
        or execute["toolName"] == observe["toolName"]
    ):
        violations.append("provider_tool_roles_differ")
    snapshots = row["snapshots"]
    if not isinstance(snapshots, list) or len(snapshots) != 4:
        violations.append("provider_counter_snapshots_differ")
        return sorted(set(violations))
    expected_phases = [
        ("beforeExecute", containers["p1"]),
        ("afterExecuteBeforeKill", containers["p1"]),
        ("afterResume", containers["p2"]),
        ("afterThirdProcessReplay", containers["p3"]),
    ]
    counts: list[tuple[int, int]] = []
    for snapshot, (phase, container_id) in zip(snapshots, expected_phases, strict=True):
        item = _exact(
            snapshot,
            {
                "containerId",
                "executeCalls",
                "observeCalls",
                "phase",
                "providerInstanceId",
            },
            label="provider counter snapshot",
        )
        if (
            item["phase"] != phase
            or item["containerId"] != container_id
            or item["providerInstanceId"] != instance_id
        ):
            violations.append("provider_counter_authority_differs")
        counts.append(
            (
                _counter(item["executeCalls"], label="execute counter"),
                _counter(item["observeCalls"], label="observe counter"),
            )
        )
    before, after_execute, after_resume, after_replay = counts
    if (
        after_execute != (before[0] + 1, before[1])
        or after_resume != (after_execute[0], after_execute[1] + 1)
        or after_replay != after_resume
    ):
        violations.append("provider_counter_deltas_differ")
    return sorted(set(violations))


def _source_status(
    source_reference: dict[str, Any],
    bundle_root: Path,
    source_root: Path | None,
    candidate_commit: str,
) -> tuple[str, list[str]]:
    row = _exact(
        source_reference, {"artifact", "status"}, label="source authority reference"
    )
    if row["status"] == "pending" and row["artifact"] is None:
        return "source_authority_pending", []
    if row["status"] not in {"pending", "complete"} or row["artifact"] is None:
        raise Tool01V2Error("source authority state is invalid")
    if source_root is None:
        raise Tool01V2Error("source root is required for source authority")
    source_value = _artifact(bundle_root, row["artifact"], label="sourceAuthority")
    try:
        verified = source_authority.verify_source_authority(source_value, source_root)
    except source_authority.SourceAuthorityError as error:
        raise Tool01V2Error(str(error)) from error
    if verified["status"] != row["status"]:
        raise Tool01V2Error("source authority status differs")
    if verified["status"] == "pending":
        return "source_authority_pending", []
    violations: list[str] = []
    if verified["gitCommit"] != candidate_commit:
        violations.append("source_commit_differs_from_candidate")
    if verified["blockers"] != []:
        violations.append("source_authority_has_blockers")
    return (
        "source_authority_passed" if not violations else "source_authority_failed",
        violations,
    )


def check(
    bundle_path: Path,
    *,
    expected_image_id: str,
    expected_source_commit: str,
    source_root: Path | None = None,
) -> dict[str, Any]:
    if _IMAGE_ID.fullmatch(expected_image_id) is None:
        raise Tool01V2Error("external expected image id is invalid")
    if _GIT_COMMIT.fullmatch(expected_source_commit) is None:
        raise Tool01V2Error("external expected source commit is invalid")
    bundle, _ = _read_json(bundle_path, label="bundle")
    row = _exact(
        bundle,
        {"artifacts", "contract", "runId", "schemaVersion", "sourceAuthority"},
        label="bundle",
    )
    if row["contract"] != BUNDLE_CONTRACT or row["schemaVersion"] != 2:
        raise Tool01V2Error("bundle authority differs")
    run_id = _identifier(row["runId"], label="run id")
    references = _exact(
        row["artifacts"],
        {
            "candidateAuthority",
            "catalog",
            "containerCrashAudit",
            "durableTimeline",
            "frameworkTrajectory",
            "providerCounters",
        },
        label="artifact set",
    )
    root = bundle_path.parent
    artifacts = {
        name: _artifact(root, reference, label=name)
        for name, reference in references.items()
    }
    candidate = _candidate(
        artifacts["candidateAuthority"],
        run_id,
        expected_image_id=expected_image_id,
        expected_source_commit=expected_source_commit,
    )
    containers = _crash_audit(artifacts["containerCrashAudit"], run_id, candidate)
    catalog = _catalog(artifacts["catalog"], run_id, containers)
    binding, trajectory_violations = _trajectory(
        artifacts["frameworkTrajectory"], run_id, containers, catalog
    )
    timeline_binding, timeline_violations = _timeline(
        artifacts["durableTimeline"], run_id, containers, binding
    )
    provider_violations = _provider(
        artifacts["providerCounters"],
        run_id,
        containers,
        catalog,
        binding,
        timeline_binding,
    )
    runtime_violations = sorted(
        set(trajectory_violations + timeline_violations + provider_violations)
    )
    runtime_status = "runtime_passed" if not runtime_violations else "runtime_failed"
    source_status, source_violations = _source_status(
        row["sourceAuthority"], root, source_root, candidate["sourceCommit"]
    )
    if runtime_violations or source_violations:
        replacement_status = "failed"
    elif source_status == "source_authority_pending":
        replacement_status = "source_authority_pending"
    else:
        replacement_status = "replacement_passed"
    return {
        "contract": RESULT_CONTRACT,
        "replacementStatus": replacement_status,
        "runId": run_id,
        "runtimeStatus": runtime_status,
        "runtimeViolations": runtime_violations,
        "sourceAuthorityStatus": source_status,
        "sourceAuthorityViolations": source_violations,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("--expected-image-id", required=True)
    parser.add_argument("--expected-source-commit", required=True)
    parser.add_argument("--source-root", type=Path)
    arguments = parser.parse_args()
    try:
        result = check(
            arguments.bundle,
            expected_image_id=arguments.expected_image_id,
            expected_source_commit=arguments.expected_source_commit,
            source_root=arguments.source_root,
        )
    except (OSError, ValueError, Tool01V2Error) as error:
        print(
            json.dumps(
                {"error": str(error), "replacementStatus": "failed"}, sort_keys=True
            )
        )
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["replacementStatus"] != "failed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
