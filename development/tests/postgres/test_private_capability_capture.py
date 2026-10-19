"""Bounded host capability metadata enters only the current private review source."""
from copy import deepcopy
from datetime import timedelta

import pytest
import sqlalchemy as sa

from eva.adapters.postgres.current_catalog import metadata
from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
from eva.application.free_will import FreeWillDecision
from eva.application.private_capability_context import build_private_capability_context
from eva.application.resident_attention import NextAttention
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256
from tests.postgres.test_owner_initiatives import costless_native, NOW
from tests.postgres.test_household_observation_initiative import _binding

BASE_PROFILE = "private-orientation-curiosity-contract-v4"


class Reader:
    def __init__(self):
        self.calls = []
        self.values = []
        self.description = "A declared current host capability; live health is unknown."

    def __call__(self, policy, mandate_sha, now):
        self.calls.append((deepcopy(policy), mandate_sha, now))
        names = {c["action_name"] for c in policy["capabilities"]} | set(
            policy.get("digital_work", {}).get("capability_ids", ()))
        value = build_private_capability_context(policy, mandate_sha, observed_at=now,
            descriptors={name: {"description": self.description} for name in names})
        self.values.append(value)
        return value


def source(auth, old, reader=None, profile=BASE_PROFILE):
    return PostgresSensoryEventStore(auth.owner._engine, tenant_id=old.tenant_id,
        installation_id=old.installation_id, initiative_principal_id=old.principal_id,
        initiative_authorizer=auth.policy, context_reviews_only=True,
        private_review_profile=profile, private_capability_context_reader=reader)


@pytest.fixture
def bound(costless_native):
    auth, old, _, owner, _, _, _ = costless_native
    owner.stop(old.intent_id, now=NOW)
    return auth, old


def complete(store, at=NOW, *, attention=None):
    assert store.schedule_context_review(now=at) == "scheduled"
    claim = store.mark_model_started(store.claim_next(now=at), now=at)
    store.complete(claim, FreeWillDecision("silence", "A considered private decision", 0,
        "synthetic-model", next_attention=attention), now=at)
    return claim


def empty(store):
    assert store._timeline.list_checkpoints(thread_prefix=store._decision_prefix, limit=4) == ()


def test_exact_builder_metadata_is_immutable_and_time_does_not_resample(bound):
    auth, old = bound
    reader = Reader()
    store = source(auth, old, reader)
    claim = complete(store)
    payload = claim.event.payload
    captured = deepcopy(reader.values[0])
    assert payload["private_capability_context"] == captured
    assert payload["review_profile_base"] == BASE_PROFILE
    expected = "pc-" + canonical_sha256({"base_profile": BASE_PROFILE,
        "capability_configuration_sha256": captured["configuration_sha256"]})
    assert payload["review_profile"] == expected and len(expected) <= 80
    assert captured["source_mandate_sha256"] == reader.calls[0][1]
    assert captured["observed_at"] == NOW.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert captured["is_execution_permission"] is False
    assert len(canonical_json_bytes(captured)) <= 8192
    original = store._timeline.events(claim.thread_id)
    reader.values[0]["capabilities"][0]["description"] = "Mutated outside the stored source"
    assert store._timeline.events(claim.thread_id) == original
    restarted = source(auth, old, reader)
    assert restarted.schedule_context_review(now=NOW + timedelta(minutes=1)) == "idle"
    assert reader.values[-1]["observed_at"] != captured["observed_at"]
    assert reader.values[-1]["configuration_sha256"] == captured["configuration_sha256"]
    assert store._timeline.events(claim.thread_id) == original


def test_changed_catalogue_has_one_distinct_source_without_a_model_profile_bump(bound):
    auth, old = bound
    reader = Reader()
    first = source(auth, old, reader)
    old_claim = complete(first)
    before = first._timeline.events(old_claim.thread_id)
    reader.description = "A genuinely changed existing host capability description."
    changed = source(auth, old, reader)
    new_claim = complete(changed, NOW + timedelta(minutes=1))
    assert old_claim.thread_id != new_claim.thread_id
    assert new_claim.thread_id.rsplit(":", 1)[1].isdigit()
    assert new_claim.event.payload["review_cause"] == "installed_profile_change"
    assert new_claim.event.payload["review_window"] == old_claim.event.payload["review_window"]
    assert new_claim.event.payload["review_profile_base"] == old_claim.event.payload["review_profile_base"]
    assert new_claim.event.payload["review_profile"] != old_claim.event.payload["review_profile"]
    assert first._timeline.events(old_claim.thread_id) == before
    assert source(auth, old, reader).schedule_context_review(now=NOW + timedelta(minutes=2)) == "idle"


def test_default_none_preserves_original_hourly_source_and_payload(bound):
    auth, old = bound
    store = source(auth, old)
    claim = complete(store)
    assert claim.thread_id.rsplit(":", 1)[1] == str(int(NOW.timestamp()) // 3600)
    assert claim.event.payload["review_profile"] == BASE_PROFILE
    assert "private_capability_context" not in claim.event.payload
    assert "review_profile_base" not in claim.event.payload
    assert source(auth, old).schedule_context_review(now=NOW + timedelta(minutes=1)) == "idle"


def test_null_base_profile_still_has_a_stable_effective_catalogue_identity(bound):
    auth, old = bound
    reader = Reader()
    first = source(auth, old, reader, profile=None)
    claim = complete(first)
    assert claim.event.payload["review_profile_base"] is None
    assert claim.event.payload["review_profile"].startswith("pc-")
    assert source(auth, old, reader, profile=None).schedule_context_review(
        now=NOW + timedelta(minutes=1)) == "idle"


@pytest.mark.parametrize("phase", ["unknown", "inflight", "expression_pending"])
def test_unresolved_reviews_block_catalogue_capture_and_new_sources(bound, phase):
    auth, old = bound
    reader = Reader()
    first = source(auth, old, reader)
    assert first.schedule_context_review(now=NOW) == "scheduled"
    claim = first.mark_model_started(first.claim_next(now=NOW), now=NOW)
    if phase == "unknown":
        first.fail(claim, RuntimeError("Lost response"), now=NOW)
    elif phase == "expression_pending":
        # Durable pending transport state; no real recipient or send.
        first._timeline.transition(thread_id=claim.thread_id, expected_revision=claim.revision,
            checkpoint_id=claim.thread_id + ":synthetic-pending", operation_id=claim.operation_id,
            phase="expression_pending", status="completed", event_type="PROACTIVE_EXPRESSION_PREPARED",
            occurred_at=NOW, data={"disposition": "respond"})
    before = first._timeline.events(claim.thread_id)
    reader.calls.clear()
    reader.description = "Changed while an older outcome is unresolved"
    assert source(auth, old, reader).schedule_context_review(now=NOW + timedelta(minutes=1)) == "idle"
    assert reader.calls == [] and first._timeline.events(claim.thread_id) == before


def test_future_model_attention_precedes_catalogue_capture(bound):
    auth, old = bound
    reader = Reader()
    first = source(auth, old, reader)
    claim = complete(first, attention=NextAttention(NOW + timedelta(hours=2), "A meaningful later review"))
    before = first._timeline.events(claim.thread_id)
    reader.calls.clear()
    reader.description = "Changed before the selected future attention"
    assert source(auth, old, reader).schedule_context_review(now=NOW + timedelta(minutes=1)) == "idle"
    assert reader.calls == [] and first._timeline.events(claim.thread_id) == before


def test_active_private_work_precedes_catalogue_capture(costless_native):
    auth, old, _, _, _, _, _ = costless_native
    reader = Reader()
    store = source(auth, old, reader)
    assert store.schedule_context_review(now=NOW) == "idle"
    assert reader.calls == []
    empty(store)


def revoke(auth):
    table = metadata.tables["eva_core.principal_bindings"]
    with auth.owner._engine.begin() as connection:
        changed = connection.execute(sa.update(table).where(
            table.c.tenant_id == auth.owner._authority.tenant_id,
            table.c.principal_binding_id == auth.owner._binding).values(status="revoked"))
        assert changed.rowcount == 1


def test_revoked_owner_is_rejected_before_catalogue_capture(bound):
    auth, old = bound
    reader = Reader()
    store = source(auth, old, reader)
    revoke(auth)
    assert store.schedule_context_review(now=NOW) == "mandate_unavailable"
    assert reader.calls == []
    empty(store)


def test_owner_revoked_during_capture_cannot_commit_a_source(bound):
    auth, old = bound
    base = Reader()
    def reader(policy, mandate_sha, now):
        value = base(policy, mandate_sha, now)
        revoke(auth)
        return value
    store = source(auth, old, reader)
    assert store.schedule_context_review(now=NOW) == "mandate_unavailable"
    empty(store)


@pytest.mark.parametrize("corruption", ["contract", "mandate", "time", "configuration", "names", "permission", "bytes"])
def test_invalid_metadata_cannot_enter_an_immutable_source(bound, corruption):
    auth, old = bound
    base = Reader()
    def reader(policy, mandate_sha, now):
        value = base(policy, mandate_sha, now)
        if corruption == "contract": value["contract"] = "OtherContract/v1"
        elif corruption == "mandate": value["source_mandate_sha256"] = "f" * 64
        elif corruption == "time": value["observed_at"] = (now + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        elif corruption == "names": value["capabilities"] = []
        elif corruption == "permission": value["is_execution_permission"] = True
        elif corruption == "bytes": value["capabilities"][0]["description"] = "x" * 9000
        value["configuration_sha256"] = canonical_sha256({k:v for k,v in value.items()
            if k not in {"observed_at", "configuration_sha256"}})
        if corruption == "configuration": value["configuration_sha256"] = "0" * 64
        return value
    store = source(auth, old, reader)
    with pytest.raises(ValueError, match="private capability context"):
        store.schedule_context_review(now=NOW)
    empty(store)


@pytest.mark.parametrize("kind", ["passive", "shared"])
def test_private_catalogue_reader_is_not_allowed_in_shared_or_passive_store(bound, kind):
    auth, old = bound
    args = {"tenant_id": old.tenant_id, "installation_id": old.installation_id,
        "private_capability_context_reader": Reader()}
    if kind == "shared":
        args.update(context_reviews_only=True, initiative_principal_id=old.principal_id,
            initiative_authorizer=auth.policy, household_binding=_binding(old.tenant_id))
    with pytest.raises(ValueError, match="private capability context"):
        PostgresSensoryEventStore(auth.owner._engine, **args)
