"""Temporary speaker identity confirmed through the existing private Telegram route."""

from datetime import datetime, timedelta
import hashlib
import re
import secrets
import sqlalchemy as sa
from eva.adapters.postgres.operational_timeline import (
    PostgresOperationalTimeline,
    operational_events,
    CheckpointConflict,
)
from eva.application.conversation import ConversationTurn, GroundedReply


class VoiceIdentity:
    def __init__(self, server, runtime, delivery, recipient):
        self.server, self.runtime, self.delivery, self.recipient = (
            server,
            runtime,
            delivery,
            recipient,
        )
        self.timeline = PostgresOperationalTimeline(
            server.store._engine, server.store.scope
        )
        self.owner = str(server.authority.policy.owner_principal_id)
        self.owner_binding = server.authority.policy.principal_binding_id

    def now(self):
        return self.server.authority.clock().replace(microsecond=0)

    def thread(self, session_id):
        from uuid import UUID

        return "voice-identity:" + str(UUID(session_id))

    def data(self, session_id):
        events = self.timeline.events(self.thread(session_id))
        return None if not events else events[0].payload["data"]

    def current(self, data):
        binding = data["session"]
        actual = self.server.sessions.read(
            binding["session_id"], binding["satellite_id"], binding["device_id"]
        )
        if (
            actual != binding
            or data["owner"] != self.owner
            or self.recipient() != data["chat_id"]
        ):
            raise ValueError("Voice identity authority changed")
        return binding

    def request(self, session_binding):
        session_id = session_binding["session_id"]
        thread = self.thread(session_id)
        existing = self.data(session_id)
        if existing is not None:
            self.current(existing)
            state = self.timeline.load(thread)
            return {
                "status": state.phase if self.now() < state.deadline else "expired",
                "new_message_sent": False,
            }
        chat_id = self.recipient()
        code = secrets.token_hex(5).upper()
        data = {
            "session": session_binding,
            "chat_id": chat_id,
            "owner": self.owner,
            "code_sha256": hashlib.sha256(code.encode()).hexdigest(),
        }
        self.current(data)
        now = self.now()
        try:
            state = self.timeline.create_thread(
                thread_id=thread,
                checkpoint_id=thread + ":requested",
                occurred_at=now,
                event_type="VOICE_IDENTITY_REQUESTED",
                phase="delivery_started",
                deadline=now + timedelta(minutes=3),
                data=data,
            )
        except CheckpointConflict:
            return {"status": "pending", "new_message_sent": False}
        # One external attempt. A delivery exception remains uncertain and is not retried.
        self.delivery.deliver_text(
            "Praat jij nu met Eva via de huisspeaker? Bevestig alleen als jij dit gesprek voert. "
            "Na bevestiging mag Eva jouw persoonlijke geheugen, agenda en functies in dit "
            "spraakgesprek gebruiken en uitspreken. De koppeling eindigt met dit gesprek.\n\n"
            f"Antwoord binnen drie minuten met: STEM JA {code}\n"
            f"Of wijs af met: STEM NEE {code}",
            expected_chat_id=chat_id,
        )
        try:
            self.timeline.transition(
                thread_id=thread,
                expected_revision=state.revision,
                checkpoint_id=thread + ":sent",
                phase="awaiting_confirmation",
                status="active",
                event_type="VOICE_IDENTITY_QUESTION_SENT",
                occurred_at=self.now(),
                data={},
                deadline=state.deadline,
                require_unexpired=True,
            )
        except CheckpointConflict:
            pass  # A valid, very fast confirmation can overtake delivery acknowledgement.
        return {"status": "awaiting_confirmation", "new_message_sent": True}

    def confirm_message(self, turn, sender_id, chat_id):
        match = re.fullmatch(
            r"(?:/stem|stem)\s+(ja|nee)\s+([A-F0-9]{10})", turn.text.strip(), re.I
        )
        if match is None:
            return None
        if turn.audience.kind != "private_person" or chat_id != str(sender_id):
            raise ValueError("Voice identity needs private Telegram confirmation")
        bindings = self.runtime._telegram_principal_bindings_loader()
        if (
            bindings.resolve(sender_id, chat_id) != self.owner
            or bindings.resolve_binding_id(sender_id, chat_id) != self.owner_binding
            or turn.principal_id != "telegram:" + str(sender_id)
            or chat_id != self.recipient()
        ):
            raise ValueError("Voice confirmation belongs to another person")
        digest = hashlib.sha256(match[2].upper().encode()).hexdigest()
        store = self.server.store
        with store._engine.begin() as connection:
            store._scope(connection)
            threads = list(
                connection.scalars(
                    sa.select(operational_events.c.thread_id).where(
                        operational_events.c.tenant_id == store.scope.tenant_id,
                        operational_events.c.installation_id
                        == store.scope.installation_id,
                        operational_events.c.event_type == "VOICE_IDENTITY_REQUESTED",
                        operational_events.c.payload["data"]["code_sha256"].astext
                        == digest,
                    )
                )
            )
        thread = threads[0] if len(threads) == 1 else None
        message = (
            "Deze spraakbevestiging is niet meer geldig. Begin een nieuw spraakgesprek."
        )
        if thread:
            state = self.timeline.load(thread)
            data = self.data(thread.removeprefix("voice-identity:"))
            try:
                self.current(data)
            except (ValueError, RuntimeError, OSError):
                return GroundedReply(
                    turn.conversation_id,
                    turn.message_id,
                    message,
                    (),
                    turn.occurred_at,
                    "voice-identity-local",
                )
            if state.phase == "confirmed" and self.now() < state.deadline:
                message = "Dit spraakgesprek is al persoonlijk gekoppeld."
            elif (
                state.phase in {"delivery_started", "awaiting_confirmation"}
                and self.now() < state.deadline
            ):
                accepted = match[1].casefold() == "ja"
                try:
                    self.timeline.transition(
                        thread_id=thread,
                        expected_revision=state.revision,
                        checkpoint_id=thread + ":decision",
                        phase="confirmed" if accepted else "denied",
                        status="completed",
                        event_type=(
                            "VOICE_IDENTITY_CONFIRMED"
                            if accepted
                            else "VOICE_IDENTITY_DENIED"
                        ),
                        occurred_at=self.now(),
                        data={
                            "telegram_message_id": turn.message_id,
                            "speaker_confirmed_at": self.server.authority.clock().strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                        },
                        deadline=self.now() + timedelta(minutes=30),
                        require_unexpired=True,
                    )
                except CheckpointConflict:
                    latest = self.timeline.load(thread)
                    if latest.phase == "confirmed" and self.now() < latest.deadline:
                        message = "Dit spraakgesprek is al persoonlijk gekoppeld."
                    return GroundedReply(
                        turn.conversation_id,
                        turn.message_id,
                        message,
                        (),
                        turn.occurred_at,
                        "voice-identity-local",
                    )
                message = (
                    "Bevestigd. Je kunt nu verder praten met Eva via die speaker; dit gesprek gebruikt jouw persoonlijke context."
                    if accepted
                    else "Afgewezen. Het spraakgesprek blijft gedeeld."
                )
        return GroundedReply(
            turn.conversation_id,
            turn.message_id,
            message,
            (),
            turn.occurred_at,
            "voice-identity-local",
        )

    def principal(self, session_binding):
        data = self.data(session_binding["session_id"])
        if data is None:
            return None
        if self.current(data) != session_binding:
            raise ValueError("Personal voice session binding differs")
        state = self.timeline.load(self.thread(session_binding["session_id"]))
        if state.phase != "confirmed":
            return None
        if self.now() >= state.deadline:
            raise ValueError("Personal voice session expired")
        return self.owner

    def private_turn(self, session_binding, request_id, text, *, source_turn=None):
        principal = self.principal(session_binding)
        if principal is None:
            return None
        if source_turn is not None:
            confirmation = next(
                event
                for event in self.timeline.events(
                    self.thread(session_binding["session_id"])
                )
                if event.event_type == "VOICE_IDENTITY_CONFIRMED"
            )
            stamp = confirmation.payload["data"].get(
                "speaker_confirmed_at", confirmation.occurred_at.isoformat()
            )
            boundary = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
            # Durable receive time survives transport retries. Same-second input
            # is conservatively shared because its ordering cannot be proven.
            received = datetime.fromisoformat(
                source_turn.occurred_at.replace("Z", "+00:00")
            )
            if received <= boundary:
                return None
        return ConversationTurn.private(
            session_binding["tenant_id"],
            session_binding["installation_id"],
            principal,
            "voice:" + session_binding["session_id"],
            "voice:" + session_binding["session_id"] + ":" + str(request_id),
            text,
            self.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
        )

    def work_return(self, binding, operation, **identity):
        turn = self.private_turn(
            binding, binding["session_id"], "Voice session control"
        )
        if turn is None:
            raise ValueError("Personal voice authority missing")
        if operation != "close":
            self.runtime.private_work_return(turn, "ensure_open")
        result = self.runtime.private_work_return(turn, operation, **identity)
        self.current(self.data(binding["session_id"]))
        return result

    def personal_context(self, binding, payload):
        # Never reinterpret pre-verification speakers or in-flight household
        # request IDs as the verified owner's private conversation.
        from eva.adapters.postgres.operational_timeline import external_timeline_scope

        confirmed = next(
            e.occurred_at
            for e in self.timeline.events(self.thread(binding["session_id"]))
            if e.event_type == "VOICE_IDENTITY_CONFIRMED"
        )
        prefix = "voice:" + binding["session_id"] + ":"
        requested = {
            prefix + str(item.message_id)
            for item in (*payload.active_work, *payload.work_results)
        }
        known = set()
        if requested:
            scope = external_timeline_scope(
                binding["tenant_id"], binding["installation_id"], "voice scope required"
            )
            timeline = PostgresOperationalTimeline(self.server.store._engine, scope)
            with self.server.store._engine.begin() as connection:
                timeline._scope(connection)
                known = set(
                    connection.scalars(
                        sa.select(
                            operational_events.c.payload["data"]["message_id"].astext
                        ).where(
                            operational_events.c.tenant_id == scope.tenant_id,
                            operational_events.c.installation_id
                            == scope.installation_id,
                            operational_events.c.event_type == "INGRESS_COMMITTED",
                            operational_events.c.payload["data"]["principal_id"].astext
                            == self.owner,
                            operational_events.c.payload["data"][
                                "message_id"
                            ].astext.in_(requested),
                        )
                    )
                )
        return payload.model_copy(
            update={
                "recent": [
                    item
                    for item in payload.recent
                    if datetime.fromisoformat(item.occurred_at.replace("Z", "+00:00"))
                    > confirmed
                ],
                "active_work": [
                    item
                    for item in payload.active_work
                    if prefix + str(item.message_id) in known
                ],
                "work_results": [
                    item
                    for item in payload.work_results
                    if prefix + str(item.message_id) in known
                ],
            }
        )
