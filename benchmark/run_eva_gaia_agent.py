#!/usr/bin/env python3
"""Expose one isolated Eva turn through the GAIA development-agent contract.

The parent ``run_gaia_development.py`` supplies only ``task_id``, ``question``
and an optional local attachment path.  This program deliberately ignores the
attachment: Eva's public benchmark route does not yet have a file-grant
adapter.  It sends the question to a locally isolated Eva candidate and emits
the exact ``{"model_answer": "..."}`` object expected by the parent runner.

It is a development adapter, not a GAIA scorer and not a leaderboard client.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
import os
import re
import sys
from typing import Any, NoReturn
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


MAX_QUESTION_BYTES = 65_536
MAX_RESPONSE_BYTES = 256 * 1024
_RUN_ID = re.compile(r"^[a-z0-9][a-z0-9._-]{2,79}$")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class GaiaEvaAgentError(ValueError):
    """The isolated Eva benchmark adapter cannot safely complete a task."""


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise GaiaEvaAgentError(f"{name} is required")
    return value


def _turn_endpoint() -> str:
    endpoint = _required_environment("EVA_GAIA_TURN_ENDPOINT")
    parsed = urlsplit(endpoint)
    hostname = parsed.hostname
    if (
        parsed.scheme not in {"http", "https"}
        or hostname not in _LOCAL_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != "/internal/v1/turns/respond"
    ):
        raise GaiaEvaAgentError("EVA_GAIA_TURN_ENDPOINT is not an isolated Eva turn route")
    return endpoint


def _task(payload: object) -> tuple[str, str]:
    if not isinstance(payload, dict) or set(payload) != {
        "attachment_path",
        "question",
        "task_id",
    }:
        raise GaiaEvaAgentError("GAIA task differs")
    task_id = payload["task_id"]
    question = payload["question"]
    attachment_path = payload["attachment_path"]
    if (
        not isinstance(task_id, str)
        or not task_id
        or len(task_id) > 200
        or not isinstance(question, str)
        or not question.strip()
        or len(question.encode("utf-8")) > MAX_QUESTION_BYTES
        or attachment_path not in (None, "") and not isinstance(attachment_path, str)
    ):
        raise GaiaEvaAgentError("GAIA task values are invalid")
    return task_id, question


def _run_id() -> str:
    value = _required_environment("EVA_GAIA_RUN_ID")
    if not _RUN_ID.fullmatch(value):
        raise GaiaEvaAgentError("EVA_GAIA_RUN_ID is invalid")
    return value


def _identifier(prefix: str, task_id: str) -> str:
    return f"{prefix}:{hashlib.sha256(task_id.encode('utf-8')).hexdigest()[:24]}"


def _turn_payload(task_id: str, question: str) -> dict[str, object]:
    run_id = _run_id()
    tenant_id = _required_environment("EVA_GAIA_TENANT_ID")
    installation_id = _required_environment("EVA_GAIA_INSTALLATION_ID")
    principal_prefix = _required_environment("EVA_GAIA_PRINCIPAL_PREFIX")
    task_key = _identifier(f"gaia:{run_id}:task", task_id)
    principal_id = _identifier(principal_prefix, task_id)
    return {
        "audience": {
            "audience_id": f"person:{principal_id}",
            "kind": "private_person",
            "principal_id": principal_id,
        },
        "conversation_id": task_key,
        "installation_id": installation_id,
        "message_id": f"{task_key}:turn:1",
        "occurred_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "principal_id": principal_id,
        "tenant_id": tenant_id,
        "text": question,
    }


def answer_for_task(payload: object, *, timeout_seconds: float = 120.0) -> str:
    """Submit exactly one private task turn and return only Eva's reply text."""

    if not 1.0 <= timeout_seconds <= 3_600.0:
        raise GaiaEvaAgentError("GAIA Eva timeout is invalid")
    task_id, question = _task(payload)
    turn = _turn_payload(task_id, question)
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
                raise GaiaEvaAgentError("Eva benchmark turn was rejected")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
    except (HTTPError, URLError, TimeoutError, OSError) as error:
        raise GaiaEvaAgentError("Eva benchmark turn failed") from error
    if len(raw) > MAX_RESPONSE_BYTES:
        raise GaiaEvaAgentError("Eva benchmark reply exceeds its byte limit")
    try:
        response_payload: Any = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise GaiaEvaAgentError("Eva benchmark reply is not JSON") from error
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
        raise GaiaEvaAgentError("Eva benchmark reply differs")
    answer = response_payload.get("text")
    if (
        response_payload.get("contract") != "EvaInternalGroundedReply/v1"
        or response_payload.get("conversation_id") != turn["conversation_id"]
        or response_payload.get("message_id") != turn["message_id"]
        or not isinstance(answer, str)
        or not answer.strip()
        or len(answer) > 100_000
        or response_payload.get("text_sha256")
        != hashlib.sha256(answer.encode("utf-8")).hexdigest()
    ):
        raise GaiaEvaAgentError("Eva benchmark answer is invalid")
    return answer


def _die(message: str) -> NoReturn:
    raise SystemExit(f"eva-gaia-agent: {message}")


def main() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise GaiaEvaAgentError("GAIA task input exceeds its byte limit")
        answer = answer_for_task(json.loads(raw))
    except (GaiaEvaAgentError, UnicodeDecodeError, json.JSONDecodeError) as error:
        _die(str(error))
    print(json.dumps({"model_answer": answer}, ensure_ascii=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
