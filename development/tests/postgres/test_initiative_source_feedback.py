"""A retained native completion returns to the same private review once."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from eva.application.free_will import FreeWillDecision
from tests.postgres.test_subscription_initiative_reviews import review_setup
from tests.postgres.test_owner_initiatives import NOW, costless_native

SOURCE = ({"work_id": "work:eva-initiative:" + "a"*64, "spec_sha256": "b"*64, "result_sha256": "c"*64, "verified_reads":[{"status":"verified_original_read"}]},)

def finish(store, now=NOW):
    claim = store.claim_next(now=now)
    claim = store.mark_model_started(claim, now=now)
    store.complete(claim, FreeWillDecision("silence", "No useful action", 0, "synthetic"), now=now)
    return claim

def test_new_result_triggers_one_same_hour_followup_without_clock_replay(review_setup):
    *_, store, factory, before, hashes = review_setup
    initial = finish(store)
    assert store.schedule_context_review(now=NOW+timedelta(seconds=1)) == "idle"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(lambda _: factory().schedule_context_review(
            now=NOW+timedelta(seconds=2), source_outcomes=SOURCE), range(2)))
    assert sorted(outcomes) == ["idle", "scheduled"]
    followup = finish(store, NOW+timedelta(seconds=2))
    assert followup.thread_id != initial.thread_id
    assert followup.event.source == "initiative-review"
    assert followup.event.payload["native_completion_source"] == {k:SOURCE[0][k] for k in ("work_id","spec_sha256","result_sha256")}
    assert followup.thread_id[len(store._decision_prefix):].isdigit()
    assert store.schedule_context_review(now=NOW+timedelta(seconds=3), source_outcomes=SOURCE) == "idle"
    # The normal hourly opportunity is preserved; no new review merely from a
    # refreshed context projection or changed completion-list ordering.
    assert store.schedule_context_review(now=NOW+timedelta(hours=1, seconds=3), source_outcomes=SOURCE) == "scheduled"

def test_unavailable_source_cannot_bypass_hour_or_create_private_review(review_setup):
    *_, store, factory, before, hashes = review_setup
    finish(store)
    unavailable = ({"work_id": SOURCE[0]["work_id"], "status": "unavailable", "verified_reads": []},)
    assert store.schedule_context_review(now=NOW+timedelta(seconds=1), source_outcomes=unavailable) == "idle"

def test_unknown_review_still_blocks_new_completion(review_setup):
    *_, store, factory, before, hashes = review_setup
    claim=store.claim_next(now=NOW)
    claim=store.mark_model_started(claim, now=NOW)
    store.fail(claim, RuntimeError("synthetic lost provider response"), now=NOW)
    assert store._timeline.load(claim.thread_id).phase == "will_usage_uncertain"
    assert store.schedule_context_review(now=NOW+timedelta(hours=2), source_outcomes=SOURCE) == "idle"


def test_private_response_normalization_keeps_host_source_hash(review_setup):
    *_, store, factory, before, hashes = review_setup
    claim=store.claim_next(now=NOW)
    claim=store.mark_model_started(claim, now=NOW)
    store.complete(claim, FreeWillDecision("respond", "Relevant read result", 1, "synthetic",
        expression="This private review must remain silent", source_context_sha256="d"*64), now=NOW)
    event=store._timeline.events(claim.thread_id)[-1]
    assert event.event_type == "WILL_DECIDED"
    assert event.payload["data"]["source_context_sha256"] == "d"*64


def test_partial_verified_read_can_return_without_fabricated_success_result(review_setup):
    *_, store, factory, before, hashes = review_setup
    finish(store)
    source=({"work_id": SOURCE[0]["work_id"], "spec_sha256": "b"*64,
        "result_sha256": None, "outcome_sha256": "e"*64,
        "work_outcome": {"state": "ambiguous"}, "verified_reads":[{"status":"verified_original_read"}]},)
    at=NOW+timedelta(seconds=1)
    assert store.schedule_context_review(now=at, source_outcomes=source) == "scheduled"
    claim=finish(store,at)
    assert claim.event.payload["native_completion_source"] == {
        "work_id":SOURCE[0]["work_id"], "spec_sha256":"b"*64, "outcome_sha256":"e"*64}
    assert store.schedule_context_review(now=at+timedelta(seconds=1), source_outcomes=source) == "idle"


def test_failed_work_without_any_verified_source_does_not_trigger_paid_feedback(review_setup):
    *_, store, factory, before, hashes = review_setup
    finish(store)
    missing=({"work_id":SOURCE[0]["work_id"],"spec_sha256":"b"*64,
        "result_sha256":None,"outcome_sha256":"e"*64,"verified_reads":[]},)
    assert store.schedule_context_review(now=NOW+timedelta(seconds=1),source_outcomes=missing)=="idle"

def test_used_latest_source_does_not_hide_another_pending_source(review_setup):
    *_, store, factory, before, hashes = review_setup
    finish(store)
    first=NOW+timedelta(seconds=1)
    assert store.schedule_context_review(now=first,source_outcomes=SOURCE)=="scheduled"
    finish(store,first)
    older=dict(SOURCE[0],work_id="work:eva-initiative:"+"d"*64,result_sha256="e"*64)
    at=first+timedelta(seconds=1)
    assert store.schedule_context_review(now=at,source_outcomes=SOURCE+(older,))=="scheduled"
    claim=finish(store,at)
    assert claim.event.payload["native_completion_source"]["work_id"]==older["work_id"]
    assert store.schedule_context_review(now=at+timedelta(seconds=1),source_outcomes=SOURCE+(older,))=="idle"
