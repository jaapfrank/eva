"""Real TLS/WebSocket admission; playback peer explicitly simulates hardware."""
import asyncio
import hashlib
import secrets
import ssl
import subprocess

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import InvalidStatus

from test_native_duplex_speaker_wire import run_async, SpeakerPeer, pcm, wire
from speaker_endpoint import SpeakerEndpoint, SpeakerGrant
from reply_lane import HouseholdBinding


@pytest.fixture
def tls_pair(tmp_path):
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                    "-keyout", str(key), "-out", str(cert), "-days", "1",
                    "-subj", "/CN=localhost", "-addext", "subjectAltName=IP:127.0.0.1"],
                   check=True, capture_output=True)
    server = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server.minimum_version = ssl.TLSVersion.TLSv1_2
    server.load_cert_chain(cert, key)
    client = ssl.create_default_context(cafile=str(cert))
    return server, client


def configured(session, **kwargs):
    token = secrets.token_urlsafe(32)
    binding = HouseholdBinding("assist_satellite.test", "configured-device", "ha-conversation", "nl-NL")
    grant = SpeakerGrant(binding, hashlib.sha256(token.encode()).hexdigest())
    return SpeakerEndpoint(grants=(grant,), pcm=pcm, session=session, receipt_timeout=.2, **kwargs), token, binding


def client(uri, token, tls, **kwargs):
    return connect(uri, ssl=tls, proxy=None, compression=None, max_size=2048,
                   additional_headers={"Authorization": "Bearer " + token}, **kwargs)


async def wait_released(endpoint):
    async with asyncio.timeout(2):
        while endpoint._active:
            await asyncio.sleep(.001)


@run_async
async def test_tls_authentication_precedes_session_or_audio(tls_pair):
    calls = []
    async def session(binding, output):
        calls.append(binding)
    endpoint, token, binding = configured(session)
    with pytest.raises(ValueError, match="TLS server"):
        endpoint.listen(host="127.0.0.1", port=0, tls=None)
    async with endpoint.listen(host="127.0.0.1", port=0, tls=tls_pair[0]) as server:
        uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
        with pytest.raises(ssl.SSLCertVerificationError):
            async with client(uri, token, ssl.create_default_context()):
                pass
        for headers, suffix, expected in [
            ({}, "", 401),
            ({"Authorization": "Bearer " + secrets.token_urlsafe(32)}, "", 401),
            ({"Authorization": "Bearer " + token, "Origin": "https://foreign.invalid"}, "", 401),
            ([("Authorization", "Bearer " + token)] * 2, "", 401),
            ({"Authorization": "Bearer " + token}, "?device=another", 404),
        ]:
            with pytest.raises(InvalidStatus) as error:
                async with connect(uri + suffix, ssl=tls_pair[1], proxy=None, additional_headers=headers):
                    pass
            assert error.value.response.status_code == expected
        assert calls == [] and not endpoint._active


@run_async
async def test_owner_binding_exclusive_device_and_revocation_stop(tls_pair):
    observed, cleaned = [], asyncio.Event()
    async def session(binding, output):
        observed.append(binding)
        try:
            await output.speak(generation=1, text="onderbreekbaar antwoord")
        finally:
            cleaned.set()
    endpoint, token, binding = configured(session)
    peer = SpeakerPeer(); peer.stopped.set()
    async with endpoint.listen(host="127.0.0.1", port=0, tls=tls_pair[0]) as server:
        uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
        async with client(uri, token, tls_pair[1]) as socket:
            peer_task = asyncio.create_task(peer.handle(socket))
            try:
                await asyncio.wait_for(peer.end_received.wait(), 2)
                assert observed == [binding]
                with pytest.raises(InvalidStatus) as conflict:
                    async with client(uri, token, tls_pair[1]):
                        pass
                assert conflict.value.response.status_code == 409
                await endpoint.revoke(device_id=binding.device_id)
                assert cleaned.is_set() and peer.stop_received.is_set()
                await wait_released(endpoint)
                with pytest.raises(InvalidStatus) as revoked:
                    async with client(uri, token, tls_pair[1]):
                        pass
                assert revoked.value.response.status_code == 401
            finally:
                await asyncio.gather(peer_task, return_exceptions=True)


@run_async
async def test_known_previous_receipt_cannot_break_reconnect(tls_pair):
    calls = []
    async def session(binding, output):
        calls.append(output.session)
        await output.speak(generation=1, text="nieuw antwoord")
    endpoint, token, binding = configured(session)
    async with endpoint.listen(host="127.0.0.1", port=0, tls=tls_pair[0]) as server:
        uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
        for attempt in range(2):
            peer = SpeakerPeer(); peer.finished.set(); peer.stopped.set()
            async with client(uri, token, tls_pair[1]) as socket:
                if attempt:
                    await socket.send(wire.Packet(wire.Kind.FINISHED, calls[0], 1, 2,
                                                  (580).to_bytes(4, "big")).encode())
                await peer.handle(socket)
            await wait_released(endpoint)
            assert peer.end_received.is_set()
        assert len(calls)==2 and calls[0] != calls[1]


@run_async
async def test_unknown_nonce_is_not_treated_as_retired(tls_pair):
    calls = []
    async def session(binding, output):
        calls.append(binding)
    endpoint, token, binding = configured(session)
    async with endpoint.listen(host="127.0.0.1", port=0, tls=tls_pair[0]) as server:
        uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
        async with client(uri, token, tls_pair[1]) as socket:
            await socket.send(wire.Packet(wire.Kind.OPENED, b"unknown!").encode())
            await socket.wait_closed()
        await wait_released(endpoint)
        assert not calls


@run_async
async def test_connection_loss_and_session_expiry_cancel_work(tls_pair):
    for disconnect in (True, False):
        entered, cleaned = asyncio.Event(), asyncio.Event()
        async def session(binding, output):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cleaned.set()
        endpoint, token, binding = configured(session, max_session_seconds=.1 if not disconnect else 2)
        peer = SpeakerPeer()
        async with endpoint.listen(host="127.0.0.1", port=0, tls=tls_pair[0]) as server:
            uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
            async with client(uri, token, tls_pair[1]) as socket:
                peer_task = asyncio.create_task(peer.handle(socket))
                await asyncio.wait_for(entered.wait(), 2)
                if disconnect:
                    await socket.close()
                await asyncio.wait_for(cleaned.wait(), 2)
                await asyncio.gather(peer_task, return_exceptions=True)
            await wait_released(endpoint)


def test_duplicate_credentials_or_devices_cannot_create_ambiguous_binding():
    async def session(binding, output):
        pass
    endpoint, token, binding = configured(session)
    with pytest.raises(ValueError, match="unique"):
        SpeakerEndpoint(grants=endpoint._grants * 2, pcm=pcm, session=session)


def test_failure_diagnostics_keep_structural_cause_without_private_error_text(capsys):
    async def session(binding, output):
        pass
    endpoint, _, _ = configured(session)
    try:
        try:
            raise RuntimeError("private credential and transcript")
        except RuntimeError as cause:
            raise wire.WireError("private microphone details") from cause
    except wire.WireError as error:
        endpoint._record_failure("conversation", error)
    failure = endpoint.diagnostics()["recentFailures"][0]
    assert failure["type"] == "WireError"
    assert failure["causes"][0]["type"] == "RuntimeError"
    assert failure["causes"][0]["frames"][-1]["file"] == __file__.split("/")[-1]
    emitted = capsys.readouterr().out
    assert "private credential and transcript" not in emitted
    assert "private microphone details" not in emitted


@run_async
async def test_expiry_during_playback_keeps_transport_until_stop_receipt(tls_pair):
    cleaned = asyncio.Event()
    async def session(binding, output):
        try:
            await output.speak(generation=1, text="lopende spraak bij sessieverloop")
        finally:
            cleaned.set()
    endpoint, token, binding = configured(session, max_session_seconds=.1)
    peer = SpeakerPeer()
    async with endpoint.listen(host="127.0.0.1", port=0, tls=tls_pair[0]) as server:
        uri = f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
        async with client(uri, token, tls_pair[1]) as socket:
            peer_task = asyncio.create_task(peer.handle(socket))
            try:
                await asyncio.wait_for(peer.end_received.wait(), 2)
                await asyncio.wait_for(peer.stop_received.wait(), 2)
                assert socket.state.name == "OPEN" and endpoint._active
                assert not cleaned.is_set()
                peer.stopped.set()
                await asyncio.wait_for(cleaned.wait(), 2)
                await wait_released(endpoint)
            finally:
                peer.stopped.set()
                await asyncio.gather(peer_task, return_exceptions=True)
