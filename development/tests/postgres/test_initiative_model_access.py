"""Local access loss defers one claim without weakening unknown-dispatch fencing."""
from datetime import timedelta
import pytest
from eva.adapters.postgres.operational_timeline import CheckpointConflict
from tests.postgres.test_initiative_preparation_recovery import _review, costless_native, NOW


@pytest.mark.parametrize('household', [False, True])
def test_access_deferral_survives_store_reclaim_without_retrying_unknown_work(costless_native, household):
    auth, old, owner, store, binding = _review(costless_native, household=household)
    claim = store.mark_model_started(store.claim_next(now=NOW), now=NOW)
    store.defer_before_dispatch(claim, reason='model_access_unavailable', now=NOW)
    state = store._timeline.load(claim.thread_id)
    assert state.phase == 'will_waiting_admission'
    assert state.status == 'waiting_for_proof'
    assert state.retry_count == 0
    event = store._timeline.events(claim.thread_id)[-1]
    assert event.payload['data']['provider_dispatched'] is False
    assert store.claim_next(now=NOW, include_waiting=False) is None
    resumed = store.claim_next(now=NOW + timedelta(seconds=1))
    assert resumed.thread_id == claim.thread_id
    assert resumed.operation_id == claim.operation_id
    resumed = store.mark_model_started(resumed, now=NOW + timedelta(seconds=1))
    store.fail(resumed, RuntimeError('unknown after dispatch'), now=NOW + timedelta(seconds=1))
    before = store._timeline.events(claim.thread_id)
    with pytest.raises(CheckpointConflict):
        store.defer_before_dispatch(resumed, reason='model_access_unavailable', now=NOW + timedelta(seconds=2))
    assert store._timeline.events(claim.thread_id) == before
    assert store._timeline.load(claim.thread_id).phase == 'will_usage_uncertain'
