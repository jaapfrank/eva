"""Release composition checks for the first real delegated provider."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace

import httpx2
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from eva.bootstrap import delegated_work_provider as provider_module
from eva.bootstrap.delegated_work_provider import (
    DEPENDENCY_LOCK_SHA256,
    ENGINE_RELEASE_SHA256,
    MODEL_PROFILE_ID,
    MODEL_PROFILE_RELEASE_SHA256,
    WORKER_PROFILE_ID,
    WORKER_PROFILE_RELEASE_SHA256,
    WORKER_PROGRAM_SHA256,
    build_unadmitted_delegated_work_spec,
    open_delegated_work_runner,
    verify_delegated_provider_dependencies,
    validate_delegated_model_access_lease,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.adapters.model.openai_codex_provider import (
    OpenAICodexProvider, RawUsageAttestation,
)

EVA2_ROOT = Path(__file__).resolve().parents[2]


def test_release_profile_is_closed_and_hash_bound() -> None:
    assert MODEL_PROFILE_ID == "model:codex:gpt-5.4-mini:v1"
    assert WORKER_PROFILE_ID == "worker:research:v1"
    for value in (
        MODEL_PROFILE_RELEASE_SHA256,
        WORKER_PROFILE_RELEASE_SHA256,
        WORKER_PROGRAM_SHA256,
        ENGINE_RELEASE_SHA256,
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", value)


def test_worker_and_engine_release_bind_real_source_and_lock_bytes() -> None:
    package_root = EVA2_ROOT / "src/eva"
    sources = {
        path: hashlib.sha256((package_root / path).read_bytes()).hexdigest()
        for path in (
            "adapters/model/__init__.py",
            "adapters/model/openai_codex_provider.py",
            "adapters/model/openai_endpoint.py",
            "adapters/postgres/delegated_work.py",
            "adapters/subagents/tool_free_child.py",
            "bootstrap/__init__.py",
            "bootstrap/delegated_work_provider.py",
            "bootstrap/delegated_work_service.py",
            "adapters/credentials/runtime_files.py",
            "contracts/delegated_work.py",
            "operations/delegated_work_worker.py",
        )
    }
    assert WORKER_PROGRAM_SHA256 == canonical_sha256(
        {"contract": "EvaDelegatedWorkerProgram/v1", "source_sha256": sources}
    )
    lock_sha256 = hashlib.sha256(
        (EVA2_ROOT / "requirements-4d.lock").read_bytes()
    ).hexdigest()
    assert DEPENDENCY_LOCK_SHA256 == lock_sha256
    assert ENGINE_RELEASE_SHA256 == canonical_sha256(
        {
            "contract": "EvaDelegatedEngineRelease/v1",
            "dependency_lock_sha256": lock_sha256,
            "openai_sdk": "3.6.0",
            "pydantic_ai": "2.37.0",
        }
    )


def test_shared_worker_uses_the_existing_core_dependency_release() -> None:
    lock = (EVA2_ROOT / "requirements-4d.lock").read_text()
    assert "openai==3.6.0 " in lock
    assert "pydantic-ai-slim==2.37.0 " in lock


def test_dependency_versions_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    installed = {"openai": "3.6.0", "pydantic-ai-slim": "2.37.0"}
    monkeypatch.setattr(provider_module, "version", installed.__getitem__)
    verify_delegated_provider_dependencies()

    installed["openai"] = "3.3.2"
    with pytest.raises(RuntimeError, match="release differs"):
        verify_delegated_provider_dependencies()


def test_unadmitted_task_builder_matches_the_only_sealed_runner_profile() -> None:
    created = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    spec = build_unadmitted_delegated_work_spec(
        work_id="work:owner:research:1",
        episode_id="11111111-1111-1111-1111-111111111111",
        generation=7,
        parent_task_id="task:telegram:42",
        owner_principal_id="22222222-2222-2222-2222-222222222222",
        principal_binding_id="33333333-3333-3333-3333-333333333333",
        task="Onderzoek welke onderhouden voice-route het beste past.",
        created_at=created,
        deadline=created + timedelta(minutes=5),
    )
    assert spec.worker_profile_id == WORKER_PROFILE_ID
    assert spec.worker_profile_release_sha256 == WORKER_PROFILE_RELEASE_SHA256
    assert spec.model_profile_id == MODEL_PROFILE_ID
    assert spec.model_profile_release_sha256 == MODEL_PROFILE_RELEASE_SHA256
    assert spec.worker_program_sha256 == WORKER_PROGRAM_SHA256
    assert spec.engine_release_sha256 == ENGINE_RELEASE_SHA256
    assert spec.grant.context_refs == []
    assert spec.grant.capability_ids == []
    assert spec.grant.child_network_access is False
    assert spec.grant.maximum_model_runs == 1
    assert spec.grant.maximum_completed_model_responses == 1


def test_bootstrap_reads_exact_shared_access_lease_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    payload = {
        "access_token": "access-token",
        "account_id": "account-id",
        "contract": "EvaModelAccessLease/v1",
        "source_sha256": hashlib.sha256(b"owner-source").hexdigest(),
    }

    def read(path: str, **kwargs: object) -> bytes:
        observed["path"] = path
        observed["kwargs"] = kwargs
        return json.dumps(payload).encode()

    monkeypatch.setattr(provider_module, "read_shared_runtime_file_bytes", read)
    lease = Path("/run/eva/model-lease/auth.json")
    assert provider_module._read_access_lease(lease) == (  # noqa: SLF001
        "access-token",
        "account-id",
    )
    assert observed == {
        "path": str(lease),
        "kwargs": {
            "setting_name": "delegated child model access lease",
            "owner_uid": 1000,
            "reader_gid": 65532,
            "required_mode": 0o640,
        },
    }
    assert validate_delegated_model_access_lease(lease) is None


class _ResponseStream(httpx2.AsyncByteStream):
    def __init__(self, output_tokens: int) -> None:
        self.output_tokens = output_tokens

    async def __aiter__(self):
        text = json.dumps({"summary": "Eén bevinding.", "findings": ["Controleer later."]})
        response = {
            "id": "resp_child", "object": "response", "created_at": 1_777_111_200,
            "model": "gpt-5.4-mini", "status": "completed",
            "output": [{"id": "msg_child", "type": "message", "role": "assistant",
                        "status": "completed", "content": [{"type": "output_text",
                        "text": text, "annotations": [], "logprobs": []}]}],
            "parallel_tool_calls": False, "tools": [], "tool_choice": "none",
            "usage": {"input_tokens": 17, "output_tokens": self.output_tokens,
                      "total_tokens": 17 + self.output_tokens,
                      "input_tokens_details": {"cached_tokens": 0},
                      "output_tokens_details": {"reasoning_tokens": 0}},
        }
        events = [
            {"type": "response.created", "response": response | {
                "status": "in_progress", "output": []}},
            {"type": "response.output_text.delta", "item_id": "msg_child",
             "output_index": 0, "content_index": 0, "delta": text, "logprobs": []},
            {"type": "response.completed", "response": response},
        ]
        for event in events:
            yield f"data: {json.dumps(event)}\n\n".encode()
        yield b"data: [DONE]\n\n"


@pytest.mark.parametrize("mode", ["success", "invalid_usage", "budget_exceeded", "cancelled"])
def test_each_child_gets_a_fresh_shared_provider_with_grant_and_cancellation(
    monkeypatch: pytest.MonkeyPatch, mode: str,
) -> None:
    sessions = []
    requests = []
    cancellation = None
    created = datetime(2026, 9, 10, tzinfo=UTC)

    async def respond(request):
        requests.append(json.loads(request.content))
        if mode == "cancelled":
            cancellation.set()
            await asyncio.Event().wait()
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, request=request,
            stream=_ResponseStream(501 if mode == "budget_exceeded" else 7),
        )

    @asynccontextmanager
    async def provider(settings, *, credential_loader):
        assert settings.model_name == "gpt-5.4-mini"
        assert settings.endpoint == "https://chatgpt.com/backend-api/codex/responses"
        assert settings.reasoning_effort == "low"
        async with OpenAICodexProvider(
            settings, credential_loader=credential_loader,
            network_transport=httpx2.MockTransport(respond),
        ) as session:
            sessions.append(session)
            if mode == "invalid_usage":
                yield SimpleNamespace(
                    model=session.model,
                    consume_raw_usage=lambda: RawUsageAttestation(18, 7, 25),
                )
            else:
                yield session

    monkeypatch.setattr(provider_module, "OpenAICodexProvider", provider)
    monkeypatch.setattr(provider_module, "_read_access_lease", lambda _: ("access", "account"))

    async def run() -> None:
        nonlocal cancellation
        async with open_delegated_work_runner(
            model_access_lease_file=Path("/run/eva/model-lease/auth.json"),
            clock=lambda: created,
        ) as runner:
            assert sessions == []  # Idle workers have no aging provider session.
            for index in range(2):
                cancellation = asyncio.Event()
                spec = build_unadmitted_delegated_work_spec(
                    work_id=f"work:shared:{index}", episode_id="context:shared",
                    generation=1, parent_task_id="task:shared",
                    owner_principal_id="owner:shared", principal_binding_id="binding:shared",
                    task="Noem één aandachtspunt.", created_at=created,
                    deadline=created + timedelta(minutes=5),
                )
                outcome = await runner.run(
                    spec, call_id=f"call:shared:{index}", cancellation_event=cancellation,
                )
                assert outcome.status == ("succeeded" if mode == "success" else mode)
                if mode == "success":
                    assert outcome.result.work_id == spec.work_id
                    assert outcome.result.call_id == f"call:shared:{index}"
                    assert outcome.result.spec_sha256 == spec.sha256
                    assert outcome.usage.input_tokens == 17
                    assert outcome.usage.output_tokens == 7
                    assert outcome.usage.tool_calls == 0
                else:
                    assert outcome.result is None
                    assert outcome.retryable is False
                    assert outcome.error_fingerprint
                    if mode == "invalid_usage":
                        assert outcome.usage.input_tokens == 17
                    if mode == "budget_exceeded":
                        assert outcome.usage.output_tokens == 501
        assert len(sessions) == 2
        assert [session.model_requests for session in sessions] == [1, 1]
        assert len(requests) == 2
        assert all(not request.get("tools") for request in requests)
        assert all(request["store"] is False for request in requests)
        assert all(request["stream"] is True for request in requests)

    asyncio.run(asyncio.wait_for(run(), timeout=5))
