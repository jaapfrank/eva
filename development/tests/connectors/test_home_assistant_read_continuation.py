"""Retained failed GETs allow a new choice, never completion or mutation replay."""
from copy import deepcopy
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import TimelineEvent
from eva.application.conversation import ConversationActionCall, ConversationActionReceipt
from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions, _has_completed_tool_evidence
from eva.contracts.canonical_json import canonical_sha256
from tests.connectors.test_mcp_discovery import TURN


def failed_read(kind="RECOVERY_FAILED", mutation=None):
    args = {"transport": "rest", "method": "GET", "path": "/api/states/binary_sensor.fixture",
            "body_json": "", "response_pointer": "", "offset": 0, "limit": 1}
    admission = {"action_name": "home_assistant_request", "action_call_id": "old-read",
        "action_arguments": args, "conversation_id": TURN.conversation_id,
        "principal_id": TURN.principal_id, "source_message_id": TURN.message_id,
        "request_tool_name": "home_assistant.api.request", "request_arguments": deepcopy(args),
        "request_expected_proof": {}, "request_tool_configuration_sha256": "a" * 64,
        "receipt_output": {"operation_kind": "invoke", "external_state_verified": False},
        "observation_output_key": "result"}
    if mutation:
        mutation(admission)
    data = (("TOOL_REQUEST_ADMITTED", admission), (kind, {"error_fingerprint": "b" * 64}))
    events = tuple(TimelineEvent(uuid4(), "historical-read", i, f"cp-{i}", "op", event,
        {"data": value}, canonical_sha256(value), datetime.now(UTC))
        for i, (event, value) in enumerate(data))
    route = PostgresExternalConversationTools(None, SimpleNamespace(action_names=("home_assistant_request",)), None)
    return route._receipt(TURN, events), events


@pytest.mark.parametrize("kind,status", [("RECOVERY_FAILED", "failed"), ("STATE_VERIFICATION_TIMED_OUT", "timed_out")])
def test_original_failed_read_stays_failed_but_new_verified_read_can_complete(kind, status):
    receipt, events = failed_read(kind)
    assert receipt.output["status"] == status
    assert "result" not in receipt.output
    assert BackgroundConnectorActions.can_continue_after(TURN, receipt)
    assert not _has_completed_tool_evidence([receipt])
    assert "proof" not in events[-1].payload["data"]
    fresh = ConversationActionReceipt(ConversationActionCall("new-read", "home_assistant_request", {}),
        {"status": "verified", "result": {"success": True}})
    assert _has_completed_tool_evidence([receipt, fresh])
    assert not _has_completed_tool_evidence([fresh, receipt])


@pytest.mark.parametrize("mutation", [
    lambda a: a["action_arguments"].update(method="POST"),
    lambda a: a["action_arguments"].update(transport="websocket"),
    lambda a: a["action_arguments"].update(body_json="{}"),
    lambda a: a.update(request_tool_name="unknown.request"),
    lambda a: a.update(request_tool_configuration_sha256="missing"),
    lambda a: a.update(request_expected_proof={"state": "on"}),
    lambda a: a.update(observation_output_key="other"),
    lambda a: a["request_arguments"].update(path="/api/other"),
    lambda a: a["receipt_output"].update(external_state_verified=True),
])
def test_no_recovery_for_mutations_or_unbound_admissions(mutation):
    receipt, _ = failed_read(mutation=mutation)
    assert not BackgroundConnectorActions.can_continue_after(TURN, receipt)
    assert not _has_completed_tool_evidence([receipt])


@pytest.mark.parametrize("kind", ["RECOVERY_NEEDS_HELP", "TOOL_REJECTED", "STATE_VERIFICATION_CONFLICT"])
def test_unknown_or_conflicting_outcome_remains_closed(kind):
    receipt, _ = failed_read(kind)
    assert not BackgroundConnectorActions.can_continue_after(TURN, receipt)
