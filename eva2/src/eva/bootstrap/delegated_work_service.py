"""Separate-process service loop for Eva's durable, bounded subagent work.

The service deliberately receives a closed runner from the release bootstrap.
Task text can therefore never select a model, provider, price or worker program.
"""

from __future__ import annotations

import asyncio
import os
import signal
import sys
from contextlib import asynccontextmanager
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.postgres.delegated_work import (
    DelegatedWorkReleaseSelector,
    PostgresDelegatedWorkStore,
)
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.bootstrap.secrets import read_runtime_secret_file
from eva.operations.delegated_work_worker import ChildRunner, DelegatedWorkWorker

_SCHEMA_HEAD = "4e_0006_memory_outbox_erasure"


def _provider_release() -> DelegatedWorkReleaseSelector:
    from eva.adapters.subagents.tool_free_child import OUTPUT_SCHEMA_SHA256
    from eva.bootstrap.delegated_work_provider import (
        ENGINE_RELEASE_SHA256,
        MODEL_PROFILE_RELEASE_SHA256,
        WORKER_PROFILE_RELEASE_SHA256,
        WORKER_PROGRAM_SHA256,
    )

    return DelegatedWorkReleaseSelector(
        worker_profile_release_sha256=WORKER_PROFILE_RELEASE_SHA256,
        model_profile_release_sha256=MODEL_PROFILE_RELEASE_SHA256,
        worker_program_sha256=WORKER_PROGRAM_SHA256,
        engine_release_sha256=ENGINE_RELEASE_SHA256,
        output_schema_sha256=OUTPUT_SCHEMA_SHA256,
    )


@asynccontextmanager
async def _open_provider_runner(model_access_lease_file: Path):
    from eva.bootstrap.delegated_work_provider import open_delegated_work_runner

    async with open_delegated_work_runner(
        model_access_lease_file=model_access_lease_file
    ) as runner:
        yield runner


@dataclass(frozen=True, slots=True)
class DelegatedWorkServiceSettings:
    """Non-secret process settings plus one private DSN-file reference."""

    database_dsn_file: str
    tenant_id: UUID
    installation_id: UUID
    worker_id: str
    poll_interval_seconds: float = 0.25

    def __post_init__(self) -> None:
        if not os.path.isabs(self.database_dsn_file):
            raise ValueError("delegated worker DSN file must be absolute")
        if not self.worker_id or len(self.worker_id) > 120:
            raise ValueError("delegated worker ID is invalid")
        if not 0.05 <= self.poll_interval_seconds <= 5:
            raise ValueError("delegated worker poll interval must be 0.05..5 seconds")

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> DelegatedWorkServiceSettings:
        values = os.environ if environment is None else environment
        if "EVA_DATABASE_DSN" in values:
            raise RuntimeError(
                "EVA_DATABASE_DSN is forbidden; use EVA_DATABASE_DSN_FILE"
            )

        def required(name: str) -> str:
            value = values.get(name, "").strip()
            if not value:
                raise RuntimeError(f"{name} is required")
            return value

        try:
            tenant_id = UUID(required("EVA_RUNTIME_TENANT_ID"))
            installation_id = UUID(required("EVA_RUNTIME_INSTALLATION_ID"))
            interval = float(values.get("EVA_DELEGATED_WORK_POLL_SECONDS", "0.25"))
        except ValueError as error:
            raise RuntimeError("delegated worker process settings are invalid") from error
        return cls(
            database_dsn_file=required("EVA_DATABASE_DSN_FILE"),
            tenant_id=tenant_id,
            installation_id=installation_id,
            worker_id=required("EVA_DELEGATED_WORKER_ID"),
            poll_interval_seconds=interval,
        )


@dataclass(frozen=True, slots=True)
class OpenAIDelegatedWorkSettings:
    """Closed process settings; sensitive bytes remain in mounted files."""

    service: DelegatedWorkServiceSettings
    model_access_lease_file: Path

    def __post_init__(self) -> None:
        if not self.model_access_lease_file.is_absolute():
            raise ValueError("delegated model lease file must be absolute")

    @classmethod
    def from_environment(
        cls, environment: Mapping[str, str] | None = None
    ) -> OpenAIDelegatedWorkSettings:
        values = os.environ if environment is None else environment
        forbidden = {
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "EVA_DELEGATED_MODEL",
            "EVA_DELEGATED_MODEL_ENDPOINT",
        }
        if forbidden & set(values):
            raise RuntimeError("inline delegated provider authority is forbidden")
        lease = values.get("EVA_MODEL_ACCESS_LEASE_FILE", "").strip()
        if not lease:
            raise RuntimeError("EVA_MODEL_ACCESS_LEASE_FILE is required")
        return cls(
            service=DelegatedWorkServiceSettings.from_environment(values),
            model_access_lease_file=Path(lease),
        )


class DelegatedWorkService:
    """Poll one PostgreSQL authority and run at most one child at a time."""

    def __init__(
        self,
        worker: DelegatedWorkWorker,
        *,
        poll_interval_seconds: float,
    ) -> None:
        if not 0.05 <= poll_interval_seconds <= 5:
            raise ValueError("delegated worker poll interval must be 0.05..5 seconds")
        self._worker = worker
        self._poll_interval_seconds = poll_interval_seconds

    async def serve(self, *, once: bool = False) -> Literal["stopped", "completed"]:
        """Run in one OS process; SIGTERM/SIGINT reaches the active model call."""

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        installed: list[signal.Signals] = []
        try:
            for requested_signal in (signal.SIGTERM, signal.SIGINT):
                loop.add_signal_handler(requested_signal, stop.set)
                installed.append(requested_signal)
        except (NotImplementedError, RuntimeError) as error:
            for installed_signal in installed:
                loop.remove_signal_handler(installed_signal)
            raise RuntimeError(
                "delegated worker requires process signal handling"
            ) from error
        try:
            while not stop.is_set():
                disposition = await self._worker.run_once(cancellation_event=stop)
                if once:
                    return "stopped" if stop.is_set() else "completed"
                if disposition == "idle":
                    try:
                        await asyncio.wait_for(
                            stop.wait(), timeout=self._poll_interval_seconds
                        )
                    except TimeoutError:
                        pass
            return "stopped"
        finally:
            for requested_signal in installed:
                loop.remove_signal_handler(requested_signal)


def run_delegated_work_service(
    runner: ChildRunner,
    *,
    release: DelegatedWorkReleaseSelector,
    settings: DelegatedWorkServiceSettings | None = None,
    once: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> Literal["stopped", "completed"]:
    """Construct the scoped store without exposing DSN or profiles in argv/logs."""

    return asyncio.run(
        serve_delegated_work_service(
            runner,
            release=release,
            settings=settings,
            once=once,
            clock=clock,
        )
    )


async def serve_delegated_work_service(
    runner: ChildRunner,
    *,
    release: DelegatedWorkReleaseSelector,
    settings: DelegatedWorkServiceSettings | None = None,
    once: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> Literal["stopped", "completed"]:
    """Run the service inside an existing async provider lifecycle."""

    selected = settings or DelegatedWorkServiceSettings.from_environment()
    database_dsn = read_runtime_secret_file(
        selected.database_dsn_file,
        setting_name="EVA_DATABASE_DSN_FILE",
    )
    engine = sa.create_engine(sqlalchemy_postgres_dsn(database_dsn), pool_pre_ping=True)
    try:
        worker = DelegatedWorkWorker(
            PostgresDelegatedWorkStore(
                engine,
                TimelineScope(selected.tenant_id, selected.installation_id),
            ),
            runner,
            worker_id=selected.worker_id,
            release=release,
            clock=clock or (lambda: datetime.now(UTC).replace(microsecond=0)),
        )
        service = DelegatedWorkService(
            worker,
            poll_interval_seconds=selected.poll_interval_seconds,
        )
        return await service.serve(once=once)
    finally:
        engine.dispose()


async def serve_openai_delegated_work(
    settings: OpenAIDelegatedWorkSettings,
    *,
    once: bool = False,
) -> Literal["stopped", "completed"]:
    """Keep provider, model runner and PostgreSQL worker on one event loop."""

    async with _open_provider_runner(settings.model_access_lease_file) as runner:
        return await serve_delegated_work_service(
            runner,
            release=_provider_release(),
            settings=settings.service,
            once=once,
        )


def check_openai_delegated_work(
    settings: OpenAIDelegatedWorkSettings | None = None,
) -> None:
    """Check the sealed sidecar inputs without starting a provider request."""

    from eva.bootstrap.delegated_work_provider import (
        validate_delegated_model_access_lease,
        verify_delegated_provider_dependencies,
    )

    selected = settings or OpenAIDelegatedWorkSettings.from_environment()
    verify_delegated_provider_dependencies()
    validate_delegated_model_access_lease(selected.model_access_lease_file)
    database_dsn = read_runtime_secret_file(
        selected.service.database_dsn_file,
        setting_name="EVA_DATABASE_DSN_FILE",
    )
    engine = sa.create_engine(sqlalchemy_postgres_dsn(database_dsn), pool_pre_ping=True)
    try:
        with engine.connect() as connection:
            revision = connection.scalar(
                sa.text("SELECT version_num FROM public.eva_alembic_version")
            )
            if revision != _SCHEMA_HEAD:
                raise RuntimeError("delegated worker schema head differs")
    finally:
        engine.dispose()


def main(arguments: list[str] | None = None) -> int:
    """Run until SIGTERM/SIGINT; configuration failures return non-zero."""

    selected_arguments = sys.argv[1:] if arguments is None else arguments
    if selected_arguments == ["--check"]:
        check_openai_delegated_work()
        return 0
    if selected_arguments:
        raise RuntimeError("delegated worker arguments are invalid")
    asyncio.run(
        serve_openai_delegated_work(
            OpenAIDelegatedWorkSettings.from_environment()
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DelegatedWorkService",
    "DelegatedWorkServiceSettings",
    "OpenAIDelegatedWorkSettings",
    "check_openai_delegated_work",
    "run_delegated_work_service",
    "serve_delegated_work_service",
    "serve_openai_delegated_work",
]
