"""Append-only persistence for normalized connector sensory events."""

from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime, timedelta
import hashlib
from uuid import NAMESPACE_URL, uuid5

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    CheckpointState,
    PostgresOperationalTimeline,
    TimelineScope,
    insert_initial_timeline_thread,
    operational_checkpoints,
    operational_events,
    transition_timeline_thread,
)
from eva.application.sensory import SensoryEvent
from eva.application.free_will import (
    AutonomousAdmissionBlocker,
    AutonomousUsageUncertain,
    FreeWillDecision,
    ProactiveExpressionClaim,
    SensoryDecisionClaim,
)
from eva.contracts.base import JsonValue
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


# Historical admission failures, not a general provider-error retry policy.
_LEGACY_COST_BLOCK_FINGERPRINT = hashlib.sha256(
    b"ConversationModelError: autonomous model spend has no verified price"
).hexdigest()


class PostgresSensoryEventStore:
    def __init__(
        self,
        engine: sa.Engine,
        *,
        tenant_id: str,
        installation_id: str,
        decision_lease_seconds: int = 60,
        observation_freshness_seconds: int = 300,
        recovery_started_at: datetime | None = None,
        historical_group_size: int = 1,
        initiative_principal_id: str | None = None,
        initiative_authorizer=None,
        context_reviews_only: bool = False,
        household_binding=None,
    ) -> None:
        self._timeline = PostgresOperationalTimeline(
            engine,
            TimelineScope(
                tenant_id=uuid5(NAMESPACE_URL, f"eva2:{tenant_id}"),
                installation_id=uuid5(
                    NAMESPACE_URL, f"eva2:{tenant_id}:{installation_id}"
                ),
            ),
        )
        if not 10 <= decision_lease_seconds <= 300:
            raise ValueError("sensory decision lease is outside 10..300 seconds")
        self._decision_lease_seconds = decision_lease_seconds
        if isinstance(observation_freshness_seconds, bool) or not isinstance(
            observation_freshness_seconds, int
        ) or observation_freshness_seconds < 1:
            raise ValueError("observation freshness must be positive seconds")
        if recovery_started_at is not None and recovery_started_at.tzinfo is None:
            raise ValueError("recovery timestamp must be timezone-aware")
        self._observation_freshness = timedelta(seconds=observation_freshness_seconds)
        self._recovery_started_at = recovery_started_at
        if (isinstance(historical_group_size, bool)
                or not isinstance(historical_group_size, int)
                or not 1 <= historical_group_size <= 16):
            raise ValueError("historical group size is outside 1..16")
        self._historical_group_size = historical_group_size
        self._initiative_principal_id = initiative_principal_id
        self._initiative_authorizer = initiative_authorizer
        if context_reviews_only and (initiative_principal_id is None or initiative_authorizer is None):
            raise ValueError("private context reviews require the current owner mandate")
        self._household_binding = household_binding
        if household_binding is not None:
            from eva.application.household_initiative import HouseholdInitiativeBinding
            if (not isinstance(household_binding, HouseholdInitiativeBinding)
                    or not context_reviews_only
                    or household_binding.tenant_id != tenant_id
                    or household_binding.installation_id != installation_id):
                raise ValueError("household initiative storage binding differs")
        self._context_reviews_only = context_reviews_only
        self._decision_prefix = ("sensory:initiative-review:" + hashlib.sha256(
            initiative_principal_id.encode()).hexdigest()[:24] + ":"
            if context_reviews_only else "sensory:home-assistant:")
        if household_binding is not None:
            self._decision_prefix = "sensory:" + household_binding.event(
                datetime.fromtimestamp(0, tz=UTC)
            ).source_event_id.rsplit(":", 1)[0] + ":"

    def schedule_context_review(self, *, now: datetime) -> str:
        """At most one durable private opportunity review per hour, without catch-up."""
        if not self._context_reviews_only:
            raise ValueError("context review scheduler requires its private source")
        try:
            self._initiative_authorizer()
        except Exception:
            return "mandate_unavailable"
        scope = self._timeline._scope_value
        schedule_prefix = ("sensory:household-initiative:" if self._household_binding is not None
                           else self._decision_prefix)
        with self._timeline._engine.begin() as connection:
            self._timeline._scope(connection)
            connection.execute(sa.text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
                {"key": schedule_prefix})
            c, e = operational_checkpoints.c, operational_events.c
            latest = connection.execute(sa.select(c.phase, c.updated_at, c.thread_id, c.revision, c.operation_id).where(
                c.tenant_id == scope.tenant_id, c.installation_id == scope.installation_id,
                c.thread_id.startswith(schedule_prefix)).order_by(c.updated_at.desc()).limit(1)).first()
            # A lost decision blocks until explicitly abandoned by the owner.
            # Preserve the original interval after abandonment: acknowledgement time
            # is not a new model attempt and must not postpone recovery by an hour.
            if latest is not None:
                terminal = {"will_decided", "will_abandoned"}
                if self._household_binding is not None:
                    terminal |= {"expression_offered", "expression_interrupted", "expression_expired"}
                if (self._household_binding is not None and latest.phase == "expression_pending"
                        and now - latest.updated_at > timedelta(minutes=15)):
                    transition_timeline_thread(connection, scope, thread_id=latest.thread_id,
                        expected_revision=latest.revision,
                        checkpoint_id=f"{latest.thread_id}:expression-expired",
                        operation_id=latest.operation_id, phase="expression_expired",
                        status="completed", event_type="PROACTIVE_EXPRESSION_EXPIRED",
                        occurred_at=now, data={"reason": "household destination opportunity expired"})
                    return "idle"
                if latest.phase not in terminal:
                    return "idle"
                reviewed_at = latest.updated_at
                if latest.phase == "will_abandoned":
                    reviewed_at = connection.execute(sa.select(e.occurred_at).where(
                        e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
                        e.thread_id == latest.thread_id, e.event_type == "WILL_USAGE_UNCERTAIN")
                        .order_by(e.sequence.desc()).limit(1)).scalar_one()
                if now - reviewed_at < timedelta(hours=1):
                    return "idle"
            if self._household_binding is None:
                pending = connection.execute(sa.select(c.thread_id).join(operational_events, sa.and_(
                    c.tenant_id == e.tenant_id, c.installation_id == e.installation_id,
                    c.thread_id == e.thread_id)).where(
                    c.tenant_id == scope.tenant_id, c.installation_id == scope.installation_id,
                    e.event_type == "PERSONAL_INTENT_ADOPTED",
                    e.payload["data"]["principal_id"].astext == self._initiative_principal_id,
                    e.payload["data"]["origin"].astext == "eva_initiative",
                    c.phase.not_in(("release_ready", "personal_stopped", "initiative_completed", "initiative_blocked"))
                ).limit(1)).first()
                if pending is not None:
                    return "idle"
            window = int(now.timestamp()) // 3600
            source_id = self._decision_prefix.removeprefix("sensory:") + str(window)
            thread_id = "sensory:" + source_id
            payload = {"review_window": window}
            if self._household_binding is None:
                # Capture the bounded source view once in the existing immutable
                # review. Retries and admission retain this original provenance;
                # current service reads remain work for the native executor.
                from eva.adapters.postgres.initiative_context import read_private_initiative_context
                payload["life_context"] = read_private_initiative_context(
                    connection, scope, self._initiative_principal_id, now)
            data = {"event_type": "private_opportunity_review", "source": "initiative-review",
                    "source_event_id": source_id, "payload": payload}
            if self._household_binding is not None:
                event = self._household_binding.event(now)
                if source_id != event.source_event_id:
                    raise RuntimeError("household initiative source identity differs")
                data = {"event_type": event.event_type, "source": event.source,
                        "source_event_id": source_id, "payload": event.payload}
            try:
                insert_initial_timeline_thread(connection, scope, thread_id=thread_id,
                    checkpoint_id=f"checkpoint:{source_id}:observed", occurred_at=now,
                    data=data, event_type="SENSORY_EVENT_OBSERVED", phase="sensory_observed",
                    status="completed", operation_id=source_id)
            except CheckpointConflict:
                return "idle"
        return "scheduled"

    def context_review_claim_is_current(self, claim, *, now: datetime) -> bool:
        if not self._context_reviews_only or not claim.thread_id.startswith(self._decision_prefix):
            return False
        state = self._timeline.load(claim.thread_id)
        return (state.phase == "will_model_started" and state.status == "active"
                and state.revision == claim.revision and state.deadline is not None
                and state.deadline > now)

    def native_capabilities(self) -> tuple[str, ...]:
        """Expose the current owner mandate, never capability guesses from memory."""
        if self._initiative_authorizer is None:
            return ()
        try:
            policy, _ = self._initiative_authorizer()
            return tuple(sorted({cap["action_name"] for cap in policy["capabilities"]}
                | set(policy.get("digital_work", {}).get("capability_ids", ()))))
        except Exception:
            return ()  # Missing or revoked mandate is not execution authority.

    def retain(self, event: SensoryEvent) -> str:
        thread_id = f"sensory:{event.source_event_id}"
        data: dict[str, JsonValue] = {
            "event_type": event.event_type,
            "payload": event.payload,
            "source": event.source,
            "source_event_id": event.source_event_id,
        }
        try:
            self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"checkpoint:{event.source_event_id}:observed",
                occurred_at=event.occurred_at,
                data=data,
                event_type="SENSORY_EVENT_OBSERVED",
                phase="sensory_observed",
                status="completed",
                operation_id=event.source_event_id,
            )
            return "retained"
        except CheckpointConflict:
            events = self._timeline.events(thread_id)
            durable = events[0].payload.get("data") if events else None
            if durable != data:
                raise RuntimeError("sensory event identity collision") from None
            return "already_retained"

    def claim_next(
        self, *, now: datetime, include_waiting: bool = True
    ) -> SensoryDecisionClaim | None:
        if not self._context_reviews_only:
            self._restore_group_completions(now=now)
            self._restore_cost_waits(now=now, admission_open=include_waiting)
        states = self._timeline.list_checkpoints(
            thread_prefix=self._decision_prefix,
            phases=("sensory_observed", "will_claimed", "will_model_started", "will_revalidated") + (
                ("will_waiting_admission",) if include_waiting else ()
            ),
            statuses=("completed", "active", "failed") + (
                ("waiting_for_proof",) if include_waiting else ()
            ),
            updated_before=now,
            limit=64,
        )
        for state in reversed(states):
            if state.phase in {"will_claimed", "will_model_started"} and state.status == "active":
                if state.deadline is None or state.deadline > now:
                    continue
                history = self._timeline.events(state.thread_id)
                last_data = history[-1].payload.get("data", {}) if history else {}
                if (state.phase == "will_model_started"
                        or not isinstance(last_data, dict)
                        or last_data.get("model_dispatch_started") is not False):
                    # Old claims had no pre-model marker either. Absence of a
                    # durable response is not evidence that no call occurred.
                    event = self._event_from_thread(state.thread_id, latest=True)
                    try:
                        self.fail(SensoryDecisionClaim(
                            event, state.thread_id, state.operation_id or event.source_event_id,
                            state.revision, state.retry_count,
                        ), AutonomousUsageUncertain("model lease expired"), now=now)
                    except CheckpointConflict:
                        pass
                    continue
                state = self._timeline.record_recovery_failure(
                    thread_id=state.thread_id,
                    expected_revision=state.revision,
                    checkpoint_id=f"{state.thread_id}:will-recovery:{state.retry_count + 1}",
                    operation_id=state.operation_id,
                    error_text="sensory will evaluation lease expired",
                    occurred_at=now,
                )
                if state.status == "needs_help":
                    continue
            if state.status == "failed" and state.retry_count >= 3:
                continue
            event = self._event_from_thread(state.thread_id, latest=True)
            if (state.phase == "will_waiting_admission" and not self._context_reviews_only) or self._requires_revalidation(event, now=now):
                # Permission to use the model says nothing about whether the
                # observation is still relevant. Keep recovery separate from a
                # fresh decision, including after a process restart.
                try:
                    self._require_revalidation(
                        SensoryDecisionClaim(
                            event, state.thread_id,
                            state.operation_id or event.source_event_id,
                            state.revision, state.retry_count,
                        ),
                        now=now,
                    )
                except CheckpointConflict:
                    pass
                continue
            try:
                return self._claim_decision(state, event, now=now)
            except CheckpointConflict:
                continue
        return None

    def _claim_decision(
        self, state: CheckpointState, event: SensoryEvent, *, now: datetime,
    ) -> SensoryDecisionClaim:
        claimed = self._timeline.transition(
            thread_id=state.thread_id, expected_revision=state.revision,
            checkpoint_id=f"{state.thread_id}:will-claimed:{state.revision + 1}",
            operation_id=state.operation_id, phase="will_claimed", status="active",
            event_type=("WILL_EVALUATION_CLAIMED" if state.phase == "sensory_observed"
                        else "WILL_EVALUATION_RECLAIMED"),
            occurred_at=now, deadline=now + timedelta(seconds=self._decision_lease_seconds),
            data={"source_event_id": event.source_event_id, "shadow_mode": True,
                  "model_dispatch_started": False},
            retry_count=state.retry_count, last_error_fingerprint=state.last_error_fingerprint,
        )
        return SensoryDecisionClaim(
            event, state.thread_id, state.operation_id or event.source_event_id,
            claimed.revision, claimed.retry_count,
        )

    def mark_model_started(
        self, claim: SensoryDecisionClaim, *, now: datetime,
    ) -> SensoryDecisionClaim | None:
        state = self._timeline.load(claim.thread_id)
        if (state.phase != "will_claimed" or state.status != "active"
                or state.revision != claim.revision
                or state.deadline is None or state.deadline <= now):
            return None
        try:
            started = self._timeline.transition(
                thread_id=claim.thread_id, expected_revision=claim.revision,
                checkpoint_id=f"{claim.thread_id}:model-started:{claim.revision + 1}",
                operation_id=claim.operation_id, phase="will_model_started", status="active",
                event_type="WILL_MODEL_STARTED", occurred_at=now,
                deadline=now + timedelta(seconds=self._decision_lease_seconds),
                data={"source_event_id": claim.event.source_event_id,
                      "meaning": "model_adapter_may_dispatch_outcome_not_yet_durable"},
                retry_count=claim.retry_count,
                last_error_fingerprint=state.last_error_fingerprint,
            )
        except CheckpointConflict:
            return None
        return SensoryDecisionClaim(
            claim.event, claim.thread_id, claim.operation_id, started.revision, started.retry_count,
        )

    def claim_revalidated(
        self, claim: SensoryDecisionClaim, *, now: datetime,
    ) -> SensoryDecisionClaim | None:
        state = self._timeline.load(claim.thread_id)
        if (state.phase != "will_revalidated" or state.status != "completed"
                or state.revision != claim.revision + 1):
            return None
        event = self._event_from_thread(state.thread_id, latest=True)
        if self._requires_revalidation(event, now=now):
            return None
        try:
            return self._claim_decision(state, event, now=now)
        except CheckpointConflict:
            return None

    def _requires_revalidation(self, event: SensoryEvent, *, now: datetime) -> bool:
        if self._household_binding is not None:
            if event != self._household_binding.event(event.occurred_at):
                raise ValueError("household review source differs")
            return False
        if self._context_reviews_only:
            if event.source != "initiative-review" or event.event_type != "private_opportunity_review":
                raise ValueError("private review source differs")
            return False  # It claims no external state; memory is recalled at evaluation time.
        return (
            now - event.occurred_at > self._observation_freshness
            or (self._recovery_started_at is not None
                and event.occurred_at < self._recovery_started_at)
        )

    def claim_next_revalidation(self, *, now: datetime) -> SensoryDecisionClaim | None:
        grouped = self._group_waiting_history(now=now)
        states = [self._timeline.load(grouped)] if grouped else self._timeline.list_checkpoints(
            thread_prefix="sensory:home-assistant:",
            phases=("will_waiting_revalidation", "will_revalidation_claimed"),
            statuses=("waiting_for_proof", "active", "failed"),
            updated_before=now, limit=64, oldest_first=True,
        )
        # New arrivals must not permanently hide older recovery work outside
        # the bounded page. Fresh observations keep their separate selection.
        for state in states:
            if state.status == "active":
                if state.deadline is None or state.deadline > now:
                    continue
                try:
                    state = self._timeline.record_recovery_failure(
                        thread_id=state.thread_id, expected_revision=state.revision,
                        checkpoint_id=f"{state.thread_id}:revalidation-expired:{state.revision + 1}",
                        operation_id=state.operation_id,
                        error_text="sensory source read lease expired", occurred_at=now,
                    )
                except CheckpointConflict:
                    continue
                if state.status == "needs_help":
                    continue
            original = self._event_from_thread(state.thread_id)
            try:
                claimed = self._timeline.transition(
                    thread_id=state.thread_id, expected_revision=state.revision,
                    checkpoint_id=f"{state.thread_id}:revalidation-claimed:{state.revision + 1}",
                    operation_id=state.operation_id, phase="will_revalidation_claimed",
                    status="active", event_type="SENSORY_REVALIDATION_CLAIMED",
                    occurred_at=now, deadline=now + timedelta(seconds=self._decision_lease_seconds),
                    data={"source_event_id": original.source_event_id},
                    retry_count=state.retry_count, last_error_fingerprint=state.last_error_fingerprint,
                )
            except CheckpointConflict:
                continue
            return SensoryDecisionClaim(
                original, state.thread_id, state.operation_id or original.source_event_id,
                claimed.revision, claimed.retry_count,
            )
        return None

    def _group_waiting_history(self, *, now: datetime) -> str | None:
        """Assign bounded same-source history to one durable decision owner.

        No observation is deleted or judged here. The ordinary revalidation,
        model-start fence and delivery loop handle the resulting group thread.
        """
        if self._historical_group_size == 1:
            return None
        scope = self._timeline._scope_value
        checkpoints, events = operational_checkpoints, operational_events
        with self._timeline._engine.begin() as connection:
            self._timeline._scope(connection)
            query = sa.select(
                checkpoints.c.thread_id, checkpoints.c.revision, checkpoints.c.operation_id,
                checkpoints.c.retry_count, events.c.payload, events.c.payload_sha256,
                events.c.occurred_at,
            ).join(events, sa.and_(
                events.c.tenant_id == checkpoints.c.tenant_id,
                events.c.installation_id == checkpoints.c.installation_id,
                events.c.thread_id == checkpoints.c.thread_id, events.c.sequence == 1,
            )).where(
                checkpoints.c.tenant_id == scope.tenant_id,
                checkpoints.c.installation_id == scope.installation_id,
                checkpoints.c.phase == "will_waiting_revalidation",
                checkpoints.c.status == "waiting_for_proof",
                checkpoints.c.updated_at <= now,
                events.c.event_type == "SENSORY_EVENT_OBSERVED",
                events.c.payload["data"]["event_type"].astext == "state_changed",
                events.c.occurred_at < now - self._observation_freshness,
            ).order_by(events.c.occurred_at, checkpoints.c.thread_id)
            seed = connection.execute(query.limit(1).with_for_update(
                of=checkpoints, skip_locked=True,
            )).mappings().first()
            if seed is None:
                return None
            original = seed["payload"]["data"]
            entity = original["payload"].get("entity_id")
            if not isinstance(entity, str) or not entity:
                return None
            rows = connection.execute(query.where(
                events.c.payload["data"]["source"].astext == original["source"],
                events.c.payload["data"]["payload"]["entity_id"].astext == entity,
            ).limit(self._historical_group_size).with_for_update(
                of=checkpoints, skip_locked=True,
            )).mappings().all()
            selected, members = [], []
            for row in rows:
                if canonical_sha256(row["payload"]) != bytes(row["payload_sha256"]).hex():
                    raise RuntimeError("historical observation hash differs")
                member = {**row["payload"]["data"], "occurred_at": row["occurred_at"].isoformat()}
                # Bound context without dropping fields or truncating history.
                if len(canonical_json_bytes([*members, member])) > 6144:
                    break
                selected.append(row)
                members.append(member)
            if len(members) < 2:
                return None
            digest = canonical_sha256({"tenant": str(scope.tenant_id),
                "installation": str(scope.installation_id), "members": members})
            source_id = f"home-assistant:history:{digest}"
            thread = f"sensory:{source_id}"
            insert_initial_timeline_thread(
                connection, scope, thread_id=thread, checkpoint_id=f"{thread}:grouped",
                operation_id=source_id, occurred_at=now, phase="will_waiting_revalidation",
                status="waiting_for_proof", event_type="SENSORY_EVENT_OBSERVED",
                data={"source": original["source"], "source_event_id": source_id,
                      "event_type": "historical_observation_batch",
                      "payload": {"entity_id": entity, "historical_observations": members}},
            )
            for row in selected:
                transition_timeline_thread(
                    connection, scope, thread_id=row["thread_id"], expected_revision=row["revision"],
                    checkpoint_id=f"{row['thread_id']}:grouped:{row['revision'] + 1}",
                    operation_id=row["operation_id"], occurred_at=now,
                    phase="will_grouped", status="waiting_for_proof",
                    event_type="SENSORY_EVENT_GROUPED", data={"group_thread_id": thread},
                    retry_count=row["retry_count"],
                )
            return thread

    def complete_revalidation(
        self, claim: SensoryDecisionClaim, event: SensoryEvent, *, now: datetime
    ) -> None:
        if (event.source != claim.event.source
                or event.source_event_id != claim.event.source_event_id
                or event.event_type != "observation_revalidated" or event.occurred_at > now):
            raise ValueError("revalidated observation identity or timestamp differs")
        state = self._timeline.load(claim.thread_id)
        if (state.phase != "will_revalidation_claimed" or state.status != "active"
                or state.revision != claim.revision
                or state.deadline is None or state.deadline <= now):
            raise CheckpointConflict("sensory revalidation claim is stale")
        if event.occurred_at < state.updated_at:
            raise ValueError("revalidated observation predates its source claim")
        self._timeline.transition(
            thread_id=claim.thread_id, expected_revision=claim.revision,
            checkpoint_id=f"{claim.thread_id}:revalidated:{claim.revision + 1}",
            operation_id=claim.operation_id, phase="will_revalidated", status="completed",
            event_type="SENSORY_EVENT_REVALIDATED", occurred_at=now,
            data={"source": event.source, "source_event_id": event.source_event_id,
                  "event_type": event.event_type, "payload": event.payload},
            retry_count=claim.retry_count, last_error_fingerprint=None,
        )

    def _restore_cost_waits(self, *, now: datetime, admission_open: bool) -> None:
        """Reclassify proven pre-dispatch legacy failures without executing work.

        Preserve the original event, all failed attempts and their retry count;
        never touch expression/delivery recovery. When admission has reopened,
        require current relevance evidence rather than replay the observation.
        A history containing any other failure or effect is not auto-repaired.
        """
        states = self._timeline.list_checkpoints(
            thread_prefix="sensory:home-assistant:",
            phases=("will_claimed",),
            statuses=("failed", "needs_help"),
            updated_before=now,
            last_error_fingerprint=_LEGACY_COST_BLOCK_FINGERPRINT,
            limit=64,
        )
        for candidate in states:
            state = self._timeline.load(candidate.thread_id)
            if state != candidate:
                continue
            events = self._timeline.events(state.thread_id)
            if not events or events[0].event_type != "SENSORY_EVENT_OBSERVED":
                continue
            safe_history = True
            for event in events[1:]:
                if event.event_type in {"WILL_EVALUATION_CLAIMED", "WILL_EVALUATION_RECLAIMED"}:
                    continue
                data = event.payload.get("data")
                if (event.event_type not in {"RECOVERY_FAILED", "RECOVERY_NEEDS_HELP"}
                        or not isinstance(data, dict)
                        or data.get("error_fingerprint") != _LEGACY_COST_BLOCK_FINGERPRINT):
                    safe_history = False
                    break
            if not safe_history:
                continue
            original = self._event_from_thread(state.thread_id)
            claim = SensoryDecisionClaim(
                original, state.thread_id, state.operation_id or original.source_event_id,
                state.revision, state.retry_count,
            )
            try:
                if admission_open:
                    self._require_revalidation(claim, now=now)
                else:
                    self.wait_for_admission(claim, "unverified_model_cost", now=now)
            except CheckpointConflict:
                # Another worker won; do not create a replacement attempt.
                continue

    def _require_revalidation(self, claim: SensoryDecisionClaim, *, now: datetime) -> None:
        self._timeline.transition(
            thread_id=claim.thread_id,
            expected_revision=claim.revision,
            checkpoint_id=f"{claim.thread_id}:will-revalidation:{claim.revision + 1}",
            operation_id=claim.operation_id,
            phase="will_waiting_revalidation",
            status="waiting_for_proof",
            event_type="WILL_REVALIDATION_REQUIRED",
            occurred_at=now,
            data={
                "reason": "historical_observation_requires_current_relevance",
                "source_event_id": claim.event.source_event_id,
            },
            retry_count=claim.retry_count,
            last_error_fingerprint=None,
        )

    def wait_for_admission(
        self, claim: SensoryDecisionClaim, reason: AutonomousAdmissionBlocker,
        *, now: datetime,
    ) -> None:
        if reason != "unverified_model_cost":
            raise ValueError("autonomous admission blocker is invalid")
        self._timeline.transition(
            thread_id=claim.thread_id,
            expected_revision=claim.revision,
            checkpoint_id=f"{claim.thread_id}:will-waiting:{claim.revision + 1}",
            operation_id=claim.operation_id,
            phase="will_waiting_admission",
            status="waiting_for_proof",
            event_type="WILL_ADMISSION_BLOCKED",
            occurred_at=now,
            data={"reason": reason, "source_event_id": claim.event.source_event_id},
            retry_count=claim.retry_count,
            last_error_fingerprint=None,
        )

    def complete(
        self,
        claim: SensoryDecisionClaim,
        decision: FreeWillDecision,
        *,
        now: datetime,
    ) -> None:
        if self._household_binding is not None:
            if (claim.event != self._household_binding.event(claim.event.occurred_at)
                    or decision.initiative is not None):
                raise ValueError("household initiative decision audience differs")
            self._initiative_authorizer()
        if (self._context_reviews_only and self._household_binding is None
                and decision.disposition == "respond"):
            decision = FreeWillDecision("silence", decision.reason, 0, decision.provider,
                usage=decision.usage, initiative=decision.initiative)
        if decision.disposition == "respond" and self._cooldown_applies(
            claim, decision, now=now
        ):
            decision = FreeWillDecision(
                "silence",
                "Een recente proactieve melding dekt deze gebeurtenis; cooldown actief.",
                0,
                "eva:proactive-cooldown",
                usage=decision.usage,
                initiative=decision.initiative,
            )
        expression_pending = decision.disposition == "respond"
        transition = dict(
            thread_id=claim.thread_id,
            expected_revision=claim.revision,
            checkpoint_id=f"{claim.thread_id}:will-decided:{claim.revision + 1}",
            operation_id=claim.operation_id,
            phase="expression_pending" if expression_pending else "will_decided",
            status="completed",
            event_type=(
                "PROACTIVE_EXPRESSION_PREPARED"
                if expression_pending
                else "WILL_DECIDED"
            ),
            occurred_at=now,
            data={
                "disposition": decision.disposition,
                "entity_id": claim.event.payload.get("entity_id"),
                "expression": decision.expression,
                "provider": decision.provider,
                "reason": decision.reason,
                "shadow_mode": True,
                "source_event_id": claim.event.source_event_id,
                "urgency": decision.urgency,
                "usage": None if decision.usage is None else asdict(decision.usage),
            },
            retry_count=0,
            last_error_fingerprint=None,
        )
        if self._household_binding is not None:
            transition["data"]["household_binding"] = self._household_binding.to_payload()
        original = self._event_from_thread(claim.thread_id)
        if original.event_type != "historical_observation_batch" and decision.initiative is None:
            self._timeline.transition(**transition)
            return
        # Decision and member coverage commit together. Only the group can
        # prepare an expression or account usage; members never send separately.
        scope = self._timeline._scope_value
        with self._timeline._engine.begin() as connection:
            self._timeline._scope(connection)
            if decision.initiative is not None:
                transition["data"]["initiative"] = self._adopt_initiative(
                    connection, claim, decision, now=now)
            transition_timeline_thread(connection, scope, **transition)
            if original.event_type == "historical_observation_batch":
                self._complete_group_members(connection, original, group_thread_id=claim.thread_id,
                    decision_revision=claim.revision + 1, disposition=decision.disposition, now=now)

    def recent_initiatives(self) -> tuple[dict[str, str], ...]:
        """Bounded, owner-scoped context for avoiding completed/stopped work."""
        if self._initiative_principal_id is None:
            return ()
        scope = self._timeline._scope_value
        c, e = operational_checkpoints.c, operational_events.c
        with self._timeline._engine.begin() as connection:
            self._timeline._scope(connection)
            rows = connection.execute(sa.select(c.phase, c.thread_id, e.payload, e.payload_sha256).join(
                operational_events, sa.and_(c.tenant_id == e.tenant_id,
                    c.installation_id == e.installation_id, c.thread_id == e.thread_id)
            ).where(c.tenant_id == scope.tenant_id, c.installation_id == scope.installation_id,
                e.event_type == "PERSONAL_INTENT_ADOPTED",
                e.payload["data"]["principal_id"].astext == self._initiative_principal_id,
                e.payload["data"]["origin"].astext == "eva_initiative"
            ).order_by(c.updated_at.desc()).limit(16)).all()
        if any(canonical_sha256(row.payload) != bytes(row.payload_sha256).hex() for row in rows):
            raise RuntimeError("initiative history payload hash differs")
        result = []
        for row in rows:
            feedback = next((event.payload["data"].get("feedback")
                for event in reversed(self._timeline.events(row.thread_id))
                if event.event_type == "PERSONAL_INITIATIVE_FEEDBACK"), None)
            result.append({"title": row.payload["data"]["title"],
                "question": row.payload["data"]["next_exploration"], "phase": row.phase,
                **({"owner_feedback": feedback} if feedback else {})})
        return tuple(result)

    def _adopt_initiative(self, connection, claim, decision, *, now):
        proposal = decision.initiative
        principal = self._initiative_principal_id
        if proposal is None or principal is None:
            return {"status": "not_admitted", "reason": "owner_scope_unavailable"}
        if self._initiative_authorizer is not None:
            try:
                self._initiative_authorizer()
            except Exception:
                return {"status": "not_admitted", "reason": "initiative_mandate_unavailable"}
        from eva.adapters.postgres.initiative_adoption import adopt_private_initiative
        return adopt_private_initiative(connection, self._timeline._scope_value,
            proposal.adoption(claim, principal_id=principal), now=now)

    def _complete_group_members(
        self, connection: sa.Connection, original: SensoryEvent, *, group_thread_id: str,
        decision_revision: int, disposition: str, now: datetime,
    ) -> None:
        """Cover members under the already-locked root, without usage or delivery."""
        scope = self._timeline._scope_value
        for member in original.payload["historical_observations"]:
            thread = f"sensory:{member['source_event_id']}"
            row = connection.execute(sa.select(operational_checkpoints).where(
                operational_checkpoints.c.tenant_id == scope.tenant_id,
                operational_checkpoints.c.installation_id == scope.installation_id,
                operational_checkpoints.c.thread_id == thread,
            ).with_for_update()).mappings().one()
            last = connection.execute(sa.select(operational_events.c.payload,
                operational_events.c.payload_sha256).where(
                operational_events.c.tenant_id == scope.tenant_id,
                operational_events.c.installation_id == scope.installation_id,
                operational_events.c.thread_id == thread,
            ).order_by(operational_events.c.sequence.desc()).limit(1)).mappings().one()
            if canonical_sha256(last["payload"]) != bytes(last["payload_sha256"]).hex():
                raise RuntimeError("historical member hash differs")
            data = last["payload"]["data"]
            if (row["phase"] not in {"will_grouped", "will_group_decided"}
                    or data.get("group_thread_id") != group_thread_id):
                raise CheckpointConflict("historical group membership differs")
            if (row["phase"] == "will_group_decided"
                    and data.get("group_decision_revision") == decision_revision):
                continue
            transition_timeline_thread(
                connection, scope, thread_id=thread, expected_revision=row["revision"],
                checkpoint_id=f"{thread}:group-decided:{row['revision'] + 1}",
                operation_id=row["operation_id"], phase="will_group_decided", status="completed",
                event_type="WILL_GROUP_DECIDED", occurred_at=now,
                data={"group_thread_id": group_thread_id, "group_decision_revision": decision_revision,
                      "disposition": disposition, "usage_accounting": "group_only"}, retry_count=0,
            )

    def _restore_group_completions(self, *, now: datetime) -> None:
        """Reconcile roots completed by a pre-grouping version during rollback.

        Only recorded decisions can cover members. Root state/usage/delivery are
        never rewritten, and uncertain or revalidating roots are not accepted.
        """
        scope = self._timeline._scope_value
        child = operational_checkpoints.alias("group_child")
        member = operational_events.alias("group_member")
        root = operational_checkpoints.alias("group_root")
        decision = operational_events.alias("group_decision")
        newer = operational_events.alias("newer_decision")
        decision_types = ("WILL_DECIDED", "PROACTIVE_EXPRESSION_PREPARED")
        query = sa.select(root.c.thread_id, root.c.revision, decision.c.payload,
            decision.c.payload_sha256).select_from(child.join(member, sa.and_(
                child.c.tenant_id == member.c.tenant_id, child.c.installation_id == member.c.installation_id,
                child.c.thread_id == member.c.thread_id, child.c.revision == member.c.sequence,
            )).join(root, sa.and_(
                root.c.tenant_id == child.c.tenant_id, root.c.installation_id == child.c.installation_id,
                root.c.thread_id == member.c.payload["data"]["group_thread_id"].astext,
            )).join(decision, sa.and_(
                decision.c.tenant_id == root.c.tenant_id, decision.c.installation_id == root.c.installation_id,
                decision.c.thread_id == root.c.thread_id,
                decision.c.event_type.in_(decision_types),
            ))).where(
                child.c.tenant_id == scope.tenant_id, child.c.installation_id == scope.installation_id,
                child.c.phase.in_(("will_grouped", "will_group_decided")),
                root.c.phase.in_(("will_decided", "expression_pending", "expression_claimed",
                                  "expression_delivered", "expression_ambiguous")),
                root.c.updated_at <= now,
                sa.func.coalesce(member.c.payload["data"]["group_decision_revision"].astext, "")
                    != decision.c.payload["checkpoint"]["revision"].astext,
                ~sa.exists(sa.select(1).where(
                    newer.c.tenant_id == root.c.tenant_id, newer.c.installation_id == root.c.installation_id,
                    newer.c.thread_id == root.c.thread_id, newer.c.event_type.in_(decision_types),
                    newer.c.sequence > decision.c.sequence,
                )),
            ).distinct().order_by(root.c.thread_id).limit(16)
        with self._timeline._engine.begin() as connection:
            self._timeline._scope(connection)
            candidates = connection.execute(query).mappings().all()
        for candidate in candidates:
            payload = candidate["payload"]
            if canonical_sha256(payload) != bytes(candidate["payload_sha256"]).hex():
                raise RuntimeError("historical group decision hash differs")
            disposition = payload["data"].get("disposition")
            if disposition not in {"silence", "respond"}:
                raise RuntimeError("historical group decision is malformed")
            original = self._event_from_thread(candidate["thread_id"])
            if original.event_type != "historical_observation_batch":
                raise RuntimeError("historical group root is malformed")
            with self._timeline._engine.begin() as connection:
                self._timeline._scope(connection)
                revision = connection.execute(sa.select(operational_checkpoints.c.revision).where(
                    operational_checkpoints.c.tenant_id == scope.tenant_id,
                    operational_checkpoints.c.installation_id == scope.installation_id,
                    operational_checkpoints.c.thread_id == candidate["thread_id"],
                ).with_for_update()).scalar_one()
                if revision != candidate["revision"]:
                    continue  # Root advanced after inspection; retry the read later.
                self._complete_group_members(connection, original,
                    group_thread_id=candidate["thread_id"],
                    decision_revision=payload["checkpoint"]["revision"], disposition=disposition, now=now)

    def claim_next_expression(
        self, *, now: datetime
    ) -> ProactiveExpressionClaim | None:
        states = self._timeline.list_checkpoints(
            thread_prefix=self._decision_prefix,
            phases=("expression_pending", "expression_claimed"),
            statuses=("completed", "active", "failed"),
            updated_before=now,
            limit=64,
        )
        for state in reversed(states):
            if state.phase == "expression_claimed" and state.status == "active":
                if state.deadline is None or state.deadline > now:
                    continue
                fingerprint = hashlib.sha256(
                    b"proactive expression acceptance is ambiguous"
                ).hexdigest()
                try:
                    self._timeline.transition(
                        thread_id=state.thread_id,
                        expected_revision=state.revision,
                        checkpoint_id=f"{state.thread_id}:expression-ambiguous",
                        operation_id=state.operation_id,
                        phase="expression_ambiguous",
                        status="needs_help",
                        event_type="PROACTIVE_EXPRESSION_AMBIGUOUS",
                        occurred_at=now,
                        data={
                            "reason": (
                                "transport acceptance cannot be proven after "
                                "claim expiry"
                            )
                        },
                        retry_count=3,
                        last_error_fingerprint=fingerprint,
                    )
                except CheckpointConflict:
                    pass
                continue
            if state.status == "failed" and state.retry_count >= 3:
                continue
            if self._household_binding is not None:
                self._validate_household_expression(state.thread_id, self._household_binding.to_payload())
                event = self._event_from_thread(state.thread_id)
                if now - event.occurred_at > timedelta(minutes=15):
                    try:
                        self._timeline.transition(thread_id=state.thread_id,
                            expected_revision=state.revision,
                            checkpoint_id=f"{state.thread_id}:expression-expired",
                            operation_id=state.operation_id, phase="expression_expired",
                            status="completed", event_type="PROACTIVE_EXPRESSION_EXPIRED",
                            occurred_at=now, data={"reason": "household opportunity expired"})
                    except CheckpointConflict:
                        pass
                    continue
            prepared = self._prepared_expression(state.thread_id)
            event = self._event_from_thread(state.thread_id, latest=True)
            if self._requires_revalidation(event, now=now):
                try:
                    self._require_revalidation(
                        SensoryDecisionClaim(
                            event, state.thread_id, state.operation_id or event.source_event_id,
                            state.revision, state.retry_count,
                        ), now=now,
                    )
                except CheckpointConflict:
                    pass
                continue
            try:
                claimed = self._timeline.transition(
                    thread_id=state.thread_id,
                    expected_revision=state.revision,
                    checkpoint_id=(
                        f"{state.thread_id}:expression-claimed:{state.revision + 1}"
                    ),
                    operation_id=state.operation_id,
                    phase="expression_claimed",
                    status="active",
                    event_type="PROACTIVE_EXPRESSION_CLAIMED",
                    occurred_at=now,
                    deadline=now + timedelta(seconds=self._decision_lease_seconds),
                    data={
                        "source_event_id": prepared["source_event_id"],
                        "urgency": prepared["urgency"],
                    },
                    retry_count=state.retry_count,
                    last_error_fingerprint=state.last_error_fingerprint,
                )
            except CheckpointConflict:
                continue
            return ProactiveExpressionClaim(
                state.thread_id,
                state.operation_id or str(prepared["source_event_id"]),
                claimed.revision,
                claimed.retry_count,
                str(prepared["source_event_id"]),
                str(prepared["expression"]),
                int(prepared["urgency"]),
            )
        return None

    def household_notifications_since(self, since: datetime) -> int:
        if self._household_binding is None:
            raise ValueError("household notification count requires shared storage")
        scope = self._timeline._scope_value
        e = operational_events.c
        with self._timeline._engine.begin() as connection:
            self._timeline._scope(connection)
            return connection.scalar(sa.select(sa.func.count()).select_from(operational_events).where(
                e.tenant_id == scope.tenant_id, e.installation_id == scope.installation_id,
                e.thread_id.startswith("sensory:household-initiative:"),
                e.event_type == "PROACTIVE_EXPRESSION_OFFERED", e.occurred_at >= since))

    def _validate_household_expression(self, thread_id, binding):
        if (self._household_binding is None
                or binding != self._household_binding.to_payload()
                or not thread_id.startswith(self._decision_prefix)):
            raise ValueError("household expression storage audience differs")
        self._initiative_authorizer()
        event = self._event_from_thread(thread_id)
        if event != self._household_binding.event(event.occurred_at):
            raise ValueError("household expression origin differs")
        history = self._timeline.events(thread_id)
        prepared = next((item.payload.get("data", {}) for item in reversed(history)
                         if item.event_type == "PROACTIVE_EXPRESSION_PREPARED"), {})
        if prepared.get("household_binding") != binding:
            raise ValueError("household expression preparation audience differs")

    def claim_next_household_expression(self, *, binding, now):
        if self._household_binding is None or binding != self._household_binding.to_payload():
            raise ValueError("household expression binding differs")
        self._initiative_authorizer()
        return self.claim_next_expression(now=now)

    def authorize_household_expression(self, claim, *, binding, now):
        self._validate_household_expression(claim.thread_id, binding)
        state = self._timeline.load(claim.thread_id)
        prepared = self._prepared_expression(claim.thread_id)
        event = self._event_from_thread(claim.thread_id)
        if (state.phase != "expression_claimed" or state.status != "active"
                or state.revision != claim.revision or state.deadline is None
                or state.deadline <= now or now - event.occurred_at > timedelta(minutes=15)
                or prepared["source_event_id"] != claim.source_event_id
                or prepared["expression"] != claim.text):
            raise CheckpointConflict("household expression claim is stale or differs")

    def acknowledge_household_expression(self, claim, *, binding, disposition,
                                         provider_message_id, now):
        if disposition not in {"offered", "interrupted"}:
            raise ValueError("household expression receipt disposition is invalid")
        # A receipt records the attempted delivery even when authority expired afterwards.
        if self._household_binding is None or binding != self._household_binding.to_payload():
            raise ValueError("household expression receipt binding differs")
        if not claim.thread_id.startswith(self._decision_prefix):
            raise ValueError("household expression receipt origin differs")
        if disposition == "offered" and (not isinstance(provider_message_id, str)
                or not provider_message_id.strip() or len(provider_message_id) > 240):
            raise ValueError("household expression receipt is invalid")
        self._timeline.transition(thread_id=claim.thread_id,
            expected_revision=claim.revision,
            checkpoint_id=f"{claim.thread_id}:expression-{disposition}",
            operation_id=claim.operation_id, phase="expression_" + disposition,
            status="completed", event_type="PROACTIVE_EXPRESSION_" + disposition.upper(),
            occurred_at=now, data={"channel": "home_assistant", "disposition": disposition,
                "household_binding": binding, "source_event_id": claim.source_event_id,
                "provider_message_id": provider_message_id}, retry_count=0,
            last_error_fingerprint=None)

    def acknowledge_expression(
        self,
        claim: ProactiveExpressionClaim,
        *,
        channel: str,
        provider_message_id: str,
        now: datetime,
    ) -> None:
        if self._household_binding is not None or channel not in {"telegram", "test"}:
            raise ValueError("proactive expression channel is invalid")
        if not provider_message_id.strip() or len(provider_message_id) > 240:
            raise ValueError("proactive expression receipt is invalid")
        self._timeline.transition(
            thread_id=claim.thread_id,
            expected_revision=claim.revision,
            checkpoint_id=f"{claim.thread_id}:expression-delivered",
            operation_id=claim.operation_id,
            phase="expression_delivered",
            status="completed",
            event_type="PROACTIVE_EXPRESSION_DELIVERED",
            occurred_at=now,
            data={
                "channel": channel,
                "provider_message_id": provider_message_id,
                "source_event_id": claim.source_event_id,
            },
            retry_count=0,
            last_error_fingerprint=None,
        )

    def acknowledge_expression_receipt(
        self,
        *,
        thread_id: str,
        revision: int,
        channel: str,
        provider_message_id: str,
        now: datetime,
    ) -> None:
        state = self._timeline.load(thread_id)
        if (
            state.phase != "expression_claimed"
            or state.status != "active"
            or state.revision != revision
        ):
            raise CheckpointConflict("proactive expression claim is stale")
        prepared = self._prepared_expression(thread_id)
        claim = ProactiveExpressionClaim(
            thread_id,
            state.operation_id or str(prepared["source_event_id"]),
            state.revision,
            state.retry_count,
            str(prepared["source_event_id"]),
            str(prepared["expression"]),
            int(prepared["urgency"]),
        )
        self.acknowledge_expression(
            claim,
            channel=channel,
            provider_message_id=provider_message_id,
            now=now,
        )

    def fail_expression(
        self, claim: ProactiveExpressionClaim, error: Exception, *, now: datetime
    ) -> None:
        error_text = f"{type(error).__name__}: {error}"
        self._timeline.transition(
            thread_id=claim.thread_id,
            expected_revision=claim.revision,
            checkpoint_id=f"{claim.thread_id}:expression-ambiguous",
            operation_id=claim.operation_id,
            phase="expression_ambiguous",
            status="needs_help",
            event_type="PROACTIVE_EXPRESSION_AMBIGUOUS",
            occurred_at=now,
            data={"reason": error_text},
            retry_count=3,
            last_error_fingerprint=hashlib.sha256(error_text.encode()).hexdigest(),
        )

    def fail(
        self,
        claim: SensoryDecisionClaim,
        error: Exception,
        *,
        now: datetime,
    ) -> None:
        state = self._timeline.load(claim.thread_id)
        if isinstance(error, AutonomousUsageUncertain) or state.phase == "will_model_started":
            # A new request cannot recover the missing measurement of this one.
            # Keep the uncertainty durable instead of spending another attempt.
            self._timeline.transition(
                thread_id=claim.thread_id,
                expected_revision=claim.revision,
                checkpoint_id=f"{claim.thread_id}:usage-uncertain:{claim.revision + 1}",
                operation_id=claim.operation_id,
                phase="will_usage_uncertain", status="needs_help",
                event_type="WILL_USAGE_UNCERTAIN", occurred_at=now,
                data={"reason": "autonomous_provider_usage_unverified", "usage": None,
                      "source_event_id": claim.event.source_event_id,
                      # A dispatch marker precedes context preparation. It does
                      # not prove that the provider was called or that missing
                      # usage caused the failure. Retain the actual exception
                      # without persisting potentially private exception text.
                      "failure_type": type(error).__name__,
                      "failure_fingerprint": hashlib.sha256(
                          f"{type(error).__name__}: {error}".encode()
                      ).hexdigest(),
                      "provider_usage_error": isinstance(error, AutonomousUsageUncertain)},
                retry_count=claim.retry_count + 1,
                last_error_fingerprint=hashlib.sha256(b"autonomous_provider_usage_unverified").hexdigest(),
            )
            return
        self._timeline.record_recovery_failure(
            thread_id=claim.thread_id,
            expected_revision=claim.revision,
            checkpoint_id=f"{claim.thread_id}:will-failed:{claim.retry_count + 1}",
            operation_id=claim.operation_id,
            error_text=f"{type(error).__name__}: {error}",
            occurred_at=now,
        )

    def _event_from_thread(self, thread_id: str, *, latest: bool = False) -> SensoryEvent:
        events = self._timeline.events(thread_id)
        if not events or events[0].event_type != "SENSORY_EVENT_OBSERVED":
            raise RuntimeError("sensory event admission is missing")
        observation = events[0]
        if latest:
            observation = next(
                (event for event in reversed(events) if event.event_type == "SENSORY_EVENT_REVALIDATED"),
                observation,
            )
        data = observation.payload.get("data")
        if not isinstance(data, dict):
            raise RuntimeError("sensory event admission is malformed")
        payload = data.get("payload")
        source = data.get("source")
        source_event_id = data.get("source_event_id")
        event_type = data.get("event_type")
        if (
            not isinstance(payload, dict)
            or not isinstance(source, str)
            or not isinstance(source_event_id, str)
            or not isinstance(event_type, str)
        ):
            raise RuntimeError("sensory event fields are malformed")
        return SensoryEvent(
            source,
            source_event_id,
            event_type,
            observation.occurred_at,
            payload,
        )

    def _prepared_expression(self, thread_id: str) -> dict[str, object]:
        for event in reversed(self._timeline.events(thread_id)):
            if event.event_type != "PROACTIVE_EXPRESSION_PREPARED":
                continue
            data = event.payload.get("data")
            if not isinstance(data, dict):
                break
            expression = data.get("expression")
            source_event_id = data.get("source_event_id")
            urgency = data.get("urgency")
            if (
                isinstance(expression, str)
                and expression.strip()
                and isinstance(source_event_id, str)
                and isinstance(urgency, int)
                and not isinstance(urgency, bool)
            ):
                return {
                    "expression": expression,
                    "source_event_id": source_event_id,
                    "urgency": urgency,
                }
            break
        raise RuntimeError("proactive expression preparation is missing")

    def _cooldown_applies(
        self,
        claim: SensoryDecisionClaim,
        decision: FreeWillDecision,
        *,
        now: datetime,
    ) -> bool:
        states = self._timeline.list_checkpoints(
            thread_prefix=self._decision_prefix,
            phases=("expression_pending", "expression_claimed", "expression_delivered"),
            statuses=("completed", "active", "failed"),
            updated_before=now,
            limit=64,
        )
        entity_id = claim.event.payload.get("entity_id")
        for state in states:
            age = now - state.updated_at
            if age > timedelta(minutes=30):
                continue
            try:
                self._prepared_expression(state.thread_id)
            except RuntimeError:
                continue
            events = self._timeline.events(state.thread_id)
            prepared_data = next(
                (
                    event.payload.get("data")
                    for event in reversed(events)
                    if event.event_type == "PROACTIVE_EXPRESSION_PREPARED"
                ),
                None,
            )
            prior_entity = (
                prepared_data.get("entity_id")
                if isinstance(prepared_data, dict)
                else None
            )
            if entity_id is not None and entity_id == prior_entity:
                return True
            if decision.urgency < 3 and age <= timedelta(minutes=5):
                return True
        return False


__all__ = ["PostgresSensoryEventStore"]
