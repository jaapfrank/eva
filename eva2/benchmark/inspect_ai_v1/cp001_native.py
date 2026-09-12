"""Native EVA/Bottie wire adapters; transport is injected, never started here.

No model client, service launcher, reset, retry or network implementation.
The CLI preflight cannot be overridden by a config boolean. Existing ingress
contracts lack the complete paired budget/cancellation/readback admission.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import time
from typing import Callable

PARTICIPANTS = ("eva2", "eva1-bottie")
ROOT = Path(__file__).resolve().parents[3]
NATIVE_SOURCES = {
    "eva2": [
        "eva2/src/eva/adapters/api/app.py",
        "eva2/src/eva/adapters/api/dashboard_conversation.py",
        "eva2/src/eva/adapters/postgres/conversation_journal.py",
        "eva2/experiments/managed-b-clean-graph-v1/probe.py",
    ],
    "eva1-bottie": [
        "src/web/server.js",
        "src/web/access.js",
        "src/web/requestidentity.js",
        "src/web/httpingress.js",
        "src/brain/llm.js",
    ],
}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def preflight(root: Path = ROOT) -> dict:
    """Only inspect source files. Never discover hosts, credentials or data."""
    import cp001_accounting

    accounting = cp001_accounting.preflight(root=root)
    participants = {}
    for name, paths in NATIVE_SOURCES.items():
        hashes = {
            p: hashlib.sha256((root / p).read_bytes()).hexdigest()
            if (root / p).is_file() and not (root / p).is_symlink()
            else None
            for p in paths
        }
        blockers = [
            "disposable clean profile/source image/configuration not admitted",
            "equal model/reasoning/input/output/call/tool limits not proven at ingress",
            "HTTP timeout does not establish server cancellation or quiescence",
            "task B equal after-save interruption/readback route not admitted",
            "complete per-sample helper/memory/retry usage not bound",
        ]
        if name == "eva1-bottie":
            blockers.append(
                "public reply has no request-bound native usage receipt; client ACK key/receipt not admitted"
            )
        if any(value is None for value in hashes.values()):
            blockers.append("required native source missing")
        participants[name] = {
            "admission": "unknown",
            "live_enabled": False,
            "source_files": hashes,
            "blockers": blockers,
        }
    return {
        "accounting": accounting,
        "mode": "native-preflight-only",
        "participants": participants,
        "product_result": "unknown",
        "ranking": None,
        "network_used": False,
        "model_calls": 0,
    }


def request_for(
    participant: str,
    *,
    run_id: str,
    task: str,
    repeat: int,
    step: int,
    text: str,
    occurred_at: str,
    client_ack_public_key: dict | None = None,
) -> tuple[str, dict]:
    """Translate identical text into the real ingress contract; no history injection.

    Task A changes only conversation at step 2, retaining the synthetic principal.
    Task B keeps conversation across resume. Replaying the same turn preserves IDs.
    New run_id means new namespace, NOT a claim of erased/isolated product memory.
    """
    if (
        participant not in PARTICIPANTS
        or not re.fullmatch("[0-9a-f]{12}", run_id)
        or task not in ("A", "B")
        or type(repeat) is not int
        or repeat not in (1, 2)
        or type(step) is not int
        or step not in range(3 if task == "A" else 2)
        or not isinstance(text, str)
        or not text.strip()
        or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", occurred_at)
    ):
        raise ValueError("invalid native turn")
    session = 1 if task == "A" and step == 2 else 0
    namespace = f"cp001-{run_id}-{task.lower()}-{repeat}"
    conversation = f"{namespace}-{session}"
    # Existing managed-B durable readback permits this exact ID shape.
    message = f"managed-b-probe:{digest(namespace + str(step))[:12]}:1"
    if participant == "eva2":
        principal = "33333333-3333-3333-8333-333333333333"
        return "/internal/v1/turns/respond", {
            "audience": {
                "audience_id": f"person:{principal}",
                "kind": "private_person",
                "principal_id": principal,
            },
            "tenant_id": "11111111-1111-1111-8111-111111111111",
            "installation_id": "22222222-2222-2222-8222-222222222222",
            "principal_id": principal,
            "conversation_id": conversation,
            "message_id": message,
            "occurred_at": occurred_at,
            "text": text,
        }
    body = {
        "conversation_id": f"web:{conversation}",
        "request_id": message,
        "text": text,
    }
    if client_ack_public_key is not None:
        body["client_ack_public_key"] = dict(client_ack_public_key)
    return "/api/chat", body


def response_text(participant: str, body: dict, response: dict) -> str:
    """Reject handoffs/errors, mismatched identity and forged content hashes."""
    if not isinstance(response, dict):
        raise ValueError("invalid response")
    if response.get("conversation_id") != body["conversation_id"]:
        raise ValueError("conversation mismatch")
    if participant == "eva2":
        text = response.get("text")
        if (
            response.get("contract") != "EvaInternalGroundedReply/v1"
            or response.get("message_id") != body["message_id"]
            or not isinstance(text, str)
            or response.get("text_sha256") != digest(text)
        ):
            raise ValueError("invalid EVA2 reply")
    else:
        text = response.get("reply")
        if (
            response.get("request_id") != body["request_id"]
            or response.get("request_replay_scope") != "durable_http_ingress"
            or response.get("error")
            or response.get("ok") is False
            or response.get("execution_pending")
            or response.get("durable_handoff")
        ):
            raise ValueError("Bottie response is not a completed reply")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("empty reply")
    return text


def independent_readback(
    participant: str, body: dict, text: str, durable: dict | None
) -> dict:
    """Observe durable evidence, never grade quality by an exact fixture answer.

    EVA data shape is the managed-B REPLY_COMMITTED payload. Bottie data shape is
    its persisted public_body_json, read independently by the supplied reader.
    Unknown full accounting stays null even if a conversation receipt is measured.
    """
    result = {
        "integrity": None,
        "conversation_usage": None,
        "total_usage": None,
        "money": None,
        "semantic_quality": None,
    }
    if durable is None:
        return result
    if not isinstance(durable, dict):
        result["integrity"] = False
        return result
    if participant == "eva2":
        if (
            durable.get("text") != text
            or durable.get("message_id") != body["message_id"]
            or durable.get("conversation_id") != body["conversation_id"]
            or durable.get("text_sha256") != digest(text)
            or durable.get("principal_id") != body["principal_id"]
        ):
            result["integrity"] = False
            return result
        result["integrity"] = True
        usage = durable.get("model_usage")
        fields = ("input_tokens", "output_tokens", "model_attempts", "tool_calls")
        if (
            isinstance(usage, dict)
            and usage.get("measurement") in ("measured", "partial")
            and all(type(usage.get(k)) is int and usage[k] >= 0 for k in fields)
            and isinstance(durable.get("provider"), str)
        ):
            result["conversation_usage"] = {
                **{k: usage[k] for k in fields},
                "measurement": usage["measurement"],
                "provider": durable["provider"],
            }
    else:
        try:
            result["integrity"] = response_text(participant, body, durable) == text
        except ValueError:
            result["integrity"] = False
    return result


def exchange(
    participant: str,
    path: str,
    body: dict,
    *,
    post: Callable,
    readback: Callable,
    input_bytes: int = 8192,
    output_bytes: int = 65536,
    timeout_seconds: float = 5,
) -> dict:
    """One actual ingress exchange using supplied I/O; never retry a timeout.

    This low-level seam is exercised with offline test transports. It cannot prove
    server-side model limits and is therefore NOT a live admission function.
    The reader receives the exact request identity; binding its independent query
    and ensuring quiescence remain prerequisites to native CLI execution.
    """
    expected_path = {"eva2": "/internal/v1/turns/respond", "eva1-bottie": "/api/chat"}
    if participant not in expected_path or path != expected_path[participant]:
        raise ValueError("unknown native route")
    if (
        any(type(v) is not int or v <= 0 for v in (input_bytes, output_bytes))
        or timeout_seconds <= 0
    ):
        raise ValueError("invalid transport limits")
    result = {
        "status": "unknown",
        "product_result": "unknown",
        "answer": None,
        "usage": None,
        "acknowledgement_ms": None,
        "first_content_ms": None,
        "verified_end_ms": None,
        "elapsed_ms": None,
        "requests": 0,
        "server_quiescent": None,
        "quality": None,
        "readback": None,
    }
    started = time.monotonic()
    if len(json.dumps(body).encode()) > input_bytes:
        result["status"] = "input_budget_exceeded"
        return result
    try:
        result["requests"] = 1
        response = post(
            path, body, timeout_seconds=timeout_seconds, max_response_bytes=output_bytes
        )
        if len(json.dumps(response).encode()) > output_bytes:
            result["status"] = "output_budget_exceeded"
            return result
        text = response_text(participant, body, response)
        result["answer"] = text
        result["first_content_ms"] = (time.monotonic() - started) * 1000
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError
        durable = readback(body, timeout_seconds=remaining)
        result["readback"] = independent_readback(participant, body, text, durable)
        result["usage"] = result["readback"]["conversation_usage"]
        if time.monotonic() - started > timeout_seconds:
            raise TimeoutError
        if result["readback"]["integrity"] is True:
            result["status"] = "readback_verified"
            result["verified_end_ms"] = (time.monotonic() - started) * 1000
        else:
            result["status"] = (
                "readback_unknown" if durable is None else "readback_failed"
            )
    except TimeoutError:
        result["status"] = "timeout_execution_unknown"
    except (OSError, ValueError):
        result["status"] = "execution_unknown"
    finally:
        result["elapsed_ms"] = (time.monotonic() - started) * 1000
    return result


def build_preflight_task(samples: list[dict], report: dict):
    """Record actual native request mappings in Inspect without invoking I/O."""
    from inspect_ai import Task
    from inspect_ai.dataset import Sample
    from inspect_ai.model import ModelOutput
    from inspect_ai.scorer import Score, scorer
    from inspect_ai.solver import solver

    @solver
    def admission():
        async def solve(state, generate):
            spec = state.metadata["spec"]
            requests = [
                request_for(
                    spec["participant"],
                    run_id="0123456789ab",
                    task=spec["task"]["id"],
                    repeat=spec["repeat"],
                    step=step,
                    text=text,
                    occurred_at="2026-09-12T00:00:00Z",
                )
                for step, text in enumerate(spec["task"]["turns"])
            ]
            state.metadata["native_requests"] = [
                {"path": path, "body": body} for path, body in requests
            ]
            state.metadata["admission"] = report["participants"][spec["participant"]]
            state.metadata["usage"] = None
            state.output = ModelOutput.from_content(
                model="mockllm/model", content="admission_unknown"
            )
            return state

        return solve

    @scorer(metrics=[])
    def native_readiness():
        async def score(state, target):
            return Score(
                value="unknown",
                explanation="Native route not admitted; no product execution.",
                metadata={"semantic_quality": None, "usage": None},
            )

        return score

    return Task(
        name="cp001_native_preflight",
        dataset=[
            Sample(
                id=s["id"],
                input=json.dumps(s["task"]["turns"], ensure_ascii=False),
                metadata={"spec": s},
            )
            for s in samples
        ],
        solver=admission(),
        scorer=native_readiness(),
    )
