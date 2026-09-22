"""One private owner service shared by chat tools and legacy Telegram commands."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eva.application.delegated_work_delivery import PrivateWorkDestination

from eva.adapters.postgres.delegated_work import (
    DelegatedWorkConflict, DelegatedWorkNotFound, DelegatedWorkNotResumable,
    PostgresDelegatedWorkStore,
    StoredDelegatedWork,
)
from eva.adapters.postgres.operational_timeline import TimelineScope, external_timeline_scope
from eva.application.conversation import ConversationTurn
from eva.bootstrap.delegated_work_provider import build_unadmitted_delegated_work_spec
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_work import DelegatedResearchSpec, PublicResearchRequest
from eva.contracts.timestamps import parse_utc_timestamp


class DelegatedWorkOwnerService:
    def __init__(self, store: PostgresDelegatedWorkStore, *, owner_principal_id: UUID,
                 principal_binding_id: UUID, clock: Callable[[], datetime] | None = None,
                 conversation_scope: TimelineScope | None = None,
                 private_bridge_principal_loader: Callable[[], str | None] | None = None,
                 delivery_destination_loader: Callable[[ConversationTurn], PrivateWorkDestination | None] | None = None,
                 delivery_status_loader: Callable[[str], str] | None = None,
                 research_profile=None):
        self.store = store
        self.research_profile = research_profile
        # Runtime binds the conversation namespace separately from storage authority.
        self.conversation_scope = conversation_scope or store.scope
        self.owner_principal_id = owner_principal_id
        self.principal_binding_id = principal_binding_id
        self._private_bridge_principal_loader = private_bridge_principal_loader
        self._delivery_destination_loader = delivery_destination_loader
        self._delivery_status_loader = delivery_status_loader
        self._clock = clock or (lambda: datetime.now(UTC).replace(microsecond=0))
        store.validate_admission_authority(owner_principal_id=owner_principal_id,
                                           principal_binding_id=principal_binding_id)

    def available_for(self, turn: ConversationTurn) -> bool:
        """Structural owner gate; each store operation revalidates current authority."""
        if (turn.audience.kind != "private_person"
                or turn.audience.principal_id != turn.principal_id):
            return False
        if turn.principal_id != str(self.owner_principal_id):
            # Keep the original Telegram conversation/memory identity. Only the
            # current trusted binding can associate it with this durable owner.
            if self._private_bridge_principal_loader is None:
                return False
            try:
                alias = self._private_bridge_principal_loader()
            except (OSError, ValueError, RuntimeError):
                return False
            if alias is None or turn.principal_id != alias:
                return False
        return external_timeline_scope(turn.tenant_id, turn.installation_id,
                                       "delegated owner scope is required") == self.conversation_scope

    def validate_authority(self, turn: ConversationTurn) -> None:
        """Recheck the current binding before returning even a saved action receipt."""
        if not self.available_for(turn):
            raise DelegatedWorkConflict("delegated work requires its private owner scope")
        self.store.validate_admission_authority(**self._owner)

    def now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None or value.microsecond:
            raise RuntimeError("delegated owner clock must use aware whole seconds")
        return value.astimezone(UTC)

    @property
    def _owner(self):
        return {"owner_principal_id": self.owner_principal_id,
                "principal_binding_id": self.principal_binding_id}

    def execute(self, operation: str, turn: ConversationTurn, *, task: str | None = None,
                work_id: str | None = None, request_key: str, research: dict | None = None) -> dict:
        try:
            return self._execute(operation, turn, task=task, work_id=work_id, request_key=request_key, research=research)
        except DelegatedWorkNotFound:
            return {"contract": "EvaDelegatedWorkOwnerResult/v1", "operation": operation,
                    "status": "not_found", "work": None,
                    "guidance": "Deze taak is niet gevonden binnen jouw eigen werkoverzicht."}
        except DelegatedWorkNotResumable as error:
            return {"contract": "EvaDelegatedWorkOwnerResult/v1", "operation": operation,
                    "status": "not_resumable", "work": self._view(error.work),
                    "guidance": "De taak is nog actief of al geslaagd. Er is geen nieuwe poging ingepland."}

    def _execute(self, operation: str, turn: ConversationTurn, *, task: str | None,
                 work_id: str | None, request_key: str, research: dict | None = None) -> dict:
        if not self.available_for(turn):
            raise DelegatedWorkConflict("delegated work requires its private owner scope")
        if operation not in {"start", "research", "list", "get", "result", "stop", "resume"}:
            raise ValueError("delegated work operation is invalid")
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 2048:
            raise ValueError("delegated work request key is required")
        if operation == "research":
            if self.research_profile is None or task is not None or work_id is not None:
                raise DelegatedWorkConflict("public background research is not composed")
            public = PublicResearchRequest.model_validate(research)
            task = public.question
        elif research is not None:
            raise ValueError("research arguments require their explicit operation")
        response = {"contract": "EvaDelegatedWorkOwnerResult/v1", "operation": operation}
        if operation == "list":
            if task is not None or work_id is not None:
                raise ValueError("list does not accept task or work id")
            rows = self.store.list_owned(**self._owner, limit=21)
            return {**response, "jobs": [self._view(row, include_result=False) for row in rows[:20]],
                    "truncated": len(rows) > 20}
        if operation in {"start", "research"}:
            if work_id is not None or not isinstance(task, str) or not task.strip():
                raise ValueError("start requires one task")
            task = task.strip()
            if len(task.encode("utf-8")) > 16384:
                raise ValueError("delegated task exceeds its existing contract bound")
            # Legacy chat keys already contain exact scope/owner/ingress identity.
            # General action keys gain those same boundaries before deriving an ID.
            legacy_key = self.command_key(turn)
            key = legacy_key if request_key == legacy_key else canonical_sha256({
                "tenant": turn.tenant_id, "installation": turn.installation_id,
                "owner": turn.principal_id, "conversation": turn.conversation_id,
                "message": turn.message_id, "request": request_key,
            })
            identity = uuid5(NAMESPACE_URL, key)
            created = parse_utc_timestamp(turn.occurred_at)
            builder = (build_unadmitted_delegated_work_spec if operation == "start"
                       else self.research_profile.build_spec)
            spec = builder(
                **({"research": public.to_payload()} if operation == "research" else {}),
                work_id="work:eva-chat:" + str(identity),
                episode_id=str(uuid5(NAMESPACE_URL, ":".join((
                    "delegated-private-context-v1", turn.tenant_id, turn.installation_id,
                    turn.principal_id, turn.conversation_id)))),
                generation=1, parent_task_id="chat:" + str(identity),
                owner_principal_id=str(self.owner_principal_id),
                principal_binding_id=str(self.principal_binding_id), task=task,
                created_at=created, deadline=created + timedelta(minutes=5),
            )
            admission = self.store.admit(
                spec=spec, title="Onderzoek vanuit Eva-chat",
                summary="Een afgeschermde onderzoeker werkt aan de gegeven opdracht.",
                worker_role="Onderzoek", retention_policy_id="retention:delegated-work:v1",
                retention_until=created + timedelta(days=7), occurred_at=created,
                **self._delivery_arguments(turn),
            )
            return {**response, "created": admission.disposition == "inserted", "work": self._view(admission.work)}
        if task is not None or not isinstance(work_id, str) or not work_id:
            raise ValueError("delegated work operation requires its work id")
        if operation == "stop":
            work = self.store.request_stop(work_id, **self._owner, now=self.now())
            return {**response, "work": self._view(work)}
        previous = self.store.load(work_id, **self._owner)
        if operation in {"get", "result"}:
            return {**response, "work": self._view(previous)}
        created = parse_utc_timestamp(turn.occurred_at)
        next_id = "work:eva-resume:" + str(uuid5(
            self.store.scope.installation_id,
            f"{previous.spec.work_id}:{previous.spec.generation + 1}",
        ))
        is_research = isinstance(previous.spec, DelegatedResearchSpec)
        if is_research and self.research_profile is None:
            raise DelegatedWorkConflict("research resume requires its configured profile")
        builder = self.research_profile.build_spec if is_research else build_unadmitted_delegated_work_spec
        spec = builder(
            **({"research": previous.spec.research.to_payload()} if is_research else {}),
            work_id=next_id, episode_id=previous.spec.episode_id,
            generation=previous.spec.generation + 1, parent_task_id=previous.spec.work_id,
            owner_principal_id=str(self.owner_principal_id),
            principal_binding_id=str(self.principal_binding_id), task=previous.spec.task,
            created_at=created, deadline=created + timedelta(minutes=5),
        )
        admission = self.store.admit(
            spec=spec, title=previous.title, summary=previous.summary,
            worker_role=previous.worker_role, retention_policy_id=previous.retention_policy_id,
            retention_until=max(previous.retention_until, created + timedelta(days=7)),
            occurred_at=created, resumes_work_id=previous.spec.work_id,
            **self._delivery_arguments(turn),
        )
        return {**response, "created": admission.disposition == "inserted",
                "previous": self._view(previous), "work": self._view(admission.work),
                "prior_outcome_unknown": (previous.state in {"call_started", "ambiguous"}
                    or (previous.state == "stopped" and previous.call_id is not None
                        and (previous.usage is None or previous.usage.measurement != "measured"
                             or previous.usage.completed_model_responses != 1))),
                "guidance": "Een nieuwe begrensde poging; de eerdere call en het eerdere verbruik blijven bewaard."}

    def private_return(self, turn, operation, **kwargs):
        from eva.adapters.postgres.private_work_return import PrivateWorkReturnStore
        return PrivateWorkReturnStore(self).execute(turn, operation, **kwargs)

    def _delivery_arguments(self, turn: ConversationTurn) -> dict:
        if self._delivery_destination_loader is None:
            return {}
        # Admission stores this alongside the task in one transaction. A failed
        # authority lookup must not silently admit work without its return route.
        self.validate_authority(turn)
        return {"delivery_destination": self._delivery_destination_loader(turn)}

    def latest_work_id(self, turn: ConversationTurn, *, stoppable=False) -> str | None:
        if not self.available_for(turn):
            raise DelegatedWorkConflict("delegated work requires its private owner scope")
        if stoppable:
            return self.store.latest_stoppable_work_id(
                **self._owner, request_key=self.command_key(turn), now=self.now(),
            )
        rows = self.store.list_owned(**self._owner, limit=1)
        return rows[0].spec.work_id if rows else None

    def board(self, turn: ConversationTurn):
        if not self.available_for(turn):
            raise DelegatedWorkConflict("delegated work requires its private owner scope")
        return self.store.workboard(**self._owner, now=self.now())

    @staticmethod
    def command_key(turn: ConversationTurn) -> str:
        return ":".join((turn.tenant_id, turn.installation_id, turn.principal_id,
                         turn.conversation_id, turn.message_id))

    def _delivery_view(self, work_id: str) -> dict:
        descriptions = {
            "unbound": "Geen automatische terugmelding gekoppeld.",
            "awaiting_result": "Automatische terugmelding wacht op een afgerond resultaat.",
            "ready": "Automatische terugmelding staat klaar; bezorging is nog niet bevestigd.",
            "delivery_claimed": "Automatische terugmelding is gestart; bezorging is nog niet bevestigd.",
            "delivery_acknowledged": "Automatische terugmelding is afgeleverd in je gekoppelde privéchat.",
            "delivery_ambiguous": "Bezorging van de automatische terugmelding is onzeker; ik stuur niet automatisch opnieuw. Je kunt het resultaat hier opvragen.",
            "unknown": "De status van de automatische terugmelding is onbekend.",
            "blocked": "Automatische terugmelding is niet beschikbaar of niet te verifiëren. Je kunt een beschikbaar resultaat hier opvragen.",
        }
        state = "unknown"
        if self._delivery_status_loader is not None:
            try:
                candidate = self._delivery_status_loader(work_id)
                if isinstance(candidate, str) and candidate in descriptions:
                    state = candidate
            except Exception:
                # Delivery authority failure must not hide a separately admitted
                # owner result or reveal destination/credential exception details.
                state = "blocked"
        return {"state": state, "description": descriptions[state]}

    def _view(self, work: StoredDelegatedWork, *, include_result=True) -> dict:
        result = None
        if work.result is not None and work.state in {"succeeded", "verified"}:
            result = {"output": work.result.output, "output_sha256": work.result.output_sha256,
                      "completed_at": work.result.completed_at, "verified": work.state == "verified"}
        return {"work_id": work.spec.work_id, "generation": work.spec.generation,
                "parent_work_id": work.spec.parent_task_id if work.spec.generation > 1 else None,
                "state": work.state, "title": work.title,
                **({"task": work.spec.task} if include_result else {"task_preview": work.spec.task[:240]}),
                "created_at": work.spec.created_at, "deadline": work.spec.deadline,
                "call_id": work.call_id, "stop_requested": work.stop_requested_at is not None,
                "usage": None if work.usage is None else work.usage.to_payload(),
                "error_code": work.error_code, "error_fingerprint": work.error_fingerprint,
                "result_available": result is not None,
                "delivery": self._delivery_view(work.spec.work_id),
                **({"result": result} if include_result else {}),
                "scope": "private_public_research_worker" if isinstance(work.spec, DelegatedResearchSpec) else "private_tool_free_worker", "new_model_run_on_resume": True}


__all__ = ["DelegatedWorkOwnerService"]
