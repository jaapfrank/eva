"""Small HTTP surface for health and memory-backed Presence recall."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
from typing import Any, Protocol
from uuid import UUID

from fastapi import BackgroundTasks, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse

from eva.adapters.api.cockpit import COCKPIT_HTML, cockpit_progress
from eva.adapters.api.dashboard_conversation import (
    DASHBOARD_CONVERSATION_HTML,
    conversation_stream_response,
    grounded_reply_wire,
)
from eva.adapters.api.dashboard_access import DashboardAccess

from eva.application.conversation import (
    ConversationConflict,
    ConversationObservationBinding,
    ConversationTurn,
    GroundedReply,
    RecentConversationMessage,
    load_current_identity_pack,
)
from eva.application.identity_drafts import identity_presentation_diff
from eva.application.soul_markdown import (
    SoulMarkdownError,
    soul_markdown_sha256,
    soul_markdown_to_draft,
)
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.identity import IdentityPackDraft
from eva.application.memory import (
    ConversationMemoryDocument,
    MemoryRetentionReceipt,
    PresenceMemory,
    RecallCurrentFacts,
)
from eva.application.memory_audience import (
    memory_audience_from_wire,
    memory_audience_to_wire,
)
from eva.application.telegram_canary import TelegramCanaryAdmission
from eva.application.people_memory_overview import PeopleMemoryOverview
from eva.contracts.workboard import WorkBoard
from eva.application.personal_interests import (
    PersonalProductReleaseArtifact,
    PersonalPrototypeArtifact,
)
from eva.application.research import ResearchArtifact
from eva.application.commerce import (
    CommerceError,
    DigitalFulfillmentDownload,
    ProductPublicationApproval,
)
from eva.application.free_will import ProactiveExpressionClaim
from eva.application.durable_goals import (
    DurableGoalError,
    DurableGoalSpec,
    DurableGoalState,
    GoalWakeCondition,
)


_RECALL_KEYS = {
    "allowed_privacy_classes",
    "audience",
    "conversation_id",
    "installation_id",
    "maximum_facts",
    "principal_id",
    "project_id",
    "query_purpose",
    "query_text",
    "recall_id",
    "requested_valid_at",
    "scope",
    "subject_ids",
    "tenant_id",
}

_RETAIN_KEYS = {
    "audience",
    "content",
    "conversation_id",
    "document_id",
    "evidence_ref",
    "installation_id",
    "occurred_at",
    "principal_id",
    "privacy_class",
    "source_ref",
    "tenant_id",
    "trust",
}

_TELEGRAM_CANARY_KEYS = {
    "chat_id",
    "installation_id",
    "message_id",
    "occurred_at",
    "recent_messages",
    "telegram_user_id",
    "tenant_id",
    "text",
}
_RETAIN_OPTIONAL_KEYS = {"project_id"}

_TURN_REQUIRED_KEYS = {
    "audience",
    "conversation_id",
    "installation_id",
    "message_id",
    "occurred_at",
    "principal_id",
    "tenant_id",
    "text",
}
_TURN_OPTIONAL_KEYS = {"observation_binding", "project_id", "recent_messages"}
_RECENT_MESSAGE_KEYS = {"occurred_at", "role", "text"}
_OBSERVATION_BINDING_KEYS = {
    "media_sha256",
    "media_type",
    "observation_id",
    "source_ref",
    "subject_region_sha256",
}
_DURABLE_GOAL_KEYS = {
    "created_at",
    "delivery_recipient",
    "description",
    "goal_id",
    "owner_id",
    "pivot_instruction",
    "primary_instruction",
    "suitability_field",
    "unsuitable_value",
    "wake",
}
_DURABLE_GOAL_WAKE_KEYS = {
    "deadline",
    "expected_value",
    "field",
    "provider",
    "resource",
}
_DASHBOARD_EFFECT_STOP_KEYS = {"effect_intent_id"}
_DASHBOARD_CONVERSATION_ERASE_KEYS = {"message_id"}
_TUYA_LOCAL_PLAN_KEYS = {
    "installation_id",
    "mode",
    "operation_id",
    "principal_id",
    "request",
    "tenant_id",
}
_TUYA_LOCAL_REFRESH_KEYS = {
    "installation_id",
    "mode",
    "plan_id",
    "tenant_id",
}
_TUYA_LOCAL_PREFLIGHT_KEYS = {
    "config_entry_id",
    "device_id",
    "friendly_name",
    "host",
    "installation_id",
    "inventory_sha256",
    "local_device_id",
    "local_key",
    "mode",
    "plan_id",
    "plan_sha256",
    "protocol_version",
    "tenant_id",
}
_TUYA_LOCAL_CANARY_PREPARE_KEYS = {
    "approval_sha256",
    "configuration_sha256",
    "installation_id",
    "mode",
    "plan_id",
    "preflight_sha256",
    "tenant_id",
}
_TUYA_LOCAL_CANARY_EXECUTE_KEYS = {
    "entities",
    "friendly_name",
    "host",
    "installation_id",
    "local_device_id",
    "local_key",
    "mode",
    "plan_id",
    "protocol_version",
    "tenant_id",
}
_TUYA_LOCAL_CANARY_RECONCILE_KEYS = {
    "installation_id",
    "mode",
    "plan_id",
    "tenant_id",
}
_ADVANCED_CANARY_APPROVAL_KEYS = {
    "approved_by",
    "device_id",
    "installation_id",
    "inventory_sha256",
    "mode",
    "plan_id",
    "plan_sha256",
    "tenant_id",
}
_ADVANCED_PACKAGE_APPROVAL_KEYS = {
    "approved_by",
    "installation_id",
    "inventory_sha256",
    "mode",
    "plan_id",
    "plan_sha256",
    "repository_id",
    "tenant_id",
    "version",
}
_ADVANCED_PACKAGE_INSTALL_KEYS = {
    "approval_sha256",
    "installation_id",
    "mode",
    "plan_id",
    "tenant_id",
}
_ADVANCED_REPOSITORY_APPROVAL_KEYS = {
    "approved_by",
    "expected_version",
    "installation_id",
    "inventory_sha256",
    "mode",
    "plan_id",
    "plan_sha256",
    "repository_full_name",
    "tenant_id",
}
_ADVANCED_REPOSITORY_REGISTER_KEYS = {
    "approval_sha256",
    "installation_id",
    "mode",
    "plan_id",
    "tenant_id",
}
_PROACTIVE_ACK_KEYS = {
    "channel",
    "provider_message_id",
    "revision",
    "thread_id",
}
_COMMERCE_PLAN_KEYS = {
    "installation_id",
    "mode",
    "principal_id",
    "tenant_id",
}
_COMMERCE_APPROVAL_KEYS = {
    "approved_by",
    "installation_id",
    "mode",
    "plan_sha256",
    "tenant_id",
}
_COMMERCE_PUBLISH_KEYS = {
    "approval_sha256",
    "installation_id",
    "mode",
    "plan_sha256",
    "tenant_id",
}
_COMMERCE_RECONCILE_KEYS = {
    "approval_sha256",
    "installation_id",
    "mode",
    "plan_sha256",
    "provider_resource_id",
    "tenant_id",
}


@dataclass(frozen=True, slots=True)
class RuntimeHealth:
    ready: bool
    database: str
    memory_projection: str
    detail: str | None = None
    model_provider: str = "unknown"


class MemoryRecallRuntime(Protocol):
    def health(self) -> RuntimeHealth: ...

    def recall(self, query: RecallCurrentFacts) -> PresenceMemory: ...

    def retain(
        self, document: ConversationMemoryDocument
    ) -> MemoryRetentionReceipt: ...

    def admit_turn_audience(self, turn: ConversationTurn) -> None: ...

    def respond(
        self,
        turn: ConversationTurn,
        recent_messages: tuple[RecentConversationMessage, ...] = (),
    ) -> GroundedReply: ...

    def after_response(self, turn: ConversationTurn, reply: GroundedReply) -> None: ...

    def admit_telegram_canary(
        self,
        *,
        telegram_user_id: int,
        chat_id: str,
        text: str,
        operation_id: str,
        now: datetime,
    ) -> TelegramCanaryAdmission: ...

    def latest_personal_prototype(
        self, tenant_id: str, installation_id: str, principal_id: str
    ) -> PersonalPrototypeArtifact | None: ...

    def latest_personal_product_release(
        self, tenant_id: str, installation_id: str, principal_id: str
    ) -> PersonalProductReleaseArtifact | None: ...

    def latest_current_research(
        self, tenant_id: str, installation_id: str, principal_id: str
    ) -> ResearchArtifact | None: ...

    def admit_durable_goal(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        goal: DurableGoalSpec,
    ) -> DurableGoalState: ...

    def cancel_durable_goal(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        goal_id: str,
        owner_id: str,
        reason: str,
        occurred_at: datetime,
    ) -> DurableGoalState: ...

    def durable_goal_state(
        self,
        *,
        tenant_id: str,
        installation_id: str,
        goal_id: str,
        owner_id: str,
    ) -> DurableGoalState | None: ...


def _query_from_wire(payload: dict[str, Any]) -> RecallCurrentFacts:
    if set(payload) != _RECALL_KEYS:
        raise ValueError("memory recall request has unknown or missing fields")
    subject_ids = payload["subject_ids"]
    privacy = payload["allowed_privacy_classes"]
    if not isinstance(subject_ids, list) or not all(
        isinstance(value, str) for value in subject_ids
    ):
        raise ValueError("memory recall subject IDs are invalid")
    if not isinstance(privacy, list) or not all(
        isinstance(value, str) for value in privacy
    ):
        raise ValueError("memory recall privacy classes are invalid")
    try:
        return RecallCurrentFacts(
            recall_id=payload["recall_id"],
            installation_id=payload["installation_id"],
            tenant_id=payload["tenant_id"],
            principal_id=payload["principal_id"],
            audience=memory_audience_from_wire(payload["audience"]),
            conversation_id=payload["conversation_id"],
            project_id=payload["project_id"],
            query_purpose=payload["query_purpose"],
            requested_valid_at=payload["requested_valid_at"],
            scope=payload["scope"],
            subject_ids=tuple(subject_ids),
            query_text=payload["query_text"],
            maximum_facts=payload["maximum_facts"],
            allowed_privacy_classes=tuple(privacy),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("memory recall request is invalid") from error


def _document_from_wire(payload: dict[str, Any]) -> ConversationMemoryDocument:
    if (
        not _RETAIN_KEYS <= set(payload)
        or set(payload) - _RETAIN_KEYS - _RETAIN_OPTIONAL_KEYS
    ):
        raise ValueError("memory retain request has unknown or missing fields")
    try:
        return ConversationMemoryDocument(
            tenant_id=payload["tenant_id"],
            installation_id=payload["installation_id"],
            principal_id=payload["principal_id"],
            conversation_id=payload["conversation_id"],
            document_id=payload["document_id"],
            content=payload["content"],
            occurred_at=payload["occurred_at"],
            source_ref=payload["source_ref"],
            evidence_ref=payload["evidence_ref"],
            privacy_class=payload["privacy_class"],
            trust=payload["trust"],
            audience=memory_audience_from_wire(payload["audience"]),
            project_id=payload.get("project_id"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("memory retain request is invalid") from error


def _turn_from_wire(
    payload: dict[str, Any],
) -> tuple[ConversationTurn, tuple[RecentConversationMessage, ...]]:
    keys = set(payload)
    if (
        not _TURN_REQUIRED_KEYS <= keys
        or keys - _TURN_REQUIRED_KEYS - _TURN_OPTIONAL_KEYS
    ):
        raise ValueError("conversation turn has unknown or missing fields")
    try:
        raw_binding = payload.get("observation_binding")
        if raw_binding is not None and (
            not isinstance(raw_binding, dict)
            or set(raw_binding) != _OBSERVATION_BINDING_KEYS
        ):
            raise ValueError("conversation observation binding is invalid")
        observation_binding = (
            None
            if raw_binding is None
            else ConversationObservationBinding(
                observation_id=raw_binding["observation_id"],
                media_sha256=raw_binding["media_sha256"],
                subject_region_sha256=raw_binding["subject_region_sha256"],
                media_type=raw_binding["media_type"],
                source_ref=raw_binding["source_ref"],
            )
        )
        audience = memory_audience_from_wire(payload["audience"])
        turn = ConversationTurn(
            tenant_id=payload["tenant_id"],
            installation_id=payload["installation_id"],
            principal_id=payload["principal_id"],
            conversation_id=payload["conversation_id"],
            message_id=payload["message_id"],
            text=payload["text"],
            occurred_at=payload["occurred_at"],
            audience=audience,
            project_id=payload.get("project_id"),
            observation_binding=observation_binding,
        )
        raw_recent = payload.get("recent_messages", [])
        if not isinstance(raw_recent, list) or len(raw_recent) > 12:
            raise ValueError("recent conversation context is invalid")
        recent: list[RecentConversationMessage] = []
        for raw in raw_recent:
            if not isinstance(raw, dict) or set(raw) != _RECENT_MESSAGE_KEYS:
                raise ValueError("recent conversation message is invalid")
            recent.append(
                RecentConversationMessage(
                    role=raw["role"],
                    text=raw["text"],
                    occurred_at=raw["occurred_at"],
                )
            )
        return turn, tuple(recent)
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("conversation turn is invalid") from error


def _memory_payload(memory: PresenceMemory) -> dict[str, Any]:
    return {
        "confirmed_facts": [
            fact.model_dump(mode="json") for fact in memory.confirmed_facts
        ],
        "contract": "EvaInternalMemoryRecallResult/v1",
        "recall": memory.recall.model_dump(mode="json"),
    }


def _durable_goal_from_wire(raw: object) -> DurableGoalSpec:
    if not isinstance(raw, dict) or set(raw) != _DURABLE_GOAL_KEYS:
        raise ValueError("durable goal request is invalid")
    wake = raw.get("wake")
    if not isinstance(wake, dict) or set(wake) != _DURABLE_GOAL_WAKE_KEYS:
        raise ValueError("durable goal wake request is invalid")
    try:
        return DurableGoalSpec(
            goal_id=raw["goal_id"],
            owner_id=raw["owner_id"],
            description=raw["description"],
            primary_instruction=raw["primary_instruction"],
            pivot_instruction=raw["pivot_instruction"],
            suitability_field=raw["suitability_field"],
            unsuitable_value=raw["unsuitable_value"],
            delivery_recipient=raw["delivery_recipient"],
            wake=GoalWakeCondition(
                provider=wake["provider"],
                resource=wake["resource"],
                field=wake["field"],
                expected_value=wake["expected_value"],
                deadline=datetime.fromisoformat(
                    wake["deadline"].replace("Z", "+00:00")
                ),
            ),
            created_at=datetime.fromisoformat(raw["created_at"].replace("Z", "+00:00")),
        )
    except (KeyError, AttributeError, TypeError, ValueError) as error:
        raise ValueError("durable goal request is invalid") from error


def _durable_goal_payload(state: DurableGoalState) -> dict[str, Any]:
    return {
        "artifact": None if state.artifact is None else state.artifact.to_payload(),
        "cancellation_reason": state.cancellation_reason,
        "contract": "EvaDurableGoalState/v1",
        "delivery_reason": state.delivery_reason,
        "delivery_status": state.delivery_status,
        "evidence": None if state.evidence is None else state.evidence.to_payload(),
        "goal": state.spec.to_payload(),
        "selected_instruction": state.selected_instruction,
        "status": state.status,
    }


def _bounded_string_fields(
    payload: dict[str, Any],
    fields: set[str] | tuple[str, ...],
    *,
    prefix: str,
    maximum_by_field: dict[str, int] | None = None,
) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in fields:
        value = payload.get(key)
        maximum = 240 if maximum_by_field is None else maximum_by_field.get(key, 240)
        if not isinstance(value, str) or not value.strip() or len(value) > maximum:
            raise HTTPException(status_code=422, detail=f"{prefix} {key} is invalid")
        values[key] = value.strip()
    return values



def create_app(
    runtime: MemoryRecallRuntime, *, dashboard_access_token: str | None = None
) -> FastAPI:
    start_background = getattr(runtime, "start_background_work", None)
    stop_background = getattr(runtime, "stop_background_work", None)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):  # type: ignore[no-untyped-def]
        if callable(start_background):
            start_background()
        try:
            yield
        finally:
            if callable(stop_background):
                stop_background()

    app = FastAPI(title="Eva Living Core", version="0.1.0", lifespan=lifespan)
    dashboard_access = DashboardAccess(dashboard_access_token)

    @app.get("/healthz")
    def health() -> dict[str, str]:
        return {"status": "alive"}

    def current_dashboard_identity():
        try:
            identity = load_current_identity_pack()
        except ValueError:
            raise HTTPException(status_code=503, detail="active identity unavailable")
        if identity.state != "current":
            raise HTTPException(status_code=503, detail="active identity unavailable")
        return identity

    @app.get("/readyz")
    def ready() -> dict[str, str | bool | None]:
        result = runtime.health()
        payload: dict[str, str | bool | None] = {
            "database": result.database,
            "detail": result.detail,
            "memory_projection": result.memory_projection,
            "model_provider": result.model_provider,
            "ready": result.ready,
        }
        if not result.ready:
            raise HTTPException(status_code=503, detail=payload)
        return payload

    @app.get("/internal/v1/dashboard/status")
    def dashboard_status(request: Request) -> JSONResponse:
        """Expose non-secret, read-only runtime status for the operator UI."""

        dashboard_access.require(request)
        result = runtime.health()
        return JSONResponse(
            {
                "contract": "EvaDashboardStatus/v1",
                "read_only": True,
                "runtime": {
                    "ready": result.ready,
                    "state": "ready" if result.ready else "not_ready",
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/internal/v1/dashboard/identity")
    def dashboard_identity(request: Request) -> JSONResponse:
        """Expose the verified active identity without a mutation surface."""

        dashboard_access.require(request)
        identity = current_dashboard_identity()
        return JSONResponse(
            {
                "contract": "EvaDashboardIdentity/v1",
                "read_only": True,
                "identity": {
                    "display_name": identity.display_name,
                    "identity_id": identity.identity_id,
                    "language": identity.language,
                    "pack_sha256": canonical_sha256(identity),
                    "pack_version": identity.pack_version,
                    "state": identity.state,
                    "temperament": identity.temperament,
                    "voice_style": {
                        "maximum_paragraphs": identity.voice_style.maximum_paragraphs,
                        "name": identity.voice_style.name,
                        "qualities": identity.voice_style.qualities,
                    },
                },
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/internal/v1/dashboard/identity/diff")
    def dashboard_identity_diff(
        request: Request, payload: dict[str, Any]
    ) -> JSONResponse:
        """Preview a staged IdentityPack presentation change without storing it."""

        dashboard_access.require(request)
        identity = current_dashboard_identity()
        try:
            draft = IdentityPackDraft.model_validate(payload)
            differences = identity_presentation_diff(draft, identity)
        except ValueError:
            raise HTTPException(status_code=422, detail="identity draft is invalid")
        return JSONResponse(
            {
                "contract": "EvaDashboardIdentityDiff/v1",
                "draft_id": draft.draft_id,
                "read_only": True,
                "base_pack_sha256": canonical_sha256(identity),
                "changes": [
                    {"field": item.field, "before": item.before, "after": item.after}
                    for item in differences
                ],
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/internal/v1/dashboard/identity/soul-import/diff")
    def dashboard_identity_soul_import_diff(
        request: Request, payload: dict[str, Any]
    ) -> JSONResponse:
        """Preview a small human SOUL.md without retaining or activating it."""

        dashboard_access.require(request)
        if set(payload) != {"draft_id", "soul_markdown"}:
            raise HTTPException(status_code=422, detail="SOUL.md import is invalid")
        draft_id = payload["draft_id"]
        markdown = payload["soul_markdown"]
        if not isinstance(draft_id, str) or not isinstance(markdown, str):
            raise HTTPException(status_code=422, detail="SOUL.md import is invalid")
        identity = current_dashboard_identity()
        try:
            draft = soul_markdown_to_draft(markdown, draft_id=draft_id, base=identity)
            differences = identity_presentation_diff(draft, identity)
        except SoulMarkdownError:
            raise HTTPException(status_code=422, detail="SOUL.md import is invalid")
        return JSONResponse(
            {
                "contract": "EvaSoulMarkdownImportPreview/v1",
                "draft_id": draft.draft_id,
                "read_only": True,
                "source_sha256": soul_markdown_sha256(markdown),
                "base_pack_sha256": canonical_sha256(identity),
                "changes": [
                    {"field": item.field, "before": item.before, "after": item.after}
                    for item in differences
                ],
                "non_claims": [
                    "SOUL.md is not opgeslagen of geactiveerd.",
                    "SOUL.md kan geen prompt, tool, policy, geheugen of permissie wijzigen.",
                ],
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/internal/v1/dashboard", response_class=HTMLResponse)
    def dashboard(request: Request) -> HTMLResponse:
        """Serve Eva's central local, read-only operator cockpit."""

        dashboard_access.require(request)
        return HTMLResponse(
            COCKPIT_HTML,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/internal/v1/dashboard/conversation", response_class=HTMLResponse)
    def dashboard_conversation(request: Request) -> HTMLResponse:
        """Serve the owner-only page for the bound private conversation."""

        dashboard_access.require(request)
        return HTMLResponse(
            DASHBOARD_CONVERSATION_HTML,
            headers={
                "Cache-Control": "no-store",
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.post("/internal/v1/dashboard/conversation/respond")
    def dashboard_conversation_respond(
        request: Request, payload: dict[str, Any], background_tasks: BackgroundTasks
    ) -> JSONResponse:
        """Submit one bound owner turn; browser input never sets its scope."""

        dashboard_access.require(request)
        if set(payload) != {"text"} or not isinstance(payload["text"], str):
            raise HTTPException(status_code=422, detail="owner conversation is invalid")
        respond_as_owner = getattr(runtime, "dashboard_turn", None)
        if not callable(respond_as_owner):
            raise HTTPException(
                status_code=503, detail="owner conversation unavailable"
            )
        try:
            turn, reply = respond_as_owner(payload["text"])
            if not isinstance(turn, ConversationTurn) or not isinstance(
                reply, GroundedReply
            ):
                raise ValueError("owner conversation result is invalid")
        except ConversationConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail="owner conversation is invalid"
            ) from error
        except RuntimeError as error:
            raise HTTPException(
                status_code=503, detail="owner conversation unavailable"
            ) from error
        background_tasks.add_task(runtime.after_response, turn, reply)
        return JSONResponse(
            {
                "contract": "EvaDashboardGroundedReply/v1",
                "conversation_id": reply.conversation_id,
                "evidence_memory_ids": list(reply.evidence_memory_ids),
                "generated_at": reply.generated_at,
                "message_id": reply.message_id,
                "provider": reply.provider,
                "text": reply.text,
                "text_sha256": reply.text_sha256,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/internal/v1/dashboard/conversation/erase")
    def dashboard_conversation_erase(
        request: Request, payload: dict[str, Any]
    ) -> JSONResponse:
        """Erase one dashboard exchange under the server-bound owner scope."""

        dashboard_access.require(request)
        if set(payload) != _DASHBOARD_CONVERSATION_ERASE_KEYS:
            raise HTTPException(
                status_code=422, detail="conversation erasure is invalid"
            )
        message_id = payload["message_id"]
        if (
            not isinstance(message_id, str)
            or not message_id.strip()
            or len(message_id) > 240
        ):
            raise HTTPException(
                status_code=422, detail="conversation erasure is invalid"
            )
        erase = getattr(runtime, "dashboard_erase_conversation_message", None)
        if not callable(erase):
            raise HTTPException(
                status_code=503, detail="conversation erasure unavailable"
            )
        try:
            erased = erase(message_id.strip())
            if not isinstance(erased, bool):
                raise RuntimeError("conversation erasure result is invalid")
        except ConversationConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(
                status_code=422, detail="conversation erasure is invalid"
            ) from error
        except RuntimeError as error:
            raise HTTPException(
                status_code=503, detail="conversation erasure unavailable"
            ) from error
        return JSONResponse(
            {
                "contract": "EvaDashboardConversationErasure/v1",
                "disposition": "erased" if erased else "already_requested",
                "message_sha256": hashlib.sha256(
                    message_id.strip().encode("utf-8")
                ).hexdigest(),
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/internal/v1/dashboard/progress")
    def dashboard_progress(request: Request) -> JSONResponse:
        """Expose the maintained proof ladder without implying live runtime state."""

        dashboard_access.require(request)
        return JSONResponse(
            cockpit_progress(),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/internal/v1/dashboard/people-memory")
    def dashboard_people_memory(request: Request) -> JSONResponse:
        """Expose coarse family/memory states without content or identifiers."""

        dashboard_access.require(request)
        projection = getattr(runtime, "dashboard_people_memory", None)
        if not callable(projection):
            raise HTTPException(
                status_code=503, detail="people and memory overview unavailable"
            )
        try:
            overview = projection()
        except Exception as error:
            raise HTTPException(
                status_code=503, detail="people and memory overview unavailable"
            ) from error
        if not isinstance(overview, PeopleMemoryOverview):
            raise HTTPException(
                status_code=503, detail="people and memory overview unavailable"
            )
        return JSONResponse(
            overview.to_payload(),
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/internal/v1/dashboard/workboard")
    def dashboard_workboard(request: Request) -> JSONResponse:
        """Expose a fixed local operator's field-minimized work cards."""

        dashboard_access.require(request)
        projection = getattr(runtime, "dashboard_workboard", None)
        if not callable(projection):
            raise HTTPException(status_code=503, detail="workboard unavailable")
        try:
            workboard = projection()
        except Exception as error:
            raise HTTPException(
                status_code=503, detail="workboard unavailable"
            ) from error
        if not isinstance(workboard, WorkBoard):
            raise HTTPException(status_code=503, detail="workboard unavailable")
        return JSONResponse(
            workboard.model_dump(mode="json"),
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/internal/v1/dashboard/effects/stop")
    def dashboard_stop_effect(
        request: Request, payload: dict[str, Any]
    ) -> JSONResponse:
        """Persist one owner stop request; never claim an external undo."""

        dashboard_access.require(request)
        if set(payload) != _DASHBOARD_EFFECT_STOP_KEYS:
            raise HTTPException(
                status_code=422, detail="effect stop request is invalid"
            )
        raw_effect_intent_id = payload["effect_intent_id"]
        if not isinstance(raw_effect_intent_id, str):
            raise HTTPException(
                status_code=422, detail="effect stop request is invalid"
            )
        try:
            effect_intent_id = UUID(raw_effect_intent_id)
        except ValueError:
            raise HTTPException(
                status_code=422, detail="effect stop request is invalid"
            )
        stop = getattr(runtime, "dashboard_stop_effect", None)
        if not callable(stop):
            raise HTTPException(status_code=503, detail="effect stop unavailable")
        try:
            result = stop(effect_intent_id)
            if str(result.effect_intent_id) != str(effect_intent_id):
                raise ValueError("effect stop result crossed the requested intent")
            disposition = result.disposition
            message = result.message
            readback_required = result.readback_required
            if (
                not isinstance(disposition, str)
                or not isinstance(message, str)
                or not isinstance(readback_required, bool)
            ):
                raise ValueError("effect stop result is invalid")
        except Exception as error:
            raise HTTPException(
                status_code=503, detail="effect stop unavailable"
            ) from error
        return JSONResponse(
            {
                "contract": "EvaDashboardEffectStop/v1",
                "effect_intent_id": str(effect_intent_id),
                "disposition": disposition,
                "message": message,
                "readback_required": readback_required,
            },
            headers={"Cache-Control": "no-store"},
        )

    @app.post("/public/v1/commerce/stripe/webhook")
    async def stripe_paid_order_webhook(request: Request) -> dict[str, object]:
        record = getattr(runtime, "record_stripe_webhook", None)
        if not callable(record):
            raise HTTPException(status_code=501, detail="Stripe webhook unavailable")
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > 1_048_576:
                    raise HTTPException(status_code=413, detail="Webhook is too large")
            except ValueError as error:
                raise HTTPException(
                    status_code=400, detail="Webhook content length is invalid"
                ) from error
        signature = request.headers.get("stripe-signature", "")
        if not signature:
            raise HTTPException(status_code=400, detail="Stripe signature is missing")
        payload = await request.body()
        if len(payload) > 1_048_576:
            raise HTTPException(status_code=413, detail="Webhook is too large")
        try:
            return record(payload=payload, signature_header=signature)
        except CommerceError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(status_code=503, detail=str(error)) from error

    @app.get("/public/v1/commerce/orders/{checkout_session_id}/download")
    def paid_product_download(checkout_session_id: str) -> Response:
        load = getattr(runtime, "paid_product_download", None)
        if not callable(load):
            raise HTTPException(status_code=501, detail="paid download unavailable")
        if (
            not checkout_session_id.startswith("cs_")
            or len(checkout_session_id) > 168
            or not checkout_session_id.isascii()
        ):
            raise HTTPException(status_code=404, detail="paid order not found")
        try:
            download: DigitalFulfillmentDownload = load(
                checkout_session_id=checkout_session_id
            )
        except (CommerceError, RuntimeError, ValueError) as error:
            raise HTTPException(
                status_code=404, detail="paid order not found"
            ) from error
        return Response(
            content=download.content,
            media_type="application/zip",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": (
                    f'attachment; filename="{download.grant.filename}"'
                ),
                "ETag": f'"{download.grant.release_sha256}"',
                "X-Eva-Fulfillment-Grant": download.grant.sha256,
            },
        )

    @app.post("/internal/v1/memory/recall")
    def recall(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            query = _query_from_wire(payload)
            return _memory_payload(runtime.recall(query))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

    @app.put("/internal/v1/memory/documents")
    def retain(payload: dict[str, Any]) -> dict[str, str]:
        try:
            receipt = runtime.retain(_document_from_wire(payload))
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        return {
            "bank_id": receipt.bank_id,
            "content_sha256": receipt.content_sha256,
            "contract": "EvaInternalMemoryRetentionReceipt/v1",
            "disposition": receipt.disposition,
            "document_id": receipt.document_id,
        }

    @app.post("/internal/v1/turns/respond")
    def respond(
        payload: dict[str, Any], background_tasks: BackgroundTasks
    ) -> dict[str, Any]:
        try:
            turn, recent_messages = _turn_from_wire(payload)
            runtime.admit_turn_audience(turn)
            reply = runtime.respond(turn, recent_messages)
        except ConversationConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(
                status_code=503,
                detail="Eva's conversation model is tijdelijk unavailable",
            ) from error
        background_tasks.add_task(runtime.after_response, turn, reply)
        return grounded_reply_wire(reply)

    @app.post("/internal/v1/telegram/canary/respond")
    def respond_telegram_canary(
        payload: dict[str, Any], background_tasks: BackgroundTasks, request: Request
    ) -> Any:
        try:
            if set(payload) != _TELEGRAM_CANARY_KEYS:
                raise ValueError("Telegram canary request differs")
            telegram_user_id = payload["telegram_user_id"]
            chat_id = payload["chat_id"]
            message_id = payload["message_id"]
            occurred_at = payload["occurred_at"]
            text = payload["text"]
            if (
                not isinstance(telegram_user_id, int)
                or isinstance(telegram_user_id, bool)
                or telegram_user_id < 1
                or not isinstance(chat_id, str)
                or not isinstance(message_id, int)
                or isinstance(message_id, bool)
                or message_id < 1
                or not isinstance(occurred_at, str)
                or not isinstance(text, str)
            ):
                raise ValueError("Telegram canary request is invalid")
            now = datetime.strptime(occurred_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=UTC
            )
            operation_id = f"telegram-canary:{chat_id}:{message_id}"
            admission = runtime.admit_telegram_canary(
                telegram_user_id=telegram_user_id,
                chat_id=chat_id,
                text=text,
                operation_id=operation_id,
                now=now,
            )
            conversation_id = f"telegram:{chat_id}"
            durable_message_id = f"telegram:{chat_id}:{message_id}"
            if admission.local_reply is not None:
                local_text = admission.local_reply
                return {
                    "contract": "EvaInternalGroundedReply/v1",
                    "conversation_id": conversation_id,
                    "evidence_memory_ids": [],
                    "generated_at": occurred_at,
                    "message_id": durable_message_id,
                    "provider": "telegram-canary-local",
                    "text": local_text,
                    "text_sha256": hashlib.sha256(local_text.encode()).hexdigest(),
                }
            assert admission.audience is not None
            recent_messages = (
                payload["recent_messages"]
                if admission.audience.kind == "private_person"
                else []
            )
            turn, recent = _turn_from_wire(
                {
                    "audience": memory_audience_to_wire(admission.audience),
                    "conversation_id": conversation_id,
                    "installation_id": payload["installation_id"],
                    "message_id": durable_message_id,
                    "occurred_at": occurred_at,
                    "principal_id": admission.principal_id,
                    "recent_messages": recent_messages,
                    "tenant_id": payload["tenant_id"],
                    "text": text,
                }
            )
            runtime.admit_turn_audience(turn)
            delegated_reply = None
            delegated = getattr(runtime, "respond_telegram_delegated_work", None)
            if delegated is not None:
                delegated_reply = delegated(
                    turn,
                    telegram_user_id=telegram_user_id,
                    chat_id=chat_id,
                )
            if delegated_reply is not None:
                return grounded_reply_wire(delegated_reply)
            if "application/x-ndjson" in request.headers.get(
                "accept", ""
            ) and not chat_id.startswith("-"):
                return conversation_stream_response(runtime, turn, recent)
            reply = runtime.respond(turn, recent)
        except ConversationConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except RuntimeError as error:
            raise HTTPException(
                status_code=503,
                detail="Eva's Telegram canary is tijdelijk unavailable",
            ) from error
        background_tasks.add_task(runtime.after_response, turn, reply)
        return grounded_reply_wire(reply)

    @app.post("/internal/v1/goals/admit")
    def admit_durable_goal(payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {"goal", "installation_id", "tenant_id"}:
            raise HTTPException(status_code=422, detail="durable goal request differs")
        try:
            state = runtime.admit_durable_goal(
                tenant_id=payload["tenant_id"],
                installation_id=payload["installation_id"],
                goal=_durable_goal_from_wire(payload["goal"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except DurableGoalError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _durable_goal_payload(state)

    @app.post("/internal/v1/goals/cancel")
    def cancel_durable_goal(payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {
            "goal_id",
            "installation_id",
            "occurred_at",
            "owner_id",
            "reason",
            "tenant_id",
        }:
            raise HTTPException(
                status_code=422, detail="goal cancellation request differs"
            )
        try:
            occurred_at = datetime.fromisoformat(
                payload["occurred_at"].replace("Z", "+00:00")
            )
            state = runtime.cancel_durable_goal(
                tenant_id=payload["tenant_id"],
                installation_id=payload["installation_id"],
                goal_id=payload["goal_id"],
                owner_id=payload["owner_id"],
                reason=payload["reason"],
                occurred_at=occurred_at,
            )
        except (AttributeError, KeyError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except DurableGoalError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return _durable_goal_payload(state)

    @app.post("/internal/v1/goals/state")
    def durable_goal_state(payload: dict[str, Any]) -> dict[str, Any]:
        if set(payload) != {
            "goal_id",
            "installation_id",
            "owner_id",
            "tenant_id",
        }:
            raise HTTPException(status_code=422, detail="goal state request differs")
        try:
            state = runtime.durable_goal_state(
                tenant_id=payload["tenant_id"],
                installation_id=payload["installation_id"],
                goal_id=payload["goal_id"],
                owner_id=payload["owner_id"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        if state is None:
            raise HTTPException(status_code=404, detail="durable goal not found")
        return _durable_goal_payload(state)

    @app.post("/internal/v1/proactive/outbox/claim")
    def claim_proactive_expression(response: Response) -> dict[str, Any] | None:
        claim = getattr(runtime, "claim_proactive_expression", None)
        if not callable(claim):
            raise HTTPException(status_code=501, detail="Proactive outbox unavailable")
        result = claim()
        if result is None:
            response.status_code = 204
            return None
        if not isinstance(result, ProactiveExpressionClaim):
            raise HTTPException(status_code=503, detail="Proactive outbox is invalid")
        return {
            "contract": "EvaProactiveExpressionClaim/v1",
            "operation_id": result.operation_id,
            "retry_count": result.retry_count,
            "revision": result.revision,
            "source_event_id": result.source_event_id,
            "text": result.text,
            "thread_id": result.thread_id,
            "urgency": result.urgency,
        }

    @app.post("/internal/v1/proactive/outbox/ack")
    def acknowledge_proactive_expression(payload: dict[str, Any]) -> dict[str, str]:
        acknowledge = getattr(runtime, "acknowledge_proactive_expression", None)
        if not callable(acknowledge):
            raise HTTPException(status_code=501, detail="Proactive outbox unavailable")
        if set(payload) != _PROACTIVE_ACK_KEYS:
            raise HTTPException(status_code=422, detail="Proactive receipt is invalid")
        try:
            acknowledge(
                thread_id=payload["thread_id"],
                revision=payload["revision"],
                channel=payload["channel"],
                provider_message_id=payload["provider_message_id"],
            )
        except (KeyError, TypeError, ValueError) as error:
            raise HTTPException(
                status_code=422, detail="Proactive receipt is invalid"
            ) from error
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"contract": "EvaProactiveExpressionReceipt/v1", "status": "accepted"}


    @app.post("/internal/v1/commerce/publications/plan")
    def plan_product_publication(payload: dict[str, Any]) -> dict[str, object]:
        planner = getattr(runtime, "plan_product_publication", None)
        if not callable(planner):
            raise HTTPException(status_code=501, detail="commerce planning unavailable")
        if (
            set(payload) != _COMMERCE_PLAN_KEYS
            or payload.get("mode") != "plan_latest_release"
        ):
            raise HTTPException(
                status_code=422,
                detail="commerce plan has unknown, missing or invalid fields",
            )
        values = _bounded_string_fields(
            payload,
            _COMMERCE_PLAN_KEYS - {"mode"},
            prefix="commerce plan",
        )
        try:
            return planner(**values)
        except (CommerceError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/internal/v1/commerce/publications/approve")
    def approve_product_publication(payload: dict[str, Any]) -> dict[str, object]:
        approve = getattr(runtime, "approve_product_publication", None)
        if not callable(approve):
            raise HTTPException(status_code=501, detail="commerce approval unavailable")
        if (
            set(payload) != _COMMERCE_APPROVAL_KEYS
            or payload.get("mode") != "approve_exact_plan"
        ):
            raise HTTPException(
                status_code=422,
                detail="commerce approval has unknown, missing or invalid fields",
            )
        values = _bounded_string_fields(
            payload,
            _COMMERCE_APPROVAL_KEYS - {"mode"},
            prefix="commerce approval",
        )
        try:
            approval: ProductPublicationApproval = approve(**values)
        except (CommerceError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {**approval.to_payload(), "approval_sha256": approval.sha256}

    @app.post("/internal/v1/commerce/publications/publish")
    async def publish_product(payload: dict[str, Any]) -> dict[str, object]:
        publish = getattr(runtime, "publish_approved_product", None)
        if not callable(publish):
            raise HTTPException(
                status_code=501, detail="commerce publication unavailable"
            )
        if (
            set(payload) != _COMMERCE_PUBLISH_KEYS
            or payload.get("mode") != "publish_approved_plan"
        ):
            raise HTTPException(
                status_code=422,
                detail="commerce publication has unknown, missing or invalid fields",
            )
        values = _bounded_string_fields(
            payload,
            _COMMERCE_PUBLISH_KEYS - {"mode"},
            prefix="commerce publication",
        )
        try:
            return await publish(**values)
        except (CommerceError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.post("/internal/v1/commerce/publications/reconcile")
    async def reconcile_product_publication(
        payload: dict[str, Any],
    ) -> dict[str, object]:
        reconcile = getattr(runtime, "reconcile_product_publication", None)
        if not callable(reconcile):
            raise HTTPException(
                status_code=501, detail="commerce reconciliation unavailable"
            )
        if (
            set(payload) != _COMMERCE_RECONCILE_KEYS
            or payload.get("mode") != "reconcile_provider_readback"
        ):
            raise HTTPException(
                status_code=422,
                detail="commerce reconciliation has unknown, missing or invalid fields",
            )
        values = _bounded_string_fields(
            payload,
            _COMMERCE_RECONCILE_KEYS - {"mode"},
            prefix="commerce reconciliation",
        )
        try:
            return await reconcile(**values)
        except (CommerceError, RuntimeError, ValueError) as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @app.get("/internal/v1/personal-projects/latest/download")
    def download_latest_personal_project(
        tenant_id: str, installation_id: str, principal_id: str
    ) -> Response:
        if not all(
            value.strip() for value in (tenant_id, installation_id, principal_id)
        ):
            raise HTTPException(status_code=422, detail="project scope is incomplete")
        loader = getattr(runtime, "latest_personal_prototype", None)
        if not callable(loader):
            raise HTTPException(status_code=501, detail="project artifacts unavailable")
        artifact = loader(tenant_id, installation_id, principal_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="personal prototype not found")
        prototype = artifact.prototype
        return Response(
            content=prototype.content.encode("utf-8"),
            media_type=prototype.media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{prototype.filename}"',
                "ETag": f'"{prototype.content_sha256}"',
                "X-Eva-Intent-Id": artifact.intent_id,
            },
        )

    @app.get("/internal/v1/personal-projects/latest/release")
    def download_latest_personal_release(
        tenant_id: str, installation_id: str, principal_id: str
    ) -> Response:
        if not all(
            value.strip() for value in (tenant_id, installation_id, principal_id)
        ):
            raise HTTPException(status_code=422, detail="project scope is incomplete")
        loader = getattr(runtime, "latest_personal_product_release", None)
        if not callable(loader):
            raise HTTPException(status_code=501, detail="product releases unavailable")
        artifact = loader(tenant_id, installation_id, principal_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="product release not found")
        release = artifact.release
        return Response(
            content=release.content,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{release.filename}"',
                "ETag": f'"{release.content_sha256}"',
                "X-Eva-Intent-Id": artifact.intent_id,
                "X-Eva-Publication-Status": "unpublished_release_candidate",
                "X-Eva-Suggested-Price-Eur-Cents": str(release.listing.price_eur_cents),
            },
        )

    @app.get("/internal/v1/research/latest/download")
    def download_latest_current_research(
        tenant_id: str, installation_id: str, principal_id: str
    ) -> Response:
        if not all(
            value.strip() for value in (tenant_id, installation_id, principal_id)
        ):
            raise HTTPException(status_code=422, detail="research scope is incomplete")
        loader = getattr(runtime, "latest_current_research", None)
        if not callable(loader):
            raise HTTPException(
                status_code=501, detail="research artifacts unavailable"
            )
        artifact = loader(tenant_id, installation_id, principal_id)
        if artifact is None:
            raise HTTPException(status_code=404, detail="research artifact not found")
        return Response(
            content=artifact.content.encode("utf-8"),
            media_type="text/markdown",
            headers={
                "Content-Disposition": (
                    'attachment; filename="eva-research-'
                    f'{artifact.content_sha256[:16]}.md"'
                ),
                "ETag": f'"{artifact.content_sha256}"',
                "X-Eva-Research-Source-Count": str(len(artifact.sources)),
                "X-Eva-Researched-At": artifact.researched_at,
            },
        )

    return app


__all__ = ["MemoryRecallRuntime", "RuntimeHealth", "create_app"]
