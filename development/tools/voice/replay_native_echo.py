"""Offline APM diagnostic; never opens a device, network, or Eva conversation.

Uses pinned public speech excerpts and an explicitly supplied installed greeting.
Run inside the existing speaker image with --network none; see the JSON protocol.
The local Silero model is a detection proxy, not production ASR or word accuracy.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import wave

import numpy as np
from av import AudioFrame, AudioResampler
from livekit import rtc
from livekit.agents.vad import VADEventType
from livekit.plugins import silero

RATE, BLOCK = 16_000, 160
FIXTURES = {
    "far": "bc3c3cacc16a603361e5fe927346f97eef7ae23f2cc9d12daad43b451c3668f4",
    "near": "002ae38b132806de58f16cf2533b21a4de57c92b1bb847cf8bfcddfade7413d8",
}


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def samples(path):
    if path.stat().st_size > 20_000_000:
        raise ValueError("WAV fixture exceeds the diagnostic bound")
    with wave.open(str(path)) as source:
        rate, channels = source.getframerate(), source.getnchannels()
        if (source.getsampwidth() != 2 or channels not in (1, 2)
                or not 8_000 <= rate <= 96_000 or source.getnframes() > rate * 30):
            raise ValueError("Expected bounded PCM16 mono/stereo WAV")
        raw = source.readframes(source.getnframes())
    mono = np.frombuffer(raw, dtype="<i2").reshape(-1, channels).mean(axis=1)
    pcm = mono.astype("<i2").tobytes()
    frame = AudioFrame(format="s16", layout="mono", samples=len(pcm) // 2)
    frame.sample_rate = rate
    frame.planes[0].update(pcm)
    resampler = AudioResampler(format="s16", layout="mono", rate=RATE)
    frames = resampler.resample(frame) + resampler.resample(None)
    return np.concatenate([
        np.frombuffer(bytes(item.planes[0])[:item.samples * 2], dtype="<i2")
        for item in frames
    ]).astype(float)


def excerpt(path, kind):
    if digest(path) != FIXTURES[kind]:
        raise ValueError("Public fixture hash differs from the fixed protocol")
    data = samples(path)
    # The original files alternate speech. Select active, independent speech
    # before constructing overlap; their original silent openings prove nothing.
    data = data[RATE:4 * RATE] if kind == "far" else data[:RATE]
    rms = np.sqrt(np.mean(data ** 2))
    if rms < 1:
        raise ValueError("Speech fixture is silent")
    return np.tile(data, 7)[:7 * RATE] / (rms + 1) * 2_500


def frame(pcm):
    return rtc.AudioFrame(np.clip(pcm, -32768, 32767).astype("<i2").tobytes(),
                          RATE, 1, len(pcm))


def replay(greeting, far_speech, near_speech, gain, clipped, automatic_gain):
    answer_at = (int((len(greeting) / RATE + 1) * 100) + 1) * BLOCK
    size = answer_at + 8 * RATE
    far, near, echo = (np.zeros(size) for _ in range(3))
    far[:len(greeting)] = greeting
    far[answer_at:answer_at + len(far_speech)] = far_speech
    begin = answer_at + RATE // 5
    near[begin:begin + len(near_speech)] = near_speech * gain
    distorted = np.clip(far, -2_200, 2_200) if clipped else far
    for delay_ms, amplitude in ((120, .65), (147, .22), (181, -.13)):
        delay = delay_ms * RATE // 1_000
        echo[delay:] += amplitude * distorted[:-delay]
    microphone = near + echo
    processor = rtc.AudioProcessingModule(
        echo_cancellation=True, auto_gain_control=automatic_gain)
    cleaned = []
    for offset in range(0, size, BLOCK):
        processor.process_reverse_stream(frame(far[offset:offset + BLOCK]))
        capture = frame(microphone[offset:offset + BLOCK])
        processor.set_stream_delay_ms(120)
        processor.process_stream(capture)
        cleaned.append(np.array(capture.data, dtype=float, copy=True))
    return np.concatenate(cleaned), near, echo, begin / RATE, answer_at / RATE


async def detection(vad, pcm):
    stream = vad.stream()
    starts, maximum = [], 0.0
    try:
        for offset in range(0, len(pcm), BLOCK * 2):
            stream.push_frame(frame(pcm[offset:offset + BLOCK * 2]))
        stream.end_input()
        async for event in stream:
            if event.type == VADEventType.START_OF_SPEECH:
                starts.append(round(event.timestamp, 3))
            if event.type == VADEventType.INFERENCE_DONE:
                maximum = max(maximum, event.probability)
    finally:
        await stream.aclose()
    return {"starts": starts, "max_probability": round(maximum, 4)}


async def measure(args):
    far, near = excerpt(args.far_wav, "far"), excerpt(args.near_wav, "near")
    greeting = samples(args.greeting_wav)
    if not 0 < len(greeting) <= 5 * RATE:
        raise ValueError("Expected one bounded installed greeting")
    # Pin the already-used detector settings; do not fit them to these fixtures.
    vad = silero.VAD.load(min_speech_duration=.2, min_silence_duration=.45,
                          activation_threshold=.7, onnx_file_path=args.vad_model)
    rows = []
    for automatic_gain in (False, True):
        for gain, clipped in ((0, False), (0, True), (1, False),
                              (.2, False), (1, True), (.2, True)):
            output, clean, echo, begin, answer = replay(
                greeting, far, near, gain, clipped, automatic_gain)
            rows.append({
                "automatic_gain": automatic_gain, "near_gain": gain,
                "clipped": clipped, "expected_near_begin": begin,
                "answer_begin": answer,
                "processed_vad": await detection(vad, output),
                "near_only_vad": await detection(vad, clean),
                "echo_input_rms": round(float(np.sqrt(np.mean(echo ** 2))), 2),
                "processed_first_500ms_rms": round(float(np.sqrt(np.mean(
                    output[int(begin * RATE):int((begin + .5) * RATE)] ** 2))), 2),
            })
    return {
        "recordedAt": datetime.now(UTC).isoformat(),
        "status": "offline_measurement_completed_not_physical_acceptance",
        "versions": {name: importlib.metadata.version(name)
                     for name in ("livekit", "livekit-plugins-silero", "av", "numpy")},
        "sha256": {name: digest(path) for name, path in {
            "harness": Path(__file__), "far": args.far_wav, "near": args.near_wav,
            "greeting": args.greeting_wav, "vad_model": args.vad_model,
        }.items()},
        "cases": rows,
        "physicalAudio": False, "productionCapture": False,
        "externalModelCalls": 0, "rawAudioSaved": False,
        "limits": "Fixed synthetic echo path and local VAD only; no DAC timing, "
                  "production transcript, word accuracy or household acceptance proof.",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("far-wav", "near-wav", "greeting-wav", "vad-model", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; retain prior failure evidence")
    result = asyncio.run(measure(args))
    descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print("Offline measurement saved; no production acceptance claimed.")


if __name__ == "__main__":
    main()
