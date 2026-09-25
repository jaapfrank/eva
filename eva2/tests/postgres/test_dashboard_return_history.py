"""Saved text returns survive page loss without replaying work or delivery."""
from dataclasses import replace
from datetime import timedelta

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import operational_events
from tests.postgres.test_scheduled_failure_return import database
from tests.postgres.test_private_work_return import setup, start
from tests.postgres.test_delegated_delivery_store import succeed


def test_history_restores_acknowledged_result_without_new_events(database):
    service, turn, clock = setup(database, "dashboard-history", False)
    spec = start(service, turn)
    saved = succeed(service.store, spec)
    clock[0] += timedelta(seconds=4)
    assert service.private_return(turn, "history")["results"] == []
    result = service.private_return(turn, "poll")["result"]
    assert service.private_return(turn, "history")["results"] == []
    service.private_return(turn, "ack", **{k: result[k] for k in ("work_id", "operation_id")})
    with database.connect() as c:
        before = c.scalar(sa.select(sa.func.count()).select_from(operational_events))
    for _ in range(2):
        history = service.private_return(turn, "history")
        assert history == {"status": "history", "results": [result]}
        assert history["results"][0]["output"] == saved.output
    assert service.private_return(turn, "poll")["result"] is None
    with database.connect() as c:
        assert c.scalar(sa.select(sa.func.count()).select_from(operational_events)) == before
    other, other_turn, _ = setup(database, "dashboard-history-other", False)
    assert other.private_return(other_turn, "history")["results"] == []
    with pytest.raises(ValueError):
        service.private_return(replace(turn, conversation_id="voice:another-session"), "history")
    clock[0] = service.store.load(spec.work_id, **service._owner).retention_until
    assert service.private_return(turn, "history")["results"] == []
