"""Release composition checks for the first real delegated provider."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

import pytest
from eva.bootstrap import delegated_work_provider as provider_module
from eva.bootstrap.delegated_work_provider import (
    ENGINE_RELEASE_SHA256,
    MODEL_PROFILE_ID,
    MODEL_PROFILE_RELEASE_SHA256,
    WORKER_PROFILE_ID,
    WORKER_PROFILE_RELEASE_SHA256,
    WORKER_PROGRAM_SHA256,
    open_delegated_work_runner,
    verify_delegated_provider_dependencies,
)
from pydantic_ai.models.function import FunctionModel

EVA2_ROOT = Path(__file__).resolve().parents[2]


def test_release_profile_is_closed_and_hash_bound() -> None:
    assert MODEL_PROFILE_ID == "model:codex:gpt-5.4-mini:v1"
    assert WORKER_PROFILE_ID == "worker:onderzoeker:v1"
    for value in (
        MODEL_PROFILE_RELEASE_SHA256,
        WORKER_PROFILE_RELEASE_SHA256,
        WORKER_PROGRAM_SHA256,
        ENGINE_RELEASE_SHA256,
    ):
        assert re.fullmatch(r"[0-9a-f]{64}", value)


def test_isolated_worker_lock_is_exactly_the_reviewed_sdk_candidate() -> None:
    candidate = (
        EVA2_ROOT / "experiments/openai-sdk-v1/requirements.lock"
    ).read_text(encoding="utf-8")
    expected = candidate.replace(
        "eva2/experiments/openai-sdk-v1/requirements.lock",
        "eva2/requirements-delegated-work.lock",
    ).replace(
        "eva2/experiments/openai-sdk-v1/requirements.in",
        "eva2/release/delegated-work-dependency-roots.in",
    )
    actual = (EVA2_ROOT / "requirements-delegated-work.lock").read_text(
        encoding="utf-8"
    )
    assert actual == expected
    roots = (
        EVA2_ROOT / "release/delegated-work-dependency-roots.in"
    ).read_text(encoding="utf-8")
    assert "openai==3.3.1" in roots
    assert "pydantic-ai-slim==2.32.1" in roots
    assert "openai==3.3.1 \\\n" in actual


def test_dependency_versions_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    installed = {"openai": "3.3.1", "pydantic-ai-slim": "2.32.1"}
    monkeypatch.setattr(provider_module, "version", installed.__getitem__)
    verify_delegated_provider_dependencies()

    installed["openai"] = "3.3.2"
    with pytest.raises(RuntimeError, match="release differs"):
        verify_delegated_provider_dependencies()


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


def test_factory_uses_only_the_sealed_provider_profile(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    model = FunctionModel(lambda _messages, _info: "unused")

    class Provider:
        def __init__(self, settings, *, credential_loader) -> None:  # type: ignore[no-untyped-def]
            observed["settings"] = settings
            observed["credential_loader"] = credential_loader

        async def __aenter__(self):  # type: ignore[no-untyped-def]
            return model

        async def __aexit__(self, *_args):  # type: ignore[no-untyped-def]
            return None

    monkeypatch.setattr(
        provider_module,
        "version",
        {"openai": "3.3.1", "pydantic-ai-slim": "2.32.1"}.__getitem__,
    )
    monkeypatch.setattr(provider_module, "OpenAIChildProvider", Provider)

    async def open_runner() -> None:
        async with open_delegated_work_runner(
            model_access_lease_file=Path("/run/eva/model-lease/auth.json")
        ) as runner:
            assert runner._registry.resolve  # noqa: SLF001
        settings = observed["settings"]
        assert settings.model_name == "gpt-5.4-mini"
        assert settings.endpoint == (
            "https://chatgpt.com/backend-api/codex/responses"
        )
        assert settings.reasoning_effort == "low"
        assert callable(observed["credential_loader"])

    asyncio.run(open_runner())
