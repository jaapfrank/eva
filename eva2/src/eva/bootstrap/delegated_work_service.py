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
from eva.adapters.postgres.operational_timeline import (
    TimelineScope,
    external_timeline_scope,
)
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.operations.delegated_work_worker import ChildRunner, DelegatedWorkWorker

_SCHEMA_HEAD = "4e_0007_delegated_private"


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
        except ValueError as error:
            raise RuntimeError("delegated worker process settings are invalid") from error
        return cls(
            database_dsn_file=required("EVA_DATABASE_DSN_FILE"),
            scope=scope,
            worker_id=required("EVA_DELEGATED_WORKER_ID"),
            poll_interval_seconds=interval,
        )


@dataclass(frozen=True, slots=True)
class OpenAIDelegatedWorkSettings:
    """Closed process settings; sensitive bytes remain in mounted files."""

    service: DelegatedWorkServiceSettings
    model_access_lease_file: Path
    research_enabled: bool = False
    research_budget_file: Path | None = None
    research_tenant_id: str | None = None
    research_installation_id: str | None = None
    household_work_config_file: Path | None = None

    def __post_init__(self) -> None:
        if self.household_work_config_file is not None and (
                not self.household_work_config_file.is_absolute() or not self.research_enabled):
            raise ValueError("household work requires public research and an absolute configuration file")
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
        store = PostgresDelegatedWorkStore(engine, selected.scope,
            household_authorizer=getattr(runner, "household_authorizer", None))
        bound_runner = runner.bind_store(store) if callable(getattr(runner, "bind_store", None)) else runner
        worker = DelegatedWorkWorker(
            store,
            bound_runner,
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
    """Run one worker loop; each admitted task opens its own provider session."""

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
                                                household_authority=authority)

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
    if selected.research_enabled:
        _, profile = _research_composition(selected)
        if selected.household_work_config_file is not None:
            from eva.bootstrap.household_work_composition import load_household_work_authority
            load_household_work_authority(selected.household_work_config_file,
                scope=selected.service.scope, profile=profile,
                model_lease_file=selected.model_access_lease_file)
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
