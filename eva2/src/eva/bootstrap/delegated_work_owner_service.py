"""One private owner service shared by chat tools and legacy Telegram commands."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid5
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from eva.application.delegated_work_delivery import PrivateWorkDestination

from eva.application.work_result_presentation import work_failure_description

from eva.adapters.postgres.delegated_work import (
    DelegatedWorkConflict, DelegatedWorkNotFound, DelegatedWorkNotResumable,
    PostgresDelegatedWorkStore,
    StoredDelegatedWork,
)
from eva.adapters.postgres.operational_timeline import TimelineScope, external_timeline_scope
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.application.conversation import ConversationTurn
from eva.bootstrap.delegated_work_provider import build_unadmitted_delegated_work_spec
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_work import DelegatedResearchSpec, PublicResearchRequest
from eva.contracts.delegated_digital_work import DelegatedDigitalWorkSpec
from eva.contracts.timestamps import parse_utc_timestamp


class DelegatedWorkOwnerService:
    def __init__(self, store: PostgresDelegatedWorkStore, *, owner_principal_id: UUID,
                 principal_binding_id: UUID, clock: Callable[[], datetime] | None = None,
                 conversation_scope: TimelineScope | None = None,
                 private_bridge_principal_loader: Callable[[], str | None] | None = None,
                 delivery_destination_loader: Callable[[ConversationTurn], PrivateWorkDestination | None] | None = None,
                 delivery_status_loader: Callable[[str], str] | None = None,
                 research_profile=None, digital_profile=None):
        self.store = store
        self.research_profile = research_profile
        self.digital_profile = digital_profile
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
                work_id: str | None = None, request_key: str, research: dict | None = None,
                intent_id: str | None = None, feedback: str | None = None,
                schedule: dict | None = None, schedule_id: str | None = None,
                search: dict | None = None, revision: dict | None = None, prior_work_id: str | None = None) -> dict:
        try:
            return self._execute(operation, turn, task=task, work_id=work_id, request_key=request_key, research=research, intent_id=intent_id, feedback=feedback, schedule=schedule, schedule_id=schedule_id, search=search, revision=revision, prior_work_id=prior_work_id)
        except DelegatedWorkNotFound:
            return {"contract": "EvaDelegatedWorkOwnerResult/v1", "operation": operation,
                    "status": "not_found", "work": None,
                    "guidance": "Deze taak is niet gevonden binnen jouw eigen werkoverzicht."}
        except DelegatedWorkNotResumable as error:
            return {"contract": "EvaDelegatedWorkOwnerResult/v1", "operation": operation,
                    "status": "not_resumable", "work": self._view(error.work, turn=turn),
                    "guidance": "De taak is nog actief of al geslaagd. Er is geen nieuwe poging ingepland."}

    def _execute(self, operation: str, turn: ConversationTurn, *, task: str | None,
                 work_id: str | None, request_key: str, research: dict | None = None,
                 intent_id: str | None = None, feedback: str | None = None,
                 schedule: dict | None = None, schedule_id: str | None = None,
                 admission_time: datetime | None = None, search: dict | None = None, revision: dict | None = None, prior_work_id: str | None = None) -> dict:
        if not self.available_for(turn):
            raise DelegatedWorkConflict("delegated work requires its private owner scope")
        prior_research = None
        if prior_work_id is not None:
            if operation != "research" or not isinstance(prior_work_id, str) or not 1 <= len(prior_work_id) <= 240:
                raise ValueError("prior work is only valid for research follow-up")
            self.validate_authority(turn)
            prior_research = self.store.load(prior_work_id, **self._owner)
            if (not isinstance(prior_research.spec, DelegatedResearchSpec)
                    or (prior_research.state in {"admitted", "call_started"}
                        and prior_research.stop_requested_at is None)
                    or prior_research.retention_until <= self.now()):
                raise ValueError("research follow-up requires a finished attempt or a recorded stop")
        from eva.adapters.postgres.scheduled_work import ScheduledOwnerWork
        if operation == "revise_digital":
            if (any(value is not None for value in (task, work_id, research, intent_id, feedback, schedule, schedule_id, search))
                    or not isinstance(revision, dict) or set(revision) != {"work_id", "instruction"}
                    or not isinstance(revision["instruction"], str)
                    or not 1 <= len(revision["instruction"].strip()) <= 2000):
                raise ValueError("digital correction requires a work ID and instruction")
            self.validate_authority(turn)
            previous = self.store.load(revision["work_id"], **self._owner)
            if not isinstance(previous.spec, DelegatedDigitalWorkSpec):
                return {"status": "not_digital", "guidance": "Deze bijsturing geldt voor digitale opdrachten. Stop ander werk gericht en neem de gewijzigde opdracht opnieuw aan via de passende route."}
            successor_id = "work:eva-resume:" + str(uuid5(self.store.scope.installation_id,
                f"{previous.spec.work_id}:{previous.spec.generation + 1}"))
            try:
                successor = self.store.load(successor_id, **self._owner)
            except DelegatedWorkNotFound:
                pass
            else:
                return {"status": "newer_attempt_exists", "work_id": successor.spec.work_id,
                        "guidance": "Gebruik de nieuwere poging voor deze correctie; er is hier niets gewijzigd."}
            previous = self.store.request_stop(previous.spec.work_id, **self._owner, now=self.now())
            if previous.state in {"succeeded", "verified"}:
                return {"status": "already_finished", "work_id": previous.spec.work_id,
                        "guidance": "Deze opdracht is al afgerond. Bespreek het bewaarde resultaat en neem zo nodig een nieuwe vervolgopdracht aan."}
            from eva.adapters.postgres.work_corrections import record_work_correction
            recorded = record_work_correction(self.store._engine, previous.spec,
                instruction=revision["instruction"], request_key=request_key, now=self.now())
            queued = ScheduledOwnerWork(self).schedule(turn, request_key=request_key,
                request={"work_id": previous.spec.work_id, "run_at": recorded.isoformat().replace("+00:00", "Z")},
                resume=True, wait_for_stop=True)
            return {"status": "correction_saved", "work_id": previous.spec.work_id,
                    "schedule": queued, "in_flight_effects_may_complete": True,
                    "guidance": "Correctie bewaard; de huidige poging stopt en één begrensde hervatting wacht daarop. Eerdere acties en resultaten blijven behouden."}
        if revision is not None:
            raise ValueError("correction arguments require their own operation")
        if operation in {"schedule_digital", "schedule_resume", "schedule_list", "schedule_cancel", "schedule_cancel_chain"}:
            if any(value is not None for value in (task, work_id, research, intent_id, feedback, search)):
                raise ValueError("schedule operation has unrelated arguments")
            self.validate_authority(turn)
            scheduler = ScheduledOwnerWork(self)
            if operation in {"schedule_digital", "schedule_resume"}:
                if schedule_id is not None:
                    raise ValueError("new schedule cannot have an existing ID")
                return scheduler.schedule(turn, request_key=request_key, request=schedule,
                                          resume=operation == "schedule_resume")
            if schedule is not None:
                raise ValueError("schedule control does not accept a new task")
            if operation == "schedule_cancel_chain":
                return scheduler.cancel_chain(schedule_id)
            if operation == "schedule_cancel":
                return scheduler.cancel(schedule_id)
            if schedule_id is not None:
                raise ValueError("schedule list does not accept an ID")
            return scheduler.list()
        if schedule is not None or schedule_id is not None:
            raise ValueError("schedule arguments require a schedule operation")
        if search is not None and operation != "search":
            raise ValueError("search arguments require their own operation")
        if operation not in {"stop_related", "related", "start", "research", "digital", "list", "search", "get", "result", "stop", "resume",
                             "initiative_list", "initiative_result", "initiative_stop", "initiative_feedback"}:
            raise ValueError("delegated work operation is invalid")
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 2048:
            raise ValueError("delegated work request key is required")
        if operation in {"initiative_list", "initiative_result", "initiative_stop", "initiative_feedback"}:
            if task is not None or work_id is not None or research is not None:
                raise ValueError("initiative operations do not accept task arguments")
            self.validate_authority(turn)
            from eva.adapters.postgres.owner_initiatives import PostgresOwnerInitiatives
            initiatives = PostgresOwnerInitiatives(self.store._engine,
                tenant_id=turn.tenant_id, installation_id=turn.installation_id,
                owner=self.owner_principal_id, binding=self.principal_binding_id,
                authority_scope=self.store.scope)
            if operation != "initiative_feedback" and feedback is not None:
                raise ValueError("feedback requires its explicit operation")
            if operation == "initiative_list":
                if intent_id is not None:
                    raise ValueError("initiative listing does not accept an intent id")
                return initiatives.page(now=self.now(), limit=8)
            if not isinstance(intent_id, str) or not intent_id:
                raise ValueError("initiative result requires an intent id")
            if operation in {"initiative_stop", "initiative_feedback"}:
                try:
                    return (initiatives.stop(intent_id, now=self.now())
                            if operation == "initiative_stop" else
                            initiatives.feedback(intent_id, feedback, now=self.now(),
                                                 request_key=request_key))
                except LookupError:
                    return {"status": "unavailable", "intent_id": intent_id,
                            "guidance": "Dit initiatief is niet beschikbaar; er is niets gewijzigd."}
            try:
                artifact = initiatives.artifact(intent_id, now=self.now())
            except LookupError:
                return {"contract": "EvaOwnerInitiativeResult/v1", "intent_id": intent_id,
                        "status": "unavailable", "artifact": None,
                        "guidance": "Geen opgeslagen resultaat beschikbaar voor dit initiatief."}
            self.validate_authority(turn)
            return {"contract": "EvaOwnerInitiativeResult/v1", "intent_id": intent_id,
                    "status": "stored", "artifact": artifact,
                    "independently_verified": False}
        if intent_id is not None or feedback is not None:
            raise ValueError("initiative arguments require an initiative operation")
        if operation == "research":
            if self.research_profile is None or task is not None or work_id is not None:
                raise DelegatedWorkConflict("public background research is not composed")
            public = PublicResearchRequest.model_validate(research)
            task = public.question
        elif research is not None:
            raise ValueError("research arguments require their explicit operation")
        if operation == "digital" and self.digital_profile is None:
            raise DelegatedWorkConflict("digital background work is not composed")
        response = {"contract": "EvaDelegatedWorkOwnerResult/v1", "operation": operation}
        if operation == "search":
            if (task is not None or work_id is not None or not isinstance(search, dict)
                    or not {"query"} <= set(search) <= {"query", "before_work_id"}):
                raise ValueError("work search fields differ")
            rows = self.store.list_owned(**self._owner, limit=21, query=search["query"],
                                         before_work_id=search.get("before_work_id"))
            return {**response, "query": search["query"].strip(),
                    "jobs": [self._view(row, include_result=False, turn=turn) for row in rows[:20]],
                    "truncated": len(rows) > 20,
                    "next_before_work_id": rows[19].spec.work_id if len(rows) > 20 else None,
                    "planning": ScheduledOwnerWork(self).list(),
                    "search_scope": "stored_private_titles_tasks_results"}
        if operation == "stop_related":
            if task is not None or research is not None or not isinstance(work_id, str) or not work_id:
                raise ValueError("related stop requires one work ID")
            self.validate_authority(turn)
            from eva.application.related_work import stop_related_work
            result = stop_related_work(work_id,
                inspect=lambda identifier: self.execute("related", turn,
                    work_id=identifier, request_key=request_key),
                stop=lambda identifier: self.execute("stop", turn,
                    work_id=identifier, request_key=request_key),
                cancel_schedule=ScheduledOwnerWork(self).cancel_chain)
            return {**response, **result, "operation": operation}
        if operation == "related":
            if task is not None or not isinstance(work_id, str) or not work_id:
                raise ValueError("related work requires one work ID")
            self.validate_authority(turn)
            from eva.application.related_work import related_work
            scheduled = ScheduledOwnerWork(self)
            planning_limited = False
            def connected(parents):
                nonlocal planning_limited
                rows = {row.spec.work_id: row for row in
                        self.store.list_owned(**self._owner, parent_ids=parents, limit=65)}
                planning = scheduled.related(parents)
                planning_limited = planning_limited or planning["truncated"]
                for identifier in planning["linked_work_ids"]:
                    if identifier in parents or identifier in rows:
                        continue
                    if len(rows) >= 65:
                        planning_limited = True
                        break
                    try:
                        rows[identifier] = self.store.load(identifier, **self._owner)
                    except DelegatedWorkNotFound:
                        planning_limited = True
                return tuple(rows.values())
            result = related_work(work_id,
                load=lambda identifier: self.store.load(identifier, **self._owner),
                children=connected,
                render=lambda row: self._view(row, include_result=False, turn=turn),
                missing=DelegatedWorkNotFound)
            planning = scheduled.related(tuple(job["work_id"] for job in result["jobs"]))
            planning.pop("linked_work_ids")
            result["truncated"] = result["truncated"] or planning_limited or planning["truncated"]
            return {**response, **result, "planning": planning}
        if operation == "list":
            if task is not None or work_id is not None:
                raise ValueError("list does not accept task or work id")
            rows = self.store.list_owned(**self._owner, limit=21, active_first=True)
            return {**response, "jobs": [self._view(row, include_result=False, turn=turn) for row in rows[:20]],
                    "truncated": len(rows) > 20,
                    "planning": ScheduledOwnerWork(self).list()}
        if operation in {"start", "research", "digital"}:
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
            created = admission_time or parse_utc_timestamp(turn.occurred_at)
            builder = (self.digital_profile.build_spec if operation == "digital" else
                       build_unadmitted_delegated_work_spec if operation == "start"
                       else self.research_profile.build_spec)
            digital_source = ({"source": {
                "tenant_id": turn.tenant_id, "installation_id": turn.installation_id,
                "principal_id": turn.principal_id, "conversation_id": turn.conversation_id,
                "message_id": turn.message_id,
                "audience": {"kind": "private_person", "principal_id": turn.principal_id}},
                "source_ingress_sha256": canonical_sha256(PostgresConversationJournal._ingress_data(turn))}
                if operation == "digital" else {})
            spec = builder(
                **({"research": public.to_payload()} if operation == "research" else {}),
                **digital_source,
                work_id="work:eva-chat:" + str(identity),
                episode_id=str(uuid5(NAMESPACE_URL, ":".join((
                    "delegated-private-context-v1", turn.tenant_id, turn.installation_id,
                    turn.principal_id, turn.conversation_id)))),
                generation=1, parent_task_id=prior_work_id or "chat:" + str(identity),
                owner_principal_id=str(self.owner_principal_id),
                principal_binding_id=str(self.principal_binding_id), task=task,
                created_at=created, deadline=created + timedelta(
                    seconds=self.digital_profile.timeout_seconds if operation == "digital" else 300),
            )
            title = " ".join((public.title if operation == "research" else task).split())[:120]
            try:
                previous = self.store.load(spec.work_id, **self._owner)
            except DelegatedWorkNotFound:
                pass
            else:
                # Preserve immutable presentation metadata on legacy replay.
                # Admission still checks the full incoming specification.
                title = previous.title
            admission = self.store.admit(
                spec=spec, title=title,
                summary=("Eva voert de digitale opdracht uit met de toegestane hulpmiddelen."
                         if operation == "digital" else "Een afgeschermde onderzoeker werkt aan de gegeven opdracht."),
                worker_role="Digitale taak" if operation == "digital" else "Onderzoek",
                retention_policy_id="retention:delegated-work:v1",
                retention_until=created + timedelta(days=7), occurred_at=created,
                **self._delivery_arguments(turn),
            )
            return {**response, "created": admission.disposition == "inserted", "work": self._view(admission.work, turn=turn)}
        if task is not None or not isinstance(work_id, str) or not work_id:
            raise ValueError("delegated work operation requires its work id")
        if operation == "stop":
            work = self.store.request_stop(work_id, **self._owner, now=self.now())
            return {**response, "work": self._view(work, turn=turn)}
        previous = self.store.load(work_id, **self._owner)
        if operation in {"get", "result"}:
            return {**response, "work": self._view(previous, turn=turn)}
        created = admission_time or parse_utc_timestamp(turn.occurred_at)
        next_id = "work:eva-resume:" + str(uuid5(
            self.store.scope.installation_id,
            f"{previous.spec.work_id}:{previous.spec.generation + 1}",
        ))
        is_research = isinstance(previous.spec, DelegatedResearchSpec)
        is_digital = isinstance(previous.spec, DelegatedDigitalWorkSpec)
        if is_research and self.research_profile is None:
            raise DelegatedWorkConflict("research resume requires its configured profile")
        if is_digital and self.digital_profile is None:
            raise DelegatedWorkConflict("digital work resume requires its configured profile")
        builder = (self.digital_profile.build_spec if is_digital else
                   self.research_profile.build_spec if is_research else build_unadmitted_delegated_work_spec)
        spec = builder(
            **({"research": previous.spec.research.to_payload()} if is_research else {}),
            **({"source": previous.spec.source.model_dump(mode="json"),
                "source_ingress_sha256": previous.spec.source_ingress_sha256,
                "execution_id": previous.spec.execution_id,
                "grant": previous.spec.grant} if is_digital else {}),
            work_id=next_id, episode_id=previous.spec.episode_id,
            generation=previous.spec.generation + 1, parent_task_id=previous.spec.work_id,
            owner_principal_id=str(self.owner_principal_id),
            principal_binding_id=str(self.principal_binding_id), task=previous.spec.task,
            created_at=created, deadline=created + timedelta(
                seconds=self.digital_profile.timeout_seconds if is_digital else 300),
        )
        admission = self.store.admit(
            spec=spec, title=previous.title, summary=previous.summary,
            worker_role=previous.worker_role, retention_policy_id=previous.retention_policy_id,
            retention_until=max(previous.retention_until, created + timedelta(days=7)),
            occurred_at=created, resumes_work_id=previous.spec.work_id,
            **self._delivery_arguments(turn),
        )
        return {**response, "created": admission.disposition == "inserted",
                "previous": self._view(previous, turn=turn), "work": self._view(admission.work, turn=turn),
                "prior_outcome_unknown": (previous.state in {"call_started", "ambiguous"}
                    or (previous.state == "stopped" and previous.call_id is not None
                        and (previous.usage is None or previous.usage.measurement != "measured"
                             or previous.usage.completed_model_responses != 1))),
                "guidance": "Een nieuwe begrensde poging; de eerdere call en het eerdere verbruik blijven bewaard."}

    def active_work_metadata(self, turn: ConversationTurn) -> dict:
        """Project authorized task metadata without per-task delivery lookups."""
        if not self.available_for(turn):
            raise DelegatedWorkConflict("delegated work requires its private owner scope")
        # As with the normal list operation, the store revalidates the current
        # owner binding and restricts results to private work in this scope.
        rows = self.store.list_owned(**self._owner, limit=21, active_first=True)
        from eva.application.work_context import work_context_metadata
        return work_context_metadata(rows, self.now())

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
            "unbound": "Geen automatische Telegram-terugmelding gekoppeld.",
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

    def _private_delivery_view(self, work, turn):
        delivery = self._delivery_view(work.spec.work_id)
        if turn is None or delivery["state"] != "unbound":
            return delivery
        from eva.adapters.postgres.private_work_return import private_episode
        if (self.available_for(turn)
                and turn.conversation_id == "dashboard:" + str(self.principal_binding_id)
                and work.spec.audience == "private"
                and work.spec.owner_principal_id == str(self.owner_principal_id)
                and work.spec.principal_binding_id == str(self.principal_binding_id)
                and work.spec.episode_id == private_episode(turn)
                and work.retention_until > self.now()
                and work.stop_requested_at is None
                and work.state in {"admitted", "call_started", "succeeded", "verified"}):
            return {"state": "private_route_available", "description":
                    "Deze opdracht hoort bij de terugmeldroute van deze website. "
                    "Een afgerond resultaat kan hier worden opgehaald zolang de pagina open is. "
                    "Dit bevestigt geen aflevering."}
        return delivery

    def _view(self, work: StoredDelegatedWork, *, include_result=True, turn=None) -> dict:
        result = None
        if work.result is not None and work.state in {"succeeded", "verified"}:
            result = {"output": work.result.output, "output_sha256": work.result.output_sha256,
                      "completed_at": work.result.completed_at, "verified": work.state == "verified"}
        corrections = None
        if include_result and isinstance(work.spec, DelegatedDigitalWorkSpec):
            from eva.adapters.postgres.work_corrections import work_corrections
            corrections = work_corrections(self.store._engine, work.spec)
        progress = None
        if include_result and isinstance(work.spec, DelegatedDigitalWorkSpec):
            from eva.adapters.postgres.background_work_progress import load_work_progress
            progress = load_work_progress(self.store._engine, work.spec)
        return {"work_id": work.spec.work_id, "generation": work.spec.generation,
                "parent_work_id": work.spec.parent_task_id if work.spec.parent_task_id.startswith("work:") else None,
                "state": work.state, "title": work.title,
                **({"task": work.spec.task} if include_result else {"task_preview": work.spec.task[:240]}),
                "created_at": work.spec.created_at, "deadline": work.spec.deadline,
                "call_id": work.call_id, "stop_requested": work.stop_requested_at is not None,
                "usage": None if work.usage is None else work.usage.to_payload(),
                "error_code": work.error_code, "error_fingerprint": work.error_fingerprint,
                "failure_description": work_failure_description(work.error_code),
                "result_available": result is not None,
                "delivery": self._private_delivery_view(work, turn),
                **({"result": result, "progress": progress, "corrections": corrections} if include_result else {}),
                "scope": ("private_digital_worker" if isinstance(work.spec, DelegatedDigitalWorkSpec) else
                          "private_public_research_worker" if isinstance(work.spec, DelegatedResearchSpec)
                          else "private_tool_free_worker"), "new_model_run_on_resume": True}


__all__ = ["DelegatedWorkOwnerService"]
