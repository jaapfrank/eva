"""Explicit candidate service using the existing HA adapters and native client.

No installer, auto-provisioning, model provider or deployment action. Private
configuration supplies authority; registry readback confirms its HA bindings.
"""
from __future__ import annotations

import argparse
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import importlib.util
import ipaddress
import json
import os
from pathlib import Path
import signal
import ssl
import stat
import sys
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from websockets.asyncio.client import connect

from eva.adapters.connectors.home_assistant_voice import HomeAssistantSpeechInput, HomeAssistantSpeechOutput
from ha_pcm import HomeAssistantPCM
from prepare_vad import verify_model, verified_detector
from reply_lane import HouseholdBinding
from speaker_endpoint import SpeakerEndpoint, SpeakerGrant
from speaker_session import HouseholdSpeakerSession

HERE = Path(__file__).resolve().parent


def native_client_class():
    """Load the same client shipped to HA, without importing Home Assistant."""
    name = "eva_native_speaker_http_client"
    if name not in sys.modules:
        path = HERE.parents[1] / "deploy/home-assistant/custom_components/eva2_voice/client.py"
        if not path.is_file() and HERE == Path("/app/speaker"):
            path = Path("/app/deploy/home-assistant/custom_components/eva2_voice/client.py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].EvaVoiceClient


class SpeakerServiceError(RuntimeError):
    """Sanitized operational error: no credentials, transcripts or provider data."""


def private_bytes(path: Path, *, limit: int = 65536) -> bytes:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > limit:
            raise ValueError("configuration/key must be a bounded owner-only regular file")
        data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("private file exceeds its bound")
        return data


@dataclass(frozen=True, repr=False)
class ServiceConfig:
    host: str
    port: int
    certificate: Path
    private_key: Path
    model: Path
    ha_url: str
    ha_token: str = field(repr=False)
    pipeline_id: str
    pipeline_language: str
    stt_engine: str
    tts_engine: str
    tts_language: str
    eva_url: str
    eva_token: str = field(repr=False)
    grants: tuple[SpeakerGrant, ...]

    def __post_init__(self):
        ipaddress.ip_address(self.host)
        if type(self.port) is not int or not 0 <= self.port <= 65535:
            raise ValueError("invalid listener port")
        for path in (self.certificate, self.private_key, self.model):
            if not isinstance(path, Path) or not path.is_absolute():
                raise ValueError("candidate file paths must be absolute")
        for token in (self.ha_token, self.eva_token):
            if (not isinstance(token, str) or not token or len(token) > 8192
                    or any(ord(c) < 32 or ord(c) == 127 for c in token)):
                raise ValueError("invalid configured service credential")
        ha = urlsplit(self.ha_url)
        _ = ha.port
        if (ha.scheme not in {"http", "https"} or not ha.hostname or ha.username or ha.password
                or ha.query or ha.fragment or ha.path not in {"", "/"}):
            raise ValueError("invalid Home Assistant origin")
        for value in (self.pipeline_id, self.pipeline_language, self.stt_engine,
                      self.tts_engine, self.tts_language):
            if not isinstance(value, str) or not value.strip() or len(value) > 200:
                raise ValueError("invalid pinned speech configuration")
        native_client_class().validate_config(url=self.eva_url, token=self.eva_token,
                    satellite_ids=frozenset(g.binding.satellite_id for g in self.grants))
        # Apply the endpoint's unique-device/credential and lifetime validation
        # before any external I/O. No socket is opened by its constructor.
        SpeakerEndpoint(grants=self.grants, pcm=None, session=None)
        import re
        for grant in self.grants:
            if (not re.fullmatch(r"[0-9a-f]{32}", grant.binding.device_id)
                    or len(grant.binding.conversation_id) > 200 or len(grant.binding.language) > 32):
                raise ValueError("invalid native household binding")

    @classmethod
    def load(cls, path: Path):
        data = json.loads(private_bytes(path))
        expected = set(cls.__dataclass_fields__)
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("candidate configuration fields differ")
        data["grants"] = tuple(SpeakerGrant(HouseholdBinding(**row["binding"]), row["credential_sha256"])
                               for row in data["grants"])
        for key in ("certificate", "private_key", "model"):
            data[key] = Path(data[key])
        return cls(**data)


async def verify_home_assistant(config: ServiceConfig) -> dict:
    """Read registry and pipeline; never change HA, record audio or run a model."""
    parsed = urlsplit(config.ha_url)
    url = urlunsplit(("wss" if parsed.scheme == "https" else "ws", parsed.netloc, "/api/websocket", "", ""))
    try:
        async with asyncio.timeout(15):
            async with connect(url, proxy=None, open_timeout=5, close_timeout=1,
                               max_size=4_000_000, max_queue=2) as socket:
                if json.loads(await socket.recv()).get("type") != "auth_required":
                    raise SpeakerServiceError("HA authentication failed")
                await socket.send(json.dumps({"type": "auth", "access_token": config.ha_token}))
                if json.loads(await socket.recv()).get("type") != "auth_ok":
                    raise SpeakerServiceError("HA authentication failed")
                async def query(index, kind, **values):
                    await socket.send(json.dumps({"id": index, "type": kind, **values}))
                    reply = json.loads(await socket.recv())
                    if reply.get("type") != "result" or reply.get("id") != index or reply.get("success") is not True:
                        raise SpeakerServiceError("HA binding readback failed")
                    return reply["result"]
                entities = await query(1, "config/entity_registry/list")
                devices = await query(2, "config/device_registry/list")
                pipeline = await query(3, "assist_pipeline/pipeline/get", pipeline_id=config.pipeline_id)
        if not isinstance(entities, list) or not isinstance(devices, list) or not isinstance(pipeline, dict):
            raise SpeakerServiceError("HA binding readback failed")
        for grant in config.grants:
            binding = grant.binding
            matches = [e for e in entities if e.get("entity_id") == binding.satellite_id]
            found = [d for d in devices if d.get("id") == binding.device_id]
            if (len(matches) != 1 or len(found) != 1 or matches[0].get("disabled_by")
                    or found[0].get("disabled_by") or matches[0].get("platform") != "esphome"
                    or matches[0].get("device_id") != binding.device_id):
                raise SpeakerServiceError("HA speaker device binding differs")
        expected = {"id": config.pipeline_id, "language": config.pipeline_language,
                    "stt_engine": config.stt_engine, "tts_engine": config.tts_engine,
                    "tts_language": config.tts_language, "tts_voice": None}
        # Direct HA TTS currently uses the pipeline's default voice. Reject a
        # configured non-default voice rather than silently sounding different.
        if any(pipeline.get(key) != value for key, value in expected.items()):
            raise SpeakerServiceError("HA speech pipeline binding differs")
        return {"haBindingVerified": True, "speakerCount": len(config.grants),
                "pipelinePinned": True, "providerCalls": 0}
    except asyncio.CancelledError:
        raise
    except SpeakerServiceError:
        raise
    except Exception:
        raise SpeakerServiceError("HA binding verification unavailable") from None


def server_tls(config: ServiceConfig) -> ssl.SSLContext:
    private_bytes(config.private_key)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(config.certificate, config.private_key)
    return context


@asynccontextmanager
async def open_service(config: ServiceConfig):
    """One explicit lifetime; shutdown leaves sockets alive for stop receipts."""
    tls = server_tls(config)
    verify_model(config.model)
    # Validate runtime/model dependencies before accepting a speaker.
    verified_detector(config.model)
    await verify_home_assistant(config)
    speech_input = HomeAssistantSpeechInput(base_url=config.ha_url, token=config.ha_token,
        pipeline_id=config.pipeline_id, expected_stt_engine=config.stt_engine,
        expected_pipeline_language=config.pipeline_language)
    speech_output = HomeAssistantSpeechOutput(base_url=config.ha_url, token=config.ha_token,
        pipeline_id=config.pipeline_id, tts_engine=config.tts_engine, tts_language=config.tts_language)
    pcm = HomeAssistantPCM(speech_output)
    if not os.access(pcm._ffmpeg, os.X_OK):
        raise SpeakerServiceError("audio decoder is unavailable")
    http = aiohttp.ClientSession(trust_env=False)
    client = endpoint = server = None
    try:
        client = native_client_class()(http, url=config.eva_url, token=config.eva_token,
                       satellite_ids=frozenset(g.binding.satellite_id for g in config.grants))
        for grant in config.grants:
            await client.verify_playback_support(satellite_id=grant.binding.satellite_id,
                                                  device_id=grant.binding.device_id)
        endpoint = SpeakerEndpoint(grants=config.grants, pcm=pcm.synthesize_pcm,
            session=HouseholdSpeakerSession(client=client, speech_input=speech_input,
                detector_factory=lambda: verified_detector(config.model), report_playback=True),
            microphone_enabled=True)
        server = await endpoint.listen(host=config.host, port=config.port, tls=tls)
        yield server, endpoint
    finally:
        async def shutdown():
            if server is not None:
                server.close(close_connections=False)
            try:
                if endpoint is not None:
                    await endpoint.close()
            finally:
                try:
                    if client is not None:
                        await client.close()
                finally:
                    try:
                        if server is not None:
                            server.close()
                            await server.wait_closed()
                    finally:
                        await http.close()
        # The same owner retains physical stop, native HTTP interrupt tasks,
        # sockets and the HTTP pool. Repeated outer cancel cannot abandon any.
        cleanup = asyncio.create_task(shutdown())
        cancelled = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                cancelled = True
        await cleanup
        if cancelled:
            raise asyncio.CancelledError


async def main(config: ServiceConfig, *, check_only: bool):
    if check_only:
        server_tls(config)
        verified_detector(config.model)
        print(json.dumps(await verify_home_assistant(config)), flush=True)
        return
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for kind in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(kind, stop.set)
    try:
        async with open_service(config) as (server, _):
            print(json.dumps({"state": "listening", "port": server.sockets[0].getsockname()[1],
                              "speakerCount": len(config.grants)}), flush=True)
            await stop.wait()
    finally:
        for kind in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(kind)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--check-only", action="store_true")
    args = parser.parse_args()
    try:
        asyncio.run(main(ServiceConfig.load(args.config), check_only=args.check_only))
    except Exception:
        print('{"state":"failed","detail":"Speaker candidate failed; private configuration/provider details suppressed."}', file=sys.stderr)
        raise SystemExit(1) from None
