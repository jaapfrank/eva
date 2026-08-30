"""Home Assistant management tools exposed through Eva's generic tool boundary."""

from __future__ import annotations

import asyncio
import hashlib
from typing import Callable, Protocol

from eva.application.advanced_tasks import (
    AdvancedTaskPlan,
    HomeAssistantInventory,
    TuyaToLocalTuyaPlanner,
)
from eva.application.conversation import (
    ConversationActionCall,
    ConversationConflict,
    ConversationToolDescriptor,
    ConversationToolPreparation,
    ConversationTurn,
)
from eva.application.orchestration import (
    ToolDispatchResult,
    ToolProofObservation,
    ToolRequest,
)
from eva.contracts.canonical_json import canonical_json_bytes


_TUYA_LOCAL_PLAN_ACTION = "plan_tuya_to_localtuya_migration"
_PLAN_TOOL = "home_assistant.management.retain_migration_plan"
_COMPLETION_GUIDANCE = (
    "De toolreceipt bevat de werkelijk gelezen Home Assistant-inventaris en het "
    "duurzaam opgeslagen migratieplan. Gebruik de aantallen, routes en blokkades uit "
    "plan als feiten en vertaal die naar een kort, natuurlijk antwoord dat bij de "
    "vraag past. Zeg ondubbelzinnig dat nog niets is gemigreerd. Noem geen receipt, "
    "action, blocker-ID, hash of andere interne techniek. Beweer geen vervolgstap: "
    "alleen het plan is werkelijk opgeslagen."
)
_CONVERSATION_TOOLS = (
    ConversationToolDescriptor(
        name=_TUYA_LOCAL_PLAN_ACTION,
        description=(
            "Inventariseer alle echte Tuya/LocalTuya-apparaten via Home Assistant "
            "en bewaar een veilig migratieplan. Dit migreert nog niets."
        ),
        input_schema={
            "additionalProperties": False,
            "properties": {
                "scope": {"const": "all_tuya_devices", "type": "string"}
            },
            "required": ["scope"],
            "type": "object",
        },
        completion_guidance=_COMPLETION_GUIDANCE,
    ),
)


class _InventoryPort(Protocol):
    async def inventory(self) -> HomeAssistantInventory: ...


class _PlanStore(Protocol):
    def retain(self, plan: AdvancedTaskPlan) -> str: ...

    def load(self, plan_id: str) -> AdvancedTaskPlan: ...


PlanStoreFactory = Callable[[str, str], _PlanStore]


class HomeAssistantConversationMigrationPlanner:
    """Turn a user request into a provider-owned, read-only migration plan."""

    action_names = (_TUYA_LOCAL_PLAN_ACTION,)

    def __init__(self, inventory: _InventoryPort) -> None:
        self._inventory = inventory

    @staticmethod
    def conversation_tools() -> tuple[ConversationToolDescriptor, ...]:
        return _CONVERSATION_TOOLS

    async def prepare(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationToolPreparation:
        if call.name != _TUYA_LOCAL_PLAN_ACTION:
            raise ConversationConflict(
                f"unsupported Home Assistant management action {call.name!r}"
            )
        if call.arguments != {"scope": "all_tuya_devices"}:
            raise ConversationConflict("Home Assistant migration scope is invalid")
        inventory = await self._inventory.inventory()
        plan = TuyaToLocalTuyaPlanner().build(
            inventory,
            operation_id=_plan_operation_id(turn),
            requested_by=turn.principal_id,
        )
        payload = plan.to_payload()
        return ConversationToolPreparation(
            tool_name=_PLAN_TOOL,
            arguments={
                "installation_id": turn.installation_id,
                "plan": payload,
                "tenant_id": turn.tenant_id,
            },
            expected_proof={
                "plan_id": plan.plan_id,
                "plan_sha256": plan.sha256,
            },
            receipt_output={"plan": payload},
        )


class HomeAssistantMigrationPlanAdapter:
    """Persist and read back a provider plan through an injected durable store."""

    def __init__(self, store_factory: PlanStoreFactory) -> None:
        self._store_factory = store_factory

    async def execute(self, request: ToolRequest) -> ToolDispatchResult:
        tenant_id, installation_id, plan = _command_fields(request)
        disposition = await asyncio.to_thread(
            self._store_factory(tenant_id, installation_id).retain, plan
        )
        return ToolDispatchResult(
            True,
            plan.plan_id,
            {"disposition": disposition, "plan_id": plan.plan_id},
        )

    async def observe(
        self, request: ToolRequest, dispatch: ToolDispatchResult | None
    ) -> ToolProofObservation:
        tenant_id, installation_id, plan = _command_fields(request)
        stored = await asyncio.to_thread(
            self._store_factory(tenant_id, installation_id).load, plan.plan_id
        )
        observed = {"plan_id": stored.plan_id, "plan_sha256": stored.sha256}
        return ToolProofObservation(
            "verified" if observed == request.expected_proof else "conflict",
            observed,
            {"source": "home_assistant_management_plan_store"},
        )


def _command_fields(request: ToolRequest) -> tuple[str, str, AdvancedTaskPlan]:
    if request.tool_name != _PLAN_TOOL or set(request.arguments) != {
        "installation_id",
        "plan",
        "tenant_id",
    }:
        raise ValueError("Home Assistant migration plan command differs")
    tenant_id = request.arguments["tenant_id"]
    installation_id = request.arguments["installation_id"]
    raw_plan = request.arguments["plan"]
    if (
        not isinstance(tenant_id, str)
        or not tenant_id.strip()
        or not isinstance(installation_id, str)
        or not installation_id.strip()
    ):
        raise ValueError("Home Assistant migration plan scope is invalid")
    plan = AdvancedTaskPlan.from_payload(raw_plan)
    if request.expected_proof != {
        "plan_id": plan.plan_id,
        "plan_sha256": plan.sha256,
    }:
        raise ValueError("Home Assistant migration plan proof differs")
    return tenant_id, installation_id, plan


def _plan_operation_id(turn: ConversationTurn) -> str:
    identity = hashlib.sha256(
        canonical_json_bytes(
            {
                "installation_id": turn.installation_id,
                "kind": "tuya_to_localtuya_plan",
                "message_id": turn.message_id,
                "tenant_id": turn.tenant_id,
            }
        )
    ).hexdigest()[:32]
    return f"tuya-local-plan:{identity}"


__all__ = [
    "HomeAssistantConversationMigrationPlanner",
    "HomeAssistantMigrationPlanAdapter",
]
