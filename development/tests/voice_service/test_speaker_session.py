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
@pytest.mark.parametrize("ending", ["mute", "stop", "revoke"])
@pytest.mark.parametrize("work_enabled", [False, True])
async def test_wakeword_interrupt_preserves_session_but_stop_and_mute_close(tls_pair, monkeypatch, ending, work_enabled):
    from uuid import uuid4
    assigned = str(uuid4())
    operations = []
    admitted=[]
    async def ready(self):
        admitted.append(self._device)
        self.household_work_returns = work_enabled
        return True
    async def work_return(self, operation, session_id, **identity):
        operations.append(operation)
        if operation == "open": return {"session_id": assigned}
        assert session_id == assigned
        if operation == "poll": return {"result": None}
        return {}
    monkeypatch.setattr(HouseholdCoreClient,"ready",ready)
    monkeypatch.setattr(HouseholdCoreClient,"work_return",work_return)
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
            if work_enabled: assert session.current_agent.state.session_id == assigned
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
            if ending in {"mute", "stop"}:
                from speaker_wire import WireError
                sink=session.output.audio
                peer.capture_end_gate=asyncio.Event()
                await peer.send(Kind.MUTED if ending == "mute" else Kind.END_SESSION)
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
        if ending == "stop":
            # Reconnection alone must never restart microphone streaming.
            next_peer = NativePeer()
            async with client(uri, token, tls_pair[1]) as next_socket:
                next_device = asyncio.create_task(next_peer.handle(next_socket))
                await next_peer.opened.wait()
                await asyncio.sleep(.05)
                assert next_peer.captures.empty()
                await next_peer.wake()
                assert await asyncio.wait_for(next_peer.captures.get(), 2) == 1
                await next_peer.send(Kind.END_SESSION)
                await asyncio.wait_for(next_device, 3)
                await wait_released(endpoint)
    assert admitted == [{"satellite_id": binding.satellite_id,
                         "device_id": binding.device_id}] * (2 if ending == "stop" else 1)
    if work_enabled:
        assert operations[0] == "open" and operations[-1] == "close"
        assert "poll" in operations
    else:
        assert operations == []
    failures=endpoint.diagnostics()["recentFailures"]
    if ending in {"mute", "stop"}: assert failures==[]
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



def test_media_authority_reuses_pinned_identity_and_detects_file_rotation(tmp_path):
    import hashlib,json
    from eva.contracts.canonical_json import canonical_sha256
    from eva.contracts.identity import IdentityPack
    from speaker_livekit_service import load_media_authority
    identity_path=tmp_path/"identity.json"
    identity_path.write_bytes((Path(__file__).parents[3]/"src/eva/application/default_identity_pack.json").read_bytes())
    identity=IdentityPack.model_validate_json(identity_path.read_bytes())
    token_path=tmp_path/"token";token_path.write_text("synthetic-household-key")
    policy_path=tmp_path/"policy.json"
    policy_path.write_text(json.dumps(dict(contract="HouseholdVoicePolicy/v1",schema_version=1,
        policy_id="home-voice",policy_version=1,state="enabled",tenant_id="tenant",installation_id="installation",
        owner_principal_id="00000000-0000-0000-0000-000000000001",
        principal_binding_id="00000000-0000-0000-0000-000000000002",
        identity_sha256=canonical_sha256(identity.model_dump(mode="json")),
        token_sha256=hashlib.sha256(token_path.read_bytes()).hexdigest(),household_id="home",
        consent_operation_id="household-consent:owner-1",devices={"assist_satellite.room":"a"*32},
        effective_at="2026-01-01T00:00:00Z",expires_at="2099-01-01T00:00:00Z")))
    for path in (identity_path,token_path,policy_path):path.chmod(0o600)
    authority=load_media_authority(policy_file=policy_path,token_file=token_path,identity_file=identity_path)
    authority.validate_current()
    token_path.write_text("different-key")
    with pytest.raises(ValueError):authority.validate_current()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "probabilities,baseline_starts,candidate_starts",
    [
        ([.95] * 4, 1, 0),
        ([.60] * 30, 1, 0),
        ([.95] * 25, 1, 1),
    ],
    ids=["brief-high-confidence-noise", "sustained-uncertain-noise", "clear-speech"],
)
async def test_speaker_vad_confirms_speech_before_interrupting(
    monkeypatch, probabilities, baseline_starts, candidate_starts
):
    """Exercise real Silero segmentation with controlled classifier output.

    This isolates the duration/confidence policy; it is not an acoustic claim
    about a real room. No recording or external speech/model service is used.
    """
    from livekit.agents import vad
    from livekit.plugins import silero
    from livekit.plugins.silero import onnx_model
    from speaker_livekit_service import make_speaker_vad

    predictions = iter(())
    monkeypatch.setattr(
        onnx_model.OnnxModel, "__call__",
        lambda self, audio: next(predictions, 0.0),
    )
    for factory, expected in [
        (lambda: silero.VAD.load(min_speech_duration=.1, min_silence_duration=.45),
         baseline_starts),
        (make_speaker_vad, candidate_starts),
    ]:
        predictions = iter([0.0] * 20 + probabilities + [0.0] * 30)
        detector = factory()
        stream = detector.stream()
        try:
            for _ in range(20 + len(probabilities) + 30):
                stream.push_frame(rtc.AudioFrame(bytes(1024), 16000, 1, 512))
            stream.end_input()
            events = [event async for event in stream]
            starts = [event for event in events
                      if event.type == vad.VADEventType.START_OF_SPEECH]
            assert len(starts) == expected
            if factory is make_speaker_vad and starts:
                # Seven 32ms frames confirm speech, without waiting for STT.
                assert .2 <= starts[0].timestamp - 20 * .032 <= .3
        finally:
            await stream.aclose()


@pytest.mark.asyncio
async def test_speaker_factory_streams_microphone_to_ha_before_speech_ends():
    """Use the real speaker factory; the source is a deterministic local double."""
    from types import SimpleNamespace
    from livekit.agents import stt, vad
    from test_streaming_input import Detector, collect, frame

    detector = Detector()
    provider_started = asyncio.Event()
    pcm = bytearray()

    async def transcribe(chunks):
        async for chunk in chunks:
            pcm.extend(chunk)
            provider_started.set()
        return "Dit is de volledige vraag."

    candidate = LiveKitHouseholdSpeakerSession(
        core_url="http://127.0.0.1:1", household_token="synthetic-household",
        stt_source=SimpleNamespace(transcribe=transcribe), tts_source=None,
        vad=detector, check=lambda: None,
    )
    session = candidate.make_session()
    stream = session.stt.stream()
    events = []
    consumer = asyncio.create_task(collect(stream, events))
    try:
        detector.emit(vad.VADEventType.START_OF_SPEECH, [frame(1)])
        await asyncio.wait_for(provider_started.wait(), 1)
        assert pcm == bytes(frame(1).data)
        assert not any(event.type == stt.SpeechEventType.FINAL_TRANSCRIPT for event in events)
        detector.emit(vad.VADEventType.INFERENCE_DONE, [frame(2)])
        detector.emit(vad.VADEventType.END_OF_SPEECH)
        stream.end_input()
        await asyncio.wait_for(consumer, 1)
        assert pcm == bytes(frame(1).data) + bytes(frame(2).data)
        assert [event.type for event in events] == [
            stt.SpeechEventType.START_OF_SPEECH,
            stt.SpeechEventType.END_OF_SPEECH,
            stt.SpeechEventType.FINAL_TRANSCRIPT,
        ]
        assert events[-1].alternatives[0].text == "Dit is de volledige vraag."
    finally:
        await stream.aclose()
        if not consumer.done():
            consumer.cancel()
        await asyncio.gather(consumer, return_exceptions=True)
        await session.aclose()
