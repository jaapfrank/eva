"""Completed own prose returns once, without inventing verified world facts."""
from datetime import timedelta
import pytest
from tests.postgres.test_subscription_initiative_reviews import review_setup
from tests.postgres.test_initiative_source_feedback import finish, SOURCE
from tests.postgres.test_owner_initiatives import NOW, costless_native

@pytest.mark.parametrize("state", ["succeeded", "ambiguous", "missing_content"])
def test_retained_completed_native_result_returns_once_without_ha_read(review_setup, state):
    *_, store, factory, before, hashes = review_setup
    finish(store)
    value = {**SOURCE[0], "verified_reads": [], "work_outcome": {"state": state},
        "reported_output": {"text": "A retained own result", "output_sha256": "d"*64,
            "semantic_verification": False}}
    if state == "missing_content":
        value["work_outcome"]["state"] = "succeeded"
        value["reported_output"] = None
    at = NOW + timedelta(seconds=1)
    expected = "scheduled" if state == "succeeded" else "idle"
    assert store.schedule_context_review(now=at, source_outcomes=(value,)) == expected
    if expected == "scheduled":
        claim = finish(store, at)
        assert claim.event.payload["native_completion_source"]["result_sha256"] == value["result_sha256"]
        assert store.schedule_context_review(now=at+timedelta(seconds=1), source_outcomes=(value,)) == "idle"
        assert value["reported_output"]["semantic_verification"] is False
