import asyncio
from dataclasses import dataclass
from functools import wraps
import importlib
import io
from pathlib import Path
import struct
import subprocess
import sys
import wave

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "experiments/native-duplex-v1"))
pcm_module = importlib.import_module("ha_pcm")


def run_async(function):
    @wraps(function)
    def run(*args, **kwargs):
        return asyncio.run(function(*args, **kwargs))
    return run


@dataclass
class Chunk:
    data: bytes
    mime_type: str


def wav_bytes():
    audio = b"".join(struct.pack("<h", (n % 100 - 50) * 100) for n in range(2400))
    stream = io.BytesIO()
    with wave.open(stream, "wb") as f:
        f.setnchannels(1); f.setsampwidth(2); f.setframerate(24000)
        f.writeframes(audio)
    return stream.getvalue(), audio


class Provider:
    def __init__(self, encoded, mime="audio/wav"):
        self.encoded, self.mime = encoded, mime
        self.closed = False

    async def synthesize(self, text):
        try:
            for offset in range(0, len(self.encoded), 333):
                yield Chunk(self.encoded[offset:offset+333], self.mime)
        finally:
            self.closed = True


@pytest.fixture
def processes(monkeypatch):
    created = []
    original = asyncio.create_subprocess_exec

    async def spawn(*args, **kwargs):
        process = await original(*args, **kwargs)
        created.append(process)
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    return created


@run_async
async def test_wav_preserves_every_sample_and_closes_process(processes):
    encoded, expected = wav_bytes()
    provider = Provider(encoded)
    decoder = pcm_module.HomeAssistantPCM(provider)
    chunks = [chunk async for chunk in decoder.synthesize_pcm("proef")]
    assert all(0 < len(chunk) <= 960 and len(chunk) % 2 == 0 for chunk in chunks)
    assert b"".join(chunks) == expected
    assert provider.closed and processes[0].returncode == 0


@pytest.mark.parametrize("codec,mime", [("flac", "audio/flac"), ("mp3", "audio/mpeg")])
@run_async
async def test_existing_ha_formats_decode_in_memory(codec, mime, processes):
    encoded, _ = wav_bytes()
    result = subprocess.run(["/usr/bin/ffmpeg", "-hide_banner", "-loglevel", "error",
                             "-i", "pipe:0", "-f", codec, "pipe:1"],
                            input=encoded, check=True, capture_output=True)
    provider = Provider(result.stdout, mime)
    chunks = [chunk async for chunk in pcm_module.HomeAssistantPCM(provider).synthesize_pcm("proef")]
    assert chunks and all(0 < len(c) <= 960 and len(c) % 2 == 0 for c in chunks)
    assert provider.closed and processes[0].returncode == 0


@run_async
async def test_excessive_decoded_duration_is_failure_not_truncated_success(processes):
    provider = Provider(wav_bytes()[0])
    decoder = pcm_module.HomeAssistantPCM(provider, max_frames=32)
    with pytest.raises(pcm_module.WireError, match="duration bound"):
        _ = [chunk async for chunk in decoder.synthesize_pcm("proef")]
    assert provider.closed and processes[0].returncode is not None


@run_async
async def test_invalid_encoded_audio_fails_and_closes(processes):
    provider = Provider(b"not an audio file")
    with pytest.raises(pcm_module.WireError):
        _ = [chunk async for chunk in pcm_module.HomeAssistantPCM(provider).synthesize_pcm("proef")]
    assert provider.closed and processes[0].returncode is not None


@run_async
async def test_cancellation_stops_decoder_and_ha_iterator(processes):
    started, closed = asyncio.Event(), asyncio.Event()

    class SlowProvider:
        async def synthesize(self, text):
            try:
                yield Chunk(wav_bytes()[0][:44], "audio/wav")
                started.set()
                await asyncio.Event().wait()
            finally:
                closed.set()

    decoder = pcm_module.HomeAssistantPCM(SlowProvider())
    source = decoder.synthesize_pcm("proef")
    read = asyncio.create_task(anext(source))
    await asyncio.wait_for(started.wait(), 1)
    read.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(read, 1)
    assert closed.is_set() and processes[0].returncode is not None


@run_async
async def test_sync_provider_setup_failure_does_not_leak_decoder(processes):
    class BrokenProvider:
        def synthesize(self, text):
            raise ValueError("provider setup failed")

    with pytest.raises(ValueError, match="provider setup failed"):
        _ = [chunk async for chunk in pcm_module.HomeAssistantPCM(BrokenProvider()).synthesize_pcm("proef")]
    assert processes[0].returncode is not None


@run_async
async def test_stalled_provider_read_timeout_cleans_up(processes):
    closed = asyncio.Event()

    class ProviderStalled:
        async def synthesize(self, text):
            try:
                await asyncio.Event().wait()
                yield
            finally:
                closed.set()

    decoder = pcm_module.HomeAssistantPCM(ProviderStalled(), read_timeout=0.03)
    with pytest.raises(TimeoutError):
        _ = [chunk async for chunk in decoder.synthesize_pcm("proef")]
    assert closed.is_set() and processes[0].returncode is not None
