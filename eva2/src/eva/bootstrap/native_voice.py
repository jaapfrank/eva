"""Bind the native HA transport to household-scoped ordinary conversations.

No mandate is minted here. The configured authority resolver must return the
current owner grant for each registered speaker's audit actor.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
from typing import Protocol

from eva.adapters.api.native_voice import VoiceReply, VoiceRequest
from eva.adapters.projection.hindsight import ActiveHouseholdConsentResolver
from eva.application.cancellation import check_cancellation, run_in_cancellation_thread
from eva.application.conversation import ConversationTurn, GroundedReply
from eva.application.memory_audience import MemoryAudience
from eva.contracts.canonical_json import canonical_json_bytes


class HouseholdConversationRuntime(Protocol):
    def admit_turn_audience(self, turn: ConversationTurn) -> None: ...
    def respond(self, turn: ConversationTurn) -> GroundedReply: ...
    def after_response(self, turn: ConversationTurn, reply: GroundedReply) -> None: ...


class NativeHouseholdResponder:
    def __init__(self, *, runtime: HouseholdConversationRuntime,
                 tenant_id: str, installation_id: str, household_id: str,
                 devices: Mapping[str, str], consent: ActiveHouseholdConsentResolver, work_server=None):
        if not tenant_id or not installation_id or not household_id or not devices:
            raise ValueError("Native household scope is required")
        self._runtime = runtime
        self._tenant = tenant_id
        self._installation = installation_id
        self._household = household_id
        self._devices = dict(devices)
        self._consent = consent
        self._work_server = work_server

    def make_turn(self, request: VoiceRequest) -> ConversationTurn:
        if self._devices.get(request.satellite_id) != request.device_id:
            raise ValueError("Native household device is not admitted")
        actor = "household-device:" + request.device_id
        now = datetime.now(UTC).replace(microsecond=0)
        reference = self._consent.active_reference(
            principal_id=actor, household_id=self._household, now=now,
        )
        if reference is None:
            raise ValueError("Native household mandate is not active")
        if self._work_server is not None:
            from uuid import UUID
            server = self._work_server
            binding = server.sessions.native(request.satellite_id, request.device_id,
                                             request.conversation_id)
            service = server.session(token=server.authority.voice.token,
                satellite_id=request.satellite_id, device_id=request.device_id,
                session_id=binding["session_id"])
            turn = service.turn(str(UUID(request.request_id)), request.text)
            self._runtime.admit_turn_audience(turn)
            check_cancellation()
            return turn
        audience = MemoryAudience.household(principal_id=actor, household_id=self._household,
                                            consent_ref=reference)
        # HA's conversation ID is continuity data, never an Eva principal or
        # a raw private conversation ID. Each device owns its own short log.
        digest = hashlib.sha256(canonical_json_bytes(
            [request.device_id, request.conversation_id])).hexdigest()
        conversation = f"household:{self._household}:conversation:{digest}"
        turn = ConversationTurn(
            tenant_id=self._tenant, installation_id=self._installation, principal_id=actor,
            conversation_id=conversation, message_id=f"{conversation}:request:{request.request_id}",
            text=request.text, occurred_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"), audience=audience,
        )
        self._runtime.admit_turn_audience(turn)
        check_cancellation()
        return turn

    async def __call__(self, request: VoiceRequest) -> VoiceReply:
        def execute() -> VoiceReply:
            check_cancellation()
            turn = self.make_turn(request)
            reply = self._runtime.respond(turn)
            check_cancellation()
            if (not isinstance(reply, GroundedReply) or reply.conversation_id != turn.conversation_id
                    or reply.message_id != turn.message_id):
                raise ValueError("Native household reply crossed its request binding")
            self._runtime.admit_turn_audience(turn)
            self._runtime.after_response(turn, reply)
            check_cancellation()
            return VoiceReply(reply.text, True)
        return await run_in_cancellation_thread(execute)
