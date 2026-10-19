"""Real local TLS/control lifecycle; media, core and hardware are explicit doubles."""
import asyncio
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import ipaddress
from pathlib import Path
import secrets
import ssl
import sys
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from eva.voice_service.agent import HouseholdCoreClient

sys.path.insert(0, str(Path(__file__).parents[1] / "operations"))
from test_native_duplex_speaker_endpoint import client, wait_released
from test_native_duplex_speaker_input import NativePeer, Kind
from reply_lane import HouseholdBinding
from speaker_endpoint import SpeakerEndpoint, SpeakerGrant

_path = Path(__file__).parents[3] / "deploy/native-speaker/codex_service.py"
_spec = importlib.util.spec_from_file_location("native_recovery_service", _path)
service = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(service)


@pytest.fixture
def tls_pair(tmp_path):
    # The installed image already includes cryptography for its media/TLS stack.
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                         serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_2
    server.load_cert_chain(cert_path, key_path)
    return server, ssl.create_default_context(cafile=str(cert_path))


def configured(session, **kwargs):
    token = secrets.token_urlsafe(32)
    binding = HouseholdBinding("assist_satellite.test", "a" * 32, "ha-conversation", "nl-NL")
    grant = SpeakerGrant(binding, hashlib.sha256(token.encode()).hexdigest())
    return SpeakerEndpoint(grants=(grant,), pcm=None, session=session,
                           receipt_timeout=.2, **kwargs), token, binding


class Media:
    def __init__(self):
        self.input = SimpleNamespace(audio=None)
        self.output = SimpleNamespace(audio=None)
        self.callbacks = {}
        self.started = asyncio.Event()
        self.closed = False
        self.agent = None

    def on(self, name, callback):
        self.callbacks[name] = callback

    async def start(self, agent):
        self.agent = agent
        self.started.set()

    async def aclose(self):
        self.closed = True
        if callback := self.callbacks.get("close"):
            callback(None)


class Candidate(service.CodexHouseholdSpeakerSession):
    def __init__(self, check=lambda: None):
        super().__init__(core_url="http://127.0.0.1:1", household_token="test-household",
                         stt_source=None, tts_source=None, vad=None, check=check)
        self.media = []

    def make_session(self):
        media = Media()
        self.media.append(media)
        return media


async def connected(endpoint, token, tls_pair, scenario):
    async with endpoint.listen(host="127.0.0.1", port=0, tls=tls_pair[0]) as server:
        uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
        await scenario(uri)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", [Kind.END_SESSION, Kind.MUTED])
async def test_core_outage_during_standby_preserves_socket_and_ice_until_fresh_wake(
    tls_pair, monkeypatch, ending
):
    calls = []
    healthy = False

    async def ready(backend):
        calls.append(healthy)
        return healthy

    monkeypatch.setattr(HouseholdCoreClient, "ready", ready)
    candidate = Candidate()
    endpoint, token, _ = configured(candidate, microphone_enabled=True, lifetime_from_wake=True)

    async def scenario(uri):
        nonlocal healthy
        peer = NativePeer()
        async with client(uri, token, tls_pair[1]) as socket:
            device = asyncio.create_task(peer.handle(socket))
            try:
                await asyncio.wait_for(peer.opened.wait(), 1)
                await asyncio.sleep(.05)
                assert calls == [], "standby must not ask a restarting core for admission"
                assert len(candidate.media) == 1 and not candidate.media[0].closed
                assert peer.captures.empty() and not candidate.media[0].started.is_set()
                healthy = True
                await peer.wake()
                assert await asyncio.wait_for(peer.captures.get(), 1) == 1
                await asyncio.wait_for(candidate.media[0].started.wait(), 1)
                assert calls == [True] and len(candidate.media) == 1
                await peer.send(ending)
                await asyncio.wait_for(device, 2)
                await wait_released(endpoint)
                assert candidate.media[0].closed
            finally:
                await socket.close()
                await asyncio.gather(device, return_exceptions=True)

    await connected(endpoint, token, tls_pair, scenario)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["network", "unauthorized", "contract"])
async def test_failed_wake_closes_without_capture_and_next_connection_requires_new_wake(
    tls_pair, monkeypatch, failure
):
    calls = []
    healthy = False

    async def ready(backend):
        calls.append(healthy)
        if not healthy:
            if failure == "network":
                raise httpx.ConnectError("synthetic unreachable core")
            if failure == "contract":
                raise ValueError("synthetic invalid ready contract")
            return False  # Existing ready() rejects 401/403 without admitting a session.
        return True

    monkeypatch.setattr(HouseholdCoreClient, "ready", ready)
    candidate = Candidate()
    endpoint, token, _ = configured(candidate, microphone_enabled=True)

    async def scenario(uri):
        nonlocal healthy
        for attempt in range(2):
            peer = NativePeer()
            async with client(uri, token, tls_pair[1]) as socket:
                device = asyncio.create_task(peer.handle(socket))
                try:
                    await asyncio.wait_for(peer.opened.wait(), 1)
                    await asyncio.sleep(.03)
                    assert len(calls) == attempt and peer.captures.empty()
                    await peer.wake()
                    if attempt:
                        assert await asyncio.wait_for(peer.captures.get(), 1) == 1
                        await asyncio.wait_for(candidate.media[-1].started.wait(), 1)
                        await peer.send(Kind.END_SESSION)
                    await asyncio.wait_for(device, 2)
                    await wait_released(endpoint)
                    assert candidate.media[-1].closed
                    if not attempt:
                        assert peer.captures.empty()
                finally:
                    await socket.close()
                    await asyncio.gather(device, return_exceptions=True)
            healthy = True
        assert calls == [False, True] and len(candidate.media) == 2
        assert candidate.media[0] is not candidate.media[1]

    await connected(endpoint, token, tls_pair, scenario)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 503])
async def test_active_core_failure_releases_device_and_new_wake_gets_new_lease(
    tls_pair, monkeypatch, status
):
    operations = []
    assigned = []
    fail = True

    async def ready(backend):
        backend.household_work_returns = True
        return True

    async def work_return(backend, operation, session_id, **identity):
        operations.append(operation)
        if operation == "open":
            assigned.append(str(uuid4()))
            backend._work_session = assigned[-1]
            return {"session_id": assigned[-1]}
        assert session_id == backend._work_session
        if operation == "poll":
            if fail:
                response = httpx.Response(status, request=httpx.Request("POST", "http://local.invalid/poll"))
                response.raise_for_status()
            return {"result": None}
        return {}

    monkeypatch.setattr(HouseholdCoreClient, "ready", ready)
    monkeypatch.setattr(HouseholdCoreClient, "work_return", work_return)
    candidate = Candidate()
    endpoint, token, _ = configured(candidate, microphone_enabled=True)

    async def scenario(uri):
        nonlocal fail
        for attempt in range(2):
            peer = NativePeer()
            async with client(uri, token, tls_pair[1]) as socket:
                device = asyncio.create_task(peer.handle(socket))
                try:
                    await asyncio.wait_for(peer.opened.wait(), 1)
                    await asyncio.sleep(.03)
                    assert peer.captures.empty() and len(assigned) == attempt
                    await peer.wake()
                    assert await asyncio.wait_for(peer.captures.get(), 1) == 1
                    await asyncio.wait_for(candidate.media[-1].started.wait(), 1)
                    if attempt:
                        await peer.send(Kind.END_SESSION)
                    await asyncio.wait_for(device, 2)
                    await wait_released(endpoint)
                    assert candidate.media[-1].closed
                finally:
                    await socket.close()
                    await asyncio.gather(device, return_exceptions=True)
            fail = False
        assert len(set(assigned)) == 2
        assert operations.count("open") == operations.count("close") == 2

    await connected(endpoint, token, tls_pair, scenario)


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["disconnect", "revoke"])
async def test_standby_cleanup_closes_prewarm_without_admitting_core(tls_pair, monkeypatch, ending):
    allowed = True

    def check():
        if not allowed:
            raise ValueError("synthetic authority revoked")

    async def ready(backend):
        raise AssertionError("standby cannot call core readiness")

    monkeypatch.setattr(HouseholdCoreClient, "ready", ready)
    candidate = Candidate(check)
    endpoint, token, _ = configured(candidate, microphone_enabled=True)

    async def scenario(uri):
        nonlocal allowed
        peer = NativePeer()
        async with client(uri, token, tls_pair[1]) as socket:
            device = asyncio.create_task(peer.handle(socket))
            try:
                await asyncio.wait_for(peer.opened.wait(), 1)
                await asyncio.sleep(.03)
                assert len(candidate.media) == 1
                if ending == "disconnect":
                    await socket.close()
                else:
                    allowed = False
                await asyncio.wait_for(device, 2)
                await wait_released(endpoint)
                assert candidate.media[0].closed and peer.captures.empty()
            finally:
                await socket.close()
                await asyncio.gather(device, return_exceptions=True)

    await connected(endpoint, token, tls_pair, scenario)
