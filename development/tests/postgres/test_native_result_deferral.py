"""Only proven unsent native announcements may be reclaimed."""
import asyncio
import pytest
from tests.postgres.test_household_public_work import setup, start
from tests.postgres.test_scheduled_failure_return import database


@pytest.mark.parametrize("terminal", ["offered", "ambiguous", "interrupted"])
def test_native_busy_deferral_reclaims_once_but_attempted_results_never_replay(database, terminal):
    from concurrent.futures import ThreadPoolExecutor
    x = setup(database, "hh-defer-" + terminal)
    work_id = start(x)
    assert asyncio.run(x.worker.run_once()) == "succeeded"
    claim = x.service.poll(x.turn)
    identity = dict(work_id=work_id, result_sha256=claim["result_sha256"])
    for _ in range(2):
        x.service.result_delivery(x.turn, "defer", **identity)
        with ThreadPoolExecutor(max_workers=2) as pool:
            claims = list(pool.map(lambda _: x.service.poll(x.turn), range(2)))
        assert sum(item is not None for item in claims) == 1
        assert next(item for item in claims if item is not None) == claim
    x.service.result_delivery(x.turn, "ack", disposition=terminal, **identity)
    assert x.service.poll(x.turn) is None
    with pytest.raises(ValueError, match="cannot be deferred"):
        x.service.result_delivery(x.turn, "defer", **identity)
    assert x.research.calls == 1
