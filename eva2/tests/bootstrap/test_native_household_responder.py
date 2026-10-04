import asyncio
from threading import Event

import pytest

from eva.adapters.api.native_voice import VoiceRequest
from eva.application.cancellation import run_cancellable
from eva.application.conversation import GroundedReply
from eva.application.memory_audience import HouseholdConsentReference
from eva.bootstrap.native_voice import NativeHouseholdResponder


class Consent:
    active = True
    def active_reference(self, *, principal_id, household_id, now):
        if not self.active:
            return None
        return HouseholdConsentReference(principal_id, household_id, "household-consent:1")


class Runtime:
    def __init__(self):
        self.turns = []
        self.retained = []
    def admit_turn_audience(self, turn):
        assert turn.audience.kind == "household"
        assert turn.principal_id == "household-device:" + "a" * 32
    def respond(self, turn):
        self.turns.append(turn)
        return GroundedReply(turn.conversation_id, turn.message_id, "Ik luister.", (), turn.occurred_at, "test")
    def after_response(self, turn, reply):
        self.retained.append(turn)


def request(**changes):
    return VoiceRequest(**{**dict(request_id="b" * 32, satellite_id="assist_satellite.room",
                                  device_id="a" * 32, conversation_id="telegram:private-owner",
                                  text="Hallo", language="nl"), **changes})


def responder(runtime, consent=None):
    return NativeHouseholdResponder(runtime=runtime, tenant_id="tenant", installation_id="installation",
        household_id="home", devices={"assist_satellite.room": "a" * 32}, consent=consent or Consent())


def test_native_binding_namespaces_ha_continuity_without_claiming_human_identity():
    async def run():
        runtime = Runtime()
        respond = responder(runtime)
        reply = await respond(request())
        assert reply.text == "Ik luister."
        turn = runtime.turns[0]
        assert turn.conversation_id.startswith("household:home:conversation:")
        assert "private-owner" not in turn.conversation_id
        assert turn.message_id == turn.conversation_id + ":request:" + "b" * 32
        assert runtime.retained == [turn]
        await respond(request(request_id="c" * 32))
        assert runtime.turns[1].conversation_id == turn.conversation_id
        assert runtime.turns[1].message_id != turn.message_id
    asyncio.run(run())


def test_native_binding_denies_missing_mandate_and_wrong_device_before_runtime():
    async def run():
        runtime = Runtime()
        consent = Consent()
        consent.active = False
        with pytest.raises(ValueError, match="mandate is not active"):
            await responder(runtime, consent)(request())
        with pytest.raises(ValueError, match="device is not admitted"):
            await responder(runtime)(request(device_id="c" * 32))
        assert not runtime.turns and not runtime.retained
    asyncio.run(run())


def test_native_binding_cancels_model_and_skips_retention_for_interrupted_reply():
    started, closed = Event(), Event()
    class SlowRuntime(Runtime):
        def respond(self, turn):
            async def provider():
                started.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()
            asyncio.run(run_cancellable(provider))
            return super().respond(turn)
    async def run():
        runtime = SlowRuntime()
        task = asyncio.create_task(responder(runtime)(request()))
        assert await asyncio.to_thread(started.wait, 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 3)
        assert closed.is_set()
        assert not runtime.retained
    asyncio.run(run())



def test_native_binding_rejects_a_reply_from_another_conversation():
    class WrongRuntime(Runtime):
        def respond(self, turn):
            return GroundedReply("private:owner", turn.message_id, "Private text", (), turn.occurred_at, "test")
    async def run():
        runtime = WrongRuntime()
        with pytest.raises(ValueError, match="crossed its request binding"):
            await responder(runtime)(request())
        assert not runtime.retained
    asyncio.run(run())


def test_request_delivery_mode_defaults_to_speaker_and_rejects_unknown_destination():
    from pydantic import ValidationError
    assert request().delivery_mode == "speaker"
    assert request(delivery_mode="automation").delivery_mode == "automation"
    with pytest.raises(ValidationError):
        request(delivery_mode="private-owner")
