"""Run the isolated durable-goal wake and recovery worker."""

from __future__ import annotations

from eva.operations.restore_activation import require_restore_activation

import asyncio
from datetime import UTC, datetime
import os
import signal
from threading import Event

import sqlalchemy as sa

from eva.adapters.connectors.durable_goal_http import HttpDurableGoalConnector
from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.adapters.postgres.connection import sqlalchemy_postgres_dsn
from eva.application.durable_goals import DurableGoalWorker
from eva.adapters.credentials.runtime_files import read_runtime_secret_file
from eva.adapters.credentials.runtime_files import (
    required_environment as _required_environment,
)


def _database_dsn() -> str:
    return read_runtime_secret_file(
        _required_environment("EVA_DATABASE_DSN_FILE"),
        setting_name="EVA_DATABASE_DSN_FILE",
    )


def main(*, select_goals=None, worker_factory=None, failure_handler=None) -> int:
    """Run the existing loop, optionally with a bootstrap-owned composition."""
    if (select_goals is None) != (worker_factory is None):
        raise ValueError("goal selector and worker factory must be configured together")
    database_dsn = _database_dsn()
    tenant_id = _required_environment("EVA_RUNTIME_TENANT_ID")
    installation_id = _required_environment("EVA_RUNTIME_INSTALLATION_ID")
    connector = None if worker_factory is not None else HttpDurableGoalConnector(
        provider=_required_environment("EVA_DURABLE_GOAL_PROVIDER"),
        base_url=_required_environment("EVA_DURABLE_GOAL_PROVIDER_URL"),
        allow_insecure_http=(
            os.environ.get("EVA_DURABLE_GOAL_ALLOW_INSECURE_HTTP", "false") == "true"
        ),
        timeout_seconds=float(os.environ.get("EVA_DURABLE_GOAL_TIMEOUT_SECONDS", "5")),
    )
    poll_seconds = float(os.environ.get("EVA_DURABLE_GOAL_POLL_SECONDS", "2"))
    if not 0.2 <= poll_seconds <= 60:
        raise RuntimeError("EVA_DURABLE_GOAL_POLL_SECONDS is outside 0.2..60")
    engine = sa.create_engine(sqlalchemy_postgres_dsn(database_dsn), pool_pre_ping=True)
    try:
        require_restore_activation(engine)
    except BaseException:
        engine.dispose()
        raise
    store = PostgresDurableGoalStore(
        engine, tenant_id=tenant_id, installation_id=installation_id
    )
    worker = None if worker_factory is not None else DurableGoalWorker(store, connector, connector, connector)
    stop = Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        while not stop.is_set():
            observed_at = datetime.now(tz=UTC).replace(microsecond=0)
            goals = (store.waiting(observed_before=observed_at, limit=64) if select_goals is None
                     else select_goals(store, observed_at))
            for goal in goals:
                if stop.is_set():
                    break
                try:
                    current_worker = (worker if worker_factory is None else
                                      worker_factory(engine, store, goal, tenant_id, installation_id))
                    assert current_worker is not None
                    asyncio.run(current_worker.on_wake(goal))
                except Exception as error:
                    status = (failure_handler(store, goal, error, observed_at) if failure_handler is not None
                              else store.record_worker_failure(
                        goal,
                        f"{type(error).__name__}:{error}",
                        datetime.now(tz=UTC).replace(microsecond=0),
                    ))
                    print(
                        f"Durable goal {goal.goal_id} {status} after "
                        f"{type(error).__name__}",
                        flush=True,
                    )
            stop.wait(poll_seconds)
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main"]
