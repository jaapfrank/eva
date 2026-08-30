from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from eva.adapters.connectors.home_assistant_management import (
    HomeAssistantConversationMigrationPlanner,
    HomeAssistantMigrationPlanAdapter,
)
from eva.application.advanced_tasks import (
    AdvancedTaskPlan,
    HomeAssistantDevice,
    HomeAssistantEntity,
    HomeAssistantIntegration,
    HomeAssistantInventory,
)
from eva.application.conversation import (
    ConversationActionCall,
    ConversationConflict,
    ConversationTurn,
)
from eva.application.orchestration import ToolRequest


def _inventory() -> HomeAssistantInventory:
    return HomeAssistantInventory(
        (HomeAssistantIntegration("tuya", "cloud", "loaded", "Tuya"),),
        (HomeAssistantDevice("device", "Schuur", "Tuya", "Lamp", ("cloud",)),),
        (HomeAssistantEntity("light.schuur", "tuya", "cloud", "device", None),),
        datetime(2026, 8, 30, 18, 30, tzinfo=UTC),
        (),
        True,
        "2026.8.1",
    )


def _turn() -> ConversationTurn:
    return ConversationTurn.private(
        "tenant",
        "installation",
        "jaap",
        "conversation",
        "message",
        "Zet Tuya veilig om naar lokaal.",
        "2026-08-30T18:30:00Z",
    )


def test_provider_owns_tuya_schema_inventory_and_plan() -> None:
    calls = 0

    class Inventory:
        async def inventory(self) -> HomeAssistantInventory:
            nonlocal calls
            calls += 1
            return _inventory()

    planner = HomeAssistantConversationMigrationPlanner(Inventory())
    preparation = asyncio.run(
        planner.prepare(
            _turn(),
            ConversationActionCall(
                "call",
                "plan_tuya_to_localtuya_migration",
                {"scope": "all_tuya_devices"},
            ),
        )
    )

    assert calls == 1
    assert {item.name for item in planner.conversation_tools()} == set(
        planner.action_names
    )
    plan = AdvancedTaskPlan.from_payload(preparation.arguments["plan"])
    assert plan.requested_by == "jaap"
    assert plan.operation_id == "tuya-local-plan:dceb813ded10ddeeb733e0ae55523215"
    assert [device.route for device in plan.devices] == ["localtuya_candidate"]
    assert preparation.expected_proof == {
        "plan_id": plan.plan_id,
        "plan_sha256": plan.sha256,
    }
    assert preparation.receipt_output == {"plan": plan.to_payload()}


def test_provider_rejects_a_broader_management_scope() -> None:
    class Inventory:
        async def inventory(self) -> HomeAssistantInventory:
            raise AssertionError("invalid input must not read Home Assistant")

    with pytest.raises(ConversationConflict, match="scope"):
        asyncio.run(
            HomeAssistantConversationMigrationPlanner(Inventory()).prepare(
                _turn(),
                ConversationActionCall(
                    "call",
                    "plan_tuya_to_localtuya_migration",
                    {"scope": "everything"},
                ),
            )
        )


def test_plan_adapter_retains_and_reads_back_exact_provider_plan() -> None:
    plan = asyncio.run(
        HomeAssistantConversationMigrationPlanner(_StaticInventory()).prepare(
            _turn(),
            ConversationActionCall(
                "call",
                "plan_tuya_to_localtuya_migration",
                {"scope": "all_tuya_devices"},
            ),
        )
    )
    stores: dict[tuple[str, str], _MemoryStore] = {}
    adapter = HomeAssistantMigrationPlanAdapter(
        lambda tenant, installation: stores.setdefault(
            (tenant, installation), _MemoryStore()
        )
    )
    request = ToolRequest(
        thread_id="tool:plan",
        operation_id="operation:plan",
        checkpoint_prefix="checkpoint:plan",
        tool_name=plan.tool_name,
        arguments=plan.arguments,
        expected_proof=plan.expected_proof,
    )

    dispatch = asyncio.run(adapter.execute(request))
    observation = asyncio.run(adapter.observe(request, dispatch))

    assert dispatch.accepted is True
    assert dispatch.details["disposition"] == "planned"
    assert observation.disposition == "verified"
    assert observation.observed == request.expected_proof


class _StaticInventory:
    async def inventory(self) -> HomeAssistantInventory:
        return _inventory()


class _MemoryStore:
    def __init__(self) -> None:
        self.plan: AdvancedTaskPlan | None = None

    def retain(self, plan: AdvancedTaskPlan) -> str:
        if self.plan is not None:
            assert self.plan == plan
            return "already_planned"
        self.plan = plan
        return "planned"

    def load(self, plan_id: str) -> AdvancedTaskPlan:
        assert self.plan is not None and self.plan.plan_id == plan_id
        return self.plan
