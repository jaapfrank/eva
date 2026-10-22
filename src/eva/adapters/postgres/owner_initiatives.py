"""Owner control of existing personal-work timelines; no second task store."""
from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID
import re
import hashlib
import sqlalchemy as sa

from eva.contracts.canonical_json import canonical_sha256
from eva.adapters.postgres.current_catalog import metadata
from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline, TimelineScope, external_timeline_scope,
    operational_checkpoints, operational_events, transition_timeline_thread,
)


class PostgresOwnerInitiatives:
    def __init__(self, engine: sa.Engine, *, tenant_id: str, installation_id: str,
                 owner: UUID, binding: UUID, authority_scope: TimelineScope) -> None:
        self._engine, self._owner, self._binding = engine, owner, binding
        self._tenant_id, self._installation_id = tenant_id, installation_id
        self._authority = authority_scope
        self._scope = external_timeline_scope(tenant_id, installation_id, "initiative scope required")

    def _authorize(self, conn, now):
        bindings = metadata.tables["eva_core.principal_bindings"]
        people = metadata.tables["eva_core.principals"]
        PostgresOperationalTimeline(self._engine, self._authority)._scope(conn)
        row = conn.execute(sa.select(bindings.c.principal_id).join(people, sa.and_(
            people.c.tenant_id == bindings.c.tenant_id,
            people.c.principal_id == bindings.c.principal_id,
        )).where(
            bindings.c.tenant_id == self._authority.tenant_id,
            bindings.c.principal_binding_id == self._binding,
            bindings.c.principal_id == self._owner,
            bindings.c.status == "verified", people.c.status == "active",
            bindings.c.retention_until > now, people.c.retention_until > now,
            bindings.c.verified_at <= now,
        ).with_for_update(read=True)).one_or_none()
        if row is None:
            raise RuntimeError("initiative owner binding is not current")
        PostgresOperationalTimeline(self._engine, self._scope)._scope(conn)

    def _query(self):
        c, e = operational_checkpoints.c, operational_events.c
        # Select the exact authorized adoption set before joining checkpoints.
        # Otherwise PostgreSQL can probe the event index once for every unrelated
        # checkpoint, even though only a handful of owner initiatives match.
        adoptions = sa.select(e.tenant_id, e.installation_id, e.thread_id, e.payload).where(
            e.tenant_id == self._scope.tenant_id,
            e.installation_id == self._scope.installation_id,
            e.event_type == "PERSONAL_INTENT_ADOPTED",
            e.payload["data"]["principal_id"].astext == str(self._owner),
            e.payload["data"]["origin"].astext == "eva_initiative",
        ).cte("owner_adoptions").prefix_with("MATERIALIZED")
        return sa.select(operational_checkpoints, adoptions.c.payload.label("adoption")).join(
            adoptions, sa.and_(c.tenant_id == adoptions.c.tenant_id,
                c.installation_id == adoptions.c.installation_id,
                c.thread_id == adoptions.c.thread_id),
        ).where(c.tenant_id == self._scope.tenant_id,
                c.installation_id == self._scope.installation_id)

    @staticmethod
    def _intent_id(value):
        if not isinstance(value, str) or not re.fullmatch(r"personal-interest:[0-9a-f]{64}", value):
            raise ValueError("invalid initiative ID")
        return value

    def page(self, *, now: datetime, limit: int = 64, include_result_preview: bool = False) -> dict:
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError("initiative page limit must be between 1 and 64")
        with self._engine.begin() as conn:
            self._authorize(conn, now)
            rows = conn.execute(self._query().order_by(
                operational_checkpoints.c.updated_at.desc(), operational_checkpoints.c.thread_id
            ).limit(limit + 1)).all()
            latest_review = self._latest_review(conn)
            household_review = self._latest_review(conn, household=True)
            review_lanes = {
                "private": {"latest_review": latest_review,
                            "unresolved_review": self._latest_review(conn, unresolved=True)},
                "household": {"latest_review": household_review,
                              "unresolved_review": self._latest_review(conn, household=True, unresolved=True)},
            }
            cards = []
            for row in rows[:limit]:
                data = row.adoption["data"]
                phase = row.phase
                status = ("stopped" if phase == "personal_stopped" else
                          "done" if phase in {"release_ready", "initiative_completed"} else
                          "attention" if row.status in ("failed", "needs_help") else "working")
                artifact = self._artifact(conn, row.thread_id)
                cards.append({"intent_id": data["intent_id"], "title": data["title"],
                    "motivation": data["motivation"], "question": data["next_exploration"],
                    "status": status, "phase": phase,
                    "updated_at": row.updated_at.isoformat(),
                    "can_stop": status not in ("stopped", "done"),
                    "has_artifact": artifact is not None,
                    "feedback": self._feedback(conn, row.thread_id)})
                if include_result_preview and artifact is not None:
                    cards[-1]["result_preview"] = artifact["content"][:500]
                    cards[-1]["result_truncated"] = len(artifact["content"]) > 500
        return {"contract": "EvaOwnerInitiatives/v1", "cards": cards, "truncated": len(rows) > limit,
                "latest_review": latest_review, "review_lanes": review_lanes,
                "checked_at": now.isoformat()}

    def recent_private_contacts(self, *, now: datetime, limit: int = 3) -> list[dict]:
        """Actual delivered self-expression, scoped to this verified private owner."""
        if type(limit) is not int or not 1 <= limit <= 3:
            raise ValueError("private contact limit must be between 1 and 3")
        prefix = "sensory:initiative-review:" + hashlib.sha256(str(self._owner).encode()).hexdigest()[:24] + ":"
        e = operational_events.c
        with self._engine.begin() as conn:
            self._authorize(conn, now)
            rows = conn.execute(sa.select(e.thread_id).where(
                e.tenant_id == self._scope.tenant_id, e.installation_id == self._scope.installation_id,
                e.thread_id.startswith(prefix), e.event_type == "PROACTIVE_EXPRESSION_DELIVERED",
                e.occurred_at <= now, e.occurred_at >= now - timedelta(days=7),
                e.payload["data"]["channel"].astext == "telegram",
            ).order_by(e.occurred_at.desc(), e.thread_id).limit(limit)).all()
            timeline = PostgresOperationalTimeline(self._engine, self._scope)
            contacts = []
            for row in rows:
                if not row.thread_id[len(prefix):].isdigit():
                    raise RuntimeError("private contact review identity differs")
                events = timeline.events(row.thread_id)
                original = events[0]
                self._validate_review_source(row.thread_id, original, household=False)
                prepared = next((event for event in events if event.event_type == "PROACTIVE_EXPRESSION_PREPARED"), None)
                delivered = next((event for event in events if event.event_type == "PROACTIVE_EXPRESSION_DELIVERED"), None)
                if prepared is None or delivered is None:
                    raise RuntimeError("private contact delivery evidence is missing")
                for event in (prepared, delivered):
                    if canonical_sha256(event.payload) != event.payload_sha256:
                        raise RuntimeError("private contact evidence hash differs")
                data, receipt, source = prepared.payload["data"], delivered.payload["data"], original.payload["data"]
                from eva.application.sensory import SensoryEvent
                from eva.adapters.postgres.sensory_events import PostgresSensoryEventStore
                observation = SensoryEvent(source["source"], source["source_event_id"], source["event_type"],
                    original.occurred_at, source["payload"])
                binding = data.get("private_contact", {})
                text = data.get("expression")
                if (binding.get("owner_principal_id") != str(self._owner)
                        or not re.fullmatch(r"[0-9a-f]{64}", str(binding.get("mandate_sha256")))
                        or binding.get("source_sha256") != PostgresSensoryEventStore._private_expression_source_sha256(observation)
                        or source["event_type"] != "private_opportunity_review"
                        or receipt.get("source_event_id") != source["source_event_id"]
                        or data.get("source_event_id") != source["source_event_id"]
                        or receipt.get("channel") != "telegram"
                        or not isinstance(receipt.get("provider_message_id"), str) or not receipt["provider_message_id"].strip()
                        or not isinstance(text, str) or not text.strip() or len(text) > 4000):
                    raise RuntimeError("private contact delivery binding differs")
                contacts.append({"text": text, "delivered_at": delivered.occurred_at.isoformat(),
                    "source_event_id": source["source_event_id"], "state": "delivered",
                    "provider_message_id": receipt["provider_message_id"]})
            self._authorize(conn, now)
            return contacts

    def _validate_review_source(self, thread_id, event, *, household):
        """Validate immutable audience/source identity, never today's replacement policy."""
        digest = event.payload_sha256 if isinstance(event.payload_sha256, str) else bytes(event.payload_sha256).hex()
        if canonical_sha256(event.payload) != digest:
            raise ValueError("review original source hash differs")
        data = event.payload.get("data", {})
        if (event.event_type != "SENSORY_EVENT_OBSERVED"
                or data.get("source_event_id") != thread_id.removeprefix("sensory:")):
            raise ValueError("review original source differs")
        if not household:
            if data.get("source") != "initiative-review":
                raise ValueError("private review original source differs")
            return
        from eva.application.household_initiative import HouseholdInitiativeBinding
        from eva.application.sensory import SensoryEvent
        try:
            binding = HouseholdInitiativeBinding(**data["payload"]["household_binding"])
            source = SensoryEvent(data["source"], data["source_event_id"], data["event_type"],
                                  event.occurred_at, data["payload"])
            valid = (binding.tenant_id == self._tenant_id
                     and binding.installation_id == self._installation_id
                     and source.source == "household-initiative" and binding.matches_event(source))
        except (KeyError, TypeError, ValueError):
            valid = False
        if not valid:
            raise ValueError("household review original binding differs")

    def _latest_review(self, conn, *, household=False, unresolved=False):
        """Keep each lane visible, including an older globally unresolved review."""
        c, e = operational_checkpoints.c, operational_events.c
        prefix = ("sensory:household-initiative:" if household else
                  "sensory:initiative-review:" + hashlib.sha256(str(self._owner).encode()).hexdigest()[:24] + ":")
        original = operational_events.alias("review_original")
        query = (sa.select(c.updated_at, c.phase, c.status, c.thread_id, c.revision,
                           e.payload, e.payload_sha256,
                           original.c.payload.label("original_payload"),
                           original.c.payload_sha256.label("original_sha256"),
                           original.c.event_type.label("original_type"),
                           original.c.occurred_at.label("original_at"))
            .join(operational_events, sa.and_(c.tenant_id == e.tenant_id,
                c.installation_id == e.installation_id, c.thread_id == e.thread_id,
                c.checkpoint_id == e.checkpoint_id, c.revision == e.sequence))
            .join(original, sa.and_(c.tenant_id == original.c.tenant_id,
                c.installation_id == original.c.installation_id, c.thread_id == original.c.thread_id,
                original.c.sequence == 1))
            .where(c.tenant_id == self._scope.tenant_id,
                c.installation_id == self._scope.installation_id, c.thread_id.startswith(prefix)))
        if unresolved:
            query = query.where(c.phase.in_(("will_model_started", "will_usage_uncertain")))
        row = conn.execute(query.order_by(c.updated_at.desc(), c.thread_id.desc()).limit(1)).first()
        if row is None:
            return None
        if canonical_sha256(row.payload) != bytes(row.payload_sha256).hex():
            raise RuntimeError("initiative review payload hash differs")
        from types import SimpleNamespace
        self._validate_review_source(row.thread_id, SimpleNamespace(
            payload=row.original_payload, payload_sha256=row.original_sha256,
            event_type=row.original_type, occurred_at=row.original_at), household=household)
        data = row.payload["data"]
        reason = data.get("reason")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 4000):
            raise RuntimeError("initiative review reason differs")
        state, blocker, intent_id = "unknown", None, None
        if row.status in ("failed", "needs_help"):
            state, blocker = "attention", ("review_preparation_failed"
                if row.phase == "will_preparation_failed" else "review_needs_help")
        elif row.phase == "will_abandoned":
            state = "abandoned"
        elif row.phase == "will_decision_rejected":
            state = "rejected"
        elif row.phase == "will_waiting_admission":
            state, blocker = "waiting", "model_admission"
        elif row.phase == "will_decided":
            proposal = data.get("initiative")
            if data.get("provider") == "eva:background-budget":
                state, blocker = "waiting", "background_budget"
            elif isinstance(proposal, dict) and proposal.get("status") == "adopted":
                intent_id = self._intent_id(proposal.get("intent_id"))
                state = "adopted"
            elif isinstance(proposal, dict) and proposal.get("status") == "not_admitted":
                state, blocker = "not_admitted", proposal.get("reason")
                if not isinstance(blocker, str) or len(blocker) > 200:
                    raise RuntimeError("initiative review blocker differs")
            elif data.get("disposition") == "silence":
                state = "silence"
        elif row.phase in ("expression_pending", "expression_claimed", "expression_delivered", "expression_expired"):
            state = {"expression_pending": "contact_pending", "expression_claimed": "contact_sending",
                     "expression_delivered": "contact_delivered", "expression_expired": "contact_expired"}[row.phase]
        elif row.phase == "sensory_observed":
            state = "scheduled"
        elif row.phase in ("will_claimed", "will_model_started", "will_revalidated"):
            state = "working"
        return {"updated_at": row.updated_at.isoformat(), "phase": row.phase,
                "state": state, "reason": reason, "blocker": blocker, "intent_id": intent_id,
                "thread_id": row.thread_id, "revision": row.revision,
                "source_payload_sha256": bytes(row.original_sha256).hex(),
                "payload_sha256": bytes(row.payload_sha256).hex()}

    def abandon_uncertain_review(self, thread_id, expected_revision, *, now: datetime):
        """Owner closes a lost decision; never infer usage or replay its work."""
        prefix = "sensory:initiative-review:" + hashlib.sha256(str(self._owner).encode()).hexdigest()[:24] + ":"
        household = isinstance(thread_id, str) and bool(re.fullmatch(
            r"sensory:household-initiative:[0-9a-f]{64}:(?:[0-9]+|observation:[0-9a-f]{64})", thread_id))
        private = (isinstance(thread_id, str) and thread_id.startswith(prefix)
                   and thread_id[len(prefix):].isdigit())
        if (not (private or household)
                or isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
                or expected_revision < 1):
            raise ValueError("invalid initiative review recovery")
        with self._engine.begin() as conn:
            self._authorize(conn, now)
            c, e = operational_checkpoints.c, operational_events.c
            row = conn.execute(sa.select(operational_checkpoints).where(
                c.tenant_id == self._scope.tenant_id,
                c.installation_id == self._scope.installation_id,
                c.thread_id == thread_id).with_for_update()).one_or_none()
            if (row is None or row.revision != expected_revision
                    or row.phase != "will_usage_uncertain" or row.status != "needs_help"):
                raise ValueError("private review recovery no longer matches")
            history = conn.execute(sa.select(operational_events).where(
                e.tenant_id == self._scope.tenant_id,
                e.installation_id == self._scope.installation_id,
                e.thread_id == thread_id).order_by(e.sequence)).all()
            allowed = {"SENSORY_EVENT_OBSERVED", "WILL_EVALUATION_CLAIMED",
                       "WILL_EVALUATION_RECLAIMED", "WILL_MODEL_STARTED", "WILL_USAGE_UNCERTAIN",
                       "WILL_PREPARATION_FAILED", "WILL_DISPATCH_DEFERRED", "WILL_ADMISSION_BLOCKED",
                       "RECOVERY_FAILED", "RECOVERY_NEEDS_HELP"}
            if (not history or any(event.event_type not in allowed
                    or canonical_sha256(event.payload) != bytes(event.payload_sha256).hex()
                    for event in history)
                    or history[0].event_type != "SENSORY_EVENT_OBSERVED"
                    or history[-1].event_type != "WILL_USAGE_UNCERTAIN"
                    or history[-1].sequence != row.revision
                    or history[-1].checkpoint_id != row.checkpoint_id):
                raise ValueError("initiative review history is not safely abandonable")
            self._validate_review_source(thread_id, history[0], household=household)
            transition_timeline_thread(conn, self._scope, thread_id=thread_id,
                expected_revision=expected_revision,
                checkpoint_id=f"{thread_id}:owner-abandoned:{expected_revision + 1}",
                operation_id=row.operation_id, phase="will_abandoned", status="completed",
                event_type="WILL_REVIEW_ABANDONED", occurred_at=now,
                retry_count=row.retry_count, last_error_fingerprint=(
                    None if row.last_error_fingerprint is None else bytes(row.last_error_fingerprint).hex()),
                data={"reason":"Eigenaarherstel: oude beoordeling afgebroken; verbruik blijft onbekend.",
                      "usage":None, "previous_revision":expected_revision,
                      "replayed":False, "owner_principal_id":str(self._owner),
                      "review_lane":"household" if household else "private",
                      "source_event_id":history[0].payload["data"]["source_event_id"],
                      "source_payload_sha256":bytes(history[0].payload_sha256).hex(),
                      "previous_payload_sha256":bytes(history[-1].payload_sha256).hex()})
        return {"contract":"EvaPrivateReviewRecovery/v1", "disposition":"abandoned",
                "usage":"unknown", "replayed":False}

    def _artifact(self, conn, thread_id):
        e = operational_events.c
        payload = conn.execute(sa.select(e.payload).where(
            e.tenant_id == self._scope.tenant_id, e.installation_id == self._scope.installation_id,
            e.thread_id == thread_id, e.event_type.in_(("PERSONAL_PROTOTYPE_COMPLETED", "PERSONAL_INITIATIVE_COMPLETED"))
        ).order_by(e.sequence.desc()).limit(1)).scalar_one_or_none()
        if payload is None:
            return None
        data = payload["data"]
        content, filename = data["content"], data["filename"]
        if (not isinstance(content, str) or len(content.encode()) > 131_072
                or not isinstance(filename, str) or len(filename) > 120
                or not filename.endswith(".md") or "/" in filename or "\\" in filename
                or hashlib.sha256(content.encode()).hexdigest() != data["content_sha256"]):
            raise RuntimeError("initiative artifact differs")
        return {"filename": filename, "content": content, "sha256": data["content_sha256"]}

    def artifact(self, intent_id: str, *, now: datetime) -> dict:
        intent_id = self._intent_id(intent_id)
        with self._engine.begin() as conn:
            self._authorize(conn, now)
            row = conn.execute(self._query().where(
                operational_checkpoints.c.thread_id == "intent:" + intent_id
            )).one_or_none()
            if row is None:
                raise LookupError("initiative unavailable")
            result = self._artifact(conn, row.thread_id)
            if result is None:
                raise LookupError("initiative artifact unavailable")
            return result

    def stop(self, intent_id: str, *, now: datetime) -> dict:
        intent_id = self._intent_id(intent_id)
        with self._engine.begin() as conn:
            self._authorize(conn, now)
            row = conn.execute(self._query().where(
                operational_checkpoints.c.thread_id == "intent:" + intent_id
            ).with_for_update(of=operational_checkpoints)).one_or_none()
            if row is None:
                raise LookupError("initiative unavailable")
            if row.phase in {"release_ready", "initiative_completed"}:
                # Completion stays immutable. Stop only the exact deferred native
                # application capability via the existing source-turn stop marker.
                status = ("stopped" if row.phase == "initiative_completed"
                          and self._has_deferred_native_application(conn, row, now)
                          else "already_completed")
            elif row.phase == "personal_stopped":
                status = "stopped"
            else:
                transition_timeline_thread(conn, self._scope, thread_id=row.thread_id,
                    expected_revision=row.revision,
                    checkpoint_id=f"{row.thread_id}:owner-stop:{row.revision + 1}",
                    phase="personal_stopped", status="completed",
                    event_type="PERSONAL_INITIATIVE_STOPPED", occurred_at=now,
                    operation_id=intent_id,
                    data={"intent_id": intent_id, "owner_principal_id": str(self._owner),
                          "principal_binding_id": str(self._binding), "reason": "owner_stop"})
                status = "stopped"
        if status == "stopped":
            self._stop_execution(intent_id)
        return {"contract": "EvaOwnerInitiativeStop/v1", "intent_id": intent_id,
                "status": status, "in_flight_effects_may_complete": status == "stopped"}

    def _feedback(self, conn, thread_id):
        e = operational_events.c
        payload = conn.execute(sa.select(e.payload).where(
            e.tenant_id == self._scope.tenant_id, e.installation_id == self._scope.installation_id,
            e.thread_id == thread_id, e.event_type == "PERSONAL_INITIATIVE_FEEDBACK"
        ).order_by(e.sequence.desc()).limit(1)).scalar_one_or_none()
        return None if payload is None else payload["data"]["feedback"]

    def feedback(self, intent_id, feedback, *, now, request_key=None):
        intent_id = self._intent_id(intent_id)
        if not isinstance(feedback, str) or not 1 <= len(feedback.strip()) <= 800:
            raise ValueError("initiative feedback must contain 1..800 characters")
        if request_key is not None and (
                not isinstance(request_key, str) or not 1 <= len(request_key) <= 2048):
            raise ValueError("initiative feedback request key is invalid")
        with self._engine.begin() as conn:
            self._authorize(conn, now)
            row = conn.execute(self._query().where(
                operational_checkpoints.c.thread_id == "intent:" + intent_id
            ).with_for_update(of=operational_checkpoints)).one_or_none()
            if row is None:
                raise LookupError("initiative unavailable")
            previous = None
            if request_key is not None:
                e = operational_events.c
                previous = conn.execute(sa.select(e.payload).where(
                    e.tenant_id == self._scope.tenant_id,
                    e.installation_id == self._scope.installation_id,
                    e.thread_id == row.thread_id,
                    e.event_type == "PERSONAL_INITIATIVE_FEEDBACK",
                    e.payload["data"]["request_key"].astext == request_key,
                ).order_by(e.sequence.desc()).limit(1)).scalar_one_or_none()
            if previous is not None:
                if previous["data"]["feedback"] != feedback.strip():
                    raise ValueError("initiative feedback changed during replay")
                execution_stopped = previous["data"]["execution_stopped"]
            else:
                terminal = row.phase in {"release_ready", "initiative_completed", "personal_stopped", "initiative_blocked"}
                execution_stopped = not terminal
                transition_timeline_thread(conn, self._scope, thread_id=row.thread_id,
                    expected_revision=row.revision,
                    checkpoint_id=f"{row.thread_id}:feedback:{row.revision + 1}",
                    phase=row.phase if terminal else "personal_stopped",
                    status=row.status if terminal else "completed",
                    event_type="PERSONAL_INITIATIVE_FEEDBACK", occurred_at=now,
                    operation_id=intent_id,
                    data={"intent_id": intent_id, "owner_principal_id": str(self._owner),
                          "principal_binding_id": str(self._binding), "feedback": feedback.strip(),
                          "execution_stopped": execution_stopped,
                          **({"request_key": request_key} if request_key is not None else {})})
        if execution_stopped:
            self._stop_execution(intent_id)
        return {"status": "recorded", "intent_id": intent_id,
                "execution_stopped": execution_stopped}

    def _has_deferred_native_application(self, conn, row, now):
        # Development completion never completes a pending runtime repair.
        # Preserve that completed work, but let the owner stop its later
        # assessment/application through the same native source STOP marker.
        repair_id = row.adoption["data"].get("repair_incident_id")
        if repair_id is not None:
            c, e = operational_checkpoints.c, operational_events.c
            repair = conn.execute(sa.select(c.phase, c.status, e.payload, e.payload_sha256)
                .select_from(operational_checkpoints.join(operational_events,
                    sa.and_(e.tenant_id == c.tenant_id, e.installation_id == c.installation_id,
                            e.thread_id == c.thread_id, e.event_type == "SELF_REPAIR_OBSERVED")))
                .where(c.tenant_id == self._scope.tenant_id,
                       c.installation_id == self._scope.installation_id,
                       c.thread_id == repair_id)).one_or_none()
            if (repair is not None and repair.phase == "repair_investigating"
                    and repair.status == "waiting_for_proof"):
                if canonical_sha256(repair.payload) != bytes(repair.payload_sha256).hex():
                    raise RuntimeError("repair source hash differs")
                data = repair.payload["data"]
                if (row.thread_id == "intent:" + data["intent_id"]
                        and row.adoption["data"].get("repair_source") == data["source"]
                        and row.adoption["data"].get("repair_mandate_sha256") == data["mandate_sha256"]):
                    return True
        from eva.adapters.postgres.current_catalog import delegated_work_runs as work
        from eva.adapters.postgres.delegated_work import PostgresDelegatedWorkStore
        from eva.contracts.delegated_digital_work import DelegatedInitiativeDigitalWorkSpec
        e = operational_events.c
        completion = conn.execute(sa.select(e.payload, e.payload_sha256).where(
            e.tenant_id == self._scope.tenant_id, e.installation_id == self._scope.installation_id,
            e.thread_id == row.thread_id, e.event_type == "PERSONAL_INITIATIVE_COMPLETED")
            .order_by(e.sequence.desc()).limit(1)).one_or_none()
        if completion is None:
            return False  # Preserve legacy direct completion's already_completed behavior.
        if canonical_sha256(completion.payload) != bytes(completion.payload_sha256).hex():
            raise RuntimeError("native completed source hash differs")
        bound = completion.payload.get("data", {})
        if not isinstance(bound.get("work_id"), str):
            return False
        PostgresOperationalTimeline(self._engine, self._authority)._scope(conn)
        try:
            stored = conn.execute(sa.select(work).where(work.c.work_id == bound["work_id"])).mappings().one_or_none()
            if stored is None:
                return False
            loaded = PostgresDelegatedWorkStore.validate_stored_row(stored)
            spec = loaded.spec
            return (type(spec) is DelegatedInitiativeDigitalWorkSpec
                and spec.owner_principal_id == str(self._owner) and spec.principal_binding_id == str(self._binding)
                and spec.origin.thread_id == row.thread_id and spec.sha256 == bound.get("spec_sha256")
                and loaded.state in {"succeeded", "verified"} and loaded.result is not None
                and canonical_sha256(loaded.result.to_payload()) == bound.get("result_sha256")
                and loaded.retention_until > now
                and "apply_reviewed_component_improvement" in spec.grant.capability_ids)
        finally:
            PostgresOperationalTimeline(self._engine, self._scope)._scope(conn)

    def _stop_execution(self, intent_id):
        from eva.adapters.postgres.conversation_actions import PostgresPersonalInterestStore
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        from eva.adapters.postgres.initiative_execution import execution_turn
        timeline = PostgresOperationalTimeline(self._engine, self._scope)
        claim = PostgresPersonalInterestStore._claim_from_events(self._tenant_id,
            self._installation_id, timeline.events("intent:" + intent_id), 0)
        if claim.execution_mode == "autonomous_task":
            PostgresConversationJournal(self._engine, self._scope).request_turn_stop(execution_turn(claim))
