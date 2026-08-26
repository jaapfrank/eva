"""Release composition checks for the first real delegated provider."""

from __future__ import annotations

import asyncio
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
from pydantic_ai.models.function import FunctionModel

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
            "adapters/model/openai_child_provider.py",
            "adapters/postgres/delegated_work.py",
            "adapters/subagents/tool_free_child.py",
            "bootstrap/__init__.py",
            "bootstrap/delegated_work_provider.py",
            "bootstrap/delegated_work_service.py",
            "bootstrap/secrets.py",
            "contracts/delegated_work.py",
            "operations/delegated_work_worker.py",
        )
    }
    assert WORKER_PROGRAM_SHA256 == canonical_sha256(
        {"contract": "EvaDelegatedWorkerProgram/v1", "source_sha256": sources}
    )
    lock_sha256 = hashlib.sha256(
        (EVA2_ROOT / "requirements-delegated-work.lock").read_bytes()
    ).hexdigest()
    assert DEPENDENCY_LOCK_SHA256 == lock_sha256
    assert ENGINE_RELEASE_SHA256 == canonical_sha256(
        {
            "contract": "EvaDelegatedEngineRelease/v1",
            "dependency_lock_sha256": lock_sha256,
            "openai_sdk": "3.3.1",
            "pydantic_ai": "2.32.1",
        }
    )


def test_isolated_worker_lock_has_only_its_narrow_direct_roots() -> None:
    actual = (EVA2_ROOT / "requirements-delegated-work.lock").read_text(
        encoding="utf-8"
    )
    roots = (
        EVA2_ROOT / "release/delegated-work-dependency-roots.in"
    ).read_text(encoding="utf-8")
    assert {
        line for line in roots.splitlines() if line and not line.startswith("#")
    } == {
        "openai==3.3.1",
        "psycopg[binary]==3.3.4",
        "pydantic==2.12.5",
        "pydantic-ai-slim==2.32.1",
        "SQLAlchemy==2.0.52",
    }
    for required in (
        "openai==3.3.1 \\\n",
        "psycopg==3.3.4 \\\n",
        "psycopg-binary==3.3.4 \\\n",
        "pydantic-ai-slim==2.32.1 \\\n",
        "sqlalchemy==2.0.52 \\\n",
    ):
        assert required in actual
    for forbidden in ("alembic==", "cryptography==", "dbos==", "fastapi==", "uvicorn=="):
        assert forbidden not in actual


def test_dependency_versions_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    installed = {"openai": "3.3.1", "pydantic-ai-slim": "2.32.1"}
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
