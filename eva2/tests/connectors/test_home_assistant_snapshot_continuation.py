"""A changing house must not abort a paginated, durable user assignment."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from eva.adapters.connectors.home_assistant_admin import (
    HomeAssistantAdminAdapter, HomeAssistantHouseholdTool,
)
from eva.adapters.postgres.external_conversation_tools import PostgresExternalConversationTools
from eva.adapters.postgres.operational_timeline import TimelineEvent
from eva.application.conversation import (
    ConversationActionCall, ConversationActionReceipt, ConversationModelResult,
    IdentityProfile, LivingConversation,
)
from eva.bootstrap.delegated_digital_runner import BackgroundConnectorActions, _has_completed_tool_evidence
from eva.contracts.canonical_json import canonical_sha256
from tests.connectors.test_mcp_gateway import _turn
from tests.application.test_conversation import _Memory


class ChangingHouse(HomeAssistantAdminAdapter):
    def __init__(self):
        super().__init__(base_url="http://ha.invalid", token="fixture")
        self.reads = 0
        self.inventory_value = self._project_household([], [], [], [
            {"entity_id": f"light.room_{n:03}", "state": "off", "attributes": {}}
            for n in range(63)
        ])

    async def household_inventory(self):
        self.reads += 1
        return deepcopy(self.inventory_value)


def arguments(**updates):
    return dict(section="entities", area_id="", domain="light", offset=0,
                snapshot_sha256="", automation_ids=[], **updates)


def command(args):
    return SimpleNamespace(tool_name="read_home_assistant_inventory", arguments=args,
                           expected_proof=args)


def test_followup_pages_keep_the_observation_while_live_states_change():
    reader = ChangingHouse()
    first = asyncio.run(reader.household_page(section="entities", domain="light"))
    reader.inventory_value["entities"][52]["state"] = "on"
    reader.inventory_value["captured_at"] = "2026-10-10T12:01:00Z"
    # Mutating the returned projection also cannot corrupt the retained source.
    first["items"][0]["state"] = "tampered"
    second = asyncio.run(reader.household_page(section="entities", domain="light",
        offset=50, snapshot_sha256=first["snapshot_sha256"]))
    again = asyncio.run(reader.household_page(section="entities", domain="light",
        snapshot_sha256=first["snapshot_sha256"]))
    assert reader.reads == 1
    assert second["items"][2]["state"] == "off"
    assert again["items"][0]["state"] == "off"
    assert second["captured_at"] == first["captured_at"]
    assert second["snapshot_sha256"] == first["snapshot_sha256"]
    assert len({row["entity_id"] for row in again["items"] + second["items"]}) == 63
    fresh = asyncio.run(reader.household_page(section="entities", domain="light", offset=50))
    assert reader.reads == 2
    assert fresh["items"][2]["state"] == "on"
    assert fresh["snapshot_sha256"] != first["snapshot_sha256"]


@pytest.mark.parametrize("section", ["entities", "automation_details", "automation_configurations"])
def test_lost_cursor_is_a_failed_read_then_a_fresh_read_can_succeed(section):
    reader = ChangingHouse()
    tool = HomeAssistantHouseholdTool(reader)
    args = {"section": section, "area_id": "", "offset": 0,
            "snapshot_sha256": "a" * 64,
            "automation_ids": ["known"] if section.startswith("automation_") else []}
    dispatch = asyncio.run(tool.execute(command(args)))
    proof = asyncio.run(tool.observe(command(args), dispatch))
    assert proof.disposition == "conflict"
    assert proof.observed == {}
    assert proof.evidence["source"] == "home_assistant_inventory_read_error"
    assert proof.evidence["error"]["restart"] == {"offset": 0, "snapshot_sha256": ""}
    dispatch.details["read_error"]["message"] = "forged"
    with pytest.raises(RuntimeError, match="observation differs"):
        asyncio.run(tool.observe(command(args), dispatch))
    valid = arguments()
    verified = asyncio.run(tool.observe(command(valid), asyncio.run(tool.execute(command(valid)))))
    assert verified.disposition == "verified"


def test_cache_eviction_preserves_failed_cursor_and_freshness():
    reader = ChangingHouse()
    first = asyncio.run(reader.household_page(section="entities"))
    for version in range(9):
        reader.inventory_value["entities"][0]["state"] = str(version)
        asyncio.run(reader.household_page(section="entities"))
    tool = HomeAssistantHouseholdTool(reader)
    args = arguments()
    args.update(snapshot_sha256=first["snapshot_sha256"], offset=50)
    proof = asyncio.run(tool.observe(command(args), asyncio.run(tool.execute(command(args)))))
    assert proof.disposition == "conflict"
    assert len(reader._household_snapshots) == 8


def test_provider_authority_errors_are_not_reclassified_as_recoverable_reads():
    class Forbidden(ChangingHouse):
        async def household_inventory(self):
            raise PermissionError("access revoked")
    with pytest.raises(PermissionError, match="access revoked"):
        asyncio.run(HomeAssistantHouseholdTool(Forbidden()).execute(command(arguments())))


@pytest.mark.parametrize("historical", [False, True])
def test_real_conversation_receives_read_error_and_finishes_following_service(historical):
    reader = ChangingHouse()
    tool = HomeAssistantHouseholdTool(reader)
    turn = _turn()
    calls = []

    class Route:
        action_names = ("read_home_assistant_inventory", "save_workspace_document")
        def available_for(self, turn): return True
        def active_context(self, turn): return ()
        def pending(self, turn, *, step_index): return None
        def execute(self, turn, call, *, step_index):
            calls.append(call.name)
            if call.name == "save_workspace_document":
                return ConversationActionReceipt(call, {"status": "verified", "document": "house.md"})
            prepared = asyncio.run(tool.prepare(turn, call))
            proof = asyncio.run(tool.observe(command(call.arguments),
                asyncio.run(tool.execute(command(call.arguments)))))
            admission = {
                "action_name": call.name, "action_call_id": call.call_id,
                "action_arguments": call.arguments, "conversation_id": turn.conversation_id,
                "principal_id": turn.principal_id, "source_message_id": turn.message_id,
                "receipt_output": prepared.receipt_output,
                "observation_output_key": prepared.observation_output_key,
            }
            admission.update(request_tool_name=call.name, request_arguments=call.arguments,
                             request_expected_proof=call.arguments)
            verification = {"proof": {"observed": proof.observed, "evidence": proof.evidence}}
            failed_kind = "STATE_VERIFICATION_CONFLICT"
            if historical and proof.disposition == "conflict":
                failed_kind = "RECOVERY_FAILED"
                verification = {"error_fingerprint": "b" * 64}
            events = tuple(TimelineEvent(uuid4(), "read-thread", i, f"cp-{i}", "op", kind,
                {"data": data}, canonical_sha256(data), datetime.now(timezone.utc))
                for i, (kind, data) in enumerate((("TOOL_REQUEST_ADMITTED", admission),
                    (failed_kind if proof.disposition == "conflict"
                     else "STATE_VERIFIED", verification))))
            return PostgresExternalConversationTools(None, tool, tool)._receipt(turn, events)

    class Model:
        responses = 0
        def generate(self, *args, **kwargs):
            self.responses += 1
            if self.responses == 1:
                value = arguments()
                value["snapshot_sha256"] = "a" * 64
                call = ConversationActionCall("stale", "read_home_assistant_inventory", value)
            elif self.responses == 2:
                receipt = kwargs["completed_action"]
                assert receipt.output["status"] == "failed"
                assert "inventory" not in receipt.output
                if historical:
                    assert "unknown" in receipt.output["tool_error"]["messages"][0]
                else:
                    assert receipt.output["tool_error"]["code"] == "snapshot_unavailable"
                call = ConversationActionCall("fresh", "read_home_assistant_inventory", arguments())
            elif self.responses == 3:
                assert kwargs["completed_action"].output["inventory"]["items"]
                call = ConversationActionCall("save", "save_workspace_document", {})
            else:
                return ConversationModelResult("The current house note was saved.", "controlled")
            return ConversationModelResult(None, "controlled", call)

    actions = BackgroundConnectorActions((Route(),), Route.action_names, lambda turn: None, 6)
    model = Model()
    reply = LivingConversation(_Memory(), model, IdentityProfile("Eva", "Helpful", ("Be honest",)), actions).respond(turn)
    assert reply.text == "The current house note was saved."
    assert calls == ["read_home_assistant_inventory", "read_home_assistant_inventory", "save_workspace_document"]
    assert not _has_completed_tool_evidence(actions.receipts[:1])
    assert _has_completed_tool_evidence(actions.receipts)


@pytest.mark.parametrize("change", ["provider", "tool_name", "arguments", "expected", "output_key"])
def test_historical_read_recovery_does_not_accept_a_different_host_binding(change):
    turn = _turn()
    args = arguments()
    admission = {
        "action_name": "read_home_assistant_inventory", "action_call_id": "earlier-read",
        "action_arguments": args, "conversation_id": turn.conversation_id,
        "principal_id": turn.principal_id, "source_message_id": turn.message_id,
        "receipt_output": {"provider": "home_assistant"},
        "observation_output_key": "inventory", "request_tool_name": "read_home_assistant_inventory",
        "request_arguments": args, "request_expected_proof": args,
    }
    if change == "provider": admission["receipt_output"] = {"provider": "other"}
    elif change == "tool_name": admission["request_tool_name"] = "set_home_assistant_light"
    elif change == "arguments": admission["request_arguments"] = {"operation": "write"}
    elif change == "expected": admission["request_expected_proof"] = {"operation": "write"}
    else: admission["observation_output_key"] = "result"
    events = tuple(TimelineEvent(uuid4(), "old", i, f"cp-{i}", "op", kind,
        {"data": data}, canonical_sha256(data), datetime.now(timezone.utc))
        for i, (kind, data) in enumerate((("TOOL_REQUEST_ADMITTED", admission),
            ("RECOVERY_FAILED", {"error_fingerprint": "b" * 64}))))
    tool = HomeAssistantHouseholdTool(ChangingHouse())
    receipt = PostgresExternalConversationTools(None, tool, tool)._receipt(turn, events)
    assert receipt.output["status"] == "failed"
    assert not BackgroundConnectorActions.can_continue_after(turn, receipt)
    assert not _has_completed_tool_evidence([receipt])
