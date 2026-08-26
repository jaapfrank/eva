from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

import pytest
from eva.adapters.postgres.delegated_work import DelegatedWorkReleaseSelector
from eva.bootstrap import delegated_work_service as service_module
from eva.bootstrap.delegated_work_service import (
    DelegatedWorkService,
    DelegatedWorkServiceSettings,
    OpenAIDelegatedWorkSettings,
    check_openai_delegated_work,
    main,
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
        release=DelegatedWorkReleaseSelector(
            worker_profile_release_sha256="11" * 32,
            model_profile_release_sha256="22" * 32,
            worker_program_sha256="33" * 32,
            engine_release_sha256="44" * 32,
            output_schema_sha256="55" * 32,
        ),
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


def test_health_check_validates_dependencies_lease_database_and_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[object] = []
    settings = OpenAIDelegatedWorkSettings.from_environment(
        {
            "EVA_DATABASE_DSN_FILE": "/run/eva/database-dsn",
            "EVA_MODEL_ACCESS_LEASE_FILE": "/run/eva/model-lease/auth.json",
            "EVA_RUNTIME_TENANT_ID": "11111111-1111-1111-1111-111111111111",
            "EVA_RUNTIME_INSTALLATION_ID": "22222222-2222-2222-2222-222222222222",
            "EVA_DELEGATED_WORKER_ID": "subagent-worker:one",
        }
    )

    class Connection:
        def __enter__(self):  # type: ignore[no-untyped-def]
            return self

        def __exit__(self, *_args):  # type: ignore[no-untyped-def]
            return None

        def scalar(self, statement):  # type: ignore[no-untyped-def]
            observed.append(str(statement))
            return "4e_0004_delegated_work_stop"

    class Engine:
        def connect(self) -> Connection:
            return Connection()

        def dispose(self) -> None:
            observed.append("disposed")

    monkeypatch.setattr(
        "eva.bootstrap.delegated_work_provider.verify_delegated_provider_dependencies",
        lambda: observed.append("dependencies"),
    )
    monkeypatch.setattr(
        "eva.bootstrap.delegated_work_provider.validate_delegated_model_access_lease",
        lambda path: observed.append(path),
    )
    monkeypatch.setattr(service_module, "read_runtime_secret_file", lambda *_a, **_k: "dsn")
    monkeypatch.setattr(service_module.sa, "create_engine", lambda dsn, **_k: Engine())

    assert check_openai_delegated_work(settings) is None
    assert observed == [
        "dependencies",
        Path("/run/eva/model-lease/auth.json"),
        "SELECT version_num FROM public.eva_alembic_version",
        "disposed",
    ]


def test_entrypoint_accepts_only_check_or_no_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []
    monkeypatch.setattr(
        service_module,
        "check_openai_delegated_work",
        lambda: observed.append("checked"),
    )
    assert main(["--check"]) == 0
    assert observed == ["checked"]
    with pytest.raises(RuntimeError, match="arguments are invalid"):
        main(["--unknown"])


def test_provider_entrypoint_uses_one_async_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: dict[str, object] = {}
    runner = object()
    release = DelegatedWorkReleaseSelector(
        worker_profile_release_sha256="11" * 32,
        model_profile_release_sha256="22" * 32,
        worker_program_sha256="33" * 32,
        engine_release_sha256="44" * 32,
        output_schema_sha256="55" * 32,
    )

    @asynccontextmanager
    async def open_runner(model_access_lease_file: Path):
        observed["lease"] = model_access_lease_file
        observed["provider_open"] = True
        try:
            yield runner
        finally:
            observed["provider_open"] = False

    async def serve(  # type: ignore[no-untyped-def]
        selected, *, release, settings, once
    ):
        assert observed["provider_open"] is True
        observed["runner"] = selected
        observed["service"] = settings
        observed["release"] = release
        observed["once"] = once
        return "completed"

    monkeypatch.setattr(service_module, "_open_provider_runner", open_runner)
    monkeypatch.setattr(service_module, "_provider_release", lambda: release)
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
        "release": release,
        "service": settings.service,
        "once": True,
    }
