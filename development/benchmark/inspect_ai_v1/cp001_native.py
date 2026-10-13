"""Native EVA/Bottie wire adapters; transport is injected, never started here.

No model client, service launcher, reset, retry or network implementation.
The CLI preflight cannot be overridden by a config boolean. Existing ingress
contracts lack the complete paired budget/cancellation/readback admission.
"""

from __future__ import annotations

from dataclasses import dataclass
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
        "src/eva/adapters/api/app.py",
        "src/eva/adapters/api/dashboard_conversation.py",
        "src/eva/adapters/postgres/conversation_journal.py",
        "development/archive/history/eva2/experiments/managed-b-clean-graph-v1/probe.py",
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


def eva2_request_for_turn(turn) -> tuple[str, dict]:
    """Serialize an already resolved native turn; never infer actor or authority.

    The trusted evaluator resolves actor, audience, session and clock using real
    provisioning before calling this function. A valid private turn represents
    the trusted connector boundary, not proof of physical person recognition.
    Household/group authority is still revalidated by the native runtime.
    """
    from dataclasses import asdict
    from eva.application.conversation import ConversationTurn
    from eva.application.memory_audience import memory_audience_to_wire

    if not isinstance(turn, ConversationTurn):
        raise ValueError("an explicitly resolved native ConversationTurn is required")
    body = {name: getattr(turn, name) for name in (
        "tenant_id", "installation_id", "principal_id", "conversation_id",
        "message_id", "text", "occurred_at")}
    body["audience"] = memory_audience_to_wire(turn.audience)
    if turn.project_id is not None:
        body["project_id"] = turn.project_id
    if turn.observation_binding is not None:
        body["observation_binding"] = asdict(turn.observation_binding)
    return "/internal/v1/turns/respond", body


def read_eva2_committed_reply(engine, body: dict, *, timeout_seconds: float):
    """Read one scoped native reply through an independent database connection.

    The caller owns the disposable database and supplies this reader to exchange.
    This is durable reply evidence only, not semantic-memory settlement, complete
    usage, authentication, fresh-state proof or permission to start a model run.
    """
    import math
    import sqlalchemy as sa
    from eva.adapters.postgres.operational_timeline import external_timeline_scope

    if (type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("a positive remaining readback timeout is required")
    scope = external_timeline_scope(body["tenant_id"], body["installation_id"], "scope")
    with engine.connect() as connection:
        connection.execute(sa.text("SET TRANSACTION READ ONLY"))
        connection.execute(sa.text("SELECT set_config(:key, :value, true)"),
                           {"key": "statement_timeout",
                            "value": str(max(1, int(timeout_seconds * 1000)))})
        rows = connection.execute(sa.text(
            "SELECT payload->'data' FROM eva_core.operational_events "
            "WHERE tenant_id=:tenant AND installation_id=:installation "
            "AND thread_id=:thread AND event_type='REPLY_COMMITTED' "
            "AND payload->'data'->>'principal_id'=:principal "
            "AND payload->'data'->>'conversation_id'=:conversation "
            "AND payload->'data'->>'message_id'=:message ORDER BY sequence"),
            {"tenant": scope.tenant_id, "installation": scope.installation_id,
             "thread": f"conversation:{body['message_id']}",
             "principal": body["principal_id"], "conversation": body["conversation_id"],
             "message": body["message_id"]}).scalars().all()
    if len(rows) > 1:
        raise ValueError("multiple committed replies for one native request")
    return rows[0] if rows else None



def read_eva2_memory_settlement(core_engine, memory_engine, body: dict, *,
                                timeout_seconds: float) -> dict:
    """Observe one turn's full native retention in a disposable scenario database.

    Both readers must target the scenario's independently provisioned databases.
    All work in its affected bank must settle; no wall-clock guess, old successful
    consolidation or fixed expected fact can stand in for the current document.
    This is completion evidence, not semantic quality or provider quiescence.
    No memory content is returned to the participant by this helper.
    """
    import math
    import sqlalchemy as sa
    from eva.adapters.postgres.operational_timeline import external_timeline_scope

    if (type(timeout_seconds) not in (int, float)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("a positive remaining readback timeout is required")
    deadline = time.monotonic() + timeout_seconds

    def read(engine, query, parameters):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("native memory readback deadline reached")
        with engine.connect() as connection:
            connection.execute(sa.text("SET TRANSACTION READ ONLY"))
            connection.execute(sa.text("SELECT set_config(:key, :value, true)"),
                               {"key": "statement_timeout",
                                "value": str(max(1, int(remaining * 1000)))})
            return [dict(row) for row in connection.execute(
                sa.text(query), parameters).mappings()]

    scope = external_timeline_scope(body["tenant_id"], body["installation_id"], "scope")
    rows = read(core_engine, """
        SELECT q.thread_id, q.payload->'data' AS document,
               c.phase, c.status, s.payload->'data' AS receipt
        FROM eva_core.operational_events q
        JOIN eva_core.operational_checkpoints c USING (tenant_id, installation_id, thread_id)
        LEFT JOIN eva_core.operational_events s
          ON s.tenant_id=q.tenant_id AND s.installation_id=q.installation_id
          AND s.thread_id=q.thread_id AND s.event_type='MEMORY_RETAIN_SUCCEEDED'
        WHERE q.tenant_id=:tenant AND q.installation_id=:installation
          AND q.event_type='MEMORY_RETAIN_QUEUED'
          AND q.payload->'data'->>'principal_id'=:principal
          AND q.payload->'data'->>'conversation_id'=:conversation
          AND q.payload->'data'->>'source_ref'=:message
        """, {"tenant": scope.tenant_id, "installation": scope.installation_id,
              "principal": body["principal_id"], "conversation": body["conversation_id"],
              "message": body["message_id"]})
    result = {"status": "pending", "settled": False, "document_id": None,
              "bank_id": None, "source_facts": None, "operations": []}
    if not rows:
        return {**result, "reason": "no exact native retain document"}
    if len(rows) != 1:
        raise ValueError("ambiguous native memory document or receipt")
    row = rows[0]
    document, receipt = row["document"], row["receipt"]
    result["document_id"] = document["document_id"]
    if (document.get("tenant_id") != body["tenant_id"]
            or document.get("installation_id") != body["installation_id"]
            or document.get("audience") != body["audience"]
            or row["thread_id"] != "memory-retain:" + digest(document["document_id"])):
        raise ValueError("native retain document scope mismatch")
    if row["status"] == "needs_help":
        return {**result, "status": "failed", "reason": "native retention needs help"}
    if row["phase"] != "memory_retained" or row["status"] != "completed":
        return {**result, "reason": "native retention pending"}
    if (not receipt or receipt.get("document_id") != document["document_id"]
            or receipt.get("content_sha256") != digest(document["content"])):
        raise ValueError("native retain receipt mismatch")
    result["bank_id"] = receipt["bank_id"]
    parameters = {"bank": receipt["bank_id"], "document": document["document_id"]}
    stored = read(memory_engine, """
        SELECT original_text FROM documents WHERE bank_id=:bank AND id=:document
        """, parameters)
    if len(stored) != 1 or stored[0]["original_text"] != document["content"]:
        return {**result, "status": "failed", "reason": "retained document readback differs"}
    facts = read(memory_engine, """
        SELECT id::text, consolidated_at, consolidation_failed_at FROM memory_units
        WHERE bank_id=:bank AND document_id=:document AND fact_type IN ('world','experience')
        """, parameters)
    result["source_facts"] = len(facts)
    result["operations"] = read(memory_engine, """
        SELECT operation_id::text, operation_type, status FROM async_operations
        WHERE bank_id=:bank ORDER BY created_at, operation_id
        """, parameters)
    if any(x["status"] in ("failed", "cancelled") for x in result["operations"]):
        return {**result, "status": "failed", "reason": "native memory operation failed"}
    if any(x["consolidation_failed_at"] is not None for x in facts):
        return {**result, "status": "failed", "reason": "native consolidation failed"}
    if (not result["operations"]
            or any(x["status"] != "completed" for x in result["operations"])
            or any(x["consolidated_at"] is None for x in facts)):
        return {**result, "reason": "native memory operations or consolidation pending"}
    if time.monotonic() >= deadline:
        raise TimeoutError("native memory readback deadline reached")
    return {**result, "status": "completed", "settled": True}


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


@dataclass(frozen=True)
class NativeHTTPResponse:
    """Transport envelope only; no reply validation or scoring."""
    body: object
    status: int


class NativeIngressError(ValueError):
    """Content-free transport failure; never retain an error response body."""
    CODES = frozenset({"http_rejected", "invalid_json", "response_size_exceeded"})

    def __init__(self, code: str, http_status: int | None = None):
        if code not in self.CODES:
            raise ValueError("unknown ingress failure code")
        super().__init__(code)
        self.code = code
        self.http_status = http_status


def _http_status(value: object) -> int | None:
    return value if type(value) is int and 100 <= value <= 599 else None


def _exchange_diagnostic(phase: str, error: Exception, http_status: int | None) -> dict:
    # Only fixed observer phases/codes and a validated status may leave the
    # transport. Exception strings, response bodies, URLs and frames stay out.
    if isinstance(error, NativeIngressError):
        code = error.code
        http_status = _http_status(error.http_status) or http_status
    elif isinstance(error, TimeoutError):
        code = "timeout"
    elif isinstance(error, OSError):
        code = "transport_error"
        http_status = _http_status(getattr(error, "code", None)) or http_status
    else:
        code = "invalid_value"
    return {"phase": phase, "code": code, "http_status": http_status}


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
        "http_status": None,
        "diagnostic": None,
    }
    phase = "ingress"
    started = time.monotonic()
    if len(json.dumps(body).encode()) > input_bytes:
        result["status"] = "input_budget_exceeded"
        return result
    try:
        result["requests"] = 1
        response = post(
            path, body, timeout_seconds=timeout_seconds, max_response_bytes=output_bytes
        )
        if isinstance(response, NativeHTTPResponse):
            result["http_status"] = _http_status(response.status)
            if result["http_status"] != 200:
                raise NativeIngressError("http_rejected", response.status)
            response = response.body
        phase = "response_size"
        if len(json.dumps(response).encode()) > output_bytes:
            result["status"] = "output_budget_exceeded"
            return result
        phase = "response_validation"
        text = response_text(participant, body, response)
        result["answer"] = text
        result["first_content_ms"] = (time.monotonic() - started) * 1000
        phase = "readback"
        remaining = timeout_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError
        durable = readback(body, timeout_seconds=remaining)
        phase = "readback_validation"
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
    except TimeoutError as error:
        result["status"] = "timeout_execution_unknown"
        result["diagnostic"] = _exchange_diagnostic(phase, error, result["http_status"])
    except (OSError, ValueError) as error:
        result["status"] = "execution_unknown"
        result["diagnostic"] = _exchange_diagnostic(phase, error, result["http_status"])
    finally:
        result["elapsed_ms"] = (time.monotonic() - started) * 1000
    if result["diagnostic"] is not None:
        result["http_status"] = result["diagnostic"]["http_status"]
    return result


class NativeProviderControl:
    """Bind a native worker's provider handshake to the existing sample ledger.

    The caller owns the worker and forwards its events without substitution.
    This controls local dispatch and records raw usage; it does not attest cloud
    cancellation, cover unbound helpers, or enable a live comparison.
    """

    def __init__(self, ledger, request_id: str):
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("A native request identity is required")
        self.ledger = ledger
        self.request_id = request_id
        self._attempts_by_event: dict[str, int | None] = {}
        self._settled: set[str] = set()

    def stop(self) -> dict:
        self.ledger.stop_async_attempts("native comparison stopped")
        return {"stop": True}

    def handle(self, event: dict) -> dict:
        from cp001_accounting import SampleStopped

        event_id = event.get("event_id") if isinstance(event, dict) else None
        control = {"provider_control": event_id, "allow": False}
        try:
            if (
                not isinstance(event_id, str)
                or not event_id
                or event.get("request_id") != self.request_id
            ):
                raise ValueError("provider event identity differs")
            kind = event.get("provider_event")
            if kind == "begin":
                if event_id in self._attempts_by_event:
                    raise ValueError("duplicate provider begin")
                # Keep a denied event bound so its no-dispatch terminal event
                # cannot be mistaken for an unobserved provider call.
                self._attempts_by_event[event_id] = None
                attempt = self.ledger.begin(
                    event.get("role"), event.get("model"), event.get("reasoning_effort")
                )
                self._attempts_by_event[event_id] = attempt
                return {**control, "allow": True, "attempt_id": attempt}
            if kind != "settle" or event_id not in self._attempts_by_event:
                raise ValueError("provider terminal event has no begin")
            if event_id in self._settled:
                raise ValueError("duplicate provider terminal event")
            expected = self._attempts_by_event[event_id]
            attempt = event.get("attempt_id")
            status = event.get("status")
            if status not in ("completed", "aborted", "error"):
                raise ValueError("invalid provider terminal status")
            if expected is None:
                if attempt is not None or status == "completed":
                    raise ValueError("denied provider call cannot complete")
                self._settled.add(event_id)
                return control
            if attempt is None and status == "aborted" and self.ledger.stop_requested:
                # Stop may reach the worker before an already reserved permit.
                # The bound terminal event closes that reservation; it supplies
                # no usage and does not prove a provider-side cancellation.
                self._settled.add(event_id)
                self.ledger.incomplete(expected, "aborted")
                return control
            if type(attempt) is not int or attempt != expected:
                raise ValueError("provider attempt identity differs")
            self._settled.add(event_id)
            if status == "completed":
                self.ledger.finish(attempt, event.get("usage"))
                return {**control, "allow": not bool(self.ledger.stopped)}
            self.ledger.incomplete(attempt, status)
            return control
        except SampleStopped:
            return control
        except (KeyError, TypeError, ValueError):
            self.ledger.stop_async_attempts("invalid native provider event")
            return {**control, "stop": True}


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
