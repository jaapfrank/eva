"""PostgreSQL-backed active conversation history for the Living Core."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import re
from typing import Any
from uuid import uuid4

import sqlalchemy as sa

from eva.adapters.postgres.operational_timeline import (
    CheckpointConflict,
    CheckpointState,
    PostgresOperationalTimeline,
    TimelineScope,
    operational_events,
    operational_checkpoints,
    insert_initial_timeline_thread,
)
from eva.adapters.postgres.scope import PostgresScopedRole, try_execution_lock
from eva.application.conversation import (
    ConversationConflict,
    ConversationExecutionLimitReached,
    ConversationStopped,
    ConversationModelTiming,
    ConversationModelUsage,
    ConversationTurn,
    GroundedReply,
    RecentConversationMessage,
)
from eva.contracts.canonical_json import canonical_json_bytes, canonical_sha256


_SHA256 = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class ConversationContentErasureReceipt:
    """Non-content proof that one raw message exchange was redacted."""

    operation_sha256: str
    principal_sha256: str
    message_sha256: str
    erased_at: str
    redacted_event_count: int
    replayed: bool


class PostgresConversationJournal(PostgresScopedRole):
    """Commit raw chat events before semantic projection or model generation."""

    _role = "eva_runtime"

    def __init__(self, engine: sa.Engine, scope: TimelineScope, *, maximum_model_requests: int = 32) -> None:
        if type(maximum_model_requests) is not int or maximum_model_requests < 1:
            raise ValueError("conversation request limit must be a positive integer")
        self._maximum_model_requests = maximum_model_requests
        self._engine = engine
        self._scope_value = scope
        self._timeline = PostgresOperationalTimeline(engine, scope)

    @staticmethod
    def _timestamp(value: str) -> datetime:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    @contextmanager
    def execution(self, turn: ConversationTurn) -> Iterator[None]:
        # Match the journal's message identity. Even a conflicting principal or
        # changed ingress must not create a parallel run for this message slot.
        key = canonical_sha256({
            "tenant_id": str(self._scope_value.tenant_id),
            "installation_id": str(self._scope_value.installation_id),
            "thread_id": self._thread_id(turn),
        })
        with try_execution_lock(self._engine, f"eva:conversation:{key}") as acquired:
            if not acquired:
                raise ConversationConflict("conversation message is already running")
            yield

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

    @staticmethod
    def _stop_identity(principal_id: str, conversation_id: str, message_id: str) -> dict[str, str]:
        return {
            "principal_id": principal_id,
            "conversation_id": conversation_id,
            "message_id": message_id,
        }

    @staticmethod
    def _stop_thread_id(identity: dict[str, str]) -> str:
        return f"conversation-stop:{canonical_sha256(identity)}"

    def _stop_fence(self, connection: sa.Connection, identity: dict[str, str]) -> None:
        key = canonical_sha256({
            "scope": [str(self._scope_value.tenant_id), str(self._scope_value.installation_id)],
            "turn": identity,
        })
        connection.execute(sa.text(
            "SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"
        ), {"key": f"eva:conversation-stop:{key}"})

    def _marker_exists(
        self, connection: sa.Connection, thread_id: str,
        event_type: str, data: dict[str, str],
    ) -> bool:
        rows = connection.execute(sa.select(
            operational_events.c.event_type, operational_events.c.payload,
        ).where(
            operational_events.c.tenant_id == self._scope_value.tenant_id,
            operational_events.c.installation_id == self._scope_value.installation_id,
            operational_events.c.thread_id == thread_id,
        )).all()
        if not rows:
            return False
        if len(rows) != 1 or rows[0].event_type != event_type or rows[0].payload.get("data") != data:
            raise ConversationConflict("conversation control marker differs")
        return True

    def admit_dispatch(self, turn: ConversationTurn, operation_id: str) -> None:
        """Order step-start admission and stop under the same short transaction lock.

        A step admitted before stop may finish; after stop no new admission is
        possible. The marker is not evidence that an external effect succeeded.
        """
        identity = self._stop_identity(turn.principal_id, turn.conversation_id, turn.message_id)
        with self._engine.begin() as connection:
            self._scope(connection)
            self._stop_fence(connection, identity)
            if self._marker_exists(connection, self._stop_thread_id(identity),
                                   "CONVERSATION_STOP_REQUESTED", identity):
                raise ConversationStopped("conversation has a durable stop request")
            data = {**identity, "operation_id": operation_id}
            thread_id = f"conversation-dispatch:{canonical_sha256(data)}"
            if not self._marker_exists(connection, thread_id, "CONVERSATION_DISPATCH_ADMITTED", data):
                insert_initial_timeline_thread(
                    connection, self._scope_value, thread_id=thread_id,
                    checkpoint_id=f"{thread_id}:admitted", data=data,
                    occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
                    event_type="CONVERSATION_DISPATCH_ADMITTED",
                    phase="dispatch_admitted", status="completed",
                )

    def begin_model_request(self, turn: ConversationTurn) -> str:
        """Persist possible provider work before I/O, ordered against owner stop.

        An admission can crash before reaching the provider. Until a result is
        recorded, both actual dispatch and any provider usage remain uncertain.
        """
        self.admit(turn)
        if self.committed_reply(turn) is not None:
            raise ConversationConflict("conversation already has a reply")
        identity = self._stop_identity(turn.principal_id, turn.conversation_id, turn.message_id)
        request_id = str(uuid4())
        thread_id = f"conversation-model:{request_id}"
        with self._engine.begin() as connection:
            self._scope(connection)
            self._stop_fence(connection, identity)
            if self._marker_exists(connection, self._stop_thread_id(identity),
                                   "CONVERSATION_STOP_REQUESTED", identity):
                raise ConversationStopped("conversation has a durable stop request")
            payload = operational_events.c.payload["data"]
            admitted = connection.execute(sa.select(payload).where(
                operational_events.c.tenant_id == self._scope_value.tenant_id,
                operational_events.c.installation_id == self._scope_value.installation_id,
                operational_events.c.event_type == "CONVERSATION_MODEL_ADMITTED",
                *(payload[key].astext == value for key, value in identity.items()),
            )).scalars().all()
            limits = [item["maximum_requests"] for item in admitted if "maximum_requests" in item]
            if any(type(value) is not int or value < 1 for value in limits):
                raise ConversationConflict("durable model request limit is invalid")
            maximum = min([self._maximum_model_requests, *limits])
            if len(admitted) >= maximum:
                raise ConversationExecutionLimitReached("conversation request limit reached")
            insert_initial_timeline_thread(
                connection, self._scope_value, thread_id=thread_id,
                checkpoint_id=f"{thread_id}:admitted",
                data={**identity, "request_id": request_id, "maximum_requests": maximum},
                occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
                event_type="CONVERSATION_MODEL_ADMITTED", phase="model_admitted",
                status="active",
            )
        return request_id

    def complete_model_request(
        self, turn: ConversationTurn, request_id: str, usage: ConversationModelUsage,
    ) -> None:
        # Recording already admitted work must remain possible after stop.
        identity = self._stop_identity(turn.principal_id, turn.conversation_id, turn.message_id)
        thread_id = f"conversation-model:{request_id}"
        events = self._timeline.events(thread_id)
        admission = {**identity, "request_id": request_id}
        stored_admission = {} if not events else events[0].payload.get("data", {})
        admitted_identity = (None if not isinstance(stored_admission, dict) else
                             {key: value for key, value in stored_admission.items() if key != "maximum_requests"})
        if (not events or events[0].event_type != "CONVERSATION_MODEL_ADMITTED"
                or admitted_identity != admission):
            raise ConversationConflict("model request admission differs")
        data = {**admission, "usage": usage.to_wire()}
        if len(events) > 1:
            if (len(events) != 2 or events[1].event_type != "CONVERSATION_MODEL_RECORDED"
                    or events[1].payload.get("data") != data):
                raise ConversationConflict("model request result differs")
            return
        state = self._timeline.load(thread_id)
        self._timeline.transition(
            thread_id=thread_id, expected_revision=state.revision,
            checkpoint_id=f"{thread_id}:recorded", phase="model_recorded",
            status="completed", event_type="CONVERSATION_MODEL_RECORDED",
            occurred_at=datetime.now(tz=UTC).replace(microsecond=0), data=data,
        )

    def model_usage(self, turn: ConversationTurn) -> ConversationModelUsage:
        """Known totals across restarts; missing results retain explicit uncertainty.

        For unknown requests the attempt count includes one admitted request,
        not a claim about actual provider dispatch or internal retry counts.
        """
        identity = self._stop_identity(turn.principal_id, turn.conversation_id, turn.message_id)
        with self._engine.begin() as connection:
            self._scope(connection)
            data = operational_events.c.payload["data"]
            rows = connection.execute(sa.select(
                operational_events.c.event_type, data.label("data"),
            ).where(
                operational_events.c.tenant_id == self._scope_value.tenant_id,
                operational_events.c.installation_id == self._scope_value.installation_id,
                operational_events.c.event_type.in_((
                    "CONVERSATION_MODEL_ADMITTED", "CONVERSATION_MODEL_RECORDED",
                )),
                *(data[key].astext == value for key, value in identity.items()),
            )).all()
        requests: dict[str, dict[str, Any]] = {}
        for row in rows:
            request_id = row.data.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                raise ConversationConflict("model usage request identity is invalid")
            request = requests.setdefault(request_id, {})
            if row.event_type in request:
                raise ConversationConflict("model usage has duplicate events")
            request[row.event_type] = row.data
        usages = []
        for request in requests.values():
            if "CONVERSATION_MODEL_ADMITTED" not in request:
                raise ConversationConflict("model usage has no admission")
            recorded = request.get("CONVERSATION_MODEL_RECORDED")
            try:
                usage = (ConversationModelUsage.unmeasured() if recorded is None
                         else ConversationModelUsage.from_wire(recorded.get("usage")))
            except ValueError as error:
                raise ConversationConflict("model request usage is invalid") from error
            if usage.measurement == "unmeasured":
                usage = ConversationModelUsage.partial(
                    model_attempts=1, tool_calls=0, input_tokens=0, output_tokens=0,
                )
            usages.append(usage)
        return ConversationModelUsage.aggregate(tuple(usages))

    def request_conversation_stop(
        self, *, principal_id: str, conversation_id: str, request_message_id: str | None = None,
    ) -> int:
        """Snapshot unfinished turns owned by this principal in this conversation.

        New turns admitted after this snapshot remain usable. No browser-supplied
        principal or cross-conversation task identity is used by the runtime.
        """
        snapshot_id = None if request_message_id is None else (
            "conversation-stop-targets:" + canonical_sha256(self._stop_identity(
                principal_id, conversation_id, request_message_id,
            ))
        )
        snapshot = [] if snapshot_id is None else self._timeline.events(snapshot_id)
        if snapshot:
            data = snapshot[0].payload.get("data")
            if (len(snapshot) != 1 or snapshot[0].event_type != "CONVERSATION_STOP_TARGETS"
                    or not isinstance(data, dict) or data.get("principal_id") != principal_id
                    or data.get("conversation_id") != conversation_id
                    or data.get("request_message_id") != request_message_id
                    or not isinstance(data.get("targets"), list)
                    or any(not isinstance(item, str) or not item for item in data["targets"])):
                raise ConversationConflict("conversation stop snapshot differs")
            targets = data["targets"]
        else:
            with self._engine.begin() as connection:
                self._scope(connection)
                data = operational_events.c.payload["data"]
                rows = connection.execute(sa.select(data.label("data")).select_from(
                    operational_events.join(operational_checkpoints, sa.and_(
                        operational_events.c.tenant_id == operational_checkpoints.c.tenant_id,
                        operational_events.c.installation_id == operational_checkpoints.c.installation_id,
                        operational_events.c.thread_id == operational_checkpoints.c.thread_id,
                    ))
                ).where(
                    operational_events.c.tenant_id == self._scope_value.tenant_id,
                    operational_events.c.installation_id == self._scope_value.installation_id,
                    operational_events.c.event_type == "INGRESS_COMMITTED",
                    data["kind"].astext == "conversation_ingress",
                    data["principal_id"].astext == principal_id,
                    data["conversation_id"].astext == conversation_id,
                    operational_checkpoints.c.status == "active",
                    *( () if request_message_id is None else
                       (data["message_id"].astext != request_message_id,) ),
                )).all()
            targets = [row.data.get("message_id") for row in rows]
            if any(not isinstance(item, str) or not item for item in targets):
                raise ConversationConflict("conversation stop target is malformed")
            if snapshot_id is not None:
                with self._engine.begin() as connection:
                    self._scope(connection)
                    insert_initial_timeline_thread(
                        connection, self._scope_value, thread_id=snapshot_id,
                        checkpoint_id=f"{snapshot_id}:targets",
                        occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
                        data={"principal_id": principal_id, "conversation_id": conversation_id,
                              "request_message_id": request_message_id, "targets": sorted(targets)},
                        event_type="CONVERSATION_STOP_TARGETS", phase="targets_snapshotted",
                        status="completed",
                    )
        count = 0
        for message_id in targets:
            identity = self._stop_identity(principal_id, conversation_id, message_id)
            thread_id = self._stop_thread_id(identity)
            with self._engine.begin() as connection:
                self._scope(connection)
                self._stop_fence(connection, identity)
                if not self._marker_exists(connection, thread_id, "CONVERSATION_STOP_REQUESTED", identity):
                    insert_initial_timeline_thread(
                        connection, self._scope_value,
                        thread_id=thread_id, checkpoint_id=f"{thread_id}:requested",
                        occurred_at=datetime.now(tz=UTC).replace(microsecond=0), data=identity,
                        event_type="CONVERSATION_STOP_REQUESTED",
                        phase="stop_requested", status="completed",
                    )
            count += 1
        return count

    def request_turn_stop(self, turn: ConversationTurn, *, request_message_id: str | None = None) -> str:
        """Stop an exact trusted ingress even if model admission has not arrived."""
        target_message_id = turn.message_id
        if request_message_id is not None:
            command_identity = self._stop_identity(turn.principal_id, turn.conversation_id, request_message_id)
            snapshot_id = "conversation-stop-targets:" + canonical_sha256(command_identity)
            with self._engine.begin() as connection:
                self._scope(connection)
                self._stop_fence(connection, command_identity)
                rows = connection.execute(sa.select(operational_events.c.event_type, operational_events.c.payload).where(
                    operational_events.c.tenant_id == self._scope_value.tenant_id,
                    operational_events.c.installation_id == self._scope_value.installation_id,
                    operational_events.c.thread_id == snapshot_id,
                )).all()
                if rows:
                    data = rows[0].payload.get("data")
                    if (len(rows) != 1 or rows[0].event_type != "CONVERSATION_STOP_TARGETS"
                            or not isinstance(data, dict) or data.get("principal_id") != turn.principal_id
                            or data.get("conversation_id") != turn.conversation_id
                            or data.get("request_message_id") != request_message_id
                            or not isinstance(data.get("targets"), list) or len(data["targets"]) != 1
                            or not isinstance(data["targets"][0], str) or not data["targets"][0]):
                        raise ConversationConflict("exact stop target snapshot differs")
                    target_message_id = data["targets"][0]
                else:
                    insert_initial_timeline_thread(
                        connection, self._scope_value, thread_id=snapshot_id,
                        checkpoint_id=f"{snapshot_id}:targets", occurred_at=datetime.now(tz=UTC).replace(microsecond=0),
                        data={"principal_id": turn.principal_id, "conversation_id": turn.conversation_id,
                              "request_message_id": request_message_id, "targets": [target_message_id]},
                        event_type="CONVERSATION_STOP_TARGETS", phase="targets_snapshotted", status="completed",
                    )
        identity = self._stop_identity(turn.principal_id, turn.conversation_id, target_message_id)
        thread_id = self._stop_thread_id(identity)
        with self._engine.begin() as connection:
            self._scope(connection)
            self._stop_fence(connection, identity)
            if not self._marker_exists(connection, thread_id, "CONVERSATION_STOP_REQUESTED", identity):
                insert_initial_timeline_thread(
                    connection, self._scope_value, thread_id=thread_id,
                    checkpoint_id=f"{thread_id}:requested",
                    occurred_at=datetime.now(tz=UTC).replace(microsecond=0), data=identity,
                    event_type="CONVERSATION_STOP_REQUESTED", phase="stop_requested", status="completed",
                )
        return target_message_id

    def ensure_running(self, turn: ConversationTurn) -> None:
        identity = self._stop_identity(turn.principal_id, turn.conversation_id, turn.message_id)
        events = self._timeline.events(self._stop_thread_id(identity))
        if not events:
            return
        if (len(events) != 1 or events[0].event_type != "CONVERSATION_STOP_REQUESTED"
                or events[0].payload.get("data") != identity):
            raise ConversationConflict("conversation stop record is malformed")
        raise ConversationStopped("conversation has a durable stop request")

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
            if text is None and isinstance(data.get("content_erasure"), dict):
                raise ConversationConflict("conversation reply content was erased")
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
                timing = (
                    ConversationModelTiming.unmeasured()
                    if "model_timing" not in data
                    else ConversationModelTiming.from_wire(data["model_timing"])
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
                model_timing=timing,
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
            "model_timing": reply.model_timing.to_wire(),
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
        household = turn.audience is not None and turn.audience.kind == "household"
        voice_requests = None
        device_id = turn.principal_id.removeprefix("household-device:")
        if (household and turn.principal_id.startswith("household-device:")
                and re.fullmatch(r"[0-9a-f]{32}", device_id)):
            from eva.adapters.postgres.voice_requests import PostgresVoiceRequests
            voice_requests = PostgresVoiceRequests(self._engine, tenant_id=turn.tenant_id,
                                                   installation_id=turn.installation_id)
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
            playback_status = None
            if household and row.event_type == "REPLY_COMMITTED":
                playback_status = "unconfirmed"
                message_id = value.get("message_id")
                prefix = turn.conversation_id + ":request:"
                digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
                if (voice_requests is not None and isinstance(message_id, str)
                        and message_id.startswith(prefix)
                        and re.fullmatch(r"[0-9a-f]{32}", message_id[len(prefix):])
                        and value.get("text_sha256") == digest):
                    playback_status = voice_requests.playback_status(device_id=device_id,
                        request_id=message_id[len(prefix):], reply_sha256=digest)
            messages.append(
                RecentConversationMessage(
                    role=("Onbekende spreker" if household else "Jaap")
                         if row.event_type == "INGRESS_COMMITTED" else "Eva",
                    text=text,
                    occurred_at=row.occurred_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    playback_status=playback_status,
                )
            )
        return tuple(messages[-limit:])

    def erase_message_content(
        self,
        *,
        principal_id: str | None = None,
        principal_sha256: str | None = None,
        message_id: str,
        operation_sha256: str,
        erased_at: datetime,
    ) -> ConversationContentErasureReceipt:
        """Physically redact only the raw ingress/reply text for one principal."""

        if (principal_id is None) == (principal_sha256 is None):
            raise ValueError("conversation erasure needs one principal binding")
        if principal_id is not None:
            if not principal_id.strip() or len(principal_id) > 240:
                raise ValueError("conversation erasure principal is invalid")
            principal_sha256 = hashlib.sha256(
                principal_id.encode("utf-8")
            ).hexdigest()
        elif not isinstance(principal_sha256, str) or not _SHA256.fullmatch(
            principal_sha256
        ):
            raise ValueError("conversation erasure principal hash is invalid")
        assert principal_sha256 is not None
        if not message_id.strip() or len(message_id) > 240:
            raise ValueError("conversation erasure message is invalid")
        if not _SHA256.fullmatch(operation_sha256):
            raise ValueError("conversation erasure operation hash is invalid")
        if (
            erased_at.tzinfo is None
            or erased_at.utcoffset() is None
            or erased_at.microsecond
        ):
            raise ValueError("conversation erasure time must be whole-second UTC")
        erased_at = erased_at.astimezone(UTC)
        erased_text = erased_at.strftime("%Y-%m-%dT%H:%M:%SZ")
        message_sha256 = hashlib.sha256(message_id.encode("utf-8")).hexdigest()
        erasure = {
            "contract": "EvaConversationContentErasure/v1",
            "erased_at": erased_text,
            "message_sha256": message_sha256,
            "operation_sha256": operation_sha256,
            "principal_sha256": principal_sha256,
            "status": "erased",
        }
        data = operational_events.c.payload["data"]
        with self._engine.begin() as connection:
            self._scope(connection)
            candidates = connection.execute(
                sa.select(operational_events)
                .where(
                    operational_events.c.tenant_id == self._scope_value.tenant_id,
                    operational_events.c.installation_id
                    == self._scope_value.installation_id,
                    operational_events.c.event_type.in_(
                        ["INGRESS_COMMITTED", "REPLY_COMMITTED"]
                    ),
                    data["message_id"].astext == message_id,
                )
                .order_by(operational_events.c.sequence)
            ).all()
            rows = []
            for row in candidates:
                value = row.payload.get("data") if isinstance(row.payload, dict) else None
                durable_principal = (
                    value.get("principal_id") if isinstance(value, dict) else None
                )
                if (
                    isinstance(durable_principal, str)
                    and hashlib.sha256(durable_principal.encode("utf-8")).hexdigest()
                    == principal_sha256
                ):
                    rows.append(row)
            if not rows:
                raise ConversationConflict("conversation erasure target does not exist")

            existing: dict[str, Any] | None = None
            redacted = 0
            for row in rows:
                payload = dict(row.payload)
                value = payload.get("data")
                if not isinstance(value, dict):
                    raise ConversationConflict("conversation erasure target is malformed")
                old_erasure = value.get("content_erasure")
                if "text" not in value:
                    if not isinstance(old_erasure, dict):
                        raise ConversationConflict(
                            "conversation content disappeared without an erasure proof"
                        )
                    if existing is None:
                        existing = old_erasure
                    elif existing != old_erasure:
                        raise ConversationConflict(
                            "conversation erasure proofs differ within one exchange"
                        )
                    continue
                if old_erasure is not None:
                    raise ConversationConflict(
                        "conversation text and erasure proof coexist"
                    )
                redacted_data = dict(value)
                redacted_data.pop("text", None)
                redacted_data.pop("text_sha256", None)
                redacted_data["content_erasure"] = erasure
                redacted_payload = dict(payload)
                redacted_payload["data"] = redacted_data
                changed = connection.scalar(
                    sa.text(
                        "SELECT eva_core.erase_conversation_event_content("
                        ":event_id, :expected_hash, :payload, :new_hash)"
                    ),
                    {
                        "event_id": row.event_id,
                        "expected_hash": bytes(row.payload_sha256),
                        "payload": canonical_json_bytes(redacted_payload).decode("utf-8"),
                        "new_hash": bytes.fromhex(canonical_sha256(redacted_payload)),
                    },
                )
                if changed is not True:
                    raise ConversationConflict("conversation erasure target vanished")
                redacted += 1

            if redacted == 0:
                if existing is None:
                    raise ConversationConflict("conversation erasure proof is missing")
                return ConversationContentErasureReceipt(
                    operation_sha256=str(existing["operation_sha256"]),
                    principal_sha256=str(existing["principal_sha256"]),
                    message_sha256=str(existing["message_sha256"]),
                    erased_at=str(existing["erased_at"]),
                    redacted_event_count=0,
                    replayed=True,
                )
        return ConversationContentErasureReceipt(
            operation_sha256=operation_sha256,
            principal_sha256=principal_sha256,
            message_sha256=message_sha256,
            erased_at=erased_text,
            redacted_event_count=redacted,
            replayed=False,
        )

__all__ = ["ConversationContentErasureReceipt", "PostgresConversationJournal"]
