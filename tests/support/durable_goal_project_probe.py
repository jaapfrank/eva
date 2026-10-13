"""Isolated process probe; fault injection is confined to this test module."""
from __future__ import annotations

import asyncio
import json
import os
import sys

import sqlalchemy as sa

from eva.adapters.connectors.durable_goal_http import HttpDurableGoalConnector
from eva.adapters.postgres.durable_goals import PostgresDurableGoalStore
from eva.application.durable_goals import DurableGoalWorker


def main() -> None:
    dsn, url, goal_id, mode = sys.argv[1:]
    assert "eva2_living_disposable_" in dsn
    assert url.startswith("http://127.0.0.1:")
    engine = sa.create_engine(dsn)
    store = PostgresDurableGoalStore(engine, tenant_id="project-test", installation_id="isolated")
    state = store.state(goal_id, "owner")
    assert state is not None
    connector = HttpDurableGoalConnector(provider="project-test", base_url=url, allow_insecure_http=True)
    original_record = store.record_effect
    original_execute = connector.execute
    original_deliver = connector.deliver

    def record(*args, **kwargs):
        result = original_record(*args, **kwargs)
        if mode == "after-first-checkpoint":
            os._exit(71)
        return result

    async def execute(**kwargs):
        result = await original_execute(**kwargs)
        if mode == "after-first-effect" and "previous_artifact" not in kwargs:
            os._exit(72)
        if mode == "after-second-effect" and "previous_artifact" in kwargs:
            os._exit(73)
        return result

    async def deliver(**kwargs):
        result = await original_deliver(**kwargs)
        if mode == "after-delivery":
            os._exit(74)
        return result

    store.record_effect = record
    connector.execute = execute
    connector.deliver = deliver
    result = asyncio.run(DurableGoalWorker(store, connector, connector, connector).on_wake(state.spec))
    print(json.dumps({"status": result.status, "completed_steps": result.completed_steps}))
    engine.dispose()


if __name__ == "__main__":
    main()
