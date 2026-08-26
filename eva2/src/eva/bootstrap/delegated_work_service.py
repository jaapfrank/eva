"""Separate-process service loop for Eva's durable, bounded subagent work.

The service deliberately receives a closed runner from the release bootstrap.
Task text can therefore never select a model, provider, price or worker program.
"""

from __future__ import annotations

import asyncio
import os
import signal
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

import sqlalchemy as sa

from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
from eva.adapters.postgres.operational_timeline import TimelineScope
from eva.bootstrap.secrets import read_runtime_secret_file
from eva.operations.delegated_work_worker import ChildRunner, DelegatedWorkWorker


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
    settings: DelegatedWorkServiceSettings | None = None,
    once: bool = False,
    clock: Callable[[], datetime] | None = None,
) -> Literal["stopped", "completed"]:
    """Construct the scoped store without exposing DSN or profiles in argv/logs."""

    selected = settings or DelegatedWorkServiceSettings.from_environment()
    database_dsn = read_runtime_secret_file(
        selected.database_dsn_file,
        setting_name="EVA_DATABASE_DSN_FILE",
    )
    engine = sa.create_engine(database_dsn, pool_pre_ping=True)
    try:
        worker = DelegatedWorkWorker(
            PostgresDelegatedWorkStore(
                engine,
                TimelineScope(selected.tenant_id, selected.installation_id),
            ),
            runner,
            worker_id=selected.worker_id,
            clock=clock or (lambda: datetime.now(UTC).replace(microsecond=0)),
        )
        service = DelegatedWorkService(
            worker,
            poll_interval_seconds=selected.poll_interval_seconds,
        )
        return asyncio.run(service.serve(once=once))
    finally:
        engine.dispose()


__all__ = [
    "DelegatedWorkService",
    "DelegatedWorkServiceSettings",
    "run_delegated_work_service",
]
