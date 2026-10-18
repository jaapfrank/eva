"""Recorded own results reach private initiative history without granting effects."""
from datetime import timedelta
import hashlib

import pytest

from eva.adapters.postgres.completed_contribution import CompletedContributionActions
from eva.adapters.postgres.operational_timeline import PostgresOperationalTimeline
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.conversation import ConversationActionCall, ConversationTurn
from tests.postgres.test_completed_contribution import costless_native, NOW


@pytest.fixture
def contributions(costless_native):
    auth, old, interests, owner, sensory, path, policy = costless_native
    owner.stop(old.intent_id, now=NOW)
    timeline = PostgresOperationalTimeline(owner._engine, owner._scope)

    def complete(index, content):
        at = NOW + timedelta(seconds=index + 1)
        def artifact(turn, work, path):
            return {"content": content,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "source_provenance": {"work_id": work, "artifact_path": path,
                    "read_receipt_sha256": "a" * 64}}
        actions = CompletedContributionActions(lambda: auth, lambda turn: str(owner._owner),
            artifact, lambda turn: None, lambda turn: None, lambda: at,
            execution_namespace="synthetic-result-continuity")
        turn = ConversationTurn.private(owner._tenant_id, owner._installation_id, str(owner._owner),
            "synthetic-result-continuity", f"synthetic-result-{index}", "Existing contribution.",
            at.strftime("%Y-%m-%dT%H:%M:%SZ"))
        call = ConversationActionCall(f"register-{index}", "register_own_contribution", {
            "title": f"Synthetic contribution {index}", "motivation": "Synthetic context",
            "source_work_id": f"work:synthetic-result-{index}",
            "artifact_path": f"/proof-state/synthetic-result-{index}.md"})
        intent = actions.execute(turn, call).output["operation_result"]["intent_id"]
        return intent, "intent:" + intent

    return owner, sensory, timeline, complete


def _phase(timeline, thread, phase, event_type, at, **data):
    current = timeline.load(thread)
    timeline.transition(thread_id=thread, expected_revision=current.revision,
        checkpoint_id=f"{thread}:fixture:{current.revision + 1}", phase=phase,
        status="completed", event_type=event_type, occurred_at=at, data=data)


@pytest.mark.parametrize("content", ["A synthetic useful result.", "é" * 601, "x" * 1199 + "🙂end"])
def test_completed_result_has_exact_source_and_utf8_byte_prefix(contributions, content):
    owner, sensory, timeline, complete = contributions
    intent, thread = complete(0, content)
    owner.feedback(intent, "Synthetic scope feedback", now=NOW + timedelta(seconds=2),
        request_key="result-continuity-feedback")
    before = timeline.events(thread)
    history = sensory.recent_initiatives()
    item = next(row for row in history if row["title"] == "Synthetic contribution 0")
    result = item["recorded_result"]
    artifact = before[1]
    assert result == {
        "content": content.encode("utf-8")[:1200].decode("utf-8", errors="ignore"),
        "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "event_sha256": artifact.payload_sha256,
        "occurred_at": artifact.occurred_at.isoformat(),
        "classification": "recorded_result_not_live_proof",
        "truncated": len(content.encode("utf-8")) > 1200,
        "is_execution_permission": False,
    }
    assert len(result["content"].encode("utf-8")) <= 1200
    assert item["owner_feedback"] == "Synthetic scope feedback"
    assert timeline.events(thread) == before
    other = PostgresSensoryEventStore(owner._engine, tenant_id=owner._tenant_id,
        installation_id=owner._installation_id, initiative_principal_id="other-owner")
    assert other.recent_initiatives() == ()


def test_only_three_newest_successes_include_results_blocked_stopped_history_remains(contributions):
    owner, sensory, timeline, complete = contributions
    values = [complete(index, f"Synthetic body {index}") for index in range(6)]
    _phase(timeline, values[0][1], "completed", "SYNTHETIC_COMPLETION_REVIEWED",
        NOW + timedelta(seconds=40))
    _phase(timeline, values[1][1], "release_ready", "SYNTHETIC_RELEASE_REVIEWED",
        NOW + timedelta(seconds=41))
    _phase(timeline, values[4][1], "initiative_blocked", "PERSONAL_INITIATIVE_BLOCKED",
        NOW + timedelta(seconds=42), intent_id=values[4][0])
    _phase(timeline, values[5][1], "personal_stopped", "PERSONAL_INITIATIVE_STOPPED",
        NOW + timedelta(seconds=43), intent_id=values[5][0])
    history = sensory.recent_initiatives()
    enriched = [row for row in history if "recorded_result" in row]
    assert [row["title"] for row in enriched] == [
        "Synthetic contribution 1", "Synthetic contribution 0", "Synthetic contribution 3"]
    for index in (2, 4, 5):
        item = next(row for row in history if row["title"] == f"Synthetic contribution {index}")
        assert set(item) == {"title", "question", "phase"}


@pytest.mark.parametrize("event_type", ["PERSONAL_PROTOTYPE_COMPLETED", "PERSONAL_INITIATIVE_COMPLETED"])
def test_latest_completed_artifact_is_used_and_missing_artifact_stays_unchanged(contributions, event_type):
    owner, sensory, timeline, complete = contributions
    intent, thread = complete(0, "Original synthetic result")
    content = "Later synthetic artifact"
    _phase(timeline, thread, "release_ready", event_type, NOW + timedelta(seconds=3),
        intent_id=intent, content=content, content_sha256=hashlib.sha256(content.encode()).hexdigest())
    item = next(row for row in sensory.recent_initiatives() if "recorded_result" in row)
    assert item["recorded_result"]["content"] == content
    # The original fixture has no artifact; only its old anti-repeat metadata is returned.
    old = next(row for row in sensory.recent_initiatives() if row["phase"] == "personal_stopped")
    assert set(old) == {"title", "question", "phase"}


@pytest.mark.parametrize("corruption", ["content_hash", "intent"])
def test_mismatched_artifact_never_enters_private_history(contributions, corruption):
    owner, sensory, timeline, complete = contributions
    intent, thread = complete(0, "Original synthetic result")
    content = "Synthetic changed result"
    _phase(timeline, thread, "initiative_completed", "PERSONAL_INITIATIVE_COMPLETED",
        NOW + timedelta(seconds=2),
        intent_id="personal-interest:" + "f" * 64 if corruption == "intent" else intent,
        content=content, content_sha256="0" * 64 if corruption == "content_hash" else
            hashlib.sha256(content.encode()).hexdigest())
    with pytest.raises(RuntimeError, match="recorded result differs"):
        sensory.recent_initiatives()


def test_completed_record_without_artifact_keeps_title_question_phase_only(contributions, costless_native):
    owner, sensory, timeline, complete = contributions
    old = costless_native[1]
    _phase(timeline, "intent:" + old.intent_id, "completed", "SYNTHETIC_LEGACY_COMPLETED",
        NOW + timedelta(seconds=1))
    assert len(sensory.recent_initiatives()) == 1
    assert set(sensory.recent_initiatives()[0]) == {"title", "question", "phase"}
