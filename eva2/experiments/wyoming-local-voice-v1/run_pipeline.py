"""Run a bounded, local Wyoming STT -> Eva V01 -> TTS proof.

The input audio is synthesized by a pinned local Piper sidecar and is sent to
a pinned local Faster-Whisper sidecar.  Only the final transcript enters
``admit_final_voice_turn``; raw PCM and text are never written to the receipt.
There is deliberately no model reply, persistence, microphone, speaker,
network request, tool call, provider credential or production import.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
from importlib.metadata import version
import json
from pathlib import Path
from typing import Final

from wyoming.asr import Transcript, Transcribe
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize, SynthesizeVoice

from eva.application.voice import admit_final_voice_turn
from eva.contracts.voice import VoicePrivacy, VoiceSession, VoiceTurn


INPUT_TEXT: Final = "Dit is een test."
INPUT_VOICE: Final = "nl_NL-pim-medium"
REPLY_TEXT: Final = "Ik heb je antwoord ontvangen."
MAX_AUDIO_BYTES: Final = 2_000_000
MAX_AUDIO_CHUNKS: Final = 2_000
PIPER_IMAGE: Final = (
    "rhasspy/wyoming-piper@sha256:"
    "6b837615b543e38587b735b72abad2dfd7ead50fd10ec1a4d5815241e5539377"
)
WHISPER_IMAGE: Final = (
    "rhasspy/wyoming-whisper@sha256:"
    "731299c56df053dd67978127a44e417c83f0d900fdaad1bd7ab8aad82f84106d"
)


def _canonical_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _experiment_file(name: str) -> Path:
    return Path(__file__).resolve().with_name(name)


def _normalized(value: str) -> str:
    return " ".join(value.casefold().split()).strip(".?! ")


async def _synthesize(*, host: str, port: int, text: str) -> tuple[AudioStart, bytes]:
    """Read exactly one bounded PCM stream from a local Piper endpoint."""

    audio_format: AudioStart | None = None
    chunks: list[bytes] = []
    async with AsyncTcpClient(host, port) as client:
        await client.write_event(
            Synthesize(text=text, voice=SynthesizeVoice(name=INPUT_VOICE)).event()
        )
        while True:
            event = await asyncio.wait_for(client.read_event(), timeout=60)
            if event is None:
                raise RuntimeError("Piper closed before audio-stop")
            if AudioStart.is_type(event.type):
                if audio_format is not None:
                    raise RuntimeError("Piper sent two audio-start events")
                audio_format = AudioStart.from_event(event)
            elif AudioChunk.is_type(event.type):
                if audio_format is None:
                    raise RuntimeError("Piper sent audio before audio-start")
                chunk = AudioChunk.from_event(event)
                if (
                    chunk.rate != audio_format.rate
                    or chunk.width != audio_format.width
                    or chunk.channels != audio_format.channels
                ):
                    raise RuntimeError("Piper changed PCM format mid-stream")
                chunks.append(chunk.audio)
                if len(chunks) > MAX_AUDIO_CHUNKS or sum(map(len, chunks)) > MAX_AUDIO_BYTES:
                    raise RuntimeError("Piper audio exceeds local proof bound")
            elif AudioStop.is_type(event.type):
                break
            else:
                raise RuntimeError(f"unexpected Piper event: {event.type}")
    if audio_format is None or not chunks:
        raise RuntimeError("Piper returned no audible PCM")
    return audio_format, b"".join(chunks)


async def _transcribe(
    *, host: str, port: int, audio_format: AudioStart, audio: bytes
) -> str:
    """Use the official Wyoming client; no custom speech protocol is parsed."""

    async with AsyncTcpClient(host, port) as client:
        await client.write_event(Transcribe(language="nl").event())
        await client.write_event(
            AudioStart(
                rate=audio_format.rate,
                width=audio_format.width,
                channels=audio_format.channels,
            ).event()
        )
        for offset in range(0, len(audio), 4_096):
            await client.write_event(
                AudioChunk(
                    rate=audio_format.rate,
                    width=audio_format.width,
                    channels=audio_format.channels,
                    audio=audio[offset : offset + 4_096],
                ).event()
            )
        await client.write_event(AudioStop().event())
        while True:
            event = await asyncio.wait_for(client.read_event(), timeout=90)
            if event is None:
                raise RuntimeError("Whisper closed before transcript")
            if Transcript.is_type(event.type):
                transcript = Transcript.from_event(event).text.strip()
                if not transcript:
                    raise RuntimeError("Whisper returned a blank final transcript")
                return transcript
            raise RuntimeError(f"unexpected Whisper event: {event.type}")


def _admit(transcript: str) -> VoiceTurn:
    created_at = _canonical_now()
    deadline = (
        datetime.strptime(created_at, "%Y-%m-%dT%H:%M:%SZ") + timedelta(minutes=5)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    session = VoiceSession(
        contract="VoiceSession/v1",
        schema_version=1,
        session_id="local-v02-session",
        tenant_id="local-v02-tenant",
        installation_id="local-v02-installation",
        principal_id="local-v02-person",
        conversation_id="local-v02-conversation",
        audience="private",
        channel="local_test",
        device_binding_id="local-v02-device",
        identity_id="local-v02-identity",
        identity_pack_version=1,
        identity_pack_sha256="1" * 64,
        policy_id="local-v02-policy",
        policy_pack_version=1,
        policy_pack_sha256="2" * 64,
        privacy=VoicePrivacy(
            contract="VoicePrivacy/v1",
            schema_version=1,
            partial_transcript_retention="ephemeral",
            raw_audio_retention="none",
            external_provider_audio_retention="disabled",
            final_transcript_admission="conversation_admission_required",
        ).model_dump(mode="json"),
        created_at=created_at,
        deadline=deadline,
    )
    turn = VoiceTurn(
        contract="VoiceTurn/v1",
        schema_version=1,
        voice_turn_id="local-v02-voice-turn",
        session_id=session.session_id,
        tenant_id=session.tenant_id,
        installation_id=session.installation_id,
        principal_id=session.principal_id,
        conversation_id=session.conversation_id,
        conversation_message_id="local-v02-message",
        audience="private",
        transcript_state="final",
        final_transcript=transcript,
        final_transcript_sha256=_sha256(transcript),
        language="nl",
        reply_generation=1,
        finalized_at=created_at,
    )
    conversation_turn = admit_final_voice_turn(session=session, turn=turn)
    if conversation_turn.text != transcript:
        raise RuntimeError("Eva voice admission changed the final transcript")
    return turn


async def _run(args: argparse.Namespace) -> dict[str, object]:
    input_format, input_audio = await _synthesize(
        host=args.piper_host, port=args.piper_port, text=INPUT_TEXT
    )
    transcript = await _transcribe(
        host=args.whisper_host,
        port=args.whisper_port,
        audio_format=input_format,
        audio=input_audio,
    )
    # The synthetic test phrase is intentionally tiny: this checks wire and
    # admission integrity, not Dutch recognition quality.
    if _normalized(transcript) != _normalized(INPUT_TEXT):
        raise RuntimeError("local STT did not preserve the sealed synthetic phrase")
    admitted = _admit(transcript)
    reply_format, reply_audio = await _synthesize(
        host=args.piper_host, port=args.piper_port, text=REPLY_TEXT
    )
    return {
        "candidate": "V02-wyoming-local-voice-v1",
        "controller": {
            "requirementsSha256": _sha256(_experiment_file("requirements.lock").read_bytes()),
            "runnerSha256": _sha256(Path(__file__).read_bytes()),
            "wyoming": version("wyoming"),
            "pydantic": version("pydantic"),
        },
        "sidecarsExpected": {
            "piperImage": PIPER_IMAGE,
            "piperUpstream": "OHF-Voice/wyoming-piper@v2.4.2",
            "whisperImage": WHISPER_IMAGE,
            "whisperUpstream": "OHF-Voice/wyoming-faster-whisper@v3.6.0",
            "whisperModel": "base-int8",
        },
        "input": {
            "phraseSha256": _sha256(INPUT_TEXT),
            "voice": INPUT_VOICE,
            "pcm": {
                "rate": input_format.rate,
                "width": input_format.width,
                "channels": input_format.channels,
                "bytes": len(input_audio),
                "sha256": _sha256(input_audio),
            },
        },
        "voiceAdmission": {
            "contract": admitted.contract,
            "schemaVersion": admitted.schema_version,
            "transcriptState": admitted.transcript_state,
            "audience": admitted.audience,
            "language": admitted.language,
            "finalTranscriptSha256": admitted.final_transcript_sha256,
        },
        "reply": {
            "phraseSha256": _sha256(REPLY_TEXT),
            "pcm": {
                "rate": reply_format.rate,
                "width": reply_format.width,
                "channels": reply_format.channels,
                "bytes": len(reply_audio),
                "sha256": _sha256(reply_audio),
            },
        },
        "nonclaims": [
            "No microphone, speaker, real person, audio recording or hardware path was used.",
            "No speech-provider credential, LLM reply, tool call, persistence or production route was used.",
            "This run alone does not prove that a model-cache restart is fully offline.",
            "This is not full-duplex voice, a barge-in proof, a latency measurement or a semantic accuracy benchmark.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--piper-host", default="127.0.0.1")
    parser.add_argument("--piper-port", type=int, required=True)
    parser.add_argument("--whisper-host", default="127.0.0.1")
    parser.add_argument("--whisper-port", type=int, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    result = asyncio.run(_run(args))
    args.receipt.write_text(
        json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))


if __name__ == "__main__":
    main()
