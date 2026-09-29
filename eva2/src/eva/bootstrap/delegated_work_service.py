"""Separate-process service loop for Eva's durable, bounded subagent work.

The service deliberately receives a closed runner from the release bootstrap.
Task text can therefore never select a model, provider, price or worker program.
"""

from __future__ import annotations

from eva.operations.restore_activation import require_restore_activation

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
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    external_timeline_scope,
)
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.operations.delegated_work_worker import ChildRunner, DelegatedWorkWorker

_SCHEMA_HEAD = "4e_0009_restore_hold"


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
    scope: TimelineScope
    worker_id: str
    poll_interval_seconds: float = 0.25
    concurrency: int = 1
    health_port: int | None = None

    def __post_init__(self) -> None:
        if self.health_port is not None and (type(self.health_port) is not int or not 1 <= self.health_port <= 65535):
            raise ValueError("delegated worker health port must be 1..65535")
        if not os.path.isabs(self.database_dsn_file):
            raise ValueError("delegated worker DSN file must be absolute")
        if not self.worker_id or len(self.worker_id) > 120:
            raise ValueError("delegated worker ID is invalid")
        if type(self.concurrency) is not int or not 1 <= self.concurrency <= 2:
            raise ValueError("delegated worker concurrency must be 1 or 2")
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
            # Managed owner work is stored in the dashboard authority namespace.
            # Older standalone workers retain their normalized runtime namespace.
            storage_ids = (values.get("EVA_DASHBOARD_TENANT_ID", "").strip(),
                           values.get("EVA_DASHBOARD_INSTALLATION_ID", "").strip())
            if any(storage_ids):
                scope = TimelineScope(UUID(required("EVA_DASHBOARD_TENANT_ID")),
                                      UUID(required("EVA_DASHBOARD_INSTALLATION_ID")))
            else:
                scope = external_timeline_scope(
                    required("EVA_RUNTIME_TENANT_ID"),
                    required("EVA_RUNTIME_INSTALLATION_ID"),
                    "delegated worker scope is required",
                )
            interval = float(values.get("EVA_DELEGATED_WORK_POLL_SECONDS", "0.25"))
            concurrency = int(values.get("EVA_DELEGATED_WORK_CONCURRENCY", "1"))
            raw_health_port = values.get("EVA_DELEGATED_WORK_HEALTH_PORT", "").strip()
            health_port = int(raw_health_port) if raw_health_port else None
        except ValueError as error:
            raise RuntimeError("delegated worker process settings are invalid") from error
        return cls(
            database_dsn_file=required("EVA_DATABASE_DSN_FILE"),
            scope=scope,
            worker_id=required("EVA_DELEGATED_WORKER_ID"),
            poll_interval_seconds=interval,
            concurrency=concurrency,
            health_port=health_port,
        )


@dataclass(frozen=True, slots=True)
class OpenAIDelegatedWorkSettings:
    """Closed process settings; sensitive bytes remain in mounted files."""

    service: DelegatedWorkServiceSettings
    model_access_lease_file: Path
    research_enabled: bool = False
    digital_enabled: bool = False
    research_budget_file: Path | None = None
    research_tenant_id: str | None = None
    research_installation_id: str | None = None
    household_work_config_file: Path | None = None

    def __post_init__(self) -> None:
        if self.household_work_config_file is not None and (
                not self.household_work_config_file.is_absolute() or not self.research_enabled):
            raise ValueError("household work requires public research and an absolute configuration file")
        if type(self.digital_enabled) is not bool:
            raise ValueError("digital switch must be a boolean")
        if type(self.research_enabled) is not bool:
            raise ValueError("research switch must be a boolean")
        if self.research_enabled and (self.research_budget_file is None
                or not self.research_budget_file.is_absolute()
                or not self.research_tenant_id or not self.research_installation_id):
            raise ValueError("research requires an absolute budget file and runtime scope")
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
        enabled = values.get("EVA_DELEGATED_RESEARCH_ENABLED", "false")
        if enabled not in {"true", "false"}:
            raise RuntimeError("EVA_DELEGATED_RESEARCH_ENABLED must be true or false")
        digital = values.get("EVA_DELEGATED_DIGITAL_ENABLED", "false")
        if digital not in {"true", "false"}:
            raise RuntimeError("EVA_DELEGATED_DIGITAL_ENABLED must be true or false")
        research = {}
        if enabled == "true":
            names = ("EVA_RUNTIME_RESOURCE_BUDGET_FILE", "EVA_RUNTIME_TENANT_ID", "EVA_RUNTIME_INSTALLATION_ID")
            if any(not values.get(name, "").strip() for name in names):
                raise RuntimeError("research budget file and runtime scope are required")
            research = dict(research_enabled=True,
                research_budget_file=Path(values[names[0]].strip()),
                research_tenant_id=values[names[1]].strip(),
                research_installation_id=values[names[2]].strip())
        return cls(
            **research,
            digital_enabled=digital == "true",
            household_work_config_file=(Path(values["EVA_HOUSEHOLD_WORK_CONFIG_FILE"])
                if values.get("EVA_HOUSEHOLD_WORK_CONFIG_FILE") else None),
            service=DelegatedWorkServiceSettings.from_environment(values),
            model_access_lease_file=Path(lease),
        )


def _research_composition(settings: OpenAIDelegatedWorkSettings):
    """Read only the same installed budget, bound to this worker's runtime scope."""
    from eva.adapters.credentials.runtime_files import read_private_runtime_file_bytes
    from eva.contracts.runtime_budget import RuntimeResourceBudget
    from eva.bootstrap.delegated_research import build_public_research_composition
    if not settings.research_enabled:
        raise RuntimeError("public research is not enabled")
    budget = RuntimeResourceBudget.from_bytes(
        read_private_runtime_file_bytes(str(settings.research_budget_file),
            setting_name="EVA_RUNTIME_RESOURCE_BUDGET_FILE"),
        expected_tenant_id=settings.research_tenant_id,
        expected_installation_id=settings.research_installation_id)
    return build_public_research_composition(model_access_lease_file=settings.model_access_lease_file,
        resource_budget=budget)


class DelegatedWorkService:
    """Poll one PostgreSQL authority through a bounded set of worker lanes."""

    def __init__(
        self,
        worker: DelegatedWorkWorker,
        *,
        poll_interval_seconds: float,
        additional_workers: tuple[DelegatedWorkWorker, ...] = (),
        health_port: int | None = None,
    ) -> None:
        if not 0.05 <= poll_interval_seconds <= 5:
            raise ValueError("delegated worker poll interval must be 0.05..5 seconds")
        if len(additional_workers) > 1 or any(item is worker for item in additional_workers):
            raise ValueError("delegated service needs at most two distinct workers")
        if health_port is not None and (type(health_port) is not int or not 1 <= health_port <= 65535):
            raise ValueError("delegated worker health port must be 1..65535")
        self._health_port = health_port
        self._worker = worker
        self._workers = (worker, *additional_workers)
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
        async def lane(worker):
            try:
                while not stop.is_set():
                    disposition = await worker.run_once(cancellation_event=stop)
                    if once:
                        return
                    if disposition == "idle":
                        try:
                            await asyncio.wait_for(stop.wait(), timeout=self._poll_interval_seconds)
                        except TimeoutError:
                            pass
            except BaseException:
                # Ask sibling calls to stop through their existing durable
                # cancellation path; do not abandon a running provider thread.
                stop.set()
                raise

        tasks = [asyncio.create_task(lane(worker))
                 for worker in (self._workers[:1] if once else self._workers)]
        group = asyncio.gather(*tasks)
        try:
            try:
                if self._health_port is None:
                    await asyncio.shield(group)
                else:
                    from eva.bootstrap.worker_liveness import worker_liveness
                    async with worker_liveness(port=self._health_port, stop=stop, lanes=tuple(tasks)):
                        await asyncio.shield(group)
            except BaseException:
                stop.set()
                # Join every lane before disposing the shared engine/provider.
                await asyncio.shield(asyncio.gather(*tasks, return_exceptions=True))
                raise
            return "stopped" if stop.is_set() else "completed"
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
    digital_settings=None,
) -> Literal["stopped", "completed"]:
    """Run the service inside an existing async provider lifecycle."""

    selected = settings or DelegatedWorkServiceSettings.from_environment()
    database_dsn = read_runtime_secret_file(
        selected.database_dsn_file,
        setting_name="EVA_DATABASE_DSN_FILE",
    )
    engine = sa.create_engine(sqlalchemy_postgres_dsn(database_dsn), pool_pre_ping=True)
    try:
        require_restore_activation(engine)
        workers = []
        lane_count = 1 if once else selected.concurrency
        for index in range(lane_count):
            store = PostgresDelegatedWorkStore(engine, selected.scope,
                household_authorizer=getattr(runner, "household_authorizer", None))
            bound_runner = runner.bind_store(store) if callable(getattr(runner, "bind_store", None)) else runner
            lane_release = release
            if digital_settings is not None:
                from eva.bootstrap.delegated_digital_composition import (
                    build_digital_runner, DigitalEnabledDelegatedRunner)
                expected_scope = TimelineScope(UUID(digital_settings.dashboard_tenant_id),
                                               UUID(digital_settings.dashboard_installation_id))
                if expected_scope != store.scope:
                    raise ValueError("digital worker storage scope differs")
                # Connector and model objects belong to a lane; task counters
                # and journals continue to be created by the existing runner.
                digital_runner = build_digital_runner(digital_settings, store,
                    household_authority=getattr(runner, "household_authority", None))
                bound_runner = DigitalEnabledDelegatedRunner(bound_runner, digital_runner)
                lane_release = (release if isinstance(release, tuple) else (release,)) + (digital_runner.profile.release,)
            worker_id = (selected.worker_id if lane_count == 1 else
                         f"{selected.worker_id[:112]}:slot-{index + 1}")
            workers.append(DelegatedWorkWorker(
                store,
                bound_runner,
                worker_id=worker_id,
                release=lane_release,
                clock=clock or (lambda: datetime.now(UTC).replace(microsecond=0)),
            ))
        service = DelegatedWorkService(
            workers[0],
            additional_workers=tuple(workers[1:]),
            poll_interval_seconds=selected.poll_interval_seconds,
            health_port=selected.health_port,
        )
        return await service.serve(once=once)
    finally:
        engine.dispose()


async def serve_openai_delegated_work(
    settings: OpenAIDelegatedWorkSettings,
    *,
    once: bool = False,
) -> Literal["stopped", "completed"]:
    """Run bounded worker lanes; each task opens its own provider session."""

    digital_settings = None
    if settings.digital_enabled:
        from eva.bootstrap.runtime import RuntimeSettings
        digital_settings = RuntimeSettings.from_environment()
        if not digital_settings.delegated_digital_enabled:
            raise ValueError("digital worker and runtime settings differ")
    if settings.research_enabled:
        from eva.bootstrap.delegated_research import serve_research_enabled_work
        research, profile = _research_composition(settings)
        authority = None
        if settings.household_work_config_file is not None:
            from eva.bootstrap.household_work_composition import load_household_work_authority
            authority = load_household_work_authority(settings.household_work_config_file,
                scope=settings.service.scope, profile=profile,
                model_lease_file=settings.model_access_lease_file)
        return await serve_research_enabled_work(settings, research, profile, once=once,
                                                household_authority=authority, digital_settings=digital_settings)

    async with _open_provider_runner(settings.model_access_lease_file) as runner:
        return await serve_delegated_work_service(
            runner,
            release=_provider_release(),
            digital_settings=digital_settings,
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
    household_authority = None
    if selected.research_enabled:
        _, profile = _research_composition(selected)
        if selected.household_work_config_file is not None:
            from eva.bootstrap.household_work_composition import load_household_work_authority
            household_authority = load_household_work_authority(selected.household_work_config_file,
                scope=selected.service.scope, profile=profile,
                model_lease_file=selected.model_access_lease_file)
    database_dsn = read_runtime_secret_file(
        selected.service.database_dsn_file,
        setting_name="EVA_DATABASE_DSN_FILE",
    )
    engine = sa.create_engine(sqlalchemy_postgres_dsn(database_dsn), pool_pre_ping=True)
    try:
        if selected.digital_enabled:
            from eva.bootstrap.runtime import RuntimeSettings
            from eva.bootstrap.delegated_digital_composition import build_digital_runner
            runtime_settings = RuntimeSettings.from_environment()
            if (not runtime_settings.delegated_digital_enabled or
                    TimelineScope(UUID(runtime_settings.dashboard_tenant_id),
                                  UUID(runtime_settings.dashboard_installation_id)) != selected.service.scope):
                raise ValueError("digital worker runtime scope differs")
            digital = build_digital_runner(runtime_settings,
                PostgresDelegatedWorkStore(engine, selected.service.scope), household_authority=household_authority)
            digital.profile.release  # Validate exact source pins without a model request.
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
