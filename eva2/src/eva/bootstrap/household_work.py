"""Explicit server composition for household public work; no default activation.

The authenticated connector selects the device and core-issued session before
model arguments reach this facade. The sponsor never becomes the voice actor.
"""

from datetime import timedelta
from eva.application.work_failure_notice import delivery_outcome
from eva.adapters.postgres.work_failure_notice import failure_notice_cutoff, deliverable_states

import hmac
from uuid import NAMESPACE_URL, UUID, uuid5
import sqlalchemy as sa
from eva.adapters.postgres.delegated_work import (
    PostgresDelegatedWorkStore,
    DelegatedWorkNotFound,
)
from eva.adapters.postgres.household_work_sessions import HouseholdWorkSessions
from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
from eva.adapters.postgres.delegated_work_actions import PostgresDelegatedWorkActions
from eva.adapters.postgres.operational_timeline import (
    external_timeline_scope,
    operational_events,
    insert_initial_timeline_thread,
    transition_timeline_thread,
    operational_checkpoints,
)
from eva.application.work_result_presentation import work_failure_description
from eva.application.conversation import ConversationTurn
from eva.application.memory_audience import HouseholdConsentReference, MemoryAudience
from eva.contracts.canonical_json import canonical_sha256
from eva.contracts.delegated_work import HouseholdWorkOrigin, PublicResearchRequest
from eva.contracts.timestamps import parse_utc_timestamp


class HouseholdWorkServer:
    def __init__(self, engine, scope, authority):
        from eva.bootstrap.delegated_research import HouseholdResearchProfile

        if not isinstance(authority.profile, HouseholdResearchProfile):
            raise ValueError("household work requires its distinct worker release")
        authority.validate_current()
        self.conversation_scope = external_timeline_scope(
            authority.policy.tenant_id, authority.policy.installation_id,
            "household conversation scope required",
        )
        self.authority = authority
        self.store = PostgresDelegatedWorkStore(
            engine,
            scope,
            household_authorizer=authority.validate_spec,
            household_clock=authority.clock,
        )
        # Readiness must prove the sponsor exists in this storage scope; valid
        # voice consent alone cannot authorize durable work in another scope.
        self.store.validate_admission_authority(
            owner_principal_id=authority.policy.owner_principal_id,
            principal_binding_id=authority.policy.principal_binding_id,
        )
        self.sessions = HouseholdWorkSessions(self.store, authority)

    def authenticate(self, token, satellite_id, device_id):
        self.authority.validate_device(satellite_id, device_id)
        if not isinstance(token, str) or not hmac.compare_digest(
            token, self.authority.voice.token
        ):
            raise ValueError("household connector authentication failed")

    def open_session(self, *, token, satellite_id, device_id):
        self.authenticate(token, satellite_id, device_id)
        return self.sessions.open(satellite_id, device_id)

    def session(self, *, token, satellite_id, device_id, session_id):
        self.authenticate(token, satellite_id, device_id)
        binding = self.sessions.read(session_id, satellite_id, device_id)
        return HouseholdWorkService(self, binding, token)


class HouseholdWorkService:
    def __init__(self, server, binding, token):
        self.server, self.store, self.binding, self._token = (
            server,
            server.store,
            binding,
            token,
        )
        self.research_profile = server.authority.profile
        self.digital_profile = getattr(server.authority, "digital_profile", None)

    @property
    def _owner(self):
        policy = self.server.authority.policy
        return dict(
            owner_principal_id=policy.owner_principal_id,
            principal_binding_id=policy.principal_binding_id,
        )

    def current(self, operation="read"):
        b = self.binding
        self.server.authenticate(self._token, b["satellite_id"], b["device_id"])
        if (
            self.server.sessions.read(
                b["session_id"], b["satellite_id"], b["device_id"], operation=operation
            )
            != b
        ):
            raise ValueError("household session binding changed")
        self.store.validate_admission_authority(**self._owner)

    def _make_turn(self, request_id, text):
        self.current()
        if str(UUID(request_id)) != request_id:
            raise ValueError("household request identifier invalid")
        b = self.binding
        ref = HouseholdConsentReference(
            b["actor_id"], b["household_id"], b["consent_operation_id"]
        )
        turn = ConversationTurn(
            b["tenant_id"],
            b["installation_id"],
            b["actor_id"],
            b["conversation_id"],
            b["conversation_id"] + ":request:" + request_id,
            text,
            self.server.authority.clock().strftime("%Y-%m-%dT%H:%M:%SZ"),
            MemoryAudience.household(
                principal_id=b["actor_id"],
                household_id=b["household_id"],
                consent_ref=ref,
            ),
        )
        return turn

    def turn(self, request_id, text):
        turn = self._make_turn(request_id, text)
        return PostgresConversationJournal(
            self.store._engine, self.server.conversation_scope
        ).admit_household_ingress(turn, validate_current=self.validate_authority)

    def control_turn(self):
        """Bound transport context, not a user message or a new ingress event."""
        return self._make_turn(str(uuid5(NAMESPACE_URL,
            "household-work-control:" + self.binding["session_id"])), "Werkresultaat")

    def available_for(self, turn):
        b = self.binding
        return (
            turn.tenant_id == b["tenant_id"]
            and turn.installation_id == b["installation_id"]
            and turn.principal_id == b["actor_id"]
            and turn.audience.kind == "household"
            and turn.audience.household_id == b["household_id"]
            and turn.audience.household_consent_ref.operation_id
            == b["consent_operation_id"]
            and turn.conversation_id == b["conversation_id"]
            and turn.message_id.startswith(b["conversation_id"] + ":request:")
            and turn.project_id is None
            and turn.observation_binding is None
        )

    def validate_authority(self, turn):
        self.current()
        if not self.available_for(turn):
            raise ValueError("household turn differs from its originating session")

    def origin(self, turn):
        p = self.server.authority.policy
        return HouseholdWorkOrigin(
            contract="HouseholdWorkOrigin/v1",
            schema_version=1,
            **self.binding,
            household_policy_id=p.household_policy_id,
            research_policy_id=p.policy_id,
            message_id=turn.message_id,
            received_at=turn.occurred_at,
        )

    def _load(self, work_id, origin, *, include_native=True):
        self.current()
        try:
            work = self.store.load(work_id, **self._owner, household_origin=origin)
        except DelegatedWorkNotFound:
            if not include_native:
                raise
            rows = self._native_work(origin, work_id=work_id, limit=1)
            if not rows:
                raise
            work = rows[0]
        if self.server.authority.clock() >= work.retention_until:
            raise DelegatedWorkNotFound("household result expired")
        return work

    def _native_work(self, current_origin, *, work_id=None, parent_ids=None, limit=21):
        """Load stored origins through unchanged exact store authorization."""
        from eva.adapters.postgres.current_catalog import delegated_work_runs as runs
        self.current()
        bindings = {item["session_id"]: item for item in
                    self.server.sessions.continuity_bindings(self.binding)}
        now = self.server.authority.clock()
        with self.store._engine.begin() as connection:
            self.store._scope(connection)
            from eva.adapters.postgres.household_work_sessions import verify_origin
            verify_origin(connection, self.store.scope, current_origin, require_open=True, now=now)
            rows = connection.execute(sa.select(runs.c.work_id, runs.c.spec_payload).where(
                runs.c.tenant_id == self.store.scope.tenant_id,
                runs.c.installation_id == self.store.scope.installation_id,
                runs.c.owner_principal_id == self._owner["owner_principal_id"],
                runs.c.principal_binding_id == self._owner["principal_binding_id"],
                runs.c.spec_payload["audience"].astext == "household",
                runs.c.spec_payload["origin"]["session_id"].astext.in_(tuple(bindings)),
                runs.c.retention_until > now,
                sa.true() if work_id is None else runs.c.work_id == work_id,
                sa.true() if parent_ids is None else runs.c.parent_task_id.in_(parent_ids),
            ).order_by(sa.case((sa.and_(runs.c.state.in_(("admitted", "call_started")),
                runs.c.stop_requested_at.is_(None)), 0), else_=1),
                runs.c.created_at.desc(), runs.c.work_id.desc()).limit(limit)).all()
        result = []
        for row in rows:
            origin = HouseholdWorkOrigin.model_validate(row.spec_payload["origin"])
            if origin.destination() != bindings.get(origin.session_id):
                raise ValueError("native work origin differs from its original session")
            result.append(self.store.load(row.work_id, **self._owner, household_origin=origin))
        self.current()
        return tuple(result)

    def view(self, work):
        self.current()
        return dict(
            work_id=work.spec.work_id,
            title=work.title,
            question=work.spec.task,
            generation=work.spec.generation,
            parent_work_id=work.spec.parent_task_id if work.spec.parent_task_id.startswith("work:") else None,
            state=work.state,
            error_code=work.error_code,
            failure_description=work_failure_description(work.error_code),
            stop_requested=work.stop_requested_at is not None,
            result=None if work.result is None else work.result.output,
            result_sha256=None if work.result is None else work.result.output_sha256,
            usage=None if work.usage is None else work.usage.to_payload(),
        )

    def active_work_metadata(self, turn):
        """Project work from authorized current-device native sessions."""
        self.validate_authority(turn)
        from eva.application.work_context import work_context_metadata
        now = self.server.authority.clock()
        rows = self._native_work(self.origin(turn), limit=21)
        return work_context_metadata(rows, now)

    def execute(
        self, operation, turn, *, request_key, research=None, work_id=None, task=None, prior_work_id=None, revision=None
    ):
        self.validate_authority(turn)
        if (task is not None and operation != "digital") or operation not in {
            "digital",
            "revise_digital",
            "research",
            "list",
            "related",
            "stop_related",
            "get",
            "result",
            "stop",
            "resume",
        }:
            raise ValueError("household work operation unavailable")
        if not isinstance(request_key, str) or not 1 <= len(request_key) <= 2048:
            raise ValueError("household action key required")
        turn = PostgresConversationJournal(
            self.store._engine, self.server.conversation_scope
        ).admit_household_ingress(turn, validate_current=self.validate_authority)
        origin = self.origin(turn)
        if operation == "revise_digital":
            if (any(value is not None for value in (task, research, work_id, prior_work_id))
                    or not isinstance(revision, dict) or set(revision) != {"work_id", "instruction"}
                    or not isinstance(revision["work_id"], str) or not revision["work_id"]
                    or not isinstance(revision["instruction"], str)
                    or not 1 <= len(revision["instruction"].strip()) <= 2000):
                raise ValueError("household correction requires a work ID and instruction")
            from eva.bootstrap.household_work_corrections import revise_household_work
            return revise_household_work(self, turn, request_key, revision)
        if revision is not None:
            raise ValueError("correction arguments require their own operation")
        if operation == "digital":
            if research is not None or work_id is not None or prior_work_id is not None:
                raise ValueError("household digital admission cannot select another destination")
            from eva.bootstrap.household_digital_work import admit_household_digital
            return admit_household_digital(self, turn, request_key, task)
        if operation == "stop_related":
            if research is not None or prior_work_id is not None or not isinstance(work_id, str) or not work_id:
                raise ValueError("related stop requires one work ID")
            from eva.application.related_work import stop_related_work
            from eva.bootstrap.household_work_corrections import HouseholdScheduledWork
            return stop_related_work(work_id,
                inspect=lambda identifier: self.execute("related", turn,
                    work_id=identifier, request_key=request_key),
                stop=lambda identifier: self.execute("stop", turn,
                    work_id=identifier, request_key=request_key),
                cancel_schedule=(None if self.digital_profile is None else
                    HouseholdScheduledWork(self.server).cancel_chain))
        if operation == "related":
            if research is not None or prior_work_id is not None or not isinstance(work_id, str) or not work_id:
                raise ValueError("related household work requires one work ID")
            from eva.application.related_work import related_work
            result = related_work(work_id,
                load=lambda identifier: self._load(identifier, origin),
                children=lambda parents: self._native_work(origin, parent_ids=parents, limit=65),
                render=lambda row: {key: value for key, value in self.view(row).items()
                                    if key not in {"result", "question", "usage"}},
                missing=DelegatedWorkNotFound)
            if self.digital_profile is not None and result.get("jobs"):
                from eva.bootstrap.household_work_corrections import HouseholdScheduledWork
                planning = HouseholdScheduledWork(self.server).related(
                    tuple(job["work_id"] for job in result["jobs"]))
                planning.pop("linked_work_ids")
                result["planning"] = planning
                result["truncated"] = result["truncated"] or planning["truncated"]
            return result
        if operation == "list":
            if research is not None or work_id is not None or prior_work_id is not None:
                raise ValueError("household listing cannot select another destination")
            rows = self._native_work(self.origin(turn), limit=21)
            # Listing supplies selection context, not all completed research text.
            return {"jobs": [{key: view[key] for key in
                    ("work_id", "title", "question", "state", "generation", "stop_requested")}
                    for view in (self.view(work) for work in rows[:20])], "truncated": len(rows) > 20}
        if prior_work_id is not None:
            if operation != "research" or not isinstance(prior_work_id, str) or not 1 <= len(prior_work_id) <= 240:
                raise ValueError("prior work is only valid for research follow-up")
            prior = self._load(prior_work_id, origin)
            if (prior.state in {"admitted", "call_started"}
                    and prior.stop_requested_at is None):
                raise ValueError("research follow-up requires a finished attempt or a recorded stop")
        created = parse_utc_timestamp(turn.occurred_at)
        previous = None
        if operation == "research":
            if work_id is not None:
                raise ValueError("household admission cannot choose a work destination")
            public = PublicResearchRequest.model_validate(research)
            key = canonical_sha256(
                dict(origin=origin.to_payload(), request_key=request_key)
            )
            work_id = "work:household:" + str(uuid5(NAMESPACE_URL, key))
            generation, parent = 1, prior_work_id or "household:" + key
        else:
            if research is not None or not isinstance(work_id, str):
                raise ValueError("household work identity required")
            previous = self._load(work_id, origin)
            if operation in {"get", "result"}:
                return {"work": self.view(previous)}
            if operation == "stop":
                if self.digital_profile is not None:
                    from eva.bootstrap.household_work_corrections import cancel_pending_household_resumes
                    cancel_pending_household_resumes(self.server, previous)
                return {
                    "work": self.view(
                        self.store.request_stop(
                            work_id,
                            **self._owner,
                            household_origin=previous.spec.origin,
                            now=self.server.authority.clock(),
                        )
                    )
                }
            from eva.contracts.delegated_digital_work import DelegatedHouseholdDigitalWorkSpec
            if isinstance(previous.spec, DelegatedHouseholdDigitalWorkSpec):
                from eva.bootstrap.household_digital_work import admit_household_digital
                return admit_household_digital(self, turn, request_key, None, previous=previous)
            origin = previous.spec.origin
            public = previous.spec.research
            generation, parent = previous.spec.generation + 1, previous.spec.work_id
            work_id = "work:eva-resume:" + str(
                uuid5(self.store.scope.installation_id, f"{parent}:{generation}")
            )
        expiry = parse_utc_timestamp(self.server.authority.policy.expires_at)
        spec = self.research_profile.build_spec(
            research=public.to_payload(),
            origin=origin.to_payload(),
            destination_sha256=canonical_sha256(
                dict(
                    origin=origin.destination(), work_id=work_id, generation=generation
                )
            ),
            work_id=work_id,
            generation=generation,
            parent_task_id=parent,
            episode_id=str(
                uuid5(
                    NAMESPACE_URL,
                    "household-public-research:"
                    + canonical_sha256(origin.destination()),
                )
            ),
            owner_principal_id=str(self._owner["owner_principal_id"]),
            principal_binding_id=str(self._owner["principal_binding_id"]),
            task=public.question,
            created_at=created,
            deadline=min(created + timedelta(minutes=5), expiry),
        )
        self.validate_authority(turn)
        title = previous.title if previous is not None else " ".join(public.title.split())[:120]
        try:
            saved = self.store.load(work_id, **self._owner, household_origin=origin)
        except DelegatedWorkNotFound:
            pass
        else:
            # Replaying an older admission must keep its immutable stored title.
            title = saved.title
        admitted = self.store.admit(
            spec=spec,
            title=title,
            summary="Publiek onderzoek voor de oorspronkelijke huishoudsessie.",
            worker_role="Onderzoek",
            retention_policy_id="retention:household-public-work:v1",
            retention_until=min(created + timedelta(days=7), expiry),
            occurred_at=created,
            **({} if previous is None else {"resumes_work_id": previous.spec.work_id}),
        )
        return dict(
            created=admitted.disposition == "inserted",
            work=self.view(admitted.work),
            prior_outcome_unknown=previous is not None and previous.call_id is not None,
        )

    def poll(self, turn):
        """Select unsent results in this exact session, including native busy deferrals."""
        self.validate_authority(turn)
        from eva.adapters.postgres.current_catalog import delegated_work_runs as runs

        with self.store._engine.begin() as c:
            self.store._scope(c)
            claimed = sa.exists(
                sa.select(1).select_from(operational_events.outerjoin(operational_checkpoints,
                    (operational_events.c.tenant_id == operational_checkpoints.c.tenant_id)
                    & (operational_events.c.installation_id == operational_checkpoints.c.installation_id)
                    & (operational_events.c.thread_id == operational_checkpoints.c.thread_id))).where(
                    sa.or_(operational_checkpoints.c.phase.is_(None), operational_checkpoints.c.phase != "deferred"),
                    operational_events.c.tenant_id == runs.c.tenant_id,
                    operational_events.c.installation_id == runs.c.installation_id,
                    operational_events.c.event_type == "HOUSEHOLD_WORK_RESULT_CLAIMED",
                    operational_events.c.payload["data"]["work_id"].astext
                    == runs.c.work_id,
                )
            )
            cutoff = failure_notice_cutoff(c, self.store, self.server.authority.clock())
            candidate = c.scalar(
                sa.select(runs.c.work_id)
                .where(
                    runs.c.tenant_id == self.store.scope.tenant_id,
                    runs.c.installation_id == self.store.scope.installation_id,
                    runs.c.spec_payload["audience"].astext == "household",
                    runs.c.spec_payload["origin"]["session_id"].astext
                    == self.binding["session_id"],
                    deliverable_states(runs, cutoff),
                    runs.c.stop_requested_at.is_(None),
                    runs.c.retention_until > self.server.authority.clock(),
                    ~claimed,
                )
                .order_by(runs.c.completed_at, runs.c.work_id)
                .limit(1)
            )
        return (
            None
            if candidate is None
            else self.result_delivery(turn, "claim", work_id=candidate)
        )

    def result_delivery(
        self, turn, operation, *, work_id, result_sha256=None, disposition=None
    ):
        """One durable claim; uncertain/interrupted output is never replayed."""
        self.validate_authority(turn)
        work = self._load(work_id, self.origin(turn), include_native=False)
        result = delivery_outcome(work)
        if result is None:
            return None
        binding = dict(
            work_id=work_id,
            generation=work.spec.generation,
            result_sha256=result.output_sha256,
            origin_message_id=work.spec.origin.message_id,
            session_generation=work.spec.origin.session_generation,
            destination_sha256=work.spec.destination_sha256,
        )
        thread = "household-work-delivery:" + canonical_sha256(binding)
        with self.store._engine.begin() as c:
            self.store._scope(c)
            c.execute(
                sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key,0))"),
                {"key": thread},
            )
            self.validate_authority(turn)
            row = (
                c.execute(
                    sa.select(operational_checkpoints)
                    .where(
                        operational_checkpoints.c.tenant_id
                        == self.store.scope.tenant_id,
                        operational_checkpoints.c.installation_id
                        == self.store.scope.installation_id,
                        operational_checkpoints.c.thread_id == thread,
                    )
                    .with_for_update()
                )
                .mappings()
                .one_or_none()
            )
            if operation == "claim" and row is not None and row["phase"] != "deferred":
                return None
            if operation == "claim" and row is None:
                insert_initial_timeline_thread(
                    c,
                    self.store.scope,
                    thread_id=thread,
                    checkpoint_id=thread + ":claimed",
                    occurred_at=self.server.authority.clock(),
                    event_type="HOUSEHOLD_WORK_RESULT_CLAIMED",
                    phase="claimed",
                    status="active",
                    data=binding,
                )
                return {**binding, "result": result.output}
            if row is None or (operation != "claim" and result_sha256 != result.output_sha256):
                raise ValueError("household result claim absent or stale")
            first = (
                c.execute(
                    sa.select(operational_events).where(
                        operational_events.c.tenant_id == self.store.scope.tenant_id,
                        operational_events.c.installation_id
                        == self.store.scope.installation_id,
                        operational_events.c.thread_id == thread,
                        operational_events.c.sequence == 1,
                    )
                )
                .mappings()
                .one()
            )
            if first["payload"]["data"] != binding or bytes(
                first["payload_sha256"]
            ).hex() != canonical_sha256(first["payload"]):
                raise ValueError("household result claim corrupt")
            if operation in {"claim", "defer"}:
                # Only the native adapter may defer before any announce request.
                # Revisions make repeated busy cycles distinct durable transitions.
                expected_phase = "deferred" if operation == "claim" else "claimed"
                if row["phase"] != expected_phase:
                    raise ValueError("household result cannot be deferred or reclaimed")
                phase = "claimed" if operation == "claim" else "deferred"
                transition_timeline_thread(c, self.store.scope, thread_id=thread,
                    expected_revision=row["revision"],
                    checkpoint_id=thread + ":" + phase + ":" + str(row["revision"] + 1),
                    occurred_at=self.server.authority.clock(),
                    event_type="HOUSEHOLD_WORK_RESULT_RECLAIMED" if operation == "claim"
                        else "HOUSEHOLD_WORK_RESULT_DEFERRED",
                    phase=phase, status="active", data=binding)
                return {**binding, "result": result.output} if operation == "claim" else binding
            if operation == "authorize":
                return binding
            if operation != "ack" or disposition not in {
                "offered",
                "completed",
                "interrupted",
                "ambiguous",
            }:
                raise ValueError("household delivery acknowledgement invalid")
            if row["phase"] == disposition:
                return binding
            if row["phase"] not in {"claimed", "offered"}:
                raise ValueError("household delivery already terminal")
            transition_timeline_thread(
                c,
                self.store.scope,
                thread_id=thread,
                expected_revision=row["revision"],
                checkpoint_id=thread + ":" + disposition,
                occurred_at=self.server.authority.clock(),
                event_type="HOUSEHOLD_WORK_RESULT_ACKNOWLEDGED",
                phase=disposition,
                status="active" if disposition == "offered" else "completed",
                data=binding,
            )
            return binding


class HouseholdWorkActions(PostgresDelegatedWorkActions):
    @property
    def action_names(self):
        return (() if self._service.digital_profile is None else ("start_digital_work", "revise_digital_work")) + (
            "start_background_research",
            "list_delegated_work",
            "get_related_work",
            "stop_related_work",
            "get_delegated_work",
            "stop_delegated_work",
            "resume_delegated_work",
        )

    @staticmethod
    def conversation_tools(**kwargs):
        allowed = {
            "start_digital_work",
            "revise_digital_work",
            "start_background_research",
            "list_delegated_work",
            "get_related_work",
            "stop_related_work",
            "get_delegated_work",
            "stop_delegated_work",
            "resume_delegated_work",
        }
        from dataclasses import replace
        return tuple(
            replace(tool, description="Bekijk de toegankelijke taken binnen de huidige persoons- "
                    "of nog geldige openbare spraaksessies op hetzelfde huisapparaat, met titel, work_id en status.")
            if tool.name == "list_delegated_work" else tool
            for tool in PostgresDelegatedWorkActions.conversation_tools(
                include_research=True, include_digital=True
            )
            if tool.name in allowed
        )

    def _validate_saved(self, turn, receipt):
        if receipt is not None:
            result = receipt.output.get("operation_result", {})
            self._service.validate_authority(turn)
            saved_work = [result.get(field) for field in ("work", "previous")]
            if receipt.call.name == "revise_digital_work" and result.get("work_id") is not None:
                saved_work.append({"work_id": result["work_id"]})
            saved_work.extend(result.get("jobs", ()))
            for work in saved_work:
                if work is not None:
                    self._service._load(work["work_id"], self._service.origin(turn))
        return receipt

    def pending(self, turn, *, step_index=0):
        return self._validate_saved(turn, super().pending(turn, step_index=step_index))

    def execute(self, turn, call, *, step_index=0):
        return self._validate_saved(
            turn, super().execute(turn, call, step_index=step_index)
        )
