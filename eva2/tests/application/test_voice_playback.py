from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from eva.application.voice import VoicePlaybackController
from eva.contracts.voice import BargeIn, VoicePrivacy, VoiceSession


def _session() -> VoiceSession:
    return VoiceSession.model_validate(
        {
            "contract": "VoiceSession/v1",
            "schema_version": 1,
            "session_id": "voice-session:001",
            "tenant_id": "tenant:001",
            "installation_id": "installation:001",
            "principal_id": "person:001",
            "conversation_id": "conversation:001",
            "audience": "private",
            "channel": "local_test",
            "device_binding_id": "voice-device:001",
            "identity_id": "identity:eva",
            "identity_pack_version": 1,
            "identity_pack_sha256": "a" * 64,
            "policy_id": "policy:default",
            "policy_pack_version": 1,
            "policy_pack_sha256": "b" * 64,
            "privacy": VoicePrivacy.model_validate(
                {
                    "contract": "VoicePrivacy/v1",
                    "schema_version": 1,
                    "partial_transcript_retention": "ephemeral",
                    "raw_audio_retention": "none",
                    "external_provider_audio_retention": "disabled",
                    "final_transcript_admission": "conversation_admission_required",
                }
            ).model_dump(mode="json"),
            "created_at": "2026-08-27T10:00:00Z",
            "deadline": "2026-08-27T10:30:00Z",
        }
    )


def _barge_in(**overrides: object) -> BargeIn:
    values: dict[str, object] = {
        "contract": "BargeIn/v1",
        "schema_version": 1,
        "barge_in_id": "barge-in:001",
        "session_id": "voice-session:001",
        "tenant_id": "tenant:001",
        "installation_id": "installation:001",
        "principal_id": "person:001",
        "conversation_id": "conversation:001",
        "audience": "private",
        "interrupted_voice_turn_id": "voice-turn:001",
        "cancel_generation": 4,
        "played_ms": 312,
        "occurred_at": "2026-08-27T10:01:03Z",
        "cancellation_scope": "playback_and_unfinished_reply_generation",
        "effect_cancel_authorized": False,
    }
    values.update(overrides)
    return BargeIn.model_validate(values)


class _Sink:
    def __init__(
        self, *, block_first_write: bool = False, block_stop: bool = False
    ) -> None:
        self.played_lengths: list[int] = []
        self.stop_calls = 0
        self.first_write_started = asyncio.Event()
        self.release_first_write = asyncio.Event()
        self.block_first_write = block_first_write
        self.stop_started = asyncio.Event()
        self.release_stop = asyncio.Event()
        self.block_stop = block_stop

    async def play(self, audio_chunk: bytes) -> None:
        if self.block_first_write and not self.played_lengths:
            self.first_write_started.set()
            await self.release_first_write.wait()
        self.played_lengths.append(len(audio_chunk))

    async def stop(self) -> None:
        self.stop_calls += 1
        self.release_first_write.set()
        self.stop_started.set()
        if self.block_stop:
            await self.release_stop.wait()


class _FailingStopSink(_Sink):
    async def stop(self) -> None:
        self.stop_calls += 1
        self.release_first_write.set()
        raise OSError("synthetic speaker disconnect")


async def _chunks(*values: bytes) -> AsyncIterator[bytes]:
    for value in values:
        yield value


def test_playback_forwards_transient_chunks_without_storing_audio() -> None:
    async def run() -> None:
        sink = _Sink()
        controller = VoicePlaybackController(
            session=_session(), voice_turn_id="voice-turn:001", reply_generation=4, sink=sink
        )

        receipt = await controller.deliver(_chunks(b"one", b"two!"))

        assert receipt.status == "completed"
        assert receipt.delivered_chunk_count == 2
        assert receipt.delivered_byte_count == 7
        assert receipt.interrupt_to_sink_stop_ms is None
        assert sink.played_lengths == [3, 4]
        assert sink.stop_calls == 0
        assert not hasattr(controller, "audio")

    asyncio.run(run())


def test_valid_barge_in_cancels_unfinished_playback_and_stops_sink() -> None:
    async def run() -> None:
        sink = _Sink(block_first_write=True)
        controller = VoicePlaybackController(
            session=_session(), voice_turn_id="voice-turn:001", reply_generation=4, sink=sink
        )
        delivery = asyncio.create_task(controller.deliver(_chunks(b"one", b"two")))
        await sink.first_write_started.wait()

        await controller.interrupt(_barge_in())
        receipt = await delivery

        assert receipt.status == "interrupted"
        assert receipt.delivered_chunk_count == 0
        assert receipt.delivered_byte_count == 0
        assert receipt.interrupt_to_sink_stop_ms is not None
        assert sink.played_lengths == []
        assert sink.stop_calls == 1

    asyncio.run(run())


def test_interrupted_delivery_waits_until_the_sink_confirms_stop() -> None:
    async def run() -> None:
        sink = _Sink(block_first_write=True, block_stop=True)
        controller = VoicePlaybackController(
            session=_session(), voice_turn_id="voice-turn:001", reply_generation=4, sink=sink
        )
        delivery = asyncio.create_task(controller.deliver(_chunks(b"one", b"two")))
        await sink.first_write_started.wait()

        interruption = asyncio.create_task(controller.interrupt(_barge_in()))
        await sink.stop_started.wait()
        await asyncio.sleep(0)
        assert not delivery.done()

        sink.release_stop.set()
        await interruption
        assert (await delivery).status == "interrupted"

    asyncio.run(run())


def test_sink_stop_failure_never_returns_an_interrupted_success_receipt() -> None:
    async def run() -> None:
        sink = _FailingStopSink(block_first_write=True)
        controller = VoicePlaybackController(
            session=_session(), voice_turn_id="voice-turn:001", reply_generation=4, sink=sink
        )
        delivery = asyncio.create_task(controller.deliver(_chunks(b"one")))
        await sink.first_write_started.wait()

        with pytest.raises(RuntimeError, match="did not stop"):
            await controller.interrupt(_barge_in())
        with pytest.raises(RuntimeError, match="did not stop"):
            await delivery
        assert sink.stop_calls == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("interrupted_voice_turn_id", "voice-turn:other", "another voice turn"),
        ("cancel_generation", 5, "another reply generation"),
        ("principal_id", "person:other", "not bound"),
    ),
)
def test_barge_in_cannot_stop_another_person_turn_or_generation(
    field: str, value: object, message: str
) -> None:
    async def run() -> None:
        sink = _Sink(block_first_write=True)
        controller = VoicePlaybackController(
            session=_session(), voice_turn_id="voice-turn:001", reply_generation=4, sink=sink
        )
        delivery = asyncio.create_task(controller.deliver(_chunks(b"one")))
        await sink.first_write_started.wait()

        with pytest.raises(ValueError, match=message):
            await controller.interrupt(_barge_in(**{field: value}))
        assert sink.stop_calls == 0

        await controller.interrupt(_barge_in())
        assert (await delivery).status == "interrupted"

    asyncio.run(run())


def test_playback_rejects_empty_or_oversized_chunks_before_sink_write() -> None:
    async def run() -> None:
        sink = _Sink()
        empty_controller = VoicePlaybackController(
            session=_session(), voice_turn_id="voice-turn:001", reply_generation=4, sink=sink
        )
        with pytest.raises(ValueError, match="non-empty"):
            await empty_controller.deliver(_chunks(b""))
        with pytest.raises(RuntimeError, match="not interruptible"):
            await empty_controller.interrupt(_barge_in())

        oversized_controller = VoicePlaybackController(
            session=_session(), voice_turn_id="voice-turn:001", reply_generation=4, sink=sink
        )
        oversized = VoicePlaybackController._MAX_CHUNK_BYTES + 1
        with pytest.raises(ValueError, match="fixed bound"):
            await oversized_controller.deliver(_chunks(b"x" * oversized))
        with pytest.raises(RuntimeError, match="not interruptible"):
            await oversized_controller.interrupt(_barge_in())
        assert sink.played_lengths == []

    asyncio.run(run())


@pytest.mark.parametrize("voice_turn_id", ("", "   "))
def test_playback_requires_a_non_empty_voice_turn_id(voice_turn_id: str) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        VoicePlaybackController(
            session=_session(),
            voice_turn_id=voice_turn_id,
            reply_generation=4,
            sink=_Sink(),
        )
