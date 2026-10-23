"""Deliver newly completed owner initiatives through the existing private transport.

Delivery is a host completion receipt, not an initiative model capability.
Uncertain sends are never automatically retried.
"""
from datetime import UTC, datetime
import hashlib

import sqlalchemy as sa

from eva.application.free_will import ProactiveExpressionClaim
from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline, insert_initial_timeline_thread,
    operational_checkpoints, operational_events, transition_timeline_thread,
)


class InitiativeCompletionDelivery:
    def __init__(self, owner_store, delivery, authorize):
        self._store = owner_store
        self._delivery = delivery
        self._authorize = authorize

    def run_once(self):
        now = datetime.now(UTC).replace(microsecond=0)
        self._authorize()
        store = self._store
        scope = store._scope
        activation_id = "initiative-delivery:" + str(store._owner)
        with store._engine.begin() as conn:
            store._authorize(conn, now)
            # Serialize selection without holding a database lock across Telegram.
            lock_id = int.from_bytes(hashlib.sha256(
                (str(scope) + activation_id).encode()).digest()[:8], "big", signed=True)
            if not conn.scalar(sa.select(sa.func.pg_try_advisory_xact_lock(lock_id))):
                return "idle"
            c, e = operational_checkpoints.c, operational_events.c
            activation = conn.execute(sa.select(operational_checkpoints).where(
                c.tenant_id == scope.tenant_id, c.installation_id == scope.installation_id,
                c.thread_id == activation_id)).one_or_none()
            if activation is None:
                insert_initial_timeline_thread(conn, scope, thread_id=activation_id,
                    checkpoint_id=activation_id + ":enabled", occurred_at=now,
                    phase="enabled", status="completed", event_type="INITIATIVE_DELIVERY_ENABLED",
                    data={"owner_principal_id": str(store._owner),
                          "historical_results_excluded": True})
                return "enabled"
            completion = operational_events.alias("completion")
            notice = operational_checkpoints.alias("notice")
            # Private self-chosen work already returns to the existing source-
            # aware review. That review decides silence/contact; an unconditional
            # completion notice would bypass its timing and feedback decision.
            base = store._query()
            adoption = base.selected_columns.adoption["data"]
            review = operational_events.alias("private_review_source")
            private_prefix = "sensory:initiative-review:" + hashlib.sha256(
                str(store._owner).encode()).hexdigest()[:24] + ":"
            reviewed_autonomous = sa.exists(sa.select(1).where(
                review.c.tenant_id == scope.tenant_id,
                review.c.installation_id == scope.installation_id,
                review.c.thread_id == adoption["source_thread_id"].astext,
                review.c.thread_id.startswith(private_prefix),
                review.c.sequence == 1,
                review.c.event_type == "SENSORY_EVENT_OBSERVED",
                review.c.payload["data"]["source"].astext == "initiative-review",
                review.c.payload["data"]["event_type"].astext == "private_opportunity_review",
                review.c.payload["data"]["source_event_id"].astext == adoption["source_event_id"].astext))
            query = base.where(~reviewed_autonomous).join(completion, sa.and_(
                completion.c.tenant_id == c.tenant_id,
                completion.c.installation_id == c.installation_id,
                completion.c.thread_id == c.thread_id,
                completion.c.event_type == "PERSONAL_INITIATIVE_COMPLETED",
            )).where(
                c.phase == "initiative_completed",
                completion.c.occurred_at > activation.updated_at,
                ~sa.exists(sa.select(1).select_from(notice).where(
                    notice.c.tenant_id == scope.tenant_id,
                    notice.c.installation_id == scope.installation_id,
                    notice.c.thread_id == sa.literal("initiative-notice:") + c.thread_id,
                )),
            ).order_by(completion.c.occurred_at, c.thread_id).limit(1)
            row = conn.execute(query).first()
            if row is None:
                return "idle"
            artifact = store._artifact(conn, row.thread_id)
            if artifact is None:
                return "idle"
            title = str(row.adoption["data"]["title"])[:250]
            content = artifact["content"]
            words = content.split()
            preview = " ".join(words[:90])[:1200]
            text = f"Ik heb mijn initiatief ‘{title}’ afgerond:\n\n{preview}"
            if len(words) > 90 or len(" ".join(words[:90])) > 1200:
                text += "\n\nDe volledige uitwerking is bewaard en beschikbaar op verzoek."
            thread_id = "initiative-notice:" + row.thread_id
            insert_initial_timeline_thread(conn, scope, thread_id=thread_id,
                checkpoint_id=thread_id + ":attempt", occurred_at=now,
                phase="delivery_attempted", status="needs_help",
                event_type="INITIATIVE_RESULT_DELIVERY_ATTEMPTED",
                operation_id=row.operation_id,
                data={"intent_id": row.adoption["data"]["intent_id"],
                      "artifact_sha256": artifact["sha256"],
                      "owner_principal_id": str(store._owner),
                      "automatic_retry": False})
        timeline = PostgresOperationalTimeline(store._engine, scope)
        try:
            self._authorize()
            # Recheck owner binding after reservation, immediately before transport.
            with store._engine.begin() as conn:
                store._authorize(conn, datetime.now(UTC).replace(microsecond=0))
            receipt = self._delivery.deliver(ProactiveExpressionClaim(
                thread_id=thread_id, operation_id=thread_id, revision=1,
                retry_count=0, source_event_id=row.thread_id, text=text, urgency=1))
        except Exception as error:
            timeline.transition(thread_id=thread_id, expected_revision=1,
                checkpoint_id=thread_id + ":uncertain", phase="delivery_uncertain",
                status="needs_help", event_type="INITIATIVE_RESULT_DELIVERY_UNCERTAIN",
                occurred_at=datetime.now(UTC).replace(microsecond=0),
                data={"error_type": type(error).__name__, "automatic_retry": False})
            return "delivery_uncertain"
        timeline.transition(thread_id=thread_id, expected_revision=1,
            checkpoint_id=thread_id + ":delivered", phase="delivered", status="completed",
            event_type="INITIATIVE_RESULT_DELIVERED",
            occurred_at=datetime.now(UTC).replace(microsecond=0),
            data={"provider_receipt": receipt, "channel": "telegram_private"})
        return "delivered"
