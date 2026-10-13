#!/usr/bin/env python3
"""Run one bounded upstream DBBench development case through Eva.

This speaks AgentRL's current structured-message protocol and deliberately
supports only the separately provisioned external-MySQL development task.  It
does not produce or claim an official AgentBench score.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import ipaddress
import json
import os
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from eva_agentbench_client import (
    AgentBenchEvaError,
    EvaAgentBenchClient,
    EvaToolFreeAgentBenchClient,
)


TASK_NAME = "dbbench-dev-external-mysql"
MAX_HTTP_BYTES = 2 * 1024 * 1024
MAX_MODEL_TURNS = 15
MODEL_MINIMUM_INTERVAL_SECONDS = 5.0


class DbBenchDevelopmentError(ValueError):
    """The bounded development run cannot safely continue."""


def _controller_base(value: str) -> str:
    parsed = urlsplit(value)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as error:
        raise DbBenchDevelopmentError("controller must use a literal IP address") from error
    if (
        parsed.scheme != "http"
        or not (address.is_loopback or address.is_private)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/") != "/api"
    ):
        raise DbBenchDevelopmentError("controller is not a private AgentRL API")
    return value.rstrip("/")


def _post(
    base: str,
    path: str,
    payload: dict[str, object],
    *,
    session_id: int | None = None,
) -> tuple[dict[str, Any], Any]:
    body = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request_headers = {"Accept": "application/json", "Content-Type": "application/json"}
    if session_id is not None:
        request_headers["Session_id"] = str(session_id)
    request = Request(
        f"{base}/{path}",
        data=body,
        method="POST",
        headers=request_headers,
    )
    try:
        with urlopen(request, timeout=120.0) as response:  # noqa: S310 -- base is validated above.
            raw = response.read(MAX_HTTP_BYTES + 1)
            headers = response.headers
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise DbBenchDevelopmentError(f"AgentRL {path} failed") from error
    if len(raw) > MAX_HTTP_BYTES:
        raise DbBenchDevelopmentError("AgentRL response is too large")
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise DbBenchDevelopmentError("AgentRL response is not JSON") from error
    if not isinstance(decoded, dict):
        raise DbBenchDevelopmentError("AgentRL response shape is invalid")
    return decoded, headers


def _messages_and_tools(
    payload: dict[str, Any],
    *,
    allow_empty_messages: bool = False,
    allow_missing_tools: bool = False,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    messages = payload.get("messages")
    tools = payload.get("tools")
    if (
        not isinstance(messages, list)
        or (not messages and not allow_empty_messages)
        or any(not isinstance(item, dict) for item in messages)
        or (
            tools is not None
            and (
                not isinstance(tools, list)
                or any(not isinstance(item, dict) for item in tools)
            )
        )
        or (tools is None and not allow_missing_tools)
    ):
        raise DbBenchDevelopmentError("AgentRL messages or tools are invalid")
    return messages, tools or []


def _validated_output_parent(path: Path) -> Path:
    if path.exists() or path.is_symlink():
        raise DbBenchDevelopmentError("output already exists")
    parent = path.parent.resolve(strict=True)
    if not parent.is_dir():
        raise DbBenchDevelopmentError("output parent is invalid")
    return parent


def _write_new_private_json(path: Path, payload: dict[str, object]) -> None:
    parent = _validated_output_parent(path)
    data = json.dumps(
        payload,
        ensure_ascii=False,
        allow_nan=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    descriptor = os.open(
        parent / path.name,
        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
        0o600,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            (parent / path.name).unlink()
        except FileNotFoundError:
            pass
        raise


def run_one_case(
    *,
    controller: str,
    case_index: int,
    output: Path,
    client: EvaAgentBenchClient | None = None,
) -> dict[str, object]:
    """Run exactly one development case and write its complete trajectory."""

    if not 0 <= case_index < 60:
        raise DbBenchDevelopmentError("case index is outside the sealed dev split")
    _validated_output_parent(output)
    base = _controller_base(controller)
    agent = client or EvaToolFreeAgentBenchClient(
        minimum_interval_seconds=MODEL_MINIMUM_INTERVAL_SECONDS,
        timeout_seconds=120.0,
    )
    initial, headers = _post(base, "start_sample", {"name": TASK_NAME, "index": case_index})
    session_header = headers.get("Session_id")
    if not isinstance(session_header, str) or not session_header.isdecimal():
        raise DbBenchDevelopmentError("AgentRL did not bind a numeric session")
    session_id = int(session_header)
    try:
        messages, tools = _messages_and_tools(initial)
    except DbBenchDevelopmentError as error:
        startup_artifact: dict[str, object] = {
            "caseIndex": case_index,
            "contract": "EvaAgentBenchDbBenchDevelopmentStartFailure/v1",
            "failedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "failure": "AgentRL task initialization returned no usable messages",
            "finish": initial.get("finish"),
            "modelCalls": 0,
            "nonClaims": [
                "No model call, SQL operation or DBBench score was produced.",
                "The response body and hidden answer fields are not stored.",
                "The external-MySQL environment is not the official Docker driver.",
            ],
            "responseFields": sorted(str(key) for key in initial),
            "status": initial.get("status"),
            "task": TASK_NAME,
        }
        _write_new_private_json(output, startup_artifact)
        raise DbBenchDevelopmentError(
            "AgentRL DBBench task initialization failed"
        ) from error
    transcript: list[dict[str, Any]] = list(messages)
    trajectory: list[dict[str, object]] = []
    model_failures: list[dict[str, object]] = []
    final: dict[str, Any] | None = None

    for turn_number in range(1, MAX_MODEL_TURNS + 1):
        try:
            assistant = agent.inference_message(transcript, tools)
        except AgentBenchEvaError as error:
            model_failures.append({"failure": str(error), "turn": turn_number})
            validation_notifier = getattr(agent, "note_validation_failure", None)
            if callable(validation_notifier):
                validation_notifier(str(error))
            if getattr(agent, "turns_used", MAX_MODEL_TURNS) < MAX_MODEL_TURNS:
                # No AgentRL interact or SQL call has happened. A new provider
                # attempt is therefore safe, remains inside the prepaid call
                # budget, and is recorded rather than hidden.
                continue
            failure_artifact: dict[str, object] = {
                "caseIndex": case_index,
                "contract": "EvaAgentBenchDbBenchDevelopmentFailure/v1",
                "failedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "failedTurn": turn_number,
                "failure": str(error),
                "initialMessages": messages,
                "modelFailures": model_failures,
                "modelReceipts": getattr(agent, "model_receipts", []),
                "modelTurnLimit": MAX_MODEL_TURNS,
                "modelTurnsCompleted": len(trajectory),
                "nonClaims": [
                    "No DBBench score was produced.",
                    "This failure artifact contains no hidden answer key.",
                    "The external-MySQL development environment is not the official Docker driver.",
                ],
                "sealedToolsSha256": hashlib.sha256(
                    json.dumps(
                        tools,
                        ensure_ascii=False,
                        allow_nan=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                "task": TASK_NAME,
                "trajectory": trajectory,
            }
            _write_new_private_json(output, failure_artifact)
            raise DbBenchDevelopmentError(
                f"Eva failed the sealed DBBench turn: {error}"
            ) from error
        response, _response_headers = _post(
            base,
            "interact",
            {"messages": [assistant]},
            session_id=session_id,
        )
        environment_messages, returned_tools = _messages_and_tools(
            response,
            allow_empty_messages=response.get("finish") is True,
            allow_missing_tools=True,
        )
        if returned_tools and returned_tools != tools:
            raise DbBenchDevelopmentError("AgentRL changed the sealed tool schemas")
        transcript.append(assistant)
        transcript.extend(environment_messages)
        final = response
        trajectory.append(
            {
                "assistant": assistant,
                "environment_messages": environment_messages,
                "finish": response.get("finish"),
                "metric": response.get("metric", response.get("metrics")),
                "reward": response.get("reward"),
                "result": response.get("result"),
                "status": response.get("status"),
                "turn": turn_number,
            }
        )
        if response.get("finish") is True:
            break

    if final is None or final.get("finish") is not True:
        raise DbBenchDevelopmentError("DBBench exceeded the sealed model-turn budget")
    artifact: dict[str, object] = {
        "caseIndex": case_index,
        "contract": "EvaAgentBenchDbBenchDevelopmentRun/v1",
        "controllerProtocol": "AgentRL structured messages with Session_id header",
        "finishedAt": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "initialMessages": messages,
        "modelFailures": model_failures,
        "modelTurnLimit": MAX_MODEL_TURNS,
        "modelMinimumIntervalSeconds": MODEL_MINIMUM_INTERVAL_SECONDS,
        "modelRoute": (
            "isolated_tool_free_sidecar"
            if isinstance(agent, EvaToolFreeAgentBenchClient)
            else "injected_test_client"
        ),
        "modelReceipts": getattr(agent, "model_receipts", []),
        "modelTurnsReservedBeforeIo": True,
        "modelTurnsUsed": getattr(agent, "turns_used", len(trajectory)),
        "nonClaims": [
            "This is not an official AgentBench score.",
            "The disposable external-MySQL environment differs from AgentBench's official Docker driver.",
            "One development case does not establish benchmark quality or leaderboard rank.",
            "The model sidecar has no Eva database, memory, house, Telegram, or connector mount.",
        ],
        "result": {
            "finish": final.get("finish"),
            "metric": final.get("metric", final.get("metrics")),
            "reward": final.get("reward"),
            "taskResult": final.get("result"),
            "status": final.get("status"),
        },
        "sealedTools": tools,
        "sealedToolsSha256": hashlib.sha256(
            json.dumps(
                tools,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest(),
        "task": TASK_NAME,
        "trajectory": trajectory,
    }
    _write_new_private_json(output, artifact)
    return artifact


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--controller", required=True)
    parser.add_argument("--case-index", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        artifact = run_one_case(
            controller=args.controller,
            case_index=args.case_index,
            output=args.output,
        )
    except DbBenchDevelopmentError as error:
        print(json.dumps({"status": "failed", "error": str(error)}, sort_keys=True))
        return 2
    print(
        json.dumps(
            {
                "caseIndex": artifact["caseIndex"],
                "finish": artifact["result"]["finish"],
                "modelTurnsUsed": artifact["modelTurnsUsed"],
                "status": artifact["result"]["status"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
