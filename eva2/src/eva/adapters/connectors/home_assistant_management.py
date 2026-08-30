"""Home Assistant management tools exposed through Eva's generic tool boundary."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
import hashlib
from typing import Callable, Protocol

from eva.application.advanced_tasks import (
    AdvancedTaskPlan,
    AdvancedTaskPackageApproval,
    AdvancedTaskRepositoryApproval,
    AdvancedTaskError,
    HomeAssistantInventory,
    HomeAssistantRepository,
    TuyaToLocalTuyaPlanner,
    require_current_package_preflight,
    require_current_repository_preflight,
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
from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_json_bytes


_TUYA_LOCAL_PLAN_ACTION = "plan_tuya_to_localtuya_migration"
_LOCALTUYA_REPOSITORY_REGISTER_ACTION = "register_approved_localtuya_repository"
_LOCALTUYA_PACKAGE_INSTALL_ACTION = "install_approved_localtuya_package"
_PLAN_TOOL = "home_assistant.management.retain_migration_plan"
_REPOSITORY_TOOL = "home_assistant.management.register_repository"
_PACKAGE_TOOL = "home_assistant.management.install_package"
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
_HACS_CONVERSATION_TOOLS = (
    ConversationToolDescriptor(
        name=_LOCALTUYA_REPOSITORY_REGISTER_ACTION,
        description=(
            "Registreer uitsluitend de exact genoemde onderhouden LocalTuya-bron "
            "in HACS, na een actueel duurzaam plan en een expliciet verzoek met "
            "bron en versie. Dit installeert niets en migreert geen apparaat."
        ),
        input_schema={
            "additionalProperties": False,
            "properties": {
                "expected_version": {"type": "string"},
                "repository_full_name": {"type": "string"},
            },
            "required": ["expected_version", "repository_full_name"],
            "type": "object",
        },
        completion_guidance=(
            "De toolreceipt is de onafhankelijke HACS-readback van precies de "
            "gevraagde repository en versie. Meld succes alleen bij status verified. "
            "Zeg kort dat alleen de bron is toegevoegd; het pakket is niet "
            "geïnstalleerd en er is niets gemigreerd. Noem geen interne techniek."
        ),
    ),
    ConversationToolDescriptor(
        name=_LOCALTUYA_PACKAGE_INSTALL_ACTION,
        description=(
            "Installeer één exact door het actuele migratieplan gebonden "
            "LocalTuya-pakket. Gebruik dit alleen na een expliciet verzoek met de "
            "exacte versie. Dit migreert nog geen apparaat."
        ),
        input_schema={
            "additionalProperties": False,
            "properties": {"version": {"type": "string"}},
            "required": ["version"],
            "type": "object",
        },
        completion_guidance=(
            "De toolreceipt is de onafhankelijke HACS-readback. Meld installatie "
            "alleen bij status verified en zeg expliciet dat nog geen Tuya-apparaat "
            "is gemigreerd. Noem geen interne techniek."
        ),
    ),
)


class _InventoryPort(Protocol):
    async def inventory(self) -> HomeAssistantInventory: ...


class _PlanStore(Protocol):
    def retain(self, plan: AdvancedTaskPlan) -> str: ...

    def load(self, plan_id: str) -> AdvancedTaskPlan: ...

    def latest(self, *, requested_by: str) -> AdvancedTaskPlan | None: ...

    def approve_package_install(
        self,
        *,
        plan_id: str,
        plan_sha256: str,
        inventory_sha256: str,
        repository_id: str,
        version: str,
        approved_by: str,
        approved_at: datetime,
    ) -> AdvancedTaskPackageApproval: ...

    def approve_repository_registration(
        self,
        *,
        plan_id: str,
        plan_sha256: str,
        inventory_sha256: str,
        repository_full_name: str,
        expected_version: str,
        approved_by: str,
        approved_at: datetime,
    ) -> AdvancedTaskRepositoryApproval: ...

    def package_install_started(self, plan_id: str) -> bool: ...

    def repository_registration_started(self, plan_id: str) -> bool: ...

    def prepare_package_install(
        self, approval: AdvancedTaskPackageApproval, *, occurred_at: datetime
    ) -> str: ...

    def prepare_repository_registration(
        self, approval: AdvancedTaskRepositoryApproval, *, occurred_at: datetime
    ) -> str: ...

    def record_package_installed(
        self,
        approval: AdvancedTaskPackageApproval,
        repository: HomeAssistantRepository,
        *,
        occurred_at: datetime,
    ) -> str: ...

    def record_repository_registered(
        self,
        approval: AdvancedTaskRepositoryApproval,
        repository: HomeAssistantRepository,
        *,
        occurred_at: datetime,
    ) -> str: ...

    def refresh(self, plan: AdvancedTaskPlan) -> str: ...


class _HacsPort(Protocol):
    async def repository(self, repository_id: str) -> HomeAssistantRepository: ...

    async def repository_by_full_name(
        self, full_name: str
    ) -> HomeAssistantRepository | None: ...

    async def register_exact(
        self,
        approval: AdvancedTaskRepositoryApproval,
        *,
        now: datetime,
        poll_interval_seconds: float = 0.5,
        timeout_seconds: float = 20.0,
    ) -> HomeAssistantRepository: ...

    async def install_exact(
        self,
        approval: AdvancedTaskPackageApproval,
        *,
        now: datetime,
        poll_interval_seconds: float = 0.5,
        timeout_seconds: float = 15.0,
    ) -> HomeAssistantRepository: ...


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


class HomeAssistantConversationHacsPlanner:
    """Bind explicit HACS changes to the current provider plan and inventory."""

    action_names = (
        _LOCALTUYA_REPOSITORY_REGISTER_ACTION,
        _LOCALTUYA_PACKAGE_INSTALL_ACTION,
    )

    def __init__(
        self, inventory: _InventoryPort, store_factory: PlanStoreFactory
    ) -> None:
        self._inventory = inventory
        self._store_factory = store_factory

    @staticmethod
    def conversation_tools() -> tuple[ConversationToolDescriptor, ...]:
        return _HACS_CONVERSATION_TOOLS

    def active_context(self, turn: ConversationTurn) -> tuple[str, ...]:
        plan = self._store_factory(turn.tenant_id, turn.installation_id).latest(
            requested_by=turn.principal_id
        )
        if plan is None:
            return ()
        repositories = ", ".join(
            f"{item.full_name} {item.available_version or 'onbekend'} "
            f"({'installed' if item.installed else 'niet geïnstalleerd'})"
            for item in plan.repositories
        )
        return (
            f"Actueel duurzaam Tuya→LocalTuya-plan {plan.plan_id}, SHA-256 "
            f"{plan.sha256}. Pakket: {repositories or 'geen gebonden pakket'}. "
            f"Blokkades: {', '.join(plan.blockers) or 'geen'}. Een "
            "pakketinstallatie migreert nog geen apparaat.",
        )

    async def prepare(
        self, turn: ConversationTurn, call: ConversationActionCall
    ) -> ConversationToolPreparation:
        store = self._store_factory(turn.tenant_id, turn.installation_id)
        plan = await asyncio.to_thread(store.latest, requested_by=turn.principal_id)
        if plan is None:
            raise ConversationConflict("Maak eerst een actueel Tuya-migratieplan")
        inventory = await self._inventory.inventory()
        now = datetime.now(tz=UTC).replace(microsecond=0)
        if call.name == _LOCALTUYA_REPOSITORY_REGISTER_ACTION:
            arguments = _repository_arguments(turn, call.arguments)
            try:
                require_current_repository_preflight(plan, inventory)
                approval = await asyncio.to_thread(
                    store.approve_repository_registration,
                    plan_id=plan.plan_id,
                    plan_sha256=plan.sha256,
                    inventory_sha256=plan.inventory_sha256,
                    repository_full_name=arguments["repository_full_name"],
                    expected_version=arguments["expected_version"],
                    approved_by=turn.principal_id,
                    approved_at=now,
                )
            except AdvancedTaskError as error:
                raise ConversationConflict(str(error)) from error
            return ConversationToolPreparation(
                tool_name=_REPOSITORY_TOOL,
                arguments={
                    "approval": approval.to_payload(),
                    "installation_id": turn.installation_id,
                    "tenant_id": turn.tenant_id,
                },
                expected_proof={
                    "available_version": approval.expected_version,
                    "custom": True,
                    "repository_full_name": approval.repository_full_name,
                    "registered": True,
                },
                receipt_output={
                    "plan_id": plan.plan_id,
                    "repository_full_name": approval.repository_full_name,
                    "version": approval.expected_version,
                },
                execution_timeout_seconds=30,
                verification_timeout_seconds=30,
            )
        if call.name == _LOCALTUYA_PACKAGE_INSTALL_ACTION:
            arguments = _package_arguments(turn, call.arguments)
            repository = next(
                (
                    item
                    for item in plan.repositories
                    if item.domain == "localtuya"
                    and item.available_version == arguments["version"]
                ),
                None,
            )
            if repository is None:
                raise ConversationConflict(
                    "De gevraagde LocalTuya-versie staat niet in het actuele plan"
                )
            try:
                require_current_package_preflight(plan, inventory)
                approval = await asyncio.to_thread(
                    store.approve_package_install,
                    plan_id=plan.plan_id,
                    plan_sha256=plan.sha256,
                    inventory_sha256=plan.inventory_sha256,
                    repository_id=repository.repository_id,
                    version=arguments["version"],
                    approved_by=turn.principal_id,
                    approved_at=now,
                )
            except AdvancedTaskError as error:
                raise ConversationConflict(str(error)) from error
            return ConversationToolPreparation(
                tool_name=_PACKAGE_TOOL,
                arguments={
                    "approval": approval.to_payload(),
                    "installation_id": turn.installation_id,
                    "tenant_id": turn.tenant_id,
                },
                expected_proof={
                    "installed": True,
                    "installed_version": approval.version,
                    "repository_id": approval.repository_id,
                },
                receipt_output={
                    "installed_version": approval.version,
                    "plan_id": plan.plan_id,
                    "repository_id": approval.repository_id,
                },
                execution_timeout_seconds=30,
                verification_timeout_seconds=30,
            )
        raise ConversationConflict(
            f"unsupported Home Assistant HACS action {call.name!r}"
        )


class HomeAssistantHacsManagementAdapter:
    """Execute and independently read back exact HACS management commands."""

    def __init__(
        self,
        hacs: _HacsPort,
        inventory: _InventoryPort,
        store_factory: PlanStoreFactory,
    ) -> None:
        self._hacs = hacs
        self._inventory = inventory
        self._store_factory = store_factory

    async def execute(self, request: ToolRequest) -> ToolDispatchResult:
        tenant_id, installation_id, approval = _hacs_command_fields(request)
        store = self._store_factory(tenant_id, installation_id)
        now = datetime.now(tz=UTC).replace(microsecond=0)
        if now >= approval.expires_at:
            raise AdvancedTaskError("LocalTuya approval expired")
        plan = await asyncio.to_thread(store.load, approval.plan_id)
        inventory = await self._inventory.inventory()
        if isinstance(approval, AdvancedTaskRepositoryApproval):
            if not await asyncio.to_thread(
                store.repository_registration_started, approval.plan_id
            ):
                require_current_repository_preflight(plan, inventory)
            await asyncio.to_thread(
                store.prepare_repository_registration, approval, occurred_at=now
            )
            repository = await self._hacs.register_exact(approval, now=now)
            disposition = await asyncio.to_thread(
                store.record_repository_registered,
                approval,
                repository,
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
            )
            await self._refresh_plan(store, plan)
        else:
            if not await asyncio.to_thread(
                store.package_install_started, approval.plan_id
            ):
                require_current_package_preflight(plan, inventory)
            await asyncio.to_thread(
                store.prepare_package_install, approval, occurred_at=now
            )
            repository = await self._hacs.install_exact(approval, now=now)
            disposition = await asyncio.to_thread(
                store.record_package_installed,
                approval,
                repository,
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
            )
        return ToolDispatchResult(
            True,
            repository.repository_id,
            {"disposition": disposition, "repository_sha256": repository.sha256},
        )

    async def observe(
        self, request: ToolRequest, dispatch: ToolDispatchResult | None
    ) -> ToolProofObservation:
        tenant_id, installation_id, approval = _hacs_command_fields(request)
        store = self._store_factory(tenant_id, installation_id)
        if isinstance(approval, AdvancedTaskRepositoryApproval):
            repository = await self._hacs.repository_by_full_name(
                approval.repository_full_name
            )
            if repository is None:
                return ToolProofObservation(
                    "pending",
                    {
                        "available_version": approval.expected_version,
                        "custom": True,
                        "repository_full_name": approval.repository_full_name,
                        "registered": False,
                    },
                    {"source": "home_assistant_hacs_repository"},
                )
            try:
                disposition = await asyncio.to_thread(
                    store.record_repository_registered,
                    approval,
                    repository,
                    occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
                )
                plan = await asyncio.to_thread(store.load, approval.plan_id)
                await self._refresh_plan(store, plan)
            except AdvancedTaskError:
                return ToolProofObservation(
                    "conflict",
                    _repository_observed(repository),
                    {"source": "home_assistant_hacs_repository"},
                )
            observed = _repository_observed(repository)
        else:
            repository = await self._hacs.repository(approval.repository_id)
            try:
                disposition = await asyncio.to_thread(
                    store.record_package_installed,
                    approval,
                    repository,
                    occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
                )
            except AdvancedTaskError:
                return ToolProofObservation(
                    "conflict",
                    _package_observed(repository),
                    {"source": "home_assistant_hacs_repository"},
                )
            observed = _package_observed(repository)
        return ToolProofObservation(
            "verified" if observed == request.expected_proof else "conflict",
            observed,
            {
                "disposition": disposition,
                "repository_sha256": repository.sha256,
                "source": "home_assistant_hacs_repository",
            },
        )

    async def _refresh_plan(
        self, store: _PlanStore, plan: AdvancedTaskPlan
    ) -> None:
        inventory = await self._inventory.inventory()
        refreshed = TuyaToLocalTuyaPlanner().build(
            inventory,
            operation_id=plan.operation_id,
            requested_by=plan.requested_by,
        )
        await asyncio.to_thread(store.refresh, refreshed)


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


def _hacs_command_fields(
    request: ToolRequest,
) -> tuple[str, str, AdvancedTaskRepositoryApproval | AdvancedTaskPackageApproval]:
    if request.tool_name not in {_REPOSITORY_TOOL, _PACKAGE_TOOL} or set(
        request.arguments
    ) != {"approval", "installation_id", "tenant_id"}:
        raise ValueError("Home Assistant HACS command differs")
    tenant_id = request.arguments["tenant_id"]
    installation_id = request.arguments["installation_id"]
    if (
        not isinstance(tenant_id, str)
        or not tenant_id.strip()
        or not isinstance(installation_id, str)
        or not installation_id.strip()
    ):
        raise ValueError("Home Assistant HACS command scope is invalid")
    approval = (
        AdvancedTaskRepositoryApproval.from_payload(request.arguments["approval"])
        if request.tool_name == _REPOSITORY_TOOL
        else AdvancedTaskPackageApproval.from_payload(request.arguments["approval"])
    )
    return tenant_id, installation_id, approval


def _repository_arguments(
    turn: ConversationTurn, raw: dict[str, JsonValue]
) -> dict[str, str]:
    if set(raw) != {"expected_version", "repository_full_name"}:
        raise ConversationConflict("LocalTuya repository request has wrong fields")
    repository = raw.get("repository_full_name")
    version = raw.get("expected_version")
    if (
        not isinstance(repository, str)
        or not repository.strip()
        or len(repository) > 240
        or not isinstance(version, str)
        or not version.strip()
        or len(version) > 80
    ):
        raise ConversationConflict("LocalTuya repository request is invalid")
    arguments = {
        "expected_version": version.strip(),
        "repository_full_name": repository.strip(),
    }
    text = turn.text.casefold()
    if (
        arguments["repository_full_name"].casefold() not in text
        or arguments["expected_version"].casefold() not in text
        or "hacs" not in text
        or not any(word in text for word in ("repository", "repo"))
        or not any(word in text for word in ("voeg", "toevoegen", "registreer"))
    ):
        raise ConversationConflict(
            "LocalTuya repository registration requires an explicit source and version"
        )
    return arguments


def _package_arguments(
    turn: ConversationTurn, raw: dict[str, JsonValue]
) -> dict[str, str]:
    if set(raw) != {"version"}:
        raise ConversationConflict("LocalTuya package request has wrong fields")
    version = raw.get("version")
    if not isinstance(version, str) or not version.strip() or len(version) > 80:
        raise ConversationConflict("LocalTuya package version is invalid")
    version = version.strip()
    text = turn.text.casefold()
    if (
        "localtuya" not in text.replace(" ", "")
        or not any(
            word in text for word in ("installeer", "installeren", "installatie goed")
        )
        or version.casefold() not in text
    ):
        raise ConversationConflict(
            "LocalTuya package installation requires an explicit versioned request"
        )
    return {"version": version}


def _repository_observed(repository: HomeAssistantRepository) -> dict[str, JsonValue]:
    return {
        "available_version": repository.available_version,
        "custom": repository.custom,
        "repository_full_name": repository.full_name,
        "registered": True,
    }


def _package_observed(repository: HomeAssistantRepository) -> dict[str, JsonValue]:
    return {
        "installed": repository.installed,
        "installed_version": repository.installed_version,
        "repository_id": repository.repository_id,
    }


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
    "HomeAssistantConversationHacsPlanner",
    "HomeAssistantConversationMigrationPlanner",
    "HomeAssistantHacsManagementAdapter",
    "HomeAssistantMigrationPlanAdapter",
]
