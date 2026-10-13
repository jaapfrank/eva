"""Read explicit direct-journal exports; never equate stop with quiescence."""

from __future__ import annotations

import ast
from pathlib import Path
from uuid import UUID

SOURCES = (
    "eva2/src/eva/application/conversation.py",
    "eva2/src/eva/adapters/postgres/conversation_journal.py",
    "eva2/src/eva/adapters/model/pydantic_conversation.py",
    "eva2/src/eva/adapters/model/openai_codex_provider.py",
)
FIELDS = ("model_attempts", "tool_calls", "input_tokens", "output_tokens")
IDENTITY = ("principal_id", "conversation_id", "message_id")
EVENTS = (
    "CONVERSATION_MODEL_ADMITTED",
    "CONVERSATION_MODEL_RECORDED",
    "CONVERSATION_STOP_REQUESTED",
)


def inspect_export(value, *, request, sources):
    """Validate row/source binding, then report only observed durable facts.

    Snapshot completeness and authenticity are not inferred from supplied JSON.
    Runtime values for per-attempt token/tool/deadline limits aren't journaled.
    """
    result = {
        "status": "unknown",
        "maximum_requests": None,
        "admitted_requests": None,
        "terminal_requests": None,
        "unresolved_requests": None,
        "stop_recorded": None,
        "known_direct_usage": None,
        "observed_local_settlement": "unknown",
        "local_stream_closed_requests": None,
        "local_settlement_scope": "exported direct attempts only",
        "remote_provider_quiescent": None,
        "server_quiescent": None,
        "live_enabled": False,
        "provider_attempt_limits": {
            "request_limit": None,
            "tool_calls_limit": None,
            "input_tokens_limit": None,
            "output_tokens_limit": None,
            "deadline_ms": None,
        },
        "blockers": [
            "snapshot_completeness_and_authenticity_unverified",
            "provider_attempt_limits_not_in_durable_events",
            "stop_does_not_attest_provider_quiescence",
            "helper_memory_coverage_unavailable",
        ],
    }
    if value is None:
        result["blockers"].append("direct_journal_export_missing")
        return result
    try:
        if not isinstance(value, dict) or not isinstance(request, dict):
            raise ValueError("invalid_export")
        if not all(sources.get(p) for p in SOURCES) or value.get("source_files") != {
            p: sources[p] for p in SOURCES
        }:
            raise ValueError("source_binding_differs")
        scope = value["scope"]
        if set(scope) != {"tenant_id", "installation_id"} or any(
            str(UUID(v)) != v for v in scope.values()
        ):
            raise ValueError("scope_invalid")
        identity = {k: request[k] for k in IDENTITY}
        if any(not isinstance(v, str) or not v for v in identity.values()):
            raise ValueError("request_identity_invalid")
        rows = value["events"]
        if not isinstance(rows, list):
            raise ValueError("events_invalid")
        admitted, terminal, stops = {}, {}, []
        for row in rows:
            if any(row.get(k) != v for k, v in scope.items()):
                raise ValueError("row_scope_differs")
            kind = row["event_type"]
            data = row["payload"]["data"]
            checkpoint = row["payload"]["checkpoint"]
            expected = {
                "CONVERSATION_MODEL_ADMITTED": ("model_admitted", "active", 1),
                "CONVERSATION_MODEL_RECORDED": ("model_recorded", "completed", 2),
                "CONVERSATION_STOP_REQUESTED": ("stop_requested", "completed", 1),
            }.get(kind)
            if (
                expected is None
                or tuple(checkpoint.get(k) for k in ("phase", "status", "revision"))
                != expected
            ):
                raise ValueError("checkpoint_differs")
            if kind not in EVENTS or any(data.get(k) != v for k, v in identity.items()):
                raise ValueError("row_identity_or_kind_differs")
            if kind == "CONVERSATION_STOP_REQUESTED":
                if stops:
                    raise ValueError("duplicate_stop")
                stops.append(data)
                continue
            attempt = data["request_id"]
            if str(UUID(attempt)) != attempt:
                raise ValueError("attempt_id_invalid")
            target = admitted if kind == "CONVERSATION_MODEL_ADMITTED" else terminal
            if attempt in target:
                raise ValueError("duplicate_attempt_event")
            target[attempt] = data
        if set(terminal) - set(admitted):
            raise ValueError("terminal_without_admission")
        limits = [v.get("maximum_requests") for v in admitted.values()]
        if any(type(v) is not int or v < 1 for v in limits):
            raise ValueError("durable_limit_missing_or_invalid")
        maximum = min(limits) if limits else None
        if maximum is not None and len(admitted) > maximum:
            raise ValueError("durable_limit_exceeded")
        known = {k: 0 for k in FIELDS}
        complete = bool(admitted) and len(admitted) == len(terminal)
        measured_rows = 0
        closed_requests = 0
        for data in terminal.values():
            if "local_stream_closed" in data:
                if data["local_stream_closed"] is not True:
                    raise ValueError("local_close_observation_invalid")
                closed_requests += 1
            if data.get("outcome", "succeeded") not in ("succeeded", "failed"):
                raise ValueError("terminal_outcome_invalid")
            usage = data["usage"]
            measurement = usage["measurement"]
            if measurement == "unmeasured":
                if any(usage.get(k) is not None for k in FIELDS):
                    raise ValueError("unknown_usage_has_values")
                complete = False
                continue
            if (
                measurement not in ("measured", "partial")
                or any(type(usage.get(k)) is not int or usage[k] < 0 for k in FIELDS)
                or usage["model_attempts"] < 1
            ):
                raise ValueError("usage_invalid")
            complete = complete and measurement == "measured"
            measured_rows += 1
            for key in FIELDS:
                known[key] += usage[key]
        result.update(
            status="source_and_rows_bound",
            maximum_requests=maximum,
            admitted_requests=len(admitted),
            terminal_requests=len(terminal),
            unresolved_requests=len(admitted) - len(terminal),
            stop_recorded=bool(stops),
            local_stream_closed_requests=closed_requests,
            observed_local_settlement=(
                "unfinished"
                if len(admitted) != len(terminal)
                else "settled"
                if admitted and closed_requests == len(admitted)
                else "unknown"
            ),
            known_direct_usage={
                "measurement": "measured"
                if complete
                else "partial"
                if measured_rows
                else "unmeasured",
                **(known if measured_rows else {k: None for k in FIELDS}),
            },
        )
        if maximum is None:
            result["blockers"].append("durable_limit_not_yet_recorded")
        if len(admitted) != len(terminal):
            result["blockers"].append("direct_attempts_unresolved")
        if result["observed_local_settlement"] != "settled":
            result["blockers"].append("local_direct_settlement_unverified")
        if not complete:
            result["blockers"].append("complete_direct_usage_unavailable")
    except (KeyError, TypeError, ValueError, AttributeError):
        result["status"] = "rejected"
        # Only our fixed error codes are emitted, never arbitrary export content.
        result["blockers"].append("invalid_direct_journal_export")
    return result


def provider_limit_contract(path: Path):
    """Extract existing SDK limit expressions; do not evaluate runtime config."""
    try:
        tree = ast.parse(path.read_text())
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "UsageLimits"
        ]
        if len(calls) != 1:
            return None
        expressions = {item.arg: ast.unparse(item.value) for item in calls[0].keywords}
        return {
            "scope": "one Agent.run, not the whole request",
            "source_expressions": expressions,
            "runtime_application_verified": False,
        }
    except (OSError, SyntaxError):
        return None
