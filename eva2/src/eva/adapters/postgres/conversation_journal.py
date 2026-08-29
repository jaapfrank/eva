"""PostgreSQL-backed active conversation history for the Living Core."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    CheckpointState,
    PostgresOperationalTimeline,
    TimelineScope,
    operational_events,
)
from eva.application.conversation import (
    ConversationConflict,
    ConversationModelUsage,
    ConversationTurn,
    GroundedReply,
    RecentConversationMessage,
)


class PostgresConversationJournal:
    """Commit raw chat events before semantic projection or model generation."""

    def __init__(self, engine: sa.Engine, scope: TimelineScope) -> None:
        self._engine = engine
        self._scope_value = scope
        self._timeline = PostgresOperationalTimeline(engine, scope)

    @staticmethod
    def _timestamp(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def admit(self, turn: ConversationTurn) -> CheckpointState:
        thread_id = self._thread_id(turn)
        data = self._ingress_data(turn)
        try:
            return self._timeline.create_thread(
                thread_id=thread_id,
                checkpoint_id=f"conversation:{turn.message_id}:ingress",
                occurred_at=self._timestamp(turn.occurred_at),
                data=data,
            )
        except CheckpointConflict:
            events = self._timeline.events(thread_id)
            if not events or events[0].event_type != "INGRESS_COMMITTED":
                raise ConversationConflict(
                    "conversation replay has no durable ingress"
                ) from None
            payload = events[0].payload
            durable = payload.get("data") if isinstance(payload, dict) else None
            if durable != data or events[0].occurred_at != self._timestamp(
                turn.occurred_at
            ):
                raise ConversationConflict(
                    "conversation message identity was reused with different ingress"
                ) from None
            return self._timeline.load(thread_id)

    def commit_reply(
        self, turn: ConversationTurn, reply: GroundedReply
    ) -> GroundedReply:
        existing = self.committed_reply(turn)
        if existing is not None:
            self._require_same_reply(existing, reply)
            return existing
        state = self._timeline.load(self._thread_id(turn))
        try:
            self._timeline.transition(
                thread_id=state.thread_id,
                expected_revision=state.revision,
                checkpoint_id=f"conversation:{turn.message_id}:reply",
                phase="reply_committed",
                status="completed",
                event_type="REPLY_COMMITTED",
                occurred_at=self._timestamp(reply.generated_at),
                data=self._reply_data(turn, reply),
            )
            return reply
        except CheckpointConflict:
            existing = self.committed_reply(turn)
            if existing is None:
                raise ConversationConflict(
                    "conversation reply commit raced without a durable reply"
                ) from None
            self._require_same_reply(existing, reply)
            return existing

    def committed_reply(self, turn: ConversationTurn) -> GroundedReply | None:
        for event in reversed(self._timeline.events(self._thread_id(turn))):
            if event.event_type != "REPLY_COMMITTED":
                continue
            payload = event.payload
            data = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict) or data.get("kind") != "conversation_reply":
                raise ConversationConflict("durable conversation reply is malformed")
            if (
                data.get("conversation_id") != turn.conversation_id
                or data.get("message_id") != turn.message_id
                or data.get("principal_id") != turn.principal_id
                or data.get("project_id") != turn.project_id
            ):
                raise ConversationConflict("durable conversation reply scope differs")
            evidence = data.get("evidence_memory_ids")
            text = data.get("text")
            provider = data.get("provider")
            if (
                not isinstance(evidence, list)
                or not all(isinstance(item, str) for item in evidence)
                or not isinstance(text, str)
                or not text
                or not isinstance(provider, str)
                or not provider
            ):
                raise ConversationConflict(
                    "durable conversation reply fields are invalid"
                )
            try:
                usage = (
                    ConversationModelUsage.unmeasured()
                    if "model_usage" not in data
                    else ConversationModelUsage.from_wire(data["model_usage"])
                )
            except ValueError as error:
                raise ConversationConflict(
                    "durable conversation reply usage is invalid"
                ) from error
            reply = GroundedReply(
                conversation_id=turn.conversation_id,
                message_id=turn.message_id,
                text=text,
                evidence_memory_ids=tuple(evidence),
                generated_at=event.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                provider=provider,
                model_usage=usage,
            )
            if data.get("text_sha256") != reply.text_sha256:
                raise ConversationConflict("durable conversation reply hash differs")
            return reply
        return None

    def load(self, turn: ConversationTurn) -> CheckpointState:
        return self._timeline.load(f"conversation:{turn.message_id}")

    def memory_queued(
        self,
        turn: ConversationTurn,
        completed: CheckpointState,
        *,
        operation_document_id: str,
    ) -> CheckpointState:
        data = {
            "conversation_id": turn.conversation_id,
            "document_id": operation_document_id,
            "kind": "memory_retain_queued",
            "message_id": turn.message_id,
            "principal_id": turn.principal_id,
        }
        if turn.project_id is not None:
            data["project_id"] = turn.project_id
        for event in reversed(self._timeline.events(completed.thread_id)):
            if event.event_type != "MEMORY_RETAIN_QUEUED":
                continue
            payload = event.payload
            durable = payload.get("data") if isinstance(payload, dict) else None
            if durable != data:
                raise ConversationConflict("memory retention replay differs")
            return self._timeline.load(completed.thread_id)
        current = self._timeline.load(completed.thread_id)
        try:
            return self._timeline.transition(
                thread_id=current.thread_id,
                expected_revision=current.revision,
                checkpoint_id=f"conversation:{turn.message_id}:memory-queued",
                phase="memory_queued",
                status="completed",
                event_type="MEMORY_RETAIN_QUEUED",
                occurred_at=self._timestamp(turn.occurred_at),
                data=data,
            )
        except CheckpointConflict:
            for event in reversed(self._timeline.events(completed.thread_id)):
                if event.event_type != "MEMORY_RETAIN_QUEUED":
                    continue
                payload = event.payload
                durable = payload.get("data") if isinstance(payload, dict) else None
                if durable == data:
                    return self._timeline.load(completed.thread_id)
            raise ConversationConflict(
                "memory retention commit raced without a matching event"
            ) from None

    def memory_skipped(
        self,
        turn: ConversationTurn,
        completed: CheckpointState,
        *,
        operation_document_id: str,
        reason: str,
    ) -> CheckpointState:
        """Record a durable, content-free quota refusal for one completed turn."""

        if reason not in {
            "person_record_limit_reached",
            "person_byte_limit_reached",
        }:
            raise ConversationConflict("memory retention refusal reason is invalid")
        data = {
            "conversation_id": turn.conversation_id,
            "document_id": operation_document_id,
            "kind": "memory_retain_skipped",
            "message_id": turn.message_id,
            "principal_id": turn.principal_id,
            "reason": reason,
        }
        if turn.project_id is not None:
            data["project_id"] = turn.project_id
        for event in reversed(self._timeline.events(completed.thread_id)):
            if event.event_type != "MEMORY_RETAIN_SKIPPED":
                continue
            payload = event.payload
            durable = payload.get("data") if isinstance(payload, dict) else None
            if durable != data:
                raise ConversationConflict("memory retention refusal replay differs")
            return self._timeline.load(completed.thread_id)
        current = self._timeline.load(completed.thread_id)
        try:
            return self._timeline.transition(
                thread_id=current.thread_id,
                expected_revision=current.revision,
                checkpoint_id=f"conversation:{turn.message_id}:memory-skipped",
                phase="memory_skipped",
                status="completed",
                event_type="MEMORY_RETAIN_SKIPPED",
                occurred_at=self._timestamp(turn.occurred_at),
                data=data,
            )
        except CheckpointConflict:
            for event in reversed(self._timeline.events(completed.thread_id)):
                if event.event_type != "MEMORY_RETAIN_SKIPPED":
                    continue
                payload = event.payload
                durable = payload.get("data") if isinstance(payload, dict) else None
                if durable == data:
                    return self._timeline.load(completed.thread_id)
            raise ConversationConflict(
                "memory retention refusal raced without a matching event"
            ) from None

    @staticmethod
    def _thread_id(turn: ConversationTurn) -> str:
        return f"conversation:{turn.message_id}"

    @staticmethod
    def _ingress_data(turn: ConversationTurn) -> dict[str, Any]:
        data = {
            "conversation_id": turn.conversation_id,
            "kind": "conversation_ingress",
            "message_id": turn.message_id,
            "principal_id": turn.principal_id,
            "text": turn.text,
        }
        if turn.project_id is not None:
            data["project_id"] = turn.project_id
        if turn.observation_binding is not None:
            binding = turn.observation_binding
            data["observation_binding"] = {
                "binding_sha256": binding.binding_sha256,
                "media_sha256": binding.media_sha256,
                "media_type": binding.media_type,
                "observation_id": binding.observation_id,
                "source_ref": binding.source_ref,
                "subject_region_sha256": binding.subject_region_sha256,
            }
        return data

    @staticmethod
    def _reply_data(turn: ConversationTurn, reply: GroundedReply) -> dict[str, Any]:
        data = {
            "conversation_id": turn.conversation_id,
            "evidence_memory_ids": list(reply.evidence_memory_ids),
            "kind": "conversation_reply",
            "message_id": turn.message_id,
            "model_usage": reply.model_usage.to_wire(),
            "principal_id": turn.principal_id,
            "provider": reply.provider,
            "text": reply.text,
            "text_sha256": reply.text_sha256,
        }
        if turn.project_id is not None:
            data["project_id"] = turn.project_id
        return data

    @staticmethod
    def _require_same_reply(durable: GroundedReply, candidate: GroundedReply) -> None:
        if durable != candidate:
            raise ConversationConflict(
                "conversation replay produced a different reply than the durable result"
            )

    def recent(
        self,
        turn: ConversationTurn,
        *,
        limit: int = 12,
        exclude_message_id: str | None = None,
    ) -> tuple[RecentConversationMessage, ...]:
        if not 1 <= limit <= 40:
            raise ValueError("recent conversation limit must be 1..40")
        data = operational_events.c.payload["data"]
        with self._engine.connect() as connection:
            transaction = connection.begin()
            try:
                self._scope(connection)
                statement = (
                    sa.select(
                        operational_events.c.event_type,
                        operational_events.c.occurred_at,
                        operational_events.c.payload,
                    )
                    .where(
                        operational_events.c.tenant_id == self._scope_value.tenant_id,
                        operational_events.c.installation_id
                        == self._scope_value.installation_id,
                        operational_events.c.event_type.in_(
                            ["INGRESS_COMMITTED", "REPLY_COMMITTED"]
                        ),
                        data["conversation_id"].astext == turn.conversation_id,
                        data["principal_id"].astext == turn.principal_id,
                    )
                    .order_by(
                        operational_events.c.occurred_at.desc(),
                        operational_events.c.thread_id.desc(),
                        operational_events.c.sequence.desc(),
                    )
                    .limit(limit + 2)
                )
                if turn.project_id is None:
                    statement = statement.where(data["project_id"].astext.is_(None))
                else:
                    statement = statement.where(
                        data["project_id"].astext == turn.project_id
                    )
                rows = connection.execute(statement).all()
            finally:
                transaction.rollback()
        messages: list[RecentConversationMessage] = []
        for row in reversed(rows):
            payload = row.payload
            value: Any = payload.get("data") if isinstance(payload, dict) else None
            if not isinstance(value, dict):
                continue
            if exclude_message_id and value.get("message_id") == exclude_message_id:
                continue
            text = value.get("text")
            if not isinstance(text, str) or not text:
                continue
            messages.append(
                RecentConversationMessage(
                    role="Jaap" if row.event_type == "INGRESS_COMMITTED" else "Eva",
                    text=text,
                    occurred_at=row.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                )
            )
        return tuple(messages[-limit:])

    def _scope(self, connection: sa.Connection) -> None:
        connection.exec_driver_sql("SET LOCAL ROLE eva_runtime")
        connection.execute(
            sa.text("SELECT set_config('eva.tenant_id', :value, true)"),
            {"value": str(self._scope_value.tenant_id)},
        )
        connection.execute(
            sa.text("SELECT set_config('eva.installation_id', :value, true)"),
            {"value": str(self._scope_value.installation_id)},
        )


__all__ = ["PostgresConversationJournal"]
