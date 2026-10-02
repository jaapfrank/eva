"""Owner control of existing personal-work timelines; no second task store."""
from __future__ import annotations

from datetime import datetime
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
        return sa.select(operational_checkpoints, e.payload.label("adoption")).join(
            operational_events, sa.and_(c.tenant_id == e.tenant_id,
                c.installation_id == e.installation_id, c.thread_id == e.thread_id)
        ).where(c.tenant_id == self._scope.tenant_id,
            c.installation_id == self._scope.installation_id,
            e.event_type == "PERSONAL_INTENT_ADOPTED",
            e.payload["data"]["principal_id"].astext == str(self._owner),
            e.payload["data"]["origin"].astext == "eva_initiative")

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
                "latest_review": latest_review, "checked_at": now.isoformat()}

    def _latest_review(self, conn):
        """Read the owner's ordinary private cycle, including decisions before adoption."""
        c, e = operational_checkpoints.c, operational_events.c
        prefix = "sensory:initiative-review:" + hashlib.sha256(str(self._owner).encode()).hexdigest()[:24] + ":"
        row = conn.execute(sa.select(c.updated_at, c.phase, c.status, e.payload, e.payload_sha256)
            .join(operational_events, sa.and_(c.tenant_id == e.tenant_id,
                c.installation_id == e.installation_id, c.thread_id == e.thread_id,
                c.checkpoint_id == e.checkpoint_id, c.revision == e.sequence))
            .where(c.tenant_id == self._scope.tenant_id,
                c.installation_id == self._scope.installation_id, c.thread_id.startswith(prefix))
            .order_by(c.updated_at.desc(), c.thread_id.desc()).limit(1)).first()
        if row is None:
            return None
        if canonical_sha256(row.payload) != bytes(row.payload_sha256).hex():
            raise RuntimeError("initiative review payload hash differs")
        data = row.payload["data"]
        reason = data.get("reason")
        if reason is not None and (not isinstance(reason, str) or len(reason) > 4000):
            raise RuntimeError("initiative review reason differs")
        state, blocker, intent_id = "unknown", None, None
        if row.status in ("failed", "needs_help"):
            state, blocker = "attention", "review_needs_help"
        elif row.phase == "will_abandoned":
            state = "abandoned"
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
        elif row.phase == "sensory_observed":
            state = "scheduled"
        elif row.phase in ("will_claimed", "will_model_started", "will_revalidated"):
            state = "working"
        return {"updated_at": row.updated_at.isoformat(), "phase": row.phase,
                "state": state, "reason": reason, "blocker": blocker, "intent_id": intent_id}

    def abandon_uncertain_review(self, thread_id, expected_revision, *, now: datetime):
        """Owner closes a lost decision; never infer usage or replay its work."""
        prefix = "sensory:initiative-review:" + hashlib.sha256(str(self._owner).encode()).hexdigest()[:24] + ":"
        if (not isinstance(thread_id, str) or not thread_id.startswith(prefix)
                or not thread_id[len(prefix):].isdigit()
                or isinstance(expected_revision, bool) or not isinstance(expected_revision, int)
                or expected_revision < 1):
            raise ValueError("invalid private review recovery")
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
                       "WILL_EVALUATION_RECLAIMED", "WILL_MODEL_STARTED", "WILL_USAGE_UNCERTAIN"}
            if (not history or any(event.event_type not in allowed
                    or canonical_sha256(event.payload) != bytes(event.payload_sha256).hex()
                    for event in history)
                    or history[0].event_type != "SENSORY_EVENT_OBSERVED"
                    or history[0].payload["data"].get("source") != "initiative-review"
                    or history[-1].event_type != "WILL_USAGE_UNCERTAIN"):
                raise ValueError("private review history is not safely abandonable")
            transition_timeline_thread(conn, self._scope, thread_id=thread_id,
                expected_revision=expected_revision,
                checkpoint_id=f"{thread_id}:owner-abandoned:{expected_revision + 1}",
                operation_id=row.operation_id, phase="will_abandoned", status="completed",
                event_type="WILL_REVIEW_ABANDONED", occurred_at=now,
                retry_count=row.retry_count, last_error_fingerprint=(
                    None if row.last_error_fingerprint is None else bytes(row.last_error_fingerprint).hex()),
                data={"reason":"Eigenaarherstel: oude beoordeling afgebroken; verbruik blijft onbekend.",
                      "usage":None, "previous_revision":expected_revision,
                      "replayed":False, "owner_principal_id":str(self._owner)})
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
                status = "already_completed"
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

    def _stop_execution(self, intent_id):
        from eva.adapters.postgres.conversation_actions import PostgresPersonalInterestStore
        from eva.adapters.postgres.conversation_journal import PostgresConversationJournal
        from eva.adapters.postgres.initiative_execution import execution_turn
        timeline = PostgresOperationalTimeline(self._engine, self._scope)
        claim = PostgresPersonalInterestStore._claim_from_events(self._tenant_id,
            self._installation_id, timeline.events("intent:" + intent_id), 0)
        if claim.execution_mode == "autonomous_task":
            PostgresConversationJournal(self._engine, self._scope).request_turn_stop(execution_turn(claim))
