#!/usr/bin/env python3
"""Validate TOOL01 runtime evidence without trusting a claimed pass flag."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
from typing import Any


BUNDLE_CONTRACT = "EvaTool01AcceptanceBundle/v1"
CATALOG_CONTRACT = "EvaTool01RuntimeCatalog/v1"
TRAJECTORY_CONTRACT = "EvaTool01FrameworkTrajectory/v1"
LEDGER_CONTRACT = "EvaTool01DurableLedger/v1"
PROVIDER_CONTRACT = "EvaTool01ProviderAudit/v1"
RESTART_CONTRACT = "EvaTool01RestartAudit/v1"
SOURCE_CONTRACT = "EvaTool01SourceAuthority/v1"
RESULT_CONTRACT = "EvaTool01AcceptanceResult/v1"

PROMPT = "Ik zit nu in de overkapping en het is hier koud."
MODEL = "gpt-5.4-mini"
REASONING = "medium"
BUDGET = {
    "modelRequests": 8,
    "outputTokens": 1_500,
    "toolCalls": 8,
    "totalTokens": 8_000,
    "wallSeconds": 120,
}
ENTRYPOINT = {
    "path": "src/eva/adapters/model/pydantic_conversation.py",
    "symbol": "PydanticConversationModel",
}
REQUIRED_RETIRED_PATHS = {
    "src/eva/adapters/model/openai_responses_transport.py",
}
REQUIRED_RETIRED_SYMBOLS = {
    "eva.adapters.model.conversation:CodexConversationModel",
    "eva.adapters.model.openai_responses_transport:OpenAIResponsesTransport",
    "eva.adapters.model.openai_responses_transport:parse_conversation_sse",
}

_DIGEST = re.compile(r"^[0-9a-f]{64}$")
_GIT_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_IDENTIFIER = re.compile(r"^[A-Za-z][A-Za-z0-9_.:-]{0,159}$")
_NONCE = re.compile(r"^[0-9a-f]{12}$")
_MAX_ARTIFACT_BYTES = 1_000_000


class Tool01Error(RuntimeError):
    """The authority or an artifact is malformed or untrusted."""


def _sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _exact(value: object, keys: set[str], *, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise Tool01Error(f"{label} fields differ")
    return value


def _text(value: object, *, label: str, limit: int = 512) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > limit
    ):
        raise Tool01Error(f"{label} is invalid")
    return value


def _identifier(value: object, *, label: str) -> str:
    result = _text(value, label=label, limit=160)
    if _IDENTIFIER.fullmatch(result) is None:
        raise Tool01Error(f"{label} is invalid")
    return result


def _path(value: object, *, label: str) -> str:
    result = _text(value, label=label, limit=512)
    candidate = PurePosixPath(result)
    if (
        candidate.is_absolute()
        or ".." in candidate.parts
        or result != candidate.as_posix()
    ):
        raise Tool01Error(f"{label} is unsafe")
    return result


def _reject_pass_claim(value: object) -> None:
    if isinstance(value, dict):
        if "passed" in value or "allPassed" in value:
            raise Tool01Error("self-asserted pass fields are forbidden")
        for item in value.values():
            _reject_pass_claim(item)
    elif isinstance(value, list):
        for item in value:
            _reject_pass_claim(item)


def _read_json(path: Path, *, label: str) -> tuple[dict[str, Any], bytes]:
    raw = path.read_bytes()
    if not raw or len(raw) > _MAX_ARTIFACT_BYTES:
        raise Tool01Error(f"{label} size is invalid")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as error:
        raise Tool01Error(f"{label} is invalid JSON") from error
    if not isinstance(value, dict):
        raise Tool01Error(f"{label} must be an object")
    _reject_pass_claim(value)
    return value, raw


def _artifact(bundle_root: Path, reference: object, *, label: str) -> dict[str, Any]:
    row = _exact(reference, {"bytes", "path", "sha256"}, label=f"{label} reference")
    relative = _path(row["path"], label=f"{label} path")
    digest = _text(row["sha256"], label=f"{label} sha256", limit=64)
    byte_length = row["bytes"]
    if (
        _DIGEST.fullmatch(digest) is None
        or not isinstance(byte_length, int)
        or isinstance(byte_length, bool)
        or byte_length <= 0
    ):
        raise Tool01Error(f"{label} reference is invalid")
    root = bundle_root.resolve()
    target = (bundle_root / relative).resolve(strict=True)
    if not target.is_relative_to(root):
        raise Tool01Error(f"{label} path escapes the bundle")
    value, raw = _read_json(target, label=label)
    if len(raw) != byte_length or _sha256(raw) != digest:
        raise Tool01Error(f"{label} binding differs")
    return value


def _sequence(value: object, *, label: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise Tool01Error(f"{label} sequence is invalid")
    return value


def _validate_header(
    value: dict[str, Any], contract: str, run_id: str, *, label: str
) -> None:
    if value.get("contract") != contract or value.get("runId") != run_id:
        raise Tool01Error(f"{label} authority differs")


def _catalog(value: dict[str, Any], run_id: str) -> dict[str, Any]:
    row = _exact(
        value,
        {
            "contract",
            "discoveredByProcess",
            "discoverySequence",
            "nonce",
            "providers",
            "runId",
            "staticSourceScan",
        },
        label="catalog",
    )
    _validate_header(row, CATALOG_CONTRACT, run_id, label="catalog")
    nonce = _text(row["nonce"], label="catalog nonce", limit=12)
    if _NONCE.fullmatch(nonce) is None:
        raise Tool01Error("catalog nonce is invalid")
    providers = row["providers"]
    if not isinstance(providers, list) or len(providers) != 2:
        raise Tool01Error("catalog must contain exactly two providers")
    tools: dict[str, dict[str, str]] = {}
    roles: list[str] = []
    provider_ids: list[str] = []
    for provider in providers:
        provider_row = _exact(provider, {"providerId", "tools"}, label="provider")
        provider_id = _identifier(provider_row["providerId"], label="provider id")
        provider_ids.append(provider_id)
        provider_tools = provider_row["tools"]
        if not isinstance(provider_tools, list) or not provider_tools:
            raise Tool01Error("provider tool list is invalid")
        for tool in provider_tools:
            tool_row = _exact(
                tool,
                {"description", "inputSchema", "name", "role"},
                label="catalog tool",
            )
            name = _identifier(tool_row["name"], label="tool name")
            role = _text(tool_row["role"], label="tool role", limit=32)
            _text(tool_row["description"], label="tool description", limit=4_096)
            if not isinstance(tool_row["inputSchema"], dict) or not name.endswith(
                f"_{nonce}"
            ):
                raise Tool01Error("runtime tool schema or nonce binding is invalid")
            if name in tools:
                raise Tool01Error("runtime tool name is duplicated")
            tools[name] = {"providerId": provider_id, "role": role}
            roles.append(role)
    if len(set(provider_ids)) != 2 or sorted(roles) != [
        "distractor",
        "effect",
        "observe",
        "readback",
    ]:
        raise Tool01Error("runtime provider roles differ")
    scan = _exact(
        row["staticSourceScan"], {"matches", "searchedToolNames"}, label="source scan"
    )
    if scan["searchedToolNames"] != sorted(tools) or scan["matches"] != []:
        raise Tool01Error("runtime tool names were not proven absent before discovery")
    discovered_by = _identifier(row["discoveredByProcess"], label="discovery process")
    return {
        "discoveryProcess": discovered_by,
        "discoverySequence": _sequence(row["discoverySequence"], label="discovery"),
        "providerIds": set(provider_ids),
        "tools": tools,
    }


def _events(value: object, *, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise Tool01Error(f"{label} events are invalid")
    result: list[dict[str, Any]] = []
    previous = 0
    for event in value:
        row = _exact(
            event, {"data", "kind", "processId", "sequence"}, label=f"{label} event"
        )
        sequence = _sequence(row["sequence"], label=label)
        if sequence <= previous:
            raise Tool01Error(f"{label} sequences are not increasing")
        previous = sequence
        if not isinstance(row["data"], dict):
            raise Tool01Error(f"{label} event data is invalid")
        result.append(row)
    return result


def _event(events: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    matches = [row for row in events if row["kind"] == kind]
    if len(matches) != 1:
        raise Tool01Error(f"trajectory must contain exactly one {kind}")
    return matches[0]


def _runtime_violations(
    catalog: dict[str, Any],
    trajectory: dict[str, Any],
    ledger: dict[str, Any],
    provider: dict[str, Any],
    restart: dict[str, Any],
    run_id: str,
) -> list[str]:
    violations: list[str] = []
    trajectory_row = _exact(
        trajectory,
        {"budget", "contract", "events", "model", "prompt", "reasoning", "runId"},
        label="trajectory",
    )
    _validate_header(trajectory_row, TRAJECTORY_CONTRACT, run_id, label="trajectory")
    if trajectory_row["prompt"] != PROMPT:
        violations.append("prompt_differs")
    if trajectory_row["model"] != MODEL or trajectory_row["reasoning"] != REASONING:
        violations.append("model_authority_differs")
    if trajectory_row["budget"] != BUDGET:
        violations.append("budget_differs")
    if any(name in trajectory_row["prompt"] for name in catalog["tools"]):
        violations.append("prompt_contains_runtime_tool_name")
    events = _events(trajectory_row["events"], label="trajectory")
    allowed_kinds = {
        "action_port_execute",
        "catalog_discovered",
        "deferred_requests",
        "deferred_results",
        "final_answer",
        "process_killed",
        "process_started",
        "replay_received",
        "replay_result",
    }
    if any(row["kind"] not in allowed_kinds for row in events):
        violations.append("unexpected_framework_event")
    starts = [row for row in events if row["kind"] == "process_started"]
    if [row["processId"] for row in starts] != ["p1", "p2", "p3"]:
        violations.append("process_sequence_differs")
    deferred = _event(events, "deferred_requests")
    action = _event(events, "action_port_execute")
    results = _event(events, "deferred_results")
    killed = _event(events, "process_killed")
    replay_received = _event(events, "replay_received")
    replay_result = _event(events, "replay_result")
    effect_name = deferred["data"].get("toolName")
    effect_spec = catalog["tools"].get(effect_name, {})
    if effect_spec.get("role") != "effect":
        violations.append("model_did_not_select_effect")
    binding_fields = ("callId", "messageId", "operationId", "toolName")
    if any(
        action["data"].get(field) != deferred["data"].get(field)
        for field in binding_fields
    ):
        violations.append("action_port_binding_differs")
    if action["data"].get("owner") != "ConversationActionPort":
        violations.append("provider_io_bypassed_action_port")
    if (
        results["data"].get("inputKind") != "DeferredToolResults"
        or results["data"].get("receiptSource") != "durable_ledger"
    ):
        violations.append("durable_deferred_result_missing")
    if results["data"].get("historySha256") != deferred["data"].get("historySha256"):
        violations.append("framework_history_differs")
    if (
        killed["processId"] != "p1"
        or killed["data"].get("phase") != "after_effect_before_readback"
    ):
        violations.append("restart_window_differs")
    if replay_received["processId"] != "p3" or replay_result["processId"] != "p3":
        violations.append("replay_process_differs")
    for field in ("messageId", "operationId"):
        if replay_received["data"].get(field) != deferred["data"].get(field):
            violations.append(f"replay_{field}_differs")

    ledger_row = _exact(ledger, {"contract", "events", "runId"}, label="ledger")
    _validate_header(ledger_row, LEDGER_CONTRACT, run_id, label="ledger")
    ledger_events = _events(ledger_row["events"], label="ledger")
    expected_types = [
        "TOOL_REQUEST_ADMITTED",
        "PROVIDER_CALL_STARTED",
        "PROVIDER_ACCEPTED",
        "READBACK_CALL_STARTED",
        "STATE_VERIFIED",
    ]
    if [row["kind"] for row in ledger_events] != expected_types:
        violations.append("ledger_event_sequence_differs")
    admission = ledger_events[0]
    admission_payload = {
        "callId": admission["data"].get("callId"),
        "messageId": admission["data"].get("messageId"),
        "operationId": admission["data"].get("operationId"),
        "toolName": admission["data"].get("toolName"),
    }
    admission_sha256 = _sha256(
        (
            json.dumps(admission_payload, sort_keys=True, separators=(",", ":")) + "\n"
        ).encode()
    )
    if admission["data"].get("eventSha256") != admission_sha256:
        violations.append("admission_hash_differs")
    if any(
        admission_payload.get(field) != deferred["data"].get(field)
        for field in binding_fields
    ):
        violations.append("ledger_admission_binding_differs")

    provider_row = _exact(
        provider,
        {"calls", "contract", "finalState", "runId"},
        label="provider audit",
    )
    _validate_header(provider_row, PROVIDER_CONTRACT, run_id, label="provider audit")
    calls = _events(provider_row["calls"], label="provider audit")
    if [row["kind"] for row in calls] != ["effect", "readback"]:
        violations.append("provider_call_sequence_differs")
    else:
        effect, readback = calls
        if effect["processId"] != "p1" or readback["processId"] != "p2":
            violations.append("provider_restart_boundary_differs")
        if (
            effect["data"].get("toolName") != effect_name
            or catalog["tools"].get(readback["data"].get("toolName"), {}).get("role")
            != "readback"
        ):
            violations.append("provider_tool_selection_differs")
        if effect["data"].get("callId") == readback["data"].get("callId"):
            violations.append("readback_is_not_separate")
        for field in ("messageId", "operationId"):
            expected = deferred["data"].get(field)
            if (
                effect["data"].get(field) != expected
                or readback["data"].get(field) != expected
            ):
                violations.append(f"provider_{field}_differs")
        if effect["data"].get("admissionEventSha256") != admission_sha256:
            violations.append("provider_admission_attestation_differs")
        if not (
            admission["sequence"]
            < ledger_events[1]["sequence"]
            < effect["sequence"]
            < ledger_events[2]["sequence"]
            < killed["sequence"]
            < starts[1]["sequence"]
            < ledger_events[3]["sequence"]
            < readback["sequence"]
            < ledger_events[4]["sequence"]
            < results["sequence"]
        ):
            violations.append("durable_execution_order_differs")
        if any(call["processId"] == "p3" for call in calls):
            violations.append("replay_called_provider")
        if any(
            catalog["tools"].get(call["data"].get("toolName"), {}).get("role")
            == "distractor"
            for call in calls
        ):
            violations.append("distractor_provider_mutated")
    if provider_row["finalState"] != {
        "comfortActive": True,
        "space": "overkapping",
        "temperatureC": 20.0,
    }:
        violations.append("final_state_differs")

    restart_row = _exact(
        restart, {"contract", "processes", "runId"}, label="restart audit"
    )
    _validate_header(restart_row, RESTART_CONTRACT, run_id, label="restart audit")
    processes = restart_row["processes"]
    expected_processes = [
        {
            "processId": "p1",
            "startSequence": 1,
            "stopKind": "killed",
            "stopSequence": killed["sequence"],
        },
        {
            "processId": "p2",
            "startSequence": starts[1]["sequence"],
            "stopKind": "clean",
            "stopSequence": _event(events, "final_answer")["sequence"],
        },
        {
            "processId": "p3",
            "startSequence": starts[2]["sequence"],
            "stopKind": "clean",
            "stopSequence": replay_result["sequence"],
        },
    ]
    if processes != expected_processes:
        violations.append("restart_audit_differs")
    if catalog["discoveryProcess"] != "p1" or not (
        starts[0]["sequence"] < catalog["discoverySequence"]
    ):
        violations.append("catalog_was_not_runtime_discovered")
    catalog_events = [row for row in events if row["kind"] == "catalog_discovered"]
    if (
        not catalog_events
        or catalog_events[0]["processId"] != "p1"
        or catalog_events[0]["sequence"] != catalog["discoverySequence"]
    ):
        violations.append("catalog_trajectory_binding_differs")
    return sorted(set(violations))


def _source_violations(value: dict[str, Any], run_id: str) -> list[str]:
    row = _exact(
        value,
        {
            "contract",
            "gitCommit",
            "retiredPaths",
            "retiredSymbols",
            "runId",
            "scan",
            "toolLoopEntrypoint",
        },
        label="source authority",
    )
    _validate_header(row, SOURCE_CONTRACT, run_id, label="source authority")
    violations: list[str] = []
    if (
        not isinstance(row["gitCommit"], str)
        or _GIT_COMMIT.fullmatch(row["gitCommit"]) is None
    ):
        raise Tool01Error("source git commit is invalid")
    entrypoint = _exact(
        row["toolLoopEntrypoint"],
        {"path", "sha256", "symbol"},
        label="tool loop entrypoint",
    )
    if (
        entrypoint.get("path") != ENTRYPOINT["path"]
        or entrypoint.get("symbol") != ENTRYPOINT["symbol"]
        or _DIGEST.fullmatch(str(entrypoint.get("sha256"))) is None
    ):
        violations.append("tool_loop_entrypoint_differs")
    retired_paths = row["retiredPaths"]
    retired_symbols = row["retiredSymbols"]
    valid_retired_paths = (
        isinstance(retired_paths, list)
        and all(isinstance(item, str) for item in retired_paths)
        and retired_paths == sorted(set(retired_paths))
    )
    if not valid_retired_paths or not REQUIRED_RETIRED_PATHS.issubset(retired_paths):
        violations.append("retired_paths_incomplete")
    valid_retired_symbols = (
        isinstance(retired_symbols, list)
        and all(isinstance(item, str) for item in retired_symbols)
        and retired_symbols == sorted(set(retired_symbols))
    )
    if not valid_retired_symbols or not REQUIRED_RETIRED_SYMBOLS.issubset(
        retired_symbols
    ):
        violations.append("retired_symbols_incomplete")
    scan = _exact(
        row["scan"],
        {
            "forbiddenPathMatches",
            "forbiddenSymbolMatches",
            "nonEmptyToolLoops",
            "runtimeOwners",
        },
        label="source scan",
    )
    expected_owner = [{"path": ENTRYPOINT["path"], "symbol": ENTRYPOINT["symbol"]}]
    if scan["nonEmptyToolLoops"] != expected_owner:
        violations.append("tool_loop_count_differs")
    if scan["runtimeOwners"] != expected_owner:
        violations.append("runtime_owner_count_differs")
    if scan["forbiddenPathMatches"] != [] or scan["forbiddenSymbolMatches"] != []:
        violations.append("retired_source_still_present")
    return sorted(set(violations))


def check(bundle_path: Path) -> dict[str, Any]:
    bundle, _ = _read_json(bundle_path, label="bundle")
    row = _exact(
        bundle,
        {"artifacts", "contract", "runId", "schemaVersion", "sourceAuthority"},
        label="bundle",
    )
    if row["contract"] != BUNDLE_CONTRACT or row["schemaVersion"] != 1:
        raise Tool01Error("bundle authority differs")
    run_id = _identifier(row["runId"], label="run id")
    references = _exact(
        row["artifacts"],
        {"catalog", "ledger", "providerAudit", "restartAudit", "trajectory"},
        label="artifact set",
    )
    root = bundle_path.parent
    loaded = {
        name: _artifact(root, reference, label=name)
        for name, reference in references.items()
    }
    runtime_violations = _runtime_violations(
        _catalog(loaded["catalog"], run_id),
        loaded["trajectory"],
        loaded["ledger"],
        loaded["providerAudit"],
        loaded["restartAudit"],
        run_id,
    )
    runtime_status = "runtime_passed" if not runtime_violations else "runtime_failed"

    source = _exact(
        row["sourceAuthority"],
        {"artifact", "status"},
        label="source authority reference",
    )
    source_violations: list[str] = []
    if source["status"] == "pending" and source["artifact"] is None:
        source_status = "source_authority_pending"
    elif source["status"] == "complete" and source["artifact"] is not None:
        source_value = _artifact(root, source["artifact"], label="sourceAuthority")
        source_violations = _source_violations(source_value, run_id)
        source_status = (
            "source_authority_passed"
            if not source_violations
            else "source_authority_failed"
        )
    else:
        raise Tool01Error("source authority state is invalid")

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
    arguments = parser.parse_args()
    try:
        result = check(arguments.bundle)
    except (OSError, ValueError, Tool01Error) as error:
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
