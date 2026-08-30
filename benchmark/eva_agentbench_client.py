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
import ipaddress
import json
import os
import re
from threading import Lock
import time
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


MAX_HISTORY_MESSAGES = 128
MAX_HISTORY_BYTES = 512 * 1024
MAX_MESSAGE_BYTES = 128 * 1024
MAX_RESPONSE_BYTES = 256 * 1024
_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
_MAX_TURNS = 15
_DBBENCH_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "execute_sql",
            "description": "Executes a given SQL statement on the database and returns the result.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The SQL query to be executed.",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "commit_final_answer",
            "description": "Commits the final answer after all operations are completed.",
            "parameters": {
                "type": "object",
                "properties": {
                    "answers": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "The list of final answers to commit.",
                    }
                },
                "required": ["answers"],
                "additionalProperties": False,
            },
        },
    },
]


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


def _tool_free_endpoint() -> str:
    endpoint = _required_environment("EVA_AGENTBENCH_TOOL_FREE_ENDPOINT")
    parsed = urlsplit(endpoint)
    try:
        address = ipaddress.ip_address(parsed.hostname or "")
    except ValueError as error:
        raise AgentBenchEvaError(
            "EVA_AGENTBENCH_TOOL_FREE_ENDPOINT must use a literal IP address"
        ) from error
    if (
        parsed.scheme != "http"
        or not (address.is_loopback or address.is_private)
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/v1/generate"
    ):
        raise AgentBenchEvaError(
            "EVA_AGENTBENCH_TOOL_FREE_ENDPOINT is not an isolated model route"
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


def tool_free_answer(
    prompt: str, *, timeout_seconds: float = 120.0
) -> tuple[str, dict[str, object]]:
    """Call the isolated tool-free model sidecar and validate its receipt."""

    if (
        not isinstance(prompt, str)
        or not prompt.strip()
        or len(prompt.encode("utf-8")) > MAX_MESSAGE_BYTES
        or not 1.0 <= timeout_seconds <= 3_600.0
    ):
        raise AgentBenchEvaError("tool-free AgentBench request is invalid")
    body = json.dumps(
        {"contract": "EvaToolFreeModelRequest/v1", "prompt": prompt},
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
    ).encode("utf-8")
    request = Request(
        _tool_free_endpoint(),
        data=body,
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 -- private literal IP is validated above.
            if response.status != 200:
                raise AgentBenchEvaError("tool-free AgentBench request was rejected")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise AgentBenchEvaError("tool-free AgentBench request failed") from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise AgentBenchEvaError("tool-free AgentBench response is too large")
    try:
        payload: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise AgentBenchEvaError("tool-free AgentBench response is not JSON") from error
    expected = {
        "contract",
        "provider",
        "text",
        "text_sha256",
        "timing",
        "tools_exposed",
        "usage",
    }
    if not isinstance(payload, dict) or set(payload) != expected:
        raise AgentBenchEvaError("tool-free AgentBench response shape is invalid")
    text = payload.get("text")
    usage = payload.get("usage")
    timing = payload.get("timing")
    if (
        payload.get("contract") != "EvaToolFreeModelReply/v1"
        or not isinstance(payload.get("provider"), str)
        or not payload["provider"].startswith("chatgpt-codex:")
        or not isinstance(text, str)
        or not text.strip()
        or len(text.encode("utf-8")) > MAX_RESPONSE_BYTES
        or payload.get("text_sha256")
        != hashlib.sha256(text.encode("utf-8")).hexdigest()
        or not isinstance(usage, dict)
        or set(usage)
        != {
            "input_tokens",
            "measurement",
            "model_attempts",
            "output_tokens",
            "tool_calls",
        }
        or payload.get("tools_exposed") != 0
        or (
            usage.get("measurement") == "measured"
            and usage.get("tool_calls") != 0
        )
        or (
            usage.get("measurement") == "unmeasured"
            and any(
                usage.get(field) is not None
                for field in (
                    "input_tokens",
                    "model_attempts",
                    "output_tokens",
                    "tool_calls",
                )
            )
        )
        or usage.get("measurement") not in {"measured", "unmeasured"}
        or not isinstance(timing, dict)
        or set(timing) != {"completed_ms", "first_content_ms"}
    ):
        raise AgentBenchEvaError("tool-free AgentBench response is invalid")
    receipt = {
        "provider": payload["provider"],
        "textSha256": payload["text_sha256"],
        "timing": timing,
        "toolsExposed": 0,
        "usage": usage,
    }
    return text, receipt


def dbbench_tool_message(
    messages: object,
    tools: object,
    *,
    timeout_seconds: float = 120.0,
    turn_limit: int = 2,
    turn_number: int = 1,
    answer_provider: Callable[[str, float], str] | None = None,
) -> dict[str, object]:
    """Return one closed OpenAI-style tool message for upstream DBBench."""

    if (
        not isinstance(messages, list)
        or not 1 <= len(messages) <= MAX_HISTORY_MESSAGES
        or any(
            not isinstance(message, dict)
            or message.get("role") not in {"system", "user", "assistant", "tool"}
            for message in messages
        )
    ):
        raise AgentBenchEvaError("AgentRL DBBench messages are invalid")
    if tools != _DBBENCH_TOOLS:
        raise AgentBenchEvaError("AgentRL DBBench tools differ from the sealed schemas")
    if not 1 <= turn_number <= turn_limit <= _MAX_TURNS:
        raise AgentBenchEvaError("AgentRL DBBench turn position is invalid")
    neutral_transcript: list[dict[str, object]] = []
    for message in messages:
        role = message["role"]
        content = message.get("content")
        if role in {"system", "user"}:
            if not isinstance(content, str) or not content:
                raise AgentBenchEvaError("AgentRL DBBench message content is invalid")
            neutral_transcript.append(
                {
                    "kind": "task_instruction" if role == "system" else "task_question",
                    "text": content,
                }
            )
        elif role == "assistant":
            calls = message.get("tool_calls")
            if content not in (None, "") or not isinstance(calls, list) or len(calls) != 1:
                raise AgentBenchEvaError("AgentRL DBBench assistant message is invalid")
            call = calls[0]
            function = call.get("function") if isinstance(call, dict) else None
            if (
                not isinstance(function, dict)
                or set(function) != {"arguments", "name"}
                or function.get("name") not in {"execute_sql", "commit_final_answer"}
                or not isinstance(function.get("arguments"), str)
            ):
                raise AgentBenchEvaError("AgentRL DBBench operation is invalid")
            try:
                operation_arguments = json.loads(function["arguments"])
            except json.JSONDecodeError as error:
                raise AgentBenchEvaError(
                    "AgentRL DBBench operation arguments are not JSON"
                ) from error
            neutral_transcript.append(
                {
                    "arguments": operation_arguments,
                    "kind": "database_operation",
                    "name": function["name"],
                }
            )
        else:
            if not isinstance(content, str):
                raise AgentBenchEvaError("AgentRL DBBench result is invalid")
            neutral_transcript.append(
                {"kind": "database_result", "result": content}
            )
    try:
        canonical = json.dumps(
            {"messages": messages, "tools": tools},
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError) as error:
        raise AgentBenchEvaError("AgentRL DBBench transcript is not JSON") from error
    neutral = json.dumps(
        {"database_transcript": neutral_transcript},
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    if max(len(canonical.encode("utf-8")), len(neutral.encode("utf-8"))) > MAX_MESSAGE_BYTES:
        raise AgentBenchEvaError("AgentRL DBBench transcript is too large")

    operation_number = 1 + sum(
        message.get("role") == "assistant" for message in messages
    )
    prompt = (
        "Act as the database agent for this DBBench development case. "
        "Return plain JSON text only; never invoke an Eva function or memory. "
        f"This is database operation {operation_number}; provider call "
        f"{turn_number} of at most {turn_limit}. "
        "Prefer one robust SQL statement that answers directly. Source values "
        "can use a different date or text display format than the human question; "
        "normalize or inspect them in SQL instead of blindly copying that display. "
        "Identify the exact answer column or columns from the noun phrase in the "
        "question before writing SQL. Words such as name, tell, or give are verbs, "
        "not a request for a Name or Player column unless the question explicitly "
        "asks for that entity. Select only the requested answer columns; use other "
        "columns only for filtering, grouping, or ordering. Use SUM, COUNT, or AVG "
        "only when the question explicitly asks to combine multiple matching rows, "
        "not merely because a requested column has a numeric name. "
        "After an empty lookup, do not repeat equivalent predicates: inspect the "
        "schema and row count, then search the identifying tokens across every "
        "relevant text column before concluding that the database lacks the answer. "
        "On the final operation, commit only when the observed evidence is sufficient; "
        "never invent a missing answer. Commit each selected answer cell as its own "
        "list item in column order, preserve the database display text, and add no "
        "labels, explanation, units, or prose. For a computed whole number, use the "
        "shortest decimal form without a trailing .0. "
        "Choose exactly one next database operation using either "
        '{"name":"execute_sql","arguments":{"query":"SQL"}} or '
        '{"name":"commit_final_answer","arguments":{"answers":["answer"]}}. '
        "Use only the neutral transcript below.\n"
        f"{neutral}"
    )
    answer = (
        answer_provider(prompt, timeout_seconds)
        if answer_provider is not None
        else answer_for_history(
            [{"role": "user", "content": prompt}],
            timeout_seconds=timeout_seconds,
        )
    )
    try:
        requested = json.loads(answer)
    except json.JSONDecodeError as error:
        raise AgentBenchEvaError("Eva DBBench tool answer is not exact JSON") from error
    if not isinstance(requested, dict) or set(requested) != {"arguments", "name"}:
        raise AgentBenchEvaError("Eva DBBench tool answer shape is invalid")
    name = requested["name"]
    arguments = requested["arguments"]
    if name == "execute_sql":
        if (
            not isinstance(arguments, dict)
            or set(arguments) != {"query"}
            or not isinstance(arguments["query"], str)
            or not arguments["query"].strip()
            or len(arguments["query"].encode("utf-8")) > 64 * 1024
        ):
            raise AgentBenchEvaError("Eva DBBench SQL arguments are invalid")
    elif name == "commit_final_answer":
        if (
            not isinstance(arguments, dict)
            or set(arguments) != {"answers"}
            or not isinstance(arguments["answers"], list)
            or not 1 <= len(arguments["answers"]) <= 64
            or any(
                not isinstance(item, str)
                or not item
                or len(item.encode("utf-8")) > 16 * 1024
                for item in arguments["answers"]
            )
        ):
            raise AgentBenchEvaError("Eva DBBench final-answer arguments are invalid")
    else:
        raise AgentBenchEvaError("Eva selected an unsealed DBBench tool")

    argument_json = json.dumps(
        arguments,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    call_seed = f"{canonical}\n{answer}".encode("utf-8")
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {
                "id": f"call_{hashlib.sha256(call_seed).hexdigest()[:24]}",
                "type": "function",
                "function": {"name": name, "arguments": argument_json},
            }
        ],
    }


class EvaAgentBenchClient:
    """Duck-typed upstream AgentBench client, configured only by environment."""

    def __init__(
        self,
        *,
        minimum_interval_seconds: float = 0.0,
        timeout_seconds: float = 120.0,
        **_unused: object,
    ) -> None:
        if not 0.0 <= minimum_interval_seconds <= 60.0:
            raise AgentBenchEvaError("AgentBench Eva call interval is invalid")
        self.minimum_interval_seconds = minimum_interval_seconds
        self.timeout_seconds = timeout_seconds
        self.model_name = "eva2-isolated-candidate"
        self._turn_limit = _turn_limit()
        self._turns_used = 0
        self._turn_lock = Lock()
        self._last_call_started: float | None = None

    @property
    def turns_used(self) -> int:
        """Return the number of provider calls reserved before I/O."""

        with self._turn_lock:
            return self._turns_used

    def _reserve_turn(self) -> int:
        with self._turn_lock:
            if self._turns_used >= self._turn_limit:
                raise AgentBenchEvaError("AgentBench Eva turn budget is exhausted")
            # Reserve before network I/O: an interrupted request must not turn
            # an explicit model budget into an unbounded retry loop.
            self._turns_used += 1
            now = time.monotonic()
            if self._last_call_started is not None:
                remaining = self.minimum_interval_seconds - (
                    now - self._last_call_started
                )
                if remaining > 0:
                    time.sleep(remaining)
            self._last_call_started = time.monotonic()
            return self._turns_used

    def inference(self, history: list[dict[str, str]]) -> str:
        self._reserve_turn()
        return answer_for_history(history, timeout_seconds=self.timeout_seconds)

    def inference_message(
        self, messages: object, tools: object
    ) -> dict[str, object]:
        turn_number = self._reserve_turn()
        return dbbench_tool_message(
            messages,
            tools,
            timeout_seconds=self.timeout_seconds,
            turn_limit=self._turn_limit,
            turn_number=turn_number,
        )


class EvaToolFreeAgentBenchClient(EvaAgentBenchClient):
    """AgentBench client backed by the isolated no-tools model sidecar."""

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)
        self.model_receipts: list[dict[str, object]] = []

    def inference_message(
        self, messages: object, tools: object
    ) -> dict[str, object]:
        turn_number = self._reserve_turn()

        def provide(prompt: str, timeout_seconds: float) -> str:
            answer, receipt = tool_free_answer(
                prompt, timeout_seconds=timeout_seconds
            )
            self.model_receipts.append({"turn": turn_number, **receipt})
            return answer

        return dbbench_tool_message(
            messages,
            tools,
            timeout_seconds=self.timeout_seconds,
            turn_limit=self._turn_limit,
            turn_number=turn_number,
            answer_provider=provide,
        )


__all__ = [
    "AgentBenchEvaError",
    "EvaAgentBenchClient",
    "EvaToolFreeAgentBenchClient",
    "answer_for_history",
    "dbbench_tool_message",
    "tool_free_answer",
]
