"""Provider-owned confirmation routing retains current authority and scope."""
import hashlib
from types import SimpleNamespace

import pytest

from eva.adapters.connectors.home_assistant import HomeAssistantConversationEffectPlanner
from eva.adapters.postgres.conversation_actions import PostgresConversationActions
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt, ConversationConflict, ConversationTurn


def turn():
    return ConversationTurn.private("tenant", "installation", "owner", "conversation",
                                    "message", "Doe de lamp uit", "2026-10-19T14:00:00Z")


def receipt(name="set_home_assistant_light", status="verified"):
    return ConversationActionReceipt(ConversationActionCall("call", name,
        {"state": "off", "target": "buitenverlichting"}), {"status": status})


def actions(authorize=lambda t: None):
    planner = HomeAssistantConversationEffectPlanner(None, allowlist=frozenset(),
        principal_sha256_allowlist=frozenset({hashlib.sha256(b"owner").hexdigest()}))
    provider = PostgresExternalConversationTools(None, planner, None, authorize_turn=authorize)
    return PostgresConversationActions(None, external_tools=(provider,))


@pytest.mark.parametrize("name", ["set_home_assistant_light", "set_home_assistant_switch"])
def test_verified_small_effect_confirmation_rechecks_current_authority(name):
    checked = []
    t = turn()
    assert actions(checked.append).contact_reply_allowed(t, receipt(name)) is True
    assert checked == [t]


@pytest.mark.parametrize("status", ["failed", "ambiguous", "timed_out", "rejected"])
def test_uncertain_effect_is_not_classified_as_simple_confirmation(status):
    assert actions().contact_reply_allowed(turn(), receipt(status=status)) is False


def test_unrelated_verified_work_is_not_classified_by_the_home_provider():
    assert actions().contact_reply_allowed(turn(), receipt("improve_isolated_repository")) is False


def test_revoked_authority_cannot_obtain_a_contact_confirmation():
    def revoked(t):
        raise ConversationConflict("authority revoked")
    with pytest.raises(ConversationConflict, match="authority revoked"):
        actions(revoked).contact_reply_allowed(turn(), receipt())


def test_only_the_registered_receipt_owner_can_opt_in():
    owner = SimpleNamespace(action_names=("bounded_action",))
    other = SimpleNamespace(action_names=("other_action",),
        contact_reply_allowed=lambda *args: pytest.fail("wrong provider consulted"))
    routed = PostgresConversationActions(None, external_tools=(owner, other))
    assert routed.contact_reply_allowed(turn(), receipt("bounded_action")) is False


@pytest.mark.parametrize("name", ["set_home_assistant_light", "set_home_assistant_switch"])
def test_available_composite_action_needs_no_model_discovery_round(name):
    from eva.adapters.model.tool_namespaces import deferred_namespaces
    definition = {"type": "function", "name": name, "parameters": {"type": "object"}}
    offered, namespaces = deferred_namespaces([definition])
    assert offered == [{**definition, "defer_loading": False}]
    assert namespaces == {}
    assert deferred_namespaces([]) == ([], {})


def partial_receipt():
    r = receipt(status="failed")
    states = {"light.a": "off", "light.c": "unknown", "light.b": "unavailable"}
    return ConversationActionReceipt(r.call, {**r.output, "entity_id": "light.group",
        "requested_state": "off", "proof": {
            "expected": {"entity_id": "light.group", "state": "off"},
            "observed": {"entity_id": "light.group", "state": "off", "member_states": states},
            "evidence": {"entity_id": "light.group", "source": "home_assistant",
                "group_verification": "partial", "group_state_confirmed": True,
                "member_states": states, "unverified_member_ids": ["light.b", "light.c"]}}})


def test_observed_member_uncertainty_can_be_explained_without_work_escalation():
    checked = []
    r = partial_receipt()
    assert actions(checked.append).contact_reply_allowed(turn(), r) is True
    assert len(checked) == 1
    assert r.output["status"] == "failed"  # Routing never upgrades the proof.


@pytest.mark.parametrize("field,value", [
    ("source", "other_provider"), ("group_verification", "invalid_membership"),
    ("group_state_confirmed", False), ("group_state_confirmed", "true"),
    ("unverified_member_ids", []), ("unverified_member_ids", ["light.a"]),
    ("unverified_member_ids", ["light.b", "light.b", "light.c"]),
    ("member_states", {"light.a": "off"}), ("entity_id", "light.other")])
def test_partial_confirmation_requires_exact_provider_evidence(field, value):
    r = partial_receipt()
    r.output["proof"]["evidence"][field] = value
    assert actions().contact_reply_allowed(turn(), r) is False


def test_partial_result_cannot_bypass_revoked_authority():
    def revoked(t):
        raise ConversationConflict("authority revoked")
    with pytest.raises(ConversationConflict, match="authority revoked"):
        actions(revoked).contact_reply_allowed(turn(), partial_receipt())


def test_partial_result_must_match_requested_target_and_state():
    r = partial_receipt()
    r.output["proof"]["expected"]["entity_id"] = "light.other"
    assert actions().contact_reply_allowed(turn(), r) is False
