"""Active conversation lifetime starts at wake, not socket admission."""
import asyncio
import hashlib
import secrets
import ssl
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).parents[1]/'operations'))
from test_native_duplex_speaker_endpoint import client
from test_native_duplex_speaker_input import NativePeer
from speaker_endpoint import SpeakerEndpoint,SpeakerGrant
from reply_lane import HouseholdBinding

@pytest.fixture
def tls_pair(tmp_path):
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from datetime import datetime, UTC, timedelta
    from ipaddress import ip_address
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    now=datetime.now(UTC)
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1))
        .not_valid_after(now+timedelta(days=1)).add_extension(x509.SubjectAlternativeName([
            x509.IPAddress(ip_address('127.0.0.1'))]),critical=False).sign(key,hashes.SHA256()))
    cp,kp=tmp_path/'cert.pem',tmp_path/'key.pem'
    cp.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    kp.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
    server=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server.minimum_version=ssl.TLSVersion.TLSv1_2
    server.load_cert_chain(cp,kp)
    return server,ssl.create_default_context(cafile=str(cp))

@pytest.mark.asyncio
async def test_wake_near_standby_deadline_gets_full_active_budget(tls_pair):
    woke=asyncio.Event();cancelled=asyncio.Event()
    async def conversation(binding,wire):
        await wire.microphone.wait_wake();woke.set()
        try:await asyncio.Event().wait()
        finally:cancelled.set()
    token=secrets.token_urlsafe(32)
    grant=SpeakerGrant(HouseholdBinding('assist_satellite.test','a'*32,'conversation','nl-NL'),hashlib.sha256(token.encode()).hexdigest())
    endpoint=SpeakerEndpoint(grants=(grant,),pcm=None,session=conversation,
        max_session_seconds=.6,receipt_timeout=1,microphone_enabled=True,lifetime_from_wake=True)
    peer=NativePeer()
    try:
        async with endpoint.listen(host='127.0.0.1',port=0,tls=tls_pair[0]) as server:
            uri=f'wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1'
            async with client(uri,token,tls_pair[1]) as socket:
                device=asyncio.create_task(peer.handle(socket))
                try:
                    await peer.opened.wait();await asyncio.sleep(.4)
                    await peer.wake();await asyncio.wait_for(woke.wait(),.2)
                    await asyncio.sleep(.3)
                    assert not cancelled.is_set()  # Old socket budget already expired.
                    await asyncio.wait_for(cancelled.wait(),.6)  # Active cap still enforced.
                finally:
                    device.cancel();await asyncio.gather(device,return_exceptions=True)
    finally:await endpoint.close()
