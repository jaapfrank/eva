from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pytest
from eva.bootstrap import delegated_work_service as service_module
from eva.bootstrap.delegated_work_service import (
    DelegatedWorkService,
    DelegatedWorkServiceSettings,
    OpenAIDelegatedWorkSettings,
    serve_openai_delegated_work,
)
from eva.operations.delegated_work_worker import DelegatedWorkWorker


class _Worker:
    def __init__(self) -> None:
        self.event: asyncio.Event | None = None

    async def run_once(self, *, cancellation_event=None):  # type: ignore[no-untyped-def]
        self.event = cancellation_event
        return "idle"


def test_settings_are_closed_and_dsn_is_only_a_file_reference() -> None:
    environment = {
        "EVA_DATABASE_DSN_FILE": "/run/eva/database-dsn",
        "EVA_RUNTIME_TENANT_ID": "11111111-1111-1111-1111-111111111111",
        "EVA_RUNTIME_INSTALLATION_ID": "22222222-2222-2222-2222-222222222222",
        "EVA_DELEGATED_WORKER_ID": "subagent-worker:one",
        "EVA_DELEGATED_WORK_POLL_SECONDS": "0.2",
        "EVA_CONVERSATION_MODEL": "must-not-be-consumed-here",
    }
    settings = DelegatedWorkServiceSettings.from_environment(environment)
    assert settings.database_dsn_file == "/run/eva/database-dsn"
    assert settings.tenant_id == UUID(environment["EVA_RUNTIME_TENANT_ID"])
    assert settings.installation_id == UUID(
        environment["EVA_RUNTIME_INSTALLATION_ID"]
    )
    assert settings.worker_id == "subagent-worker:one"
    assert settings.poll_interval_seconds == 0.2

    for changed in (
        environment | {"EVA_DATABASE_DSN_FILE": "postgresql://inline"},
        environment | {"EVA_DATABASE_DSN": "postgresql://inline"},
        environment | {"EVA_DELEGATED_WORK_POLL_SECONDS": "0.01"},
        environment | {"EVA_RUNTIME_TENANT_ID": "invalid"},
        {
            key: value
            for key, value in environment.items()
            if key != "EVA_DELEGATED_WORKER_ID"
        },
    ):
        with pytest.raises((RuntimeError, ValueError)):
            DelegatedWorkServiceSettings.from_environment(changed)


def test_one_shot_service_passes_its_process_stop_event_to_the_worker() -> None:
    worker = _Worker()
    service = DelegatedWorkService(  # type: ignore[arg-type]
        worker, poll_interval_seconds=0.05
    )
    assert asyncio.run(service.serve(once=True)) == "completed"
    assert isinstance(worker.event, asyncio.Event)
    assert worker.event.is_set() is False


class _ClaimThenFenceStore:
    def __init__(self) -> None:
        self.start_call_count = 0

    def claim_next(self, **_kwargs):  # type: ignore[no-untyped-def]
        return object()

    def start_call(self, _claim, **_kwargs):  # type: ignore[no-untyped-def]
        self.start_call_count += 1
        return None


class _UnusedRunner:
    async def run(self, *_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise AssertionError("runner must remain behind the durable call fence")


def test_process_stop_gets_one_event_loop_turn_between_claim_and_call_fence() -> None:
    store = _ClaimThenFenceStore()
    worker = DelegatedWorkWorker(  # type: ignore[arg-type]
        store,
        _UnusedRunner(),
        worker_id="subagent-worker:stop-boundary",
    )

    async def stop_after_claim() -> str:
        stop = asyncio.Event()
        asyncio.get_running_loop().call_soon(stop.set)
        return await worker.run_once(cancellation_event=stop)

    assert asyncio.run(stop_after_claim()) == "crashed_before_call"
    assert store.start_call_count == 0


def test_provider_entrypoint_accepts_only_file_and_release_authority() -> None:
    environment = {
        "EVA_DATABASE_DSN_FILE": "/run/eva/database-dsn",
        "EVA_MODEL_ACCESS_LEASE_FILE": "/run/eva/model-lease/auth.json",
        "EVA_RUNTIME_TENANT_ID": "11111111-1111-1111-1111-111111111111",
        "EVA_RUNTIME_INSTALLATION_ID": "22222222-2222-2222-2222-222222222222",
        "EVA_DELEGATED_WORKER_ID": "subagent-worker:one",
    }
    settings = OpenAIDelegatedWorkSettings.from_environment(environment)
    assert settings.model_access_lease_file == Path(
        "/run/eva/model-lease/auth.json"
    )

    for forbidden in (
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "EVA_DELEGATED_MODEL",
        "EVA_DELEGATED_MODEL_ENDPOINT",
    ):
        with pytest.raises(RuntimeError, match="authority is forbidden"):
            OpenAIDelegatedWorkSettings.from_environment(
                environment | {forbidden: "attacker-controlled"}
            )


def test_provider_entrypoint_uses_one_async_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    runner = object()

    @asynccontextmanager
    async def open_runner(*, model_access_lease_file: Path):
        observed["lease"] = model_access_lease_file
        observed["provider_open"] = True
        try:
            yield runner
        finally:
            observed["provider_open"] = False

    async def serve(selected, *, settings, once):  # type: ignore[no-untyped-def]
        assert observed["provider_open"] is True
        observed["runner"] = selected
        observed["service"] = settings
        observed["once"] = once
        return "completed"

    monkeypatch.setattr(service_module, "open_delegated_work_runner", open_runner)
    monkeypatch.setattr(service_module, "serve_delegated_work_service", serve)
    settings = OpenAIDelegatedWorkSettings.from_environment(
        {
            "EVA_DATABASE_DSN_FILE": "/run/eva/database-dsn",
            "EVA_MODEL_ACCESS_LEASE_FILE": "/run/eva/model-lease/auth.json",
            "EVA_RUNTIME_TENANT_ID": "11111111-1111-1111-1111-111111111111",
            "EVA_RUNTIME_INSTALLATION_ID": "22222222-2222-2222-2222-222222222222",
            "EVA_DELEGATED_WORKER_ID": "subagent-worker:one",
        }
    )

    assert asyncio.run(serve_openai_delegated_work(settings, once=True)) == "completed"
    assert observed == {
        "lease": Path("/run/eva/model-lease/auth.json"),
        "provider_open": False,
        "runner": runner,
        "service": settings.service,
        "once": True,
    }
