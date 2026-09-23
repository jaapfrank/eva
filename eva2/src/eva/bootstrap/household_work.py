"""Explicit server composition for household public work; no default activation.

The authenticated connector selects the device and core-issued session before
model arguments reach this facade. The sponsor never becomes the voice actor.
"""

from datetime import timedelta
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

    def _load(self, work_id, origin):
        self.current()
        work = self.store.load(work_id, **self._owner, household_origin=origin)
        if self.server.authority.clock() >= work.retention_until:
            raise DelegatedWorkNotFound("household result expired")
        return work

    def view(self, work):
        self.current()
        return dict(
            work_id=work.spec.work_id,
            title=work.title,
            question=work.spec.research.question,
            generation=work.spec.generation,
            state=work.state,
            stop_requested=work.stop_requested_at is not None,
            result=None if work.result is None else work.result.output,
            result_sha256=None if work.result is None else work.result.output_sha256,
            usage=None if work.usage is None else work.usage.to_payload(),
        )

    def execute(
        self, operation, turn, *, request_key, research=None, work_id=None, task=None
    ):
        self.validate_authority(turn)
        if task is not None or operation not in {
            "research",
            "list",
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
        if operation == "list":
            if research is not None or work_id is not None:
                raise ValueError("household listing cannot select another destination")
            rows = self.store.list_household(**self._owner, household_origin=origin,
                now=self.server.authority.clock(), limit=21)
            # Listing supplies selection context, not all completed research text.
            return {"jobs": [{key: view[key] for key in
                    ("work_id", "title", "question", "state", "generation", "stop_requested")}
                    for view in (self.view(work) for work in rows[:20])], "truncated": len(rows) > 20}
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
            generation, parent = 1, "household:" + key
        else:
            if research is not None or not isinstance(work_id, str):
                raise ValueError("household work identity required")
            previous = self._load(work_id, origin)
            if operation in {"get", "result"}:
                return {"work": self.view(previous)}
            if operation == "stop":
                return {
                    "work": self.view(
                        self.store.request_stop(
                            work_id,
                            **self._owner,
                            household_origin=origin,
                            now=self.server.authority.clock(),
                        )
                    )
                }
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
        """Select only an unclaimed result in this exact session, independent of owner lists."""
        self.validate_authority(turn)
        from eva.adapters.postgres.current_catalog import delegated_work_runs as runs

        with self.store._engine.begin() as c:
            self.store._scope(c)
            claimed = sa.exists(
                sa.select(1).where(
                    operational_events.c.tenant_id == runs.c.tenant_id,
                    operational_events.c.installation_id == runs.c.installation_id,
                    operational_events.c.event_type == "HOUSEHOLD_WORK_RESULT_CLAIMED",
                    operational_events.c.payload["data"]["work_id"].astext
                    == runs.c.work_id,
                )
            )
            candidate = c.scalar(
                sa.select(runs.c.work_id)
                .where(
                    runs.c.tenant_id == self.store.scope.tenant_id,
                    runs.c.installation_id == self.store.scope.installation_id,
                    runs.c.spec_payload["audience"].astext == "household",
                    runs.c.spec_payload["origin"]["session_id"].astext
                    == self.binding["session_id"],
                    runs.c.state.in_(["succeeded", "verified"]),
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
        work = self._load(work_id, self.origin(turn))
        if (
            work.state not in {"succeeded", "verified"}
            or work.result is None
            or work.stop_requested_at is not None
        ):
            return None
        binding = dict(
            work_id=work_id,
            generation=work.spec.generation,
            result_sha256=work.result.output_sha256,
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
            if operation == "claim":
                if row is not None:
                    return None
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
                return {**binding, "result": work.result.output}
            if row is None or result_sha256 != work.result.output_sha256:
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
        return (
            "start_background_research",
            "list_delegated_work",
            "get_delegated_work",
            "stop_delegated_work",
            "resume_delegated_work",
        )

    @staticmethod
    def conversation_tools(**kwargs):
        allowed = {
            "start_background_research",
            "list_delegated_work",
            "get_delegated_work",
            "stop_delegated_work",
            "resume_delegated_work",
        }
        from dataclasses import replace
        return tuple(
            replace(tool, description="Bekijk de toegankelijke taken binnen de huidige persoons- "
                    "of huishoudsessiegrenzen, met hun titel, work_id en status.")
            if tool.name == "list_delegated_work" else tool
            for tool in PostgresDelegatedWorkActions.conversation_tools(
                include_research=True
            )
            if tool.name in allowed
        )

    def _validate_saved(self, turn, receipt):
        if receipt is not None:
            result = receipt.output.get("operation_result", {})
            self._service.validate_authority(turn)
            saved_work = [result.get(field) for field in ("work", "previous")]
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
