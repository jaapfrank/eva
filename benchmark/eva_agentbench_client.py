"""Minimal isolated Eva client for AgentBench's upstream ``AgentClient`` slot.

The upstream runner calls ``inference(history)`` and expects precisely the next
agent message.  It does not give an adapter a task identifier, so every call is
made a fresh, deterministic Eva conversation from the complete AgentBench
transcript.  That prevents one benchmark sample from recalling another sample
through Eva's normal memory while still providing the model the full history
that AgentBench supplied.

This module deliberately contains no AgentBench scorer, task selection or
answer key.  It is a development adapter until an exact upstream environment
has produced a separately validated run receipt.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
import re
from threading import Lock
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


MAX_HISTORY_MESSAGES = 128
MAX_HISTORY_BYTES = 512 * 1024
MAX_MESSAGE_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_MAX_TURNS = 8


class AgentBenchEvaError(ValueError):
    """The isolated AgentBench-to-Eva bridge cannot safely continue."""


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise AgentBenchEvaError(f"{name} is required")
    return value


def _turn_endpoint() -> str:
    endpoint = _required_environment("EVA_AGENTBENCH_TURN_ENDPOINT")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in _LOCAL_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/internal/v1/turns/respond"
    ):
        raise AgentBenchEvaError(
            "EVA_AGENTBENCH_TURN_ENDPOINT is not an isolated Eva turn route"
        )
    return endpoint


def _run_id() -> str:
    value = _required_environment("EVA_AGENTBENCH_RUN_ID")
    if not _RUN_ID.fullmatch(value):
        raise AgentBenchEvaError("EVA_AGENTBENCH_RUN_ID is invalid")
    return value


def _turn_limit() -> int:
    raw = _required_environment("EVA_AGENTBENCH_MAX_TURNS")
    try:
        value = int(raw)
    except ValueError as error:
        raise AgentBenchEvaError("EVA_AGENTBENCH_MAX_TURNS is invalid") from error
    if not 1 <= value <= _MAX_TURNS:
        raise AgentBenchEvaError("EVA_AGENTBENCH_MAX_TURNS is invalid")
    return value


def _canonical_history(history: object) -> bytes:
    if not isinstance(history, list) or not 1 <= len(history) <= MAX_HISTORY_MESSAGES:
        raise AgentBenchEvaError("AgentBench history length is invalid")
    messages: list[dict[str, str]] = []
    for message in history:
        if not isinstance(message, dict) or set(message) != {"content", "role"}:
            raise AgentBenchEvaError("AgentBench history shape is invalid")
        role = message["role"]
        content = message["content"]
        if role not in {"user", "agent"} or not isinstance(content, str):
            raise AgentBenchEvaError("AgentBench history message is invalid")
        if not content or len(content.encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise AgentBenchEvaError("AgentBench history message is too large")
        messages.append({"content": content, "role": role})
    raw = json.dumps(
        messages,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if len(raw) > MAX_HISTORY_BYTES:
        raise AgentBenchEvaError("AgentBench history exceeds its byte limit")
    return raw


def _identifier(prefix: str, value: bytes) -> str:
    return f"{prefix}:{hashlib.sha256(value).hexdigest()[:24]}"


def _turn_payload(history: object) -> dict[str, object]:
    canonical_history = _canonical_history(history)
    run_id = _run_id()
    tenant_id = _required_environment("EVA_AGENTBENCH_TENANT_ID")
    installation_id = _required_environment("EVA_AGENTBENCH_INSTALLATION_ID")
    principal_prefix = _required_environment("EVA_AGENTBENCH_PRINCIPAL_PREFIX")
    turn_key = _identifier(f"agentbench:{run_id}:turn", canonical_history)
    principal_id = _identifier(principal_prefix, canonical_history)
    transcript = canonical_history.decode("utf-8")
    return {
        "audience": {
            "audience_id": f"person:{principal_id}",
            "kind": "private_person",
            "principal_id": principal_id,
        },
        "conversation_id": turn_key,
        "installation_id": installation_id,
        "message_id": f"{turn_key}:message:1",
        "occurred_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "principal_id": principal_id,
        "tenant_id": tenant_id,
        "text": (
            "Return only the next assistant message for this AgentBench transcript. "
            "Do not discuss this wrapper or use memories outside the transcript.\n"
            f"{transcript}"
        ),
    }


def answer_for_history(history: object, *, timeout_seconds: float = 120.0) -> str:
    """Send one complete AgentBench interaction to isolated Eva and return its text."""

    if not 1.0 <= timeout_seconds <= 3_600.0:
        raise AgentBenchEvaError("AgentBench Eva timeout is invalid")
    turn = _turn_payload(history)
    body = json.dumps(turn, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = Request(
        _turn_endpoint(),
        data=body,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 -- endpoint is allowlisted above.
            if response.status != 200:
                raise AgentBenchEvaError("Eva AgentBench turn was rejected")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise AgentBenchEvaError("Eva AgentBench turn failed") from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AgentBenchEvaError("Eva AgentBench reply exceeds its byte limit")
    try:
        response_payload: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AgentBenchEvaError("Eva AgentBench reply is not JSON") from error
    if not isinstance(response_payload, dict) or set(response_payload) != {
        "contract",
        "conversation_id",
        "evidence_memory_ids",
        "generated_at",
        "message_id",
        "provider",
        "text",
        "text_sha256",
    }:
        raise AgentBenchEvaError("Eva AgentBench reply differs")
    answer = response_payload.get("text")
    if (
        response_payload.get("contract") != "EvaInternalGroundedReply/v1"
        or response_payload.get("conversation_id") != turn["conversation_id"]
        or response_payload.get("message_id") != turn["message_id"]
        or response_payload.get("evidence_memory_ids") != []
        or not isinstance(answer, str)
        or not answer.strip()
        or len(answer.encode("utf-8")) > MAX_RESPONSE_BYTES
        or response_payload.get("text_sha256")
        != hashlib.sha256(answer.encode("utf-8")).hexdigest()
    ):
        raise AgentBenchEvaError("Eva AgentBench answer is invalid")
    return answer


class EvaAgentBenchClient:
    """Duck-typed upstream AgentBench client, configured only by environment."""

    def __init__(self, *, timeout_seconds: float = 120.0, **_unused: object) -> None:
        self.timeout_seconds = timeout_seconds
        self.model_name = "eva2-isolated-candidate"
        self._turn_limit = _turn_limit()
        self._turns_used = 0
        self._turn_lock = Lock()

    def inference(self, history: list[dict[str, str]]) -> str:
        with self._turn_lock:
            if self._turns_used >= self._turn_limit:
                raise AgentBenchEvaError("AgentBench Eva turn budget is exhausted")
            # Reserve before network I/O: an interrupted request must not turn
            # an explicit model budget into an unbounded retry loop.
            self._turns_used += 1
        return answer_for_history(history, timeout_seconds=self.timeout_seconds)


__all__ = ["AgentBenchEvaError", "EvaAgentBenchClient", "answer_for_history"]
