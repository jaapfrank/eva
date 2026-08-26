"""Compatibility checks for the official-SDK delegated child provider."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import contextmanager
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from time import sleep
from typing import Any, Iterator

import pytest

pytest.importorskip("openai")

import eva.bootstrap  # noqa: E402, F401  # Load root before its model submodule.
from eva.adapters.model.openai_child_provider import (  # noqa: E402
    ChildProviderConfigurationError,
    OpenAIChildProvider,
    OpenAIChildProviderSettings,
)
from eva.adapters.subagents.tool_free_child import (  # noqa: E402
    OUTPUT_SCHEMA_SHA256,
    ClosedModelRegistry,
    PydanticToolFreeChildRunner,
    ToolFreeChildProfile,
)
from eva.contracts.delegated_work import DelegatedWorkGrant, DelegatedWorkSpec  # noqa: E402

NOW = "2026-08-26T12:00:00Z"
WORKER_PROGRAM = hashlib.sha256(b"worker-program").hexdigest()
ENGINE_RELEASE = hashlib.sha256(b"engine-release").hexdigest()
MODEL_RELEASE = hashlib.sha256(b"model-release").hexdigest()
WORKER_RELEASE = hashlib.sha256(b"worker-release").hexdigest()


def _write_lease(path: Path, *, token: str = "token-a", account: str = "account-a") -> None:
    path.write_text(
        json.dumps(
            {
                "access_token": token,
                "account_id": account,
                "contract": "EvaModelAccessLease/v1",
                "source_sha256": hashlib.sha256(token.encode()).hexdigest(),
            },
            separators=(",", ":"),
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    path.chmod(0o640)


def _load_lease(path: Path) -> tuple[str, str]:
    value = json.loads(path.read_text(encoding="utf-8"))
    return value["access_token"], value["account_id"]


def _response() -> dict[str, Any]:
    return {
        "id": "resp_eva_child_1",
        "object": "response",
        "created_at": 1_777_111_200.0,
        "status": "completed",
        "model": "gpt-5.4-mini",
        "output": [
            {
                "id": "msg_eva_child_1",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": '{"summary":"gecontroleerd","findings":["bewijs-1"]}',
                        "annotations": [],
                        "logprobs": [],
                    }
                ],
            }
        ],
        "parallel_tool_calls": False,
        "tool_choice": "none",
        "tools": [],
        "usage": {
            "input_tokens": 31,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 11,
            "output_tokens_details": {"reasoning_tokens": 0},
            "total_tokens": 42,
        },
    }


@contextmanager
def _provider_server(
    *,
    status: int = 200,
    response_padding: int = 0,
    body_delay: float = 0,
    terminal: str = "completed",
    usage_mode: str = "valid",
    error_message: str = "provider failure",
) -> Iterator[tuple[str, list[dict[str, Any]]]]:
    captured: list[dict[str, Any]] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, _format: str, *_args: object) -> None:
            pass

        def do_POST(self) -> None:  # noqa: N802
            size = int(self.headers.get("Content-Length", "0"))
            body = json.loads(self.rfile.read(size))
            captured.append(
                {
                    "path": self.path,
                    "body": body,
                    "headers": {
                        key.lower(): value for key, value in self.headers.items()
                    },
                }
            )
            if self.path.endswith("/responses/input_tokens"):
                response = {"object": "response.input_tokens", "input_tokens": 31}
                encoded = json.dumps(response, separators=(",", ":")).encode()
                content_type = "application/json"
            elif self.path.endswith("/responses"):
                if status != 200:
                    encoded = json.dumps(
                        {"error": {"message": error_message, "type": "test_error"}}
                    ).encode()
                    content_type = "application/json"
                    self.send_response(status)
                    self.send_header("Content-Type", content_type)
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                created = _response()
                created["status"] = "in_progress"
                created["output"] = []
                delta = '{"summary":"gecontroleerd","findings":["bewijs-1"]}'
                if response_padding:
                    delta = '{"summary":"' + ("x" * response_padding) + '","findings":[]}'
                events = [
                    {"type": "response.created", "response": created},
                    {
                        "type": "response.output_text.delta",
                        "item_id": "msg_eva_child_1",
                        "output_index": 0,
                        "content_index": 0,
                        "delta": delta,
                        "logprobs": [],
                    },
                ]
                if terminal == "completed":
                    completed = _response()
                    if response_padding:
                        completed["output"][0]["content"][0]["text"] = delta
                    if usage_mode == "missing":
                        completed["usage"] = None
                    elif usage_mode == "corrupt":
                        completed["usage"]["total_tokens"] = -1
                    events.append({"type": "response.completed", "response": completed})
                elif terminal in {"cancelled", "failed", "incomplete"}:
                    failed = _response()
                    failed["status"] = terminal
                    events.append({"type": f"response.{terminal}", "response": failed})
                frames = [
                    f"event: {event['type']}\ndata: "
                    f"{json.dumps(event, separators=(',', ':'))}\n\n"
                    for event in events
                ]
                frames.append("data: [DONE]\n\n")
                encoded = "".join(frames).encode()
                content_type = "text/event-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            if body_delay:
                self.wfile.flush()
                sleep(body_delay)
            try:
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/responses", captured
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _spec() -> DelegatedWorkSpec:
    grant = DelegatedWorkGrant(
        contract="DelegatedWorkGrant/v1",
        schema_version=1,
        context_refs=[],
        capability_ids=[],
        workspace_ref=None,
        child_network_access=False,
        maximum_model_runs=1,
        maximum_completed_model_responses=1,
        maximum_input_tokens=2_000,
        maximum_output_tokens=500,
        maximum_total_tokens=2_500,
        maximum_cost_usd_micros=10_000,
        timeout_seconds=10,
        maximum_claims_before_call_start=2,
        delegation_depth=1,
    )
    return DelegatedWorkSpec(
        contract="DelegatedWork/v1",
        schema_version=1,
        work_id="work:provider:1",
        episode_id="episode:provider:1",
        generation=1,
        parent_task_id="task:provider:1",
        owner_principal_id="person:owner",
        principal_binding_id="binding:telegram:owner",
        audience="private",
        worker_name="onderzoeker",
        worker_profile_id="worker:research:v1",
        worker_profile_release_sha256=WORKER_RELEASE,
        task="Controleer precies één bewering.",
        model_profile_id="model:codex:gpt-5.4-mini:v1",
        model_profile_release_sha256=MODEL_RELEASE,
        worker_program_sha256=WORKER_PROGRAM,
        engine_release_sha256=ENGINE_RELEASE,
        output_schema_sha256=OUTPUT_SCHEMA_SHA256,
        grant=grant.to_payload(),
        created_at=NOW,
        deadline="2026-08-26T12:05:00Z",
    )


def test_official_provider_counts_then_runs_with_rotated_token(tmp_path: Path) -> None:
    lease = tmp_path / "auth.json"
    _write_lease(lease)
    lease_reads = 0

    def rotating_loader() -> tuple[str, str]:
        nonlocal lease_reads
        lease_reads += 1
        return ("token-a" if lease_reads <= 2 else "token-b", "account-a")

    async def run(endpoint: str, captured: list[dict[str, Any]]) -> None:
        settings = OpenAIChildProviderSettings(
            endpoint=endpoint,
            model_name="gpt-5.4-mini",
        )
        provider = OpenAIChildProvider(
            settings,
            credential_loader=rotating_loader,
        )
        async with provider as model:
            profile = ToolFreeChildProfile(
                model_profile_id="model:codex:gpt-5.4-mini:v1",
                model_profile_release_sha256=MODEL_RELEASE,
                worker_profile_id="worker:research:v1",
                worker_profile_release_sha256=WORKER_RELEASE,
                instructions="Geef alleen een controleerbaar en compact onderzoeksresultaat.",
                model=model,
                input_cost_usd_micros_per_million_tokens=750_000,
                output_cost_usd_micros_per_million_tokens=4_500_000,
            )
            runner = PydanticToolFreeChildRunner(
                ClosedModelRegistry({profile.model_profile_id: profile}),
                worker_program_sha256=WORKER_PROGRAM,
                engine_release_sha256=ENGINE_RELEASE,
                clock=lambda: datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
            )
            outcome = await runner.run(_spec(), call_id="call:provider:1")
            assert provider.local_count_requests == 1

        assert outcome.status == "succeeded"
        assert outcome.result is not None
        assert outcome.usage.input_tokens == 31
        assert outcome.usage.output_tokens == 11
        assert lease_reads >= 3
        assert [item["path"] for item in captured] == ["/responses"]
        for item in captured:
            assert item["headers"]["authorization"] == "Bearer token-b"
            assert item["headers"]["openai-account-id"] == "account-a"
            assert item["headers"]["originator"] == "codex_cli_rs"
            assert item["headers"]["session_id"]
        assert captured[0]["body"]["store"] is False
        assert captured[0]["body"].get("parallel_tool_calls") in {None, False}
        assert captured[0]["body"]["tool_choice"] == "none"
        assert not captured[0]["body"].get("tools")
        assert "max_output_tokens" not in captured[0]["body"]

    with _provider_server() as (endpoint, captured):
        asyncio.run(run(endpoint, captured))


def test_account_rotation_fails_before_second_http_call(tmp_path: Path) -> None:
    lease = tmp_path / "auth.json"
    _write_lease(lease)

    async def run(endpoint: str) -> None:
        async with OpenAIChildProvider(
            OpenAIChildProviderSettings(
                endpoint=endpoint,
                model_name="gpt-5.4-mini",
            ),
            credential_loader=lambda: _load_lease(lease),
        ) as model:
            profile = ToolFreeChildProfile(
                model_profile_id="model:codex:gpt-5.4-mini:v1",
                model_profile_release_sha256=MODEL_RELEASE,
                worker_profile_id="worker:research:v1",
                worker_profile_release_sha256=WORKER_RELEASE,
                instructions="Geef alleen een controleerbaar onderzoeksresultaat.",
                model=model,
                input_cost_usd_micros_per_million_tokens=750_000,
                output_cost_usd_micros_per_million_tokens=4_500_000,
            )
            runner = PydanticToolFreeChildRunner(
                ClosedModelRegistry({profile.model_profile_id: profile}),
                worker_program_sha256=WORKER_PROGRAM,
                engine_release_sha256=ENGINE_RELEASE,
                clock=lambda: datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
            )
            _write_lease(lease, account="account-b")
            outcome = await runner.run(_spec(), call_id="call:provider:2")
            assert outcome.status == "misconfigured"
            assert outcome.result is None

    with _provider_server() as (endpoint, captured):
        asyncio.run(run(endpoint))
    assert captured == []


def test_conservative_preflight_blocks_generation_without_network(tmp_path: Path) -> None:
    lease = tmp_path / "auth.json"
    _write_lease(lease)

    async def run(endpoint: str) -> None:
        async with OpenAIChildProvider(
            OpenAIChildProviderSettings(endpoint=endpoint, model_name="gpt-5.4-mini"),
            credential_loader=lambda: _load_lease(lease),
        ) as model:
            profile = ToolFreeChildProfile(
                model_profile_id="model:codex:gpt-5.4-mini:v1",
                model_profile_release_sha256=MODEL_RELEASE,
                worker_profile_id="worker:research:v1",
                worker_profile_release_sha256=WORKER_RELEASE,
                instructions="Geef alleen een controleerbaar onderzoeksresultaat.",
                model=model,
                input_cost_usd_micros_per_million_tokens=750_000,
                output_cost_usd_micros_per_million_tokens=4_500_000,
            )
            runner = PydanticToolFreeChildRunner(
                ClosedModelRegistry({profile.model_profile_id: profile}),
                worker_program_sha256=WORKER_PROGRAM,
                engine_release_sha256=ENGINE_RELEASE,
                clock=lambda: datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
            )
            spec = _spec()
            restricted = spec.model_copy(
                update={
                    "grant": spec.grant.model_copy(
                        update={"maximum_input_tokens": 1}
                    )
                }
            )
            outcome = await runner.run(restricted, call_id="call:provider:preflight")
            assert outcome.status == "budget_exceeded"

    with _provider_server() as (endpoint, captured):
        asyncio.run(run(endpoint))
    assert captured == []


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(401, "provider_rejected"), (403, "provider_rejected"), (429, "provider_transient"), (500, "provider_transient")],
)
def test_provider_http_errors_are_sanitized_and_never_retried(
    tmp_path: Path,
    status_code: int,
    expected: str,
) -> None:
    secret = "SECRET-CANARY-MUST-NOT-SURVIVE"
    lease = tmp_path / "auth.json"
    _write_lease(lease, token=secret)

    async def run(endpoint: str):  # type: ignore[no-untyped-def]
        async with OpenAIChildProvider(
            OpenAIChildProviderSettings(endpoint=endpoint, model_name="gpt-5.4-mini"),
            credential_loader=lambda: _load_lease(lease),
        ) as model:
            profile = ToolFreeChildProfile(
                model_profile_id="model:codex:gpt-5.4-mini:v1",
                model_profile_release_sha256=MODEL_RELEASE,
                worker_profile_id="worker:research:v1",
                worker_profile_release_sha256=WORKER_RELEASE,
                instructions="Geef alleen een controleerbaar onderzoeksresultaat.",
                model=model,
                input_cost_usd_micros_per_million_tokens=750_000,
                output_cost_usd_micros_per_million_tokens=4_500_000,
            )
            runner = PydanticToolFreeChildRunner(
                ClosedModelRegistry({profile.model_profile_id: profile}),
                worker_program_sha256=WORKER_PROGRAM,
                engine_release_sha256=ENGINE_RELEASE,
                clock=lambda: datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
            )
            return await runner.run(_spec(), call_id="call:provider:http")

    with _provider_server(
        status=status_code,
        error_message=secret,
    ) as (endpoint, captured):
        outcome = asyncio.run(run(endpoint))
    assert outcome.status == expected
    assert secret not in repr(outcome)
    assert len(captured) == 1


@pytest.mark.parametrize(
    "terminal", ["cancelled", "failed", "incomplete", "missing"]
)
def test_noncompleted_provider_stream_never_becomes_success(
    tmp_path: Path,
    terminal: str,
) -> None:
    lease = tmp_path / "auth.json"
    _write_lease(lease)

    async def run(endpoint: str):  # type: ignore[no-untyped-def]
        async with OpenAIChildProvider(
            OpenAIChildProviderSettings(endpoint=endpoint, model_name="gpt-5.4-mini"),
            credential_loader=lambda: _load_lease(lease),
        ) as model:
            profile = ToolFreeChildProfile(
                model_profile_id="model:codex:gpt-5.4-mini:v1",
                model_profile_release_sha256=MODEL_RELEASE,
                worker_profile_id="worker:research:v1",
                worker_profile_release_sha256=WORKER_RELEASE,
                instructions="Geef alleen een controleerbaar onderzoeksresultaat.",
                model=model,
                input_cost_usd_micros_per_million_tokens=750_000,
                output_cost_usd_micros_per_million_tokens=4_500_000,
            )
            runner = PydanticToolFreeChildRunner(
                ClosedModelRegistry({profile.model_profile_id: profile}),
                worker_program_sha256=WORKER_PROGRAM,
                engine_release_sha256=ENGINE_RELEASE,
                clock=lambda: datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
            )
            return await runner.run(_spec(), call_id="call:provider:terminal")

    with _provider_server(terminal=terminal) as (endpoint, captured):
        outcome = asyncio.run(run(endpoint))
    assert outcome.status != "succeeded"
    assert len(captured) == 1


@pytest.mark.parametrize("usage_mode", ["missing", "corrupt"])
def test_completed_response_requires_valid_usage(
    tmp_path: Path,
    usage_mode: str,
) -> None:
    lease = tmp_path / "auth.json"
    _write_lease(lease)

    async def run(endpoint: str):  # type: ignore[no-untyped-def]
        async with OpenAIChildProvider(
            OpenAIChildProviderSettings(endpoint=endpoint, model_name="gpt-5.4-mini"),
            credential_loader=lambda: _load_lease(lease),
        ) as model:
            profile = ToolFreeChildProfile(
                model_profile_id="model:codex:gpt-5.4-mini:v1",
                model_profile_release_sha256=MODEL_RELEASE,
                worker_profile_id="worker:research:v1",
                worker_profile_release_sha256=WORKER_RELEASE,
                instructions="Geef alleen een controleerbaar onderzoeksresultaat.",
                model=model,
                input_cost_usd_micros_per_million_tokens=750_000,
                output_cost_usd_micros_per_million_tokens=4_500_000,
            )
            runner = PydanticToolFreeChildRunner(
                ClosedModelRegistry({profile.model_profile_id: profile}),
                worker_program_sha256=WORKER_PROGRAM,
                engine_release_sha256=ENGINE_RELEASE,
                clock=lambda: datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
            )
            return await runner.run(_spec(), call_id="call:provider:usage")

    with _provider_server(usage_mode=usage_mode) as (endpoint, captured):
        outcome = asyncio.run(run(endpoint))
    assert outcome.status != "succeeded"
    assert len(captured) == 1


def test_response_byte_cap_fails_closed(tmp_path: Path) -> None:
    lease = tmp_path / "auth.json"
    _write_lease(lease)

    async def run(endpoint: str):  # type: ignore[no-untyped-def]
        async with OpenAIChildProvider(
            OpenAIChildProviderSettings(
                endpoint=endpoint,
                model_name="gpt-5.4-mini",
                maximum_response_bytes=4_096,
            ),
            credential_loader=lambda: _load_lease(lease),
        ) as model:
            profile = ToolFreeChildProfile(
                model_profile_id="model:codex:gpt-5.4-mini:v1",
                model_profile_release_sha256=MODEL_RELEASE,
                worker_profile_id="worker:research:v1",
                worker_profile_release_sha256=WORKER_RELEASE,
                instructions="Geef alleen een controleerbaar onderzoeksresultaat.",
                model=model,
                input_cost_usd_micros_per_million_tokens=750_000,
                output_cost_usd_micros_per_million_tokens=4_500_000,
            )
            runner = PydanticToolFreeChildRunner(
                ClosedModelRegistry({profile.model_profile_id: profile}),
                worker_program_sha256=WORKER_PROGRAM,
                engine_release_sha256=ENGINE_RELEASE,
                clock=lambda: datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
            )
            return await runner.run(_spec(), call_id="call:provider:size")

    with _provider_server(response_padding=6_000) as (endpoint, captured):
        outcome = asyncio.run(run(endpoint))
    assert outcome.status != "succeeded"
    assert len(captured) == 1


def test_cancellation_during_stream_closes_without_success(tmp_path: Path) -> None:
    lease = tmp_path / "auth.json"
    _write_lease(lease)

    async def run(endpoint: str):  # type: ignore[no-untyped-def]
        async with OpenAIChildProvider(
            OpenAIChildProviderSettings(endpoint=endpoint, model_name="gpt-5.4-mini"),
            credential_loader=lambda: _load_lease(lease),
        ) as model:
            profile = ToolFreeChildProfile(
                model_profile_id="model:codex:gpt-5.4-mini:v1",
                model_profile_release_sha256=MODEL_RELEASE,
                worker_profile_id="worker:research:v1",
                worker_profile_release_sha256=WORKER_RELEASE,
                instructions="Geef alleen een controleerbaar onderzoeksresultaat.",
                model=model,
                input_cost_usd_micros_per_million_tokens=750_000,
                output_cost_usd_micros_per_million_tokens=4_500_000,
            )
            runner = PydanticToolFreeChildRunner(
                ClosedModelRegistry({profile.model_profile_id: profile}),
                worker_program_sha256=WORKER_PROGRAM,
                engine_release_sha256=ENGINE_RELEASE,
                clock=lambda: datetime(2026, 8, 26, 12, 0, 1, tzinfo=UTC),
            )
            cancellation = asyncio.Event()
            pending = asyncio.create_task(
                runner.run(
                    _spec(),
                    call_id="call:provider:cancel",
                    cancellation_event=cancellation,
                )
            )
            await asyncio.sleep(0.1)
            cancellation.set()
            return await asyncio.wait_for(pending, timeout=2)

    with _provider_server(body_delay=3) as (endpoint, captured):
        outcome = asyncio.run(run(endpoint))
    assert outcome.status == "cancelled"
    assert len(captured) == 1


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com/responses",
        "https://example.com/v1",
        "https://user:password@example.com/responses",
        "https://example.com/responses?debug=true",
    ],
)
def test_provider_settings_reject_unsafe_endpoint(endpoint: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="must end"):
        OpenAIChildProviderSettings(
            endpoint=endpoint,
            model_name="gpt-5.4-mini",
        )


def test_sdk_debug_logging_is_forbidden(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lease = tmp_path / "auth.json"
    _write_lease(lease)
    monkeypatch.setenv("OPENAI_LOG", "debug")

    async def run() -> None:
        provider = OpenAIChildProvider(
            OpenAIChildProviderSettings(
                endpoint="https://example.com/responses",
                model_name="gpt-5.4-mini",
            ),
            credential_loader=lambda: _load_lease(lease),
        )
        with pytest.raises(ChildProviderConfigurationError, match="logging"):
            await provider.__aenter__()

    asyncio.run(run())
