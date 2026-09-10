import asyncio
from pathlib import Path
import sys
import pytest
from livekit import rtc
from livekit.agents import AgentSession
from eva.voice_service.agent import HouseholdCoreClient
sys.path.insert(0, str(Path(__file__).parents[1]/"operations"))
from test_native_duplex_speaker_input import NativePeer, Kind
from test_native_duplex_speaker_endpoint import tls_pair, configured, client, wait_released
from speaker_livekit_session import LiveKitHouseholdSpeakerSession


@pytest.mark.asyncio
@pytest.mark.parametrize("ending", ["mute", "revoke"])
async def test_same_livekit_session_survives_local_stop_and_mute_closes(tls_pair, monkeypatch, ending):
    admitted=[]
    async def ready(self):
        admitted.append(self._device)
        return True
    monkeypatch.setattr(HouseholdCoreClient,"ready",ready)
    class Candidate(LiveKitHouseholdSpeakerSession):
        def make_session(self):
            self.actual_session=AgentSession(aec_warmup_duration=0)
            return self.actual_session
    valid=True
    def check():
        if not valid: raise ValueError("revoked")
    candidate=Candidate(core_url="http://127.0.0.1:1",household_token="test-household",
        stt_source=None,tts_source=None,vad=None,check=check)
    import secrets, hashlib
    from speaker_endpoint import SpeakerEndpoint, SpeakerGrant
    from reply_lane import HouseholdBinding
    token=secrets.token_urlsafe(32)
    binding=HouseholdBinding("assist_satellite.test", "a"*32, "ha-conversation", "nl-NL")
    endpoint=SpeakerEndpoint(grants=(SpeakerGrant(binding,hashlib.sha256(token.encode()).hexdigest()),),
        pcm=None,session=candidate,microphone_enabled=True,receipt_timeout=1)
    peer=NativePeer()
    async with endpoint.listen(host="127.0.0.1",port=0,tls=tls_pair[0]) as server:
        uri=f"wss://127.0.0.1:{server.sockets[0].getsockname()[1]}/eva-speaker/v1"
        async with client(uri,token,tls_pair[1]) as socket:
            device=asyncio.create_task(peer.handle(socket))
            await peer.opened.wait()
            assert peer.captures.empty()
            await peer.wake()
            assert await asyncio.wait_for(peer.captures.get(),2)==1
            session=candidate.actual_session
            async def audio(): yield rtc.AudioFrame(bytes(960),24000,1,480)
            # Session startup follows the capture receipt.
            for _ in range(100):
                try:
                    if session.current_agent is not None: break
                except RuntimeError: pass
                await asyncio.sleep(.01)
            first=session.say("Audio",audio=audio(),allow_interruptions=True)
            assert await asyncio.wait_for(peer.answers.get(),2)==1
            peer.stop_gate=asyncio.Event()
            await peer.send(Kind.LOCAL_STOP)
            await asyncio.sleep(.05)
            assert peer.captures.empty()
            peer.stop_gate.set()
            assert await asyncio.wait_for(peer.captures.get(),2)==2
            assert candidate.actual_session is session
            assert first.interrupted
            if ending=="mute":
                from speaker_wire import WireError
                sink=session.output.audio
                peer.capture_end_gate=asyncio.Event()
                await peer.send(Kind.MUTED)
                async with asyncio.timeout(1):
                    while not sink.closed: await asyncio.sleep(.005)
                with pytest.raises(WireError):
                    await sink.capture_frame(rtc.AudioFrame(bytes(960),24000,1,480))
                assert not any(p.kind==Kind.BEGIN and p.generation>1 for p in peer.packets)
                peer.capture_end_gate.set()
            else:
                valid=False
            await asyncio.wait_for(device,3)
            await wait_released(endpoint)
    assert admitted==[{"satellite_id":binding.satellite_id,"device_id":binding.device_id}]
    failures=endpoint.diagnostics()["recentFailures"]
    if ending=="mute": assert failures==[]
    else: assert failures and failures[-1]["type"]=="ValueError"


def test_household_client_does_not_accept_identity_from_transcript():
    client=HouseholdCoreClient("http://localhost:1","house-key",satellite_id="assist_satellite.room",device_id="a"*32)
    path,body=client.request({"text":"hello"})
    assert path=="/internal/v1/ha-voice/session/respond" and body["device_id"]=="a"*32
    for field in ("device_id","satellite_id","principal_id","audience"):
        with pytest.raises(ValueError): client.request({field:"owner"})


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["revoked", "owner-token", "wrong-device"])
async def test_service_rejects_unbound_authority_before_tls_or_network(fault, monkeypatch):
    from types import SimpleNamespace
    import speaker_livekit_service as service
    def unexpected(*args): raise AssertionError("Opened resources before authority admission")
    monkeypatch.setattr(service,"server_tls",unexpected)
    def check():
        if fault=="revoked": raise ValueError("revoked")
    config=SimpleNamespace(eva_token="owner" if fault=="owner-token" else "house",
        grants=(SimpleNamespace(binding=SimpleNamespace(satellite_id="assist_satellite.room",device_id="a"*32)),))
    authority=SimpleNamespace(validate_current=check,token="house",
        policy=SimpleNamespace(devices={"assist_satellite.room":("b" if fault=="wrong-device" else "a")*32}))
    with pytest.raises(ValueError):
        async with service.open_livekit_service(config,authority=authority):
            raise AssertionError("Unbound service started")
